from __future__ import annotations

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
    ControlPlaneLeaseConflict,
    ControlPlanePoolTimeout,
    ControlPlaneTransactionTimeout,
    ControlPlaneVersionConflict,
    StoreConfiguration,
    TransactionalControlPlaneStore,
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
    assert store.lookup_idempotency(
        "lifecycle.cancel", "cancel-key-0001", request
    ) == updated


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
    monkeypatch.setenv(
        "EVM_LIFECYCLE_RUNTIME_ROOT", "/mnt/evm-data/test-lifecycle-runs"
    )
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
        '{"schema_version":"evm.dataset_shards.v1","identity_sha256":"'
        + split_identity
        + '"}',
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
        return result.model_copy(
            update={"status": "ready", "executable": True, "blockers": []}
        )

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
