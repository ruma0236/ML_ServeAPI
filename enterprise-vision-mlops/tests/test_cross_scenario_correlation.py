from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from evm.operations.correlation import (
    CorrelationError,
    CorrelationPolicy,
    CorrelationStore,
    load_policy,
    stable_digest,
    uuid7,
)
from evm.operations.correlation_replay import _event, run_proof


NOW = datetime(2026, 8, 2, 14, 30, tzinfo=UTC)
REVISION = "a" * 40


@pytest.fixture
def policy() -> CorrelationPolicy:
    return load_policy(
        Path("configs/operations/cross_scenario_correlation.toml"),
        revision=REVISION,
    )


def event_for(
    policy: CorrelationPolicy,
    *,
    sequence: int = 1,
    target_index: int = 0,
    observed_at: datetime = NOW,
):
    return _event(
        sequence=sequence,
        target_index=target_index,
        source_revision=REVISION,
        policy=policy,
        observed_at=observed_at,
        series_id="lifecycle-series-1234",
    )


def test_uuid7_uses_time_ordered_version_and_rfc_variant() -> None:
    first = UUID(uuid7(NOW))
    second = UUID(uuid7(NOW + timedelta(milliseconds=1)))
    assert (first.version, first.variant) == (7, "specified in RFC 4122")
    assert first.int < second.int


def test_policy_loader_pins_component_revision(policy: CorrelationPolicy) -> None:
    assert policy.policy_version == "cross-scenario-correlation-v1"
    assert set(policy.component_revisions.values()) == {REVISION}
    assert policy.collector_cadence_ms == 5_000


def test_semantic_replay_dedupes_raw_observations(tmp_path: Path, policy: CorrelationPolicy) -> None:
    store = CorrelationStore(tmp_path, policy)
    first, raw_first = event_for(policy, sequence=1)
    initial = store.ingest(first, raw_evidence=raw_first, ingested_at=NOW + timedelta(seconds=1))

    second, raw_second = event_for(policy, sequence=2)
    raw_second = {**raw_second, "collector_note": "different raw timestamp metadata"}
    second = second.model_copy(
        update={
            "evidence_digest": stable_digest(raw_second),
            "event_id": "evt-semantic-replay-0002",
        }
    )
    replay = store.ingest(second, raw_evidence=raw_second, ingested_at=NOW + timedelta(seconds=1))

    assert (initial.outcome, replay.outcome) == ("new", "deduped")
    assert initial.incident_id == replay.incident_id
    assert replay.retained_event_id == initial.source_event_id
    assert replay.duplicate_count == 2
    assert (initial.action_emitted, replay.action_emitted) == (True, False)


def test_same_timestamp_different_exact_uid_never_merges(
    tmp_path: Path,
    policy: CorrelationPolicy,
) -> None:
    store = CorrelationStore(tmp_path, policy)
    first, first_raw = event_for(policy, sequence=1, target_index=1)
    second, second_raw = event_for(policy, sequence=2, target_index=2)
    first_result = store.ingest(
        first,
        raw_evidence=first_raw,
        ingested_at=NOW + timedelta(seconds=1),
    )
    second_result = store.ingest(
        second,
        raw_evidence=second_raw,
        ingested_at=NOW + timedelta(seconds=1),
    )
    assert first_result.incident_id != second_result.incident_id
    assert len(store.snapshot().root_index) == 2


@pytest.mark.parametrize(
    ("target_count", "blocker"),
    [(0, "target_missing"), (2, "target_ambiguous")],
)
def test_mutating_zero_or_multiple_target_fails_closed(
    tmp_path: Path,
    policy: CorrelationPolicy,
    target_count: int,
    blocker: str,
) -> None:
    event, raw = event_for(policy)
    event = event.model_copy(
        update={"recommended_action": "restart_exact", "target_match_count": target_count}
    )
    result = CorrelationStore(tmp_path, policy).ingest(
        event,
        raw_evidence=raw,
        ingested_at=NOW + timedelta(seconds=1),
    )
    assert result.outcome == "blocked"
    assert blocker in result.blockers
    assert result.action_emitted is False


def test_stale_revision_and_policy_mismatch_fail_closed(
    tmp_path: Path,
    policy: CorrelationPolicy,
) -> None:
    stale, stale_raw = event_for(policy, sequence=1)
    stale_result = CorrelationStore(tmp_path / "stale", policy).ingest(
        stale,
        raw_evidence=stale_raw,
        ingested_at=NOW + timedelta(seconds=21),
    )
    mismatch, mismatch_raw = event_for(policy, sequence=2)
    mismatch = mismatch.model_copy(
        update={"source_revision": "b" * 40, "policy_version": "wrong-policy"}
    )
    mismatch_result = CorrelationStore(tmp_path / "mismatch", policy).ingest(
        mismatch,
        raw_evidence=mismatch_raw,
        ingested_at=NOW + timedelta(seconds=1),
    )
    assert (stale_result.outcome, stale_result.action_emitted) == ("blocked", False)
    assert "event_stale" in stale_result.blockers
    assert {"source_revision_mismatch", "policy_version_mismatch"}.issubset(
        mismatch_result.blockers
    )


def test_unknown_causation_is_held_without_action(tmp_path: Path, policy: CorrelationPolicy) -> None:
    event, raw = event_for(policy)
    event = event.model_copy(
        update={
            "causation_id": "evt-parent-unknown",
            "parent_incident_id": "inc-parent-unknown",
        }
    )
    result = CorrelationStore(tmp_path, policy).ingest(
        event,
        raw_evidence=raw,
        ingested_at=NOW + timedelta(seconds=1),
    )
    assert result.outcome == "held"
    assert result.blockers == ["causation_parent_unknown"]
    assert result.action_emitted is False


def test_explicit_causal_child_joins_parent_and_cycle_blocks(
    tmp_path: Path,
    policy: CorrelationPolicy,
) -> None:
    store = CorrelationStore(tmp_path, policy)
    root, root_raw = event_for(policy, sequence=1)
    root_result = store.ingest(
        root,
        raw_evidence=root_raw,
        ingested_at=NOW + timedelta(seconds=1),
    )

    child, child_raw = event_for(policy, sequence=2)
    child_inputs = {"cause": "recovery_requested", "target_uid": "uid-00000000"}
    child_raw = {**child_raw, "kind": "causal-child"}
    child = child.model_copy(
        update={
            "event_id": "evt-causal-child-0002",
            "causation_id": root.event_id,
            "parent_incident_id": root_result.incident_id,
            "event_type": "recovery_recommended",
            "cause_code": "recovery_requested",
            "decision_inputs": child_inputs,
            "semantic_identity_digest": stable_digest(child_inputs),
            "evidence_digest": stable_digest(child_raw),
        }
    )
    child_result = store.ingest(
        child,
        raw_evidence=child_raw,
        ingested_at=NOW + timedelta(seconds=1),
    )
    assert child_result.incident_id == root_result.incident_id
    assert len(store.read_incident(root_result.incident_id).edges) == 1

    with pytest.raises(CorrelationError, match="causal_cycle"):
        store.add_causal_edge(
            root_result.incident_id,
            parent_event_id=child.event_id,
            child_event_id=root.event_id,
            dependency_rule="invalid_reverse_edge",
            now=NOW + timedelta(seconds=2),
        )
    incident = store.read_incident(root_result.incident_id)
    assert (incident.state, incident.blockers) == ("blocked", ["causal_cycle"])


def test_same_fingerprint_after_ttl_creates_recurrence(
    tmp_path: Path,
    policy: CorrelationPolicy,
) -> None:
    store = CorrelationStore(tmp_path, policy)
    first, first_raw = event_for(policy, sequence=1)
    initial = store.ingest(
        first,
        raw_evidence=first_raw,
        ingested_at=NOW + timedelta(seconds=1),
    )
    later_time = NOW + timedelta(seconds=policy.dedupe_ttl_seconds + 1)
    later, later_raw = event_for(policy, sequence=2, observed_at=later_time)
    recurring = store.ingest(
        later,
        raw_evidence=later_raw,
        ingested_at=later_time + timedelta(seconds=1),
    )
    assert recurring.outcome == "recurrence"
    assert recurring.incident_id != initial.incident_id
    assert recurring.action_emitted is True


def test_restart_reuses_durable_root_and_action(tmp_path: Path, policy: CorrelationPolicy) -> None:
    first_store = CorrelationStore(tmp_path, policy)
    first, first_raw = event_for(policy, sequence=1)
    initial = first_store.ingest(
        first,
        raw_evidence=first_raw,
        ingested_at=NOW + timedelta(seconds=1),
    )
    restarted_store = CorrelationStore(tmp_path, policy)
    replay, replay_raw = event_for(policy, sequence=2)
    replay_result = restarted_store.ingest(
        replay,
        raw_evidence=replay_raw,
        ingested_at=NOW + timedelta(seconds=1),
    )
    assert replay_result.incident_id == initial.incident_id
    assert replay_result.action_emitted is False
    assert len(restarted_store.snapshot().action_index) == 1


def test_replay_proof_detects_no_false_merge_or_duplicate_action(
    tmp_path: Path,
    policy: CorrelationPolicy,
) -> None:
    proof = run_proof(
        output=tmp_path,
        policy=policy,
        source_revision=REVISION,
        series_count=1,
        event_count=50,
        unrelated_event_count=5,
        observed_at=NOW,
    )
    assert proof.passed is True
    assert proof.total_events == 50
    assert proof.total_unrelated_events == 5
    assert proof.false_merge_count == 0
    assert proof.duplicate_parent_count == 0
    assert proof.duplicate_action_count == 0
    assert (tmp_path / "artifact-index.json").is_file()
