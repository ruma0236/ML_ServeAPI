from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evm.operations.scenario_c_quality import (
    CandidateApproval,
    CandidateEvaluation,
    PredictionRecord,
    RegistryConflict,
    ReleaseDependencies,
    RetrainingProfile,
    ScenarioCIdentity,
    ScenarioCPolicy,
    ScenarioCRegistry,
    build_retraining_candidate,
    build_review_event,
    evaluate_candidate_gate,
    evaluate_quality_windows,
    payload_sha256,
)


NOW = datetime(2026, 8, 2, 4, 0, tzinfo=timezone.utc)


def policy() -> ScenarioCPolicy:
    return ScenarioCPolicy(
        schema_version="evm.scenario_c_policy.v1",
        policy_id="test-policy",
        max_batch_decision_seconds=300,
        max_missing_required_fields=0,
        max_duplicate_sample_ids=0,
        max_duplicate_content_digests=0,
        min_label_coverage=1,
        min_content_digest_coverage=1,
        max_input_category_js=0.10,
        max_predicted_class_js=0.05,
        max_confidence_psi=0.10,
        max_mean_confidence_drop=0.05,
        max_low_confidence_rate_increase=0.10,
        max_accuracy_drop=0.05,
        max_f1_drop=0.10,
        low_confidence_threshold=0.70,
        signal_precedence=["identity", "schema", "data_distribution", "model_quality"],
    )


def identity() -> ScenarioCIdentity:
    return ScenarioCIdentity(
        dataset_id="visa",
        dataset_version="visa-v1",
        shard_index_sha256="a" * 64,
        baseline_candidate_id="b0",
        baseline_architecture="efficientnet-b0",
        baseline_model_sha256="b" * 64,
        ct_snapshot_id="ct-v1",
        ct_manifest_sha256="c" * 64,
        source_revision="1234567",
    )


def record(index: int, category: str, actual: str, predicted: str, confidence: float):
    return PredictionRecord(
        sample_id=f"sample-{index}",
        content_sha256=f"{index:064x}",
        image_uri=f"file:///data/{index}.jpg",
        class_name=category,
        actual_label=actual,
        predicted_label=predicted,
        confidence=confidence,
    )


def baseline() -> list[PredictionRecord]:
    return [
        record(1, "pcb1", "normal", "normal", 0.95),
        record(2, "pcb2", "normal", "normal", 0.93),
        record(3, "pcb3", "anomaly", "anomaly", 0.91),
        record(4, "pcb4", "normal", "normal", 0.89),
    ]


def shifted() -> list[PredictionRecord]:
    return [
        record(11, "pcb3", "normal", "anomaly", 0.55),
        record(12, "pcb3", "normal", "anomaly", 0.58),
        record(13, "pcb3", "anomaly", "anomaly", 0.61),
        record(14, "pcb3", "normal", "normal", 0.64),
    ]


def profile() -> RetrainingProfile:
    return RetrainingProfile(
        profile_id="profile-v1",
        architecture="efficientnet-b0",
        framework="torch",
        seed=20260802,
        max_epochs=20,
        early_stop_patience=4,
        metric_names=["accuracy", "f1", "auroc"],
    )


def event_and_candidate():
    decision = evaluate_quality_windows(
        policy=policy(), baseline=baseline(), current=shifted()
    )
    event = build_review_event(
        policy=policy(),
        identity=identity(),
        baseline=baseline(),
        current=shifted(),
        decision=decision,
        affected_slices=["pcb3"],
        created_at=NOW,
    )
    candidate = build_retraining_candidate(
        event=event,
        identity=identity(),
        profile=profile(),
        derived_manifest_digest="d" * 64,
        created_at=NOW,
    )
    return event, candidate


def evaluation(candidate, *, passed: bool = True) -> CandidateEvaluation:
    if not passed:
        return CandidateEvaluation(
            evaluation_id="eval-failed",
            candidate_id=candidate.candidate_id,
            candidate_digest=candidate.candidate_digest,
            status="fail",
            ct_status="fail",
            blockers=["f1_below_minimum"],
        )
    return CandidateEvaluation(
        evaluation_id="eval-pass",
        candidate_id=candidate.candidate_id,
        candidate_digest=candidate.candidate_digest,
        status="pass",
        metrics={"accuracy": 0.95, "f1": 0.80, "auroc": 0.97},
        metric_thresholds={"accuracy": 0.90, "f1": 0.75, "auroc": 0.90},
        model_digest="e" * 64,
        mlflow_run_uri="mlflow://runs/test",
        ct_snapshot_id="ct-v1",
        ct_digest="c" * 64,
        ct_status="pass",
    )


def approval(candidate, decision: str, *, actor: str = "approver", expired: bool = False):
    return CandidateApproval(
        approval_id=f"approval-{decision}",
        candidate_id=candidate.candidate_id,
        candidate_digest=candidate.candidate_digest,
        decision=decision,
        actor=actor,
        reason=f"validate {decision} decision",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW - timedelta(seconds=1) if expired else NOW + timedelta(minutes=5),
    )


def dependencies(*, integrity: bool = False, live: bool = False):
    return ReleaseDependencies(
        scenario_b_release_controls_passed=True,
        scenario_e_integrity_passed=integrity,
        production_live_canary_authorized=live,
    )


def test_known_good_window_has_no_false_alert() -> None:
    result = evaluate_quality_windows(
        policy=policy(), baseline=baseline(), current=baseline()
    )
    assert result.state == "within_policy"
    assert result.triggered_rules == []


def test_shifted_window_creates_review_decision() -> None:
    result = evaluate_quality_windows(
        policy=policy(), baseline=baseline(), current=shifted()
    )
    assert result.state == "review_required"
    assert "input_category_js" in result.triggered_rules
    assert "accuracy_drop" in result.triggered_rules


def test_identity_mismatch_blocks_before_drift() -> None:
    result = evaluate_quality_windows(
        policy=policy(), baseline=baseline(), current=shifted(), identity_valid=False
    )
    assert result.state == "blocked_invalid_evidence"
    assert result.blocker_codes == ["identity_mismatch"]
    assert result.metrics == {}


def test_duplicate_sample_blocks_before_candidate() -> None:
    current = shifted() + [shifted()[0].model_copy()]
    result = evaluate_quality_windows(policy=policy(), baseline=baseline(), current=current)
    assert result.state == "blocked_invalid_evidence"
    assert "current_duplicate_sample_ids" in result.blocker_codes


def test_review_event_and_candidate_are_deterministic() -> None:
    event_a, candidate_a = event_and_candidate()
    event_b, candidate_b = event_and_candidate()
    assert event_a.event_id == event_b.event_id
    assert event_a.fingerprint == event_b.fingerprint
    assert candidate_a.candidate_id == candidate_b.candidate_id
    assert candidate_a.candidate_digest == candidate_b.candidate_digest
    assert candidate_a.automatic_training is False


def test_registry_deduplicates_three_retries(tmp_path) -> None:
    event, candidate = event_and_candidate()
    registry = ScenarioCRegistry(tmp_path)
    results = [registry.register(event, candidate) for _ in range(3)]
    assert results[0].event_created is True
    assert results[1].event_created is False
    assert results[2].candidate_created is False
    assert results[2].event_count == 1
    assert results[2].candidate_count == 1
    assert results[2].attempt_count == 3


def test_registry_rejects_conflicting_event_identity(tmp_path) -> None:
    event, candidate = event_and_candidate()
    registry = ScenarioCRegistry(tmp_path)
    registry.register(event, candidate)
    conflict = event.model_copy(update={"metrics": {"input_category_js": 0.99}})
    with pytest.raises(RegistryConflict, match="event_identity_payload_conflict"):
        registry.register(conflict, candidate)


@pytest.mark.parametrize(
    ("decision", "expected"),
    [("manual_hold", "manual_hold"), ("rejected", "candidate_rejected")],
)
def test_hold_and_reject_are_audited_blockers(decision, expected) -> None:
    _, candidate = event_and_candidate()
    result = evaluate_candidate_gate(
        candidate=candidate,
        evaluation=evaluation(candidate),
        approval=approval(candidate, decision),
        dependencies=dependencies(integrity=True, live=True),
        requester="requester",
        evaluated_at=NOW,
    )
    assert result.state == "blocked"
    assert expected in result.blockers
    assert result.deployment_intent_created is False


def test_same_actor_approval_is_blocked() -> None:
    _, candidate = event_and_candidate()
    result = evaluate_candidate_gate(
        candidate=candidate,
        evaluation=evaluation(candidate),
        approval=approval(candidate, "approved", actor="requester"),
        dependencies=dependencies(integrity=True, live=True),
        requester="requester",
        evaluated_at=NOW,
    )
    assert "approval_separation_of_duties_failed" in result.blockers


def test_expired_approval_is_blocked() -> None:
    _, candidate = event_and_candidate()
    result = evaluate_candidate_gate(
        candidate=candidate,
        evaluation=evaluation(candidate),
        approval=approval(candidate, "approved", expired=True),
        dependencies=dependencies(integrity=True, live=True),
        requester="requester",
        evaluated_at=NOW,
    )
    assert "approval_expired" in result.blockers


def test_scenario_e_open_blocks_approved_candidate() -> None:
    _, candidate = event_and_candidate()
    result = evaluate_candidate_gate(
        candidate=candidate,
        evaluation=evaluation(candidate),
        approval=approval(candidate, "approved"),
        dependencies=dependencies(integrity=False, live=True),
        requester="requester",
        evaluated_at=NOW,
    )
    assert result.blockers == ["scenario_e_integrity_not_passed"]
    assert result.production_mutated is False


def test_valid_approved_fixture_only_creates_limited_release_handoff() -> None:
    _, candidate = event_and_candidate()
    result = evaluate_candidate_gate(
        candidate=candidate,
        evaluation=evaluation(candidate),
        approval=approval(candidate, "approved"),
        dependencies=dependencies(integrity=True, live=True),
        requester="requester",
        evaluated_at=NOW,
    )
    assert result.state == "limited_release_handoff"
    assert result.limited_release_eligible is True
    assert result.deployment_intent_created is False
    assert result.production_mutated is False


def test_candidate_payload_has_complete_identity_linkage() -> None:
    event, candidate = event_and_candidate()
    linkage = {
        event.fingerprint,
        candidate.derived_manifest_digest,
        candidate.baseline_model_digest,
        candidate.training_profile_digest,
        candidate.requested_ct_digest,
        payload_sha256(identity().model_dump(mode="json")),
    }
    assert len(linkage) == 6
