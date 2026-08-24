from __future__ import annotations

import copy
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


def success_attempt(repetition: int = 1) -> dict[str, object]:
    return {
        "attempt_id": f"success-{repetition}",
        "profile": "successful_transition",
        "repetition": repetition,
        "phase_timeline": [{"phase": phase} for phase in SUCCESS_PHASES],
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
        "illegal_owner_overlap": 0,
        "trace_complete": 1000,
        "blue_in_flight_before_unload": 0,
        "green_in_flight_before_unload": 0,
        "rollback_exact_blue": True,
        "latency": {
            "p95_ms": 10.0,
            "p99_ms": 20.0,
            "max_inter_completion_gap_ms": 100.0,
        },
        "transition_seconds": 2.0,
        "rollback_seconds": 2.0,
        "peak_vram_mib": 1024.0,
        "cleanup": {
            "blue_only": True,
            "green_unloaded": True,
            "queue_zero": True,
            "lease_owner_exact": True,
        },
    }


def fault_attempt(profile: str, repetition: int = 1) -> dict[str, object]:
    return {
        "attempt_id": f"{profile}-{repetition}",
        "profile": profile,
        "repetition": repetition,
        "guard_rejected": True,
        "guard_code": f"{profile}_rejected",
        "route_unchanged_blue": True,
        "green_effect_count": 0,
        "route_switch_count": 0,
        "http_5xx": 0,
        "orphan_count": 0,
        "blue_health_after": True,
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
        ("cleanup", lambda raw: raw["cleanup"].update(queue_zero=False)),  # type: ignore[union-attr]
    ]
    for _name, mutate in mutations:
        raw = copy.deepcopy(success_attempt())
        mutate(raw)
        with pytest.raises(S6BMRuntimeError):
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
        fault_attempt(profile, repetition)
        for profile in profiles
        for repetition in range(1, 4)
    )
    analysis = analyze_attempts(attempts, config)
    assert all(analysis["acceptance"].values())
    assert analysis["supplementary_guards_passed"] is True
    assert analysis["evidence_ready"] is True

    incomplete = analyze_attempts(attempts[:-1], config)
    assert incomplete["supplementary_guards_passed"] is False
    assert incomplete["evidence_ready"] is False
