from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from evm.scale_validation.s6bm_runtime import (
    S6BMConfig,
    S6BMRuntimeError,
    SUCCESS_PHASES,
    analyze_attempts,
    project_fault_attempt,
    project_success_attempt,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/s8_v4_s6bm_blue_green_v1.toml"


def identities() -> dict[str, object]:
    config = S6BMConfig.from_path(CONFIG)
    return {
        "image_digest": config.image_digest,
        "repository_sha256": config.repository_sha256,
        "blue": {
            "model_name": config.blue.model_name,
            "model_version": config.blue.model_version,
            "artifact_sha256": config.blue.artifact_sha256,
            "config_sha256": config.blue.config_sha256,
        },
        "green": {
            "model_name": config.green.model_name,
            "model_version": config.green.model_version,
            "artifact_sha256": config.green.artifact_sha256,
            "config_sha256": config.green.config_sha256,
        },
        "lease": {
            "run_id": "s8-v4-s6bm-unit-test",
            "scenario_id": "S6B-M",
            "model_family": "tabular",
            "purpose": "scale_validation_inference",
            "owner_exact": True,
        },
    }


def success_attempt(repetition: int = 1) -> dict[str, object]:
    config = S6BMConfig.from_path(CONFIG)
    records = []
    for index in range(1000):
        role = "blue" if index < 100 else "green"
        model = config.blue if role == "blue" else config.green
        records.append(
            {
                "run_id": "s8-v4-s6bm-unit-test",
                "attempt_id": f"success-{repetition}",
                "request_id": f"request-{index:04d}",
                "trace_id": f"{index + 1:032x}",
                "effect_id": hashlib.sha256(
                    f"success-{repetition}:request-{index:04d}".encode("ascii")
                ).hexdigest(),
                "offered_traceparent": (f"00-{index + 1:032x}-{index + 2:016x}-01"),
                "offered_identity": {
                    "model_role": role,
                    "model_name": model.model_name,
                    "model_version": model.model_version,
                    "artifact_sha256": model.artifact_sha256,
                },
                "status_code": 200,
                "outcome": "completed",
                "model_role": role,
                "model_name": model.model_name,
                "model_version": model.model_version,
                "artifact_sha256": model.artifact_sha256,
                "output": list(model.expected_output),
                "elapsed_ms": 10.0,
                "completed_monotonic": 100.0 + index * 0.001,
            }
        )
    return {
        "attempt_id": f"success-{repetition}",
        "profile": "successful_transition",
        "repetition": repetition,
        "identities": identities(),
        "phase_timeline": [
            {"phase": phase, "monotonic_seconds": float(index + 1)}
            for index, phase in enumerate(SUCCESS_PHASES)
        ],
        "request_records": records,
        "requests": {
            "logical": 1000,
            "accepted": 1000,
            "terminal": 1000,
            "lost": 0,
            "duplicate_effect": 0,
            "wrong_version": 0,
            "transport_failure": 0,
            "http_5xx": 0,
        },
        "idempotent_replay": {
            "request_id": "request-0000",
            "replayed": True,
            "unique_count_before": 100,
            "unique_count_after": 100,
            "record": {**records[0], "replayed": True},
        },
        "illegal_owner_overlap": 0,
        "owner_samples": [{"owner_exact": True}],
        "trace_complete": 1000,
        "blue_in_flight_before_unload": 0,
        "green_in_flight_before_unload": 0,
        "rollback_exact_blue": True,
        "latency": {
            "p95_ms": 10.0,
            "p99_ms": 10.0,
            "max_inter_completion_gap_ms": 1.0,
        },
        "transition_seconds": 2.0,
        "rollback_seconds": 2.0,
        "peak_vram_mib": 1024.0,
        "physical_model_state": {
            "green_loaded_ready": True,
            "blue_unloaded_not_ready": True,
            "blue_reloaded_ready": True,
            "green_unloaded_not_ready": True,
            "blue_final_ready": True,
        },
        "telemetry": {
            "api_target_up": True,
            "triton_target_up": True,
            "trace_correlation_complete": True,
            "metric_delta_complete": True,
        },
        "cleanup": {
            "blue_only": True,
            "green_unloaded": True,
            "queue_zero": True,
            "lease_owner_exact": True,
        },
    }


def fault_attempt(profile: str, repetition: int = 1) -> dict[str, object]:
    codes = {
        "wrong_digest": "green_digest_mismatch",
        "green_load_failure": "triton_model_control_failed",
        "green_readiness_failure": "green_readiness_rejected",
        "green_canary_failure": "green_canary_rejected",
        "vram_preflight_rejection": "vram_preflight_rejected",
    }
    state = {
        "phase": "blue_only",
        "route_weights": {"blue": 100, "green": 0},
        "loaded_roles": ["blue"],
    }
    observation: dict[str, object] = {"injection_observed": True}
    if profile == "vram_preflight_rejection":
        observation.update(free_vram_mib=1000.0, required_vram_mib=1512.0)
    if profile == "green_canary_failure":
        observation["canary_mismatch"] = True
    return {
        "attempt_id": f"{profile}-{repetition}",
        "profile": profile,
        "repetition": repetition,
        "guard_rejected": True,
        "identities": identities(),
        "guard_code": codes[profile],
        "rejection": {
            "request_sent": True,
            "status_code": 409,
            "guard_code": codes[profile],
        },
        "before_state": state,
        "final_state": state,
        "fault_observation": observation,
        "route_unchanged_blue": True,
        "green_effect_count": 0,
        "route_switch_count": 0,
        "http_5xx": 0,
        "orphan_count": 0,
        "blue_health_after": True,
        "telemetry": {"api_target_up": True, "triton_target_up": True},
        "cleanup": {"blue_only": True, "green_unloaded": True},
    }


def test_s6bm_config_freezes_canonical_matrix_and_distinct_models() -> None:
    config = S6BMConfig.from_path(CONFIG)
    assert config.procedure["successful_transition_repetitions"] == 3
    assert config.procedure["logical_requests_per_transition"] == 1000
    assert config.procedure["canary_weight_percent"] == 10
    assert config.blue.artifact_sha256 != config.green.artifact_sha256


def test_s6bm_success_projection_rejects_loss_identity_and_cleanup() -> None:
    config = S6BMConfig.from_path(CONFIG)
    assert project_success_attempt(success_attempt(), config)["passed"] is True
    mutations = [
        ("loss", lambda raw: raw["requests"].update(lost=1)),  # type: ignore[union-attr]
        ("phase", lambda raw: raw["phase_timeline"].pop()),  # type: ignore[union-attr]
        ("owner", lambda raw: raw.update(illegal_owner_overlap=1)),
        ("trace", lambda raw: raw.update(trace_complete=999)),
        ("drain", lambda raw: raw.update(blue_in_flight_before_unload=1)),
        ("rollback", lambda raw: raw.update(rollback_exact_blue=False)),
        (
            "replay",
            lambda raw: raw["idempotent_replay"]["record"].update(artifact_sha256="f" * 64),
        ),
        ("cleanup", lambda raw: raw["cleanup"].update(queue_zero=False)),  # type: ignore[union-attr]
    ]
    for _name, mutate in mutations:
        raw = copy.deepcopy(success_attempt())
        mutate(raw)
        with pytest.raises(S6BMRuntimeError):
            project_success_attempt(raw, config)


def test_s6bm_success_projection_reports_transport_failure_before_trace_gap() -> None:
    config = S6BMConfig.from_path(CONFIG)
    raw = success_attempt()
    raw["request_records"][0] = {  # type: ignore[index]
        "request_id": "request-0000",
        "status_code": 0,
        "outcome": "transport_failure",
    }

    with pytest.raises(S6BMRuntimeError, match="s6bm_request_not_completed"):
        project_success_attempt(raw, config)


def test_s6bm_fault_projection_rejects_fail_open_mutations() -> None:
    config = S6BMConfig.from_path(CONFIG)
    assert project_fault_attempt(fault_attempt("wrong_digest"), config, "wrong_digest")
    for field, value in (
        ("guard_rejected", False),
        ("route_unchanged_blue", False),
        ("green_effect_count", 1),
        ("route_switch_count", 1),
        ("http_5xx", 1),
        ("orphan_count", 1),
        ("blue_health_after", False),
    ):
        raw = fault_attempt("wrong_digest")
        raw[field] = value
        with pytest.raises(S6BMRuntimeError):
            project_fault_attempt(raw, config, "wrong_digest")


def test_s6bm_analysis_requires_every_repetition_and_supplementary_guard() -> None:
    config = S6BMConfig.from_path(CONFIG)
    profiles = [
        "wrong_digest",
        "green_load_failure",
        "green_readiness_failure",
        "green_canary_failure",
        "vram_preflight_rejection",
    ]
    attempts = [success_attempt(repetition) for repetition in range(1, 4)]
    attempts.extend(
        fault_attempt(profile, repetition) for profile in profiles for repetition in range(1, 4)
    )
    analysis = analyze_attempts(attempts, config)
    assert all(analysis["acceptance"].values())
    assert analysis["supplementary_guards_passed"] is True
    assert analysis["evidence_ready"] is True

    with pytest.raises(S6BMRuntimeError, match="s6bm_repetition_set"):
        analyze_attempts(attempts[:-1], config)


def test_s6bm_analysis_rejects_duplicate_and_out_of_contract_repetitions() -> None:
    config = S6BMConfig.from_path(CONFIG)
    profiles = [
        "wrong_digest",
        "green_load_failure",
        "green_readiness_failure",
        "green_canary_failure",
        "vram_preflight_rejection",
    ]
    attempts = [success_attempt(repetition) for repetition in range(1, 4)]
    attempts.extend(
        fault_attempt(profile, repetition) for profile in profiles for repetition in range(1, 4)
    )

    duplicate = copy.deepcopy(attempts)
    duplicate[1]["repetition"] = 1
    with pytest.raises(S6BMRuntimeError, match="s6bm_repetition_set"):
        analyze_attempts(duplicate, config)

    out_of_contract = copy.deepcopy(attempts)
    out_of_contract[2]["repetition"] = 4
    with pytest.raises(S6BMRuntimeError, match="s6bm_repetition_set"):
        analyze_attempts(out_of_contract, config)


def test_s6bm_success_rejects_offered_identity_and_effect_mutations() -> None:
    config = S6BMConfig.from_path(CONFIG)
    offered = success_attempt()
    offered["request_records"][0]["offered_identity"]["model_name"] = "substituted"  # type: ignore[index]
    with pytest.raises(S6BMRuntimeError, match="s6bm_offered_served_identity"):
        project_success_attempt(offered, config)

    duplicate_effect = success_attempt()
    duplicate_effect["request_records"][1]["effect_id"] = duplicate_effect[  # type: ignore[index]
        "request_records"
    ][0]["effect_id"]
    with pytest.raises(S6BMRuntimeError, match="s6bm_effect_identity"):
        project_success_attempt(duplicate_effect, config)
