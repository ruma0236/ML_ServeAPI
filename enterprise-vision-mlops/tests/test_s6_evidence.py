from __future__ import annotations

import hashlib
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
    validate_s6_closure,
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
        trace_id = f"{repetition * 100 + index:032x}"
        observations.append(
            {
                "logical_request_id": identity,
                "success": True,
                "logical_latency_ms": latency,
                "trace_identity_matches": True,
                "trace_id": trace_id,
                "sampled": index == 0,
                "attempts": [{"status": 200, "trace_header": trace_id}],
            }
        )
    database = {
        "accepted_ids": [item["logical_request_id"] for item in observations],
        "terminal_ids": [item["logical_request_id"] for item in observations],
        "effect_ids": [f"effect-{repetition}-{index}" for index in range(2)],
        "duplicate_effects": 0,
        "drain_events": [
            {
                "instance_id": "pod-a",
                "drain_completed": True,
                "drain_elapsed_seconds": 0.1,
                "started_at": "2026-08-24T00:00:00Z",
                "completed_at": "2026-08-24T00:00:00.100000Z",
            },
            {
                "instance_id": "pod-b",
                "drain_completed": True,
                "drain_elapsed_seconds": 0.2,
                "started_at": "2026-08-24T00:00:01Z",
                "completed_at": "2026-08-24T00:00:01.200000Z",
            },
        ],
        "expected_drain_instance_ids": ["pod-a", "pod-b"],
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
        "trace_summary": {
            "expected": 1,
            "observed": 1,
            "missing": 0,
            "complete": True,
            "raw_tail_bytes": 100,
            "raw_tail_sha256": "f" * 64,
        },
        "drain_event_count": 2,
        "maximum_drain_seconds": 0.2,
        "rollout_seconds": 8.0,
        "prometheus_up": True,
        "prometheus_recovery_seconds": 0.0,
        "before": {"release_ids": ["old"]},
        "after": {"release_ids": ["new"]},
        "cleanup_passed": True,
        "database": database,
        "observations": observations,
    }


def _gpu_raw(repetition: int, phase: str) -> dict[str, object]:
    samples = [
        {
            "device": "cuda",
            "http_elapsed_ms": float(index + 1),
            "observed_monotonic": float(110 + index),
        }
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
        "source_to_target_interruption_seconds": 10.0,
        "target_to_source_interruption_seconds": 11.0,
        "target_p50_ms": 15.5,
        "target_p95_ms": 28.55,
        "target_p99_ms": 29.71,
        "target_inference_count": 30,
        "target_cuda_inference": True,
        "source_cuda_inference_restored": True,
        "prometheus_restored": True,
        "prometheus_health": "up",
        "source_prediction_before": {"device": "cuda", "observed_monotonic": 100.0},
        "source_prediction_after": {"device": "cuda", "observed_monotonic": 150.0},
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


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda value: value["trace_summary"].__setitem__("complete", False),
            "trace_complete",
        ),
        (
            lambda value: value["database"]["drain_events"][0].__setitem__(
                "instance_id", "unexpected-pod"
            ),
            "drain_identity_closure",
        ),
    ],
)
def test_s6_api_projection_fails_closed_on_trace_or_drain_identity_mutation(
    mutation, expected: str
) -> None:
    payload = _api_raw(1)
    mutation(payload)
    errors: list[str] = []

    project_api_result(payload, errors=errors)

    assert any(expected in error for error in errors)


def test_s6_api_projection_rejects_raw_trace_header_mismatch() -> None:
    payload = _api_raw(1)
    payload["observations"][0]["attempts"][0]["trace_header"] = "0" * 32
    errors: list[str] = []

    project_api_result(payload, errors=errors)

    assert any("trace_identity_matches" in error for error in errors)
    assert any("trace_observed" in error or "trace_complete" in error for error in errors)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda value: value["owner_timeline"][0].__setitem__("owner_count", 2),
            "owner_overlap",
        ),
        (
            lambda value: value.__setitem__("source_identity_after", "z" * 64),
            "rollback_identity",
        ),
        (
            lambda value: value.__setitem__("approval_reuse_rejected", False),
            "approval",
        ),
    ],
)
def test_s6_gpu_projection_fails_closed_on_handoff_identity_mutation(
    mutation, expected: str
) -> None:
    payload = _gpu_raw(1, "acceptance")
    mutation(payload)
    errors: list[str] = []

    project_gpu_result(payload, errors=errors, prefix="gpu")

    assert any(expected in error for error in errors)


def test_s6_gpu_projection_rejects_summary_interruption_mismatch() -> None:
    payload = _gpu_raw(1, "acceptance")
    payload["source_to_target_interruption_seconds"] = 1.0
    payload["target_to_source_interruption_seconds"] = 1.0
    errors: list[str] = []

    projected = project_gpu_result(payload, errors=errors, prefix="gpu")

    assert any("source_to_target_interruption_seconds" in error for error in errors)
    assert any("target_to_source_interruption_seconds" in error for error in errors)
    assert projected["source_to_target_interruption_seconds"] == 10.0
    assert projected["target_to_source_interruption_seconds"] == 11.0


def _closure(payload: dict[str, object], config: S6RuntimeConfig) -> dict[str, object]:
    return {
        "schema_version": "evm.s6_rolling_handoff_closure.v1",
        "status": "verified",
        "verdict": "passed",
        "claim_boundary": config.claim_boundary,
        "source_identity": {
            "experiment_commit": "a" * 40,
            "validator_revision": "a" * 40,
        },
        "final_runtime_evidence": {
            "experiment_git_blob_sha256": hashlib.sha256(
                (canonical_json(payload) + "\n").encode("utf-8")
            ).hexdigest(),
            "api_repetitions": 3,
            "gpu_repetitions": 3,
            "acceptance": payload["analysis"]["acceptance"],
        },
        "failed_attempts_and_rca": [
            {"attempt_id": "S6-ATTEMPT-01", "acceptance_credit": False},
            {"attempt_id": "S6-ATTEMPT-02", "acceptance_credit": False},
        ],
        "regression": {
            name: {"status": "passed"}
            for name in (
                "focused_s6",
                "full_python_real_postgresql",
                "lifecycle_host_e2e",
                "control_panel",
                "frontend_production_build",
                "s0_s5_regression",
                "current_revision_runtime_smoke",
            )
        },
        "cleanup": {
            "runtime_cleanup_passed": True,
            "private_inventory_rehash_passed": True,
            "git_blob_validation_passed": True,
            "source_serving_ready": True,
            "target_scaled_zero": True,
            "s6_isolated_resources_removed": True,
            "queues_and_leases_zero": True,
            "prometheus_baseline_healthy": True,
        },
    }


def canonical_json(value: object) -> str:
    import json

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_s6_closure_recomputes_acceptance_and_requires_cleanup(tmp_path: Path) -> None:
    payload, config, private_root = _payload(tmp_path)
    closure = _closure(payload, config)

    validated = validate_s6_closure(
        closure,
        experiment=payload,
        experiment_sha256=closure["final_runtime_evidence"][
            "experiment_git_blob_sha256"
        ],
        config=config,
        private_root=private_root,
    )

    assert validated["status"] == "valid"
    assert validated["api_repetitions"] == 3
    assert validated["gpu_repetitions"] == 3

    closure["cleanup"]["source_serving_ready"] = False
    with pytest.raises(S6EvidenceValidationError, match="closure_cleanup"):
        validate_s6_closure(
            closure,
            experiment=payload,
            experiment_sha256=closure["final_runtime_evidence"][
                "experiment_git_blob_sha256"
            ],
            config=config,
            private_root=private_root,
        )
