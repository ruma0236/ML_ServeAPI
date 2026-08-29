from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from evm.scale_validation.x1_artifacts import PROFILE_IDS, render_triton_config
from evm.scale_validation.x1_contract import (
    GPU_NAME,
    GPU_UUID,
    MODEL_IDS,
    REPETITIONS,
    X1Contract,
    canonical_sha256,
    sha256_file,
)


class X1RuntimeValidationError(RuntimeError):
    pass


def validate_triton_runtime_config(
    payload: Mapping[str, Any], *, model_id: str, identity: Mapping[str, Any]
) -> None:
    if model_id not in MODEL_IDS:
        raise X1RuntimeValidationError("x1_triton_config_model")
    feature_count = _strict_nonnegative(identity.get("feature_count"), "feature_count")
    max_batch_size = _strict_nonnegative(identity.get("max_batch_size"), "max_batch_size")
    preferred = identity.get("preferred_batch_size")
    if not isinstance(preferred, list) or any(type(item) is not int for item in preferred):
        raise X1RuntimeValidationError("x1_triton_config_preferred")
    delay = _strict_nonnegative(
        identity.get("max_queue_delay_microseconds"), "max_queue_delay_microseconds"
    )
    if (
        payload.get("name") != model_id
        or payload.get("backend") != "pytorch"
        or payload.get("max_batch_size") != max_batch_size
        or payload.get("version_policy") != {"specific": {"versions": ["1"]}}
    ):
        raise X1RuntimeValidationError("x1_triton_config_identity")
    groups = payload.get("instance_group")
    if not isinstance(groups, list) or len(groups) != 1 or not isinstance(groups[0], Mapping):
        raise X1RuntimeValidationError("x1_triton_config_instance_group")
    group = groups[0]
    if (
        group.get("name") != f"{model_id}_0"
        or group.get("kind") != "KIND_GPU"
        or group.get("count") != 1
        or group.get("gpus") != [0]
    ):
        raise X1RuntimeValidationError("x1_triton_config_instance_group")
    inputs = payload.get("input")
    outputs = payload.get("output")
    if not isinstance(inputs, list) or len(inputs) != 1 or not isinstance(inputs[0], Mapping):
        raise X1RuntimeValidationError("x1_triton_config_input")
    if not isinstance(outputs, list) or len(outputs) != 1 or not isinstance(outputs[0], Mapping):
        raise X1RuntimeValidationError("x1_triton_config_output")
    input_record = inputs[0]
    output_record = outputs[0]
    if (
        input_record.get("name") != "INPUT__0"
        or input_record.get("data_type") != "TYPE_FP32"
        or input_record.get("dims") != [feature_count]
        or input_record.get("is_shape_tensor", False) is not False
        or input_record.get("allow_ragged_batch", False) is not False
        or input_record.get("optional", False) is not False
    ):
        raise X1RuntimeValidationError("x1_triton_config_input")
    if (
        output_record.get("name") != "OUTPUT__0"
        or output_record.get("data_type") != "TYPE_FP32"
        or output_record.get("dims") != [1]
        or output_record.get("is_shape_tensor", False) is not False
    ):
        raise X1RuntimeValidationError("x1_triton_config_output")
    dynamic = payload.get("dynamic_batching")
    if max_batch_size == 0:
        if dynamic not in (None, {}):
            raise X1RuntimeValidationError("x1_triton_config_batching")
    elif (
        not isinstance(dynamic, Mapping)
        or dynamic.get("preferred_batch_size") != preferred
        or dynamic.get("max_queue_delay_microseconds") != delay
        or dynamic.get("preserve_ordering") is not True
        or dynamic.get("priority_levels", 0) != 0
    ):
        raise X1RuntimeValidationError("x1_triton_config_batching")
    if payload.get("sequence_batching") not in (None, {}) or payload.get(
        "ensemble_scheduling"
    ) not in (None, {}):
        raise X1RuntimeValidationError("x1_triton_config_extra_scheduler")
    if payload.get("model_warmup") not in (None, []):
        raise X1RuntimeValidationError("x1_triton_config_model_warmup")
    cache = payload.get("response_cache")
    if cache not in (None, {}) and (
        not isinstance(cache, Mapping) or cache.get("enable") is not False
    ):
        raise X1RuntimeValidationError("x1_triton_config_response_cache")


def build_runtime_manifest(
    contract: X1Contract,
    *,
    artifact_manifest: Mapping[str, Any],
    artifact_manifest_file_sha256: str,
    artifact_manifest_container_path: str,
    active_profile: str,
    lease_run_id: str,
    lease_id: str,
    fencing_token_sha256: str,
    source_revision: str,
) -> dict[str, Any]:
    contract.assert_unchanged()
    if len(artifact_manifest_file_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in artifact_manifest_file_sha256
    ):
        raise X1RuntimeValidationError("x1_runtime_artifact_manifest_sha256")
    if active_profile not in PROFILE_IDS:
        raise X1RuntimeValidationError("x1_runtime_profile")
    models = artifact_manifest.get("models")
    repositories = artifact_manifest.get("repositories")
    if not isinstance(models, Mapping) or set(models) != set(MODEL_IDS):
        raise X1RuntimeValidationError("x1_runtime_artifact_models")
    if not isinstance(repositories, Mapping) or set(repositories) != set(PROFILE_IDS):
        raise X1RuntimeValidationError("x1_runtime_artifact_profiles")
    profiles: dict[str, Any] = {}
    for profile_id in PROFILE_IDS:
        profile = repositories[profile_id]
        if not isinstance(profile, Mapping) or not isinstance(profile.get("entries"), list):
            raise X1RuntimeValidationError("x1_runtime_artifact_profile")
        indexed = {entry["path"]: entry for entry in profile["entries"]}
        profile_models: dict[str, Any] = {}
        for model_id in MODEL_IDS:
            config = indexed.get(f"{model_id}/config.pbtxt")
            artifact = indexed.get(f"{model_id}/1/model.pt")
            if not isinstance(config, Mapping) or not isinstance(artifact, Mapping):
                raise X1RuntimeValidationError("x1_runtime_repository_entry")
            batch_contract = {
                "disabled": {
                    "max_batch_size": 0,
                    "preferred_batch_size": [],
                    "max_queue_delay_microseconds": 0,
                },
                "enabled-4-8-2ms": {
                    "max_batch_size": 16,
                    "preferred_batch_size": [4, 8],
                    "max_queue_delay_microseconds": 2000,
                },
                "enabled-8-16-10ms": {
                    "max_batch_size": 32,
                    "preferred_batch_size": [8, 16],
                    "max_queue_delay_microseconds": 10000,
                },
            }[profile_id]
            profile_models[model_id] = {
                "model_version": "1",
                "artifact_sha256": artifact["sha256"],
                "config_sha256": config["sha256"],
                **batch_contract,
                "feature_count": 39 if model_id == "criteo_dlrm_lite" else 28,
                "instance_group_count": 1,
                "gpu_device_index": 0,
            }
        profiles[profile_id] = {
            "repository_aggregate_sha256": profile["aggregate_sha256"],
            "models": profile_models,
        }
    manifest = {
        "schema_version": "evm.s8_v4.x1_runtime_manifest.v1",
        "source_revision": source_revision,
        "contract_sha256": contract.sha256,
        "manifest_sha256": artifact_manifest_file_sha256,
        "artifact_manifest_path": artifact_manifest_container_path,
        "active_profile": active_profile,
        "models": profiles[active_profile]["models"],
        "profiles": profiles,
        "lease": {
            "run_id": lease_run_id,
            "lease_id": lease_id,
            "fencing_token_sha256": fencing_token_sha256,
            "purpose": "scale_validation_inference",
            "scenario_id": "X1",
            "model_family": "heterogeneous",
            "source_revision": source_revision,
        },
    }
    manifest["runtime_identity_sha256"] = canonical_sha256(manifest)
    return manifest


def validate_q0_bundle(
    bundle: Mapping[str, Any], contract: X1Contract, artifact_manifest: Mapping[str, Any]
) -> dict[str, Any]:
    contract.assert_unchanged()
    if bundle.get("schema_version") != "evm.s8_v4.x1_q0.v1":
        raise X1RuntimeValidationError("x1_q0_schema")
    records = bundle.get("models")
    if not isinstance(records, list) or len(records) != 4:
        raise X1RuntimeValidationError("x1_q0_model_count")
    by_model: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise X1RuntimeValidationError("x1_q0_record")
        model_id = _string(record, "model_id")
        if model_id in by_model or model_id not in MODEL_IDS:
            raise X1RuntimeValidationError("x1_q0_model_set")
        by_model[model_id] = record
    if set(by_model) != set(MODEL_IDS):
        raise X1RuntimeValidationError("x1_q0_model_set")
    artifact_models = artifact_manifest["models"]
    for model_id in MODEL_IDS:
        record = by_model[model_id]
        if (
            record.get("model_version") != "1"
            or record.get("artifact_sha256") != artifact_models[model_id]["artifact_sha256"]
            or record.get("isolated_request_count") != 64
            or record.get("correct_count") != 64
            or record.get("failed_count") != 0
            or record.get("cpu_fallback_detected") is not False
            or record.get("actual_cuda_activity") is not True
        ):
            raise X1RuntimeValidationError(f"x1_q0_outcome:{model_id}")
        metrics = record.get("triton_delta")
        if not isinstance(metrics, Mapping):
            raise X1RuntimeValidationError(f"x1_q0_metrics:{model_id}")
        for key in ("success_count", "inference_count"):
            if _strict_nonnegative(metrics.get(key), key) != 64:
                raise X1RuntimeValidationError(f"x1_q0_metric_count:{model_id}:{key}")
        if _strict_nonnegative(metrics.get("execution_count"), "execution_count") <= 0:
            raise X1RuntimeValidationError(f"x1_q0_execution:{model_id}")
        if _finite(metrics.get("compute_duration_us"), "compute_duration_us") <= 0:
            raise X1RuntimeValidationError(f"x1_q0_compute:{model_id}")
        if record.get("gpu_uuid") != GPU_UUID or record.get("gpu_name") != GPU_NAME:
            raise X1RuntimeValidationError(f"x1_q0_gpu_identity:{model_id}")
        if record.get("config_bytes_sha256") != record.get("config_sha256"):
            raise X1RuntimeValidationError(f"x1_q0_config_binding:{model_id}")
        if record.get("repository_index_exact") is not True:
            raise X1RuntimeValidationError(f"x1_q0_repository_index:{model_id}")
    return {
        "models_passed": 4,
        "requests_passed": 256,
        "silent_cpu_fallback": 0,
        "actual_cuda": True,
    }


def select_batching_profiles(records: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    expected_candidates = ("enabled-4-8-2ms", "enabled-8-16-10ms")
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str, int]] = set()
    for record in records:
        model_id = record.get("model_id")
        candidate = record.get("batch_candidate")
        repetition = record.get("repetition")
        identity = (str(model_id), str(candidate), repetition)
        if (
            model_id not in MODEL_IDS
            or candidate not in expected_candidates
            or repetition not in REPETITIONS
            or identity in seen
        ):
            raise X1RuntimeValidationError("x1_batch_calibration_identity")
        seen.add(identity)
        groups[(str(model_id), str(candidate))].append(record)
    expected = {
        (model_id, candidate, repetition)
        for model_id in MODEL_IDS
        for candidate in expected_candidates
        for repetition in REPETITIONS
    }
    if seen != expected:
        raise X1RuntimeValidationError("x1_batch_calibration_repetition_set")
    result: dict[str, str] = {}
    for model_id in MODEL_IDS:
        scored: list[tuple[float, str]] = []
        for candidate in expected_candidates:
            rates = [
                _positive(record.get("safe_service_rps"), "safe_service_rps")
                for record in groups[(model_id, candidate)]
            ]
            if any(
                record.get("guardrails_passed") is not True
                for record in groups[(model_id, candidate)]
            ):
                raise X1RuntimeValidationError("x1_batch_calibration_guardrail")
            scored.append((min(rates), candidate))
        scored.sort(key=lambda item: (-item[0], expected_candidates.index(item[1])))
        result[model_id] = scored[0][1]
    return result


def validate_runtime_profile_files(
    *, artifact_root: Path, artifact_manifest: Mapping[str, Any], profile_id: str
) -> None:
    if profile_id not in PROFILE_IDS:
        raise X1RuntimeValidationError("x1_runtime_profile")
    models = artifact_manifest["models"]
    for model_id in MODEL_IDS:
        feature_count = int(models[model_id]["feature_count"])
        profile_root = artifact_root / "model-repositories" / profile_id
        config = profile_root / model_id / "config.pbtxt"
        artifact = profile_root / model_id / "1" / "model.pt"
        if config.read_bytes() != render_triton_config(
            model_id=model_id, feature_count=feature_count, profile_id=profile_id
        ):
            raise X1RuntimeValidationError("x1_runtime_config_drift")
        if sha256_file(artifact) != models[model_id]["artifact_sha256"]:
            raise X1RuntimeValidationError("x1_runtime_artifact_drift")


def _string(value: Mapping[str, Any], key: str) -> str:
    observed = value.get(key)
    if not isinstance(observed, str) or not observed:
        raise X1RuntimeValidationError(f"x1_runtime_string:{key}")
    return observed


def _strict_nonnegative(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise X1RuntimeValidationError(f"x1_runtime_integer:{field}")
    return value


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise X1RuntimeValidationError(f"x1_runtime_numeric:{field}")
    observed = float(value)
    if not math.isfinite(observed):
        raise X1RuntimeValidationError(f"x1_runtime_finite:{field}")
    return observed


def _positive(value: Any, field: str) -> float:
    observed = _finite(value, field)
    if observed <= 0:
        raise X1RuntimeValidationError(f"x1_runtime_positive:{field}")
    return observed
