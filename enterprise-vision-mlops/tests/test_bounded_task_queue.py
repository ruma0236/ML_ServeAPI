from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from fastapi import HTTPException

from apps.api.control_panel_tasks import create_task, task_http
from evm.control_panel import operations
from evm.control_panel.admission_queue import (
    canonical_json_bytes,
    canonical_payload_size,
    load_admission_queue_config,
)
from evm.control_panel.transactional_store import (
    ControlPlaneAdmissionRejected,
    ControlPlaneItemTooLarge,
    ControlPlaneLeaseConflict,
    StoreConfiguration,
    TransactionalControlPlaneStore,
    reset_transactional_store,
)
from evm.control_panel.schemas import TaskAssignmentRequest
from evm.control_panel.task_queue_worker import BoundedTaskQueueWorker


@pytest.fixture
def postgres_dsn() -> str:
    value = os.getenv("EVM_TEST_CONTROL_PLANE_DATABASE_URL")
    if not value:
        pytest.skip("real PostgreSQL test DSN is not configured")
    return value


@pytest.fixture
def store(postgres_dsn: str):
    schema = f"evm_s2_test_{uuid4().hex[:12]}"
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


def config(**updates):
    return replace(load_admission_queue_config(), **updates)


def task_payload(task_id: str, *, fill: str = "") -> dict[str, object]:
    return {
        "task_id": task_id,
        "task_type": "airflow_dag_run",
        "owner": "test-owner",
        "priority": "normal",
        "resource_profile": "cpu",
        "config_payload": {"dag_id": "deterministic", "fill": fill},
        "dry_run": False,
        "idempotency_key": f"idem-{task_id}",
        "version": 1,
        "status": "queued",
        "created_at": "2026-08-16T00:00:00Z",
        "queued_at": "2026-08-16T00:00:00Z",
        "audit": [],
    }


def admit(
    store: TransactionalControlPlaneStore,
    task_id: str,
    *,
    active_config=None,
    fill: str = "",
    now: datetime | None = None,
):
    payload = task_payload(task_id, fill=fill)
    return store.admit_task_assignment(
        scope="task.create",
        idempotency_key=f"idem-{task_id}",
        request_payload={"task_id": task_id, "fill": fill},
        task_payload=payload,
        priority=20,
        config=active_config or config(),
        now=now,
    )


def test_s2_profile_is_frozen_and_uses_canonical_utf8_bytes():
    active = load_admission_queue_config()
    payload = {"z": "한글", "a": 1}

    assert active.profile_version == "s2-bounded-queue-v1-frozen-20260816"
    assert active.gpu_workers == 1
    assert active.local_max_depth <= active.durable_max_depth
    assert active.max_item_bytes <= active.local_max_bytes
    assert canonical_json_bytes(payload) == '{"a":1,"z":"한글"}'.encode("utf-8")
    assert canonical_payload_size(payload) == len(canonical_json_bytes(payload))
    assert len(active.sha256) == 64


def test_api_maps_item_size_and_capacity_to_distinct_contracts():
    oversized = task_http(
        ControlPlaneItemTooLarge(payload_bytes=11, max_item_bytes=10)
    )
    pressure = task_http(
        ControlPlaneAdmissionRejected(
            reason="durable_depth_limit",
            retry_after_seconds=3,
            current_depth=4,
            current_bytes=100,
        )
    )

    assert oversized.status_code == 413
    assert oversized.detail["payload_bytes"] == 11
    assert pressure.status_code == 429
    assert pressure.headers == {"Retry-After": "3"}
    assert pressure.detail["reason"] == "durable_depth_limit"


def test_worker_executor_result_fails_closed_on_empty_or_invalid_output():
    assert BoundedTaskQueueWorker._executor_result(b"")["failure_class"] == "executor_empty_result"
    assert BoundedTaskQueueWorker._executor_result(b"not-json\n")["failure_class"] == "executor_invalid_result"
    assert BoundedTaskQueueWorker._executor_result(
        b'{"outcome":"completed","task_status":"running"}\n'
    ) == {"outcome": "completed", "task_status": "running"}


def test_real_postgres_admission_replay_does_not_consume_capacity_or_reserve_rejection(
    store: TransactionalControlPlaneStore,
):
    active = config(durable_max_depth=1, durable_max_bytes=4096, max_item_bytes=2048)
    first = admit(store, "task-one", active_config=active)
    replay = admit(store, "task-one", active_config=active)

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.queue_id == first.queue_id
    assert store.task_queue_snapshot().active_depth == 1

    with pytest.raises(ControlPlaneAdmissionRejected) as rejected:
        admit(store, "task-two", active_config=active)
    assert rejected.value.reason == "durable_depth_limit"
    assert store.lookup_idempotency(
        "task.create", "idem-task-two", {"task_id": "task-two", "fill": ""}
    ) is None
    assert len(store.list_task_queue_items()) == 1


def test_real_postgres_oversized_item_is_not_persisted(store: TransactionalControlPlaneStore):
    payload = task_payload("task-large", fill="x" * 256)
    size = canonical_payload_size(payload)
    active = config(max_item_bytes=size - 1)

    with pytest.raises(ControlPlaneItemTooLarge) as rejected:
        admit(store, "task-large", active_config=active, fill="x" * 256)

    assert rejected.value.payload_bytes == size
    assert store.list_task_queue_items() == []


def test_real_postgres_claim_fences_stale_owner_and_closes_once(
    store: TransactionalControlPlaneStore,
):
    now = datetime(2026, 8, 16, 0, 0, tzinfo=UTC)
    active = config(max_age_seconds=60, lease_seconds=2)
    admit(store, "task-fenced", active_config=active, now=now)
    first = store.claim_task_queue_items(
        owner="worker-one",
        max_items=1,
        max_bytes=active.local_max_bytes,
        lease_seconds=2,
        scan_limit=active.durable_max_depth,
        now=now,
    )[0]
    reconciliation = store.reconcile_task_queue(
        max_attempts=active.max_attempts,
        now=now + timedelta(seconds=3),
    )
    second = store.claim_task_queue_items(
        owner="worker-two",
        max_items=1,
        max_bytes=active.local_max_bytes,
        lease_seconds=2,
        scan_limit=active.durable_max_depth,
        now=now + timedelta(seconds=3),
    )[0]

    assert reconciliation == {"expired": 0, "requeued": 1, "dlq": 0}
    assert second.lease_epoch == first.lease_epoch + 1
    with pytest.raises(ControlPlaneLeaseConflict):
        store.complete_task_queue_item(
            first,
            state="completed",
            reason="stale-owner",
            now=now + timedelta(seconds=3),
        )
    completed = store.complete_task_queue_item(
        second,
        state="completed",
        reason="replacement-owner",
        now=now + timedelta(seconds=4),
    )
    assert completed["state"] == "completed"
    assert store.task_queue_snapshot(now=now + timedelta(seconds=4)).active_depth == 0


def test_real_postgres_retry_budget_and_poison_work_reach_dlq(
    store: TransactionalControlPlaneStore,
):
    now = datetime(2026, 8, 16, 1, 0, tzinfo=UTC)
    active = config(global_retry_budget=1, max_attempts=3)
    admit(store, "task-transient-a", active_config=active, now=now)
    admit(store, "task-transient-b", active_config=active, now=now)
    admit(store, "task-poison", active_config=active, now=now)
    leases = store.claim_task_queue_items(
        owner="worker",
        max_items=3,
        max_bytes=active.local_max_bytes,
        lease_seconds=active.lease_seconds,
        scan_limit=active.durable_max_depth,
        now=now,
    )
    by_task = {lease.task_id: lease for lease in leases}

    retry = store.reschedule_task_queue_item(
        by_task["task-transient-a"],
        failure_class="dependency_503",
        transient=True,
        config=active,
        now=now + timedelta(milliseconds=1),
    )
    exhausted = store.reschedule_task_queue_item(
        by_task["task-transient-b"],
        failure_class="dependency_503",
        transient=True,
        config=active,
        now=now + timedelta(milliseconds=2),
    )
    poison = store.reschedule_task_queue_item(
        by_task["task-poison"],
        failure_class="invalid_payload",
        transient=False,
        config=active,
        now=now + timedelta(milliseconds=3),
    )

    assert retry["state"] == "retry_wait"
    assert exhausted["state"] == "dlq"
    assert exhausted["terminal_reason"].startswith("retry_budget_exhausted")
    assert poison["state"] == "dlq"
    assert poison["terminal_reason"] == "permanent:invalid_payload"


def test_existing_task_api_uses_atomic_durable_admission_and_returns_429(
    postgres_dsn: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    schema = f"evm_s2_api_{uuid4().hex[:12]}"
    active = config(durable_max_depth=1, durable_max_bytes=4096, max_item_bytes=2048)
    monkeypatch.setenv("EVM_CONTROL_PLANE_STORE_MODE", "postgres")
    monkeypatch.setenv("EVM_CONTROL_PLANE_DATABASE_URL", postgres_dsn)
    monkeypatch.setenv("EVM_CONTROL_PLANE_DATABASE_SCHEMA", schema)
    monkeypatch.setenv("EVM_TASK_ADMISSION_MODE", "durable")
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path / "operations"))
    monkeypatch.setattr(operations, "load_admission_queue_config", lambda: active)
    reset_transactional_store()

    def request(task_id: str) -> TaskAssignmentRequest:
        return TaskAssignmentRequest(
            task_type="airflow_dag_run",
            owner="test-owner",
            priority="normal",
            resource_profile="cpu",
            approval_policy="auto",
            config_payload={"dag_id": "deterministic", "test_id": task_id},
            dry_run=False,
            idempotency_key=f"api-{task_id}-0001",
        )

    try:
        accepted = create_task(request("one"))
        replay = create_task(request("one"))
        assert accepted.task_id == replay.task_id
        assert operations.read_tasks().tasks[0].task_id == accepted.task_id

        with pytest.raises(HTTPException) as rejected:
            create_task(request("two"))
        assert rejected.value.status_code == 429
        assert rejected.value.headers == {"Retry-After": str(active.retry_after_seconds)}
        assert operations.get_transactional_store().lookup_idempotency(
            "task.create",
            "api-two-0001",
            request("two").model_dump(mode="json", exclude_none=True),
        ) is None
    finally:
        reset_transactional_store()
        import psycopg

        with psycopg.connect(postgres_dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
