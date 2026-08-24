from __future__ import annotations

from pathlib import Path

import pytest

from evm.scale_validation.s7_evidence import (
    S7EvidenceValidationError,
    _ready_identity_projection,
    asset_contract_projection,
    profile_projection_by_identity,
    project_profile,
    token_f1,
)
from evm.scale_validation.s7_runtime import S7RuntimeConfig


ROOT = Path(__file__).resolve().parents[1]


def config() -> S7RuntimeConfig:
    return S7RuntimeConfig.from_path(ROOT / "configs/s7_family_admission.toml")


def image_profile() -> dict:
    requests = []
    for index in range(6):
        requests.append(
            {
                "request_id": f"request-{index}",
                "request_class": "short",
                "expected": "normal",
                "outcome": "completed",
                "status_code": 200,
                "arrived_offset_seconds": index * 0.01,
                "finished_offset_seconds": 0.1 + index * 0.01,
                "latency_seconds": 0.1,
                "trace_id_sent": f"{index:032x}",
                "trace_id_observed": f"{index:032x}",
                "oom": False,
                "response": {
                    "prediction": "normal",
                    "confidence": 0.9,
                    "operational_metrics": {
                        "request_bytes": 80,
                        "image_bytes": 1000,
                        "image_pixels": 50176,
                        "queue_wait_seconds": 0.01,
                        "decode_seconds": 0.01,
                        "preprocess_seconds": 0.01,
                        "inference_seconds": 0.02,
                        "peak_vram_bytes": 4096,
                    },
                },
            }
        )
    return {
        "profile_id": "image-small",
        "family": "image",
        "repetition": 1,
        "seed_applied": True,
        "measurement_seconds": 1.0,
        "requests": requests,
        "resource_samples": [
            {
                "gpu_used_memory_bytes": 4096,
                "gpu_utilization_percent": 10,
                "gpu_temperature_celsius": 40,
            }
        ],
        "final_admission": {
            "active_requests": 0,
            "queue_depth": 0,
            "reserved": {"request_bytes": 0},
        },
        "prometheus_up": True,
        "lease_identity_exact": True,
        "cleanup_passed": True,
    }


def test_project_profile_recomputes_quality_latency_and_trace() -> None:
    errors: list[str] = []
    projected = project_profile(image_profile(), config=config(), errors=errors)

    assert errors == []
    assert projected["quality"] == {"binary_accuracy": 1.0, "mean_confidence": 0.9}
    assert projected["trace_complete"] is True
    assert projected["metric_schema"]["generation"] == []
    assert "generation" not in projected


def test_over_limit_rejection_is_not_admitted_starvation() -> None:
    payload = image_profile()
    payload["profile_id"] = "image-over-limit"
    for item in payload["requests"]:
        item["request_class"] = "long"
        item["outcome"] = "rejected"
        item["status_code"] = 413
        item["response"] = {}
        item.pop("trace_id_observed")
    errors: list[str] = []

    projected = project_profile(payload, config=config(), errors=errors)

    assert errors == []
    assert projected["pre_admission_rejection_count"] == 6
    assert projected["admitted_starvation_count"] == 0
    assert projected["long_request_noncompletion_count"] == 6


def test_project_profile_fails_closed_on_image_token_metric() -> None:
    payload = image_profile()
    payload["requests"][0]["response"]["operational_metrics"]["input_tokens"] = 4
    errors: list[str] = []

    project_profile(payload, config=config(), errors=errors)

    assert any("image_token_metric_present" in error for error in errors)
    assert any("unsupported_operational" in error for error in errors)


def test_project_profile_fails_closed_on_cost_class_mismatch() -> None:
    payload = image_profile()
    payload["requests"][0]["response"]["operational_metrics"]["image_pixels"] = 1_502_280
    errors: list[str] = []

    project_profile(payload, config=config(), errors=errors)

    assert any("request_class_cost_mismatch" in error for error in errors)


def test_project_profile_rejects_nan() -> None:
    payload = image_profile()
    payload["requests"][0]["latency_seconds"] = float("nan")

    with pytest.raises(S7EvidenceValidationError, match="metric_invalid"):
        project_profile(payload, config=config(), errors=[])


def test_token_f1_is_deterministic() -> None:
    assert token_f1("the third daughter is alice", "the third daughter is alice") == 1.0
    assert token_f1("", "expected") == 0.0


def test_profile_projection_identity_is_order_independent() -> None:
    left = [
        {"profile_id": "image-small", "repetition": 1, "completed": 6},
        {"profile_id": "llm-short", "repetition": 1, "completed": 6},
    ]
    errors: list[str] = []

    left_projection = profile_projection_by_identity(left, errors=errors, label="left")
    right_projection = profile_projection_by_identity(
        list(reversed(left)),
        errors=errors,
        label="right",
    )

    assert errors == []
    assert left_projection == right_projection


def test_profile_projection_identity_rejects_duplicate_repetition() -> None:
    duplicate = [
        {"profile_id": "image-small", "repetition": 1},
        {"profile_id": "image-small", "repetition": 1},
    ]
    errors: list[str] = []

    profile_projection_by_identity(duplicate, errors=errors, label="public")

    assert errors == ["public_profile_duplicate:image-small:r01"]


def test_llm_ready_projection_requires_observed_4bit_runtime() -> None:
    asset = {
        "model_repository": "repository",
        "model_revision": "revision",
        "adapter_sha256": "a" * 64,
        "data_identity_sha256": "b" * 64,
        "model_source_commit": "c" * 40,
        "quantization": "int4_nf4",
    }
    ready = {
        "status": "ready",
        "model_family": "llm",
        "model_repository": "repository",
        "model_revision": "revision",
        "model_artifact_sha256": "a" * 64,
        "data_identity_sha256": "b" * 64,
        "model_source_commit": "c" * 40,
        "runtime_source_commit": "d" * 40,
        "quantization": "int4_nf4",
        "quantization_runtime": {
            "requested": "int4_nf4",
            "observed": "int4_nf4",
            "loaded_in_4bit": True,
            "linear_4bit_module_count": 12,
        },
        "runtime": {"cuda_available": True, "torch": "2", "cuda": "12"},
    }

    projected = _ready_identity_projection("llm", ready, asset, "d" * 40)
    assert projected["loaded_in_4bit"] is True

    ready["quantization_runtime"]["loaded_in_4bit"] = False
    with pytest.raises(ValueError, match="4bit"):
        _ready_identity_projection("llm", ready, asset, "d" * 40)


def test_asset_contract_binds_scienceqa_noncommercial_license() -> None:
    projected = asset_contract_projection(
        config=config(),
        git_root=None,
        revision="a" * 40,
    )

    assert projected["image"]["dataset"]["license_id"] == "CC-BY-4.0"
    assert projected["vlm"]["dataset"]["license_id"] == "CC-BY-NC-SA-4.0"
    assert projected["vlm"]["noncommercial_restriction"] is True
    assert projected["llm"]["dataset"]["license_id"] == "CC-BY-SA-3.0"
