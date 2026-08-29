from __future__ import annotations

import copy
from pathlib import Path

import pytest

from evm.scale_validation.x1_contract import MODEL_IDS, X1Contract
from evm.scale_validation.x1_runtime import (
    X1RuntimeValidationError,
    build_runtime_manifest,
    select_batching_profiles,
    validate_triton_runtime_config,
    validate_q0_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops")


def contract() -> X1Contract:
    return X1Contract.from_path(
        ROOT / "configs/s8_v4_x1_heterogeneous_v1.toml",
        source_root=ROOT,
        data_root=DATA_ROOT,
    )


def artifact_manifest() -> dict[str, object]:
    models = {
        model_id: {
            "artifact_sha256": chr(97 + index) * 64,
            "feature_count": 39 if model_id == "criteo_dlrm_lite" else 28,
        }
        for index, model_id in enumerate(MODEL_IDS)
    }
    repositories = {}
    for profile in ("disabled", "enabled-4-8-2ms", "enabled-8-16-10ms"):
        entries = []
        for index, model_id in enumerate(MODEL_IDS):
            entries.extend(
                [
                    {"path": f"{model_id}/config.pbtxt", "sha256": chr(101 + index) * 64},
                    {"path": f"{model_id}/1/model.pt", "sha256": chr(97 + index) * 64},
                ]
            )
        repositories[profile] = {"entries": entries, "aggregate_sha256": profile * 4}
    return {
        "models": models,
        "repositories": repositories,
        "artifact_identity_sha256": "f" * 64,
    }


def test_x1_runtime_manifest_binds_profiles_models_and_inference_lease() -> None:
    manifest = build_runtime_manifest(
        contract(),
        artifact_manifest=artifact_manifest(),
        artifact_manifest_file_sha256="9" * 64,
        artifact_manifest_container_path="/mnt/evm-data/x1/x1-artifact-manifest.json",
        active_profile="disabled",
        lease_run_id="s8-v4-x1-inference-unit",
        lease_id="lease-unit",
        fencing_token_sha256="a" * 64,
        source_revision="b" * 40,
    )
    assert set(manifest["models"]) == set(MODEL_IDS)
    assert manifest["manifest_sha256"] == "9" * 64
    assert manifest["lease"]["scenario_id"] == "X1"
    assert manifest["profiles"]["enabled-4-8-2ms"]["models"][MODEL_IDS[0]]["max_batch_size"] == 16


def test_x1_runtime_manifest_rejects_noncanonical_artifact_file_sha() -> None:
    with pytest.raises(X1RuntimeValidationError, match="x1_runtime_artifact_manifest_sha256"):
        build_runtime_manifest(
            contract(),
            artifact_manifest=artifact_manifest(),
            artifact_manifest_file_sha256="not-a-sha",
            artifact_manifest_container_path="/mnt/evm-data/x1/x1-artifact-manifest.json",
            active_profile="disabled",
            lease_run_id="s8-v4-x1-inference-unit",
            lease_id="lease-unit",
            fencing_token_sha256="a" * 64,
            source_revision="b" * 40,
        )


def q0_bundle() -> dict[str, object]:
    artifacts = artifact_manifest()
    return {
        "schema_version": "evm.s8_v4.x1_q0.v1",
        "models": [
            {
                "model_id": model_id,
                "model_version": "1",
                "artifact_sha256": artifacts["models"][model_id]["artifact_sha256"],
                "isolated_request_count": 64,
                "correct_count": 64,
                "failed_count": 0,
                "cpu_fallback_detected": False,
                "actual_cuda_activity": True,
                "triton_delta": {
                    "success_count": 64,
                    "inference_count": 64,
                    "execution_count": 64,
                    "compute_duration_us": 640,
                },
                "gpu_uuid": "GPU-4eea4bfc-f15e-bd25-c1b8-ed53ade9ad1d",
                "gpu_name": "NVIDIA GeForce RTX 4080 SUPER",
                "config_sha256": chr(101 + index) * 64,
                "config_bytes_sha256": chr(101 + index) * 64,
                "repository_index_exact": True,
            }
            for index, model_id in enumerate(MODEL_IDS)
        ],
    }


def test_x1_q0_recomputes_exact_cuda_and_per_model_counts() -> None:
    assert validate_q0_bundle(q0_bundle(), contract(), artifact_manifest()) == {
        "models_passed": 4,
        "requests_passed": 256,
        "silent_cpu_fallback": 0,
        "actual_cuda": True,
    }


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("isolated_request_count", 63, "x1_q0_outcome"),
        ("cpu_fallback_detected", True, "x1_q0_outcome"),
        ("repository_index_exact", False, "x1_q0_repository_index"),
    ],
)
def test_x1_q0_rejects_count_fallback_or_repository_mutation(
    field: str, value: object, reason: str
) -> None:
    payload = q0_bundle()
    payload["models"][0][field] = value
    with pytest.raises(X1RuntimeValidationError, match=reason):
        validate_q0_bundle(payload, contract(), artifact_manifest())


def batching_records() -> list[dict[str, object]]:
    return [
        {
            "model_id": model_id,
            "batch_candidate": candidate,
            "repetition": repetition,
            "safe_service_rps": float(100 + candidate_index * 10),
            "guardrails_passed": True,
        }
        for model_id in MODEL_IDS
        for candidate_index, candidate in enumerate(("enabled-4-8-2ms", "enabled-8-16-10ms"))
        for repetition in (1, 2, 3)
    ]


def test_x1_batching_selection_requires_exact_24_noncredit_repetitions() -> None:
    selected = select_batching_profiles(batching_records())
    assert set(selected.values()) == {"enabled-8-16-10ms"}
    incomplete = copy.deepcopy(batching_records())
    incomplete.pop()
    with pytest.raises(X1RuntimeValidationError, match="x1_batch_calibration_repetition_set"):
        select_batching_profiles(incomplete)


def triton_config() -> dict[str, object]:
    model_id = MODEL_IDS[0]
    return {
        "name": model_id,
        "backend": "pytorch",
        "max_batch_size": 16,
        "version_policy": {"specific": {"versions": [1]}},
        "instance_group": [{"name": f"{model_id}_0", "kind": "KIND_GPU", "count": 1, "gpus": [0]}],
        "input": [
            {
                "name": "INPUT__0",
                "data_type": "TYPE_FP32",
                "dims": [28],
                "is_shape_tensor": False,
                "allow_ragged_batch": False,
                "optional": False,
            }
        ],
        "output": [
            {
                "name": "OUTPUT__0",
                "data_type": "TYPE_FP32",
                "dims": [1],
                "label_filename": "",
                "is_shape_tensor": False,
            }
        ],
        "dynamic_batching": {
            "preferred_batch_size": [4, 8],
            "max_queue_delay_microseconds": 2000,
            "preserve_ordering": True,
            "priority_levels": 0,
        },
    }


def batch_identity() -> dict[str, object]:
    return {
        "feature_count": 28,
        "max_batch_size": 16,
        "preferred_batch_size": [4, 8],
        "max_queue_delay_microseconds": 2000,
    }


def test_x1_triton_config_shared_gate_accepts_exact_gpu_batch_profile() -> None:
    validate_triton_runtime_config(
        triton_config(), model_id=MODEL_IDS[0], identity=batch_identity()
    )


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (
            lambda value: value["instance_group"][0].__setitem__("kind", "KIND_CPU"),
            "x1_triton_config_instance_group",
        ),
        (
            lambda value: value["dynamic_batching"].__setitem__("preferred_batch_size", [999]),
            "x1_triton_config_batching",
        ),
        (
            lambda value: value.__setitem__("response_cache", {"enable": True}),
            "x1_triton_config_response_cache",
        ),
        (
            lambda value: value.__setitem__("version_policy", {"specific": {"versions": ["1"]}}),
            "x1_triton_config_identity",
        ),
    ],
)
def test_x1_triton_config_shared_gate_rejects_runtime_mutation(
    mutator: object, reason: str
) -> None:
    payload = triton_config()
    mutator(payload)  # type: ignore[operator]
    with pytest.raises(X1RuntimeValidationError, match=reason):
        validate_triton_runtime_config(payload, model_id=MODEL_IDS[0], identity=batch_identity())
