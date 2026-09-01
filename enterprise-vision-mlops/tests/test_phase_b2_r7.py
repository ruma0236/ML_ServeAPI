from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from evm.scale_validation import phase_b2_r7 as r7


REVISION = "a" * 40
TREE = "b" * 40
SHA = "c" * 64
IMAGE = "sha256:" + "d" * 64


def _historical_classifications() -> list[dict[str, object]]:
    values = (
        ("control_plane_task_entity_statuses", 36, 0, 0, 36, "unproven"),
        ("mlflow_running_rows", 1, 0, 0, 1, "unproven"),
        ("kubernetes_terminal_failed_objects", 11, 0, 11, 0, "historical_nonexecuting"),
    )
    return [
        {
            "source": source,
            "observed_count": observed,
            "executing_count": executing,
            "historical_count": historical,
            "unproven_count": unproven,
            "classification": classification,
            "attestation": {"path": f"C:/attestations/{source}.json", "sha256": SHA},
        }
        for source, observed, executing, historical, unproven, classification in values
    ]


def _service_pins() -> dict[str, dict[str, object]]:
    pins: dict[str, dict[str, object]] = {}
    for index, service in enumerate(r7.LONG_LIVED_SERVICES, start=1):
        image = (
            IMAGE if service in {"api", "task-queue-worker"} else ("sha256:" + f"{index + 20:064x}")
        )
        pins[service] = {
            "container_name": "evm-" + service,
            "container_id": f"{index:064x}",
            "image_id": image,
            "healthcheck_expected": service
            not in {"grafana", "minio", "otel-collector", "prometheus"},
        }
    pins["api"]["container_name"] = "evm-api"
    pins["task-queue-worker"]["container_name"] = "evm-task-queue-worker"
    return pins


def manifest() -> dict[str, object]:
    runtime = {
        name: {
            "path": f"C:/repository/{name}.py",
            "sha256": SHA,
            "blob_oid": REVISION,
            "bytes": 10,
        }
        for name in r7.RUNTIME_COMPONENTS
    }
    parents = [
        {
            "role": role,
            "kind": role,
            "path": f"C:/parents/{role}.json",
            "sha256": SHA,
            "immutable": True,
            "must_not_execute": True,
        }
        for role in r7.PARENT_CHECKPOINT_ROLES
    ]
    failed_pods = [
        {
            "uid": str(uuid.UUID(int=index)),
            "namespace": "evm-production",
            "name": f"evm-b0-production-r7-{index:02d}",
            "reason": "UnexpectedAdmissionError",
            "owner_uid": str(uuid.UUID(int=100)),
        }
        for index in range(1, 12)
    ]
    expected_state = {
        "compose": {
            "project_name": "enterprise-vision-mlops",
            "config_path": "C:/repository/docker-compose.yml",
            "config_sha256": SHA,
            "long_lived_services": list(r7.LONG_LIVED_SERVICES),
            "one_shot_services": list(r7.ONE_SHOT_SERVICES),
            "service_pins": _service_pins(),
            "stability": {
                "duration_seconds": 300,
                "interval_seconds": 5,
                "samples": 61,
                "restart_delta": 0,
            },
        },
        "api": {
            "base_url": "http://127.0.0.1:8000",
            "api_container_name": "evm-api",
            "worker_container_name": "evm-task-queue-worker",
            "image_id": IMAGE,
            "source_revision": REVISION,
            "source_tree": TREE,
            "image_attestation": {"path": "C:/attestations/api.json", "sha256": SHA},
        },
        "database": {
            "instances": copy.deepcopy(r7.DATABASE_INSTANCES),
            "control_plane_schema_versions": [
                "001_transactional_control_plane",
                "002_bounded_admission_queue",
            ],
            "mlflow_migration_head": r7.MLFLOW_MIGRATION_HEAD,
            "airflow_migration_head": r7.AIRFLOW_MIGRATION_HEAD,
        },
        "kubernetes": {
            "allowed_historical_failed_pods": failed_pods,
            "health_confirmation_samples": 2,
            "residual_selectors": ["evm.openai.local/scenario=s8-v4-x1"],
        },
        "compose_services": list(r7.LONG_LIVED_SERVICES),
        "api_base_url": "http://127.0.0.1:8000",
        "b0": {
            "uid": "cfdab424-dcc5-4d5f-a46f-ae7530441ef4",
            "image": "evm-b0@sha256:" + "e" * 64,
        },
        "prometheus_jobs": list(r7.PROMETHEUS_JOBS),
        "prometheus_targets_url": "http://127.0.0.1:9090/api/v1/targets",
        "gpu_lease_path": "F:/data/runtime/gpu-lease/active.json",
        "active_job_roots": [],
        "active_claim_roots": [],
        "x1_residue_paths": ["F:/data/x1-a.json", "F:/data/x1-b.json"],
        "x1_docker_name_filter": "name=evm-x1",
        "x1_ports": [31120, 31121, 31122],
        "x1_kubernetes_selectors": ["evm.openai.local/scenario=s8-v4-x1"],
    }
    return {
        "schema_version": r7.SCHEMA_VERSION,
        "work_order_id": r7.WORK_ORDER_ID,
        "bundle_id": "x1-phase-b2-r7-test-restore",
        "execution_mode": "restore-only",
        "created_at": "2026-09-01T00:00:00Z",
        "canonical_revision": REVISION,
        "canonical_tree": TREE,
        "bundle": {"path": "C:/bundle/r7"},
        "output": {
            "path": "C:/output/r7",
            "must_not_exist_before_runner": True,
            "write_mode": "create-exclusive",
        },
        "timeout_contract": r7.TimeoutContract().to_dict(),
        "lifecycle_timeout_contract": r7.LifecycleTimeoutContract().to_dict(),
        "process_containment": dict(r7.PROCESS_CONTAINMENT_CONTRACT),
        "probe_max_attempts": 1,
        "call_contract": {
            "restore-only": dict(r7.RESTORE_LIFECYCLE_COUNTS),
            "launcher": dict(r7.LAUNCHER_COUNTS),
            "collectors": dict(r7.RESTORE_COLLECTOR_COUNTS),
            "downstream": dict(r7.DOWNSTREAM_COUNTS),
        },
        "repository": {
            "preserved_untracked_count": r7.PRESERVED_UNTRACKED_COUNT,
            "untracked_path_set_sha256": SHA,
            "untracked_path_set_encoding": r7.UNTRACKED_PATH_SET_ENCODING,
            "tracked_changes": 0,
        },
        "job_scope_contract": {
            **copy.deepcopy(r7.JOB_SCOPE_CONTRACT),
            "historical_classifications": _historical_classifications(),
        },
        "expected_state": expected_state,
        "parent_checkpoints": parents,
        "evidence": {
            "write_mode": "create-exclusive",
            "failure_creates_completion_marker": False,
            "restore_only_creates_completion_marker": False,
            "failure_index_is_not_success_index": True,
            "success_requires_all_invariants": True,
        },
        "etw_contract": {
            "decision": (
                "existing_pinned_etw_evidence_is_admissible;"
                "fresh_capture_not_a_phase_b2_go_invariant"
            ),
            "amendment_path": "C:/attestations/etw-amendment.json",
            "amendment_sha256": SHA,
            "fresh_capture_required_for_phase_b2_go": False,
            "fresh_invocations": 0,
        },
        "runtime": runtime,
    }


def _execution_proof(*, inactivity_proven: bool) -> dict[str, object]:
    return {
        "inactivity_proven": inactivity_proven,
        "active_job_count": 0,
        "active_claim_count": 0,
        "active_lease_count": 0,
        "outcome_unknown_count": 0,
        "evidence": {"path": "pending", "sha256": SHA},
    }


def _materialize_historical_attestations(value: dict[str, object], root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    classifications = value["job_scope_contract"]["historical_classifications"]
    pods = value["expected_state"]["kubernetes"]["allowed_historical_failed_pods"]
    payloads: dict[str, dict[str, object]] = {}
    for item in classifications:
        source = item["source"]
        if source == "control_plane_task_entity_statuses":
            records = [
                {
                    "identity": {
                        "entity_id": f"task-{index:02d}",
                        "created_at": "2026-07-01T00:00:00Z",
                        "updated_at": "2026-07-01T00:00:00Z",
                    },
                    "observed_state": "queued",
                    "classification": "unproven",
                    "execution_proof": _execution_proof(inactivity_proven=False),
                }
                for index in range(item["observed_count"])
            ]
        elif source == "mlflow_running_rows":
            records = [
                {
                    "identity": {
                        "run_id": "a" * 32,
                        "lifecycle_stage": "active",
                        "start_time": "2026-07-01T00:00:00Z",
                        "end_time": "not-recorded",
                    },
                    "observed_state": "RUNNING",
                    "classification": "unproven",
                    "execution_proof": _execution_proof(inactivity_proven=False),
                }
            ]
        else:
            records = [
                {
                    "identity": {
                        "uid": pod["uid"],
                        "namespace": pod["namespace"],
                        "name": pod["name"],
                        "owner_uid": pod["owner_uid"],
                        "reason": pod["reason"],
                    },
                    "observed_state": "Failed",
                    "classification": "historical_nonexecuting",
                    "execution_proof": _execution_proof(inactivity_proven=True),
                }
                for pod in pods
            ]
        payloads[source] = {
            "source": source,
            "captured_at": "2026-09-01T00:00:00Z",
            "query_sha256": r7.HISTORICAL_QUERY_SHA256[source],
            "counts": {
                name: item[name]
                for name in (
                    "observed_count",
                    "executing_count",
                    "historical_count",
                    "unproven_count",
                )
            },
            "classification": item["classification"],
            "records": records,
        }
    paths: dict[str, Path] = {}
    proof_root = root / "proofs"
    proof_root.mkdir(parents=True, exist_ok=True)
    for source, payload in payloads.items():
        for index, record in enumerate(payload["records"]):
            execution_proof = record["execution_proof"]
            active_total = sum(
                execution_proof[name]
                for name in (
                    "active_job_count",
                    "active_claim_count",
                    "active_lease_count",
                    "outcome_unknown_count",
                )
            )
            decision = (
                "proven_inactive"
                if execution_proof["inactivity_proven"] is True
                else "executing"
                if active_total
                else "unproven"
            )
            proof_payload = {
                "source": source,
                "identity": record["identity"],
                "observed_state": record["observed_state"],
                "captured_at": payload["captured_at"],
                "query_sha256": payload["query_sha256"],
                "active_job_count": execution_proof["active_job_count"],
                "active_claim_count": execution_proof["active_claim_count"],
                "active_lease_count": execution_proof["active_lease_count"],
                "outcome_unknown_count": execution_proof["outcome_unknown_count"],
                "inactivity_decision": decision,
                "decision_authority": r7.HISTORICAL_DECISION_AUTHORITY,
            }
            proof_path = proof_root / f"{source}-{index:03d}.json"
            proof_path.write_text(json.dumps(proof_payload), encoding="utf-8")
            execution_proof["evidence"] = {
                "path": str(proof_path),
                "sha256": r7.sha256_file(proof_path),
            }
    for item in classifications:
        source = item["source"]
        path = root / f"{source}.json"
        path.write_text(json.dumps(payloads[source]), encoding="utf-8")
        item["attestation"] = {"path": str(path), "sha256": r7.sha256_file(path)}
        paths[source] = path
    return paths


def _passing_restore_report() -> dict[str, object]:
    stages = [
        {
            "stage": stage.value,
            "started_at": "2026-09-01T00:00:00Z",
            "ended_at": "2026-09-01T00:00:01Z",
            "duration_seconds": 1.0,
            "attempts": 1,
            "max_attempts": 1,
            "passed": True,
            "retryable_ignored": False,
            "last_error": None,
            "manual_intervention_required": False,
            "residual_pids": [],
            "invariants": {},
            "details": {},
            "deadline_remaining_seconds": 500.0,
        }
        for stage in r7.RESTORE_STAGE_ORDER
    ]
    invariant_names = {
        *r7.R7_REQUIRED_INVARIANTS,
        *(stage.value for stage in r7.RESTORE_STAGE_ORDER),
    }
    return {
        "mode": "restore-only",
        "decision": "restore_only_pass",
        "deadline_exceeded": False,
        "passed": True,
        "restore_only_pass": True,
        "acceptance_credit": False,
        "completion_marker_created": False,
        "phase_b2_executed": False,
        "manual_intervention_required": False,
        "residual_pids": [],
        "call_counts": dict(r7.RESTORE_LIFECYCLE_COUNTS),
        "required_invariants": list(r7.R7_REQUIRED_INVARIANTS),
        "success_invariants": {name: True for name in invariant_names},
        "stages": stages,
    }


def test_restore_only_manifest_accepts_exact_contract() -> None:
    result = r7.validate_r7_manifest(manifest(), expected_revision=REVISION)
    assert result["mode"] == "restore-only"
    assert result["launcher_calls"] == r7.LAUNCHER_COUNTS
    assert set(result["parents"]) == set(r7.PARENT_CHECKPOINT_ROLES)
    assert (
        result["job_scope_contract"]["historical_classifications"][1]["classification"]
        == "unproven"
    )


def test_historical_attestation_semantics_are_read_and_fail_closed(tmp_path: Path) -> None:
    value = manifest()
    paths = _materialize_historical_attestations(value, tmp_path)
    result = r7.validate_r7_manifest(
        value,
        expected_revision=REVISION,
        verify_attestations=True,
    )
    assert result["job_scope_contract"]["historical_classifications"][0]["unproven_count"] == 36

    cp_path = paths["control_plane_task_entity_statuses"]
    cp = json.loads(cp_path.read_text(encoding="utf-8"))
    cp["records"][0]["classification"] = "historical_nonexecuting"
    cp_path.write_text(json.dumps(cp), encoding="utf-8")
    value["job_scope_contract"]["historical_classifications"][0]["attestation"]["sha256"] = (
        r7.sha256_file(cp_path)
    )
    with pytest.raises(r7.R7ContractError, match="inactivity_proof_required"):
        r7.validate_r7_manifest(
            value,
            expected_revision=REVISION,
            verify_attestations=True,
        )


def test_historical_attestation_valid_sha_does_not_excuse_empty_or_drifted_payload(
    tmp_path: Path,
) -> None:
    value = manifest()
    paths = _materialize_historical_attestations(value, tmp_path)
    mlflow_path = paths["mlflow_running_rows"]
    mlflow_path.write_text("{}", encoding="utf-8")
    value["job_scope_contract"]["historical_classifications"][1]["attestation"]["sha256"] = (
        r7.sha256_file(mlflow_path)
    )
    with pytest.raises(r7.R7ContractError, match="source_or_fields_mismatch"):
        r7.validate_r7_manifest(
            value,
            expected_revision=REVISION,
            verify_attestations=True,
        )

    value = manifest()
    paths = _materialize_historical_attestations(value, tmp_path / "proof-drift")
    cp_path = paths["control_plane_task_entity_statuses"]
    cp = json.loads(cp_path.read_text(encoding="utf-8"))
    proof_path = Path(cp["records"][0]["execution_proof"]["evidence"]["path"])
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    proof["decision_authority"] = ""
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    cp["records"][0]["execution_proof"]["evidence"]["sha256"] = r7.sha256_file(proof_path)
    cp_path.write_text(json.dumps(cp), encoding="utf-8")
    value["job_scope_contract"]["historical_classifications"][0]["attestation"]["sha256"] = (
        r7.sha256_file(cp_path)
    )
    with pytest.raises(r7.R7ContractError, match="decision_authority"):
        r7.validate_r7_manifest(
            value,
            expected_revision=REVISION,
            verify_attestations=True,
        )

    value = manifest()
    paths = _materialize_historical_attestations(value, tmp_path / "identity-drift")
    k8s_path = paths["kubernetes_terminal_failed_objects"]
    k8s = json.loads(k8s_path.read_text(encoding="utf-8"))
    k8s["records"][0]["identity"]["uid"] = str(uuid.uuid4())
    k8s_path.write_text(json.dumps(k8s), encoding="utf-8")
    value["job_scope_contract"]["historical_classifications"][2]["attestation"]["sha256"] = (
        r7.sha256_file(k8s_path)
    )
    with pytest.raises(r7.R7ContractError, match="proof_identity_mismatch"):
        r7.validate_r7_manifest(
            value,
            expected_revision=REVISION,
            verify_attestations=True,
        )


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda value: value.update(execution_mode="fresh"), "restore_only"),
        (lambda value: value.update(probe_max_attempts=2), "probe_max_attempts"),
        (
            lambda value: value["call_contract"]["restore-only"].update(compose_start=1),
            "exact_counts",
        ),
        (
            lambda value: value["process_containment"].update(force_termination_attempts=1),
            "containment",
        ),
        (
            lambda value: value["repository"].update(preserved_untracked_count=4243),
            "untracked_count",
        ),
        (
            lambda value: value["expected_state"]["api"].update(source_revision="f" * 40),
            "api_source_revision",
        ),
        (
            lambda value: value["expected_state"]["database"].update(mlflow_migration_head="bad"),
            "mlflow_migration_head",
        ),
        (
            lambda value: value["job_scope_contract"]["historical_classifications"][1].update(
                classification="historical_nonexecuting"
            ),
            "classification_label",
        ),
        (
            lambda value: value["expected_state"]["kubernetes"][
                "allowed_historical_failed_pods"
            ].pop(),
            "count_must_equal_11",
        ),
        (
            lambda value: value["expected_state"]["kubernetes"].update(
                health_confirmation_samples=1
            ),
            "health_confirmation_samples_must_equal_2",
        ),
        (
            lambda value: value["parent_checkpoints"].pop(),
            "parent_checkpoint_count",
        ),
        (
            lambda value: value["parent_checkpoints"][0].update(path="C:/bundle/r7/old.json"),
            "inside_bundle",
        ),
        (
            lambda value: value["runtime"].pop("docker_compose"),
            "runtime_component_role_set",
        ),
    ],
)
def test_manifest_mutations_fail_closed(mutator: object, match: str) -> None:
    value = manifest()
    mutator(value)  # type: ignore[operator]
    with pytest.raises(r7.R7ContractError, match=match):
        r7.validate_r7_manifest(value, expected_revision=REVISION)


def test_control_plane_migration_pin_must_be_nonempty_unique_and_canonical() -> None:
    value = manifest()
    versions = value["expected_state"]["database"]["control_plane_schema_versions"]
    versions.append(versions[0])
    with pytest.raises(r7.R7ContractError, match="nonempty_unique"):
        r7.validate_r7_manifest(value, expected_revision=REVISION)


def test_runtime_sha_blob_and_byte_pins_are_measured(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "r7@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Phase B2 r7"], check=True)
    value = manifest()
    runtime: dict[str, dict[str, object]] = {}
    for name in r7.RUNTIME_COMPONENTS:
        path = tmp_path / f"{name}.py"
        path.write_text(f"# {name}\n", encoding="utf-8")
        runtime[name] = {
            "path": str(path),
            "sha256": r7.sha256_file(path),
            "blob_oid": REVISION,
            "bytes": path.stat().st_size,
        }
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "fixture"], check=True)
    for name in r7.RUNTIME_COMPONENTS:
        runtime[name]["blob_oid"] = r7.git_head_blob_oid(tmp_path, Path(str(runtime[name]["path"])))
    value["runtime"] = runtime
    measured = r7.validate_runtime_pins(value, tmp_path)
    assert set(measured) == set(r7.RUNTIME_COMPONENTS)
    Path(str(runtime["runner"]["path"])).write_text("# mutation\n", encoding="utf-8")
    with pytest.raises(r7.R7ContractError, match="runner_sha256_mismatch"):
        r7.validate_runtime_pins(value, tmp_path)


def _write_parents(tmp_path: Path) -> tuple[list[dict[str, object]], dict[str, Path]]:
    entries: list[dict[str, object]] = []
    paths: dict[str, Path] = {}
    for role in r7.PARENT_CHECKPOINT_ROLES:
        payload: dict[str, object] = {
            "acceptance_credit": False,
            "success_marker_created": False,
            "completion_marker_created": False,
            "phase_b2_executed": False,
        }
        if role == "r5_failure_seal":
            payload.update(
                failure_only=True,
                report={"call_counts": dict(r7.RESTORE_LIFECYCLE_COUNTS)},
            )
        if role == "r5_failure_index":
            payload["failure_only"] = True
        path = tmp_path / f"{role}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[role] = path
        entries.append(
            {
                "role": role,
                "kind": role,
                "path": str(path),
                "sha256": r7.sha256_file(path),
                "immutable": True,
                "must_not_execute": True,
            }
        )
    return entries, paths


def test_seven_parent_checkpoint_set_is_read_only_and_semantic(tmp_path: Path) -> None:
    entries, paths = _write_parents(tmp_path)
    before = {role: r7.sha256_file(path) for role, path in paths.items()}
    payloads, checkpoint = r7.read_parent_checkpoints(entries)
    after = {role: r7.sha256_file(path) for role, path in paths.items()}
    assert before == after
    assert set(payloads) == set(r7.PARENT_CHECKPOINT_ROLES)
    assert checkpoint.source == "r7_seven_parent_checkpoint_set"
    assert checkpoint.historical_call_counts == r7.RESTORE_LIFECYCLE_COUNTS
    assert all(not checkpoint.permits(name) for name in r7.RESTORE_LIFECYCLE_COUNTS)
    assert all(not checkpoint.permits(name) for name in r7.RESTORE_COLLECTOR_COUNTS)
    assert all(not checkpoint.permits(name) for name in r7.DOWNSTREAM_COUNTS)

    paths["r6_compose_rca"].write_text("{}", encoding="utf-8")
    with pytest.raises(r7.R7ContractError, match="sha256_mismatch"):
        r7.read_parent_checkpoints(entries)


def test_launcher_evidence_requires_full_chain_token_and_exact_counts() -> None:
    value = manifest()
    chain = {name: SHA for name in ("outer", "bridge", "manifest")}
    chain.update({name: SHA for name in r7.RUNTIME_COMPONENTS})
    chain.update({role: SHA for role in r7.PARENT_CHECKPOINT_ROLES})
    evidence = {
        "schema": r7.LAUNCHER_EVIDENCE_SCHEMA,
        "token_evidence": {
            "administrator": True,
            "integrity": "High",
            "token_elevation_type": "Full",
        },
        "sha_chain": chain,
        "git": {},
        "run_id": value["bundle_id"],
        "mode": "restore-only",
        "invocation_counts": dict(r7.LAUNCHER_COUNTS),
    }
    encoded = base64.b64encode(json.dumps(evidence).encode()).decode()
    assert r7.decode_launcher_evidence(encoded, value)["invocation_counts"]["runner"] == 1

    extra = copy.deepcopy(evidence)
    extra["unexpected"] = True
    with pytest.raises(r7.R7ContractError, match="top_level_fields"):
        r7.decode_launcher_evidence(base64.b64encode(json.dumps(extra).encode()).decode(), value)

    wrong_run = copy.deepcopy(evidence)
    wrong_run["run_id"] = "x1-phase-b2-r7-different"
    with pytest.raises(r7.R7ContractError, match="run_id_mismatch"):
        r7.decode_launcher_evidence(
            base64.b64encode(json.dumps(wrong_run).encode()).decode(), value
        )

    wrong_mode = copy.deepcopy(evidence)
    wrong_mode["mode"] = "fresh"
    with pytest.raises(r7.R7ContractError, match="mode_mismatch"):
        r7.decode_launcher_evidence(
            base64.b64encode(json.dumps(wrong_mode).encode()).decode(), value
        )

    evidence["invocation_counts"]["runner"] = 2
    encoded = base64.b64encode(json.dumps(evidence).encode()).decode()
    with pytest.raises(r7.R7ContractError, match="exact_counts"):
        r7.decode_launcher_evidence(encoded, value)

    evidence["invocation_counts"] = dict(r7.LAUNCHER_COUNTS)
    evidence["token_evidence"]["integrity"] = "not-high"
    encoded = base64.b64encode(json.dumps(evidence).encode()).decode()
    with pytest.raises(r7.R7ContractError, match="high_or_system"):
        r7.decode_launcher_evidence(encoded, value)


def test_evidence_writer_loops_partial_writes_and_fsyncs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = r7.EvidenceWriter(tmp_path / "evidence")
    real_write = os.write
    writes: list[int] = []
    fsyncs: list[int] = []

    def partial_write(descriptor: int, payload: object) -> int:
        raw = bytes(payload)[:3]
        writes.append(len(raw))
        return real_write(descriptor, raw)

    monkeypatch.setattr(r7.os, "write", partial_write)
    monkeypatch.setattr(r7.os, "fsync", lambda descriptor: fsyncs.append(descriptor))
    identity = writer.write_bytes("partial.bin", b"0123456789")
    assert (writer.root / "partial.bin").read_bytes() == b"0123456789"
    assert identity["bytes"] == 10
    assert len(writes) == 4
    assert len(fsyncs) == 1
    publish_source = writer.root / ".partial.bin.publish-source"
    assert publish_source.read_bytes() == b"0123456789"
    assert os.path.samefile(publish_source, writer.root / "partial.bin")


def test_partial_write_failure_remains_append_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = r7.EvidenceWriter(tmp_path / "partial-failure")
    real_write = os.write
    calls = 0

    def fail_after_prefix(descriptor: int, payload: object) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, bytes(payload)[:4])
        raise OSError("injected_partial_write_failure")

    monkeypatch.setattr(r7.os, "write", fail_after_prefix)
    with pytest.raises(OSError, match="injected"):
        writer.write_bytes("sealed.bin", b"abcdefgh")
    assert not (writer.root / "sealed.bin").exists()
    publish_source = writer.root / ".sealed.bin.publish-source"
    assert publish_source.read_bytes() == b"abcd"
    with pytest.raises(r7.R7EvidenceExistsError):
        writer.write_bytes("sealed.bin", b"replacement")


def test_json_hash_and_parse_share_one_byte_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = (tmp_path / "snapshot.json").resolve()
    original = b'{"state":"pinned"}'
    replacement = b'{"state":"swapped"}'
    path.write_bytes(original)
    real_read_bytes = Path.read_bytes
    reads = 0

    def changing_read_bytes(candidate: Path) -> bytes:
        nonlocal reads
        if candidate.resolve() != path:
            return real_read_bytes(candidate)
        reads += 1
        return original if reads == 1 else replacement

    monkeypatch.setattr(Path, "read_bytes", changing_read_bytes)
    payload, measured_sha = r7._read_json_snapshot(path)

    assert reads == 1
    assert payload == {"state": "pinned"}
    assert measured_sha == hashlib.sha256(original).hexdigest()


def test_failure_and_restore_seals_never_create_completion_marker(tmp_path: Path) -> None:
    failed = r7.EvidenceWriter(tmp_path / "failed")
    result = failed.seal_failure({"passed": False})
    assert result["failure_index"]["sha256"]
    for leaf in ("failure-evidence-index.json", "failure-seal.json"):
        source = failed.root / f".{leaf}.publish-source"
        final = failed.root / leaf
        assert os.path.samefile(source, final)
        assert r7.sha256_file(source) == r7.sha256_file(final)
    failure_index = json.loads((failed.root / "failure-evidence-index.json").read_text())
    indexed = {item["path"]: item for item in failure_index["files"]}
    assert (
        indexed[".failure-seal.json.publish-source"]["sha256"]
        == indexed["failure-seal.json"]["sha256"]
    )
    assert not (failed.root / "completion-marker.json").exists()
    with pytest.raises(r7.R7EvidenceExistsError):
        failed.seal_failure({"passed": False})

    passed = r7.EvidenceWriter(tmp_path / "passed")
    report = _passing_restore_report()
    sealed = passed.seal_restore_only(report)
    assert sealed["restore_only_index"]["sha256"]
    for leaf in ("restore-only-report.json", "restore-only-index.json"):
        assert os.path.samefile(passed.root / f".{leaf}.publish-source", passed.root / leaf)
    restore_index = json.loads((passed.root / "restore-only-index.json").read_text())
    indexed = {item["path"]: item for item in restore_index["files"]}
    assert (
        indexed[".restore-only-report.json.publish-source"]["sha256"]
        == indexed["restore-only-report.json"]["sha256"]
    )
    assert not (passed.root / "completion-marker.json").exists()


def test_failure_seal_is_the_final_atomic_commit_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = r7.EvidenceWriter(tmp_path / "failure-publication")
    real_write_bytes = writer.write_bytes
    order: list[str] = []

    def fail_final_seal(name: str, payload: bytes) -> dict[str, object]:
        order.append(name)
        if name == "failure-seal.json":
            raise OSError("injected_commit_record_failure")
        return real_write_bytes(name, payload)

    monkeypatch.setattr(writer, "write_bytes", fail_final_seal)
    with pytest.raises(OSError, match="commit_record_failure"):
        writer.seal_failure({"passed": False})
    assert order == ["failure-evidence-index.json", "failure-seal.json"]
    assert not (writer.root / "failure-seal.json").exists()
    pending = json.loads((writer.root / "failure-evidence-index.json").read_text())
    assert pending["publication_state"] == "pending_until_commit_record_exists"
    assert pending["commit_record"]["path"] == "failure-seal.json"
    assert any(item["path"] == ".failure-seal.json.publish-source" for item in pending["files"])


def test_failure_seal_partial_os_write_never_publishes_commit_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = r7.EvidenceWriter(tmp_path / "failure-partial-commit")
    real_write_bytes = writer.write_bytes
    real_os_write = os.write

    def fault_final_seal(name: str, payload: bytes) -> dict[str, object]:
        if name != "failure-seal.json":
            return real_write_bytes(name, payload)
        calls = 0

        def prefix_then_fail(descriptor: int, remaining: object) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_os_write(descriptor, bytes(remaining)[:8])
            raise OSError("injected_partial_commit_write")

        monkeypatch.setattr(r7.os, "write", prefix_then_fail)
        try:
            return real_write_bytes(name, payload)
        finally:
            monkeypatch.setattr(r7.os, "write", real_os_write)

    monkeypatch.setattr(writer, "write_bytes", fault_final_seal)
    with pytest.raises(OSError, match="partial_commit"):
        writer.seal_failure({"passed": False})

    assert (writer.root / "failure-evidence-index.json").is_file()
    assert not (writer.root / "failure-seal.json").exists()
    publish_source = writer.root / ".failure-seal.json.publish-source"
    assert publish_source.stat().st_size == 8
    index = json.loads((writer.root / "failure-evidence-index.json").read_text())
    assert index["publication_state"] == "pending_until_commit_record_exists"
    assert index["commit_record"]["bytes"] > publish_source.stat().st_size


def test_restore_success_rejects_unproven_or_incomplete_invariants(tmp_path: Path) -> None:
    writer = r7.EvidenceWriter(tmp_path / "blocked")
    report = _passing_restore_report()
    report["success_invariants"]["historical_control_plane_tasks_classified"] = False
    with pytest.raises(r7.R7SuccessInvariantError, match="required_invariants"):
        writer.seal_restore_only(report)
    assert not (writer.root / "restore-only-index.json").exists()


def test_restore_types_are_r7_local_and_probe_false_is_fail_closed() -> None:
    assert r7.RestoreStage.COMPOSE.value == "compose"
    assert r7.RestoreStage.__module__ == r7.__name__
    assert r7.RestoreCheckpoint.__module__ == r7.__name__
    assert not r7.ProbeResult.normalize({"passed": True, "invariants": {"known": False}}).passed


def test_reconcile_safe_failure_continues_each_read_only_stage_once_without_retry() -> None:
    calls: list[str] = []

    def probe(stage: r7.RestoreStage) -> object:
        def invoke(_deadline: r7.RestoreDeadline) -> dict[str, object]:
            calls.append(stage.value)
            if stage is r7.RestoreStage.COMPOSE:
                return {
                    "passed": False,
                    "retryable": True,
                    "last_error": "compose_observation_failed",
                }
            return {"passed": True}

        return invoke

    harness = r7.ReconcileRestoreHarness(
        probes={stage: probe(stage) for stage in r7.RESTORE_STAGE_ORDER},
        max_probe_attempts=1,
    )
    report = harness.run_restore_only(
        r7.RestoreCheckpoint(
            source="test-parent-set",
            historical_call_counts=dict(r7.RESTORE_LIFECYCLE_COUNTS),
        )
    )

    assert calls == [stage.value for stage in r7.RESTORE_STAGE_ORDER]
    assert len(report.stages) == len(r7.RESTORE_STAGE_ORDER)
    assert all(stage["attempts"] == stage["max_attempts"] == 1 for stage in report.stages)
    assert report.stages[1]["retryable_ignored"] is True
    assert report.call_counts == r7.RESTORE_LIFECYCLE_COUNTS
    assert not report.passed
    assert report.manual_intervention_required
    assert report.last_error == "compose_observation_failed"


@pytest.mark.parametrize(
    "unsafe_result",
    [
        {
            "passed": False,
            "last_error": "manual_latch",
            "manual_intervention_required": True,
        },
        {
            "passed": False,
            "last_error": "residual_latch",
            "residual_pids": [701, 702],
        },
    ],
)
def test_reconcile_manual_or_residual_latch_blocks_all_followup_probes(
    unsafe_result: dict[str, object],
) -> None:
    calls: list[str] = []

    def first(_deadline: r7.RestoreDeadline) -> dict[str, object]:
        calls.append(r7.RestoreStage.DOCKER_ENGINE.value)
        return unsafe_result

    def forbidden(_deadline: r7.RestoreDeadline) -> dict[str, object]:
        calls.append("forbidden_followup")
        return {"passed": True}

    probes = {stage: forbidden for stage in r7.RESTORE_STAGE_ORDER}
    probes[r7.RestoreStage.DOCKER_ENGINE] = first
    report = r7.ReconcileRestoreHarness(probes=probes).run_restore_only(
        r7.RestoreCheckpoint(
            source="test-parent-set",
            historical_call_counts=dict(r7.RESTORE_LIFECYCLE_COUNTS),
        )
    )

    assert calls == [r7.RestoreStage.DOCKER_ENGINE.value]
    assert len(report.stages) == 1
    assert report.call_counts == r7.RESTORE_LIFECYCLE_COUNTS
    assert report.manual_intervention_required


def test_reconcile_expired_deadline_does_not_launch_probe_or_followup() -> None:
    now = 0.0
    probes_called = 0

    def advancing_clock() -> float:
        nonlocal now
        now += 601.0
        return now

    def forbidden(_deadline: r7.RestoreDeadline) -> dict[str, object]:
        nonlocal probes_called
        probes_called += 1
        return {"passed": True}

    report = r7.ReconcileRestoreHarness(
        probes={stage: forbidden for stage in r7.RESTORE_STAGE_ORDER},
        clock=advancing_clock,
    ).run_restore_only(
        r7.RestoreCheckpoint(
            source="test-parent-set",
            historical_call_counts=dict(r7.RESTORE_LIFECYCLE_COUNTS),
        )
    )

    assert probes_called == 0
    assert len(report.stages) == 1
    assert report.deadline_exceeded
    assert report.stages[0]["attempts"] == 0
    assert report.call_counts == r7.RESTORE_LIFECYCLE_COUNTS


def test_reconcile_rejects_any_probe_attempt_budget_other_than_exactly_one() -> None:
    with pytest.raises(r7.R7ContractError, match="probe_max_attempts_must_equal_1"):
        r7.ReconcileRestoreHarness(max_probe_attempts=2)


def test_reconcile_and_success_seal_reject_reduced_required_invariant_sets(
    tmp_path: Path,
) -> None:
    with pytest.raises(r7.R7ContractError, match="required_invariant_set_mismatch"):
        r7.ReconcileRestoreHarness(required_invariants=("docker_engine",))

    writer = r7.EvidenceWriter(tmp_path / "reduced-invariants")
    report = {
        "mode": "restore-only",
        "decision": "restore_only_pass",
        "deadline_exceeded": False,
        "passed": True,
        "restore_only_pass": True,
        "acceptance_credit": False,
        "completion_marker_created": False,
        "phase_b2_executed": False,
        "manual_intervention_required": False,
        "residual_pids": [],
        "call_counts": dict(r7.RESTORE_LIFECYCLE_COUNTS),
        "required_invariants": ["docker_engine"],
        "success_invariants": {"docker_engine": True},
    }
    with pytest.raises(r7.R7SuccessInvariantError, match="required_invariant_set_mismatch"):
        writer.seal_restore_only(report)
