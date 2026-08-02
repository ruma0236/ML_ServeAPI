from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evm.operations.scenario_d_supervision import (
    ChildHeartbeat,
    ChildIdentity,
    ChildObservation,
    LifecycleRunClaimStore,
    ProcessRecord,
    RestartLedger,
    RestartLedgerData,
    ScenarioDPolicy,
    current_process_started_at,
    evaluate_child,
    evaluate_runtime_tick,
)


NOW = datetime(2026, 8, 2, 6, 0, tzinfo=timezone.utc)
COMMIT = "a" * 40
LEASE = "lease-12345678"


def test_current_process_start_precedes_observation() -> None:
    started_at = current_process_started_at()
    observed_at = datetime.now(timezone.utc)
    assert started_at.tzinfo is not None
    assert started_at <= observed_at
    assert observed_at - started_at < timedelta(days=1)
    assert current_process_started_at() == started_at


@pytest.fixture
def policy() -> ScenarioDPolicy:
    return ScenarioDPolicy(
        schema_version="evm.scenario_d_policy.v1",
        check_interval_seconds=5,
        heartbeat_interval_seconds=5,
        heartbeat_stale_seconds=20,
        stale_debounce_samples=2,
        max_restarts_per_window=3,
        restart_window_seconds=300,
        restart_backoff_seconds=[1, 2, 4],
        max_detection_seconds=10,
        max_stale_detection_seconds=25,
        max_recovery_seconds=60,
        max_heartbeat_p95_seconds=7.5,
        run_claim_ttl_seconds=30,
    )


def observation(
    *,
    child: str = "lifecycle_worker",
    heartbeat_age: float = 1,
    source_commit: str = COMMIT,
    lease_id: str = LEASE,
    fence: int = 7,
) -> ChildObservation:
    started_at = NOW - timedelta(minutes=5)
    identity = ChildIdentity(
        child_name=child,
        pid=101,
        process_started_at=started_at,
        process_instance_id="instance-12345678",
        source_commit=source_commit,
        supervisor_lease_id=lease_id,
        fencing_token=fence,
    )
    heartbeat = ChildHeartbeat(
        **identity.model_dump(),
        observed_at=NOW - timedelta(seconds=heartbeat_age),
    )
    return ChildObservation(
        schema_version="evm.scenario_d_child_observation.v1",
        child_name=child,
        observed_at=NOW,
        expected_source_commit=COMMIT,
        expected_lease_id=LEASE,
        expected_fencing_token=7,
        pid_file_pid=101,
        pid_file_process_exists=True,
        identity=identity,
        heartbeat=heartbeat,
        processes=[
            ProcessRecord(
                pid=101,
                process_started_at=started_at,
                command_matches=True,
                executable="python.exe",
                command_line=f"python -m evm.control_panel.{child}",
            )
        ],
    )


def test_live_requires_all_identity_signals(policy: ScenarioDPolicy) -> None:
    decision = evaluate_child(observation(), policy)
    assert decision.state == "live"
    assert decision.action == "none"
    assert decision.exact_identity is True
    assert decision.revision_matches is True
    assert decision.lease_matches is True
    assert decision.fencing_matches is True


@pytest.mark.parametrize("child", ["lifecycle_worker", "kubernetes_observer"])
def test_stopped_child_restarts_without_target_kill(
    child: str, policy: ScenarioDPolicy
) -> None:
    item = observation(child=child).model_copy(
        update={"pid_file_process_exists": False, "processes": []}
    )
    decision = evaluate_child(item, policy)
    assert decision.reason == "process_missing"
    assert decision.action == "restart_exact"
    assert decision.target_pid is None


def test_stale_heartbeat_is_debounced_then_restarts(policy: ScenarioDPolicy) -> None:
    item = observation(heartbeat_age=25)
    first = evaluate_child(item, policy)
    second = evaluate_child(item, policy, prior_failed_samples=first.failed_samples)
    assert (first.state, first.action) == ("suspect", "none")
    assert (second.state, second.action, second.target_pid) == (
        "recovering",
        "restart_exact",
        101,
    )


def test_revision_and_fence_mismatches_restart_only_exact_target(
    policy: ScenarioDPolicy,
) -> None:
    revision = evaluate_child(observation(source_commit="b" * 40), policy)
    fence = evaluate_child(observation(lease_id="old-lease-123", fence=6), policy)
    assert (revision.reason, revision.target_pid) == ("source_revision_mismatch", 101)
    assert (fence.reason, fence.target_pid) == ("supervisor_fence_mismatch", 101)


def test_duplicate_process_fails_closed(policy: ScenarioDPolicy) -> None:
    item = observation()
    duplicate = item.processes[0].model_copy(update={"pid": 202})
    decision = evaluate_child(item.model_copy(update={"processes": [*item.processes, duplicate]}), policy)
    assert (decision.state, decision.reason, decision.action) == (
        "blocked",
        "blocked_duplicate",
        "none",
    )


def test_stale_pid_for_unrelated_process_fails_closed(policy: ScenarioDPolicy) -> None:
    item = observation().model_copy(update={"processes": []})
    decision = evaluate_child(item, policy)
    assert decision.reason == "blocked_unknown_owner"
    assert decision.action == "none"


def test_heartbeat_identity_mismatch_fails_closed(policy: ScenarioDPolicy) -> None:
    item = observation()
    heartbeat = item.heartbeat.model_copy(update={"process_instance_id": "wrong-instance"})
    decision = evaluate_child(item.model_copy(update={"heartbeat": heartbeat}), policy)
    assert decision.reason == "blocked_identity"
    assert decision.action == "none"


def test_restart_ledger_blocks_duplicate_backoff_and_budget(
    tmp_path: Path, policy: ScenarioDPolicy
) -> None:
    ledger = RestartLedger(tmp_path / "ledger.json")
    first = evaluate_child(
        observation().model_copy(update={"pid_file_process_exists": False, "processes": []}),
        policy,
    )
    assert ledger.admit(first, policy, now=NOW).action == "restart_exact"
    assert ledger.admit(first, policy, now=NOW + timedelta(milliseconds=100)).reason == (
        "duplicate_incident_replay"
    )

    for offset, pid in ((2, 201), (5, 202)):
        item = observation().model_copy(
            update={"pid_file_pid": pid, "pid_file_process_exists": False, "processes": []}
        )
        admitted = ledger.admit(evaluate_child(item, policy), policy, now=NOW + timedelta(seconds=offset))
        assert admitted.action == "restart_exact"

    exhausted_item = observation().model_copy(
        update={"pid_file_pid": 203, "pid_file_process_exists": False, "processes": []}
    )
    exhausted = ledger.admit(
        evaluate_child(exhausted_item, policy), policy, now=NOW + timedelta(seconds=10)
    )
    assert (exhausted.state, exhausted.reason, exhausted.action) == (
        "circuit_open",
        "restart_budget_exhausted",
        "none",
    )
    assert len(RestartLedgerData.model_validate_json((tmp_path / "ledger.json").read_text()).attempts) == 3


def test_runtime_tick_persists_state_and_audit(tmp_path: Path, policy: ScenarioDPolicy) -> None:
    item = observation(heartbeat_age=25)
    paths = {
        "state_path": tmp_path / "state.json",
        "ledger_path": tmp_path / "ledger.json",
        "audit_path": tmp_path / "audit.jsonl",
    }
    first = evaluate_runtime_tick(observation=item, policy=policy, **paths)
    second = evaluate_runtime_tick(
        observation=item.model_copy(update={"observed_at": NOW + timedelta(seconds=5)}),
        policy=policy,
        **paths,
    )
    assert first.state == "suspect"
    assert second.action == "restart_exact"
    assert len((tmp_path / "audit.jsonl").read_text().splitlines()) == 2


def test_run_claim_is_idempotent_and_blocks_duplicate_owner(tmp_path: Path) -> None:
    store = LifecycleRunClaimStore(tmp_path, ttl_seconds=30)
    kwargs = {
        "run_id": "run-1",
        "worker_id": "worker-a",
        "worker_pid": 101,
        "process_instance_id": "instance-12345678",
        "source_commit": COMMIT,
        "supervisor_lease_id": LEASE,
        "fencing_token": 7,
    }
    acquired = store.acquire(**kwargs, now=NOW)
    reused = store.acquire(**kwargs, now=NOW + timedelta(seconds=1))
    conflict = store.acquire(
        **{**kwargs, "worker_id": "worker-b", "worker_pid": 202, "process_instance_id": "instance-87654321"},
        now=NOW + timedelta(seconds=2),
    )
    assert acquired.acquired and reused.acquired
    assert reused.claim.claim_id == acquired.claim.claim_id
    assert (conflict.acquired, conflict.reason) == (False, "active_claim_conflict")


def test_expired_claim_is_replaced_but_higher_fence_blocks_stale_supervisor(
    tmp_path: Path,
) -> None:
    store = LifecycleRunClaimStore(tmp_path, ttl_seconds=5)
    first = store.acquire(
        run_id="run-1",
        worker_id="worker-a",
        worker_pid=101,
        process_instance_id="instance-12345678",
        source_commit=COMMIT,
        supervisor_lease_id=LEASE,
        fencing_token=7,
        now=NOW,
    )
    replaced = store.acquire(
        run_id="run-1",
        worker_id="worker-b",
        worker_pid=202,
        process_instance_id="instance-87654321",
        source_commit=COMMIT,
        supervisor_lease_id=LEASE,
        fencing_token=7,
        now=NOW + timedelta(seconds=6),
    )
    stale = store.acquire(
        run_id="run-1",
        worker_id="worker-old",
        worker_pid=303,
        process_instance_id="instance-old-1234",
        source_commit=COMMIT,
        supervisor_lease_id="older-lease-123",
        fencing_token=6,
        now=NOW + timedelta(seconds=12),
    )
    assert first.claim.claim_epoch == 1
    assert replaced.claim.claim_epoch == 2
    assert replaced.reason == "expired_claim_replaced"
    assert (stale.acquired, stale.reason) == (False, "stale_supervisor_fence")


def test_release_allows_new_claim_without_duplicate_execution(tmp_path: Path) -> None:
    store = LifecycleRunClaimStore(tmp_path, ttl_seconds=30)
    first = store.acquire(
        run_id="run-1",
        worker_id="worker-a",
        worker_pid=101,
        process_instance_id="instance-12345678",
        source_commit=COMMIT,
        supervisor_lease_id=LEASE,
        fencing_token=7,
        now=NOW,
    )
    store.release(first.claim, now=NOW + timedelta(seconds=1))
    second = store.acquire(
        run_id="run-1",
        worker_id="worker-b",
        worker_pid=202,
        process_instance_id="instance-87654321",
        source_commit=COMMIT,
        supervisor_lease_id=LEASE,
        fencing_token=7,
        now=NOW + timedelta(seconds=2),
    )
    assert second.acquired is True
    assert second.claim.claim_epoch == 2
