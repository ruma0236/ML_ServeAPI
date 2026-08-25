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
    build_continuity_plan,
    canonical_sha256,
    canonical,
    project_fault_attempt,
    project_raw_drain_timeline,
    project_success_attempt,
)
from evm.scale_validation.s6bm_causal import (
    S6BMCausalError,
    _database_clock_envelope,
    _fit_affine_clock_model,
    _unix_nano,
    _validate_hold_effect_span_order,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/s8_v4_s6bm_blue_green_v1.toml"
V3_CONFIG = ROOT / "configs/s8_v4_s6bm_blue_green_v3.toml"
V4_CONFIG = ROOT / "configs/s8_v4_s6bm_blue_green_v4.toml"


def affine_points(residuals: list[int], *, drift_ppm: int = 0) -> list[dict[str, object]]:
    origin = 1_000_000_000
    points: list[dict[str, object]] = []
    for index, residual in enumerate(residuals, start=1):
        monotonic_ns = origin + index * 1_000_000
        drift_ns = ((monotonic_ns - origin) * drift_ppm) // 1_000_000
        points.append(
            {
                "source": "unit",
                "source_sequence": index,
                "monotonic_before_ns": monotonic_ns,
                "monotonic_after_ns": monotonic_ns,
                "unix_ns": 2_000_000_000 + monotonic_ns + drift_ns + residual,
            }
        )
    return points


def test_s6bm_v4_affine_clock_exact_boundaries() -> None:
    config = S6BMConfig.from_path(V4_CONFIG)
    _, at_bound = _fit_affine_clock_model(affine_points([0] * 5, drift_ppm=100), config)
    assert at_bound["outlier_count"] == 0

    with pytest.raises(S6BMCausalError, match="s6bm_v4_clock_affine_drift"):
        _fit_affine_clock_model(affine_points([0] * 5, drift_ppm=101), config)

    _fit_affine_clock_model(affine_points([-500_000, 0, 1_000_000, 0, -500_000]), config)
    with pytest.raises(S6BMCausalError, match="s6bm_v4_clock_affine_residual"):
        _fit_affine_clock_model(affine_points([-500_000, 0, 1_000_001, -2, -499_999]), config)

    with pytest.raises(S6BMCausalError, match="s6bm_v4_clock_affine_step"):
        _fit_affine_clock_model(affine_points([-333_334, 1_000_001, -1_000_000, 333_333]), config)


def test_s6bm_v4_same_clock_nested_span_closures_allow_equality() -> None:
    effect = {"start_unix_ns": 200, "end_unix_ns": 400}
    controller = {"start_unix_ns": 100, "end_unix_ns": 400}
    server = {"start_unix_ns": 50, "end_unix_ns": 400}

    _validate_hold_effect_span_order(
        effect_start_interval=(1000, 1100),
        commit_interval=(1200, 1300),
        effect_end_interval=(1400, 1500),
        effect_span=effect,
        controller_span=controller,
        server_span=server,
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("effect_commit", "s6bm_v4_effect_span_commit_order"),
        ("controller_end", "s6bm_v4_controller_effect_span_order"),
        ("server_end", "s6bm_v4_server_controller_span_order"),
    ],
)
def test_s6bm_v4_same_clock_span_order_fails_closed(mutation: str, reason: str) -> None:
    effect = {"start_unix_ns": 200, "end_unix_ns": 400}
    controller = {"start_unix_ns": 100, "end_unix_ns": 400}
    server = {"start_unix_ns": 50, "end_unix_ns": 400}
    commit_interval = (1200, 1300)
    effect_end_interval = (1400, 1500)
    if mutation == "effect_commit":
        effect_end_interval = (1250, 1350)
    elif mutation == "controller_end":
        controller["end_unix_ns"] = 399
    else:
        server["end_unix_ns"] = 399

    with pytest.raises(S6BMCausalError, match=reason):
        _validate_hold_effect_span_order(
            effect_start_interval=(1000, 1100),
            commit_interval=commit_interval,
            effect_end_interval=effect_end_interval,
            effect_span=effect,
            controller_span=controller,
            server_span=server,
        )


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
    assert config.clock["affine_model"] == "centered_ols_fraction_all_points_v1"
    assert config.clock["affine_max_drift_ppm"] == 100
    assert config.clock["affine_max_residual_ns"] == 1_000_000
    assert config.clock["affine_max_step_ns"] == 2_000_000
    assert config.clock["affine_required_outlier_count"] == 0
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
    assert config.durable_effect["database_clock_anchor_max_selected_width_ns"] == 5_000_000
    assert config.durable_effect["write_pool_size_change_forbidden"] is True
    assert config.durable_effect["whole_request_serialization_forbidden"] is True
    assert config.trace["exact_parent_span_required"] is True
    assert config.continuity == {
        "contract": "fixed_exact_1000_blue_continuity_v1",
        "logical_request_count": 1000,
        "canary_count": 100,
        "causal_hold_count": 1,
        "bridge_count": 40,
        "normal_count": 859,
        "cadence_ms": 150,
        "producer_workers": 4,
        "max_in_flight_requests": 4,
        "max_request_payload_bytes": 4096,
        "max_in_flight_payload_bytes": 16384,
        "bridge_hold_count": 4,
        "bridge_hold_ms": 400,
        "required_actor_bridge_count": 4,
        "crossover_bridge_count": 1,
        "required_actor_bridge_selection": "prefix",
        "held_bridge_selection": "suffix",
        "crossover_bridge_selection": "first_required",
        "pending_crossover_count": 2,
        "pre_switch_terminal_bridge_count": 39,
        "route_switch_barrier_mode": "exact_one_after_39_blue_terminal",
        "route_switch_barrier_timeout_seconds": 15,
        "max_schedule_lateness_ms": 500,
        "minimum_blue_in_flight_at_switch": 2,
        "minimum_bridge_cross_switch_completions": 1,
        "minimum_transition_terminal_completions": 8,
        "adaptive_pacing_forbidden": True,
        "post_hoc_windowing_forbidden": True,
        "schedule_hash_algorithm": "sha256_canonical_json",
    }
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
    assert projection["max_selected_width_ns"] == 5_000_000
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

    at_bound = copy.deepcopy(receipt)
    for candidate in at_bound["database_clock_anchor_candidates"]:
        candidate["monotonic_before_ns"] = candidate["sequence"] * 6_000_000
        candidate["monotonic_after_ns"] = candidate["monotonic_before_ns"] + 5_000_000
        rehash(candidate)
    at_bound["database_clock_anchor"] = at_bound["database_clock_anchor_candidates"][0]
    at_bound["database_clock_anchor_selection"]["selected_sequence"] = 1
    at_bound["commit_timestamp_observed_at"] = at_bound["database_clock_anchor"][
        "database_clock_timestamp"
    ]
    at_bound["commit_timestamp_finished_monotonic_ns"] = 60_000_000
    _, at_bound_projection = _database_clock_envelope(at_bound, config)
    assert at_bound_projection["width_ns"] == 5_000_000

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
        candidate["monotonic_before_ns"] = candidate["sequence"] * 6_000_000
        candidate["monotonic_after_ns"] = candidate["monotonic_before_ns"] + 5_000_001
        rehash(candidate)
    all_over["database_clock_anchor"] = all_over["database_clock_anchor_candidates"][0]
    all_over["database_clock_anchor_selection"]["selected_sequence"] = 1
    all_over["commit_timestamp_observed_at"] = all_over["database_clock_anchor"][
        "database_clock_timestamp"
    ]
    all_over["commit_timestamp_finished_monotonic_ns"] = 60_000_000
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


def v4_continuity_attempt() -> dict[str, object]:
    config = S6BMConfig.from_path(V4_CONFIG)
    attempt_id = "s6bm-success-1-continuity-unit"
    plan = build_continuity_plan(config, attempt_id)
    records: list[dict[str, object]] = []
    producer_started = 86.0
    switch = 93.0
    role_items = [dict(item) for role in plan["role_order"] for item in plan["roles"][role]]
    for item in role_items:
        traffic_role = str(item["traffic_role"])
        ordinal = int(item["ordinal"])
        role = str(item["expected_model_role"])
        model = config.blue if role == "blue" else config.green
        if traffic_role == "canary":
            attempted = 84.0 + ordinal * 0.001
            terminal = attempted + 0.01
        elif traffic_role == "causal_hold":
            attempted = 85.0
            terminal = 93.04
        elif traffic_role == "bridge":
            bridge_index = ordinal - 101
            attempted = producer_started + bridge_index * 0.15 + 0.001
            if bridge_index == 0:
                terminal = 93.04
            elif bridge_index >= 36:
                terminal = attempted + 0.41
            else:
                terminal = attempted + 0.05
        else:
            normal_index = ordinal - 141
            attempted = 93.1 + normal_index * 0.0001
            terminal = attempted + 0.01
        completed = terminal + 0.005
        request_id = str(item["request_id"])
        payload_sha = hashlib.sha256(f"payload:{request_id}".encode("ascii")).hexdigest()
        records.append(
            {
                "run_id": "s8-v4-s6bm-unit-test",
                "attempt_id": attempt_id,
                "request_id": request_id,
                "request_nonce": hashlib.sha256(request_id.encode("ascii")).hexdigest()[:32],
                "trace_id": f"{ordinal + 1:032x}",
                "effect_id": hashlib.sha256(
                    f"{attempt_id}:{request_id}".encode("ascii")
                ).hexdigest(),
                "offered_traceparent": f"00-{ordinal + 1:032x}-{ordinal + 2:016x}-01",
                "offered_identity": {
                    "model_role": role,
                    "model_name": model.model_name,
                    "model_version": model.model_version,
                    "artifact_sha256": model.artifact_sha256,
                },
                "offered_payload_bytes": 500,
                "offered_payload_sha256": payload_sha,
                "status_code": 200,
                "outcome": "completed",
                "model_role": role,
                "model_name": model.model_name,
                "model_version": model.model_version,
                "artifact_sha256": model.artifact_sha256,
                "output": list(model.expected_output),
                "elapsed_ms": (completed - attempted) * 1000,
                "attempted_monotonic": attempted,
                "completed_monotonic": completed,
                "route_generation": 3 if traffic_role == "normal" else 2,
                "durable_effect": {
                    "readback_visible": True,
                    "readback_finished_monotonic_ns": int(terminal * 1e9),
                },
            }
        )
    terminal_times = sorted(
        int(dict(item["durable_effect"])["readback_finished_monotonic_ns"]) / 1e9
        for item in records
    )
    latencies = sorted(float(item["elapsed_ms"]) for item in records)

    def percentile(value: float) -> float:
        position = (len(latencies) - 1) * value
        lower = int(position)
        upper = min(lower + 1, len(latencies) - 1)
        fraction = position - lower
        return latencies[lower] + (latencies[upper] - latencies[lower]) * fraction

    dispatches = []
    for item in plan["roles"]["bridge"]:
        record = next(value for value in records if value["request_id"] == item["request_id"])
        attempted = float(record["attempted_monotonic"])
        dispatches.append(
            {
                "request_id": item["request_id"],
                "scheduled_offset_ms": item["scheduled_offset_ms"],
                "hold_ms": item["hold_ms"],
                "actor_receipt_required": item["actor_receipt_required"],
                "causal_crossover": item["causal_crossover"],
                "payload_bytes": 500,
                "payload_sha256": record["offered_payload_sha256"],
                "capacity_wait_ms": 0.0,
                "attempted_monotonic": attempted,
                "schedule_lateness_ms": (
                    attempted - (producer_started + int(item["scheduled_offset_ms"]) / 1000)
                )
                * 1000,
                "outcome": "completed",
                "status_code": 200,
            }
        )
    plan_artifact_sha = hashlib.sha256((canonical(plan) + "\n").encode("ascii")).hexdigest()
    role_counts = {role: len(plan["roles"][role]) for role in plan["role_order"]}
    required_bridge_ids = [
        item["request_id"]
        for item in plan["roles"]["bridge"]
        if item["actor_receipt_required"] is True
    ]
    crossover_bridge_ids = [
        item["request_id"] for item in plan["roles"]["bridge"] if item["causal_crossover"] is True
    ]
    causal_hold_ids = [item["request_id"] for item in plan["roles"]["causal_hold"]]
    pending_crossover_ids = sorted(causal_hold_ids + crossover_bridge_ids)
    pre_switch_terminal_ids = sorted(
        item["request_id"]
        for item in plan["roles"]["bridge"]
        if item["causal_crossover"] is not True
    )
    pre_switch_terminal_records = []
    for request_id in pre_switch_terminal_ids:
        record = next(item for item in records if item["request_id"] == request_id)
        pre_switch_terminal_records.append(
            {
                "request_id": request_id,
                "attempt_id": record["attempt_id"],
                "run_id": record["run_id"],
                "trace_id": record["trace_id"],
                "effect_id": record["effect_id"],
                "model_role": record["model_role"],
                "model_name": record["model_name"],
                "model_version": record["model_version"],
                "artifact_sha256": record["artifact_sha256"],
                "route_generation": record["route_generation"],
                "status_code": record["status_code"],
                "outcome": record["outcome"],
                "attempted_monotonic": record["attempted_monotonic"],
                "completed_monotonic": record["completed_monotonic"],
                "durable_effect_readback_finished_monotonic_ns": record["durable_effect"][
                    "readback_finished_monotonic_ns"
                ],
            }
        )
    bridge_gate_events = []
    for sequence, (request_id, stage) in enumerate(
        (
            (request_id, stage)
            for request_id in required_bridge_ids
            for stage in (
                "api_server_handler_entry",
                "controller_entry",
                "triton_backend_compute_entry",
            )
        ),
        start=1,
    ):
        record = next(item for item in records if item["request_id"] == request_id)
        bridge_gate_events.append(
            {
                "causal_sequence": sequence,
                "event_type": stage,
                "attempt_id": attempt_id,
                "run_id": record["run_id"],
                "request_id": request_id,
                "request_nonce": record["request_nonce"],
                "trace_id": record["trace_id"],
                "effect_id": record["effect_id"],
                "model_role": "blue",
                "model_name": config.blue.model_name,
                "model_version": config.blue.model_version,
                "artifact_sha256": config.blue.artifact_sha256,
                "route_generation": 2,
                "actor_identity": f"actor:{stage}",
                "transaction_id": str(10_000 + sequence),
                "payload_sha256": hashlib.sha256(
                    f"{request_id}:{stage}".encode("ascii")
                ).hexdigest(),
                "database_recorded_at": "2026-08-25T00:00:00Z",
                "readback_at": "2026-08-25T00:00:00.001Z",
                "readback_visible": True,
                "readback_source": "postgresql_attempt_export",
            }
        )
    return {
        "attempt_id": attempt_id,
        "profile": "successful_transition",
        "repetition": 1,
        "identities": identities(),
        "phase_timeline": [
            {"phase": phase, "monotonic_seconds": monotonic}
            for phase, monotonic in zip(
                SUCCESS_PHASES,
                (80.0, 81.0, 84.0, 93.0, 93.01, 95.0, 96.0, 97.0, 98.0, 99.0),
                strict=True,
            )
        ],
        "traffic_plan": plan,
        "traffic_plan_artifact": {
            "path": f"traffic-plans/{attempt_id}/traffic-plan.json",
            "sha256": plan_artifact_sha,
            "bytes": len((canonical(plan) + "\n").encode("ascii")),
        },
        "continuity_execution": {
            "plan_sha256": plan["plan_sha256"],
            "plan_frozen_monotonic": 79.0,
            "controller_initialized_monotonic": 80.0,
            "producer_started_monotonic": producer_started,
            "causal_gate_started_monotonic": producer_started + 0.0001,
            "all_submitted_monotonic": 91.851,
            "switch_invoked_monotonic": 92.99,
            "transition_receipt_observed_monotonic": 93.005,
            "producer_finished_monotonic": 94.0,
            "adaptive_pacing": False,
            "switch_gate_basis": (
                "all40_schedule_plus_exact39_blue_terminal_plus_exact4x3_receipts_"
                "plus_exact2_pending_crossovers"
            ),
            "blue_in_flight_before_switch": 2,
            "pre_switch_state": {
                "generation": 2,
                "phase": "canary",
                "blue_in_flight": 2,
                "pending_crossover_request_ids": list(pending_crossover_ids),
                "pending_crossover_count": 2,
            },
            "bridge_triton_start_receipts": [
                {"request_id": request_id} for request_id in required_bridge_ids
            ],
            "bridge_actor_receipt_gate": {
                "schema_version": "evm.s8_v4.s6bm_bridge_actor_receipt_gate.v1",
                "attempt_id": attempt_id,
                "route_generation": 2,
                "required_request_ids": required_bridge_ids,
                "required_request_set_sha256": canonical_sha256(required_bridge_ids),
                "required_stage_count": 3,
                "expected_event_count": 12,
                "visible_event_count": 12,
                "raw_readback_export": {
                    "path": f"causal/{attempt_id}/bridge-start-receipts-pre-switch.json",
                    "sha256": "a" * 64,
                    "bytes": 1024,
                },
                "raw_readback_event_count": 15,
                "selected_event_set_sha256": canonical_sha256(bridge_gate_events),
                "events": bridge_gate_events,
                "collector_request_ids": required_bridge_ids,
                "collector_request_set_sha256": canonical_sha256(required_bridge_ids),
                "gate_satisfied_monotonic": 92.98,
            },
            "pre_switch_terminal_gate": {
                "schema_version": ("evm.s8_v4.s6bm_pre_switch_bridge_terminal_gate.v1"),
                "crossover_request_id": crossover_bridge_ids[0],
                "expected_terminal_request_ids": pre_switch_terminal_ids,
                "expected_terminal_request_set_sha256": canonical_sha256(pre_switch_terminal_ids),
                "expected_terminal_count": 39,
                "observed_terminal_request_ids": pre_switch_terminal_ids,
                "observed_terminal_request_set_sha256": canonical_sha256(pre_switch_terminal_ids),
                "observed_terminal_count": 39,
                "terminal_records": pre_switch_terminal_records,
                "terminal_records_sha256": canonical_sha256(pre_switch_terminal_records),
                "all_submitted_monotonic": 91.851,
                "all_non_crossover_terminal_monotonic": 92.3,
            },
            "max_reserved_requests_observed": 4,
            "max_reserved_payload_bytes_observed": 2000,
            "reserved_requests_at_finish": 0,
            "reserved_payload_bytes_at_finish": 0,
            "dispatches": dispatches,
        },
        "traffic_conservation": {
            "logical_request_ids": 1000,
            "offered": 1000,
            "admitted": 1000,
            "terminal": 1000,
            "completed": 1000,
            "duplicate_replay_attempts": 1,
            "client_attempts": 1001,
            "missing": 0,
            "duplicate": 0,
            "dropped": 0,
            "backpressure_terminal": 0,
            "schedule_late_dispatches": 0,
            "capacity_waited_dispatches": 0,
            "terminal_during_transition": 39,
            "bridge_cross_switch_completions": 1,
            "gap_clock": "durable_effect_readback_monotonic_ns",
            "role_counts": role_counts,
        },
        "causal_proof": {
            "route_transition_receipt": {
                "old_route_generation": 2,
                "new_route_generation": 3,
                "route_applied_monotonic_ns": int(switch * 1e9),
                "continuity_receipt_request_ids": required_bridge_ids,
                "continuity_receipt_request_set_sha256": canonical_sha256(required_bridge_ids),
                "continuity_crossover_request_ids": crossover_bridge_ids,
                "continuity_crossover_request_set_sha256": canonical_sha256(crossover_bridge_ids),
                "pending_crossover_request_ids": list(pending_crossover_ids),
                "pending_crossover_request_set_sha256": canonical_sha256(pending_crossover_ids),
                "released_crossover_request_ids": list(pending_crossover_ids),
                "crossover_release_monotonic_ns": int(93.002 * 1e9),
                "crossover_release_basis": ("fence_commit_readback_and_route_applied"),
            }
        },
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
            "request_id": records[0]["request_id"],
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
            "p95_ms": percentile(0.95),
            "p99_ms": percentile(0.99),
            "max_inter_completion_gap_ms": max(
                (right - left) * 1000
                for left, right in zip(terminal_times, terminal_times[1:], strict=False)
            ),
        },
        "transition_seconds": 13.0,
        "rollback_seconds": 4.0,
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
            "controller_pending_crossovers_zero": True,
        },
    }


def test_s6bm_v4_continuity_plan_is_exact_and_frozen() -> None:
    config = S6BMConfig.from_path(V4_CONFIG)
    plan = build_continuity_plan(config, "s6bm-success-plan-unit")
    ids = [item["request_id"] for role in plan["role_order"] for item in plan["roles"][role]]
    assert len(ids) == len(set(ids)) == 1000
    assert {role: len(plan["roles"][role]) for role in plan["role_order"]} == {
        "canary": 100,
        "causal_hold": 1,
        "bridge": 40,
        "normal": 859,
    }
    assert plan["request_id_set_sha256"] == canonical_sha256(ids)
    assert plan["adaptive_pacing"] is False
    assert plan["completion_windowing"] == "all_exact_logical_ids"
    required_bridge = [
        item for item in plan["roles"]["bridge"] if item["actor_receipt_required"] is True
    ]
    held_bridge = [item for item in plan["roles"]["bridge"] if item["hold_ms"] > 0]
    crossover_bridge = [
        item for item in plan["roles"]["bridge"] if item["causal_crossover"] is True
    ]
    assert len(required_bridge) == 4
    assert [item["ordinal"] for item in required_bridge] == [101, 102, 103, 104]
    assert len(held_bridge) == 4
    assert [item["ordinal"] for item in held_bridge] == [137, 138, 139, 140]
    assert all(item["hold_ms"] == 400 for item in held_bridge)
    assert [item["request_id"] for item in crossover_bridge] == [required_bridge[0]["request_id"]]
    assert set(plan["bridge_subsets"]) == {"receipt_required", "held", "crossover"}


def test_s6bm_v4_continuity_projection_uses_all_durable_terminal_completions() -> None:
    config = S6BMConfig.from_path(V4_CONFIG)
    raw = v4_continuity_attempt()
    projection = project_success_attempt(raw, config)
    assert projection["continuity"]["logical_request_count"] == 1000
    assert projection["continuity"]["bridge_cross_switch_completions"] == 1
    assert projection["continuity"]["terminal_during_transition"] == 39
    assert (
        projection["max_inter_completion_gap_ms"] == raw["latency"]["max_inter_completion_gap_ms"]
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("partition_overlap", "s6bm_continuity_plan_binding"),
        ("logical_missing", "s6bm_request_records_empty|s6bm_continuity_exact_logical_set"),
        ("schedule_late", "s6bm_continuity_schedule_late"),
        ("bridge_post_switch", "s6bm_continuity_stale_blue_admission"),
        ("wrong_epoch", "s6bm_continuity_generation_binding"),
        ("wrong_digest", "s6bm_request_model_identity"),
        ("synthetic_completion", "s6bm_durable_terminal_readback"),
        ("gap_window", "s6bm_continuity_post_hoc_window"),
        ("capacity", "s6bm_continuity_capacity_bound"),
        ("actor_receipt_missing", "s6bm_continuity_actor_receipt_gate"),
        ("callback_before_actor_start", "s6bm_continuity_actor_receipt_gate"),
        ("terminal_set_missing", "s6bm_continuity_pre_switch_terminal_set"),
        ("terminal_set_extra", "s6bm_continuity_pre_switch_terminal_set"),
        ("terminal_identity_mismatch", "s6bm_continuity_pre_switch_terminal_binding"),
        ("last_future_green_routed", "s6bm_continuity_role_binding"),
        ("pending_crossover_missing", "s6bm_continuity_crossover_release_binding"),
        ("pending_crossover_extra", "s6bm_continuity_crossover_release_binding"),
        ("pending_state_missing", "s6bm_continuity_pending_crossover_gate"),
        ("pending_state_extra", "s6bm_continuity_pending_crossover_gate"),
        ("one_crossover_not_released", "s6bm_continuity_crossover_release_binding"),
        ("release_before_route_applied", "s6bm_continuity_crossover_release_order"),
        ("release_before_receipts", "s6bm_continuity_crossover_release_order"),
        ("crossover_timeout_cleanup", "s6bm_success_cleanup"),
        ("normal_old_epoch", "s6bm_continuity_generation_binding"),
        ("premature_unload", "s6bm_drain_unload_before_blue_completion"),
    ],
)
def test_s6bm_v4_continuity_mutations_fail_closed(mutation: str, reason: str) -> None:
    config = S6BMConfig.from_path(V4_CONFIG)
    raw = v4_continuity_attempt()
    bridge_id = raw["traffic_plan"]["roles"]["bridge"][0]["request_id"]
    bridge_record = next(item for item in raw["request_records"] if item["request_id"] == bridge_id)
    if mutation == "partition_overlap":
        raw["traffic_plan"]["roles"]["normal"][0]["request_id"] = bridge_id
    elif mutation == "logical_missing":
        raw["request_records"].pop()
    elif mutation == "schedule_late":
        bridge_record["attempted_monotonic"] += 0.6
        raw["continuity_execution"]["dispatches"][0]["attempted_monotonic"] += 0.6
        raw["continuity_execution"]["dispatches"][0]["schedule_lateness_ms"] += 600
    elif mutation == "bridge_post_switch":
        bridge_record["attempted_monotonic"] = 93.001
        raw["continuity_execution"]["dispatches"][0]["attempted_monotonic"] = 93.001
        raw["continuity_execution"]["dispatches"][0]["schedule_lateness_ms"] = 7001.0
    elif mutation == "wrong_epoch":
        bridge_record["route_generation"] = 3
    elif mutation == "wrong_digest":
        bridge_record["artifact_sha256"] = "f" * 64
    elif mutation == "synthetic_completion":
        bridge_record["durable_effect"] = {}
    elif mutation == "gap_window":
        raw["completion_window"] = {"exclude": [bridge_id]}
    elif mutation == "capacity":
        raw["continuity_execution"]["max_reserved_requests_observed"] = 5
    elif mutation == "actor_receipt_missing":
        raw["continuity_execution"]["bridge_actor_receipt_gate"]["events"].pop()
    elif mutation == "callback_before_actor_start":
        raw["continuity_execution"]["bridge_actor_receipt_gate"]["gate_satisfied_monotonic"] = 93.0
    elif mutation == "terminal_set_missing":
        raw["continuity_execution"]["pre_switch_terminal_gate"]["terminal_records"].pop()
    elif mutation == "terminal_set_extra":
        crossover_id = raw["traffic_plan"]["bridge_subsets"]["crossover"]["request_ids"][0]
        crossover_record = next(
            item for item in raw["request_records"] if item["request_id"] == crossover_id
        )
        raw["continuity_execution"]["pre_switch_terminal_gate"]["terminal_records"].append(
            {
                "request_id": crossover_id,
                "attempt_id": crossover_record["attempt_id"],
                "run_id": crossover_record["run_id"],
            }
        )
    elif mutation == "terminal_identity_mismatch":
        terminal_gate = raw["continuity_execution"]["pre_switch_terminal_gate"]
        terminal_gate["terminal_records"][0]["model_role"] = "green"
        terminal_gate["terminal_records_sha256"] = canonical_sha256(
            terminal_gate["terminal_records"]
        )
    elif mutation == "last_future_green_routed":
        last_id = raw["continuity_execution"]["pre_switch_terminal_gate"][
            "expected_terminal_request_ids"
        ][-1]
        last_record = next(item for item in raw["request_records"] if item["request_id"] == last_id)
        last_record["model_role"] = "green"
        last_record["model_name"] = config.green.model_name
        last_record["model_version"] = config.green.model_version
        last_record["artifact_sha256"] = config.green.artifact_sha256
        last_record["offered_identity"] = {
            "model_role": "green",
            "model_name": config.green.model_name,
            "model_version": config.green.model_version,
            "artifact_sha256": config.green.artifact_sha256,
        }
        last_record["output"] = list(config.green.expected_output)
        last_record["route_generation"] = 3
    elif mutation == "pending_crossover_missing":
        raw["causal_proof"]["route_transition_receipt"]["pending_crossover_request_ids"].pop()
    elif mutation == "pending_crossover_extra":
        raw["causal_proof"]["route_transition_receipt"]["pending_crossover_request_ids"].append(
            "unexpected-crossover"
        )
    elif mutation == "pending_state_missing":
        raw["continuity_execution"]["pre_switch_state"]["pending_crossover_request_ids"].pop()
    elif mutation == "pending_state_extra":
        raw["continuity_execution"]["pre_switch_state"]["pending_crossover_request_ids"].append(
            "unexpected-crossover"
        )
    elif mutation == "one_crossover_not_released":
        raw["causal_proof"]["route_transition_receipt"]["released_crossover_request_ids"].pop()
    elif mutation == "release_before_route_applied":
        raw["causal_proof"]["route_transition_receipt"]["crossover_release_monotonic_ns"] = int(
            92.999 * 1e9
        )
    elif mutation == "release_before_receipts":
        raw["causal_proof"]["route_transition_receipt"]["crossover_release_monotonic_ns"] = int(
            92.97 * 1e9
        )
    elif mutation == "crossover_timeout_cleanup":
        raw["cleanup"]["controller_pending_crossovers_zero"] = False
    elif mutation == "normal_old_epoch":
        normal_id = raw["traffic_plan"]["roles"]["normal"][0]["request_id"]
        next(item for item in raw["request_records"] if item["request_id"] == normal_id)[
            "route_generation"
        ] = 2
    elif mutation == "premature_unload":
        next(item for item in raw["phase_timeline"] if item["phase"] == "green_only")[
            "monotonic_seconds"
        ] = 93.03
    with pytest.raises(S6BMRuntimeError, match=reason):
        project_success_attempt(raw, config)


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
