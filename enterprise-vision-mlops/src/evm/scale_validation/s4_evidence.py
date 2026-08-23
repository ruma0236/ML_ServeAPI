from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evm.scale_validation.s4_runtime import (
    S4RuntimeConfig,
    analyze_s4_results,
    canonical_sha256,
)


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
RUNTIME_MODULE_PATH = "enterprise-vision-mlops/src/evm/scale_validation/s4_runtime.py"
RUNTIME_CONFIG_PATH = "enterprise-vision-mlops/configs/s4_gpu_batching_runtime.toml"
MINIMUM_DELIVERY_RATIO = 0.98
MAXIMUM_DELIVERY_RATIO = 1.02
MAXIMUM_SKIPPED_RELEASE_RATIO = 0.02


class S4EvidenceValidationError(RuntimeError):
    pass


def validate_s4_gpu_batching_evidence(
    payload: Mapping[str, Any],
    *,
    config: S4RuntimeConfig,
    git_root: Path | None = None,
    validation_revision: str = "HEAD",
    private_root: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    _validate_finite_numbers(payload, path="evidence", errors=errors)
    if errors:
        raise S4EvidenceValidationError(
            "s4_gpu_batching_evidence_invalid:" + ",".join(sorted(set(errors)))
        )
    if payload.get("schema_version") != "evm.s4_gpu_batching_experiment.v1":
        errors.append("schema_version")

    source = dict(payload.get("source_identity", {}))
    runtime_revision = str(source.get("implementation_revision") or "")
    if not REVISION_PATTERN.fullmatch(runtime_revision):
        errors.append("runtime_revision")
    if source.get("runtime_config_sha256") != config.sha256:
        errors.append("runtime_config_sha256")
    if source.get("image") != f"enterprise-vision-mlops-gpu-batching:{runtime_revision[:12]}":
        errors.append("runtime_image_identity")
    source_identity: dict[str, Any] = {}
    if git_root is not None and REVISION_PATTERN.fullmatch(runtime_revision):
        source_identity = _validate_git_identity(
            git_root=git_root,
            runtime_revision=runtime_revision,
            validation_revision=validation_revision,
            expected_config_sha256=config.sha256,
            errors=errors,
        )

    if _canonical(payload.get("runtime_contract")) != _canonical(config.public_dict()):
        errors.append("runtime_contract")
    model = dict(payload.get("model_identity", {}))
    for key in (
        "dataset_identity_sha256",
        "split_manifest_sha256",
        "model_identity_sha256",
        "artifact_sha256",
        "registry_sha256",
    ):
        if not SHA256_PATTERN.fullmatch(str(model.get(key) or "")):
            errors.append(f"model_identity:{key}")
    if model.get("architecture") != config.architecture:
        errors.append("model_architecture")

    raw_results = payload.get("point_results")
    results = list(raw_results) if isinstance(raw_results, Sequence) else []
    _validate_point_contract(results, config=config, errors=errors)
    recomputed = analyze_s4_results(results, config)
    if _canonical(payload.get("analysis")) != _canonical(recomputed):
        errors.append("analysis_projection")
    if _canonical(payload.get("acceptance")) != _canonical(recomputed["acceptance"]):
        errors.append("acceptance_projection")
    if payload.get("runtime_verdict") != recomputed["runtime_verdict"]:
        errors.append("runtime_verdict_projection")
    if not all(bool(value) for value in recomputed["acceptance"].values()):
        errors.append("acceptance_not_all_passed")

    _validate_stabilization(
        payload.get("open_loop_stabilization"), config=config, errors=errors
    )
    _validate_cleanup(payload.get("cleanup"), errors=errors)
    failures = payload.get("failed_attempts_and_rca")
    if not isinstance(failures, Sequence) or failures:
        errors.append("accepted_run_failures")
    counts = dict(payload.get("experiment_counts", {}))
    if counts != {"matrix": 60, "instance_axis": 3, "open_loop": 3, "total": 66}:
        errors.append("experiment_counts")

    private_summary: dict[str, Any] = {}
    if private_root is not None:
        private_summary = _validate_private_evidence(
            payload=payload,
            results=results,
            private_root=private_root,
            errors=errors,
        )
    else:
        private = dict(payload.get("private_evidence", {}))
        if int(private.get("artifact_count", 0)) <= 0:
            errors.append("private_artifact_count")
        for key in ("aggregate_sha256", "index_sha256"):
            if not SHA256_PATTERN.fullmatch(str(private.get(key) or "")):
                errors.append(f"private_{key}")

    if errors:
        raise S4EvidenceValidationError(
            "s4_gpu_batching_evidence_invalid:" + ",".join(sorted(set(errors)))
        )
    selected = dict(recomputed["selected_operating_point"] or {})
    open_loop = [item for item in results if item.get("mode") == "open-loop"]
    return {
        "status": "valid",
        "runtime_revision": runtime_revision,
        "point_result_count": len(results),
        "acceptance": recomputed["acceptance"],
        "runtime_verdict": recomputed["runtime_verdict"],
        "selected_operating_point": {
            "batch_size": selected.get("batch_size"),
            "max_delay_ms": selected.get("max_delay_ms"),
            "instance_count": selected.get("instance_count"),
            "service_rps_mean": selected.get("service_rps_mean"),
        },
        "validated_open_loop_service_rps_mean": (
            sum(float(item["service_rps"]) for item in open_loop) / len(open_loop)
        ),
        "s2_capacity_recalculation": recomputed["s2_capacity_recalculation"],
        "source_identity": source_identity,
        "private_evidence": private_summary,
    }


def validate_s4_gpu_batching_closure(
    closure: Mapping[str, Any],
    *,
    experiment: Mapping[str, Any],
    experiment_sha256: str,
    config: S4RuntimeConfig,
    git_root: Path | None = None,
    validation_revision: str = "HEAD",
    private_root: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if closure.get("schema_version") != "evm.s4_gpu_batching_closure.v1":
        errors.append("closure_schema_version")
    validated = validate_s4_gpu_batching_evidence(
        experiment,
        config=config,
        git_root=git_root,
        validation_revision=validation_revision,
        private_root=private_root,
    )
    final = dict(closure.get("final_runtime_evidence", {}))
    if final.get("git_blob_sha256") != experiment_sha256:
        errors.append("closure_experiment_sha256")
    if final.get("point_result_count") != 66:
        errors.append("closure_point_count")
    if _canonical(final.get("acceptance")) != _canonical(validated["acceptance"]):
        errors.append("closure_acceptance")
    if closure.get("verdict") != "passed" or closure.get("status") != "verified":
        errors.append("closure_verdict")
    if _canonical(closure.get("selected_operating_point")) != _canonical(
        validated["selected_operating_point"]
    ):
        errors.append("closure_selected_point")
    if _canonical(closure.get("s2_capacity_recalculation")) != _canonical(
        validated["s2_capacity_recalculation"]
    ):
        errors.append("closure_capacity")
    regression = dict(closure.get("regression", {}))
    required = (
        "focused_s4",
        "full_python_real_postgresql",
        "lifecycle_host_e2e",
        "control_panel",
        "frontend_production_build",
        "s0_s3_regression",
        "current_revision_runtime_smoke",
    )
    if any(dict(regression.get(key, {})).get("status") != "passed" for key in required):
        errors.append("closure_regression")
    cleanup = dict(closure.get("cleanup", {}))
    if cleanup.get("runtime_cleanup_passed") is not True:
        errors.append("closure_cleanup")
    if cleanup.get("private_inventory_rehash_passed") is not True:
        errors.append("closure_private_rehash")
    if cleanup.get("git_blob_validation_passed") is not True:
        errors.append("closure_git_blob_validation")
    if errors:
        raise S4EvidenceValidationError(
            "s4_gpu_batching_closure_invalid:" + ",".join(sorted(set(errors)))
        )
    return {
        "status": "valid",
        "experiment_sha256": experiment_sha256,
        "point_result_count": 66,
        "acceptance": validated["acceptance"],
    }


def _validate_point_contract(
    results: list[Any], *, config: S4RuntimeConfig, errors: list[str]
) -> None:
    if len(results) != 66:
        errors.append("point_result_count")
    expected_matrix = {
        (batch, delay, 1)
        for batch in config.batch_sizes
        for delay in config.max_delays_ms
    }
    matrix_counts: Counter[tuple[int, int, int]] = Counter()
    instance_repetitions: list[int] = []
    open_repetitions: list[int] = []
    for index, item in enumerate(results):
        if not isinstance(item, Mapping):
            errors.append(f"point_mapping:{index}")
            continue
        mode = item.get("mode")
        key = (
            int(item.get("batch_size", -1)),
            int(item.get("max_delay_ms", -1)),
            int(item.get("instance_count", -1)),
        )
        repetition = int(item.get("repetition", 0))
        if mode == "matrix":
            matrix_counts[key] += 1
        elif mode == "instance-axis":
            if key != (1, 0, 2):
                errors.append("instance_axis_identity")
            instance_repetitions.append(repetition)
        elif mode == "open-loop":
            open_repetitions.append(repetition)
            _validate_open_loop(item, config=config, index=index, errors=errors)
        else:
            errors.append(f"point_mode:{index}")
        if item.get("evidence_valid") is not True:
            errors.append(f"point_evidence:{index}")
        if item.get("hard_stop_guardrail_passed") is not True:
            errors.append(f"point_hard_stop:{index}")
        if int(item.get("oom_count", -1)) != 0:
            errors.append(f"point_oom:{index}")
        if item.get("prometheus_up") is not True:
            errors.append(f"point_prometheus:{index}")
        if int(item.get("request_count", 0)) <= 0 or int(item.get("success_count", 0)) <= 0:
            errors.append(f"point_non_vacuous:{index}")
        if float(item.get("error_rate", 1.0)) > config.maximum_error_rate:
            errors.append(f"point_error_rate:{index}")
        trace = dict(item.get("trace", {}))
        if (
            int(trace.get("expected_count", 0)) <= 0
            or trace.get("expected_count") != trace.get("complete_count")
            or int(trace.get("missing_count", -1)) != 0
        ):
            errors.append(f"point_trace:{index}")
        gauges = dict(item.get("terminal_gauges", {}))
        if len(gauges) != 3 or any(float(value) != 0 for value in gauges.values()):
            errors.append(f"point_terminal_gauges:{index}")
        if not SHA256_PATTERN.fullmatch(str(item.get("private_point_sha256") or "")):
            errors.append(f"point_private_hash:{index}")
    if set(matrix_counts) != expected_matrix:
        errors.append("matrix_identity")
    if any(matrix_counts[key] != config.repetitions for key in expected_matrix):
        errors.append("matrix_repetitions")
    if sorted(instance_repetitions) != [1, 2, 3]:
        errors.append("instance_repetitions")
    if sorted(open_repetitions) != [1, 2, 3]:
        errors.append("open_repetitions")


def _validate_open_loop(
    item: Mapping[str, Any], *, config: S4RuntimeConfig, index: int, errors: list[str]
) -> None:
    if float(item.get("target_offered_rps", 0)) != config.open_maximum_target_rps:
        errors.append(f"open_target:{index}")
    delivery = float(item.get("offered_rate_delivery_ratio", 0))
    if not MINIMUM_DELIVERY_RATIO <= delivery <= MAXIMUM_DELIVERY_RATIO:
        errors.append(f"open_delivery:{index}")
    if float(item.get("skipped_release_ratio", math.inf)) > MAXIMUM_SKIPPED_RELEASE_RATIO:
        errors.append(f"open_skipped_release:{index}")
    maximum_release_lag_ms = max(
        50.0,
        2000.0 / config.open_maximum_target_rps,
    )
    if float(item.get("release_lag_p99_ms", math.inf)) > maximum_release_lag_ms:
        errors.append(f"open_release_lag:{index}")
    if item.get("load_generator_valid") is not True:
        errors.append(f"open_load_generator:{index}")
    if item.get("operating_guardrail_passed") is not True:
        errors.append(f"open_operating_guardrail:{index}")
    if float(item.get("p99_ms", math.inf)) > config.maximum_p99_ms:
        errors.append(f"open_p99:{index}")
    if float(item.get("queue_wait_p99_ms", math.inf)) > config.maximum_queue_wait_ms:
        errors.append(f"open_queue_p99:{index}")


def _validate_stabilization(
    raw: Any, *, config: S4RuntimeConfig, errors: list[str]
) -> None:
    value = dict(raw) if isinstance(raw, Mapping) else {}
    if value.get("quiet_gate_passed") is not True:
        errors.append("stabilization_verdict")
    if value.get("lease_matches") is not True:
        errors.append("stabilization_lease")
    if value.get("experiment_container_absent") is not True:
        errors.append("stabilization_container")
    if int(value.get("sample_count", 0)) < 13:
        errors.append("stabilization_sample_count")
    if int(value.get("terminal_sample_count", 0)) != config.open_stabilization_terminal_sample_count:
        errors.append("stabilization_terminal_count")
    if float(value.get("terminal_utilization_percent_median", math.inf)) > float(
        value.get("utilization_ceiling_percent", -math.inf)
    ):
        errors.append("stabilization_utilization")
    if float(value.get("terminal_temperature_celsius_max", math.inf)) > config.maximum_temperature_celsius:
        errors.append("stabilization_temperature")


def _validate_cleanup(raw: Any, *, errors: list[str]) -> None:
    cleanup = dict(raw) if isinstance(raw, Mapping) else {}
    for key in (
        "lease_released",
        "holder_uid_restored",
        "holder_ready",
        "serving_cuda_ready",
        "active_lease_absent",
        "api_container_absent",
        "prometheus_container_absent",
    ):
        if cleanup.get(key) is not True:
            errors.append(f"cleanup:{key}")


def _validate_private_evidence(
    *,
    payload: Mapping[str, Any],
    results: list[Any],
    private_root: Path,
    errors: list[str],
) -> dict[str, Any]:
    index_path = private_root / "private-evidence-index.json"
    if not index_path.is_file():
        errors.append("private_index_missing")
        return {}
    index_raw = index_path.read_bytes()
    index = json.loads(index_raw)
    entries = list(index.get("entries", []))
    observed_entries: list[dict[str, Any]] = []
    for offset, entry in enumerate(entries):
        relative = str(entry.get("path") or "")
        path = private_root / relative
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            errors.append(f"private_path:{offset}")
            continue
        if not path.is_file():
            errors.append(f"private_missing:{relative}")
            continue
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != entry.get("sha256") or len(raw) != int(entry.get("size_bytes", -1)):
            errors.append(f"private_hash:{relative}")
        observed_entries.append(
            {"path": relative, "size_bytes": len(raw), "sha256": digest}
        )
    private = dict(payload.get("private_evidence", {}))
    if len(observed_entries) != int(private.get("artifact_count", -1)):
        errors.append("private_count")
    if sum(item["size_bytes"] for item in observed_entries) != int(
        private.get("total_bytes", -1)
    ):
        errors.append("private_bytes")
    if canonical_sha256(observed_entries) != private.get("aggregate_sha256"):
        errors.append("private_aggregate")
    if hashlib.sha256(index_raw).hexdigest() != private.get("index_sha256"):
        errors.append("private_index_sha256")
    for item in results:
        if not isinstance(item, Mapping):
            continue
        point_path = (
            private_root
            / str(item["point_id"])
            / f"repetition-{int(item['repetition'])}"
            / "point-private.json"
        )
        if not point_path.is_file() or hashlib.sha256(point_path.read_bytes()).hexdigest() != item.get(
            "private_point_sha256"
        ):
            errors.append(f"private_point_projection:{item.get('point_id')}:{item.get('repetition')}")
    return {
        "artifact_count": len(observed_entries),
        "total_bytes": sum(item["size_bytes"] for item in observed_entries),
        "aggregate_sha256": canonical_sha256(observed_entries),
        "index_sha256": hashlib.sha256(index_raw).hexdigest(),
    }


def _validate_git_identity(
    *,
    git_root: Path,
    runtime_revision: str,
    validation_revision: str,
    expected_config_sha256: str,
    errors: list[str],
) -> dict[str, Any]:
    try:
        commit = _git(git_root, "rev-parse", f"{runtime_revision}^{{commit}}").decode().strip()
        if commit != runtime_revision:
            errors.append("runtime_commit")
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", runtime_revision, validation_revision],
            cwd=git_root,
            check=False,
        ).returncode
        if ancestor != 0:
            errors.append("runtime_ancestry")
        config_raw = _git(git_root, "show", f"{runtime_revision}:{RUNTIME_CONFIG_PATH}")
        module_raw = _git(git_root, "show", f"{runtime_revision}:{RUNTIME_MODULE_PATH}")
        config_sha = hashlib.sha256(config_raw).hexdigest()
        if config_sha != expected_config_sha256:
            errors.append("runtime_config_git_blob")
        return {
            "runtime_revision": runtime_revision,
            "runtime_config_git_blob_sha256": config_sha,
            "runtime_module_git_blob_sha256": hashlib.sha256(module_raw).hexdigest(),
            "validation_revision": _git(
                git_root, "rev-parse", f"{validation_revision}^{{commit}}"
            ).decode().strip(),
        }
    except (subprocess.CalledProcessError, OSError):
        errors.append("git_identity")
        return {}


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True
    ).stdout


def _validate_finite_numbers(value: Any, *, path: str, errors: list[str]) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(f"non_finite:{path}")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _validate_finite_numbers(item, path=f"{path}.{key}", errors=errors)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _validate_finite_numbers(item, path=f"{path}[{index}]", errors=errors)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
