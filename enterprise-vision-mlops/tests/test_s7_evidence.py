from __future__ import annotations

from pathlib import Path

import pytest

from evm.scale_validation.s7_evidence import (
    S7EvidenceValidationError,
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
