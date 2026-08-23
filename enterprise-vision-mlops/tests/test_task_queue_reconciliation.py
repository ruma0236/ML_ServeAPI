from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from evm.control_panel import operations
from evm.control_panel.admission_queue import load_admission_queue_config
from evm.control_panel.transactional_store import (
    ControlPlaneParityError,
    StoreConfiguration,
    TransactionalControlPlaneStore,
)
from scripts.dev.reconcile_stranded_task_queue import public_summary


@pytest.fixture
def store():
    dsn = os.getenv("EVM_TEST_CONTROL_PLANE_DATABASE_URL")
    if not dsn:
        pytest.skip("real PostgreSQL test DSN is not configured")
    schema = f"evm_reconcile_test_{uuid4().hex[:12]}"
    instance = TransactionalControlPlaneStore(
        StoreConfiguration(
            mode="postgres",
            dsn=dsn,
            schema=schema,
            pool_min_size=1,
            pool_max_size=2,
            acquire_timeout_seconds=0.5,
        )
    )
    try:
        yield instance
    finally:
        instance.close()
        import psycopg

        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def legacy_task(task_id: str, *, runtime_id: str | None = None) -> dict[str, object]:
    return {
        "task_id": task_id,
        "task_type": "airflow_dag_run",
        "owner": "legacy-test-owner",
        "priority": "normal",
        "resource_profile": "local-pipeline-workers",
        "config_payload": {"dag_id": "enterprise_vision_mlops_daily"},
        "dry_run": False,
        "approval_policy": "auto",
        "version": 1,
        "status": "queued",
        "created_at": "2026-07-09T00:00:00Z",
        "queued_at": "2026-07-09T00:00:00Z",
        "runtime_id": runtime_id,
        "audit": [
            {
                "timestamp": "2026-07-09T00:00:00Z",
                "actor": "legacy-test-owner",
                "event": "task_assignment_created",
                "details": {"status": "queued"},
            }
        ],
    }


def seed(store: TransactionalControlPlaneStore, *payloads: dict[str, object]) -> None:
    for payload in payloads:
        store.insert_entity(
            "task_assignment",
            str(payload["task_id"]),
            payload,
            state="queued",
            version=1,
        )
    store.refresh_task_mirror_from_authority()


def test_real_postgres_reconciliation_is_dry_run_atomic_idempotent_and_rollbackable(
    store: TransactionalControlPlaneStore,
):
    seed(store, legacy_task("task-legacy-a"), legacy_task("task-legacy-b"))
    cutoff = datetime(2027, 1, 1, tzinfo=UTC)
    snapshot = store.inspect_stranded_task_queue(cutoff=cutoff)

    assert snapshot["candidate_count"] == 2
    assert snapshot["eligible_count"] == 2
    assert snapshot["blocked_count"] == 0
    ids = [str(item["task_id"]) for item in snapshot["items"]]

    dry_run = store.reconcile_stranded_task_queue(
        task_ids=ids,
        cutoff=cutoff,
        expected_snapshot_sha256=snapshot["snapshot_sha256"],
        actor="test-operator",
        reason="test_reconciliation",
        dry_run=True,
    )
    assert dry_run["status"] == "dry_run_passed"
    assert all(store.get_entity("task_assignment", task_id)["status"] == "queued" for task_id in ids)

    applied = store.reconcile_stranded_task_queue(
        task_ids=ids,
        cutoff=cutoff,
        expected_snapshot_sha256=snapshot["snapshot_sha256"],
        actor="test-operator",
        reason="test_reconciliation",
        dry_run=False,
        observed_at=datetime(2026, 8, 23, tzinfo=UTC),
    )
    assert applied["status"] == "applied"
    assert applied["reconciled_count"] == 2
    assert store.task_mirror_parity()["matches"] is True
    assert store.verify_task_queue_cutover(
        mode="durable", config=load_admission_queue_config()
    )["stranded_depth"] == 0
    assert all(store.get_entity("task_assignment", task_id)["status"] == "cancelled" for task_id in ids)

    replay = store.reconcile_stranded_task_queue(
        task_ids=ids,
        cutoff=cutoff,
        expected_snapshot_sha256=snapshot["snapshot_sha256"],
        actor="test-operator",
        reason="test_reconciliation",
        dry_run=False,
    )
    assert replay["status"] == "replayed"

    rolled_back = store.rollback_stranded_task_queue(
        snapshot=snapshot,
        actor="test-operator",
        reason="test_rollback",
        observed_at=datetime(2026, 8, 23, 0, 1, tzinfo=UTC),
    )
    assert rolled_back["status"] == "rolled_back"
    assert store.task_mirror_parity()["matches"] is True
    assert all(store.get_entity("task_assignment", task_id)["status"] == "queued" for task_id in ids)
    assert all(
        store.get_entity("task_assignment", task_id)["audit"][-1]["event"]
        == "historical_task_reconciliation_rolled_back"
        for task_id in ids
    )


def test_real_postgres_reconciliation_fails_closed_on_runtime_identity_and_allowlist_drift(
    store: TransactionalControlPlaneStore,
):
    seed(store, legacy_task("task-safe"), legacy_task("task-unsafe", runtime_id="existing-run"))
    cutoff = datetime(2027, 1, 1, tzinfo=UTC)
    snapshot = store.inspect_stranded_task_queue(cutoff=cutoff)

    assert snapshot["candidate_count"] == 2
    assert snapshot["eligible_count"] == 1
    assert snapshot["blocked_count"] == 1
    with pytest.raises(ControlPlaneParityError, match="allowlist"):
        store.reconcile_stranded_task_queue(
            task_ids=["task-safe"],
            cutoff=cutoff,
            expected_snapshot_sha256=snapshot["snapshot_sha256"],
            actor="test-operator",
            reason="test_reconciliation",
            dry_run=False,
        )
    with pytest.raises(ControlPlaneParityError, match="preconditions"):
        store.reconcile_stranded_task_queue(
            task_ids=["task-safe", "task-unsafe"],
            cutoff=cutoff,
            expected_snapshot_sha256=snapshot["snapshot_sha256"],
            actor="test-operator",
            reason="test_reconciliation",
            dry_run=False,
        )
    assert store.get_entity("task_assignment", "task-safe")["status"] == "queued"


def test_public_summary_never_exposes_private_task_items():
    summary = public_summary(
        {
            "status": "snapshot_captured",
            "candidate_count": 38,
            "eligible_count": 38,
            "blocked_count": 0,
            "snapshot_sha256": "a" * 64,
            "items": [{"task_id": "private-task-id"}],
        }
    )

    assert summary["candidate_count"] == 38
    assert "items" not in summary
    assert "private-task-id" not in str(summary)


def test_real_postgres_file_mirror_preserves_canonical_legacy_payload(
    store: TransactionalControlPlaneStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    seed(store, legacy_task("task-legacy-mirror"))
    monkeypatch.setattr(operations, "get_transactional_store", lambda: store)
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path))

    operations.sync_task_json_mirror_from_store()

    parity = operations.verify_task_json_mirror_parity()
    assert parity["matches"] is True
    assert parity["authority_count"] == parity["file_count"] == 1
