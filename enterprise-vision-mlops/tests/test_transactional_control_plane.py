from __future__ import annotations

import concurrent.futures
import hashlib
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from evm.control_panel import lifecycle_runs
from evm.control_panel import operations
from evm.control_panel.lifecycle_runs import (
    LifecycleActionRequest,
    LifecycleRunRequest,
    create_lifecycle_run,
    queue_lifecycle_run,
)
from evm.control_panel.pipeline_profiles import default_profile, save_profile
from evm.control_panel.schemas import TaskAssignmentRequest, TaskTransitionRequest
from evm.control_panel.transactional_store import (
    ControlPlaneIdempotencyConflict,
    ControlPlaneLeaseConflict,
    ControlPlanePoolTimeout,
    ControlPlaneParityError,
    ControlPlaneTransactionTimeout,
    ControlPlaneVersionConflict,
    StoreConfiguration,
    TransactionalControlPlaneStore,
    canonical_digest,
    reset_transactional_store,
    s6bm_terminal_fence_record,
)


@pytest.fixture
def postgres_dsn() -> str:
    value = os.getenv("EVM_TEST_CONTROL_PLANE_DATABASE_URL")
    if not value:
        pytest.skip("real PostgreSQL test DSN is not configured")
    return value


@pytest.fixture
def store(postgres_dsn: str):
    schema = f"evm_s1_test_{uuid4().hex[:12]}"
    instance = TransactionalControlPlaneStore(
        StoreConfiguration(
            mode="postgres",
            dsn=postgres_dsn,
            schema=schema,
            pool_min_size=1,
            pool_max_size=4,
            acquire_timeout_seconds=0.5,
        )
    )
    try:
        yield instance
    finally:
        instance.close()
        import psycopg

        with psycopg.connect(postgres_dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def s6bm_transition_context(
    identity: dict[str, object],
    *,
    continuity_receipt_request_ids: list[str] | None = None,
    continuity_crossover_request_ids: list[str] | None = None,
    pending_crossover_request_ids: list[str] | None = None,
    continuity_terminal_request_ids: list[str] | None = None,
    continuity_terminal_records_sha256: str | None = None,
) -> dict[str, object]:
    terminal_ids = continuity_terminal_request_ids or []
    pending_ids = (
        [str(identity["request_id"])]
        if pending_crossover_request_ids is None
        else pending_crossover_request_ids
    )
    source_payload = {
        "run_id": identity["run_id"],
        "action": "green_switched",
        "expected_generation": identity["route_generation"],
        "causal_crossover": identity,
        "continuity_receipt_request_ids": continuity_receipt_request_ids or [],
        "continuity_crossover_request_ids": continuity_crossover_request_ids or [],
        "pending_crossover_request_ids": pending_ids,
        "continuity_terminal_request_ids": terminal_ids,
        "continuity_terminal_request_set_sha256": (
            canonical_digest(terminal_ids) if terminal_ids else None
        ),
        "continuity_terminal_records_sha256": continuity_terminal_records_sha256,
    }
    source_payload_sha256 = canonical_digest(source_payload)
    core = {
        "attempt_id": identity["attempt_id"],
        "run_id": identity["run_id"],
        "request_id": identity["request_id"],
        "action": "green_switched",
        "old_route_generation": identity["route_generation"],
        "new_route_generation": int(identity["route_generation"]) + 1,
        "source_payload_sha256": source_payload_sha256,
        "source_revision": "a" * 40,
        "cell_id": identity["attempt_id"],
        "replica_id": "s6bm-test-replica",
    }
    transition_id = canonical_digest(
        {"schema_version": "evm.s6bm.route_transition_identity.v1", **core}
    )
    return {
        "schema_version": "evm.s6bm.route_transition_context.v1",
        **core,
        "transition_id": transition_id,
        "fence_id": canonical_digest(
            {
                "schema_version": "evm.s6bm.route_fence_identity.v1",
                "transition_id": transition_id,
                "attempt_id": identity["attempt_id"],
                "request_id": identity["request_id"],
            }
        ),
        "source_payload": source_payload,
        "actor": {
            "actor_identity": "api-control-plane-route-switch",
            "process_id": os.getpid(),
            "thread_id": threading.get_ident(),
            "source_revision": "a" * 40,
            "service_instance_id": "s6bm-test-replica",
        },
    }


def test_entity_optimistic_version_and_idempotency(store: TransactionalControlPlaneStore):
    original = {"run_id": "run-1", "version": 1, "state": "queued"}
    store.insert_entity(
        "lifecycle_run",
        "run-1",
        original,
        state="queued",
        version=1,
    )

    updated = store.mutate_entity(
        "lifecycle_run",
        "run-1",
        expected_version=1,
        fallback_payload=None,
        mutate=lambda payload: {**payload, "version": 2, "state": "cancelled"},
    )
    assert updated["state"] == "cancelled"
    with pytest.raises(ControlPlaneVersionConflict):
        store.mutate_entity(
            "lifecycle_run",
            "run-1",
            expected_version=1,
            fallback_payload=None,
            mutate=lambda payload: {**payload, "version": 2, "state": "failed"},
        )

    request = {"run_id": "run-1", "action": "cancel"}
    store.record_idempotency(
        "lifecycle.cancel",
        "cancel-key-0001",
        request,
        updated,
        entity_kind="lifecycle_run",
        entity_id="run-1",
    )
    assert store.lookup_idempotency("lifecycle.cancel", "cancel-key-0001", request) == updated


def test_idempotent_terminal_entity_commits_one_effect_in_real_postgres(
    store: TransactionalControlPlaneStore,
) -> None:
    request = {"logical_request_id": "s6-request-0001", "seed": 20260823}
    response = {
        "schema_version": "evm.api_rollout_probe.v1",
        "logical_request_id": "s6-request-0001",
        "effect_id": "a" * 64,
        "state": "completed",
    }

    first, first_replayed = store.commit_idempotent_terminal_entity(
        scope="s6.api-rollout-probe",
        idempotency_key="s6-request-0001",
        request_payload=request,
        entity_kind="s6_rollout_probe",
        entity_id="s6-request-0001",
        response_payload=response,
        state="completed",
    )
    replay, replayed = store.commit_idempotent_terminal_entity(
        scope="s6.api-rollout-probe",
        idempotency_key="s6-request-0001",
        request_payload=request,
        entity_kind="s6_rollout_probe",
        entity_id="s6-request-0001",
        response_payload={**response, "effect_id": "b" * 64},
        state="completed",
    )

    assert first_replayed is False
    assert replayed is True
    assert first == replay == response
    assert store.get_entity("s6_rollout_probe", "s6-request-0001") == response
    assert len(store.list_entities("s6_rollout_probe")) == 1
    with pytest.raises(ControlPlaneIdempotencyConflict):
        store.commit_idempotent_terminal_entity(
            scope="s6.api-rollout-probe",
            idempotency_key="s6-request-0001",
            request_payload={**request, "seed": 1},
            entity_kind="s6_rollout_probe",
            entity_id="s6-request-0001",
            response_payload=response,
            state="completed",
        )


def test_generic_terminal_receipt_does_not_require_s6bm_causal_identity(
    store: TransactionalControlPlaneStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("generic receipt entered the S6B-M clock-anchor path")

    monkeypatch.setattr(store, "_collect_s6bm_commit_timestamp_receipt", fail_if_called)
    request = {"request_id": "x1-generic-request-0001"}
    response = {
        "schema_version": "evm.s8_v4.x1_terminal_effect.v1",
        "attempt_id": "x1-generic-attempt-0001",
        "request_id": request["request_id"],
        "effect_id": "a" * 64,
        "terminal_outcome": "completed",
    }

    stored, replayed, receipt = store.commit_idempotent_terminal_entity_with_receipt(
        scope="x1.terminal-effect.x1-generic-attempt-0001",
        idempotency_key=request["request_id"],
        request_payload=request,
        entity_kind="x1_terminal_effect",
        entity_id=response["effect_id"],
        response_payload=response,
        state="completed",
    )

    assert replayed is False
    assert stored["request_id"] == request["request_id"]
    assert stored["durable_commit"]["schema_version"] == "evm.control_plane.durable_commit.v1"
    assert stored["durable_commit"]["causal_sequence"] is None
    assert receipt["schema_version"] == "evm.control_plane.durable_effect_receipt.v1"
    assert receipt["readback_visible"] is True
    assert receipt["commit_timestamp_required"] is False
    assert receipt["separate_transaction_readback"] is True
    assert receipt["readback_transaction_id"] != receipt["transaction_id"]
    assert receipt["causal_sequence"] is None
    assert receipt["causal_payload_sha256"] is None


def test_generic_terminal_receipt_commits_frozen_concurrency_without_clock_lane_starvation(
    postgres_dsn: str,
) -> None:
    schema = f"evm_x1_generic_concurrency_{uuid4().hex[:12]}"
    instance = TransactionalControlPlaneStore(
        StoreConfiguration(
            mode="postgres",
            dsn=postgres_dsn,
            schema=schema,
            pool_min_size=1,
            pool_max_size=8,
            acquire_timeout_seconds=2.0,
            commit_timestamp_readback_max_concurrency=2,
            commit_timestamp_readback_acquire_timeout_seconds=0.01,
        )
    )
    barrier = threading.Barrier(16)

    def commit(index: int) -> dict[str, object]:
        request_id = f"x1-generic-concurrency-{index:02d}"
        effect_id = hashlib.sha256(f"{request_id}:effect".encode("ascii")).hexdigest()
        barrier.wait(timeout=5)
        stored, replayed, receipt = instance.commit_idempotent_terminal_entity_with_receipt(
            scope="x1.terminal-effect.x1-generic-concurrency",
            idempotency_key=request_id,
            request_payload={"request_id": request_id},
            entity_kind="x1_terminal_effect",
            entity_id=effect_id,
            response_payload={
                "schema_version": "evm.s8_v4.x1_terminal_effect.v1",
                "attempt_id": "x1-generic-concurrency",
                "request_id": request_id,
                "effect_id": effect_id,
                "terminal_outcome": "completed",
            },
            state="completed",
        )
        assert replayed is False
        assert stored["request_id"] == request_id
        return receipt

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            receipts = list(pool.map(commit, range(16)))
        assert len(receipts) == 16
        assert len({receipt["entity_id"] for receipt in receipts}) == 16
        assert all(receipt["readback_visible"] is True for receipt in receipts)
        assert all(receipt["separate_transaction_readback"] is True for receipt in receipts)
        assert all(
            receipt["readback_transaction_id"] != receipt["transaction_id"] for receipt in receipts
        )
        assert len(instance.list_entities("x1_terminal_effect")) == 16
        assert instance.telemetry().timeouts == 0
        readback = instance.commit_timestamp_readback_telemetry()
        assert readback.acquisitions == 0
        assert readback.timeouts == 0
        pool_stats = instance._pool.get_stats()
        assert pool_stats["requests_waiting"] == 0
        assert pool_stats["pool_available"] == pool_stats["pool_size"]
    finally:
        instance.close()
        import psycopg

        with psycopg.connect(postgres_dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def test_s6bm_causal_fence_effect_and_unload_are_ordered_in_real_postgres(
    store: TransactionalControlPlaneStore,
) -> None:
    identity = {
        "attempt_id": "s6bm-success-1-causal",
        "run_id": "s8-v4-s6bm-causal-test",
        "request_id": "s6bm-crossover-request-0001",
        "request_nonce": "nonce-crossover-0001",
        "trace_id": "1" * 32,
        "effect_id": "2" * 64,
        "model_role": "blue",
        "model_name": "s6bm_blue",
        "model_version": "1",
        "artifact_sha256": "3" * 64,
        "route_generation": 3,
    }
    for index, stage in enumerate(
        (
            "api_server_handler_entry",
            "controller_entry",
            "triton_backend_compute_entry",
        ),
        start=1,
    ):
        before = time.perf_counter_ns()
        anchor = {
            "monotonic_before_ns": before,
            "monotonic_after_ns": time.perf_counter_ns(),
        }
        receipt = store.commit_s6bm_start_receipt(
            event_type=stage,
            payload={
                **identity,
                "actor_start_unix_ns": time.time_ns() - (10_000_000 - index),
                "stage": stage,
                **(anchor if stage != "triton_backend_compute_entry" else {}),
                **(
                    {"collector_observation": anchor}
                    if stage == "triton_backend_compute_entry"
                    else {}
                ),
            },
            actor_identity=f"actor-{index}",
        )
        assert receipt["readback_visible"] is True

    switch = store.commit_s6bm_route_switch_fence(
        crossover_identity=identity,
        transition_context=s6bm_transition_context(identity),
    )
    assert switch["event_type"] == "blue_to_green_switch_commit"
    assert switch["schema_version"] == "evm.s6bm.route_switch_receipt.v2"
    assert switch["old_route_generation"] == 3
    assert switch["new_route_generation"] == 4
    assert switch["actor_process_id"] == os.getpid()
    assert switch["actor_thread_id"] == threading.get_ident()
    assert (
        switch["commit_ack_monotonic_ns"]
        <= switch["readback_started_monotonic_ns"]
        <= switch["readback_finished_monotonic_ns"]
    )
    effect_payload = {
        **identity,
        "schema_version": "evm.s8_v4.s6bm_terminal_causal_event.v1",
        "actor_start_unix_ns": time.time_ns(),
        "result_sha256": "4" * 64,
        "requires_switch_before_effect": True,
    }
    stored, replayed, effect = store.commit_idempotent_terminal_entity_with_receipt(
        scope=f"s6bm.terminal-effect.{identity['attempt_id']}",
        idempotency_key=identity["request_id"],
        request_payload={"request_id": identity["request_id"], "seed": 20260825},
        entity_kind="s6bm_terminal_effect",
        entity_id=identity["effect_id"],
        response_payload={**identity, "terminal_outcome": "completed"},
        state="completed",
        causal_payload=effect_payload,
    )
    assert replayed is False
    assert stored["durable_commit"]["causal_sequence"] == effect["causal_sequence"]
    assert stored["durable_commit"]["schema_version"] == "evm.s6bm.durable_commit.v3"
    assert stored["durable_commit"]["transaction_id"] == effect["transaction_id"]
    assert stored["durable_commit"]["write_backend_pid"] == effect["write_backend_pid"]
    assert effect["schema_version"] == "evm.s6bm.durable_effect_receipt.v4"
    assert effect["commit_timestamp_tracking"] == "on"
    assert effect["commit_timestamp_visible"] is True
    assert effect["separate_connection_readback"] is True
    assert effect["commit_timestamp_readback_lane"] == "bounded_parallel_post_commit_v1"
    assert effect["commit_timestamp_readback_concurrency_limit"] == 2
    assert 1 <= effect["commit_timestamp_readback_in_flight_at_acquire"] <= 2
    assert 1 <= effect["commit_timestamp_readback_max_in_flight_observed"] <= 2
    assert effect["commit_timestamp_readback_wait_seconds"] < 2.0
    assert effect["commit_timestamp_backend_pid"] != effect["write_backend_pid"]
    database_anchor = effect["database_clock_anchor"]
    assert database_anchor["schema_version"] == "evm.s6bm.database_clock_anchor.v2"
    assert database_anchor["backend_pid"] == effect["commit_timestamp_backend_pid"]
    assert database_anchor["database_clock_timestamp"] == effect["commit_timestamp_observed_at"]
    assert database_anchor["monotonic_before_ns"] <= database_anchor["monotonic_after_ns"]
    assert database_anchor["anchor_hash"] == canonical_digest(
        {key: value for key, value in database_anchor.items() if key != "anchor_hash"}
    )
    candidates = effect["database_clock_anchor_candidates"]
    assert [candidate["sequence"] for candidate in candidates] == list(range(1, 9))
    assert len({candidate["anchor_nonce"] for candidate in candidates}) == 8
    assert database_anchor == min(
        candidates,
        key=lambda candidate: (
            candidate["monotonic_after_ns"] - candidate["monotonic_before_ns"],
            candidate["sequence"],
        ),
    )
    assert (
        database_anchor["monotonic_after_ns"] - database_anchor["monotonic_before_ns"] <= 1_000_000
    )
    assert switch["causal_sequence"] < effect["causal_sequence"]
    assert stored["observed_transition"] == effect["observed_transition"]
    assert stored["durable_commit"]["observed_transition"] == effect["observed_transition"]
    assert effect["transition_readback_visible"] is True
    replayed_stored, replayed, replayed_effect = (
        store.commit_idempotent_terminal_entity_with_receipt(
            scope=f"s6bm.terminal-effect.{identity['attempt_id']}",
            idempotency_key=identity["request_id"],
            request_payload={"request_id": identity["request_id"], "seed": 20260825},
            entity_kind="s6bm_terminal_effect",
            entity_id=identity["effect_id"],
            response_payload={**identity, "terminal_outcome": "completed"},
            state="completed",
            causal_payload=effect_payload,
        )
    )
    assert replayed is True
    assert replayed_stored == stored
    assert replayed_effect["causal_sequence"] == effect["causal_sequence"]
    unload = store.commit_s6bm_unload_intent(
        crossover_identity=identity,
        pre_switch_blue_effects=[
            {"request_id": identity["request_id"], "effect_id": identity["effect_id"]}
        ],
    )
    assert effect["causal_sequence"] < unload["causal_sequence"]
    events = store.list_s6bm_causal_events(attempt_id=identity["attempt_id"])
    assert [event["event_type"] for event in events] == [
        "api_server_handler_entry",
        "controller_entry",
        "triton_backend_compute_entry",
        "blue_to_green_switch_commit",
        "durable_terminal_effect_commit",
        "blue_unload_intent",
    ]
    switch_event = events[3]
    effect_event = events[4]
    observed = stored["observed_transition"]
    assert observed["transition_id"] == switch_event["payload"]["transition_id"]
    assert observed["fence_id"] == switch_event["payload"]["fence_id"]
    assert observed["fence_sequence"] == switch_event["causal_sequence"]
    assert observed["fence_transaction_id"] == switch_event["transaction_id"]
    assert observed["fence_payload_sha256"] == switch_event["payload_sha256"]
    assert effect_event["payload"]["observed_transition"] == observed
    assert effect_event["transaction_id"] == stored["durable_commit"]["transaction_id"]
    assert effect_event["transaction_id"] != switch_event["transaction_id"]


def test_s6bm_bridge_actor_receipts_are_visible_before_switch_in_real_postgres(
    store: TransactionalControlPlaneStore,
) -> None:
    attempt_id = "s6bm-success-bridge-gate"
    run_id = "s8-v4-s6bm-bridge-gate"
    stages = (
        "api_server_handler_entry",
        "controller_entry",
        "triton_backend_compute_entry",
    )

    def identity(request_id: str, ordinal: int) -> dict[str, object]:
        return {
            "attempt_id": attempt_id,
            "run_id": run_id,
            "request_id": request_id,
            "request_nonce": f"nonce-{ordinal:04d}",
            "trace_id": f"{ordinal + 1:032x}",
            "effect_id": f"{ordinal + 1:064x}",
            "model_role": "blue",
            "model_name": "s6bm_blue",
            "model_version": "1",
            "artifact_sha256": "3" * 64,
            "route_generation": 3,
        }

    hold = identity("s6bm-hold", 0)
    bridge_ids = [f"s6bm-bridge-{index}" for index in range(4)]
    bridge_receipts: list[dict[str, object]] = []
    for ordinal, current in enumerate(
        [hold, *(identity(request_id, index + 1) for index, request_id in enumerate(bridge_ids))]
    ):
        for stage in stages:
            before = time.perf_counter_ns()
            receipt = store.commit_s6bm_start_receipt(
                event_type=stage,
                payload={
                    **current,
                    "actor_start_unix_ns": time.time_ns(),
                    **(
                        {
                            "collector_observation": {
                                "monotonic_before_ns": before,
                                "monotonic_after_ns": time.perf_counter_ns(),
                            }
                        }
                        if stage == "triton_backend_compute_entry"
                        else {
                            "monotonic_before_ns": before,
                            "monotonic_after_ns": time.perf_counter_ns(),
                        }
                    ),
                    "actor_ordinal": ordinal,
                },
                actor_identity=f"bridge-actor-{ordinal}-{stage}",
            )
            assert receipt["readback_visible"] is True
            if current["request_id"] in bridge_ids:
                bridge_receipts.append(receipt)

    terminal_ids = [f"s6bm-bridge-terminal-{index:02d}" for index in range(39)]
    for ordinal, request_id in enumerate(terminal_ids, start=100):
        effect_id = hashlib.sha256(f"{attempt_id}:{request_id}".encode("ascii")).hexdigest()
        trace_id = f"{ordinal + 1:032x}"
        result_sha256 = hashlib.sha256(f"result:{request_id}".encode("ascii")).hexdigest()
        effect_identity = {
            "attempt_id": attempt_id,
            "run_id": run_id,
            "request_id": request_id,
            "request_nonce": f"nonce-{ordinal:04d}",
            "trace_id": trace_id,
            "effect_id": effect_id,
            "model_role": "blue",
            "model_name": "s6bm_blue",
            "model_version": "1",
            "artifact_sha256": "3" * 64,
            "route_generation": 3,
        }
        response_payload = {
            "schema_version": "evm.s8_v4.s6bm_terminal_effect.v1",
            "run_id": run_id,
            "attempt_id": attempt_id,
            "request_id": request_id,
            "trace_id": trace_id,
            "effect_id": effect_id,
            "served_identity": {
                "model_role": "blue",
                "model_name": "s6bm_blue",
                "model_version": "1",
                "artifact_sha256": "3" * 64,
            },
            "route_generation": 3,
            "result_sha256": result_sha256,
            "terminal_outcome": "completed",
        }
        store.commit_idempotent_terminal_entity_with_receipt(
            scope=f"s6bm.terminal-effect.{attempt_id}",
            idempotency_key=request_id,
            request_payload={"request_id": request_id, "ordinal": ordinal},
            entity_kind="s6bm_terminal_effect",
            entity_id=effect_id,
            response_payload=response_payload,
            state="completed",
            causal_payload={
                **effect_identity,
                "schema_version": "evm.s8_v4.s6bm_terminal_causal_event.v1",
                "result_sha256": result_sha256,
                "requires_switch_before_effect": False,
            },
        )
    terminal_entities = {
        str(item["idempotency_key"]): item
        for item in store.list_idempotent_terminal_entities(
            entity_kind="s6bm_terminal_effect", attempt_id=attempt_id
        )
    }
    terminal_events = {
        str(item["request_id"]): item
        for item in store.list_s6bm_causal_events(attempt_id=attempt_id)
        if item["event_type"] == "durable_terminal_effect_commit"
    }
    terminal_records = [
        s6bm_terminal_fence_record(terminal_entities[request_id], terminal_events[request_id])
        for request_id in terminal_ids
    ]
    terminal_records_sha256 = canonical_digest(terminal_records)

    missing_terminal_ids = [*terminal_ids[:-1], "s6bm-bridge-terminal-missing"]
    with pytest.raises(ControlPlaneParityError, match="terminal set is incomplete"):
        store.commit_s6bm_route_switch_fence(
            crossover_identity=hold,
            transition_context=s6bm_transition_context(
                hold,
                continuity_receipt_request_ids=bridge_ids,
                continuity_crossover_request_ids=[bridge_ids[0]],
                pending_crossover_request_ids=sorted([str(hold["request_id"]), bridge_ids[0]]),
                continuity_terminal_request_ids=missing_terminal_ids,
                continuity_terminal_records_sha256=terminal_records_sha256,
            ),
        )
    with pytest.raises(ControlPlaneParityError, match="terminal record hash mismatch"):
        store.commit_s6bm_route_switch_fence(
            crossover_identity=hold,
            transition_context=s6bm_transition_context(
                hold,
                continuity_receipt_request_ids=bridge_ids,
                continuity_crossover_request_ids=[bridge_ids[0]],
                pending_crossover_request_ids=sorted([str(hold["request_id"]), bridge_ids[0]]),
                continuity_terminal_request_ids=terminal_ids,
                continuity_terminal_records_sha256="f" * 64,
            ),
        )

    switch = store.commit_s6bm_route_switch_fence(
        crossover_identity=hold,
        transition_context=s6bm_transition_context(
            hold,
            continuity_receipt_request_ids=bridge_ids,
            continuity_crossover_request_ids=[bridge_ids[0]],
            pending_crossover_request_ids=sorted([str(hold["request_id"]), bridge_ids[0]]),
            continuity_terminal_request_ids=terminal_ids,
            continuity_terminal_records_sha256=terminal_records_sha256,
        ),
    )
    switch_recorded = datetime.fromisoformat(
        str(switch["database_recorded_at"]).replace("Z", "+00:00")
    )
    assert len(bridge_receipts) == 12
    assert switch["continuity_receipt_request_ids"] == bridge_ids
    assert switch["continuity_crossover_request_ids"] == [bridge_ids[0]]
    assert switch["pending_crossover_request_ids"] == sorted(
        [str(hold["request_id"]), bridge_ids[0]]
    )
    assert switch["continuity_terminal_request_ids"] == terminal_ids
    assert switch["continuity_terminal_request_set_sha256"] == canonical_digest(terminal_ids)
    assert switch["continuity_terminal_records_sha256"] == terminal_records_sha256
    assert len({receipt["transaction_id"] for receipt in bridge_receipts}) == 12
    assert all(
        receipt["causal_sequence"] < switch["causal_sequence"]
        and receipt["readback_visible"] is True
        and receipt["replayed"] is False
        and int(receipt["commit_ack_monotonic_ns"]) > 0
        and receipt["payload_sha256"] == canonical_digest(receipt["payload"])
        and receipt["attempt_id"] == attempt_id
        and receipt["run_id"] == run_id
        and receipt["request_id"] in bridge_ids
        and receipt["event_type"] in stages
        and receipt["model_role"] == "blue"
        and int(receipt["route_generation"]) == 3
        and datetime.fromisoformat(str(receipt["database_recorded_at"]).replace("Z", "+00:00"))
        < switch_recorded
        and datetime.fromisoformat(str(receipt["readback_at"]).replace("Z", "+00:00"))
        < switch_recorded
        for receipt in bridge_receipts
    )

    crossover = identity(bridge_ids[0], 1)
    crossover_result_sha256 = hashlib.sha256(
        f"result:{crossover['request_id']}".encode("ascii")
    ).hexdigest()
    crossover_response = {
        "schema_version": "evm.s8_v4.s6bm_terminal_effect.v1",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "request_id": crossover["request_id"],
        "trace_id": crossover["trace_id"],
        "effect_id": crossover["effect_id"],
        "served_identity": {
            "model_role": "blue",
            "model_name": "s6bm_blue",
            "model_version": "1",
            "artifact_sha256": "3" * 64,
        },
        "route_generation": 3,
        "result_sha256": crossover_result_sha256,
        "terminal_outcome": "completed",
    }
    stored, replayed, effect = store.commit_idempotent_terminal_entity_with_receipt(
        scope=f"s6bm.terminal-effect.{attempt_id}",
        idempotency_key=str(crossover["request_id"]),
        request_payload={"request_id": crossover["request_id"], "ordinal": 1},
        entity_kind="s6bm_terminal_effect",
        entity_id=str(crossover["effect_id"]),
        response_payload=crossover_response,
        state="completed",
        causal_payload={
            **crossover,
            "schema_version": "evm.s8_v4.s6bm_terminal_causal_event.v1",
            "result_sha256": crossover_result_sha256,
            "requires_switch_before_effect": True,
        },
    )
    assert replayed is False
    observed = effect["observed_transition"]
    assert observed["request_id"] == hold["request_id"]
    assert observed["transition_id"] == switch["transition_id"]
    assert observed["fence_sequence"] == switch["fence_sequence"]
    assert stored["observed_transition"] == observed
    assert effect["transition_readback_visible"] is True


def test_s6bm_cold_store_commits_frozen_concurrency_without_pool_starvation(
    postgres_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api import control_panel_workloads as api
    from evm.model_runtime.triton_blue_green import (
        TritonBlueGreenPredictRequest,
        TritonBlueGreenPredictResponse,
    )

    schema = f"evm_s6bm_cold_test_{uuid4().hex[:12]}"
    api.shutdown_s6bm_terminal_store()
    monkeypatch.setenv("EVM_S6BM_DATABASE_URL", postgres_dsn)
    monkeypatch.setenv("EVM_S6BM_DATABASE_SCHEMA", schema)
    monkeypatch.setenv("EVM_CONTROL_PLANE_AUTO_MIGRATE", "true")
    monkeypatch.setenv("EVM_S6BM_DATABASE_POOL_MAX_SIZE", "8")
    monkeypatch.setenv("EVM_S6BM_COMMIT_READBACK_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("EVM_S6BM_COMMIT_READBACK_ACQUIRE_TIMEOUT_SECONDS", "2")
    artifact_sha256 = "4" * 64
    barrier = threading.Barrier(16)

    def commit(index: int) -> dict[str, object]:
        request_id = f"s6bm-frozen-concurrency-{index:02d}"
        trace_id = hashlib.sha256(request_id.encode("ascii")).hexdigest()[:32]
        request = TritonBlueGreenPredictRequest(
            run_id="s8-v4-s6bm-cold-concurrency",
            lease_id="lease-test",
            fencing_token="fence-test",
            attempt_id="s6bm-frozen-concurrency",
            request_id=request_id,
            request_nonce=f"nonce-{index:08d}",
            traceparent=f"00-{trace_id}-0123456789abcdef-01",
            input_values=[1.0, 2.0, 3.0, 4.0],
            expected_model_role="blue",
            expected_model_name="s6bm_blue",
            expected_model_version="1",
            expected_artifact_sha256=artifact_sha256,
        )
        response = TritonBlueGreenPredictResponse(
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            request_id=request.request_id,
            trace_id=trace_id,
            effect_id=hashlib.sha256(f"{request_id}:effect".encode("ascii")).hexdigest(),
            route_generation=1,
            model_role="blue",
            model_name="s6bm_blue",
            model_version="1",
            artifact_sha256=artifact_sha256,
            route_phase="blue_only",
            output=[3.0, 5.0, 7.0, 9.0],
            result_sha256=hashlib.sha256(f"{request_id}:result".encode("ascii")).hexdigest(),
            elapsed_ms=1.0,
        )
        barrier.wait(timeout=5)
        return api._commit_s6bm_terminal_effect_sync(request, response)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            receipts = list(pool.map(commit, range(16)))
        store = api.initialize_s6bm_terminal_store()
        assert len(receipts) == 16
        assert len({receipt["entity_id"] for receipt in receipts}) == 16
        assert len({receipt["transaction_id"] for receipt in receipts}) == 16
        assert all(receipt["readback_visible"] is True for receipt in receipts)
        assert all(
            receipt["write_backend_pid"] != receipt["commit_timestamp_backend_pid"]
            for receipt in receipts
        )
        assert all(
            receipt["commit_timestamp_readback_concurrency_limit"] == 2 for receipt in receipts
        )
        assert all(receipt["commit_timestamp_readback_wait_seconds"] < 2.0 for receipt in receipts)
        selected_widths = [
            receipt["database_clock_anchor"]["monotonic_after_ns"]
            - receipt["database_clock_anchor"]["monotonic_before_ns"]
            for receipt in receipts
        ]
        assert max(selected_widths) <= 5_000_000
        assert len(store.list_entities("s6bm_terminal_effect")) == 16
        assert store.telemetry().timeouts == 0
        readback = store.commit_timestamp_readback_telemetry()
        assert readback.acquisitions == 16
        assert readback.timeouts == 0
        assert readback.in_flight == 0
        assert readback.max_in_flight == 2
        pool_stats = store._pool.get_stats()
        assert pool_stats["pool_max"] == 8
        assert pool_stats["requests_waiting"] == 0
        assert pool_stats["pool_available"] == pool_stats["pool_size"]
    finally:
        api.shutdown_s6bm_terminal_store()
        import psycopg

        with psycopg.connect(postgres_dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def test_s6bm_terminal_effect_rejects_forged_route_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api import control_panel_workloads as api
    from evm.model_runtime.triton_blue_green import (
        TritonBlueGreenPredictRequest,
        TritonBlueGreenPredictResponse,
    )

    request = TritonBlueGreenPredictRequest(
        run_id="s8-v4-s6bm-route-binding",
        lease_id="lease-route-binding",
        fencing_token="fence-route-binding",
        attempt_id="s6bm-route-binding-attempt",
        request_id="s6bm-route-binding-request",
        request_nonce="s6bm-route-binding-nonce",
        traceparent="00-" + "1" * 32 + "-" + "2" * 16 + "-01",
        input_values=[1, 2, 3, 4],
        expected_model_role="green",
        expected_model_name="s6bm_green",
        expected_model_version="1",
        expected_artifact_sha256="3" * 64,
        expected_route_generation=4,
    )
    response = TritonBlueGreenPredictResponse(
        run_id=request.run_id,
        attempt_id=request.attempt_id,
        request_id=request.request_id,
        trace_id="1" * 32,
        effect_id="4" * 64,
        route_generation=5,
        model_role="green",
        model_name="s6bm_green",
        model_version="1",
        artifact_sha256="3" * 64,
        route_phase="blue_draining",
        output=[4, 6, 8, 10],
        result_sha256="5" * 64,
        elapsed_ms=1,
    )
    monkeypatch.setattr(
        api,
        "_s6bm_terminal_store",
        lambda: pytest.fail("forged route revision reached durable storage"),
    )
    with pytest.raises(RuntimeError, match="request/response route revision parity"):
        api._commit_s6bm_terminal_effect_sync(request, response)


def test_s6bm_route_revision_restore_is_monotonic_and_aba_safe(
    store: TransactionalControlPlaneStore,
) -> None:
    from evm.model_runtime.triton_blue_green import (
        TritonBlueGreenInitializeRequest,
        TritonModelIdentity,
        active_route_identity_sha256,
    )

    request = TritonBlueGreenInitializeRequest(
        run_id="s8-v4-s6bm-route-store",
        source_revision="a" * 40,
        triton_http_url="http://127.0.0.1:18100",
        image_digest="sha256:" + "b" * 64,
        gpu_uuid="GPU-route-store",
        lease_id="lease-route-1",
        fencing_token="fence-route-1",
        blue=TritonModelIdentity(
            role="blue",
            model_name="s6bm_blue",
            model_version="1",
            artifact_sha256="c" * 64,
            config_sha256="d" * 64,
            expected_output=[3, 5, 7, 9],
        ),
        green=TritonModelIdentity(
            role="green",
            model_name="s6bm_green",
            model_version="1",
            artifact_sha256="e" * 64,
            config_sha256="f" * 64,
            expected_output=[4, 6, 8, 10],
        ),
    )

    def payload(
        current: TritonBlueGreenInitializeRequest,
        *,
        control: int,
        route: int,
        weights: dict[str, int],
        action: str,
        approval: str | None,
        approvals: list[str],
        changed: bool,
    ) -> dict[str, object]:
        return {
            "schema_version": "evm.s6bm.route_revision.v1",
            "run_id": current.run_id,
            "source_revision": current.source_revision,
            "control_generation": control,
            "route_generation": route,
            "phase": "canary" if weights == {"blue": 90, "green": 10} else "blue_only",
            "route_weights": weights,
            "loaded_roles": ["blue", "green"] if weights["green"] else ["blue"],
            "active_route_identity_sha256": active_route_identity_sha256(current, weights),
            "blue_identity_sha256": canonical_digest(current.blue.model_dump(mode="json")),
            "green_identity_sha256": canonical_digest(current.green.model_dump(mode="json")),
            "image_digest": current.image_digest,
            "gpu_uuid": current.gpu_uuid,
            "action": action,
            "approval_id": approval,
            "used_approvals": approvals,
            "route_changed": changed,
            "lease_id": current.lease_id,
            "fencing_token_sha256": hashlib.sha256(
                current.fencing_token.encode("utf-8")
            ).hexdigest(),
            "transition_id": None,
            "transition_new_route_generation": None,
        }

    initial = payload(
        request,
        control=1,
        route=1,
        weights={"blue": 100, "green": 0},
        action="initialized",
        approval=None,
        approvals=[],
        changed=True,
    )
    assert (
        store.restore_or_initialize_s6bm_route_revision(initial_payload=initial)["payload"]
        == initial
    )

    loaded = payload(
        request,
        control=2,
        route=1,
        weights={"blue": 100, "green": 0},
        action="green_loaded",
        approval="approval-green-loaded",
        approvals=["approval-green-loaded"],
        changed=False,
    )
    store.commit_s6bm_route_revision(
        previous_control_generation=1,
        previous_route_generation=1,
        payload=loaded,
    )
    canary = payload(
        request,
        control=3,
        route=3,
        weights={"blue": 90, "green": 10},
        action="canary_started",
        approval="approval-canary",
        approvals=["approval-canary", "approval-green-loaded"],
        changed=True,
    )
    store.commit_s6bm_route_revision(
        previous_control_generation=2,
        previous_route_generation=1,
        payload=canary,
    )

    rebound_lease = request.model_copy(
        update={"lease_id": "lease-route-2", "fencing_token": "fence-route-2"}
    )
    lease_receipt = store.restore_or_initialize_s6bm_route_revision(
        initial_payload=payload(
            rebound_lease,
            control=1,
            route=1,
            weights={"blue": 100, "green": 0},
            action="initialized",
            approval=None,
            approvals=[],
            changed=True,
        )
    )
    assert lease_receipt["payload"]["action"] == "lease_rebound"
    assert (
        lease_receipt["payload"]["control_generation"],
        lease_receipt["payload"]["route_generation"],
    ) == (4, 3)

    changed_blue = rebound_lease.model_copy(
        update={
            "blue": rebound_lease.blue.model_copy(
                update={"model_version": "2", "artifact_sha256": "1" * 64}
            )
        }
    )
    identity_receipt = store.restore_or_initialize_s6bm_route_revision(
        initial_payload=payload(
            changed_blue,
            control=1,
            route=1,
            weights={"blue": 100, "green": 0},
            action="initialized",
            approval=None,
            approvals=[],
            changed=True,
        )
    )
    assert identity_receipt["payload"]["action"] == "active_identity_rebound"
    assert (
        identity_receipt["payload"]["control_generation"],
        identity_receipt["payload"]["route_generation"],
    ) == (5, 5)

    original_again = request.model_copy(
        update={"lease_id": "lease-route-2", "fencing_token": "fence-route-2"}
    )
    aba_receipt = store.restore_or_initialize_s6bm_route_revision(
        initial_payload=payload(
            original_again,
            control=1,
            route=1,
            weights={"blue": 100, "green": 0},
            action="initialized",
            approval=None,
            approvals=[],
            changed=True,
        )
    )
    assert (
        aba_receipt["payload"]["control_generation"],
        aba_receipt["payload"]["route_generation"],
    ) == (6, 6)

    schema = store.configuration.schema
    with store.transaction("route_revision_history_test") as connection:
        rows = connection.execute(
            f"""
            SELECT control_generation, route_generation, route_changed
            FROM {schema}.s6bm_route_revisions
            WHERE run_id=%s ORDER BY control_generation
            """,
            (request.run_id,),
        ).fetchall()
    assert [int(row["control_generation"]) for row in rows] == [1, 2, 3, 4, 5, 6]
    assert [int(row["route_generation"]) for row in rows] == [1, 1, 3, 3, 5, 6]
    assert [bool(row["route_changed"]) for row in rows] == [True, False, True, False, True, True]


def test_s6bm_terminal_effect_binds_switch_route_revision_and_lease_fence(
    store: TransactionalControlPlaneStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api import control_panel_workloads as api
    from evm.model_runtime.triton_blue_green import (
        TritonBlueGreenInitializeRequest,
        TritonBlueGreenPredictRequest,
        TritonBlueGreenPredictResponse,
        TritonModelIdentity,
        active_route_identity_sha256,
        model_identity_sha256,
    )

    initialize = TritonBlueGreenInitializeRequest(
        run_id="s8-v4-s6bm-route-effect-binding",
        source_revision="a" * 40,
        triton_http_url="http://127.0.0.1:18100",
        image_digest="sha256:" + "b" * 64,
        gpu_uuid="GPU-route-effect-binding",
        lease_id="lease-route-effect-binding",
        fencing_token="fence-route-effect-binding",
        blue=TritonModelIdentity(
            role="blue",
            model_name="s6bm_blue",
            model_version="1",
            artifact_sha256="c" * 64,
            config_sha256="d" * 64,
            expected_output=[3, 5, 7, 9],
        ),
        green=TritonModelIdentity(
            role="green",
            model_name="s6bm_green",
            model_version="1",
            artifact_sha256="e" * 64,
            config_sha256="f" * 64,
            expected_output=[4, 6, 8, 10],
        ),
    )

    def revision(
        *,
        control: int,
        route: int,
        phase: str,
        weights: dict[str, int],
        action: str,
        approval: str | None,
        approvals: list[str],
        changed: bool,
        transition: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "schema_version": "evm.s6bm.route_revision.v1",
            "run_id": initialize.run_id,
            "source_revision": initialize.source_revision,
            "control_generation": control,
            "route_generation": route,
            "phase": phase,
            "route_weights": weights,
            "loaded_roles": ["blue", "green"] if control > 1 else ["blue"],
            "active_route_identity_sha256": active_route_identity_sha256(initialize, weights),
            "blue_identity_sha256": model_identity_sha256(initialize.blue),
            "green_identity_sha256": model_identity_sha256(initialize.green),
            "image_digest": initialize.image_digest,
            "gpu_uuid": initialize.gpu_uuid,
            "action": action,
            "approval_id": approval,
            "used_approvals": approvals,
            "route_changed": changed,
            "lease_id": initialize.lease_id,
            "fencing_token_sha256": hashlib.sha256(
                initialize.fencing_token.encode("utf-8")
            ).hexdigest(),
            "transition_id": transition["transition_id"] if transition else None,
            "transition_new_route_generation": (
                transition["new_route_generation"] if transition else None
            ),
        }

    initial = revision(
        control=1,
        route=1,
        phase="blue_only",
        weights={"blue": 100, "green": 0},
        action="initialized",
        approval=None,
        approvals=[],
        changed=True,
    )
    store.restore_or_initialize_s6bm_route_revision(initial_payload=initial)
    approvals = ["approval-load"]
    store.commit_s6bm_route_revision(
        previous_control_generation=1,
        previous_route_generation=1,
        payload=revision(
            control=2,
            route=1,
            phase="green_loaded",
            weights={"blue": 100, "green": 0},
            action="green_loaded",
            approval="approval-load",
            approvals=approvals,
            changed=False,
        ),
    )
    approvals = sorted([*approvals, "approval-canary"])
    store.commit_s6bm_route_revision(
        previous_control_generation=2,
        previous_route_generation=1,
        payload=revision(
            control=3,
            route=3,
            phase="canary",
            weights={"blue": 90, "green": 10},
            action="canary_started",
            approval="approval-canary",
            approvals=approvals,
            changed=True,
        ),
    )
    crossover = {
        "attempt_id": "s6bm-route-effect-crossover",
        "run_id": initialize.run_id,
        "request_id": "s6bm-route-effect-hold",
        "request_nonce": "nonce-route-effect-hold",
        "trace_id": "1" * 32,
        "effect_id": "2" * 64,
        "model_role": "blue",
        "model_name": initialize.blue.model_name,
        "model_version": initialize.blue.model_version,
        "artifact_sha256": initialize.blue.artifact_sha256,
        "route_generation": 3,
    }
    for index, stage in enumerate(
        ("api_server_handler_entry", "controller_entry", "triton_backend_compute_entry"),
        start=1,
    ):
        before = time.perf_counter_ns()
        anchor = {
            "monotonic_before_ns": before,
            "monotonic_after_ns": time.perf_counter_ns(),
        }
        store.commit_s6bm_start_receipt(
            event_type=stage,
            payload={
                **crossover,
                "actor_start_unix_ns": time.time_ns() - (10_000_000 - index),
                "stage": stage,
                **(anchor if stage != "triton_backend_compute_entry" else {}),
                **(
                    {"collector_observation": anchor}
                    if stage == "triton_backend_compute_entry"
                    else {}
                ),
            },
            actor_identity=f"route-effect-actor-{index}",
        )
    switch = store.commit_s6bm_route_switch_fence(
        crossover_identity=crossover,
        transition_context=s6bm_transition_context(crossover),
    )
    approvals = sorted([*approvals, "approval-switch"])
    store.commit_s6bm_route_revision(
        previous_control_generation=3,
        previous_route_generation=3,
        payload=revision(
            control=4,
            route=4,
            phase="green_active",
            weights={"blue": 0, "green": 100},
            action="green_switched",
            approval="approval-switch",
            approvals=approvals,
            changed=True,
            transition=switch,
        ),
    )
    approvals = sorted([*approvals, "approval-drain"])
    store.commit_s6bm_route_revision(
        previous_control_generation=4,
        previous_route_generation=4,
        payload=revision(
            control=5,
            route=4,
            phase="blue_draining",
            weights={"blue": 0, "green": 100},
            action="blue_drain_started",
            approval="approval-drain",
            approvals=approvals,
            changed=False,
        ),
    )

    monkeypatch.setattr(api, "_s6bm_terminal_store", lambda: store)
    request = TritonBlueGreenPredictRequest(
        run_id=initialize.run_id,
        lease_id=initialize.lease_id,
        fencing_token=initialize.fencing_token,
        attempt_id="s6bm-route-effect-request-attempt",
        request_id="s6bm-route-effect-request-0001",
        request_nonce="nonce-route-effect-request-0001",
        traceparent="00-" + "3" * 32 + "-" + "4" * 16 + "-01",
        input_values=[1, 2, 3, 4],
        expected_model_role="green",
        expected_model_name=initialize.green.model_name,
        expected_model_version=initialize.green.model_version,
        expected_artifact_sha256=initialize.green.artifact_sha256,
        expected_route_generation=4,
    )
    response = TritonBlueGreenPredictResponse(
        run_id=request.run_id,
        attempt_id=request.attempt_id,
        request_id=request.request_id,
        trace_id="3" * 32,
        effect_id="5" * 64,
        route_generation=4,
        model_role="green",
        model_name=initialize.green.model_name,
        model_version=initialize.green.model_version,
        artifact_sha256=initialize.green.artifact_sha256,
        route_identity_sha256=model_identity_sha256(initialize.green),
        route_phase="blue_draining",
        output=[4, 6, 8, 10],
        result_sha256="6" * 64,
        elapsed_ms=1,
    )
    receipt = api._commit_s6bm_terminal_effect_sync(request, response)
    observed = receipt["observed_route_revision"]
    assert observed["route_generation"] == 4
    assert observed["route_source_control_generation"] == 4
    assert observed["route_source_action"] == "green_switched"
    assert observed["transition_id"] == switch["transition_id"]
    assert observed["lease_binding_control_generation"] == 5
    assert receipt["route_revision_readback_visible"] is True

    crossover_request = TritonBlueGreenPredictRequest(
        run_id=initialize.run_id,
        lease_id=initialize.lease_id,
        fencing_token=initialize.fencing_token,
        attempt_id=crossover["attempt_id"],
        request_id=crossover["request_id"],
        request_nonce=crossover["request_nonce"],
        traceparent="00-" + crossover["trace_id"] + "-" + "2" * 16 + "-01",
        input_values=[1, 2, 3, 4],
        hold_ms=1,
        expected_model_role="blue",
        expected_model_name=initialize.blue.model_name,
        expected_model_version=initialize.blue.model_version,
        expected_artifact_sha256=initialize.blue.artifact_sha256,
        expected_route_generation=3,
        causal_crossover=True,
    )
    crossover_response = TritonBlueGreenPredictResponse(
        run_id=crossover_request.run_id,
        attempt_id=crossover_request.attempt_id,
        request_id=crossover_request.request_id,
        trace_id=crossover["trace_id"],
        effect_id=crossover["effect_id"],
        route_generation=3,
        model_role="blue",
        model_name=initialize.blue.model_name,
        model_version=initialize.blue.model_version,
        artifact_sha256=initialize.blue.artifact_sha256,
        route_identity_sha256=model_identity_sha256(initialize.blue),
        route_phase="canary",
        output=[3, 5, 7, 9],
        result_sha256="b" * 64,
        elapsed_ms=1,
    )
    crossover_receipt = api._commit_s6bm_terminal_effect_sync(
        crossover_request,
        crossover_response,
    )
    crossover_revision = crossover_receipt["observed_route_revision"]
    assert crossover_revision["route_generation"] == 3
    assert crossover_revision["route_source_control_generation"] == 3
    assert crossover_revision["route_source_action"] == "canary_started"
    assert crossover_revision["lease_binding_control_generation"] == 5
    assert crossover_revision["lease_binding_payload"]["route_generation"] == 4
    assert crossover_revision["lease_binding_payload"]["action"] == "blue_drain_started"
    assert crossover_receipt["observed_transition"]["transition_id"] == switch["transition_id"]

    forged_identity = response.model_copy(
        update={
            "request_id": "s6bm-route-effect-request-0002",
            "effect_id": "7" * 64,
            "route_identity_sha256": "8" * 64,
        }
    )
    with pytest.raises(ControlPlaneParityError, match="route revision model binding"):
        api._commit_s6bm_terminal_effect_sync(
            request.model_copy(update={"request_id": forged_identity.request_id}),
            forged_identity,
        )

    forged_route = response.model_copy(
        update={
            "request_id": "s6bm-route-effect-request-0003",
            "effect_id": "9" * 64,
            "route_generation": 5,
        }
    )
    with pytest.raises(ControlPlaneParityError, match="route revision source is ambiguous"):
        api._commit_s6bm_terminal_effect_sync(
            request.model_copy(
                update={"request_id": forged_route.request_id, "expected_route_generation": 5}
            ),
            forged_route,
        )

    stale_lease_request = request.model_copy(
        update={
            "request_id": "s6bm-route-effect-request-0004",
            "fencing_token": "stale-fence-route-effect-binding",
        }
    )
    with pytest.raises(ControlPlaneParityError, match="effect current lease fence changed"):
        api._commit_s6bm_terminal_effect_sync(
            stale_lease_request,
            response.model_copy(
                update={
                    "request_id": stale_lease_request.request_id,
                    "effect_id": "a" * 64,
                }
            ),
        )


def test_s6bm_manager_reinitializes_from_durable_route_revision(
    store: TransactionalControlPlaneStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evm.model_runtime.triton_blue_green import (
        TritonBlueGreenControlRequest,
        TritonBlueGreenInitializeRequest,
        TritonBlueGreenManager,
        TritonModelIdentity,
        action_digest,
    )

    monkeypatch.setenv("EVM_S6BM_ENABLED", "1")
    manager = TritonBlueGreenManager()
    monkeypatch.setattr(manager, "_assert_lease", lambda *_args, **_kwargs: None)
    request = TritonBlueGreenInitializeRequest(
        run_id="s8-v4-s6bm-route-restore",
        source_revision="a" * 40,
        triton_http_url="http://127.0.0.1:18100",
        image_digest="sha256:" + "b" * 64,
        gpu_uuid="GPU-route-restore",
        lease_id="lease-route-restore",
        fencing_token="fence-route-restore",
        blue=TritonModelIdentity(
            role="blue",
            model_name="s6bm_blue",
            model_version="1",
            artifact_sha256="c" * 64,
            config_sha256="d" * 64,
            expected_output=[3, 5, 7, 9],
        ),
        green=TritonModelIdentity(
            role="green",
            model_name="s6bm_green",
            model_version="1",
            artifact_sha256="e" * 64,
            config_sha256="f" * 64,
            expected_output=[4, 6, 8, 10],
        ),
    )

    def restore(
        _request: TritonBlueGreenInitializeRequest, payload: dict[str, object]
    ) -> dict[str, object]:
        return store.restore_or_initialize_s6bm_route_revision(initial_payload=payload)

    def commit(
        _request: TritonBlueGreenControlRequest, context: dict[str, object]
    ) -> dict[str, object]:
        return store.commit_s6bm_route_revision(
            previous_control_generation=int(context["previous_control_generation"]),
            previous_route_generation=int(context["previous_route_generation"]),
            payload=dict(context["payload"]),
        )

    def control(action: str) -> None:
        value = TritonBlueGreenControlRequest(
            run_id=request.run_id,
            action=action,
            expected_generation=manager.snapshot().generation,
            lease_id=request.lease_id,
            fencing_token=request.fencing_token,
            blue_artifact_sha256=request.blue.artifact_sha256,
            green_artifact_sha256=request.green.artifact_sha256,
            approval_id=f"approval-{action}-{manager.snapshot().generation}",
            action_digest="0" * 64,
        )
        value = value.model_copy(update={"action_digest": action_digest(value)})
        manager.control(value, route_state_committer=commit)

    manager.initialize(request, route_state_restorer=restore)
    control("green_loaded")
    control("canary_started")
    control("green_aborted")
    assert (
        manager.snapshot().generation,
        manager.snapshot().route_generation,
        manager.snapshot().phase,
    ) == (4, 4, "blue_only")
    manager.reset(request.run_id, request.lease_id, request.fencing_token)
    restored = manager.initialize(request, route_state_restorer=restore)
    assert (restored.generation, restored.route_generation, restored.phase) == (4, 4, "blue_only")

    manager.reset(request.run_id, request.lease_id, request.fencing_token)
    changed = request.model_copy(
        update={
            "blue": request.blue.model_copy(
                update={"model_version": "2", "artifact_sha256": "1" * 64}
            )
        }
    )
    rebound = manager.initialize(changed, route_state_restorer=restore)
    assert (rebound.generation, rebound.route_generation) == (5, 5)


def test_s6bm_causal_fence_rejects_missing_receipt_and_early_effect(
    store: TransactionalControlPlaneStore,
) -> None:
    identity = {
        "attempt_id": "s6bm-success-2-negative",
        "run_id": "s8-v4-s6bm-causal-negative",
        "request_id": "s6bm-crossover-request-0002",
        "request_nonce": "nonce-crossover-0002",
        "trace_id": "5" * 32,
        "effect_id": "6" * 64,
        "model_role": "blue",
        "model_name": "s6bm_blue",
        "model_version": "1",
        "artifact_sha256": "7" * 64,
        "route_generation": 3,
    }
    for stage in ("api_server_handler_entry", "controller_entry"):
        before = time.perf_counter_ns()
        store.commit_s6bm_start_receipt(
            event_type=stage,
            payload={
                **identity,
                "actor_start_unix_ns": time.time_ns(),
                "monotonic_before_ns": before,
                "monotonic_after_ns": time.perf_counter_ns(),
                "stage": stage,
            },
            actor_identity=stage,
        )
    with pytest.raises(ControlPlaneParityError, match="start receipts are incomplete"):
        store.commit_s6bm_route_switch_fence(
            crossover_identity=identity,
            transition_context=s6bm_transition_context(identity),
        )
    with pytest.raises(ControlPlaneParityError, match="preceded route switch"):
        store.commit_idempotent_terminal_entity_with_receipt(
            scope=f"s6bm.terminal-effect.{identity['attempt_id']}",
            idempotency_key=identity["request_id"],
            request_payload={"request_id": identity["request_id"]},
            entity_kind="s6bm_terminal_effect",
            entity_id=identity["effect_id"],
            response_payload={**identity, "terminal_outcome": "completed"},
            state="completed",
            causal_payload={
                **identity,
                "actor_start_unix_ns": time.time_ns(),
                "requires_switch_before_effect": True,
            },
        )


def test_claim_fence_blocks_expired_owner(store: TransactionalControlPlaneStore):
    now = datetime(2026, 8, 15, 0, 0, tzinfo=UTC)
    first = store.acquire_claim(
        run_id="run-lease",
        worker_id="worker-a",
        worker_pid=101,
        process_instance_id="process-a",
        source_commit="a" * 40,
        supervisor_lease_id="lease-a-0001",
        fencing_token=1,
        ttl_seconds=2,
        now=now,
    )
    assert first.acquired and first.claim is not None
    assert store.read_claim("run-lease") == first.claim
    conflict = store.acquire_claim(
        run_id="run-lease",
        worker_id="worker-b",
        worker_pid=202,
        process_instance_id="process-b",
        source_commit="a" * 40,
        supervisor_lease_id="lease-b-0001",
        fencing_token=2,
        ttl_seconds=2,
        now=now + timedelta(seconds=1),
    )
    assert not conflict.acquired
    assert conflict.reason == "active_claim_conflict"

    replacement = store.acquire_claim(
        run_id="run-lease",
        worker_id="worker-b",
        worker_pid=202,
        process_instance_id="process-b",
        source_commit="a" * 40,
        supervisor_lease_id="lease-b-0001",
        fencing_token=2,
        ttl_seconds=2,
        now=now + timedelta(seconds=3),
    )
    assert replacement.acquired and replacement.claim is not None
    assert replacement.claim["claim_epoch"] == first.claim["claim_epoch"] + 1
    with store.bind_claim(first.claim):
        with pytest.raises(ControlPlaneLeaseConflict, match="bound_claim_lost"):
            store.assert_bound_claim("run-lease")


def test_side_effect_outbox_is_exactly_once(store: TransactionalControlPlaneStore):
    now = datetime.now(UTC).isoformat()
    payload = {
        "schema_version": "evm.lifecycle_side_effect.v1",
        "side_effect_key": "a" * 64,
        "lifecycle_series_id": "series-0001",
        "lifecycle_run_id": "run-side-effect",
        "attempt_id": "attempt-0001",
        "correlation_id": "correlation-0001",
        "stage_id": "deployment",
        "action": "apply",
        "action_digest": "b" * 64,
        "state": "reserved",
        "runtime_id": None,
        "evidence_uri": None,
        "reserved_at": now,
        "updated_at": now,
    }
    first, created = store.reserve_side_effect(payload)
    replay, replay_created = store.reserve_side_effect(payload)
    assert created is True
    assert replay_created is False
    assert replay == first
    completed = store.complete_side_effect(
        "a" * 64,
        state="completed",
        runtime_id="runtime-generalized",
        evidence_uri="evidence/generalized.json",
        updated_at=datetime.now(UTC).isoformat(),
    )
    assert completed["state"] == "completed"
    assert len(store.list_side_effects("run-side-effect")) == 1


def test_pool_exhaustion_has_bounded_timeout(postgres_dsn: str):
    schema = f"evm_s1_pool_{uuid4().hex[:12]}"
    store = TransactionalControlPlaneStore(
        StoreConfiguration(
            mode="postgres",
            dsn=postgres_dsn,
            schema=schema,
            pool_min_size=1,
            pool_max_size=1,
            acquire_timeout_seconds=0.2,
        )
    )
    acquired = threading.Event()

    def holder() -> None:
        with store.hold_connection(0):
            acquired.set()
            time.sleep(0.6)

    thread = threading.Thread(target=holder)
    thread.start()
    assert acquired.wait(timeout=1)
    started = time.monotonic()
    try:
        with pytest.raises(ControlPlanePoolTimeout):
            store.get_entity("lifecycle_run", "missing")
        assert time.monotonic() - started < 0.6
        assert store.telemetry().timeouts == 1
    finally:
        thread.join(timeout=2)
        store.close()
        import psycopg

        with psycopg.connect(postgres_dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def test_advisory_lock_wait_has_bounded_timeout(postgres_dsn: str):
    schema = f"evm_s1_lock_{uuid4().hex[:12]}"
    store = TransactionalControlPlaneStore(
        StoreConfiguration(
            mode="postgres",
            dsn=postgres_dsn,
            schema=schema,
            pool_min_size=2,
            pool_max_size=2,
            acquire_timeout_seconds=0.5,
            lock_timeout_seconds=0.2,
            statement_timeout_seconds=2,
        )
    )
    acquired = threading.Event()

    def holder() -> None:
        with store.serialized("same-logical-resource"):
            acquired.set()
            time.sleep(0.6)

    thread = threading.Thread(target=holder)
    thread.start()
    assert acquired.wait(timeout=1)
    started = time.monotonic()
    try:
        with pytest.raises(ControlPlaneTransactionTimeout):
            with store.serialized("same-logical-resource"):
                pass
        assert time.monotonic() - started < 0.6
    finally:
        thread.join(timeout=2)
        store.close()
        import psycopg

        with psycopg.connect(postgres_dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def test_existing_lifecycle_boundary_replays_create_and_queue(
    postgres_dsn: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    schema = f"evm_s1_lifecycle_{uuid4().hex[:12]}"
    monkeypatch.setenv("EVM_CONTROL_PLANE_STORE_MODE", "dual")
    monkeypatch.setenv("EVM_CONTROL_PLANE_DATABASE_URL", postgres_dsn)
    monkeypatch.setenv("EVM_CONTROL_PLANE_DATABASE_SCHEMA", schema)
    monkeypatch.setenv("EVM_PIPELINE_PROFILE_ROOT", str(tmp_path / "profiles"))
    monkeypatch.setenv("EVM_PIPELINE_PROFILE_RUNTIME_ROOT", "/mnt/evm-data/test-profiles")
    monkeypatch.setenv("EVM_LIFECYCLE_RUN_ROOT", str(tmp_path / "lifecycle-runs"))
    monkeypatch.setenv("EVM_EXPERIMENT_RUN_ROOT", str(tmp_path / "experiments"))
    monkeypatch.setenv("EVM_LIFECYCLE_RUNTIME_ROOT", "/mnt/evm-data/test-lifecycle-runs")
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", str(tmp_path / "data-root"))
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")
    monkeypatch.setenv("EVM_PROJECT_ROOT", str(Path(__file__).resolve().parents[1]))
    monkeypatch.setenv("EVM_GIT_COMMIT", "a" * 40)
    monkeypatch.setenv("EVM_GIT_BRANCH", "test/s1-lifecycle")
    source_manifest = tmp_path / "data-root" / "manifest.jsonl"
    split_manifest = tmp_path / "data-root" / "shard_index.json"
    source_manifest.parent.mkdir(parents=True)
    source_manifest.write_text('{"sample_id":"sample-1"}\n', encoding="utf-8")
    split_identity = "b" * 64
    split_manifest.write_text(
        '{"schema_version":"evm.dataset_shards.v1","identity_sha256":"' + split_identity + '"}',
        encoding="utf-8",
    )
    profile = default_profile()
    profile = profile.model_copy(
        update={
            "data": profile.data.model_copy(
                update={
                    "source_manifest_uri": str(source_manifest),
                    "split_manifest_uri": str(split_manifest),
                    "split_manifest_sha256": split_identity,
                }
            )
        }
    )
    record = save_profile(profile)
    original_validate = lifecycle_runs.validate_profile

    def executable(profile_value):
        result = original_validate(profile_value)
        return result.model_copy(update={"status": "ready", "executable": True, "blockers": []})

    monkeypatch.setattr(lifecycle_runs, "validate_profile", executable)
    reset_transactional_store()
    create_request = LifecycleRunRequest(
        profile_id=record.profile_id,
        profile_version=record.version,
        actor="requester@example.com",
        reason="Exercise transactional lifecycle idempotency",
        dry_run=True,
        idempotency_key="lifecycle-create-0001",
    )
    try:
        first = create_lifecycle_run(create_request)
        replay = create_lifecycle_run(create_request)
        assert replay.run_id == first.run_id
        assert replay.version == first.version == 1
        queue_request = LifecycleActionRequest(
            actor="requester@example.com",
            reason="Queue the exact transactional lifecycle once",
            expected_version=first.version,
            idempotency_key="lifecycle-queue-0001",
        )
        queued = queue_lifecycle_run(first.run_id, queue_request)
        queue_replay = queue_lifecycle_run(first.run_id, queue_request)
        assert queue_replay.run_id == queued.run_id
        assert queue_replay.version == queued.version == 2
        assert queue_replay.state == "queued"
        assert len(lifecycle_runs.read_runs().runs) == 1
    finally:
        reset_transactional_store()
        import psycopg

        with psycopg.connect(postgres_dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def test_existing_task_boundary_replays_create_and_confirm(
    postgres_dsn: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    schema = f"evm_s1_task_{uuid4().hex[:12]}"
    monkeypatch.setenv("EVM_CONTROL_PLANE_STORE_MODE", "dual")
    monkeypatch.setenv("EVM_CONTROL_PLANE_DATABASE_URL", postgres_dsn)
    monkeypatch.setenv("EVM_CONTROL_PLANE_DATABASE_SCHEMA", schema)
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path / "operations"))
    reset_transactional_store()
    request = TaskAssignmentRequest(
        cycle_id="cycle-generalized",
        task_type="airflow_dag_run",
        owner="ml-platform",
        priority="normal",
        resource_profile="local-pipeline-workers",
        approval_policy="manual",
        config_payload={"dag_id": "enterprise_vision_mlops_daily"},
        dry_run=False,
        idempotency_key="task-create-0001",
    )
    try:
        first = operations.create_task_assignment(request)
        replay = operations.create_task_assignment(request)
        assert replay.task_id == first.task_id
        assert len(operations.read_tasks().tasks) == 1
        transition = TaskTransitionRequest(
            actor="operator",
            reason="Confirm the exact queued task once",
            expected_version=first.version,
            idempotency_key="task-confirm-0001",
        )
        confirmed = operations.confirm_task_assignment(first.task_id, transition)
        confirm_replay = operations.confirm_task_assignment(first.task_id, transition)
        assert confirmed is not None and confirm_replay is not None
        assert confirm_replay.task_id == confirmed.task_id
        assert confirm_replay.version == confirmed.version == 2
        assert confirm_replay.status == "queued"
    finally:
        reset_transactional_store()
        import psycopg

        with psycopg.connect(postgres_dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
