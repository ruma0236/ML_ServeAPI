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

    switch = store.commit_s6bm_route_switch_fence(crossover_identity=identity)
    assert switch["event_type"] == "blue_to_green_switch_commit"
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
    assert stored["durable_commit"]["schema_version"] == "evm.s6bm.durable_commit.v2"
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
        assert max(selected_widths) <= 1_000_000
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
        store.commit_s6bm_route_switch_fence(crossover_identity=identity)
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
