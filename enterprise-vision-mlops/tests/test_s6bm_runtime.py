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
    canonical_sha256,
    project_fault_attempt,
    project_raw_drain_timeline,
    project_success_attempt,
)
from evm.scale_validation.s6bm_causal import (
    S6BMCausalError,
    _database_clock_envelope,
    _unix_nano,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/s8_v4_s6bm_blue_green_v1.toml"
V3_CONFIG = ROOT / "configs/s8_v4_s6bm_blue_green_v3.toml"
V4_CONFIG = ROOT / "configs/s8_v4_s6bm_blue_green_v4.toml"


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


def test_s6bm_v3_contract_freezes_causal_receipt_and_effect_boundaries() -> None:
    config = S6BMConfig.from_path(V3_CONFIG)

    assert config.schema_version == "evm.s8_v4.s6bm_runtime_config.v3"
    assert config.causal_fence["required_start_receipts"] == [
        "api_server_handler_entry",
        "controller_entry",
        "triton_backend_compute_entry",
    ]
    assert config.causal_fence["route_switch_requires_all_start_receipts"] is True
    assert config.causal_fence["exact_commit_instant_claimed"] is False
    assert config.triton_actor_receipt["required_activity"] == "COMPUTE_START"
    assert config.triton_actor_receipt["missing_or_ambiguous_trace_fails"] is True
    assert config.durable_effect["same_transaction_causal_receipt"] is True


def test_s6bm_v4_contract_freezes_auditor_hard_gates() -> None:
    config = S6BMConfig.from_path(V4_CONFIG)

    assert config.schema_version == "evm.s8_v4.s6bm_runtime_config.v4"
    assert config.clock["independent_nonce_anchor_required"] is True
    assert config.clock["adjudicated_request_anchor_forbidden"] is True
    assert (
        config.causal_fence["same_transaction_entity_idempotency_effect_event_and_sequence"] is True
    )
    assert config.triton_actor_receipt["registration_actor"] == ("dedicated_collector_process")
    assert config.triton_actor_receipt["runner_synthesized_receipt_forbidden"] is True
    assert config.durable_effect["commit_timestamp_tracking_required"] is True
    assert config.durable_effect["commit_timestamp_separate_connection_required"] is True
    assert config.durable_effect["database_clock_anchor_required"] is True
    assert config.durable_effect["commit_timestamp_readback_lane"] == (
        "bounded_parallel_post_commit_v1"
    )
    assert config.durable_effect["commit_timestamp_readback_max_concurrency"] == 2
    assert config.durable_effect["write_pool_size_change_forbidden"] is True
    assert config.durable_effect["whole_request_serialization_forbidden"] is True
    assert config.trace["exact_parent_span_required"] is True
    assert set(config.run_set) == {
        "contract",
        "baseline",
        "successful_transition",
        "wrong_digest",
        "green_load_failure",
        "green_readiness_failure",
        "green_canary_failure",
        "vram_preflight_rejection",
    }
    assert config.run_set == {
        "contract": "exact_frozen_matrix_set_v1",
        "baseline": [1, 2, 3],
        "successful_transition": [1, 2, 3],
        "wrong_digest": [1, 2, 3],
        "green_load_failure": [1, 2, 3],
        "green_readiness_failure": [1, 2, 3],
        "green_canary_failure": [1, 2, 3],
        "vram_preflight_rejection": [1, 2, 3],
    }


def test_s6bm_v4_database_clock_anchor_is_independent_and_fail_closed() -> None:
    config = S6BMConfig.from_path(V4_CONFIG)
    transaction_id = "101"
    backend_pid = 202
    schema_name = f"{config.durable_effect['schema_prefix']}_unit"
    candidates = []
    for sequence, width in enumerate((900, 800, 700, 600, 500, 400, 300, 200), 1):
        nonce = f"{sequence:032x}"
        observed_at = f"2026-08-25T00:00:00.00020{sequence}Z"
        before = sequence * 10_000
        anchor = {
            "schema_version": "evm.s6bm.database_clock_anchor.v2",
            "sequence": sequence,
            "anchor_nonce": nonce,
            "clock_source": "postgresql_clock_timestamp",
            "schema_name": schema_name,
            "source_identity": (f"postgresql:{schema_name}:{transaction_id}:{backend_pid}:{nonce}"),
            "transaction_id": transaction_id,
            "backend_pid": backend_pid,
            "monotonic_before_ns": before,
            "monotonic_after_ns": before + width,
            "database_clock_timestamp": observed_at,
            "database_unix_ns": _unix_nano(observed_at, "test"),
        }
        anchor["anchor_hash"] = canonical_sha256(anchor)
        candidates.append(anchor)
    selected = candidates[-1]
    receipt = {
        "schema_version": "evm.s6bm.durable_effect_receipt.v4",
        "transaction_id": transaction_id,
        "commit_timestamp_backend_pid": backend_pid,
        "commit_timestamp_observed_at": selected["database_clock_timestamp"],
        "commit_timestamp_started_monotonic_ns": 1,
        "commit_timestamp_finished_monotonic_ns": 100_000,
        "database_clock_anchor": selected,
        "database_clock_anchor_candidates": candidates,
        "database_clock_anchor_selection": {
            "strategy": "minimum_width_then_sequence",
            "candidate_count": 8,
            "selected_sequence": 8,
        },
    }
    envelope, projection = _database_clock_envelope(receipt, config)
    assert envelope[0] <= envelope[1]
    assert projection["width_ns"] == 200
    assert projection["candidate_count"] == 8
    assert projection["selected_sequence"] == 8

    def rehash(anchor: dict[str, object]) -> None:
        anchor["anchor_hash"] = canonical_sha256(
            {key: value for key, value in anchor.items() if key != "anchor_hash"}
        )

    tie = copy.deepcopy(receipt)
    tie_candidate = tie["database_clock_anchor_candidates"][6]
    tie_candidate["monotonic_after_ns"] = tie_candidate["monotonic_before_ns"] + 200
    rehash(tie_candidate)
    tie["database_clock_anchor"] = tie_candidate
    tie["database_clock_anchor_selection"]["selected_sequence"] = 7
    tie["commit_timestamp_observed_at"] = tie_candidate["database_clock_timestamp"]
    _, tie_projection = _database_clock_envelope(tie, config)
    assert tie_projection["selected_sequence"] == 7

    mutations = []
    missing = copy.deepcopy(receipt)
    missing["database_clock_anchor_candidates"].pop()
    mutations.append((missing, "candidate_set"))
    duplicate = copy.deepcopy(receipt)
    duplicate["database_clock_anchor_candidates"][1]["anchor_nonce"] = duplicate[
        "database_clock_anchor_candidates"
    ][0]["anchor_nonce"]
    duplicate["database_clock_anchor_candidates"][1]["source_identity"] = duplicate[
        "database_clock_anchor_candidates"
    ][0]["source_identity"]
    rehash(duplicate["database_clock_anchor_candidates"][1])
    mutations.append((duplicate, "candidate_duplicate"))
    reordered = copy.deepcopy(receipt)
    reordered["database_clock_anchor_candidates"][0:2] = reversed(
        reordered["database_clock_anchor_candidates"][0:2]
    )
    mutations.append((reordered, "candidate_set"))
    wrong_index = copy.deepcopy(receipt)
    wrong_index["database_clock_anchor_selection"]["selected_sequence"] = 7
    mutations.append((wrong_index, "selection"))
    nonminimum = copy.deepcopy(receipt)
    nonminimum["database_clock_anchor"] = nonminimum["database_clock_anchor_candidates"][6]
    nonminimum["database_clock_anchor_selection"]["selected_sequence"] = 7
    nonminimum["commit_timestamp_observed_at"] = nonminimum["database_clock_anchor"][
        "database_clock_timestamp"
    ]
    mutations.append((nonminimum, "selection"))
    hash_drift = copy.deepcopy(receipt)
    hash_drift["database_clock_anchor_candidates"][3]["anchor_hash"] = "f" * 64
    mutations.append((hash_drift, "candidate_hash"))
    all_over = copy.deepcopy(receipt)
    for candidate in all_over["database_clock_anchor_candidates"]:
        candidate["monotonic_before_ns"] = candidate["sequence"] * 2_000_000
        candidate["monotonic_after_ns"] = candidate["monotonic_before_ns"] + 1_000_001
        rehash(candidate)
    all_over["database_clock_anchor"] = all_over["database_clock_anchor_candidates"][0]
    all_over["database_clock_anchor_selection"]["selected_sequence"] = 1
    all_over["commit_timestamp_observed_at"] = all_over["database_clock_anchor"][
        "database_clock_timestamp"
    ]
    all_over["commit_timestamp_finished_monotonic_ns"] = 20_000_000
    mutations.append((all_over, "all_candidates_over_bound"))

    for candidate, code in mutations:
        with pytest.raises(S6BMCausalError, match=f"database_clock_{code}"):
            _database_clock_envelope(candidate, config)


def success_attempt(repetition: int = 1) -> dict[str, object]:
    config = S6BMConfig.from_path(CONFIG)
    records = []
    for index in range(1000):
        role = "blue" if index < 100 else "green"
        model = config.blue if role == "blue" else config.green
        is_hold = index == 99
        completed_monotonic = 94.2 if is_hold else 92.0 + index * 0.001
        elapsed_ms = 1300.0 if is_hold else 10.0
        records.append(
            {
                "run_id": "s8-v4-s6bm-unit-test",
                "attempt_id": f"success-{repetition}",
                "request_id": (
                    f"success-{repetition}-hold-blue-00000" if is_hold else f"request-{index:04d}"
                ),
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
                "elapsed_ms": elapsed_ms,
                "attempted_monotonic": completed_monotonic - elapsed_ms / 1000.0,
                "completed_monotonic": completed_monotonic,
            }
        )
    return {
        "attempt_id": f"success-{repetition}",
        "profile": "successful_transition",
        "repetition": repetition,
        "identities": identities(),
        "phase_timeline": [
            {"phase": phase, "monotonic_seconds": monotonic}
            for phase, monotonic in zip(
                SUCCESS_PHASES,
                (90.0, 91.0, 92.0, 93.0, 93.01, 95.0, 96.0, 97.0, 98.0, 99.0),
                strict=True,
            )
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
            "max_inter_completion_gap_ms": 1201.0,
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
    attempt_id = f"{profile}-{repetition}"
    suite_id = "s6bm-unit-suite"
    return {
        "attempt_id": attempt_id,
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
        "telemetry": {
            "suite_id": suite_id,
            "attempt_id": attempt_id,
            "target_count": 2,
            "target_labels": [
                {
                    "job": "evm-s8-v4-s6bm-api",
                    "scenario": "s8-v4-s6bm",
                    "suite_id": suite_id,
                    "attempt_id": attempt_id,
                },
                {
                    "job": "evm-s8-v4-s6bm-triton",
                    "scenario": "s8-v4-s6bm",
                    "suite_id": suite_id,
                    "attempt_id": attempt_id,
                },
            ],
            "api_target_up": True,
            "triton_target_up": True,
        },
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


def test_s6bm_raw_drain_projection_rejects_timeline_consistent_fail_open_cases() -> None:
    config = S6BMConfig.from_path(CONFIG)
    raw = success_attempt()
    projection = project_raw_drain_timeline(raw, config)
    assert projection["hold_request_count"] == 1
    assert projection["blue_in_flight_at_switch"] == 1
    assert projection["blue_in_flight_at_unload_boundary"] == 0

    completed_before_switch = copy.deepcopy(raw)
    hold = next(
        item
        for item in completed_before_switch["request_records"]  # type: ignore[index]
        if "-hold-blue-" in item["request_id"]
    )
    hold["attempted_monotonic"] = 91.6
    hold["completed_monotonic"] = 92.9
    hold["elapsed_ms"] = 1300.0
    with pytest.raises(S6BMRuntimeError, match="s6bm_drain_hold_request_absent"):
        project_raw_drain_timeline(completed_before_switch, config)

    unload_before_completion = copy.deepcopy(raw)
    next(
        item
        for item in unload_before_completion["phase_timeline"]  # type: ignore[index]
        if item["phase"] == "green_only"
    )["monotonic_seconds"] = 94.199
    with pytest.raises(S6BMRuntimeError, match="s6bm_drain_unload_before_blue_completion"):
        project_raw_drain_timeline(unload_before_completion, config)


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

    for mutate in (
        lambda raw: raw["telemetry"].update(attempt_id="substituted"),  # type: ignore[union-attr]
        lambda raw: raw["telemetry"].update(target_count=1),  # type: ignore[union-attr]
        lambda raw: raw["telemetry"]["target_labels"][0].update(  # type: ignore[index]
            attempt_id="substituted"
        ),
    ):
        raw = fault_attempt("wrong_digest")
        mutate(raw)
        with pytest.raises(S6BMRuntimeError, match="s6bm_fault_telemetry_identity"):
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
