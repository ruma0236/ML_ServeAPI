from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from evm.operations.correlation import IncidentRecord
from evm.operations.recovery_coordination import (
    IncidentTiming,
    LeaseAcquireRequest,
    RecoveryActionRequest,
    RecoveryCoordinationPolicy,
    RecoveryCoordinationStore,
    action_digest,
    build_incident_plane_snapshot,
    exact_target,
    recovery_approval,
    write_incident_plane_snapshot,
)


NOW = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)
REVISION = "c0bf42277ec4e227b9a38e0326e638eada736026"
POLICY_VERSION = "recovery-coordination-v1"
INCIDENT_ID = "inc-0198d15e-0000-7000-8000-000000000001"
CORRELATION_ID = "0198d15e-0000-7000-8000-000000000002"


def policy() -> RecoveryCoordinationPolicy:
    return RecoveryCoordinationPolicy(
        policy_version=POLICY_VERSION,
        source_revision=REVISION,
        lease_ttl_seconds=20,
        renewal_interval_seconds=5,
        approval_ttl_seconds=300,
        allowed_actions=["hold", "recommend-exact-restart", "recommend-rollback"],
    )


def target(*, match_count: int = 1, pod_uid: str = "pod-uid-current-0001"):
    return exact_target(
        "production-b0",
        {
            "cluster": "docker-desktop",
            "namespace": "evm-production",
            "deployment": "evm-b0-production",
            "deployment_uid": "cfdab424-dcc5-4d5f-a46f-ae7530441ef4",
            "pod_uid": pod_uid,
            "image_digest": "sha256:" + "a" * 64,
        },
        match_count=match_count,
    )


def acquire_request(
    *,
    owner: str = "scenario-a-controller",
    observed_at: datetime = NOW,
    match_count: int = 1,
    source_revision: str = REVISION,
) -> LeaseAcquireRequest:
    return LeaseAcquireRequest(
        incident_id=INCIDENT_ID,
        correlation_id=CORRELATION_ID,
        target=target(match_count=match_count),
        owner_id=owner,
        source_revision=source_revision,
        policy_version=POLICY_VERSION,
        observed_at_utc=observed_at,
        evidence_fresh_until_utc=observed_at + timedelta(seconds=20),
    )


def approval_for(
    *,
    approval_id: str,
    nonce: str,
    target_digest: str,
    digest: str,
    issued_at: datetime = NOW,
    expires_at: datetime | None = None,
):
    return recovery_approval(
        approval_id=approval_id,
        incident_id=INCIDENT_ID,
        correlation_id=CORRELATION_ID,
        target_identity_digest=target_digest,
        action_digest=digest,
        source_revision=REVISION,
        policy_version=POLICY_VERSION,
        actor="maintenance-approver",
        nonce=nonce,
        issued_at_utc=issued_at,
        expires_at_utc=expires_at or issued_at + timedelta(minutes=2),
    )


def action_request(lease, approval_id: str, *, action: str = "recommend-exact-restart"):
    parameters = {"pod_uid": lease.target.identity["pod_uid"]}
    return RecoveryActionRequest(
        incident_id=INCIDENT_ID,
        correlation_id=CORRELATION_ID,
        target=lease.target,
        owner_id=lease.owner_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        action=action,
        parameters=parameters,
        action_digest=action_digest(action, parameters),
        approval_id=approval_id,
        source_revision=REVISION,
        policy_version=POLICY_VERSION,
        observed_at_utc=NOW + timedelta(seconds=2),
    )


def test_exact_owner_is_deduped_and_conflict_fails_closed(tmp_path: Path) -> None:
    store = RecoveryCoordinationStore(tmp_path, policy())
    acquired = store.acquire(acquire_request())
    repeated = store.acquire(acquire_request(observed_at=NOW + timedelta(seconds=1)))
    conflict = store.acquire(
        acquire_request(owner="scenario-b-controller", observed_at=NOW + timedelta(seconds=2))
    )

    assert acquired.result == "acquired"
    assert repeated.result == "deduped"
    assert repeated.lease == acquired.lease
    assert conflict.result == "blocked"
    assert conflict.blockers == ["owner_conflict"]
    assert conflict.mutation_intent_count == 0
    assert len(store.snapshot().leases) == 1


def test_missing_ambiguous_stale_and_revision_mismatch_targets_block(tmp_path: Path) -> None:
    store = RecoveryCoordinationStore(tmp_path, policy())
    missing = store.acquire(acquire_request(match_count=0))
    ambiguous = store.acquire(acquire_request(match_count=2))
    stale_request = acquire_request(observed_at=NOW)
    stale = store.acquire(
        stale_request.model_copy(update={"evidence_fresh_until_utc": NOW})
    )
    wrong_revision = store.acquire(acquire_request(source_revision="1" * 40))

    assert missing.blockers == ["target_missing"]
    assert ambiguous.blockers == ["target_ambiguous"]
    assert stale.blockers == ["evidence_stale"]
    assert wrong_revision.blockers == ["revision_mismatch"]
    assert len(store.snapshot().leases) == 0


def test_expired_owner_is_replaced_only_with_higher_fence(tmp_path: Path) -> None:
    store = RecoveryCoordinationStore(tmp_path, policy())
    first = store.acquire(acquire_request()).lease
    assert first is not None
    second = store.acquire(
        acquire_request(
            owner="scenario-b-controller",
            observed_at=NOW + timedelta(seconds=21),
        )
    ).lease
    assert second is not None

    assert second.fencing_token == first.fencing_token + 1
    assert store.snapshot().leases[first.lease_id].state == "expired"
    stale_renewal = store.renew(
        first.lease_id,
        owner_id=first.owner_id,
        fencing_token=first.fencing_token,
        observed_at_utc=NOW + timedelta(seconds=22),
    )
    assert stale_renewal.result == "blocked"
    assert "lease_inactive" in stale_renewal.blockers


def test_renew_and_release_require_exact_owner_and_fence(tmp_path: Path) -> None:
    store = RecoveryCoordinationStore(tmp_path, policy())
    lease = store.acquire(acquire_request()).lease
    assert lease is not None

    mismatch = store.renew(
        lease.lease_id,
        owner_id=lease.owner_id,
        fencing_token=lease.fencing_token + 1,
        observed_at_utc=NOW + timedelta(seconds=4),
    )
    renewed = store.renew(
        lease.lease_id,
        owner_id=lease.owner_id,
        fencing_token=lease.fencing_token,
        observed_at_utc=NOW + timedelta(seconds=5),
    )
    released = store.release(
        lease.lease_id,
        owner_id=lease.owner_id,
        fencing_token=lease.fencing_token,
        observed_at_utc=NOW + timedelta(seconds=6),
    )

    assert mismatch.blockers == ["fence_mismatch"]
    assert renewed.result == "renewed"
    assert released.result == "released"
    assert not store.snapshot().active_lease_index


def test_valid_approval_authorizes_one_recommendation_across_restart(tmp_path: Path) -> None:
    store = RecoveryCoordinationStore(tmp_path, policy())
    lease = store.acquire(acquire_request()).lease
    assert lease is not None
    request = action_request(lease, "approval-current-0001")
    approval = approval_for(
        approval_id=request.approval_id,
        nonce="nonce-current-0001",
        target_digest=lease.target.identity_digest,
        digest=request.action_digest,
    )
    recorded = store.record_approval(approval, observed_at_utc=NOW + timedelta(seconds=1))
    authorized = store.authorize(request)

    restarted = RecoveryCoordinationStore(tmp_path, policy())
    replayed = restarted.authorize(request.model_copy(update={"observed_at_utc": NOW + timedelta(seconds=3)}))

    assert recorded.result == "recorded"
    assert authorized.result == "authorized"
    assert authorized.recommendation_count == 1
    assert authorized.mutation_intent_count == 0
    assert authorized.action is not None
    assert authorized.action.external_mutation_dispatched is False
    assert replayed.result == "deduped"
    assert replayed.action == authorized.action
    assert len(restarted.snapshot().actions) == 1


def test_expired_or_mismatched_approval_emits_no_recommendation(tmp_path: Path) -> None:
    store = RecoveryCoordinationStore(tmp_path, policy())
    lease = store.acquire(acquire_request()).lease
    assert lease is not None
    request = action_request(lease, "approval-expired-0001")
    expired = approval_for(
        approval_id=request.approval_id,
        nonce="nonce-expired-0001",
        target_digest=lease.target.identity_digest,
        digest=request.action_digest,
        issued_at=NOW - timedelta(minutes=3),
        expires_at=NOW - timedelta(seconds=1),
    )
    rejected = store.record_approval(expired, observed_at_utc=NOW)
    no_approval = store.authorize(request)

    assert rejected.blockers == ["approval_expired"]
    assert no_approval.blockers == ["approval_missing"]
    assert no_approval.recommendation_count == 0
    assert no_approval.mutation_intent_count == 0
    assert not store.snapshot().actions


def test_approval_target_and_action_mismatch_fail_closed(tmp_path: Path) -> None:
    store = RecoveryCoordinationStore(tmp_path, policy())
    lease = store.acquire(acquire_request()).lease
    assert lease is not None
    request = action_request(lease, "approval-mismatch-0001")
    approval = approval_for(
        approval_id=request.approval_id,
        nonce="nonce-mismatch-0001",
        target_digest=target(pod_uid="other-pod-uid-0002").identity_digest,
        digest=action_digest("recommend-rollback", {}),
    )
    assert store.record_approval(approval, observed_at_utc=NOW + timedelta(seconds=1)).admitted

    decision = store.authorize(request)
    assert set(decision.blockers) == {"approval_action_mismatch", "approval_target_mismatch"}
    assert decision.recommendation_count == 0
    assert decision.mutation_intent_count == 0


def test_consumed_nonce_cannot_authorize_a_different_action(tmp_path: Path) -> None:
    store = RecoveryCoordinationStore(tmp_path, policy())
    lease = store.acquire(acquire_request()).lease
    assert lease is not None
    first_request = action_request(lease, "approval-shared-0001")
    approval = approval_for(
        approval_id=first_request.approval_id,
        nonce="nonce-shared-0001",
        target_digest=lease.target.identity_digest,
        digest=first_request.action_digest,
    )
    store.record_approval(approval, observed_at_utc=NOW + timedelta(seconds=1))
    assert store.authorize(first_request).admitted

    second_parameters: dict[str, str] = {}
    second = first_request.model_copy(
        update={
            "action": "recommend-rollback",
            "parameters": second_parameters,
            "action_digest": action_digest("recommend-rollback", second_parameters),
            "observed_at_utc": NOW + timedelta(seconds=3),
        }
    )
    blocked = store.authorize(second)
    assert set(blocked.blockers) == {"approval_action_mismatch", "approval_replayed"}
    assert len(store.snapshot().actions) == 1


def test_incident_plane_is_read_only_and_preserves_timing_and_evidence(tmp_path: Path) -> None:
    correlation_root = tmp_path / "correlation"
    coordination_root = tmp_path / "coordination"
    incident_path = correlation_root / "incidents" / f"{INCIDENT_ID}.json"
    incident_path.parent.mkdir(parents=True)
    incident = IncidentRecord(
        schema_version="evm.cross_scenario_incident.v1",
        incident_id=INCIDENT_ID,
        correlation_id=CORRELATION_ID,
        root_fingerprint="f" * 64,
        root_event_id="event-root-0001",
        state="recovery_pending",
        event_ids=["event-root-0001"],
        created_at=NOW,
        updated_at=NOW + timedelta(seconds=1),
    )
    incident_path.write_text(
        json.dumps(incident.model_dump(mode="json"), default=str),
        encoding="utf-8",
    )
    store = RecoveryCoordinationStore(coordination_root, policy())
    lease = store.acquire(acquire_request()).lease
    assert lease is not None
    snapshot = build_incident_plane_snapshot(
        correlation_root=correlation_root,
        coordination_store=store,
        generated_at_utc=NOW + timedelta(seconds=2),
        evidence_root="F:/evidence/recovery-proof",
        timing_by_incident={
            INCIDENT_ID: IncidentTiming(
                collection_delay_ms=5000,
                correlation_overhead_ms=31.2,
                containment_seconds=0.2,
                recovery_seconds=10.1,
            )
        },
        child_evidence_by_incident={INCIDENT_ID: ["F:/evidence/scenario-a.json"]},
    )
    output = tmp_path / "incident-plane.json"
    write_incident_plane_snapshot(output, snapshot)

    loaded = type(snapshot).model_validate_json(output.read_text(encoding="utf-8"))
    record = loaded.incidents[0]
    assert loaded.mutation_endpoint_available is False
    assert record.owner_id == "scenario-a-controller"
    assert record.fencing_token == 1
    assert record.timing.correlation_overhead_ms == 31.2
    assert record.child_evidence_uris == ["F:/evidence/scenario-a.json"]

