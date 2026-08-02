from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evm.control_panel.lifecycle_guards import file_digest
from evm.control_panel.lifecycle_quality_guard import (
    LifecycleQualityGuardBlocked,
    LifecycleQualityReviewActionRequest,
    LifecycleQualityReviewRegistration,
    apply_quality_review_action,
    authorize_training,
    load_quality_review,
    quality_review_path,
    register_quality_review,
)
from evm.operations.scenario_c_quality import (
    RetrainingCandidate,
    RetrainingProfile,
    ReviewEvent,
    ScenarioCIdentity,
    ScenarioCPolicy,
    payload_sha256,
)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=str), encoding="utf-8")


def policy() -> ScenarioCPolicy:
    return ScenarioCPolicy(
        schema_version="evm.scenario_c_policy.v1",
        policy_id="scenario-c-test",
        max_batch_decision_seconds=300,
        max_missing_required_fields=0,
        max_duplicate_sample_ids=0,
        max_duplicate_content_digests=0,
        min_label_coverage=1,
        min_content_digest_coverage=1,
        max_input_category_js=0.1,
        max_predicted_class_js=0.05,
        max_confidence_psi=0.1,
        max_mean_confidence_drop=0.05,
        max_low_confidence_rate_increase=0.1,
        max_accuracy_drop=0.05,
        max_f1_drop=0.1,
        low_confidence_threshold=0.7,
        signal_precedence=[
            "identity",
            "schema",
            "data_distribution",
            "model_quality",
        ],
    )


def fixture_bundle(tmp_path: Path):
    source_commit = "a" * 40
    run = SimpleNamespace(
        run_id="lifecycle-quality-test",
        profile_id="profile-c",
        profile_version=9,
        profile_digest="b" * 64,
        effective_config_digest="c" * 64,
        lifecycle_series_id="series-quality-test",
        attempt_id="attempt-quality-test",
        correlation_id="correlation-quality-test",
        source_commit=source_commit,
        artifact_root=str(tmp_path / "run"),
        actor="requester@example.com",
    )
    identity = ScenarioCIdentity(
        dataset_id="visa",
        dataset_version="visa-v1",
        shard_index_sha256="1" * 64,
        baseline_candidate_id="stable-b0",
        baseline_architecture="efficientnet-b0",
        baseline_model_sha256="2" * 64,
        ct_snapshot_id="ct-v1",
        ct_manifest_sha256="3" * 64,
        source_revision=source_commit,
    )
    event = ReviewEvent(
        event_id="quality-review-test",
        fingerprint="4" * 64,
        policy_id="scenario-c-test",
        identity_digest=payload_sha256(identity.model_dump(mode="json")),
        baseline_window_digest="5" * 64,
        current_window_digest="6" * 64,
        decision="review_required",
        triggered_rules=["input_category_js"],
        metrics={"input_category_js": 0.75},
        affected_slices=["pcb3"],
        created_at=datetime(2026, 8, 2, tzinfo=UTC),
    )
    derived = tmp_path / "derived.jsonl"
    derived.write_text('{"sample_id":"pcb3-1"}\n', encoding="utf-8")
    candidate = RetrainingCandidate(
        candidate_id="retrain-quality-test",
        candidate_digest="7" * 64,
        event_id=event.event_id,
        event_fingerprint=event.fingerprint,
        dataset_version=identity.dataset_version,
        derived_manifest_digest=file_digest(derived),
        baseline_model_digest=identity.baseline_model_sha256,
        training_profile=RetrainingProfile(
            profile_id="visa-b0-retrain",
            architecture="efficientnet-b0",
            framework="torch",
            seed=20260802,
            max_epochs=20,
            early_stop_patience=4,
            metric_names=["accuracy", "f1", "auroc"],
        ),
        training_profile_digest=payload_sha256(
            RetrainingProfile(
                profile_id="visa-b0-retrain",
                architecture="efficientnet-b0",
                framework="torch",
                seed=20260802,
                max_epochs=20,
                early_stop_patience=4,
                metric_names=["accuracy", "f1", "auroc"],
            ).model_dump(mode="json")
        ),
        source_revision=source_commit,
        requested_ct_snapshot_id=identity.ct_snapshot_id,
        requested_ct_digest=identity.ct_manifest_sha256,
        created_at=datetime(2026, 8, 2, tzinfo=UTC),
    )
    paths = {
        "policy": tmp_path / "policy.json",
        "identity": tmp_path / "identity.json",
        "event": tmp_path / "event.json",
        "candidate": tmp_path / "candidate.json",
        "derived": derived,
    }
    for key, value in (
        ("policy", policy()),
        ("identity", identity),
        ("event", event),
        ("candidate", candidate),
    ):
        write_json(paths[key], value.model_dump(mode="json"))
    observed_at = datetime.now(UTC)
    request = LifecycleQualityReviewRegistration(
        actor="drift-monitor",
        reason="Bind deterministic drift evidence to the exact lifecycle run",
        expected_version=1,
        policy_uri=str(paths["policy"]),
        policy_sha256=file_digest(paths["policy"]),
        identity_uri=str(paths["identity"]),
        identity_sha256=file_digest(paths["identity"]),
        review_event_uri=str(paths["event"]),
        review_event_sha256=file_digest(paths["event"]),
        retraining_candidate_uri=str(paths["candidate"]),
        retraining_candidate_sha256=file_digest(paths["candidate"]),
        derived_manifest_uri=str(paths["derived"]),
        derived_manifest_sha256=file_digest(paths["derived"]),
        observed_at=observed_at,
    )
    return run, request, paths


def action(review, *, action: str, actor: str = "quality-owner"):
    return LifecycleQualityReviewActionRequest(
        actor=actor,
        reason=f"Exercise governed {action} decision path",
        expected_version=1,
        expected_review_version=review.review_version,
        action=action,
        candidate_id=review.candidate_id,
        candidate_digest=review.candidate_digest,
        approval_ttl_seconds=3600,
    )


def test_registration_deduplicates_exact_and_stale_signals(tmp_path: Path) -> None:
    run, request, _paths = fixture_bundle(tmp_path)

    first, created = register_quality_review(run, request)
    duplicate, duplicate_created = register_quality_review(run, request)
    stale_request = request.model_copy(
        update={"observed_at": request.observed_at - timedelta(minutes=5)}
    )
    stale, stale_created = register_quality_review(run, stale_request)

    assert created is True
    assert duplicate_created is False
    assert stale_created is False
    assert stale.registration_attempts == 3
    assert stale.duplicate_attempts == 2
    assert stale.stale_attempts == 2
    assert stale.event_id == first.event_id == duplicate.event_id
    assert stale.candidate_id == first.candidate_id == duplicate.candidate_id
    assert load_quality_review(run) == stale


def test_quality_review_uses_configured_writable_lifecycle_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, request, _paths = fixture_bundle(tmp_path)
    writable_root = tmp_path / "api-artifacts" / "w7" / "lifecycle_runs"
    monkeypatch.setenv("EVM_LIFECYCLE_RUN_ROOT", str(writable_root))

    review, created = register_quality_review(run, request)

    expected = writable_root / run.run_id / "quality" / "scenario-c-review.json"
    assert created is True
    assert quality_review_path(run) == expected
    assert expected.is_file()
    assert load_quality_review(run) == review


def test_manual_hold_then_independent_approval_is_consumed_once(tmp_path: Path) -> None:
    run, request, _paths = fixture_bundle(tmp_path)
    review, _created = register_quality_review(run, request)

    with pytest.raises(LifecycleQualityGuardBlocked, match="quality_review_training_hold"):
        authorize_training(run)
    held = apply_quality_review_action(run, action(review, action="manual_hold"))
    with pytest.raises(LifecycleQualityGuardBlocked, match="quality_review_training_hold"):
        authorize_training(run)
    approved = apply_quality_review_action(
        run,
        action(held, action="approve_for_training"),
    )
    validated, consumed_during_validation = authorize_training(run, consume=False)
    consumed, consumed_now = authorize_training(run)
    replay, replay_consumed = authorize_training(run)

    assert approved.state == "approved_for_training"
    assert validated is not None and consumed_during_validation is False
    assert consumed is not None and consumed_now is True
    assert replay is not None and replay_consumed is False
    assert replay.approval_consumption_count == 1
    assert [item.event for item in replay.audit][-3:] == [
        "quality_review_manual_hold",
        "quality_review_training_approved",
        "quality_review_training_approval_consumed",
    ]


def test_reject_and_separation_of_duties_fail_closed(tmp_path: Path) -> None:
    run, request, _paths = fixture_bundle(tmp_path)
    review, _created = register_quality_review(run, request)

    with pytest.raises(
        LifecycleQualityGuardBlocked,
        match="quality_review_action_blocked",
    ):
        apply_quality_review_action(
            run,
            action(
                review,
                action="approve_for_training",
                actor=run.actor,
            ),
        )
    rejected = apply_quality_review_action(run, action(review, action="reject"))
    with pytest.raises(
        LifecycleQualityGuardBlocked,
        match="quality_review_candidate_rejected",
    ):
        authorize_training(run)
    assert rejected.state == "rejected"


def test_registration_rejects_tampered_derived_manifest(tmp_path: Path) -> None:
    run, request, paths = fixture_bundle(tmp_path)
    paths["derived"].write_text('{"sample_id":"tampered"}\n', encoding="utf-8")

    with pytest.raises(
        LifecycleQualityGuardBlocked,
        match="quality_review_evidence_digest_mismatch",
    ):
        register_quality_review(run, request)
