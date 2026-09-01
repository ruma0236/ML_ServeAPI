from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from evm.scale_validation.s7_evidence import (
    S7EvidenceValidationError,
    _ready_identity_projection,
    asset_contract_projection,
    profile_projection_by_identity,
    project_profile,
    token_f1,
    validate_s7_runtime_smoke,
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
        item["status_code"] = 422
        item["response"] = {"detail": "image_pixels_exceeded"}
    errors: list[str] = []

    projected = project_profile(payload, config=config(), errors=errors)

    assert errors == []
    assert projected["pre_admission_rejection_count"] == 6
    assert projected["admitted_starvation_count"] == 0
    assert projected["long_request_noncompletion_count"] == 6


def test_project_profile_rejects_admitted_profile_partial_rejection() -> None:
    payload = image_profile()
    request = payload["requests"][0]
    request["outcome"] = "rejected"
    request["status_code"] = 422
    request["response"] = {"detail": "image_pixels_exceeded"}
    errors: list[str] = []

    project_profile(payload, config=config(), errors=errors)

    assert any("admitted_outcome_invariant" in error for error in errors)


def test_project_profile_rejects_over_limit_oom_or_wrong_reason() -> None:
    payload = image_profile()
    payload["profile_id"] = "image-over-limit"
    for item in payload["requests"]:
        item["request_class"] = "long"
        item["outcome"] = "rejected"
        item["status_code"] = 422
        item["response"] = {"detail": "image_pixels_exceeded"}
    payload["requests"][0]["oom"] = True
    payload["requests"][1]["response"]["detail"] = "generic_rejection"
    errors: list[str] = []

    projected = project_profile(payload, config=config(), errors=errors)

    assert any("over_limit_outcome_invariant" in error for error in errors)
    assert projected["pre_admission_rejection_count"] == 4
    assert projected["oom_count"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda profiles: next(
            item
            for item in profiles
            if item["profile_id"] == "image-small" and item["repetition"] == 1
        ).update({"completed": 5, "rejected": 1, "pre_admission_rejection_count": 1}),
        lambda profiles: next(
            item
            for item in profiles
            if item["profile_id"] == "image-over-limit" and item["repetition"] == 1
        ).update({"oom_count": 1}),
    ],
)
def test_s7_analysis_rejects_outcome_summary_mutations(mutation) -> None:
    from evm.scale_validation.s7_runtime import analyze_s7_profiles

    public = json.loads(
        (ROOT / "docs/status/evidence/s7-auxiliary-admission-reprojection.json").read_text(
            encoding="utf-8"
        )
    )
    profiles = copy.deepcopy(public["profiles"])
    mutation(profiles)

    analysis = analyze_s7_profiles(profiles, config())

    assert analysis["runtime_verdict"] == "failed"
    assert not all(analysis["acceptance"].values())


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


def test_legacy_current_s7_smoke_is_explicit_remediation_without_live_rehash(
    tmp_path: Path,
) -> None:
    smoke = json.loads(
        (ROOT / "docs/status/evidence/s7-current-revision-cuda-smoke.json").read_text(
            encoding="utf-8"
        )
    )
    result = validate_s7_runtime_smoke(
        smoke,
        config=config(),
        private_root=tmp_path / "private-root-is-intentionally-absent",
        data_root=tmp_path / "live-data-root-is-intentionally-absent",
    )

    assert result == {
        "status": "remediation_required",
        "classification": "legacy_snapshot_absent",
        "acceptance_credit": False,
        "suite_id": "20260829T160726Z-1f631c19",
        "legacy_schema_version": "evm.s7_current_revision_cuda_smoke.v2",
        "live_manifest_rehashed": False,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.__setitem__("acceptance_credit", True),
        lambda value: value["profiles"][0].__setitem__("completed", 5),
        lambda value: value["source_identity"]["runtime_asset_overrides"]["image"].__setitem__(
            "observed_manifest_sha256", "0" * 64
        ),
    ],
)
def test_legacy_current_s7_smoke_cannot_be_retroactively_promoted(mutation, tmp_path: Path) -> None:
    smoke = json.loads(
        (ROOT / "docs/status/evidence/s7-current-revision-cuda-smoke.json").read_text(
            encoding="utf-8"
        )
    )
    mutation(smoke)

    result = validate_s7_runtime_smoke(
        smoke,
        config=config(),
        private_root=tmp_path / "missing-private-root",
        data_root=tmp_path / "missing-data-root",
    )
    assert result["status"] == "remediation_required"
    assert result["classification"] == "legacy_snapshot_absent"
    assert result["acceptance_credit"] is False
    assert result["live_manifest_rehashed"] is False
