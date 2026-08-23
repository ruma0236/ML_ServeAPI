from __future__ import annotations

from pathlib import Path

import pytest

from scripts.dev.run_s6_rolling_handoff_experiment import (
    build_private_index,
    canonical_write,
)
from evm.scale_validation.s6_evidence import (
    S6EvidenceValidationError,
    project_api_result,
    project_gpu_result,
    validate_s6_experiment,
)
from evm.scale_validation.s6_runtime import S6RuntimeConfig, analyze_s6_results, file_sha256


SOURCE_CONFIG = Path("configs/s6_rolling_handoff.toml")


def _config(tmp_path: Path) -> S6RuntimeConfig:
    source_paths = (
        "artifacts/w7/lifecycle_runs/lifecycle-20260805T104249-7d184e13/readiness.json",
        "artifacts/w7/lifecycle_runs/lifecycle-20260805T104249-7d184e13/model/lifecycle-20260805T104249-7d184e13/efficientnet-b0-profile-standard-b0-manual-tuning-v11/candidate_summary.json",
        "artifacts/w7/lifecycle_runs/lifecycle-20260805T104249-7d184e13/validation/release-submission.json",
        "artifacts/w7/lifecycle_runs/lifecycle-20260805T104249-7d184e13/model/lifecycle-20260805T104249-7d184e13/efficientnet-b0-profile-standard-b0-manual-tuning-v11/model.pt",
    )
    replacements = ("r.json", "c.json", "s.json", "m.pt")
    payload = SOURCE_CONFIG.read_text(encoding="utf-8")
    for source, replacement in zip(source_paths, replacements, strict=True):
        payload = payload.replace(source, replacement)
    payload = payload.replace("target_requests_per_second = 25.0", "target_requests_per_second = 1.0")
    payload = payload.replace("measurement_seconds = 20.0", "measurement_seconds = 2.0")
    payload = payload.replace("rollout_offset_seconds = 5.0", "rollout_offset_seconds = 1.0")
    local_config = tmp_path / "s6.toml"
    local_config.write_text(payload, encoding="utf-8")
    for relative in replacements:
        (tmp_path / relative).write_bytes(b"fixture")
    return S6RuntimeConfig.from_path(local_config, data_root=tmp_path)


def _api_raw(repetition: int) -> dict[str, object]:
    observations = []
    for index, latency in enumerate((100.0, 110.0)):
        identity = f"r{repetition}-request-{index}"
        observations.append(
            {
                "logical_request_id": identity,
                "success": True,
                "logical_latency_ms": latency,
                "trace_identity_matches": True,
                "attempts": [{"status": 200}],
            }
        )
    database = {
        "accepted_ids": [item["logical_request_id"] for item in observations],
        "terminal_ids": [item["logical_request_id"] for item in observations],
        "effect_ids": [f"effect-{repetition}-{index}" for index in range(2)],
        "duplicate_effects": 0,
        "drain_events": [
            {"drain_completed": True, "drain_elapsed_seconds": 0.1},
            {"drain_completed": True, "drain_elapsed_seconds": 0.2},
        ],
    }
    return {
        "schema_version": "evm.s6_api_rolling_repetition.v1",
        "repetition": repetition,
        "logical_requests": 2,
        "attempts": 2,
        "client_success": 2,
        "database_accepted": 2,
        "database_terminal": 2,
        "accepted_loss": 0,
        "client_success_without_acceptance": 0,
        "duplicate_effects": 0,
        "error_rate": 0.0,
        "retry_amplification": 1.0,
        "measurement_seconds": 2.0,
        "service_rps": 1.0,
        "mean_ms": 105.0,
        "p50_ms": 105.0,
        "p95_ms": 109.5,
        "p99_ms": 109.9,
        "maximum_ms": 110.0,
        "trace_identity_matches": 2,
        "trace_expected": 1,
        "trace_observed": 1,
        "trace_complete": True,
        "trace_summary": {"expected": 1, "observed": 1, "complete": True},
        "drain_event_count": 2,
        "maximum_drain_seconds": 0.2,
        "rollout_seconds": 8.0,
        "prometheus_up": True,
        "before": {"release_ids": ["old"]},
        "after": {"release_ids": ["new"]},
        "cleanup_passed": True,
        "database": database,
        "observations": observations,
    }


def _gpu_raw(repetition: int, phase: str) -> dict[str, object]:
    samples = [
        {"device": "cuda", "http_elapsed_ms": float(index + 1)}
        for index in range(30)
    ]
    return {
        "schema_version": "evm.s6_gpu_handoff_repetition.v1",
        "phase": phase,
        "repetition": repetition,
        "status": "passed",
        "candidate_gate_passed": True,
        "approval_consumed_once": True,
        "approval_reuse_rejected": True,
        "candidate_gate": {
            "status": "passed",
            "candidate_id": "candidate",
            "model_sha256": "b" * 64,
        },
        "approval": {"state": "consumed", "single_use": True},
        "zero_owner_overlap": True,
        "owner_timeline": [
            {"owner_count": 1},
            {"owner_count": 0},
            {"owner_count": 1},
            {"owner_count": 0},
            {"owner_count": 1},
        ],
        "target_identity_exact": True,
        "rollback_exact": True,
        "source_identity_before": "a" * 64,
        "source_identity_after": "a" * 64,
        "source_to_target_interruption_seconds": 8.0,
        "target_to_source_interruption_seconds": 7.0,
        "target_p50_ms": 15.5,
        "target_p95_ms": 28.55,
        "target_p99_ms": 29.71,
        "target_inference_count": 30,
        "target_cuda_inference": True,
        "source_cuda_inference_restored": True,
        "prometheus_restored": True,
        "prometheus_health": "up",
        "source_prediction_after": {"device": "cuda"},
        "target_ready": {
            "candidate_id": "candidate",
            "model_sha256": "b" * 64,
            "cuda_available": True,
            "device": "cuda",
        },
        "target_samples": samples,
    }


def _payload(tmp_path: Path) -> tuple[dict[str, object], S6RuntimeConfig, Path]:
    config = _config(tmp_path)
    private_root = tmp_path / "private"
    api = [_api_raw(index) for index in range(1, 4)]
    calibration = _gpu_raw(0, "calibration")
    gpu = [_gpu_raw(index, "acceptance") for index in range(1, 4)]
    for index, item in enumerate(api, start=1):
        canonical_write(private_root / "api" / f"repetition-{index:02d}.json", item)
    canonical_write(private_root / "gpu/calibration/gpu-handoff-result.json", calibration)
    for index, item in enumerate(gpu, start=1):
        canonical_write(
            private_root / f"gpu/repetition-{index:02d}/gpu-handoff-result.json",
            item,
        )
    index = build_private_index(private_root)
    index_path = private_root / "private-evidence-index.json"
    canonical_write(index_path, index)
    errors: list[str] = []
    projected_api = [project_api_result(item, errors=errors) for item in api]
    projected_calibration = project_gpu_result(
        calibration, errors=errors, prefix="calibration"
    )
    projected_gpu = [
        project_gpu_result(item, errors=errors, prefix=f"gpu-{index}")
        for index, item in enumerate(gpu, start=1)
    ]
    assert not errors
    analysis = analyze_s6_results(
        api_repetitions=projected_api,
        gpu_calibration=projected_calibration,
        gpu_repetitions=projected_gpu,
        config=config,
    )
    payload: dict[str, object] = {
        "schema_version": "evm.s6_rolling_handoff_experiment.v1",
        "status": "verified",
        "verdict": "passed",
        "source_identity": {"revision": "a" * 40, "config_sha256": config.sha256},
        "api_repetitions": projected_api,
        "gpu_calibration": projected_calibration,
        "gpu_repetitions": projected_gpu,
        "analysis": analysis,
        "private_evidence": {
            "artifact_count": index["artifact_count"],
            "total_bytes": index["total_bytes"],
            "aggregate_sha256": index["aggregate_sha256"],
            "index_sha256": file_sha256(index_path),
        },
        "claim_boundary": config.claim_boundary,
    }
    return payload, config, private_root


def test_s6_evidence_validator_recomputes_private_projections(tmp_path: Path) -> None:
    payload, config, private_root = _payload(tmp_path)

    validated = validate_s6_experiment(
        payload,
        config=config,
        private_root=private_root,
    )

    assert validated["status"] == "valid"
    assert validated["analysis"]["status"] == "passed"


def test_s6_evidence_validator_rejects_summary_only_pass_mutation(tmp_path: Path) -> None:
    payload, config, private_root = _payload(tmp_path)
    payload["api_repetitions"][0]["accepted_loss"] = 1

    with pytest.raises(S6EvidenceValidationError, match="api_public_projection"):
        validate_s6_experiment(
            payload,
            config=config,
            private_root=private_root,
        )
