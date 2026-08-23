from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

from evm.scale_validation.s7_runtime import (
    GENERATION_SCHEMAS,
    OPERATIONAL_SCHEMAS,
    QUALITY_SCHEMAS,
    S7RuntimeConfig,
    S7RuntimeError,
    analyze_s7_profiles,
    canonical_sha256,
    percentile,
    profile_family,
)


REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SOURCE_PATHS = {
    "config": "enterprise-vision-mlops/configs/s7_family_admission.toml",
    "runtime": "enterprise-vision-mlops/src/evm/scale_validation/s7_runtime.py",
    "evidence": "enterprise-vision-mlops/src/evm/scale_validation/s7_evidence.py",
    "runner": ("enterprise-vision-mlops/scripts/dev/run_s7_auxiliary_admission_experiment.py"),
    "validator": (
        "enterprise-vision-mlops/scripts/dev/validate_s7_auxiliary_admission_evidence.py"
    ),
    "admission": ("enterprise-vision-mlops/src/evm/model_runtime/family_admission.py"),
    "generative_serving": ("enterprise-vision-mlops/src/evm/model_runtime/serving.py"),
    "image_serving": ("enterprise-vision-mlops/apps/api/efficientnet_serving.py"),
    "gpu_lease": ("enterprise-vision-mlops/src/evm/control_panel/scenario_workloads.py"),
    "prometheus": ("enterprise-vision-mlops/monitoring/prometheus/prometheus.yml"),
}
EXPERIMENT_PATH = (
    "enterprise-vision-mlops/docs/status/evidence/s7-auxiliary-admission-experiment.json"
)
CLOSURE_VALIDATOR_PATHS = {
    "validator_cli": SOURCE_PATHS["validator"],
    "validator_module": SOURCE_PATHS["evidence"],
}


class S7EvidenceValidationError(RuntimeError):
    pass


def project_profile(
    payload: Mapping[str, Any], *, config: S7RuntimeConfig, errors: list[str]
) -> dict[str, Any]:
    profile_id = str(payload.get("profile_id") or "")
    prefix = f"{profile_id}:{payload.get('repetition', 'unknown')}"
    try:
        family = profile_family(profile_id)
    except S7RuntimeError:
        errors.append(f"{prefix}:family")
        family = "image"
    if payload.get("family") != family:
        errors.append(f"{prefix}:family_identity")
    requests = list(payload.get("requests", []))
    completed = [item for item in requests if item.get("outcome") == "completed"]
    rejected = [item for item in requests if item.get("outcome") == "rejected"]
    expired = [item for item in requests if item.get("outcome") == "expired"]
    transport = [item for item in requests if item.get("outcome") == "transport_failed"]
    latencies = [_finite(item.get("latency_seconds"), f"{prefix}:latency") for item in completed]
    measurement_seconds = _finite(
        payload.get("measurement_seconds"), f"{prefix}:measurement_seconds"
    )
    operational = [
        dict(item.get("response", {})).get("operational_metrics", {}) for item in completed
    ]
    allowed_operational = set(OPERATIONAL_SCHEMAS[family])
    for index, metrics in enumerate(operational):
        if not isinstance(metrics, Mapping):
            errors.append(f"{prefix}:operational_metrics_{index}")
            continue
        unexpected = set(metrics) - allowed_operational
        if unexpected:
            errors.append(f"{prefix}:unsupported_operational:{','.join(sorted(unexpected))}")
        if family == "image" and set(metrics) & {
            "input_tokens",
            "generated_tokens",
            "ttft_seconds",
            "tpot_seconds",
            "tokens_per_second",
        }:
            errors.append(f"{prefix}:image_token_metric_present")
        for key, value in metrics.items():
            _finite(value, f"{prefix}:operational:{key}")

    quality = _quality(family, completed)
    generation = _generation(family, completed)
    service_order = sorted(
        completed,
        key=lambda item: _finite(item.get("finished_offset_seconds"), f"{prefix}:finished_offset"),
    )
    long_requests = [item for item in requests if item.get("request_class") == "long"]
    short_requests = [item for item in requests if item.get("request_class") == "short"]
    long_completed = [item for item in completed if item.get("request_class") == "long"]
    short_completed = [item for item in completed if item.get("request_class") == "short"]
    maximum_short_bypass = 0
    for long_item in long_completed:
        arrived = _finite(long_item.get("arrived_offset_seconds"), f"{prefix}:long_arrived")
        finished = _finite(long_item.get("finished_offset_seconds"), f"{prefix}:long_finished")
        bypass = sum(
            1
            for item in service_order
            if item.get("request_class") == "short"
            and arrived
            <= _finite(item.get("finished_offset_seconds"), f"{prefix}:short_finished")
            < finished
        )
        maximum_short_bypass = max(maximum_short_bypass, bypass)
    long_waits = [
        _finite(
            dict(item.get("response", {}))
            .get("operational_metrics", {})
            .get("queue_wait_seconds", 0),
            f"{prefix}:long_queue_wait",
        )
        for item in long_completed
    ]
    starvation_count = sum(
        1
        for item in long_requests
        if item.get("outcome") != "completed"
        or _finite(
            dict(item.get("response", {}))
            .get("operational_metrics", {})
            .get("queue_wait_seconds", 0),
            f"{prefix}:starvation_wait",
        )
        > config.starvation_seconds
    )
    for item in completed:
        observed_long = _scheduling_units(family, item) >= config.long_request_cost_units[family]
        if observed_long != (item.get("request_class") == "long"):
            errors.append(f"{prefix}:request_class_cost_mismatch")
    trace_complete = bool(completed) and all(
        item.get("trace_id_sent") and item.get("trace_id_sent") == item.get("trace_id_observed")
        for item in completed
    )
    if profile_id.endswith("over-limit"):
        trace_complete = True
    final_admission = dict(payload.get("final_admission", {}))
    drained = (
        int(final_admission.get("active_requests", -1)) == 0
        and int(final_admission.get("queue_depth", -1)) == 0
        and all(int(value) == 0 for value in dict(final_admission.get("reserved", {})).values())
    )
    resource_samples = list(payload.get("resource_samples", []))
    peak_vram = max(
        (int(sample.get("gpu_used_memory_bytes") or 0) for sample in resource_samples),
        default=0,
    )
    max_gpu_util = max(
        (
            _finite(sample.get("gpu_utilization_percent", 0), f"{prefix}:gpu_util")
            for sample in resource_samples
        ),
        default=0.0,
    )
    max_temperature = max(
        (
            _finite(sample.get("gpu_temperature_celsius", 0), f"{prefix}:gpu_temp")
            for sample in resource_samples
        ),
        default=0.0,
    )
    expected_request_count = (
        config.fairness_short_requests + config.fairness_long_requests
        if profile_id.endswith("fairness")
        else config.requests_per_profile
    )
    if len(requests) != expected_request_count:
        errors.append(f"{prefix}:request_count")
    if len({str(item.get("request_id")) for item in requests}) != len(requests):
        errors.append(f"{prefix}:request_identity_duplicate")
    if payload.get("seed_applied") is not True:
        errors.append(f"{prefix}:seed_not_applied")
    if not resource_samples:
        errors.append(f"{prefix}:resource_samples_missing")
    projection = {
        "profile_id": profile_id,
        "family": family,
        "repetition": int(payload.get("repetition", 0)),
        "request_count": len(requests),
        "accepted": len(completed),
        "completed": len(completed),
        "rejected": len(rejected),
        "expired": len(expired),
        "transport_failed": len(transport),
        "rejection_statuses": sorted({int(item.get("status_code", 0)) for item in rejected}),
        "measurement_seconds": measurement_seconds,
        "throughput_requests_per_second": (
            len(completed) / measurement_seconds if measurement_seconds else 0.0
        ),
        "p50_seconds": percentile(latencies, 0.50),
        "p95_seconds": percentile(latencies, 0.95),
        "p99_seconds": percentile(latencies, 0.99),
        "maximum_queue_wait_seconds": max(
            (
                _finite(
                    dict(item.get("response", {}))
                    .get("operational_metrics", {})
                    .get("queue_wait_seconds", 0),
                    f"{prefix}:queue_wait",
                )
                for item in completed
            ),
            default=0.0,
        ),
        "short_request_count": len(short_requests),
        "long_request_count": len(long_requests),
        "short_completed": len(short_completed),
        "long_completed": len(long_completed),
        "maximum_short_bypass": maximum_short_bypass,
        "long_request_max_wait_seconds": max(long_waits, default=0.0),
        "starvation_count": starvation_count,
        "oom_count": sum(1 for item in requests if item.get("oom") is True),
        "peak_gpu_used_memory_bytes": peak_vram,
        "gpu_utilization_percent_max": max_gpu_util,
        "gpu_temperature_celsius_max": max_temperature,
        "quality": quality,
        "metric_schema": {
            "quality": list(QUALITY_SCHEMAS[family]),
            "generation": list(GENERATION_SCHEMAS[family]),
            "operational": list(OPERATIONAL_SCHEMAS[family]),
        },
        "trace_complete": trace_complete,
        "prometheus_up": payload.get("prometheus_up") is True,
        "drained": drained,
        "lease_identity_exact": payload.get("lease_identity_exact") is True,
        "cleanup_passed": payload.get("cleanup_passed") is True,
    }
    if generation:
        projection["generation"] = generation
    return projection


def validate_s7_experiment(
    payload: Mapping[str, Any],
    *,
    config: S7RuntimeConfig,
    private_root: Path,
    git_root: Path | None = None,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != "evm.s7_auxiliary_admission_experiment.v1":
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
            if subprocess.run(
                ["git", "merge-base", "--is-ancestor", revision, validation_revision],
                cwd=git_root,
                check=False,
            ).returncode:
                errors.append("source_revision_not_ancestor")
            git_identity = source_git_identity(git_root, revision)
            if _canonical(source.get("git_blobs")) != _canonical(git_identity):
                errors.append("git_blob_identity")
            if git_identity.get("config", {}).get("sha256") != config.sha256:
                errors.append("config_git_blob_sha256")
        except (OSError, subprocess.CalledProcessError):
            errors.append("git_identity_unavailable")
    private = validate_private_evidence(private_root, errors)
    projected = [
        project_profile(item, config=config, errors=errors) for item in private.get("profiles", [])
    ]
    public_profiles = profile_projection_by_identity(
        list(payload.get("profiles", [])),
        errors=errors,
        label="public",
    )
    private_profiles = profile_projection_by_identity(
        projected,
        errors=errors,
        label="private",
    )
    if _canonical(public_profiles) != _canonical(private_profiles):
        errors.append("public_projection")
    try:
        analysis = analyze_s7_profiles(projected, config)
    except Exception:
        analysis = {}
        errors.append("analysis_recompute_failed")
    if _canonical(payload.get("analysis")) != _canonical(analysis):
        errors.append("analysis_projection")
    public_private = dict(payload.get("private_evidence", {}))
    expected_private = private.get("summary", {})
    for key in ("artifact_count", "total_bytes", "aggregate_sha256", "index_sha256"):
        if public_private.get(key) != expected_private.get(key):
            errors.append(f"private_evidence:{key}")
    if payload.get("claim_boundary") != config.claim_boundary:
        errors.append("claim_boundary")
    if analysis.get("runtime_verdict") != "passed":
        errors.append("acceptance")
    if errors:
        raise S7EvidenceValidationError("s7_experiment_invalid:" + ",".join(sorted(set(errors))))
    return {
        "status": "valid",
        "revision": revision,
        "source_identity": git_identity,
        "analysis": analysis,
        "private_evidence": expected_private,
    }


def validate_s7_closure(
    closure: Mapping[str, Any],
    *,
    experiment: Mapping[str, Any],
    experiment_sha256: str,
    config: S7RuntimeConfig,
    private_root: Path,
    git_root: Path | None = None,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    if closure.get("schema_version") != "evm.s7_auxiliary_admission_closure.v1":
        errors.append("schema_version")
    validated = validate_s7_experiment(
        experiment,
        config=config,
        private_root=private_root,
        git_root=git_root,
        validation_revision=validation_revision,
    )
    final = dict(closure.get("final_runtime_evidence", {}))
    if final.get("experiment_git_blob_sha256") != experiment_sha256:
        errors.append("experiment_sha256")
    if _canonical(final.get("acceptance")) != _canonical(validated["analysis"]["acceptance"]):
        errors.append("acceptance")
    if int(final.get("profile_repetitions", 0)) != 36:
        errors.append("profile_repetitions")
    required_regression = (
        "focused_s7",
        "real_postgresql",
        "lifecycle_host_e2e",
        "full_python",
        "control_panel",
        "frontend_production_build",
        "s0_s6_regression",
        "current_revision_cuda_smoke",
    )
    regression = dict(closure.get("regression", {}))
    if any(
        dict(regression.get(name, {})).get("status") != "passed" for name in required_regression
    ):
        errors.append("regression")
    cleanup = dict(closure.get("cleanup", {}))
    for key in (
        "source_serving_ready",
        "actual_cuda_inference",
        "s7_processes_removed",
        "gpu_lease_zero",
        "queue_and_outcome_unknown_zero",
        "prometheus_baseline_healthy",
        "private_inventory_rehash_passed",
        "git_blob_validation_passed",
    ):
        if cleanup.get(key) is not True:
            errors.append(f"cleanup:{key}")
    if closure.get("status") != "verified" or closure.get("verdict") != "passed":
        errors.append("verdict")
    if closure.get("claim_boundary") != config.claim_boundary:
        errors.append("claim_boundary")
    source = dict(closure.get("source_identity", {}))
    experiment_commit = str(source.get("experiment_commit") or "")
    validator_revision = str(source.get("validator_revision") or "")
    if git_root is not None:
        try:
            for revision in (experiment_commit, validator_revision):
                if not REVISION_PATTERN.fullmatch(revision):
                    raise subprocess.CalledProcessError(1, "revision")
                _git(git_root, "cat-file", "-e", f"{revision}^{{commit}}")
                if subprocess.run(
                    ["git", "merge-base", "--is-ancestor", revision, validation_revision],
                    cwd=git_root,
                    check=False,
                ).returncode:
                    errors.append("closure_revision_not_ancestor")
            expected_experiment = git_blob_identity(git_root, experiment_commit, EXPERIMENT_PATH)
            if _canonical(source.get("experiment")) != _canonical(expected_experiment):
                errors.append("closure_experiment_git_blob")
            expected_validators = {
                name: git_blob_identity(git_root, validator_revision, path)
                for name, path in CLOSURE_VALIDATOR_PATHS.items()
            }
            if _canonical(source.get("validators")) != _canonical(expected_validators):
                errors.append("closure_validator_git_blobs")
        except (OSError, subprocess.CalledProcessError):
            errors.append("closure_git_identity_unavailable")
    if errors:
        raise S7EvidenceValidationError("s7_closure_invalid:" + ",".join(sorted(set(errors))))
    return {
        "status": "valid",
        "experiment_sha256": experiment_sha256,
        "acceptance": validated["analysis"]["acceptance"],
        "profile_repetitions": 36,
    }


def validate_private_evidence(root: Path, errors: list[str]) -> dict[str, Any]:
    index_path = root / "private-evidence-index.json"
    if not index_path.is_file():
        errors.append("private_index_missing")
        return {"profiles": [], "summary": {}}
    raw = index_path.read_bytes()
    if b"\r\n" in raw or not raw.endswith(b"\n"):
        errors.append("private_index_not_canonical_lf")
    try:
        index = json.loads(raw)
    except json.JSONDecodeError:
        errors.append("private_index_invalid")
        return {"profiles": [], "summary": {}}
    entries = list(index.get("artifacts", []))
    observed: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []
    for entry in entries:
        relative = str(entry.get("path") or "")
        path = root / relative
        if not relative or not path.is_file():
            errors.append(f"private_artifact_missing:{relative}")
            continue
        digest = _file_sha256(path)
        size = path.stat().st_size
        observed.append({"path": relative, "sha256": digest, "bytes": size})
        if digest != entry.get("sha256") or size != int(entry.get("bytes", -1)):
            errors.append(f"private_artifact_identity:{relative}")
        if relative.startswith("profiles/") and relative.endswith(".json"):
            try:
                profiles.append(json.loads(path.read_bytes()))
            except json.JSONDecodeError:
                errors.append(f"private_profile_invalid:{relative}")
    observed.sort(key=lambda item: item["path"])
    if _canonical(observed) != _canonical(entries):
        errors.append("private_index_projection")
    aggregate = canonical_sha256(observed)
    if aggregate != index.get("aggregate_sha256"):
        errors.append("private_aggregate_sha256")
    summary = {
        "artifact_count": len(observed),
        "total_bytes": sum(int(item["bytes"]) for item in observed),
        "aggregate_sha256": aggregate,
        "index_sha256": hashlib.sha256(raw).hexdigest(),
    }
    return {"profiles": profiles, "summary": summary}


def profile_projection_by_identity(
    profiles: list[Any],
    *,
    errors: list[str],
    label: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(profiles):
        if not isinstance(value, Mapping):
            errors.append(f"{label}_profile_invalid:{index}")
            continue
        profile = dict(value)
        profile_id = str(profile.get("profile_id") or "")
        try:
            repetition = int(profile.get("repetition", 0))
        except (TypeError, ValueError):
            errors.append(f"{label}_profile_identity:{index}")
            continue
        if not profile_id or repetition < 1:
            errors.append(f"{label}_profile_identity:{index}")
            continue
        identity = f"{profile_id}:r{repetition:02d}"
        if identity in result:
            errors.append(f"{label}_profile_duplicate:{identity}")
            continue
        result[identity] = profile
    return result


def source_git_identity(git_root: Path, revision: str) -> dict[str, Any]:
    _git(git_root, "cat-file", "-e", f"{revision}^{{commit}}")
    return {
        name: git_blob_identity(git_root, revision, path) for name, path in SOURCE_PATHS.items()
    }


def git_blob_identity(git_root: Path, revision: str, path: str) -> dict[str, Any]:
    raw = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=git_root,
        check=True,
        capture_output=True,
    ).stdout
    if b"\r\n" in raw or not raw.endswith(b"\n"):
        raise subprocess.CalledProcessError(1, "git_blob_not_canonical_lf")
    oid = _git(git_root, "rev-parse", f"{revision}:{path}")
    return {"path": path, "blob_oid": oid, "sha256": hashlib.sha256(raw).hexdigest()}


def token_f1(prediction: str, expected: str) -> float:
    left = prediction.lower().split()
    right = expected.lower().split()
    if not left or not right:
        return 0.0
    counts: dict[str, int] = {}
    for token in right:
        counts[token] = counts.get(token, 0) + 1
    overlap = 0
    for token in left:
        if counts.get(token, 0) > 0:
            counts[token] -= 1
            overlap += 1
    if overlap == 0:
        return 0.0
    precision = overlap / len(left)
    recall = overlap / len(right)
    return 2 * precision * recall / (precision + recall)


def _quality(family: str, completed: list[Mapping[str, Any]]) -> dict[str, float]:
    if not completed:
        return {key: 0.0 for key in QUALITY_SCHEMAS[family]}
    if family == "image":
        correct = sum(
            1
            for item in completed
            if str(dict(item.get("response", {})).get("prediction")) == str(item.get("expected"))
        )
        confidence = [
            _finite(dict(item.get("response", {})).get("confidence"), "confidence")
            for item in completed
        ]
        return {
            "binary_accuracy": correct / len(completed),
            "mean_confidence": sum(confidence) / len(confidence),
        }
    if family == "vlm":
        parsed = [
            item
            for item in completed
            if dict(item.get("response", {})).get("predicted_index") is not None
        ]
        correct = sum(
            1
            for item in parsed
            if int(dict(item.get("response", {}))["predicted_index"]) == int(item.get("expected"))
        )
        return {
            "accuracy": correct / len(completed),
            "parse_rate": len(parsed) / len(completed),
        }
    scores = [
        token_f1(
            str(dict(item.get("response", {})).get("output") or ""),
            str(item.get("expected") or ""),
        )
        for item in completed
    ]
    nonempty = sum(
        1 for item in completed if str(dict(item.get("response", {})).get("output") or "").strip()
    )
    return {
        "mean_token_f1": sum(scores) / len(scores),
        "nonempty_rate": nonempty / len(completed),
    }


def _generation(family: str, completed: list[Mapping[str, Any]]) -> dict[str, Any]:
    if family == "image" or not completed:
        return {}
    metrics = [
        dict(dict(item.get("response", {})).get("operational_metrics", {})) for item in completed
    ]
    generated = [int(item.get("generated_tokens", 0)) for item in metrics]
    token_rates = [_finite(item.get("tokens_per_second", 0), "token_rate") for item in metrics]
    ttft = [_finite(item["ttft_seconds"], "ttft") for item in metrics if "ttft_seconds" in item]
    tpot = [_finite(item["tpot_seconds"], "tpot") for item in metrics if "tpot_seconds" in item]
    reasons: dict[str, int] = {}
    for item in completed:
        reason = str(dict(item.get("response", {})).get("termination_reason") or "unknown")
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "generated_tokens_total": sum(generated),
        "tokens_per_second_mean": sum(token_rates) / len(token_rates),
        "ttft_p95_seconds": percentile(ttft, 0.95),
        "tpot_p95_seconds": percentile(tpot, 0.95),
        "termination_reasons": reasons,
    }


def _scheduling_units(family: str, item: Mapping[str, Any]) -> float:
    metrics = dict(dict(item.get("response", {})).get("operational_metrics", {}))
    request_units = _finite(metrics.get("request_bytes", 0), "request_bytes") / 1024
    if family == "image":
        return max(
            1.0,
            request_units,
            _finite(metrics.get("image_bytes", 0), "image_bytes") / 1024,
            _finite(metrics.get("image_pixels", 0), "image_pixels") / 1024,
        )
    requested_output = int(item.get("requested_output_tokens", 0))
    if family == "vlm":
        return max(
            1.0,
            request_units,
            _finite(metrics.get("image_bytes", 0), "image_bytes") / 1024,
            _finite(metrics.get("image_pixels", 0), "image_pixels") / 1024,
            1536 + requested_output,
        )
    return max(
        1.0,
        request_units,
        _finite(metrics.get("input_tokens", 0), "input_tokens") + requested_output,
    )


def _finite(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise S7EvidenceValidationError(f"s7_metric_invalid:{label}") from exc
    if not (result >= 0) or result in {float("inf"), float("-inf")}:
        raise S7EvidenceValidationError(f"s7_metric_invalid:{label}")
    return result


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
