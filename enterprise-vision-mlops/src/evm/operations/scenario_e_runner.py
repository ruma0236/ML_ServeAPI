from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import requests

from evm.operations.failure_evidence import (
    ApprovalEvidence,
    ArtifactEvidence,
    CheckEvidence,
    ClosureEvidence,
    DecisionEvidence,
    EnvironmentEvidence,
    IdentityEvidence,
    InjectionEvidence,
    OperationalFailureReport,
    PortfolioEvidence,
    RecoveryEvidence,
    SignalEvidence,
    SourceEvidence,
    TimingEvidence,
    sha256_file,
    validate_closure,
)
from evm.operations.failure_scenarios import atomic_write_json
from evm.operations.metrics import OperationalMetricProjection
from evm.operations.scenario_e_integrity import (
    IntegrityAdmission,
    IntegrityCounts,
    IntegrityException,
    IntegrityIdentity,
    MlflowObservation,
    SignedTrustManifest,
    TrustedFile,
    TrustManifest,
    build_integrity_admission,
    identity_fingerprint,
    manifest_id,
    sign_manifest,
    validate_integrity,
    validate_integrity_admission,
)


DEFAULT_INFERENCE_IMAGE_URI = (
    "file:///F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/raw/industrial/visa/"
    "candle/Data/Images/Normal/0000.JPG"
)
FixtureMutator = Callable[[TrustManifest, Path, dict[str, Any]], tuple[TrustManifest, dict[str, Any]]]


def utc_now() -> datetime:
    return datetime.now(UTC)


def json_write(path: Path, payload: Any) -> None:
    atomic_write_json(path, payload)


def json_read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def jsonl_read(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError(f"expected JSONL object: {path}")
                rows.append(payload)
    return rows


def jsonl_write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def git_text(project_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def command_json(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    required = {"policy", "identity", "paths", "expected_counts", "shard_digests", "lineage"}
    if not required.issubset(payload):
        raise ValueError(f"scenario E config sections missing: {sorted(required - set(payload))}")
    return payload


def trusted_files(config: dict[str, Any], config_path: Path) -> list[TrustedFile]:
    paths = config["paths"]
    identity = config["identity"]
    expected = {
        "shard_manifest": identity["shard_manifest_sha256"],
        "split_manifest": identity["split_manifest_sha256"],
        "ct_manifest": identity["ct_manifest_sha256"],
        "candidate_summary": identity["candidate_summary_sha256"],
        "lineage": identity["lineage_sha256"],
        "model_card": identity["model_card_sha256"],
        "model_artifact": identity["model_digest"],
        "policy": sha256_file(config_path),
    }
    return [
        TrustedFile(
            role=role,
            path=str(config_path.resolve()) if role == "policy" else str(Path(paths[role]).resolve()),
            sha256=digest,
        )
        for role, digest in expected.items()
    ]


def _manifest_with_id(payload: dict[str, Any]) -> TrustManifest:
    payload = {**payload, "manifest_id": "0" * 64}
    draft = TrustManifest.model_validate(payload)
    return draft.model_copy(update={"manifest_id": manifest_id(draft)})


def _refresh_exception(manifest: TrustManifest) -> TrustManifest:
    subject = identity_fingerprint(manifest.identity)
    refreshed = [
        item.model_copy(update={"subject_fingerprint": subject})
        for item in manifest.exceptions
    ]
    return _manifest_with_id(
        {**manifest.model_dump(mode="json"), "exceptions": [item.model_dump(mode="json") for item in refreshed]}
    )


def build_canonical_manifest(
    config: dict[str, Any],
    config_path: Path,
    *,
    issued_at: datetime,
    source_revision: str,
) -> TrustManifest:
    policy = config["policy"]
    identity_cfg = config["identity"]
    identity = IntegrityIdentity(
        **identity_cfg,
        policy_sha256=sha256_file(config_path),
        training_source_revision=None,
    )
    subject = identity_fingerprint(identity)
    expires_at = issued_at + timedelta(days=int(policy["manifest_valid_days"]))
    exception = IntegrityException(
        exception_id=f"legacy-source-{subject[:16]}",
        code="training_source_revision_missing",
        requester=str(policy["exception_requester"]),
        approver=str(policy["exception_approver"]),
        reason=(
            "The 2026-07-12 production B0 artifacts predate mandatory training source "
            "revision capture; all non-excepted identities remain exact and immutable."
        ),
        subject_fingerprint=subject,
        issued_at=issued_at,
        expires_at=min(
            issued_at + timedelta(days=int(policy["exception_days"])),
            expires_at,
        ),
    )
    counts_cfg = config["expected_counts"]
    return _manifest_with_id(
        {
            "schema_version": "evm.scenario_e_trust_manifest.v1",
            "issue": "SCRUM-176",
            "issuer": policy["issuer"],
            "key_id": policy["key_id"],
            "validator_source_revision": source_revision,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "identity": identity.model_dump(mode="json"),
            "expected_counts": IntegrityCounts(
                record_count=counts_cfg["record_count"],
                shard_count=counts_cfg["shard_count"],
                split_counts=counts_cfg["split_counts"],
                ct_record_count=counts_cfg["ct_record_count"],
            ).model_dump(mode="json"),
            "files": [item.model_dump(mode="json") for item in trusted_files(config, config_path)],
            "shard_digests": config["shard_digests"],
            "lineage_parents": config["lineage"]["required_parent_fields"],
            "exceptions": [exception.model_dump(mode="json")],
        }
    )


def _replace_file(
    manifest: TrustManifest,
    role: str,
    path: Path,
    digest: str,
) -> TrustManifest:
    files = [
        item.model_copy(update={"path": str(path.resolve()), "sha256": digest})
        if item.role == role
        else item
        for item in manifest.files
    ]
    identity_updates: dict[str, Any] = {}
    identity_field = {
        "shard_manifest": "shard_manifest_sha256",
        "split_manifest": "split_manifest_sha256",
        "ct_manifest": "ct_manifest_sha256",
        "candidate_summary": "candidate_summary_sha256",
        "lineage": "lineage_sha256",
        "model_card": "model_card_sha256",
        "model_artifact": "model_digest",
        "policy": "policy_sha256",
    }[role]
    identity_updates[identity_field] = digest
    updated = manifest.model_copy(
        update={
            "files": files,
            "identity": manifest.identity.model_copy(update=identity_updates),
        }
    )
    return _refresh_exception(updated)


def _update_index_path(
    manifest: TrustManifest,
    fixture_root: Path,
    *,
    shard_id: str,
    shard_path: Path,
) -> TrustManifest:
    source = Path(next(item.path for item in manifest.files if item.role == "shard_manifest"))
    payload = json_read(source)
    matches = [item for item in payload["shards"] if item.get("shard_id") == shard_id]
    if len(matches) != 1:
        raise ValueError(f"fixture shard selector invalid: {shard_id}")
    matches[0]["path"] = str(shard_path.resolve())
    index_path = fixture_root / "shard_index.json"
    json_write(index_path, payload)
    return _replace_file(manifest, "shard_manifest", index_path, sha256_file(index_path))


def _sign_fixture(
    manifest: TrustManifest,
    private_key: bytes,
    path: Path,
) -> SignedTrustManifest:
    envelope = sign_manifest(manifest, private_key)
    json_write(path, envelope.model_dump(mode="json"))
    return envelope


def _canonical_shard_path(manifest: TrustManifest, shard_id: str) -> Path:
    index = json_read(Path(next(item.path for item in manifest.files if item.role == "shard_manifest")))
    descriptor = next(item for item in index["shards"] if item.get("shard_id") == shard_id)
    data_root = Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops")
    value = str(descriptor["path"]).replace("\\", "/")
    if value.startswith("/mnt/evm-data/"):
        return data_root / value.removeprefix("/mnt/evm-data/")
    return Path(value)


def build_fixture_matrix(
    canonical: TrustManifest,
    *,
    fixture_root: Path,
    private_key: bytes,
    mlflow: MlflowObservation,
    observed_image_digest: str,
) -> list[dict[str, Any]]:
    fixture_root.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, Any]] = []

    def register(
        name: str,
        expected: str | None,
        manifest: TrustManifest,
        *,
        mlflow_observation: MlflowObservation = mlflow,
        image_digest: str = observed_image_digest,
        after_sign: Callable[[], None] | None = None,
        corrupt_signature: bool = False,
    ) -> None:
        root = fixture_root / name
        root.mkdir(parents=True, exist_ok=True)
        envelope_path = root / "signed-trust-manifest.json"
        envelope = _sign_fixture(manifest, private_key, envelope_path)
        if corrupt_signature:
            envelope = envelope.model_copy(update={"signature": "A" + envelope.signature[1:]})
            json_write(envelope_path, envelope.model_dump(mode="json"))
        if after_sign:
            after_sign()
        cases.append(
            {
                "fixture": name,
                "expected_primary_blocker": expected,
                "envelope_uri": str(envelope_path.resolve()),
                "mlflow": mlflow_observation.model_dump(mode="json"),
                "observed_image_digest": image_digest,
                "recipe": {
                    "parent_manifest_id": canonical.manifest_id,
                    "isolated_root": str(root.resolve()),
                    "canonical_mutation": False,
                },
            }
        )

    register("invalid_signature", "trust_signature_invalid", canonical, corrupt_signature=True)

    stale_issued = canonical.issued_at - timedelta(days=60)
    stale = canonical.model_copy(
        update={
            "issued_at": stale_issued,
            "expires_at": stale_issued + timedelta(days=1),
            "exceptions": [
                item.model_copy(
                    update={
                        "issued_at": stale_issued,
                        "expires_at": stale_issued + timedelta(days=1),
                    }
                )
                for item in canonical.exceptions
            ],
        }
    )
    register("stale_manifest", "trust_manifest_stale", _refresh_exception(stale))

    missing_path = fixture_root / "missing_manifest" / "missing-shard-index.json"
    missing = _replace_file(
        canonical,
        "shard_manifest",
        missing_path,
        canonical.identity.shard_manifest_sha256,
    )
    register("missing_manifest", "manifest_missing", missing)

    tampered_root = fixture_root / "tampered_manifest"
    tampered_root.mkdir(parents=True, exist_ok=True)
    tampered_path = tampered_root / "shard_index.json"
    source_index = Path(next(item.path for item in canonical.files if item.role == "shard_manifest"))
    shutil.copy2(source_index, tampered_path)
    tampered = canonical.model_copy(
        update={
            "files": [
                item.model_copy(update={"path": str(tampered_path.resolve())})
                if item.role == "shard_manifest"
                else item
                for item in canonical.files
            ]
        }
    )
    tampered = _refresh_exception(tampered)
    register(
        "tampered_manifest",
        "manifest_digest_mismatch",
        tampered,
        after_sign=lambda: tampered_path.write_bytes(tampered_path.read_bytes() + b"\n"),
    )

    missing_shard_root = fixture_root / "missing_shard"
    missing_shard_path = missing_shard_root / "not-present.jsonl"
    missing_shard = _update_index_path(
        canonical,
        missing_shard_root,
        shard_id="train-0000",
        shard_path=missing_shard_path,
    )
    register("missing_shard", "shard_missing", missing_shard)

    corrected_root = fixture_root / "corrected_isolated"
    corrected_shard = corrected_root / "train_shard_0000.jsonl"
    corrected_shard.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_canonical_shard_path(canonical, "train-0000"), corrected_shard)
    corrected = _update_index_path(
        canonical,
        corrected_root,
        shard_id="train-0000",
        shard_path=corrected_shard,
    )
    corrected = corrected.model_copy(
        update={
            "shard_digests": {
                **corrected.shard_digests,
                "train-0000": sha256_file(corrected_shard),
            }
        }
    )
    register("corrected_isolated", None, _refresh_exception(corrected))

    duplicate_root = fixture_root / "duplicate_record"
    duplicate_shard = duplicate_root / "train_shard_0000.jsonl"
    rows = jsonl_read(_canonical_shard_path(canonical, "train-0000"))
    rows[10] = dict(rows[9])
    jsonl_write(duplicate_shard, rows)
    duplicate = _update_index_path(
        canonical,
        duplicate_root,
        shard_id="train-0000",
        shard_path=duplicate_shard,
    )
    duplicate = duplicate.model_copy(
        update={
            "shard_digests": {
                **duplicate.shard_digests,
                "train-0000": sha256_file(duplicate_shard),
            }
        }
    )
    register("duplicate_record", "duplicate_record_identity", _refresh_exception(duplicate))

    leakage_root = fixture_root / "split_leakage"
    leakage_shard = leakage_root / "validation_shard_0013.jsonl"
    train_row = jsonl_read(_canonical_shard_path(canonical, "train-0000"))[9]
    validation_rows = jsonl_read(_canonical_shard_path(canonical, "validation-0013"))
    for field in ("sample_id", "id", "content_sha256"):
        if field in train_row:
            validation_rows[10][field] = train_row[field]
    jsonl_write(leakage_shard, validation_rows)
    leakage = _update_index_path(
        canonical,
        leakage_root,
        shard_id="validation-0013",
        shard_path=leakage_shard,
    )
    leakage = leakage.model_copy(
        update={
            "shard_digests": {
                **leakage.shard_digests,
                "validation-0013": sha256_file(leakage_shard),
            }
        }
    )
    register("split_leakage", "split_leakage_detected", _refresh_exception(leakage))

    ct_root = fixture_root / "ct_identity_mismatch"
    ct_path = ct_root / "holdout_manifest.jsonl"
    source_ct = Path(next(item.path for item in canonical.files if item.role == "ct_manifest"))
    jsonl_write(ct_path, jsonl_read(source_ct)[:-1])
    ct_manifest = _replace_file(canonical, "ct_manifest", ct_path, sha256_file(ct_path))
    register("ct_identity_mismatch", "ct_identity_mismatch", ct_manifest)

    lineage_root = fixture_root / "lineage_parent_missing"
    lineage_path = lineage_root / "lineage.json"
    lineage_payload = json_read(Path(next(item.path for item in canonical.files if item.role == "lineage")))
    lineage_payload.pop("artifact_uri", None)
    json_write(lineage_path, lineage_payload)
    lineage_manifest = _replace_file(canonical, "lineage", lineage_path, sha256_file(lineage_path))
    register("lineage_parent_missing", "lineage_parent_missing", lineage_manifest)

    model_identity_root = fixture_root / "model_identity_mismatch"
    candidate_path = model_identity_root / "candidate_summary.json"
    candidate_payload = json_read(
        Path(next(item.path for item in canonical.files if item.role == "candidate_summary"))
    )
    candidate_payload["candidate_id"] = "unapproved-candidate"
    json_write(candidate_path, candidate_payload)
    candidate_manifest = _replace_file(
        canonical,
        "candidate_summary",
        candidate_path,
        sha256_file(candidate_path),
    )
    register("model_identity_mismatch", "model_identity_mismatch", candidate_manifest)

    model_root = fixture_root / "model_artifact_tampered"
    model_path = model_root / "model.pt"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    source_model = Path(next(item.path for item in canonical.files if item.role == "model_artifact"))
    shutil.copy2(source_model, model_path)
    model_manifest = canonical.model_copy(
        update={
            "files": [
                item.model_copy(update={"path": str(model_path.resolve())})
                if item.role == "model_artifact"
                else item
                for item in canonical.files
            ]
        }
    )

    def corrupt_model() -> None:
        with model_path.open("r+b") as handle:
            first = handle.read(1)
            handle.seek(0)
            handle.write(bytes([first[0] ^ 0x01]))

    register(
        "model_artifact_tampered",
        "model_artifact_digest_mismatch",
        _refresh_exception(model_manifest),
        after_sign=corrupt_model,
    )

    register(
        "mlflow_identity_mismatch",
        "mlflow_identity_mismatch",
        canonical,
        mlflow_observation=mlflow.model_copy(update={"run_id": "wrong-run-id"}),
    )
    register(
        "container_image_digest_mismatch",
        "container_image_digest_mismatch",
        canonical,
        image_digest="sha256:" + "0" * 64,
    )
    return cases


def mlflow_observation(run_id: str) -> MlflowObservation:
    response = requests.get(
        "http://127.0.0.1:5000/api/2.0/mlflow/runs/get",
        params={"run_id": run_id},
        timeout=15,
    )
    response.raise_for_status()
    run = response.json()["run"]
    params = {item["key"]: item["value"] for item in run["data"].get("params", [])}
    return MlflowObservation(
        run_id=str(run["info"]["run_id"]),
        status=str(run["info"]["status"]),
        candidate_id=str(params.get("candidate_id") or ""),
        dataset_version=str(params.get("dataset_version") or ""),
        artifact_uri=str(params.get("artifact_uri") or ""),
    )


def production_snapshot(inference_image_uri: str) -> dict[str, Any]:
    deployment = command_json(
        [
            "kubectl",
            "-n",
            "evm-production",
            "get",
            "deployment",
            "evm-b0-production",
            "-o",
            "json",
        ]
    )
    nodes = command_json(["kubectl", "get", "nodes", "-o", "json"])
    plugin = command_json(
        [
            "kubectl",
            "-n",
            "kube-system",
            "get",
            "pods",
            "-l",
            "name=nvidia-device-plugin-ds",
            "-o",
            "json",
        ]
    )
    ready = requests.get("http://127.0.0.1:30800/ready", timeout=15).json()
    inference = requests.post(
        "http://127.0.0.1:30800/predict",
        json={"image_uri": inference_image_uri},
        timeout=30,
    ).json()
    prometheus = requests.get("http://127.0.0.1:9090/api/v1/targets", timeout=15).json()
    runtime = requests.get(
        "http://127.0.0.1:8000/control-panel/v1/runtime-supervisor",
        timeout=15,
    ).json()
    image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
    image_digest = "sha256:" + image.split("@sha256:", 1)[1] if "@sha256:" in image else ""
    targets = {
        item.get("labels", {}).get("job"): item.get("health")
        for item in prometheus["data"]["activeTargets"]
        if item.get("labels", {}).get("job") in {"evm-api", "evm-b0-production"}
    }
    return {
        "observed_at": utc_now().isoformat(),
        "deployment": {
            "namespace": deployment["metadata"]["namespace"],
            "name": deployment["metadata"]["name"],
            "uid": deployment["metadata"]["uid"],
            "replicas": deployment["status"].get("replicas", 0),
            "ready_replicas": deployment["status"].get("readyReplicas", 0),
            "available_replicas": deployment["status"].get("availableReplicas", 0),
            "image": image,
            "image_digest": image_digest,
        },
        "ready": ready,
        "inference": inference,
        "gpu_allocatable": [
            item["status"].get("allocatable", {}).get("nvidia.com/gpu", "0")
            for item in nodes["items"]
        ],
        "device_plugin": [
            {
                "uid": item["metadata"]["uid"],
                "phase": item["status"].get("phase"),
                "ready": all(
                    status.get("ready", False)
                    for status in item["status"].get("containerStatuses", [])
                ),
            }
            for item in plugin["items"]
        ],
        "prometheus_targets": targets,
        "runtime_supervisor": runtime,
    }


def mutation_snapshot(config: dict[str, Any], manifest: TrustManifest) -> dict[str, Any]:
    ledger_path = Path(config["paths"]["deployment_intent_ledger"])
    if ledger_path.is_file():
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8-sig"))
            ledger_count = len(ledger) if isinstance(ledger, list) else -1
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            ledger_count = -1
        ledger_sha = sha256_file(ledger_path)
    else:
        ledger_count = 0
        ledger_sha = None
    canonical: dict[str, str] = {
        item.role: sha256_file(Path(item.path))
        for item in manifest.files
        if item.role != "policy"
    }
    shard_index = json_read(Path(next(item.path for item in manifest.files if item.role == "shard_manifest")))
    data_root = Path(config["paths"]["host_data_root"])
    for descriptor in shard_index["shards"]:
        value = str(descriptor["path"]).replace("\\", "/")
        path = data_root / value.removeprefix("/mnt/evm-data/")
        canonical[f"shard:{descriptor['shard_id']}"] = sha256_file(path)
    return {
        "observed_at": utc_now().isoformat(),
        "deployment_intent_ledger": {
            "path": str(ledger_path.resolve()),
            "count": ledger_count,
            "sha256": ledger_sha,
        },
        "canonical_digests": canonical,
    }


def invariant_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    keys = (
        ("deployment_uid", before["production"]["deployment"]["uid"], after["production"]["deployment"]["uid"]),
        ("deployment_image", before["production"]["deployment"]["image"], after["production"]["deployment"]["image"]),
        ("model_digest", before["production"]["ready"]["model_sha256"], after["production"]["ready"]["model_sha256"]),
        ("gpu_allocatable", before["production"]["gpu_allocatable"], after["production"]["gpu_allocatable"]),
        ("device_plugin", before["production"]["device_plugin"], after["production"]["device_plugin"]),
        ("ledger_count", before["mutation"]["deployment_intent_ledger"]["count"], after["mutation"]["deployment_intent_ledger"]["count"]),
        ("ledger_sha", before["mutation"]["deployment_intent_ledger"]["sha256"], after["mutation"]["deployment_intent_ledger"]["sha256"]),
        ("canonical_digests", before["mutation"]["canonical_digests"], after["mutation"]["canonical_digests"]),
    )
    checks = {name: {"before": first, "after": second, "unchanged": first == second} for name, first, second in keys}
    checks["production_ready"] = {
        "before": before["production"]["deployment"]["ready_replicas"],
        "after": after["production"]["deployment"]["ready_replicas"],
        "unchanged": after["production"]["deployment"]["ready_replicas"] == 1,
    }
    checks["cuda_inference"] = {
        "before": before["production"]["inference"].get("device"),
        "after": after["production"]["inference"].get("device"),
        "unchanged": before["production"]["inference"].get("device") == "cuda"
        and after["production"]["inference"].get("device") == "cuda",
    }
    checks["prometheus"] = {
        "before": before["production"]["prometheus_targets"],
        "after": after["production"]["prometheus_targets"],
        "unchanged": all(
            before["production"]["prometheus_targets"].get(job) == "up"
            and after["production"]["prometheus_targets"].get(job) == "up"
            for job in ("evm-api", "evm-b0-production")
        ),
    }
    return {
        "schema_version": "evm.scenario_e_invariant_diff.v1",
        "checks": checks,
        "passed": all(item["unchanged"] for item in checks.values()),
    }


def mount_uri(path: Path, host_data_root: Path) -> str:
    resolved = path.resolve()
    root = host_data_root.resolve()
    if resolved.is_relative_to(root):
        suffix = resolved.relative_to(root).as_posix()
        return f"/mnt/evm-data/{suffix}"
    return str(resolved)


def artifact(path: Path) -> ArtifactEvidence:
    return ArtifactEvidence(
        uri=str(path.resolve()),
        sha256=sha256_file(path),
        media_type="application/json",
        evidence_role="run_evidence",
    )


def run(config_path: Path, project_root: Path) -> Path:
    started_at = utc_now()
    monotonic_started = time.monotonic_ns()
    config = load_config(config_path)
    policy = config["policy"]
    output_root = Path(config["paths"]["output_root"])
    head = git_text(project_root, "rev-parse", "HEAD")
    upstream = git_text(project_root, "rev-parse", "@{u}")
    branch = git_text(project_root, "branch", "--show-current")
    dirty = bool(git_text(project_root, "status", "--porcelain", "--", "."))
    if dirty or head != upstream:
        raise RuntimeError(f"scenario_e_source_preflight_failed:dirty={dirty}:head={head}:upstream={upstream}")
    run_id = f"scenario-e-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{head[:8]}"
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=False)

    private_key = Path(policy["private_key_path"]).read_bytes()
    public_key_path = project_root / policy["public_key_path"]
    public_key = public_key_path.read_bytes()
    canonical = build_canonical_manifest(
        config,
        config_path,
        issued_at=started_at,
        source_revision=head,
    )
    signed = sign_manifest(canonical, private_key)
    signed_path = run_root / "signed-trust-manifest.json"
    json_write(signed_path, signed.model_dump(mode="json"))

    mlflow = mlflow_observation(canonical.identity.mlflow_run_id)
    before = {
        "production": production_snapshot(DEFAULT_INFERENCE_IMAGE_URI),
        "mutation": mutation_snapshot(config, canonical),
    }
    json_write(run_root / "pre-invariants.json", before)
    observed_image = before["production"]["deployment"]["image_digest"]
    if observed_image != canonical.identity.container_image_digest:
        raise RuntimeError("scenario_e_preflight_container_identity_mismatch")

    allowed_roots = [Path(item) for item in policy["allowed_roots"]]
    validation_args = {
        "public_key_pem": public_key,
        "allowed_roots": allowed_roots,
        "host_data_root": Path(config["paths"]["host_data_root"]),
        "host_ct_root": Path(config["paths"]["host_ct_root"]),
    }
    canonical_results = [
        validate_integrity(
            signed,
            **validation_args,
            observed_image_digest=observed_image,
            mlflow=mlflow,
            now=started_at + timedelta(seconds=index),
        )
        for index in range(int(policy["canonical_replays"]))
    ]
    canonical_payload = {
        "schema_version": "evm.scenario_e_canonical_replays.v1",
        "run_id": run_id,
        "results": [item.model_dump(mode="json") for item in canonical_results],
        "passed": all(item.decision == "admitted" for item in canonical_results)
        and len({item.decision_fingerprint for item in canonical_results}) == 1,
    }
    canonical_path = run_root / "canonical-validations.json"
    json_write(canonical_path, canonical_payload)
    if not canonical_payload["passed"]:
        raise RuntimeError("scenario_e_canonical_validation_failed")

    fixture_specs = build_fixture_matrix(
        canonical,
        fixture_root=run_root / "fixtures",
        private_key=private_key,
        mlflow=mlflow,
        observed_image_digest=observed_image,
    )
    fixture_results: list[dict[str, Any]] = []
    for spec in fixture_specs:
        envelope = SignedTrustManifest.model_validate_json(
            Path(spec["envelope_uri"]).read_text(encoding="utf-8-sig")
        )
        observed_mlflow = MlflowObservation.model_validate(spec["mlflow"])
        replays = [
            validate_integrity(
                envelope,
                **validation_args,
                observed_image_digest=spec["observed_image_digest"],
                mlflow=observed_mlflow,
                now=started_at + timedelta(seconds=index),
            )
            for index in range(int(policy["fixture_replays"]))
        ]
        expected = spec["expected_primary_blocker"]
        passed = all(
            (item.decision == "admitted" and expected is None)
            or (item.decision == "blocked" and item.primary_blocker == expected)
            for item in replays
        ) and len({item.decision_fingerprint for item in replays}) == 1
        fixture_results.append(
            {
                **spec,
                "replays": [item.model_dump(mode="json") for item in replays],
                "passed": passed,
            }
        )
    fixture_payload = {
        "schema_version": "evm.scenario_e_fixture_matrix.v1",
        "run_id": run_id,
        "fixture_count": len(fixture_results),
        "replay_count": sum(len(item["replays"]) for item in fixture_results),
        "results": fixture_results,
        "passed": all(item["passed"] for item in fixture_results),
    }
    fixture_path = run_root / "fixture-matrix.json"
    json_write(fixture_path, fixture_payload)
    if not fixture_payload["passed"]:
        failed = [item["fixture"] for item in fixture_results if not item["passed"]]
        raise RuntimeError(f"scenario_e_fixture_acceptance_failed:{failed}")

    latest = output_root / "_latest"
    latest.mkdir(parents=True, exist_ok=True)
    validation_path = run_root / "canonical-validation.json"
    json_write(validation_path, canonical_results[-1].model_dump(mode="json"))
    admission = build_integrity_admission(
        envelope=signed,
        validation=canonical_results[-1],
        signed_manifest_path=signed_path,
        validation_path=validation_path,
        source_revision=head,
        signed_manifest_uri=mount_uri(signed_path, Path(config["paths"]["host_data_root"])),
        validation_uri=mount_uri(validation_path, Path(config["paths"]["host_data_root"])),
    )
    admission_path = run_root / "integrity-admission.json"
    json_write(admission_path, admission.model_dump(mode="json"))
    admission_gate_blockers = validate_integrity_admission(
        admission_path,
        public_key_path=public_key_path,
        expected_candidate_id=canonical.identity.candidate_id,
        expected_dataset_version=canonical.identity.dataset_version,
        expected_model_digest=canonical.identity.model_digest,
        expected_image_digest=canonical.identity.container_image_digest,
        now=started_at + timedelta(seconds=10),
    )
    if admission_gate_blockers:
        raise RuntimeError(f"scenario_e_admission_pointer_invalid:{admission_gate_blockers}")

    after = {
        "production": production_snapshot(DEFAULT_INFERENCE_IMAGE_URI),
        "mutation": mutation_snapshot(config, canonical),
    }
    json_write(run_root / "post-invariants.json", after)
    diff = invariant_diff(before, after)
    diff_path = run_root / "invariant-diff.json"
    json_write(diff_path, diff)
    if not diff["passed"]:
        raise RuntimeError("scenario_e_invariant_changed")

    metric = OperationalMetricProjection(
        schema_version="evm.operational_metrics.v1",
        scenario="E",
        target="data-artifact",
        state="passed",
        signals={"artifact_integrity": True, "identity": True},
        validation_result="passed",
        blockers=[],
    )
    metric_path = run_root / "metrics.json"
    json_write(metric_path, metric.model_dump(mode="json"))

    finished_at = utc_now()
    monotonic_finished = time.monotonic_ns()
    precondition_ids = [
        "source_clean_and_pushed",
        "canonical_signature_valid",
        "production_preflight_healthy",
    ]
    postcondition_ids = [
        "canonical_three_run_pass",
        "fixture_matrix_pass",
        "decision_fingerprint_deterministic",
        "deployment_intent_delta_zero",
        "canonical_digest_delta_zero",
        "production_identity_unchanged",
        "gpu_device_plugin_unchanged",
        "cuda_inference_healthy",
        "prometheus_targets_up",
        "admission_pointer_valid",
        "validation_latency_within_slo",
    ]
    max_validation = max(item.validation_seconds for item in canonical_results)
    artifacts = [
        artifact(path)
        for path in (
            signed_path,
            canonical_path,
            validation_path,
            fixture_path,
            run_root / "pre-invariants.json",
            run_root / "post-invariants.json",
            diff_path,
            admission_path,
            metric_path,
        )
    ]
    report = OperationalFailureReport(
        schema_version="evm.operational_failure_evidence.v1",
        scenario_id="E",
        run_id=run_id,
        claim_class="local_operational_validation",
        status="passed",
        started_at=started_at,
        finished_at=finished_at,
        actor="ml-platform-integrity",
        approval=ApprovalEvidence(required=False, decision="not_required"),
        source=SourceEvidence(
            commit=head,
            branch=branch,
            dirty=False,
            api_revision=str(before["production"]["runtime_supervisor"].get("source_commit") or "unknown"),
            worker_revision=str(
                next(
                    item.get("source_commit")
                    for item in before["production"]["runtime_supervisor"].get("children", [])
                    if item.get("name") == "lifecycle_worker"
                )
            ),
            observer_revision=str(
                next(
                    item.get("source_commit")
                    for item in before["production"]["runtime_supervisor"].get("children", [])
                    if item.get("name") == "kubernetes_observer"
                )
            ),
        ),
        environment=EnvironmentEvidence(
            cluster_context=git_text(project_root, "config", "--get", "remote.origin.url"),
            node="docker-desktop",
            namespaces=["evm-production", "kube-system"],
            hardware={"gpu_allocatable": before["production"]["gpu_allocatable"]},
            runtime_versions={"validator": "evm.scenario_e_integrity_validation.v1"},
        ),
        identities=IdentityEvidence(
            dataset_version=canonical.identity.dataset_version,
            split_digest=canonical.identity.split_manifest_sha256,
            model_digest=canonical.identity.model_digest,
            artifact_digest=canonical.identity.model_digest,
            image_digest=canonical.identity.container_image_digest.removeprefix("sha256:"),
            ct_digest=canonical.identity.ct_manifest_sha256,
        ),
        identity_requirements=[
            "dataset_version",
            "split_digest",
            "model_digest",
            "artifact_digest",
            "image_digest",
            "ct_digest",
        ],
        preconditions=[
            CheckEvidence(check_id=precondition_ids[0], passed=True, observed={"head": head, "upstream": upstream}),
            CheckEvidence(check_id=precondition_ids[1], passed=True, observed={"manifest_id": canonical.manifest_id}),
            CheckEvidence(
                check_id=precondition_ids[2],
                passed=True,
                observed={
                    "deployment_uid": before["production"]["deployment"]["uid"],
                    "ready_replicas": before["production"]["deployment"]["ready_replicas"],
                    "device": before["production"]["inference"].get("device"),
                },
            ),
        ],
        injection=InjectionEvidence(
            method="isolated_derived_fixture_and_controlled_replay",
            action="validate signed corruptions without canonical or runtime mutation",
            target={"kind": "data-artifact", "uid": canonical.manifest_id},
            expected_effect="stable fail-closed blocker and zero deployment intent",
            blast_radius="F-drive Scenario E evidence root only",
            performed=True,
        ),
        signals=[
            SignalEvidence(
                signal_id="canonical_admission",
                source=str(canonical_path.resolve()),
                observed_at=finished_at,
                healthy=True,
                detail={"replays": len(canonical_results)},
            ),
            SignalEvidence(
                signal_id="corruption_matrix",
                source=str(fixture_path.resolve()),
                observed_at=finished_at,
                healthy=True,
                detail={
                    "fixtures": fixture_payload["fixture_count"],
                    "replays": fixture_payload["replay_count"],
                },
            ),
            SignalEvidence(
                signal_id="runtime_invariants",
                source=str(diff_path.resolve()),
                observed_at=finished_at,
                healthy=True,
                detail={"production_mutation": False},
            ),
        ],
        decision=DecisionEvidence(
            expected="all isolated corruptions blocked and canonical subject admitted",
            observed="fixture matrix and canonical replays passed with zero downstream mutation",
            blocker_codes=[],
        ),
        mitigation={
            "action": "quarantine failed derived bundle and retain immutable evidence",
            "production_rollback": "not_required_no_production_mutation",
        },
        recovery=RecoveryEvidence(
            action="correct isolated manifest or identity and re-sign with exact trust root",
            target_identity={"manifest_id": canonical.manifest_id},
            result="corrected_isolated_bundle_admitted",
        ),
        postconditions=[
            CheckEvidence(check_id="canonical_three_run_pass", passed=True, observed={"runs": len(canonical_results)}),
            CheckEvidence(check_id="fixture_matrix_pass", passed=True, observed={"fixtures": fixture_payload["fixture_count"], "replays": fixture_payload["replay_count"]}),
            CheckEvidence(check_id="decision_fingerprint_deterministic", passed=True, observed={"canonical_fingerprint": canonical_results[0].decision_fingerprint}),
            CheckEvidence(check_id="deployment_intent_delta_zero", passed=True, observed=diff["checks"]["ledger_count"]),
            CheckEvidence(check_id="canonical_digest_delta_zero", passed=True, observed={"subjects": len(before["mutation"]["canonical_digests"])}),
            CheckEvidence(check_id="production_identity_unchanged", passed=True, observed={"uid": after["production"]["deployment"]["uid"], "model_digest": after["production"]["ready"]["model_sha256"]}),
            CheckEvidence(check_id="gpu_device_plugin_unchanged", passed=True, observed={"gpu": after["production"]["gpu_allocatable"], "plugin": after["production"]["device_plugin"]}),
            CheckEvidence(check_id="cuda_inference_healthy", passed=True, observed={"device": after["production"]["inference"].get("device"), "prediction": after["production"]["inference"].get("prediction")}),
            CheckEvidence(check_id="prometheus_targets_up", passed=True, observed=after["production"]["prometheus_targets"]),
            CheckEvidence(check_id="admission_pointer_valid", passed=True, observed={"blockers": admission_gate_blockers}),
            CheckEvidence(
                check_id="validation_latency_within_slo",
                passed=max_validation <= float(policy["max_validation_seconds"]),
                observed={"max_seconds": max_validation, "slo_seconds": policy["max_validation_seconds"]},
            ),
        ],
        artifacts=artifacts,
        limitations=[
            "single-node local filesystem and Docker Desktop Kubernetes only",
            "local Ed25519 key is not backed by KMS, HSM, Sigstore or a transparency log",
            "controlled replay is not production traffic or multi-writer object-store validation",
            "legacy B0 training source revision is admitted only by an exact expiring exception",
        ],
        portfolio=PortfolioEvidence(
            competencies=[
                "data contracts and cross-split leakage control",
                "signed artifact identity and fail-closed release admission",
                "immutable evidence, audit and exception governance",
            ],
            interview_questions=[
                "Why separate manifest byte SHA from semantic dataset identity?",
                "Where must integrity be revalidated to prevent TOCTOU?",
                "How would the local Ed25519 root migrate to KMS or Sigstore?",
            ],
            trade_offs=[
                "full exact scans favor correctness over incremental validation latency",
                "short-lived signed admission reduces repeated scans but creates a TOCTOU window",
                "a narrow legacy exception preserves service continuity while retaining provenance debt",
            ],
            factual_claims=[
                "validated real VisA identities and isolated signed corruption fixtures",
                "blocked every admitted corruption class before downstream intent",
                "preserved exact production B0, GPU and canonical data identities",
            ],
            prohibited_claims=[
                "enterprise PKI, KMS, SLSA or Sigstore compliance",
                "HA, production traffic or organization-wide governance validation",
            ],
        ),
        timing=TimingEvidence(
            audit_started_at=started_at,
            audit_finished_at=finished_at,
            monotonic_started_ns=monotonic_started,
            monotonic_finished_ns=monotonic_finished,
            sample_cadence_seconds=1.0,
            signal_precedence=list(policy["signal_precedence"]),
        ),
        readiness_closure=ClosureEvidence(
            decision="passed",
            required_check_ids=precondition_ids,
            completed_at=finished_at,
        ),
        live_proof_closure=ClosureEvidence(
            decision="passed",
            required_check_ids=postcondition_ids,
            completed_at=finished_at,
        ),
    )
    report_path = run_root / "report.json"
    json_write(report_path, report.model_dump(mode="json"))
    closure_errors = validate_closure(report, "live_proof")
    if closure_errors:
        raise RuntimeError(f"scenario_e_common_evidence_invalid:{closure_errors}")

    evidence_files = [path for path in run_root.rglob("*") if path.is_file()]
    evidence_index = {
        "schema_version": "evm.scenario_e_evidence_index.v1",
        "run_id": run_id,
        "artifacts": [
            {"uri": str(path.resolve()), "sha256": sha256_file(path)}
            for path in sorted(evidence_files)
        ],
    }
    index_path = run_root / "evidence-index.json"
    json_write(index_path, evidence_index)

    latest_signed = latest / "signed-trust-manifest.json"
    latest_validation = latest / "canonical-validation.json"
    latest_admission = latest / "integrity-admission.json"
    shutil.copy2(signed_path, latest_signed)
    shutil.copy2(validation_path, latest_validation)
    latest_admission_payload = IntegrityAdmission.model_validate(
        {
            **admission.model_dump(mode="json"),
            "signed_manifest_uri": mount_uri(latest_signed, Path(config["paths"]["host_data_root"])),
            "signed_manifest_sha256": sha256_file(latest_signed),
            "validation_uri": mount_uri(latest_validation, Path(config["paths"]["host_data_root"])),
            "validation_sha256": sha256_file(latest_validation),
        }
    )
    json_write(latest_admission, latest_admission_payload.model_dump(mode="json"))
    json_write(
        latest / "latest-run.json",
        {
            "run_id": run_id,
            "run_uri": str(run_root.resolve()),
            "report_uri": str(report_path.resolve()),
            "report_sha256": sha256_file(report_path),
            "evidence_index_uri": str(index_path.resolve()),
            "evidence_index_sha256": sha256_file(index_path),
            "source_revision": head,
            "passed": True,
        },
    )
    global_metrics = Path(config["paths"]["host_data_root"]) / "artifacts/operations/failure_scenarios/_latest/metrics.json"
    json_write(global_metrics, metric.model_dump(mode="json"))
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Scenario E integrity validation.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args()
    report_path = run(args.config.resolve(), args.project_root.resolve())
    print(json.dumps({"report_uri": str(report_path.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
