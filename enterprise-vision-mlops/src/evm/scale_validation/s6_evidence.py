from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evm.scale_validation.s6_runtime import (
    S6RuntimeConfig,
    analyze_s6_results,
    file_sha256,
    payload_sha256,
    summarize_latencies,
)


REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SOURCE_PATHS = {
    "config": "enterprise-vision-mlops/configs/s6_rolling_handoff.toml",
    "runtime": "enterprise-vision-mlops/src/evm/scale_validation/s6_runtime.py",
    "evidence": "enterprise-vision-mlops/src/evm/scale_validation/s6_evidence.py",
    "runner": "enterprise-vision-mlops/scripts/dev/run_s6_rolling_handoff_experiment.py",
    "validator": "enterprise-vision-mlops/scripts/dev/validate_s6_rolling_handoff_evidence.py",
    "api_main": "enterprise-vision-mlops/apps/api/main.py",
    "api_runtime": "enterprise-vision-mlops/apps/api/control_panel_runtime.py",
    "api_drain": "enterprise-vision-mlops/src/evm/control_panel/api_rollout.py",
    "kubernetes": "enterprise-vision-mlops/infra/kubernetes/scale-validation/s6/api-rolling.yaml",
    "prometheus": "enterprise-vision-mlops/monitoring/prometheus/prometheus.yml",
}
API_PUBLIC_FIELDS = (
    "repetition",
    "logical_requests",
    "attempts",
    "client_success",
    "database_accepted",
    "database_terminal",
    "accepted_loss",
    "client_success_without_acceptance",
    "duplicate_effects",
    "error_rate",
    "retry_amplification",
    "measurement_seconds",
    "service_rps",
    "mean_ms",
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "maximum_ms",
    "trace_identity_matches",
    "trace_expected",
    "trace_observed",
    "trace_complete",
    "drain_event_count",
    "maximum_drain_seconds",
    "rollout_seconds",
    "prometheus_up",
    "cleanup_passed",
)
GPU_PUBLIC_FIELDS = (
    "phase",
    "repetition",
    "status",
    "candidate_gate_passed",
    "approval_consumed_once",
    "approval_reuse_rejected",
    "zero_owner_overlap",
    "target_identity_exact",
    "rollback_exact",
    "source_to_target_interruption_seconds",
    "target_to_source_interruption_seconds",
    "target_p50_ms",
    "target_p95_ms",
    "target_p99_ms",
    "target_inference_count",
    "target_cuda_inference",
    "source_cuda_inference_restored",
    "prometheus_restored",
)


class S6EvidenceValidationError(RuntimeError):
    pass


def source_git_identity(git_root: Path, revision: str) -> dict[str, Any]:
    _git(git_root, "cat-file", "-e", f"{revision}^{{commit}}")
    return {
        name: git_blob_identity(git_root, revision, path)
        for name, path in SOURCE_PATHS.items()
    }


def validate_s6_experiment(
    payload: Mapping[str, Any],
    *,
    config: S6RuntimeConfig,
    private_root: Path,
    git_root: Path | None = None,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != "evm.s6_rolling_handoff_experiment.v1":
        errors.append("schema_version")
    if payload.get("status") != "verified" or payload.get("verdict") != "passed":
        errors.append("verdict")
    source = dict(payload.get("source_identity", {}))
    revision = str(source.get("revision") or "")
    if not REVISION_PATTERN.fullmatch(revision):
        errors.append("source_revision")
    if source.get("config_sha256") != config.sha256:
        errors.append("config_sha256")
    git_identity: dict[str, Any] = {}
    if git_root is not None and REVISION_PATTERN.fullmatch(revision):
        try:
            if (
                subprocess.run(
                    ["git", "merge-base", "--is-ancestor", revision, validation_revision],
                    cwd=git_root,
                    check=False,
                ).returncode
                != 0
            ):
                errors.append("source_revision_not_ancestor")
            git_identity = source_git_identity(git_root, revision)
            if canonical(source.get("git_blobs")) != canonical(git_identity):
                errors.append("git_blob_identity")
            if git_identity.get("config", {}).get("sha256") != config.sha256:
                errors.append("config_git_blob_sha256")
        except (OSError, subprocess.CalledProcessError):
            errors.append("git_identity_unavailable")

    private_validation = validate_private_evidence(private_root, errors)
    api_private = private_validation.get("api", [])
    gpu_calibration_private = private_validation.get("gpu_calibration", {})
    gpu_private = private_validation.get("gpu", [])
    api_projected = [project_api_result(item, errors=errors) for item in api_private]
    calibration_projected = project_gpu_result(
        gpu_calibration_private, errors=errors, prefix="gpu_calibration"
    )
    gpu_projected = [
        project_gpu_result(item, errors=errors, prefix=f"gpu_{index}")
        for index, item in enumerate(gpu_private, start=1)
    ]
    if canonical(payload.get("api_repetitions")) != canonical(api_projected):
        errors.append("api_public_projection")
    if canonical(payload.get("gpu_calibration")) != canonical(calibration_projected):
        errors.append("gpu_calibration_public_projection")
    if canonical(payload.get("gpu_repetitions")) != canonical(gpu_projected):
        errors.append("gpu_public_projection")
    try:
        analysis = analyze_s6_results(
            api_repetitions=api_projected,
            gpu_calibration=calibration_projected,
            gpu_repetitions=gpu_projected,
            config=config,
        )
    except Exception:
        errors.append("analysis_recompute_failed")
        analysis = {}
    if canonical(payload.get("analysis")) != canonical(analysis):
        errors.append("analysis_projection")
    private_summary = dict(payload.get("private_evidence", {}))
    expected_private = private_validation.get("summary", {})
    for key in ("artifact_count", "total_bytes", "aggregate_sha256", "index_sha256"):
        if private_summary.get(key) != expected_private.get(key):
            errors.append(f"private_evidence:{key}")
    if payload.get("claim_boundary") != config.claim_boundary:
        errors.append("claim_boundary")
    if analysis.get("status") != "passed":
        errors.append("acceptance")
    if errors:
        raise S6EvidenceValidationError(
            "s6_experiment_invalid:" + ",".join(sorted(set(errors)))
        )
    return {
        "status": "valid",
        "revision": revision,
        "source_identity": git_identity,
        "analysis": analysis,
        "private_evidence": expected_private,
    }


def project_api_result(
    payload: Mapping[str, Any], *, errors: list[str]
) -> dict[str, Any]:
    prefix = f"api_{payload.get('repetition', 'unknown')}"
    observations = list(payload.get("observations", []))
    successful = [item for item in observations if item.get("success") is True]
    client_ids = {str(item.get("logical_request_id")) for item in successful}
    database = dict(payload.get("database", {}))
    accepted = set(str(value) for value in database.get("accepted_ids", []))
    terminal = set(str(value) for value in database.get("terminal_ids", []))
    effects = [str(value) for value in database.get("effect_ids", [])]
    latencies = [float(item.get("logical_latency_ms", 0)) for item in successful]
    recomputed = {
        "logical_requests": len(observations),
        "attempts": sum(len(item.get("attempts", [])) for item in observations),
        "client_success": len(client_ids),
        "database_accepted": len(accepted),
        "database_terminal": len(terminal),
        "accepted_loss": len(accepted - client_ids),
        "client_success_without_acceptance": len(client_ids - accepted),
        "duplicate_effects": len(effects) - len(set(effects)),
        "trace_identity_matches": sum(
            1 for item in successful if item.get("trace_identity_matches") is True
        ),
    }
    for key, value in recomputed.items():
        if payload.get(key) != value:
            errors.append(f"{prefix}:{key}")
    if len(effects) != len(accepted) or any(not value for value in effects):
        errors.append(f"{prefix}:effect_identity_closure")
    expected_error_rate = (
        (len(observations) - len(successful)) / len(observations)
        if observations
        else 1.0
    )
    expected_retry_amplification = recomputed["attempts"] / max(1, len(observations))
    measurement_seconds = float(payload.get("measurement_seconds", 0))
    expected_service_rps = len(successful) / max(measurement_seconds, 1e-9)
    for key, value in (
        ("error_rate", expected_error_rate),
        ("retry_amplification", expected_retry_amplification),
        ("service_rps", expected_service_rps),
    ):
        if abs(float(payload.get(key, -1)) - value) > 1e-9:
            errors.append(f"{prefix}:{key}")
    if latencies:
        latency = summarize_latencies(latencies)
        for key, value in latency.items():
            if abs(float(payload.get(key, -1)) - value) > 1e-9:
                errors.append(f"{prefix}:{key}")
    trace = dict(payload.get("trace_summary", {}))
    if trace.get("complete") is not True:
        errors.append(f"{prefix}:trace_complete")
    drains = list(database.get("drain_events", []))
    expected_drain_ids = set(database.get("expected_drain_instance_ids", []))
    observed_drain_ids = {
        str(item.get("instance_id") or "") for item in drains
    }
    if expected_drain_ids != observed_drain_ids or any(not value for value in observed_drain_ids):
        errors.append(f"{prefix}:drain_identity_closure")
    if any(item.get("drain_completed") is not True for item in drains):
        errors.append(f"{prefix}:drain_terminal")
    if int(payload.get("drain_event_count", -1)) != len(drains):
        errors.append(f"{prefix}:drain_count")
    if payload.get("before", {}).get("release_ids") != ["old"]:
        errors.append(f"{prefix}:old_identity")
    if payload.get("after", {}).get("release_ids") != ["new"]:
        errors.append(f"{prefix}:new_identity")
    return {
        key: (
            int(trace.get("expected", 0))
            if key == "trace_expected"
            else int(trace.get("observed", 0))
            if key == "trace_observed"
            else bool(trace.get("complete"))
            if key == "trace_complete"
            else payload[key]
        )
        for key in API_PUBLIC_FIELDS
    }


def project_gpu_result(
    payload: Mapping[str, Any], *, errors: list[str], prefix: str
) -> dict[str, Any]:
    samples = list(payload.get("target_samples", []))
    if len(samples) != int(payload.get("target_inference_count", -1)):
        errors.append(f"{prefix}:inference_count")
    if not samples or any(item.get("device") != "cuda" for item in samples):
        errors.append(f"{prefix}:cuda_samples")
    if samples:
        latency = summarize_latencies(
            [float(item.get("http_elapsed_ms", 0)) for item in samples]
        )
        for key in ("p50_ms", "p95_ms", "p99_ms"):
            target_key = f"target_{key}"
            if abs(float(payload.get(target_key, -1)) - latency[key]) > 1e-9:
                errors.append(f"{prefix}:{target_key}")
    owners = list(payload.get("owner_timeline", []))
    zero_overlap = bool(owners) and all(
        "error" not in item and 0 <= int(item.get("owner_count", -1)) <= 1
        for item in owners
    )
    if not zero_overlap:
        errors.append(f"{prefix}:owner_overlap")
    approval = dict(payload.get("approval", {}))
    approval_reuse_rejected = payload.get("approval_reuse_rejected") is True
    approval_consumed_once = (
        approval.get("state") == "consumed"
        and approval.get("single_use") is True
        and approval_reuse_rejected
    )
    if not approval_consumed_once:
        errors.append(f"{prefix}:approval")
    rollback_exact = (
        payload.get("source_identity_before") == payload.get("source_identity_after")
    )
    if not rollback_exact:
        errors.append(f"{prefix}:rollback_identity")
    candidate_gate = dict(payload.get("candidate_gate", {}))
    target_ready = dict(payload.get("target_ready", {}))
    candidate_gate_passed = candidate_gate.get("status") == "passed"
    target_identity_exact = (
        target_ready.get("candidate_id") == candidate_gate.get("candidate_id")
        and target_ready.get("model_sha256") == candidate_gate.get("model_sha256")
        and target_ready.get("cuda_available") is True
        and target_ready.get("device") == "cuda"
    )
    source_after = dict(payload.get("source_prediction_after", {}))
    target_cuda = bool(samples) and all(item.get("device") == "cuda" for item in samples)
    prometheus_restored = payload.get("prometheus_health") == "up"
    recomputed = {
        "candidate_gate_passed": candidate_gate_passed,
        "approval_consumed_once": approval_consumed_once,
        "approval_reuse_rejected": approval_reuse_rejected,
        "zero_owner_overlap": zero_overlap,
        "target_identity_exact": target_identity_exact,
        "rollback_exact": rollback_exact,
        "target_cuda_inference": target_cuda,
        "source_cuda_inference_restored": source_after.get("device") == "cuda",
        "prometheus_restored": prometheus_restored,
    }
    for key, value in recomputed.items():
        if payload.get(key) is not value:
            errors.append(f"{prefix}:{key}")
    return {
        key: recomputed.get(key, payload[key])
        for key in GPU_PUBLIC_FIELDS
    }


def validate_private_evidence(root: Path, errors: list[str]) -> dict[str, Any]:
    index_path = root / "private-evidence-index.json"
    if not index_path.is_file():
        errors.append("private_index_missing")
        return {}
    index = read_json(index_path)
    entries = list(index.get("artifacts", []))
    expected_paths = set()
    total_bytes = 0
    for entry in entries:
        relative = Path(str(entry.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            errors.append("private_path_unsafe")
            continue
        path = root / relative
        expected_paths.add(relative.as_posix())
        if not path.is_file():
            errors.append(f"private_missing:{relative.as_posix()}")
            continue
        size = path.stat().st_size
        total_bytes += size
        if int(entry.get("bytes", -1)) != size:
            errors.append(f"private_bytes:{relative.as_posix()}")
        if entry.get("sha256") != file_sha256(path):
            errors.append(f"private_sha256:{relative.as_posix()}")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != index_path
    }
    if actual_paths != expected_paths:
        errors.append("private_inventory_paths")
    if int(index.get("artifact_count", -1)) != len(entries):
        errors.append("private_artifact_count")
    if int(index.get("total_bytes", -1)) != total_bytes:
        errors.append("private_total_bytes")
    if index.get("aggregate_sha256") != payload_sha256(entries):
        errors.append("private_aggregate_sha256")
    api = [
        read_json(root / "api" / f"repetition-{index:02d}.json")
        for index in range(1, 4)
        if (root / "api" / f"repetition-{index:02d}.json").is_file()
    ]
    calibration_path = root / "gpu/calibration/gpu-handoff-result.json"
    calibration = read_json(calibration_path) if calibration_path.is_file() else {}
    gpu = [
        read_json(root / "gpu" / f"repetition-{index:02d}" / "gpu-handoff-result.json")
        for index in range(1, 4)
        if (root / "gpu" / f"repetition-{index:02d}" / "gpu-handoff-result.json").is_file()
    ]
    if len(api) != 3:
        errors.append("private_api_repetitions")
    if not calibration:
        errors.append("private_gpu_calibration")
    if len(gpu) != 3:
        errors.append("private_gpu_repetitions")
    return {
        "api": api,
        "gpu_calibration": calibration,
        "gpu": gpu,
        "summary": {
            "artifact_count": len(entries),
            "total_bytes": total_bytes,
            "aggregate_sha256": payload_sha256(entries),
            "index_sha256": file_sha256(index_path),
        },
    }


def git_blob_identity(git_root: Path, revision: str, path: str) -> dict[str, str]:
    blob_oid = _git(git_root, "rev-parse", f"{revision}:{path}")
    blob = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=git_root,
        check=True,
        capture_output=True,
    ).stdout
    return {
        "path": path,
        "blob_oid": blob_oid,
        "sha256": hashlib.sha256(blob).hexdigest(),
    }


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise S6EvidenceValidationError(f"s6_json_mapping_required:{path.name}")
    return payload


def canonical(value: Any) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _git(git_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=git_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
