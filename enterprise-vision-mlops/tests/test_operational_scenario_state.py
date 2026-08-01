from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evm.operations.failure_scenarios import (
    ApprovalRejected,
    ApprovalStore,
    LeaseConflict,
    LeaseManager,
    ScenarioStateStore,
    StateTransitionError,
    TargetRef,
)


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
TARGET = TargetRef(namespace="evm-production", name="evm-b0-pod", uid="pod-uid-1")


def test_state_transition_is_atomic_and_revision_guarded(tmp_path: Path) -> None:
    store = ScenarioStateStore(tmp_path)
    state = store.create(scenario_id="A", run_id="run-1", now=NOW)
    state = store.transition(
        "run-1",
        next_state="baseline_validated",
        expected_revision=state.revision,
        reason="baseline_passed",
        now=NOW + timedelta(seconds=1),
    )

    assert state.state == "baseline_validated"
    assert state.revision == 1
    assert store.load("run-1").history[-1].reason == "baseline_passed"
    with pytest.raises(StateTransitionError, match="stale_state_revision"):
        store.transition(
            "run-1",
            next_state="non_disruptive_validated",
            expected_revision=0,
            reason="stale_writer",
        )


def test_state_machine_rejects_skipped_gate(tmp_path: Path) -> None:
    store = ScenarioStateStore(tmp_path)
    store.create(scenario_id="A", run_id="run-1", now=NOW)

    with pytest.raises(StateTransitionError, match="invalid_transition"):
        store.transition(
            "run-1",
            next_state="approved",
            expected_revision=0,
            reason="skip_gates",
        )


def test_target_uid_lease_is_exclusive_and_reclaimable_after_expiry(tmp_path: Path) -> None:
    manager = LeaseManager(tmp_path)
    first = manager.acquire(
        run_id="run-1",
        owner="worker-1",
        target=TARGET,
        ttl_seconds=30,
        now=NOW,
    )
    with pytest.raises(LeaseConflict, match="target_lease_held"):
        manager.acquire(
            run_id="run-2",
            owner="worker-2",
            target=TARGET,
            ttl_seconds=30,
            now=NOW + timedelta(seconds=10),
        )

    second = manager.acquire(
        run_id="run-2",
        owner="worker-2",
        target=TARGET,
        ttl_seconds=30,
        now=NOW + timedelta(seconds=31),
    )
    assert second.run_id == "run-2"
    with pytest.raises(LeaseConflict, match="lease_token_mismatch"):
        manager.release(first)
    manager.release(second)


def test_approval_is_exactly_bound_and_single_use(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path)
    approval = store.issue(
        run_id="run-1",
        target=TARGET,
        action="delete_pod",
        source_revision="abcdef1",
        approver="operator",
        ttl_seconds=60,
        now=NOW,
    )

    consumed = store.consume(
        approval.approval_id,
        run_id="run-1",
        target=TARGET,
        action="delete_pod",
        source_revision="abcdef1",
        now=NOW + timedelta(seconds=1),
    )
    assert consumed.action_digest == approval.action_digest
    with pytest.raises(ApprovalRejected, match="already_consumed"):
        store.consume(
            approval.approval_id,
            run_id="run-1",
            target=TARGET,
            action="delete_pod",
            source_revision="abcdef1",
            now=NOW + timedelta(seconds=2),
        )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"run_id": "run-2"}, "run_id"),
        ({"target": TARGET.model_copy(update={"uid": "other-uid"})}, "target"),
        ({"action": "patch_daemonset"}, "action"),
        ({"source_revision": "fffffff"}, "source_revision"),
    ],
)
def test_approval_rejects_binding_mismatch(
    tmp_path: Path,
    overrides: dict,
    reason: str,
) -> None:
    store = ApprovalStore(tmp_path)
    approval = store.issue(
        run_id="run-1",
        target=TARGET,
        action="delete_pod",
        source_revision="abcdef1",
        approver="operator",
        ttl_seconds=60,
        now=NOW,
    )
    request = {
        "run_id": "run-1",
        "target": TARGET,
        "action": "delete_pod",
        "source_revision": "abcdef1",
        "now": NOW + timedelta(seconds=1),
    }
    request.update(overrides)

    with pytest.raises(ApprovalRejected, match=reason):
        store.consume(approval.approval_id, **request)


def test_approval_rejects_expired_binding(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path)
    approval = store.issue(
        run_id="run-1",
        target=TARGET,
        action="delete_pod",
        source_revision="abcdef1",
        approver="operator",
        ttl_seconds=5,
        now=NOW,
    )

    with pytest.raises(ApprovalRejected, match="not_expired"):
        store.consume(
            approval.approval_id,
            run_id="run-1",
            target=TARGET,
            action="delete_pod",
            source_revision="abcdef1",
            now=NOW + timedelta(seconds=6),
        )
