from __future__ import annotations

import os
import threading
import time
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
    ControlPlaneDeadlineExceeded,
    ControlPlaneItemTooLarge,
    ControlPlaneLeaseConflict,
    ControlPlaneTaskValidationError,
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

    assert active.profile_version == "s2-bounded-queue-v3-frozen-20260816"
    assert active.gpu_workers == 1
    assert active.lease_renew_interval_seconds < active.lease_seconds
    assert active.ingress_max_body_bytes <= active.max_item_bytes
    assert active.retry_budget_scope == "s2-bounded-queue-v3"
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
    first = store.begin_task_queue_attempt(first, lease_seconds=2, now=now)
    reconciliation = store.reconcile_task_queue(
        config=active,
        now=now + timedelta(seconds=3),
    )
    second = store.claim_task_queue_items(
        owner="worker-two",
        max_items=1,
        max_bytes=active.local_max_bytes,
        lease_seconds=2,
        scan_limit=active.durable_max_depth,
        now=now + timedelta(seconds=4),
    )[0]
    second = store.begin_task_queue_attempt(
        second,
        lease_seconds=2,
        now=now + timedelta(seconds=4),
    )

    assert reconciliation == {
        "expired": 0,
        "requeued": 1,
        "dlq": 0,
        "outcome_unknown": 0,
    }
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
    by_task = {
        task_id: store.begin_task_queue_attempt(
            lease,
            lease_seconds=active.lease_seconds,
            now=now,
        )
        for task_id, lease in by_task.items()
    }

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


def test_real_postgres_release_before_start_does_not_consume_attempt(store):
    now = datetime(2026, 8, 16, 2, 0, tzinfo=UTC)
    active = config()
    admit(store, "task-restart-before-start", active_config=active, now=now)
    first = store.claim_task_queue_items(
        owner="worker-one",
        max_items=1,
        max_bytes=active.local_max_bytes,
        lease_seconds=active.lease_seconds,
        scan_limit=active.durable_max_depth,
        now=now,
    )[0]

    store.release_task_queue_lease(
        first,
        reason="graceful_shutdown_before_start",
        now=now + timedelta(milliseconds=1),
    )
    row = store.get_task_queue_item(task_id=first.task_id)

    assert row["claim_count"] == 1
    assert row["attempt_count"] == 0
    assert row["state"] == "available"


@pytest.mark.parametrize(
    "profile",
    ("gpu-rtx4080-exclusive", "windows-rtx-4080-super"),
)
def test_real_postgres_existing_gpu_profiles_are_claimed_only_by_gpu_consumer(
    store,
    profile,
):
    now = datetime(2026, 8, 16, 3, 0, tzinfo=UTC)
    active = config()
    payload = task_payload("task-gpu")
    payload["resource_profile"] = profile
    store.admit_task_assignment(
        scope="task.create",
        idempotency_key="idem-task-gpu",
        request_payload={"task_id": "task-gpu"},
        task_payload=payload,
        priority=20,
        config=active,
        now=now,
    )

    assert store.claim_task_queue_items(
        owner="cpu-worker",
        max_items=1,
        max_bytes=active.local_max_bytes,
        lease_seconds=active.lease_seconds,
        scan_limit=active.durable_max_depth,
        resource_class="cpu",
        now=now,
    ) == []
    gpu = store.claim_task_queue_items(
        owner="gpu-worker",
        max_items=1,
        max_bytes=active.local_max_bytes,
        lease_seconds=active.lease_seconds,
        scan_limit=active.durable_max_depth,
        resource_class="gpu",
        now=now,
    )[0]
    assert gpu.resource_class == "gpu"


def test_real_postgres_request_deadline_can_only_narrow_frozen_max_age(store):
    observed_at = datetime.now(UTC)
    active = config(max_age_seconds=120)
    payload = task_payload("task-short-deadline")
    payload["config_payload"]["queue_deadline_seconds"] = 1.5
    store.admit_task_assignment(
        scope="task.create",
        idempotency_key="idem-task-short-deadline",
        request_payload={"task_id": "task-short-deadline"},
        task_payload=payload,
        priority=20,
        config=active,
        now=observed_at,
    )
    row = store.get_task_queue_item(task_id="task-short-deadline")

    assert row["deadline_at"] == (observed_at + timedelta(seconds=1.5)).isoformat()

    invalid = task_payload("task-invalid-deadline")
    invalid["config_payload"]["queue_deadline_seconds"] = 121
    with pytest.raises(ControlPlaneTaskValidationError) as rejected:
        store.admit_task_assignment(
            scope="task.create",
            idempotency_key="idem-task-invalid-deadline",
            request_payload={"task_id": "task-invalid-deadline"},
            task_payload=invalid,
            priority=20,
            config=active,
            now=observed_at,
        )
    assert rejected.value.reason == "queue_deadline_out_of_bounds"


def test_real_postgres_admission_lock_wait_is_bounded(store):
    active = config(admission_wait_seconds=0.25)
    locked = threading.Event()
    release = threading.Event()

    def holder():
        with store.serialized("task-admission-capacity"):
            locked.set()
            release.wait(timeout=2)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert locked.wait(timeout=1)
    started = time.monotonic()
    try:
        with pytest.raises(ControlPlaneAdmissionRejected) as rejected:
            admit(store, "task-lock-contention", active_config=active)
    finally:
        release.set()
        thread.join(timeout=2)

    assert rejected.value.reason == "admission_lock_timeout"
    assert time.monotonic() - started < 1.0


def test_real_postgres_effect_reservation_is_deterministic_and_fenced(store):
    now = datetime(2026, 8, 16, 4, 0, tzinfo=UTC)
    active = config()
    admit(store, "task-effect", active_config=active, now=now)
    lease = store.claim_task_queue_items(
        owner="worker",
        max_items=1,
        max_bytes=active.local_max_bytes,
        lease_seconds=active.lease_seconds,
        scan_limit=active.durable_max_depth,
        now=now,
    )[0]
    lease = store.begin_task_queue_attempt(
        lease,
        lease_seconds=active.lease_seconds,
        now=now,
    )
    first = store.reserve_task_dispatch_effect(
        lease,
        dag_id="deterministic",
        dag_run_id="cp__task-effect",
        now=now,
    )
    second = store.reserve_task_dispatch_effect(
        lease,
        dag_id="deterministic",
        dag_run_id="cp__task-effect",
        now=now,
    )

    assert first["effect_key"] == second["effect_key"]
    with pytest.raises(ControlPlaneLeaseConflict):
        store.reserve_task_dispatch_effect(
            lease,
            dag_id="deterministic",
            dag_run_id="changed-run-id",
            now=now,
        )


def test_durable_mode_direct_dispatch_is_queue_owned_ack_without_effect(store, monkeypatch):
    active = config()
    admit(store, "task-direct-bypass", active_config=active)
    monkeypatch.setenv("EVM_TASK_ADMISSION_MODE", "durable")
    monkeypatch.setattr(operations, "get_transactional_store", lambda: store)

    acknowledged = operations.dispatch_task_assignment("task-direct-bypass")

    assert acknowledged is not None
    assert acknowledged.task_id == "task-direct-bypass"
    assert store.get_task_queue_item(task_id="task-direct-bypass")["state"] == "available"
    assert store.get_task_dispatch_effect(task_id="task-direct-bypass") is None


def test_real_postgres_durable_dispatches_do_not_share_global_ledger_lock(
    store,
    monkeypatch,
):
    active = config()
    observed_at = datetime.now(UTC)
    for index in range(2):
        admit(
            store,
            f"task-parallel-{index}",
            active_config=active,
            now=observed_at,
        )
    leases = store.claim_task_queue_items(
        owner="parallel-worker",
        max_items=2,
        max_bytes=active.local_max_bytes,
        lease_seconds=active.lease_seconds,
        scan_limit=active.durable_max_depth,
        now=observed_at,
    )
    leases = [
        store.begin_task_queue_attempt(
            lease,
            lease_seconds=active.lease_seconds,
            now=observed_at,
        )
        for lease in leases
    ]
    barrier = threading.Barrier(2)
    counter_lock = threading.Lock()
    running = 0
    peak = 0

    def fake_airflow(path, *, method="GET", payload=None):
        nonlocal running, peak
        if method == "GET":
            raise operations.TaskDispatchError(
                "airflow_dag_run_not_found",
                "not created yet",
                status_code=404,
            )
        with counter_lock:
            running += 1
            peak = max(peak, running)
        barrier.wait(timeout=2)
        time.sleep(0.05)
        with counter_lock:
            running -= 1
        return {"dag_run_id": payload["dag_run_id"], "state": "success"}

    monkeypatch.setenv("EVM_TASK_ADMISSION_MODE", "durable")
    monkeypatch.setattr(operations, "get_transactional_store", lambda: store)
    monkeypatch.setattr(operations, "airflow_api_request", fake_airflow)
    failures = []

    def dispatch(lease):
        try:
            with store.bind_task_queue_lease(lease):
                operations.dispatch_queued_task_assignment(lease.task_id)
        except Exception as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    threads = [threading.Thread(target=dispatch, args=(lease,)) for lease in leases]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert failures == []
    assert peak == 2
    assert all(not thread.is_alive() for thread in threads)
    assert {
        item["state"] for item in store.list_task_queue_items()
    } == {"completed"}


def test_real_postgres_compaction_bounds_row_history_and_rollback_mirror(store):
    observed_at = datetime.now(UTC)
    active = config(
        terminal_queue_max_rows=2,
        task_history_max_terminal_rows=2,
        terminal_queue_max_age_seconds=3600,
        compaction_batch_size=16,
    )
    for index in range(5):
        admit(
            store,
            f"task-history-{index}",
            active_config=active,
            now=observed_at,
        )
    leases = store.claim_task_queue_items(
        owner="history-worker",
        max_items=5,
        max_bytes=active.local_max_bytes,
        lease_seconds=active.lease_seconds,
        scan_limit=active.durable_max_depth,
        now=observed_at,
    )
    for lease in leases:
        started = store.begin_task_queue_attempt(
            lease,
            lease_seconds=active.lease_seconds,
            now=observed_at,
        )
        store.complete_task_queue_item(
            started,
            state="completed",
            reason="test_terminal",
            now=observed_at + timedelta(milliseconds=1),
        )

    compacted = store.compact_task_queue_history(
        config=active,
        now=observed_at + timedelta(seconds=1),
    )
    remaining = store.list_entities("task_assignment")
    store.replace_task_mirror(remaining)
    snapshot = store.task_queue_history_snapshot()

    assert compacted == {
        "queue_rows": 3,
        "effect_rows": 0,
        "task_rows": 3,
        "idempotency_rows": 0,
    }
    assert snapshot.queue_rows == 2
    assert snapshot.task_rows == 2
    assert snapshot.mirror_rows == 2
    assert snapshot.compacted_rows == {"queue": 3, "task": 3}
    assert snapshot.compacted_bytes["queue"] > 0
    assert snapshot.compacted_bytes["task"] > 0
    assert snapshot.queue_bytes > 0
    assert snapshot.task_bytes > 0
    assert snapshot.mirror_bytes > 0


def test_real_postgres_compaction_preserves_bounded_idempotency_replay(store):
    observed_at = datetime.now(UTC)
    active = config(
        terminal_queue_max_rows=2,
        task_history_max_terminal_rows=2,
        terminal_queue_max_age_seconds=3600,
        compaction_batch_size=16,
        idempotency_tombstone_max_rows=16,
        idempotency_tombstone_retention_seconds=3600,
    )
    original = {}
    for index in range(5):
        result = admit(
            store,
            f"task-tombstone-{index}",
            active_config=active,
            now=observed_at,
        )
        original[index] = result.task_payload
    leases = store.claim_task_queue_items(
        owner="history-worker",
        max_items=5,
        max_bytes=active.local_max_bytes,
        lease_seconds=active.lease_seconds,
        scan_limit=active.durable_max_depth,
        now=observed_at,
    )
    for lease in leases:
        started = store.begin_task_queue_attempt(
            lease,
            lease_seconds=active.lease_seconds,
            now=observed_at,
        )
        store.complete_task_queue_item(
            started,
            state="completed",
            reason="test_terminal",
            now=observed_at + timedelta(milliseconds=1),
        )
    store.compact_task_queue_history(
        config=active,
        now=observed_at + timedelta(seconds=1),
    )
    before = store.task_queue_history_snapshot()

    replay = admit(
        store,
        "task-tombstone-0",
        active_config=active,
        now=observed_at + timedelta(seconds=2),
    )
    after = store.task_queue_history_snapshot()

    assert replay.replayed is True
    assert replay.task_payload == original[0]
    assert replay.queue_id == "not-applicable"
    assert after.queue_rows == before.queue_rows
    assert after.effect_rows == before.effect_rows
    assert after.idempotency_rows == before.idempotency_rows == 5


def test_real_postgres_deadline_fences_late_effect_commit_and_preserves_unknown(store):
    observed_at = datetime.now(UTC)
    active = config(max_age_seconds=2, runtime_terminal_timeout_seconds=2)
    admit(store, "task-deadline-fence", active_config=active, now=observed_at)
    lease = store.claim_task_queue_items(
        owner="deadline-worker",
        max_items=1,
        max_bytes=active.local_max_bytes,
        lease_seconds=5,
        scan_limit=active.durable_max_depth,
        now=observed_at,
    )[0]
    lease = store.begin_task_queue_attempt(
        lease,
        lease_seconds=5,
        now=observed_at,
    )
    effect = store.reserve_task_dispatch_effect(
        lease,
        dag_id="deterministic",
        dag_run_id="cp__task-deadline-fence",
        now=observed_at,
    )
    store.mark_task_dispatch_effect_submitting(
        lease,
        effect_key=effect["effect_key"],
        now=observed_at,
    )
    late = observed_at + timedelta(seconds=3)

    with pytest.raises(ControlPlaneDeadlineExceeded):
        store.commit_task_dispatch_effect(
            lease,
            effect_key=effect["effect_key"],
            runtime_state="success",
            runtime_payload={"state": "success"},
            task_payload={**task_payload("task-deadline-fence"), "version": 2},
            terminal=True,
            now=late,
        )

    result = store.reconcile_task_queue(config=active, now=late)
    row = store.get_task_queue_item(task_id="task-deadline-fence")
    effect_row = store.get_task_dispatch_effect(task_id="task-deadline-fence")
    assert result["outcome_unknown"] == 1
    assert row["state"] == "outcome_unknown"
    assert effect_row["state"] == "outcome_unknown"


def test_real_postgres_expired_without_effect_does_not_block_following_healthy(store):
    observed_at = datetime.now(UTC)
    active = config(max_age_seconds=1)
    admit(store, "task-expired-no-effect", active_config=active, now=observed_at)
    result = store.reconcile_task_queue(
        config=active,
        now=observed_at + timedelta(seconds=2),
    )

    assert result["expired"] == 1
    assert store.get_task_dispatch_effect(task_id="task-expired-no-effect") is None

    healthy = admit(
        store,
        "task-after-expired",
        active_config=active,
        now=observed_at + timedelta(seconds=2),
    )
    lease = store.claim_task_queue_items(
        owner="healthy-worker",
        max_items=1,
        max_bytes=active.local_max_bytes,
        lease_seconds=active.lease_seconds,
        scan_limit=active.durable_max_depth,
        now=observed_at + timedelta(seconds=2),
    )[0]
    lease = store.begin_task_queue_attempt(
        lease,
        lease_seconds=active.lease_seconds,
        now=observed_at + timedelta(seconds=2),
    )
    store.complete_task_queue_item(
        lease,
        state="completed",
        reason="healthy_after_expired",
        now=observed_at + timedelta(seconds=2, milliseconds=1),
    )
    assert healthy.task_payload["task_id"] == "task-after-expired"
    assert store.get_task_queue_item(task_id="task-after-expired")["state"] == "completed"


def test_real_postgres_runtime_poll_claim_is_fair(store):
    observed_at = datetime.now(UTC)
    active = config()
    for index in range(5):
        task_id = f"task-runtime-poll-{index}"
        admit(store, task_id, active_config=active, now=observed_at)
    leases = store.claim_task_queue_items(
        owner="runtime-worker",
        max_items=5,
        max_bytes=active.local_max_bytes,
        lease_seconds=active.lease_seconds,
        scan_limit=active.durable_max_depth,
        now=observed_at,
    )
    for lease in leases:
        lease = store.begin_task_queue_attempt(
            lease,
            lease_seconds=active.lease_seconds,
            now=observed_at,
        )
        effect = store.reserve_task_dispatch_effect(
            lease,
            dag_id="deterministic",
            dag_run_id=f"cp__{lease.task_id}",
            now=observed_at,
        )
        store.mark_task_dispatch_effect_submitting(
            lease,
            effect_key=effect["effect_key"],
            now=observed_at,
        )
        running = dict(lease.task_payload)
        running.update(
            {
                "status": "running",
                "version": 2,
                "runtime_system": "airflow",
                "runtime_id": f"cp__{lease.task_id}",
                "runtime_state": "queued",
                "runtime_url": "http://fixture/runtime",
            }
        )
        store.commit_task_dispatch_effect(
            lease,
            effect_key=effect["effect_key"],
            runtime_state="queued",
            runtime_payload={"state": "queued"},
            task_payload=running,
            terminal=False,
            now=observed_at,
        )

    first = store.claim_runtime_pending_for_poll(
        max_items=2,
        poll_interval_seconds=10,
        now=observed_at,
    )
    second = store.claim_runtime_pending_for_poll(
        max_items=2,
        poll_interval_seconds=10,
        now=observed_at,
    )

    assert len(first) == len(second) == 2
    assert {item["task_id"] for item in first}.isdisjoint(
        {item["task_id"] for item in second}
    )


def test_real_postgres_gpu_downstream_outstanding_is_globally_one(store):
    observed_at = datetime.now(UTC)
    active = config()
    for index in range(2):
        payload = task_payload(f"task-gpu-cap-{index}")
        payload["resource_profile"] = "windows-rtx-4080-super"
        store.admit_task_assignment(
            scope="task.create",
            idempotency_key=f"idem-task-gpu-cap-{index}",
            request_payload={"task_id": f"task-gpu-cap-{index}"},
            task_payload=payload,
            priority=20,
            config=active,
            now=observed_at,
        )
    first = store.claim_task_queue_items(
        owner="gpu-worker-one",
        max_items=2,
        max_bytes=active.local_max_bytes,
        lease_seconds=active.lease_seconds,
        scan_limit=active.durable_max_depth,
        resource_class="gpu",
        max_outstanding=1,
        now=observed_at,
    )
    blocked = store.claim_task_queue_items(
        owner="gpu-worker-two",
        max_items=1,
        max_bytes=active.local_max_bytes,
        lease_seconds=active.lease_seconds,
        scan_limit=active.durable_max_depth,
        resource_class="gpu",
        max_outstanding=1,
        now=observed_at,
    )

    assert len(first) == 1
    assert blocked == []
