from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GIT_ROOT = PROJECT_ROOT.parent
EXPECTED_B0_UID = "cfdab424-dcc5-4d5f-a46f-ae7530441ef4"
EXPECTED_B0_IMAGE = (
    "enterprise-vision-mlops-efficientnet-serving@"
    "sha256:227b483f466678e00fbf13fd6b3ad1059ca2c6771239d204494fb610fa7d9f7a"
)
ETW_AMENDMENT = Path(
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation\private"
    r"\s8-v4\x1-clock-phase-b2-failure-seals"
    r"\x1-clock-phase-b2-r3-failure-seal-20260831T135958Z-0a68addf"
    r"\etw-contract-amendment.json"
)
ETW_AMENDMENT_SHA256 = "71ddc50a2a91f707b8183a19c87f490bdad8421ab18446dceb21622bc3439715"
RUNTIME_PATHS = {
    "builder": Path("scripts/dev/prepare_x1_phase_b2_r7_bundle.py"),
    "core": Path("src/evm/scale_validation/phase_b2_r7.py"),
    "process": Path("src/evm/scale_validation/phase_b2_r7_process.py"),
    "runner": Path("scripts/dev/run_x1_phase_b2_r7.py"),
    "validator": Path("scripts/dev/validate_phase_b2_r7_bundle.ps1"),
    "docker_compose": Path("docker-compose.yml"),
}

RESTORE_MODE = "restore-only"
REQUIRED_PARENT_ROLES = (
    "r5_failure_seal",
    "r5_failure_index",
    "r6_compose_rca",
    "r6_failure_seal_amendment",
    "r6_final_index",
    "post_manual_on_readback",
    "post_manual_on_index",
)
PARENT_KINDS = {role: role for role in REQUIRED_PARENT_ROLES}
LONG_LIVED_SERVICES = (
    "airflow-postgres",
    "airflow-scheduler",
    "airflow-webserver",
    "api",
    "control-panel",
    "control-plane-postgres",
    "grafana",
    "minio",
    "mlflow",
    "otel-collector",
    "postgres",
    "prometheus",
    "task-queue-worker",
)
ONE_SHOT_SERVICES = ("airflow-init", "minio-create-buckets")
CONTAINER_NAMES = {
    "airflow-postgres": "evm-airflow-postgres",
    "airflow-scheduler": "evm-airflow-scheduler",
    "airflow-webserver": "evm-airflow-webserver",
    "api": "evm-api",
    "control-panel": "evm-control-panel",
    "control-plane-postgres": "evm-control-plane-postgres",
    "grafana": "evm-grafana",
    "minio": "evm-minio",
    "mlflow": "evm-mlflow",
    "otel-collector": "evm-otel-collector",
    "postgres": "evm-postgres",
    "prometheus": "evm-prometheus",
    "task-queue-worker": "evm-task-queue-worker",
    "airflow-init": "evm-airflow-init",
    "minio-create-buckets": "evm-minio-init",
}
HEALTHCHECK_EXPECTED = {
    name: name
    in {
        "airflow-postgres",
        "airflow-scheduler",
        "airflow-webserver",
        "api",
        "control-panel",
        "control-plane-postgres",
        "mlflow",
        "postgres",
        "task-queue-worker",
    }
    for name in (*LONG_LIVED_SERVICES, *ONE_SHOT_SERVICES)
}
AIRFLOW_MIGRATION_HEAD = "5f2621c13b39"
MLFLOW_MIGRATION_HEAD = "0584bdc529eb"
HISTORICAL_QUERY_TEXTS = {
    "control_plane_task_entity_statuses": (
        "SELECT entity_id,state,"
        "to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'),"
        "to_char(updated_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') "
        "FROM evm_control_plane.entities WHERE entity_kind='task_assignment' "
        "AND state IN ('queued','pending_confirmation','running') "
        "ORDER BY entity_id;"
    ),
    "mlflow_running_rows": (
        "SELECT run_uuid,status,lifecycle_stage,COALESCE(start_time::text,''),"
        "COALESCE(end_time::text,'') FROM runs WHERE status='RUNNING' ORDER BY run_uuid;"
    ),
    "kubernetes_terminal_failed_objects": (
        "kubectl get pods -A --field-selector=status.phase=Failed -o json"
    ),
}
HISTORICAL_QUERY_SHA256 = {
    source: hashlib.sha256(query.encode("utf-8")).hexdigest()
    for source, query in HISTORICAL_QUERY_TEXTS.items()
}
HISTORICAL_DECISION_AUTHORITY = "phase-b2-r7-independent-review"
RUNTIME_STATE_SCHEMA = "evm.s8_v4.x1_phase_b2_r7_runtime_state_pins.v1"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class BundleBuildError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("ascii")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise BundleBuildError(f"bundle_file_exists:{path}") from exc
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("exclusive bundle write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise BundleBuildError(f"git_command_failed:{arguments}:{result.stderr.strip()}")
    return result.stdout.strip()


def git_bytes(repository: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise BundleBuildError(
            f"git_bytes_command_failed:{arguments}:{result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return result.stdout


def untracked_identity(repository: Path) -> tuple[int, str]:
    raw = git_bytes(
        repository, "-c", "core.quotepath=false", "ls-files", "--others", "--exclude-standard", "-z"
    )
    parts = raw.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    try:
        paths = [part.decode("utf-8", errors="strict") for part in parts]
    except UnicodeDecodeError as exc:
        raise BundleBuildError("untracked_path_not_utf8") from exc
    if len(paths) != len(set(paths)):
        raise BundleBuildError("untracked_paths_duplicate")
    ordered = sorted(paths)
    digest = hashlib.sha256()
    for path in ordered:
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
    return len(ordered), digest.hexdigest()


def git_blob_oid(repository: Path, path: Path) -> str:
    relative = path.resolve().relative_to(repository.resolve()).as_posix()
    value = git(repository, "rev-parse", f"HEAD:{relative}")
    if len(value) != 40:
        raise BundleBuildError(f"git_blob_oid_invalid:{relative}:{value}")
    return value


def source_pin(project_root: Path, path: Path) -> dict[str, Any]:
    absolute = (project_root / path).resolve()
    if not absolute.is_file():
        raise BundleBuildError(f"runtime_source_missing:{path}")
    return {
        "path": str(absolute),
        "sha256": sha256_file(absolute),
        "blob_oid": git_blob_oid(project_root.parent, absolute),
        "bytes": absolute.stat().st_size,
    }


def read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise BundleBuildError(f"{label}_missing:{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleBuildError(f"{label}_json_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise BundleBuildError(f"{label}_object_required:{path}")
    return value


def require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise BundleBuildError(
            f"{label}_keys_mismatch:missing={sorted(expected - actual)}:extra={sorted(actual - expected)}"
        )


def contains_scalar(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    if isinstance(value, Mapping):
        return any(contains_scalar(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(contains_scalar(item, expected) for item in value)
    return False


def parse_parent_specs(specs: list[str]) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for spec in specs:
        role, separator, raw_path = spec.partition("=")
        if not separator or role not in REQUIRED_PARENT_ROLES or not raw_path:
            raise BundleBuildError(f"parent_spec_invalid:{spec}")
        if role in parsed:
            raise BundleBuildError(f"parent_role_duplicate:{role}")
        parsed[role] = Path(raw_path).resolve()
    missing = set(REQUIRED_PARENT_ROLES) - set(parsed)
    extra = set(parsed) - set(REQUIRED_PARENT_ROLES)
    if missing or extra or len(specs) != len(REQUIRED_PARENT_ROLES):
        raise BundleBuildError(
            f"parent_role_set_mismatch:missing={sorted(missing)}:extra={sorted(extra)}"
        )
    if len({os.path.normcase(str(path)) for path in parsed.values()}) != len(parsed):
        raise BundleBuildError("parent_paths_must_be_distinct")
    return parsed


def build_parent_checkpoints(
    parent_paths: Mapping[str, Path],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    payloads: dict[str, dict[str, Any]] = {}
    for role in REQUIRED_PARENT_ROLES:
        path = parent_paths[role]
        payloads[role] = read_json_object(path, label=f"parent_{role}")
        entries.append(
            {
                "role": role,
                "path": str(path),
                "sha256": sha256_file(path),
                "kind": PARENT_KINDS[role],
                "immutable": True,
                "must_not_execute": True,
            }
        )
    return entries, payloads


def source_schema_versions(project_root: Path) -> list[str]:
    path = project_root / "src" / "evm" / "control_panel" / "transactional_store.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise BundleBuildError("schema_versions_source_parse_failed") from exc
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and (
            isinstance(getattr(node, "target", None), ast.Name)
            and getattr(node, "target").id == "SCHEMA_VERSIONS"
            or isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "SCHEMA_VERSIONS"
                for target in node.targets
            )
        )
    ]
    if len(matches) != 1:
        raise BundleBuildError(f"schema_versions_assignment_count:{len(matches)}")
    node = matches[0]
    literal = ast.literal_eval(node.value)  # type: ignore[arg-type]
    if (
        not isinstance(literal, tuple)
        or not literal
        or not all(isinstance(item, str) for item in literal)
    ):
        raise BundleBuildError("schema_versions_literal_tuple_required")
    versions = list(literal)
    if len(versions) != len(set(versions)) or versions != sorted(versions):
        raise BundleBuildError("schema_versions_must_be_unique_sorted")
    return versions


def verify_source_identity(
    project_root: Path,
    branch: str,
    expected_untracked: int,
    expected_untracked_digest: str,
) -> dict[str, Any]:
    repository = project_root.parent
    revision = git(repository, "rev-parse", "HEAD").lower()
    tree = git(repository, "rev-parse", "HEAD^{tree}").lower()
    actual_branch = git(repository, "branch", "--show-current")
    origin = git(repository, "rev-parse", f"origin/{branch}").lower()
    remote_line = git(repository, "ls-remote", "origin", f"refs/heads/{branch}")
    remote_parts = remote_line.split()
    remote = remote_parts[0].lower() if len(remote_parts) == 2 else ""
    tracked = git(repository, "status", "--porcelain=v1", "--untracked-files=no")
    untracked, untracked_digest = untracked_identity(repository)
    if actual_branch != branch:
        raise BundleBuildError(f"branch_mismatch:{actual_branch}")
    if not revision or revision != origin or revision != remote:
        raise BundleBuildError(f"local_origin_remote_mismatch:{revision}:{origin}:{remote}")
    if tracked:
        raise BundleBuildError("tracked_changes_present")
    if untracked != expected_untracked:
        raise BundleBuildError(f"untracked_count_mismatch:{untracked}")
    expected_digest = expected_untracked_digest.lower()
    if not HEX64.fullmatch(expected_digest):
        raise BundleBuildError("expected_untracked_digest_invalid")
    if untracked_digest != expected_digest:
        raise BundleBuildError(f"untracked_digest_mismatch:{untracked_digest}")
    return {
        "revision": revision,
        "tree": tree,
        "branch": actual_branch,
        "origin_revision": origin,
        "remote_revision": remote,
        "tracked": 0,
        "untracked": untracked,
        "untracked_path_encoding": "utf-8",
        "untracked_sort": "ordinal",
        "untracked_separator": "nul",
        "untracked_path_digest_sha256": untracked_digest,
    }


def _normal_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise BundleBuildError(f"{label}_path_required")
    return Path(value).resolve()


HISTORICAL_IDENTITY_KEYS = {
    "control_plane_task_entity_statuses": {"entity_id", "created_at", "updated_at"},
    "mlflow_running_rows": {"run_id", "lifecycle_stage", "start_time", "end_time"},
    "kubernetes_terminal_failed_objects": {
        "uid",
        "namespace",
        "name",
        "owner_uid",
        "reason",
    },
}


def _validate_historical_attestation(
    *,
    path: Path,
    sha256: str,
    source: str,
    expected_counts: Mapping[str, int],
    expected_classification: str,
    expected_kubernetes_uids: set[str],
    proof_paths: set[Path],
) -> None:
    if sha256_file(path) != sha256:
        raise BundleBuildError(f"historical_attestation_sha_mismatch:{source}")
    payload = read_json_object(path, label=f"historical_attestation_{source}")
    require_exact_keys(
        payload,
        {"source", "captured_at", "query_sha256", "counts", "classification", "records"},
        f"historical_attestation_{source}",
    )
    if payload["source"] != source:
        raise BundleBuildError(f"historical_attestation_source_mismatch:{source}")
    captured_at = payload["captured_at"]
    if not isinstance(captured_at, str) or not captured_at.strip() or not captured_at.endswith("Z"):
        raise BundleBuildError(f"historical_attestation_captured_at_required:{source}")
    try:
        datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BundleBuildError(f"historical_attestation_captured_at_invalid:{source}") from exc
    if payload["query_sha256"] != HISTORICAL_QUERY_SHA256[source]:
        raise BundleBuildError(f"historical_attestation_query_sha_invalid:{source}")
    counts = payload["counts"]
    if not isinstance(counts, dict):
        raise BundleBuildError(f"historical_attestation_counts_object_required:{source}")
    required_counts = {
        "observed_count",
        "executing_count",
        "historical_count",
        "unproven_count",
    }
    require_exact_keys(counts, required_counts, f"historical_attestation_counts_{source}")
    if counts != dict(expected_counts):
        raise BundleBuildError(f"historical_attestation_counts_mismatch:{source}")
    if payload["classification"] != expected_classification:
        raise BundleBuildError(f"historical_attestation_classification_mismatch:{source}")
    records = payload["records"]
    if not isinstance(records, list) or len(records) != counts["observed_count"]:
        raise BundleBuildError(f"historical_attestation_record_count_mismatch:{source}")
    classified_counts = {"executing": 0, "historical_nonexecuting": 0, "unproven": 0}
    identities: set[str] = set()
    kubernetes_uids: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise BundleBuildError(
                f"historical_attestation_record_object_required:{source}:{index}"
            )
        require_exact_keys(
            record,
            {"identity", "observed_state", "classification", "execution_proof"},
            f"historical_attestation_record_{source}_{index}",
        )
        identity = record["identity"]
        if not isinstance(identity, dict):
            raise BundleBuildError(
                f"historical_attestation_identity_object_required:{source}:{index}"
            )
        require_exact_keys(
            identity, HISTORICAL_IDENTITY_KEYS[source], f"historical_identity_{source}_{index}"
        )
        if not all(isinstance(item, str) and item.strip() for item in identity.values()):
            raise BundleBuildError(
                f"historical_attestation_identity_value_required:{source}:{index}"
            )
        if source == "kubernetes_terminal_failed_objects":
            if not UUID.fullmatch(identity["uid"]) or not UUID.fullmatch(identity["owner_uid"]):
                raise BundleBuildError(f"historical_attestation_identity_uid_invalid:{index}")
            kubernetes_uids.add(identity["uid"])
        identity_key = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        if identity_key in identities:
            raise BundleBuildError(f"historical_attestation_identity_duplicate:{source}:{index}")
        identities.add(identity_key)
        if not isinstance(record["observed_state"], str) or not record["observed_state"].strip():
            raise BundleBuildError(
                f"historical_attestation_observed_state_required:{source}:{index}"
            )
        classification = record["classification"]
        if classification not in classified_counts:
            raise BundleBuildError(
                f"historical_attestation_record_classification_invalid:{source}:{index}"
            )
        classified_counts[classification] += 1
        proof = record["execution_proof"]
        if not isinstance(proof, dict):
            raise BundleBuildError(
                f"historical_attestation_execution_proof_object_required:{source}:{index}"
            )
        require_exact_keys(
            proof,
            {
                "inactivity_proven",
                "active_job_count",
                "active_claim_count",
                "active_lease_count",
                "outcome_unknown_count",
                "evidence",
            },
            f"historical_execution_proof_{source}_{index}",
        )
        for count_name in (
            "active_job_count",
            "active_claim_count",
            "active_lease_count",
            "outcome_unknown_count",
        ):
            count = proof[count_name]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise BundleBuildError(f"historical_execution_proof_count_invalid:{source}:{index}")
        if not isinstance(proof["inactivity_proven"], bool):
            raise BundleBuildError(f"historical_execution_proof_boolean_required:{source}:{index}")
        evidence = proof["evidence"]
        if not isinstance(evidence, dict):
            raise BundleBuildError(
                f"historical_execution_proof_evidence_object_required:{source}:{index}"
            )
        require_exact_keys(evidence, {"path", "sha256"}, "historical_execution_proof_evidence")
        evidence_path = _normal_path(evidence["path"], "historical_execution_proof_evidence")
        evidence_sha = str(evidence["sha256"]).lower()
        if evidence_path == path or evidence_path in proof_paths:
            raise BundleBuildError("historical_attestation_proof_paths_must_be_distinct")
        proof_paths.add(evidence_path)
        if not HEX64.fullmatch(evidence_sha) or sha256_file(evidence_path) != evidence_sha:
            raise BundleBuildError(
                f"historical_execution_proof_evidence_sha_mismatch:{source}:{index}"
            )
        proof_payload = read_json_object(
            evidence_path, label=f"historical_execution_proof_payload_{source}_{index}"
        )
        require_exact_keys(
            proof_payload,
            {
                "source",
                "identity",
                "observed_state",
                "captured_at",
                "query_sha256",
                "active_job_count",
                "active_claim_count",
                "active_lease_count",
                "outcome_unknown_count",
                "inactivity_decision",
                "decision_authority",
            },
            f"historical_execution_proof_payload_{source}_{index}",
        )
        if proof_payload["source"] != source or proof_payload["identity"] != identity:
            raise BundleBuildError(f"historical_execution_proof_identity_mismatch:{source}:{index}")
        if proof_payload["observed_state"] != record["observed_state"]:
            raise BundleBuildError(f"historical_execution_proof_state_mismatch:{source}:{index}")
        proof_captured_at = proof_payload["captured_at"]
        if not isinstance(proof_captured_at, str) or not proof_captured_at.endswith("Z"):
            raise BundleBuildError(f"historical_execution_proof_timestamp_invalid:{source}:{index}")
        try:
            datetime.fromisoformat(proof_captured_at[:-1] + "+00:00")
        except ValueError as exc:
            raise BundleBuildError(
                f"historical_execution_proof_timestamp_invalid:{source}:{index}"
            ) from exc
        if proof_payload["query_sha256"] != payload["query_sha256"]:
            raise BundleBuildError(f"historical_execution_proof_query_mismatch:{source}:{index}")
        for count_name in (
            "active_job_count",
            "active_claim_count",
            "active_lease_count",
            "outcome_unknown_count",
        ):
            if proof_payload[count_name] != proof[count_name]:
                raise BundleBuildError(
                    f"historical_execution_proof_count_mismatch:{source}:{index}"
                )
        expected_decision = (
            "proven_inactive"
            if proof["inactivity_proven"] is True
            else "executing"
            if sum(
                proof[name]
                for name in (
                    "active_job_count",
                    "active_claim_count",
                    "active_lease_count",
                    "outcome_unknown_count",
                )
            )
            else "unproven"
        )
        if proof_payload["inactivity_decision"] != expected_decision:
            raise BundleBuildError(f"historical_execution_proof_decision_mismatch:{source}:{index}")
        if proof_payload["decision_authority"] != HISTORICAL_DECISION_AUTHORITY:
            raise BundleBuildError(
                f"historical_execution_proof_authority_mismatch:{source}:{index}"
            )
        active_total = sum(
            proof[name]
            for name in (
                "active_job_count",
                "active_claim_count",
                "active_lease_count",
                "outcome_unknown_count",
            )
        )
        if classification == "historical_nonexecuting" and (
            proof["inactivity_proven"] is not True or active_total != 0
        ):
            raise BundleBuildError(f"historical_execution_proof_insufficient:{source}:{index}")
        if classification == "unproven" and proof["inactivity_proven"] is True:
            raise BundleBuildError(f"historical_unproven_record_claims_proof:{source}:{index}")
    if (
        classified_counts["executing"] != counts["executing_count"]
        or classified_counts["historical_nonexecuting"] != counts["historical_count"]
        or classified_counts["unproven"] != counts["unproven_count"]
    ):
        raise BundleBuildError(
            f"historical_attestation_record_classification_count_mismatch:{source}"
        )
    if (
        source == "kubernetes_terminal_failed_objects"
        and kubernetes_uids != expected_kubernetes_uids
    ):
        raise BundleBuildError("historical_attestation_kubernetes_identity_set_mismatch")


def _validate_job_scope(value: Any, *, expected_kubernetes_uids: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BundleBuildError("job_scope_contract_object_required")
    require_exact_keys(
        value,
        {
            "canonical_active_jobs",
            "historical_observations",
            "historical_classifications",
        },
        "job_scope_contract",
    )
    expected_canonical = {
        "sources": ["kubernetes_job_status_active", "manifest_active_job_file_markers"],
        "required_count": 0,
    }
    expected_historical = {
        "sources": [
            "control_plane_task_entity_statuses",
            "mlflow_running_rows",
            "kubernetes_terminal_failed_objects",
        ],
        "separate_from_canonical_active_jobs": True,
        "unknown_or_unproven_blocks_restore": True,
        "deletion_required": False,
    }
    if value["canonical_active_jobs"] != expected_canonical:
        raise BundleBuildError("job_scope_canonical_active_jobs_mismatch")
    if value["historical_observations"] != expected_historical:
        raise BundleBuildError("job_scope_historical_observations_mismatch")
    classifications = value["historical_classifications"]
    expected_sources = expected_historical["sources"]
    if not isinstance(classifications, list) or len(classifications) != len(expected_sources):
        raise BundleBuildError("historical_classification_count_mismatch")
    attestation_paths: set[Path] = set()
    proof_paths: set[Path] = set()
    for expected_source, item in zip(expected_sources, classifications, strict=True):
        if not isinstance(item, dict):
            raise BundleBuildError("historical_classification_object_required")
        require_exact_keys(
            item,
            {
                "source",
                "observed_count",
                "executing_count",
                "historical_count",
                "unproven_count",
                "classification",
                "attestation",
            },
            f"historical_classification_{expected_source}",
        )
        if item["source"] != expected_source:
            raise BundleBuildError("historical_classification_source_mismatch")
        counts = []
        for key in ("observed_count", "executing_count", "historical_count", "unproven_count"):
            count = item[key]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise BundleBuildError(
                    f"historical_classification_count_invalid:{expected_source}:{key}"
                )
            counts.append(count)
        observed, executing, historical, unproven = counts
        if observed != executing + historical + unproven:
            raise BundleBuildError(
                f"historical_classification_count_sum_mismatch:{expected_source}"
            )
        expected_label = (
            "unproven" if unproven else "executing" if executing else "historical_nonexecuting"
        )
        if item["classification"] != expected_label:
            raise BundleBuildError(f"historical_classification_label_mismatch:{expected_source}")
        if expected_source == "kubernetes_terminal_failed_objects" and observed != 11:
            raise BundleBuildError("historical_failed_pod_classification_count_mismatch")
        attestation = item["attestation"]
        if not isinstance(attestation, dict):
            raise BundleBuildError("historical_classification_attestation_object_required")
        require_exact_keys(attestation, {"path", "sha256"}, "historical_classification_attestation")
        attestation_path = _normal_path(
            attestation["path"], "historical_classification_attestation"
        )
        if attestation_path in attestation_paths:
            raise BundleBuildError("historical_attestation_paths_must_be_distinct")
        attestation_paths.add(attestation_path)
        attestation_sha = str(attestation["sha256"]).lower()
        if not HEX64.fullmatch(attestation_sha) or sha256_file(attestation_path) != attestation_sha:
            raise BundleBuildError(
                f"historical_classification_attestation_sha_mismatch:{expected_source}"
            )
        _validate_historical_attestation(
            path=attestation_path,
            sha256=attestation_sha,
            source=expected_source,
            expected_counts={
                "observed_count": observed,
                "executing_count": executing,
                "historical_count": historical,
                "unproven_count": unproven,
            },
            expected_classification=expected_label,
            expected_kubernetes_uids=expected_kubernetes_uids,
            proof_paths=proof_paths,
        )
    if attestation_paths & proof_paths:
        raise BundleBuildError("historical_attestation_and_proof_paths_must_be_distinct")
    return value


def validate_runtime_state_pins(
    path: Path,
    *,
    project_root: Path,
    source_identity: Mapping[str, Any],
    parent_entries: list[dict[str, Any]],
    parent_payloads: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = read_json_object(path, label="runtime_state_pins")
    require_exact_keys(
        value,
        {
            "schema_version",
            "source_evidence",
            "compose",
            "api",
            "database",
            "kubernetes",
            "job_scope_contract",
        },
        "runtime_state_pins",
    )
    if value["schema_version"] != RUNTIME_STATE_SCHEMA:
        raise BundleBuildError("runtime_state_schema_mismatch")
    parents = {entry["role"]: entry for entry in parent_entries}
    source_evidence = value["source_evidence"]
    if not isinstance(source_evidence, dict):
        raise BundleBuildError("runtime_state_source_evidence_object_required")
    require_exact_keys(
        source_evidence, {"post_manual_on_readback", "post_manual_on_index"}, "source_evidence"
    )
    for role in ("post_manual_on_readback", "post_manual_on_index"):
        pin = source_evidence[role]
        if not isinstance(pin, dict):
            raise BundleBuildError(f"source_evidence_{role}_object_required")
        require_exact_keys(pin, {"path", "sha256"}, f"source_evidence_{role}")
        if _normal_path(pin["path"], role) != Path(parents[role]["path"]):
            raise BundleBuildError(f"source_evidence_{role}_path_mismatch")
        if str(pin["sha256"]).lower() != parents[role]["sha256"]:
            raise BundleBuildError(f"source_evidence_{role}_sha_mismatch")

    compose = value["compose"]
    if not isinstance(compose, dict):
        raise BundleBuildError("runtime_state_compose_object_required")
    require_exact_keys(
        compose,
        {
            "project_name",
            "config_path",
            "config_sha256",
            "long_lived_services",
            "one_shot_services",
            "service_pins",
            "stability",
        },
        "runtime_state_compose",
    )
    config_path = _normal_path(compose["config_path"], "compose_config")
    expected_config = (project_root / "docker-compose.yml").resolve()
    if config_path != expected_config or not config_path.is_file():
        raise BundleBuildError("compose_config_path_mismatch")
    if compose["project_name"] != "enterprise-vision-mlops":
        raise BundleBuildError("compose_project_name_mismatch")
    if str(compose["config_sha256"]).lower() != sha256_file(config_path):
        raise BundleBuildError("compose_config_sha_mismatch")
    if compose["long_lived_services"] != list(LONG_LIVED_SERVICES):
        raise BundleBuildError("compose_long_lived_services_mismatch")
    if compose["one_shot_services"] != list(ONE_SHOT_SERVICES):
        raise BundleBuildError("compose_one_shot_services_mismatch")
    service_pins = compose["service_pins"]
    if not isinstance(service_pins, dict) or set(service_pins) != set(LONG_LIVED_SERVICES):
        raise BundleBuildError("compose_service_pin_set_mismatch")
    container_ids: set[str] = set()
    for service in LONG_LIVED_SERVICES:
        pin = service_pins[service]
        if not isinstance(pin, dict):
            raise BundleBuildError(f"compose_service_pin_object_required:{service}")
        require_exact_keys(
            pin,
            {"container_name", "container_id", "image_id", "healthcheck_expected"},
            f"compose_service_pin_{service}",
        )
        if pin["container_name"] != CONTAINER_NAMES[service]:
            raise BundleBuildError(f"compose_container_name_mismatch:{service}")
        if not isinstance(pin["container_id"], str) or not HEX64.fullmatch(pin["container_id"]):
            raise BundleBuildError(f"compose_container_id_invalid:{service}")
        if pin["container_id"] in container_ids:
            raise BundleBuildError("compose_container_ids_must_be_distinct")
        container_ids.add(pin["container_id"])
        if not isinstance(pin["image_id"], str) or not IMAGE_ID.fullmatch(pin["image_id"]):
            raise BundleBuildError(f"compose_image_id_invalid:{service}")
        if pin["healthcheck_expected"] is not HEALTHCHECK_EXPECTED[service]:
            raise BundleBuildError(f"compose_healthcheck_contract_mismatch:{service}")
    if compose["stability"] != {
        "duration_seconds": 300,
        "interval_seconds": 5,
        "samples": 61,
        "restart_delta": 0,
    }:
        raise BundleBuildError("compose_stability_contract_mismatch")

    api = value["api"]
    if not isinstance(api, dict):
        raise BundleBuildError("runtime_state_api_object_required")
    require_exact_keys(
        api,
        {
            "base_url",
            "api_container_name",
            "worker_container_name",
            "image_id",
            "image_attestation",
            "source_revision",
            "source_tree",
        },
        "runtime_state_api",
    )
    if api["base_url"] != "http://127.0.0.1:8000":
        raise BundleBuildError("api_base_url_mismatch")
    if (
        api["api_container_name"] != "evm-api"
        or api["worker_container_name"] != "evm-task-queue-worker"
    ):
        raise BundleBuildError("api_container_name_mismatch")
    if not isinstance(api["image_id"], str) or not IMAGE_ID.fullmatch(api["image_id"]):
        raise BundleBuildError("api_image_id_invalid")
    if (
        api["source_revision"] != source_identity["revision"]
        or api["source_tree"] != source_identity["tree"]
    ):
        raise BundleBuildError("api_source_identity_mismatch")
    image_attestation = api["image_attestation"]
    if not isinstance(image_attestation, dict):
        raise BundleBuildError("api_image_attestation_object_required")
    require_exact_keys(image_attestation, {"path", "sha256"}, "api_image_attestation")
    attestation_path = _normal_path(image_attestation["path"], "api_image_attestation")
    attestation_sha = str(image_attestation["sha256"]).lower()
    if not HEX64.fullmatch(attestation_sha) or sha256_file(attestation_path) != attestation_sha:
        raise BundleBuildError("api_image_attestation_sha_mismatch")
    attestation = read_json_object(attestation_path, label="api_image_attestation")
    for scalar in (api["image_id"], source_identity["revision"], source_identity["tree"]):
        if not contains_scalar(attestation, str(scalar)):
            raise BundleBuildError(f"api_image_attestation_identity_missing:{scalar}")

    database = value["database"]
    if not isinstance(database, dict):
        raise BundleBuildError("runtime_state_database_object_required")
    require_exact_keys(
        database,
        {
            "control_plane_schema_versions",
            "airflow_migration_head",
            "mlflow_migration_head",
            "instances",
        },
        "runtime_state_database",
    )
    canonical_versions = source_schema_versions(project_root)
    if database["control_plane_schema_versions"] != canonical_versions:
        raise BundleBuildError("control_plane_schema_versions_source_mismatch")
    if database["airflow_migration_head"] != AIRFLOW_MIGRATION_HEAD:
        raise BundleBuildError("airflow_migration_head_mismatch")
    if database["mlflow_migration_head"] != MLFLOW_MIGRATION_HEAD:
        raise BundleBuildError("mlflow_migration_head_mismatch")
    expected_instances = {
        "control_plane": {
            "container_name": "evm-control-plane-postgres",
            "user": "evm_control_plane",
            "database": "evm_control_plane",
        },
        "mlflow": {"container_name": "evm-postgres", "user": "mlflow", "database": "mlflow"},
        "airflow": {
            "container_name": "evm-airflow-postgres",
            "user": "airflow",
            "database": "airflow",
        },
    }
    if database["instances"] != expected_instances:
        raise BundleBuildError("database_instances_mismatch")

    kubernetes = value["kubernetes"]
    if not isinstance(kubernetes, dict):
        raise BundleBuildError("runtime_state_kubernetes_object_required")
    require_exact_keys(
        kubernetes,
        {"allowed_historical_failed_pods", "health_confirmation_samples", "residual_selectors"},
        "runtime_state_kubernetes",
    )
    if kubernetes["health_confirmation_samples"] != 2:
        raise BundleBuildError("kubernetes_health_confirmation_samples_mismatch")
    if kubernetes["residual_selectors"] != ["evm.openai.local/scenario=s8-v4-x1"]:
        raise BundleBuildError("kubernetes_residual_selectors_mismatch")
    allowlist = kubernetes["allowed_historical_failed_pods"]
    if not isinstance(allowlist, list) or len(allowlist) != 11:
        raise BundleBuildError("kubernetes_failed_pod_allowlist_required")
    identities: list[tuple[str, str, str]] = []
    for index, item in enumerate(allowlist):
        if not isinstance(item, dict):
            raise BundleBuildError(f"kubernetes_allowlist_object_required:{index}")
        require_exact_keys(
            item,
            {"uid", "name", "namespace", "reason", "owner_uid"},
            f"kubernetes_allowlist_{index}",
        )
        if not UUID.fullmatch(str(item["uid"])) or not UUID.fullmatch(str(item["owner_uid"])):
            raise BundleBuildError(f"kubernetes_allowlist_uid_invalid:{index}")
        if not all(
            isinstance(item[key], str) and item[key].strip()
            for key in ("name", "namespace", "reason")
        ):
            raise BundleBuildError(f"kubernetes_allowlist_text_invalid:{index}")
        if (
            item["namespace"] != "evm-production"
            or not item["name"].startswith("evm-b0-production-")
            or item["reason"] != "UnexpectedAdmissionError"
        ):
            raise BundleBuildError(f"kubernetes_allowlist_identity_mismatch:{index}")
        identities.append((item["namespace"], item["name"], item["uid"]))
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise BundleBuildError("kubernetes_allowlist_must_be_unique_sorted")

    job_scope = _validate_job_scope(
        value["job_scope_contract"],
        expected_kubernetes_uids={str(item["uid"]) for item in allowlist},
    )
    expected_runtime_state = {
        "compose": compose,
        "api": api,
        "database": database,
        "kubernetes": kubernetes,
        "job_scope_contract": job_scope,
    }
    readback = parent_payloads["post_manual_on_readback"]
    if readback.get("runtime_state") != expected_runtime_state:
        raise BundleBuildError("post_manual_on_readback_runtime_state_mismatch")
    readback_parent = parents["post_manual_on_readback"]
    index = parent_payloads["post_manual_on_index"]
    if not contains_scalar(index, readback_parent["path"]) or not contains_scalar(
        index, readback_parent["sha256"]
    ):
        raise BundleBuildError("post_manual_on_index_readback_link_missing")
    return expected_runtime_state, value


def build_manifest(
    *,
    run_id: str,
    source_identity: Mapping[str, Any],
    project_root: Path,
    staging_directory: Path,
    output_directory: Path,
    python_path: Path,
    runtime: Mapping[str, Any],
    parent_checkpoints: list[dict[str, Any]],
    expected_state: Mapping[str, Any],
) -> dict[str, Any]:
    resolved_project_root = project_root.resolve()
    manifest = {
        "schema_version": "evm.s8_v4.x1_phase_b2_r7_restore_work_order.v1",
        "work_order_id": "s8-v4-x1-phase-b2-r7-restore-only-validation",
        "bundle_id": run_id,
        "execution_mode": RESTORE_MODE,
        "created_at": utc_now(),
        "canonical_revision": source_identity["revision"],
        "canonical_tree": source_identity["tree"],
        "bundle": {
            "path": str(staging_directory.resolve()),
        },
        "repository": {
            "preserved_untracked_count": source_identity["untracked"],
            "untracked_path_set_sha256": source_identity["untracked_path_digest_sha256"],
            "untracked_path_set_encoding": ("ordinal-sorted UTF-8 paths, each NUL-terminated"),
            "tracked_changes": 0,
        },
        "parent_checkpoints": parent_checkpoints,
        "output": {
            "path": str(output_directory.resolve()),
            "must_not_exist_before_runner": True,
            "write_mode": "create-exclusive",
        },
        "timeout_contract": {
            "kubectl_timeout_seconds": 8.0,
            "wrapper_timeout_seconds": 15.0,
            "restore_deadline_seconds": 600.0,
            "residual_repoll_seconds": 120.0,
            "stream_drain_seconds": 5.0,
        },
        "lifecycle_timeout_contract": {
            "compose_internal_seconds": 120.0,
            "compose_wrapper_seconds": 150.0,
            "desktop_internal_seconds": 300.0,
            "desktop_wrapper_seconds": 330.0,
            "sampler_internal_seconds": 180.0,
            "sampler_wrapper_seconds": 210.0,
            "attempt_deadline_seconds": 1200.0,
        },
        "process_containment": {
            "provider": "windows_job_object",
            "create_suspended": True,
            "assign_before_resume": True,
            "breakaway_allowed": False,
            "kill_on_job_close": False,
            "terminate_job_object_allowed": False,
            "job_accounting_authoritative": True,
            "stdio_drain_before_followup": True,
            "residual_repoll_seconds": 120,
            "force_termination_attempts": 0,
            "wsl_run_uuid_and_process_group": True,
            "wsl_proc_residual_check": True,
        },
        "probe_max_attempts": 1,
        "call_contract": {
            "restore-only": {
                "docker_off_probe": 0,
                "compose_stop": 0,
                "desktop_stop": 0,
                "wsl_shutdown": 0,
                "desktop_start": 0,
                "compose_start": 0,
            },
            "launcher": {"outer": 1, "bridge": 1, "runner": 1, "automatic_retry": 0},
            "collectors": {
                "windows_fresh_collector": 0,
                "wsl_fresh_collector": 0,
            },
            "downstream": {
                "full_stack_3180": 0,
                "q0": 0,
                "calibration_54": 0,
                "matrix_78": 0,
                "integrated_v4": 0,
                "etw": 0,
            },
        },
        "expected_state": {
            "compose": dict(expected_state["compose"]),
            "api": dict(expected_state["api"]),
            "database": dict(expected_state["database"]),
            "kubernetes": dict(expected_state["kubernetes"]),
            "compose_services": list(LONG_LIVED_SERVICES),
            "api_base_url": expected_state["api"]["base_url"],
            "b0": {
                "uid": EXPECTED_B0_UID,
                "uid_basis": (
                    "tracked canonical status evidence predating r4 and immutable deployment identity"
                ),
                "image": EXPECTED_B0_IMAGE,
                "ready_url": "http://127.0.0.1:30800/ready",
                "predict_url": "http://127.0.0.1:30800/predict",
                "sample_image_uri": (
                    "/mnt/evm-data/data/raw/industrial/visa/candle/Data/Images/Anomaly/000.JPG"
                ),
            },
            "prometheus_jobs": [
                "evm-api",
                "evm-b0-production",
                "evm-otel-collector",
                "evm-task-queue-worker",
                "prometheus",
            ],
            "prometheus_targets_url": "http://127.0.0.1:9090/api/v1/targets",
            "gpu_lease_path": (
                "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/runtime/gpu-lease/active.json"
            ),
            "active_job_roots": [],
            "active_claim_roots": [],
            "x1_residue_paths": [
                "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/"
                "prometheus-targets/s8-v4-x1-triton.json",
                "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/"
                "prometheus-targets/s8-v4-x1-api.json",
            ],
            "x1_docker_name_filter": "name=evm-x1",
            "x1_ports": [31120, 31121, 31122],
            "x1_kubernetes_selectors": list(expected_state["kubernetes"]["residual_selectors"]),
        },
        "job_scope_contract": dict(expected_state["job_scope_contract"]),
        "etw_contract": {
            "decision": (
                "existing_pinned_etw_evidence_is_admissible;"
                "fresh_capture_not_a_phase_b2_go_invariant"
            ),
            "amendment_path": str(ETW_AMENDMENT),
            "amendment_sha256": ETW_AMENDMENT_SHA256,
            "fresh_capture_required_for_phase_b2_go": False,
            "fresh_invocations": 0,
        },
        "evidence": {
            "write_mode": "create-exclusive",
            "failure_creates_completion_marker": False,
            "failure_index_is_not_success_index": True,
            "restore_only_creates_completion_marker": False,
            "success_requires_all_invariants": True,
        },
        "runtime": dict(runtime),
    }
    expected_core_path = (resolved_project_root / RUNTIME_PATHS["core"]).resolve()
    runtime_core = runtime.get("core")
    if not isinstance(runtime_core, Mapping):
        raise BundleBuildError("runtime_core_pin_required")
    runtime_core_path = Path(str(runtime_core.get("path", ""))).resolve()
    if runtime_core_path != expected_core_path:
        raise BundleBuildError("runtime_core_path_mismatch")
    runtime_core_sha = str(runtime_core.get("sha256", "")).lower()
    if not HEX64.fullmatch(runtime_core_sha) or sha256_file(expected_core_path) != runtime_core_sha:
        raise BundleBuildError("runtime_core_sha_mismatch")
    if not python_path.is_file():
        raise BundleBuildError(f"python_missing_for_core_validation:{python_path}")
    core_probe = (
        "import inspect,json,pathlib,sys\n"
        "root=pathlib.Path(sys.argv[1]).resolve()\n"
        "expected_core=pathlib.Path(sys.argv[2]).resolve()\n"
        "sys.path.insert(0,str(root/'src'))\n"
        "import evm.scale_validation.phase_b2_r7 as core\n"
        "actual_core=pathlib.Path(core.__file__).resolve()\n"
        "assert actual_core==expected_core,(actual_core,expected_core)\n"
        "contract=json.loads(sys.argv[5])\n"
        "assert core.HISTORICAL_QUERY_SHA256==contract['query_sha256']\n"
        "assert core.HISTORICAL_DECISION_AUTHORITY==contract['decision_authority']\n"
        "manifest=json.load(sys.stdin)\n"
        "kwargs={'expected_revision':sys.argv[3],"
        "'expected_untracked_path_set_sha256':sys.argv[4]}\n"
        "if 'verify_attestations' in inspect.signature(core.validate_r7_manifest).parameters:"
        " kwargs['verify_attestations']=True\n"
        "core.validate_r7_manifest(manifest,**kwargs)\n"
    )
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            str(python_path.resolve()),
            "-c",
            core_probe,
            str(resolved_project_root),
            str(expected_core_path),
            str(source_identity["revision"]),
            str(source_identity["untracked_path_digest_sha256"]),
            json.dumps(
                {
                    "query_sha256": HISTORICAL_QUERY_SHA256,
                    "decision_authority": HISTORICAL_DECISION_AUTHORITY,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        ],
        input=json.dumps(manifest, allow_nan=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=resolved_project_root,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        raise BundleBuildError(
            "core_manifest_validation_failed:"
            + (result.stderr.strip() or result.stdout.strip() or str(result.returncode))
        )
    return manifest


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render_outer(*, bridge_sha256: str, run_id: str) -> str:
    return f"""[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ExpectedOuterSha256,
  [Parameter(Mandatory = $true)][string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ExpectedBridgeSha256 = '{bridge_sha256}'
$PinnedRunId = {_ps_literal(run_id)}
$outerPath = $PSCommandPath
$outerExpected = $ExpectedOuterSha256.ToLowerInvariant()
$outerObserved = (Get-FileHash -LiteralPath $outerPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
if ($outerObserved -ne $outerExpected) {{ throw 'outer_sha256_mismatch' }}
$bridgePath = Join-Path $PSScriptRoot 'invoke-x1-phase-b2-r7-bridge.ps1'
if (-not (Test-Path -LiteralPath $bridgePath -PathType Leaf)) {{ throw 'bridge_missing' }}
$bridgeObserved = (Get-FileHash -LiteralPath $bridgePath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
if ($bridgeObserved -ne $ExpectedBridgeSha256) {{ throw 'bridge_sha256_mismatch' }}
if (Test-Path -LiteralPath $OutputDirectory) {{ throw 'output_directory_exists' }}
$reservation = Join-Path $PSScriptRoot 'r7-outer-invocation-reservation.json'
$reservationValue = [ordered]@{{ schema='s8-v4-x1-phase-b2-r7-outer-reservation/v1'; created_at=[DateTime]::UtcNow.ToString('o'); pid=$PID; run_id=$PinnedRunId; mode='restore-only'; output_directory=$OutputDirectory }}
$bytes = [Text.UTF8Encoding]::new($false).GetBytes(($reservationValue | ConvertTo-Json -Depth 8 -Compress) + [Environment]::NewLine)
$stream = [IO.File]::Open($reservation,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
try {{ $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) }} finally {{ $stream.Dispose() }}

# Re-read both executable leaves after the reservation write.  These are the
# values handed to the bridge and are the final operations before invocation.
$outerObserved = (Get-FileHash -LiteralPath $outerPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
if ($outerObserved -ne $outerExpected) {{ throw 'outer_sha256_mismatch_immediate' }}
$bridgeObserved = (Get-FileHash -LiteralPath $bridgePath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
if ($bridgeObserved -ne $ExpectedBridgeSha256) {{ throw 'bridge_sha256_mismatch_immediate' }}

# R7_BRIDGE_INVOKE_EXACTLY_ONCE
& $bridgePath -ExpectedOuterSha256 $outerExpected -ObservedOuterSha256 $outerObserved -ExpectedBridgeSha256FromOuter $ExpectedBridgeSha256 -ObservedBridgeSha256 $bridgeObserved -OuterLauncherPath $outerPath -OutputDirectory $OutputDirectory
exit $LASTEXITCODE
"""


def render_bridge(
    *,
    manifest_sha256: str,
    manifest: Mapping[str, Any],
    runtime: Mapping[str, Mapping[str, Any]],
    project_root: Path,
    source_identity: Mapping[str, Any],
    python_path: Path,
) -> str:
    revision = str(manifest["canonical_revision"])
    tree = str(manifest["canonical_tree"])
    repository = str(project_root.resolve().parent)
    resolved_project = str(project_root.resolve())
    branch = str(source_identity["branch"])
    untracked = int(manifest["repository"]["preserved_untracked_count"])  # type: ignore[index]
    untracked_digest = str(manifest["repository"]["untracked_path_set_sha256"])  # type: ignore[index]
    run_id = str(manifest["bundle_id"])
    component_variables = {
        "builder": "Builder",
        "core": "Core",
        "process": "Process",
        "runner": "Runner",
        "validator": "Validator",
        "docker_compose": "DockerCompose",
    }
    declarations: list[str] = []
    guards: list[str] = []
    chain_entries: list[str] = []
    for name, variable in component_variables.items():
        declarations.extend(
            (
                f"${variable}Path = {_ps_literal(str(runtime[name]['path']))}",
                f"$Expected{variable}Sha256 = '{runtime[name]['sha256']}'",
            )
        )
        guards.append(
            f"if ((Get-Sha256 ${variable}Path) -ne $Expected{variable}Sha256) "
            f"{{ throw '{name}_sha256_mismatch' }}"
        )
        chain_entries.append(f"{name}=Get-Sha256 ${variable}Path")
    untracked_probe = base64.b64encode(
        (
            "import hashlib,json,subprocess,sys\n"
            "raw=subprocess.run(['git','-C',sys.argv[1],'-c','core.quotepath=false','ls-files','--others','--exclude-standard','-z'],check=True,capture_output=True).stdout\n"
            "parts=raw.split(b'\\0'); parts=parts[:-1] if parts and parts[-1]==b'' else parts\n"
            "paths=sorted(item.decode('utf-8','strict') for item in parts)\n"
            "digest=hashlib.sha256()\n"
            "for item in paths: digest.update(item.encode('utf-8')); digest.update(b'\\0')\n"
            "print(json.dumps({'count':len(paths),'sha256':digest.hexdigest()},sort_keys=True))\n"
        ).encode("utf-8")
    ).decode("ascii")
    parent_roles = ",".join(_ps_literal(role) for role in REQUIRED_PARENT_ROLES)
    return f"""[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ExpectedOuterSha256,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ObservedOuterSha256,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ExpectedBridgeSha256FromOuter,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{{64}}$')][string]$ObservedBridgeSha256,
  [Parameter(Mandatory = $true)][string]$OuterLauncherPath,
  [Parameter(Mandatory = $true)][string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ExpectedManifestSha256 = '{manifest_sha256}'
$PinnedRevision = '{revision}'
$PinnedTree = '{tree}'
$PinnedRunId = {_ps_literal(run_id)}
$RepositoryRoot = {_ps_literal(repository)}
$ProjectRoot = {_ps_literal(resolved_project)}
$ExpectedBranch = {_ps_literal(branch)}
$ExpectedUntrackedCount = {untracked}
$ExpectedUntrackedDigestSha256 = '{untracked_digest}'
$ExpectedParentRoles = @({parent_roles})
$ManifestPath = Join-Path $PSScriptRoot 'phase-b2-r7-work-order.json'
$PythonPath = {_ps_literal(str(python_path.resolve()))}
{chr(10).join(declarations)}
$UntrackedProbeBase64 = '{untracked_probe}'

function Get-Sha256([string]$Path) {{
  (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
}}
function Invoke-GitRead([string[]]$Arguments) {{
  $text = @(& git.exe -c "safe.directory=$RepositoryRoot" -C $RepositoryRoot @Arguments 2>&1)
  if ($LASTEXITCODE -ne 0) {{ throw "git_identity_read_failed:$($Arguments -join ',')" }}
  ($text -join [Environment]::NewLine).Trim()
}}
function Write-CreateNewJson([string]$Path,[object]$Value) {{
  $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Value | ConvertTo-Json -Depth 20 -Compress) + [Environment]::NewLine)
  $stream = [IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
  try {{ $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) }} finally {{ $stream.Dispose() }}
}}

$outerExpected = $ExpectedOuterSha256.ToLowerInvariant()
$outerObserved = $ObservedOuterSha256.ToLowerInvariant()
$bridgeExpected = $ExpectedBridgeSha256FromOuter.ToLowerInvariant()
$bridgeObserved = $ObservedBridgeSha256.ToLowerInvariant()
if ((Get-Sha256 $OuterLauncherPath) -ne $outerExpected -or $outerObserved -ne $outerExpected) {{ throw 'outer_sha256_mismatch_at_bridge' }}
if ((Get-Sha256 $PSCommandPath) -ne $bridgeExpected -or $bridgeObserved -ne $bridgeExpected) {{ throw 'bridge_sha256_mismatch' }}
if ((Get-Sha256 $ManifestPath) -ne $ExpectedManifestSha256) {{ throw 'manifest_sha256_mismatch' }}
{chr(10).join(guards)}
if (Test-Path -LiteralPath $OutputDirectory) {{ throw 'output_directory_exists' }}
$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ([string]$manifest.execution_mode -ne 'restore-only') {{ throw 'manifest_execution_mode_mismatch' }}
if ([string]$manifest.bundle_id -ne $PinnedRunId) {{ throw 'manifest_run_id_mismatch' }}
if ([IO.Path]::GetFullPath([string]$manifest.bundle.path) -ne [IO.Path]::GetFullPath($PSScriptRoot)) {{ throw 'manifest_bundle_path_mismatch' }}
if ([string]$manifest.output.path -ne [IO.Path]::GetFullPath($OutputDirectory)) {{ throw 'manifest_output_path_mismatch' }}

$manifestParents = @($manifest.parent_checkpoints)
if ($manifestParents.Count -ne $ExpectedParentRoles.Count) {{ throw 'parent_checkpoint_count_mismatch' }}
$parentShaChain = [ordered]@{{}}
foreach ($role in $ExpectedParentRoles) {{
  $matches = @($manifestParents | Where-Object {{ [string]$_.role -ceq $role }})
  if ($matches.Count -ne 1) {{ throw "parent_checkpoint_role_mismatch:$role" }}
  $parent = $matches[0]
  $parentPath = [IO.Path]::GetFullPath([string]$parent.path)
  if (-not (Test-Path -LiteralPath $parentPath -PathType Leaf)) {{ throw "parent_checkpoint_missing:$role" }}
  if ($parent.immutable -ne $true -or $parent.must_not_execute -ne $true) {{ throw "parent_checkpoint_mutability_mismatch:$role" }}
  $parentSha = (Get-Sha256 $parentPath)
  if ($parentSha -ne [string]$parent.sha256) {{ throw "parent_checkpoint_sha256_mismatch:$role" }}
  $bundlePrefix = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd([char]92,[char]47) + [IO.Path]::DirectorySeparatorChar
  if ($parentPath.StartsWith($bundlePrefix,[StringComparison]::OrdinalIgnoreCase)) {{ throw "parent_checkpoint_inside_bundle:$role" }}
  $parentShaChain[$role] = $parentSha
}}

$actualBranch = Invoke-GitRead @('branch','--show-current')
$actualRevision = Invoke-GitRead @('rev-parse','HEAD')
$actualTree = Invoke-GitRead @('rev-parse','HEAD^{{tree}}')
$originRevision = Invoke-GitRead @('rev-parse',"origin/$ExpectedBranch")
$remoteText = Invoke-GitRead @('ls-remote','origin',"refs/heads/$ExpectedBranch")
$remoteRevision = @($remoteText -split '\\s+')[0]
$trackedStatus = Invoke-GitRead @('status','--porcelain=v1','--untracked-files=no')
if ($actualBranch -ne $ExpectedBranch) {{ throw 'git_branch_mismatch' }}
if ($actualRevision -ne $PinnedRevision -or $originRevision -ne $PinnedRevision -or $remoteRevision -ne $PinnedRevision) {{ throw 'git_local_origin_remote_mismatch' }}
if ($actualTree -ne $PinnedTree) {{ throw 'git_tree_mismatch' }}
if (-not [string]::IsNullOrWhiteSpace($trackedStatus)) {{ throw 'git_tracked_changes_present' }}
$untrackedProbe = [Text.UTF8Encoding]::new($false).GetString([Convert]::FromBase64String($UntrackedProbeBase64))
$untrackedOutput = @(& $PythonPath -c $untrackedProbe $RepositoryRoot 2>&1)
if ($LASTEXITCODE -ne 0) {{ throw "git_untracked_probe_failed:$($untrackedOutput -join [Environment]::NewLine)" }}
$untrackedIdentity = (($untrackedOutput -join [Environment]::NewLine).Trim() | ConvertFrom-Json -ErrorAction Stop)
$untrackedCount = [int]$untrackedIdentity.count
$untrackedDigest = [string]$untrackedIdentity.sha256
if ($untrackedCount -ne $ExpectedUntrackedCount) {{ throw "git_untracked_count_mismatch:$untrackedCount" }}
if ($untrackedDigest -ne $ExpectedUntrackedDigestSha256) {{ throw "git_untracked_digest_mismatch:$untrackedDigest" }}

if (-not ('R7TokenNative' -as [type])) {{
  Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class R7TokenNative {{
  [DllImport("advapi32.dll", SetLastError=true)]
  private static extern bool GetTokenInformation(IntPtr token, int infoClass, out int value, int length, out int returnedLength);
  public static int ElevationType(IntPtr token) {{
    int value; int returnedLength;
    if (!GetTokenInformation(token, 18, out value, sizeof(int), out returnedLength)) {{
      throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    }}
    return value;
  }}
}}
'@
}}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$administrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$groups = (& whoami.exe /groups | Out-String)
$integrity = if ($groups -match 'S-1-16-16384') {{ 'System' }} elseif ($groups -match 'S-1-16-12288') {{ 'High' }} else {{ 'Other' }}
$elevationValue = [R7TokenNative]::ElevationType($identity.Token)
$elevationType = if ($elevationValue -eq 2) {{ 'Full' }} else {{ "NotFull:$elevationValue" }}
$execution = Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
$codex = $null
$ancestor = $execution
for ($depth=0; $depth -lt 8 -and $null -ne $ancestor; $depth++) {{
  if ([string]$ancestor.Name -ieq 'codex.exe') {{ $codex=$ancestor; break }}
  if ([int]$ancestor.ParentProcessId -le 0) {{ break }}
  $ancestor = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$ancestor.ParentProcessId)" -ErrorAction SilentlyContinue
}}
$tokenEvidence = [ordered]@{{ captured_at=[DateTime]::UtcNow.ToString('o'); administrator=$administrator; integrity=$integrity; token_elevation_type=$elevationType; token_elevation_type_value=$elevationValue; execution_powershell=[ordered]@{{pid=[int]$execution.ProcessId;ppid=[int]$execution.ParentProcessId;session_id=[int]$execution.SessionId;path=[string]$execution.ExecutablePath}}; codex=if($null -eq $codex){{$null}}else{{[ordered]@{{pid=[int]$codex.ProcessId;ppid=[int]$codex.ParentProcessId;session_id=[int]$codex.SessionId;path=[string]$codex.ExecutablePath;command_line=[string]$codex.CommandLine}}}} }}
if (-not ($administrator -and $integrity -in @('High','System') -and $elevationType -eq 'Full')) {{
  [ordered]@{{decision='administrator_token_required';token_evidence=$tokenEvidence}} | ConvertTo-Json -Depth 10 -Compress
  exit 3
}}

if ((Get-Sha256 $ValidatorPath) -ne $ExpectedValidatorSha256) {{ throw 'validator_sha256_mismatch_immediate' }}
$validation = @(& $ValidatorPath -ManifestPath $ManifestPath -OuterPath $OuterLauncherPath -BridgePath $PSCommandPath -ExpectedOuterSha256 $outerExpected -PreExecution 2>&1)
if ($LASTEXITCODE -ne 0) {{ throw "staging_validator_failed:$($validation -join [Environment]::NewLine)" }}
$bridgeReservation = Join-Path $PSScriptRoot 'r7-bridge-invocation-reservation.json'
Write-CreateNewJson $bridgeReservation ([ordered]@{{schema='s8-v4-x1-phase-b2-r7-bridge-reservation/v1';created_at=[DateTime]::UtcNow.ToString('o');pid=$PID;run_id=$PinnedRunId;mode='restore-only';output_directory=$OutputDirectory}})
$launcherEvidence = [ordered]@{{
  schema='s8-v4-x1-phase-b2-r7-launcher-evidence/v1'
  token_evidence=$tokenEvidence
  sha_chain=$null
  git=[ordered]@{{branch=$actualBranch;revision=$actualRevision;origin_revision=$originRevision;remote_revision=$remoteRevision;tree=$actualTree;tracked=0;untracked=$untrackedCount;untracked_path_set_sha256=$untrackedDigest}}
  run_id=$PinnedRunId
  mode='restore-only'
  invocation_counts=[ordered]@{{outer=1;bridge=1;runner=1;automatic_retry=0}}
}}
$shaChain = [ordered]@{{outer=Get-Sha256 $OuterLauncherPath;bridge=Get-Sha256 $PSCommandPath;manifest=Get-Sha256 $ManifestPath;{";".join(chain_entries)}}}
foreach ($role in $ExpectedParentRoles) {{ $shaChain[$role] = $parentShaChain[$role] }}
$launcherEvidence.sha_chain = $shaChain
$launcherBase64 = [Convert]::ToBase64String([Text.UTF8Encoding]::new($false).GetBytes(($launcherEvidence | ConvertTo-Json -Depth 20 -Compress)))

# The executable Python leaves are imported before the runner can perform its
# own manifest validation, so pin them again at the invocation boundary.  The
# outer and bridge are re-read here as the final launcher-chain observation.
if ((Get-Sha256 $OuterLauncherPath) -ne $outerExpected) {{ throw 'outer_sha256_mismatch_immediate_before_runner' }}
if ((Get-Sha256 $PSCommandPath) -ne $bridgeExpected) {{ throw 'bridge_sha256_mismatch_immediate_before_runner' }}
if ((Get-Sha256 $RunnerPath) -ne $ExpectedRunnerSha256) {{ throw 'runner_sha256_mismatch_immediate' }}
if ((Get-Sha256 $CorePath) -ne $ExpectedCoreSha256) {{ throw 'core_sha256_mismatch_immediate' }}
if ((Get-Sha256 $ProcessPath) -ne $ExpectedProcessSha256) {{ throw 'process_sha256_mismatch_immediate' }}
# R7_RUNNER_INVOKE_EXACTLY_ONCE
& $PythonPath $RunnerPath --manifest $ManifestPath --output-directory $OutputDirectory --expected-revision $PinnedRevision --launcher-evidence-base64 $launcherBase64 --repository-root $RepositoryRoot --mode restore-only
exit $LASTEXITCODE
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create one append-only Phase B2 r7 restore-only bundle."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--staging-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--parent", action="append", default=[], metavar="ROLE=PATH")
    parser.add_argument("--runtime-state-pins", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--branch", default="codex/distributed-scale-validation-plan")
    parser.add_argument("--expected-untracked", type=int, required=True)
    parser.add_argument("--expected-untracked-digest", required=True)
    parser.add_argument("--python", type=Path, default=Path(r"F:\evm_w7_torch\python.exe"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    staging_directory = args.staging_directory.resolve()
    output_directory = args.output_directory.resolve()
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{15,160}", args.run_id)
        or "r7" not in args.run_id.lower()
    ):
        raise BundleBuildError("run_id_invalid_or_not_r7")
    if args.staging_directory.exists():
        raise BundleBuildError(f"staging_directory_exists:{args.staging_directory}")
    if args.output_directory.exists():
        raise BundleBuildError(f"output_directory_exists:{args.output_directory}")
    if os.path.normcase(str(staging_directory)) == os.path.normcase(str(output_directory)):
        raise BundleBuildError("staging_output_must_be_distinct")
    if not args.python.is_file():
        raise BundleBuildError(f"python_missing:{args.python}")
    if sha256_file(ETW_AMENDMENT) != ETW_AMENDMENT_SHA256:
        raise BundleBuildError("etw_amendment_sha256_mismatch")
    parent_paths = parse_parent_specs(args.parent)
    for role, parent_path in parent_paths.items():
        for protected in (staging_directory, output_directory):
            prefix = os.path.normcase(str(protected)) + os.sep
            if os.path.normcase(str(parent_path)).startswith(prefix):
                raise BundleBuildError(f"parent_inside_protected_output:{role}")
    parent_checkpoints, parent_payloads = build_parent_checkpoints(parent_paths)
    source_identity = verify_source_identity(
        project_root,
        args.branch,
        args.expected_untracked,
        args.expected_untracked_digest,
    )
    expected_state, _runtime_state_document = validate_runtime_state_pins(
        args.runtime_state_pins.resolve(),
        project_root=project_root,
        source_identity=source_identity,
        parent_entries=parent_checkpoints,
        parent_payloads=parent_payloads,
    )
    runtime = {name: source_pin(project_root, relative) for name, relative in RUNTIME_PATHS.items()}
    manifest = build_manifest(
        run_id=args.run_id,
        source_identity=source_identity,
        project_root=project_root,
        staging_directory=staging_directory,
        output_directory=output_directory,
        python_path=args.python,
        runtime=runtime,
        parent_checkpoints=parent_checkpoints,
        expected_state=expected_state,
    )
    staging_directory.mkdir(parents=True, exist_ok=False)
    manifest_path = staging_directory / "phase-b2-r7-work-order.json"
    write_exclusive(manifest_path, canonical_json_bytes(manifest))
    bridge_path = staging_directory / "invoke-x1-phase-b2-r7-bridge.ps1"
    bridge = render_bridge(
        manifest_sha256=sha256_file(manifest_path),
        manifest=manifest,
        runtime=runtime,
        project_root=project_root,
        source_identity=source_identity,
        python_path=args.python,
    )
    write_exclusive(bridge_path, bridge.encode("utf-8"))
    outer_path = staging_directory / "invoke-verified-x1-phase-b2-r7.ps1"
    outer = render_outer(bridge_sha256=sha256_file(bridge_path), run_id=args.run_id)
    write_exclusive(outer_path, outer.encode("utf-8"))
    result = {
        "schema": "s8-v4-x1-phase-b2-r7-bundle-build/v1",
        "created_at": utc_now(),
        "mode": RESTORE_MODE,
        "run_id": args.run_id,
        "staging_directory": str(staging_directory),
        "source_identity": source_identity,
        "parent_checkpoints": parent_checkpoints,
        "files": {
            "outer": {"path": str(outer_path), "sha256": sha256_file(outer_path)},
            "bridge": {"path": str(bridge_path), "sha256": sha256_file(bridge_path)},
            "manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
        },
        "actual_invocations": {"outer": 0, "bridge": 0, "runner": 0},
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
