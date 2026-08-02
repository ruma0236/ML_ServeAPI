from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import Field, model_validator

from evm.control_panel.lifecycle_guards import canonical_digest, file_digest
from evm.control_panel.readiness_evaluator import runtime_path
from evm.control_panel.schemas import ContractModel
from evm.operations.scenario_c_quality import (
    RetrainingCandidate,
    ReviewEvent,
    ScenarioCIdentity,
    ScenarioCPolicy,
    payload_sha256,
)


QUALITY_REVIEW_SCHEMA = "evm.lifecycle_quality_review.v1"
QualityReviewState = Literal[
    "review_required",
    "manual_hold",
    "rejected",
    "approved_for_training",
]
QualityReviewAction = Literal[
    "manual_hold",
    "reject",
    "approve_for_training",
]


class LifecycleRunIdentity(Protocol):
    run_id: str
    profile_id: str
    profile_version: int
    profile_digest: str
    effective_config_digest: str
    lifecycle_series_id: str | None
    attempt_id: str | None
    correlation_id: str | None
    source_commit: str | None
    artifact_root: str
    actor: str


class LifecycleQualityReviewRegistration(ContractModel):
    actor: str = Field(min_length=2)
    reason: str = Field(min_length=8)
    expected_version: int = Field(ge=1)
    policy_uri: str = Field(min_length=1)
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    identity_uri: str = Field(min_length=1)
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_event_uri: str = Field(min_length=1)
    review_event_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    retraining_candidate_uri: str = Field(min_length=1)
    retraining_candidate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    derived_manifest_uri: str = Field(min_length=1)
    derived_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: datetime

    @model_validator(mode="after")
    def validate_utc(self) -> "LifecycleQualityReviewRegistration":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("observed_at must be timezone-aware UTC")
        return self


class LifecycleQualityReviewActionRequest(ContractModel):
    actor: str = Field(min_length=2)
    reason: str = Field(min_length=8)
    expected_version: int = Field(ge=1)
    expected_review_version: int = Field(ge=1)
    action: QualityReviewAction
    candidate_id: str = Field(min_length=1)
    candidate_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_ttl_seconds: int = Field(default=3600, ge=60, le=604800)


class QualityReviewAudit(ContractModel):
    sequence: int = Field(ge=1)
    event: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    occurred_at: datetime
    action_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class LifecycleQualityReview(ContractModel):
    schema_version: Literal["evm.lifecycle_quality_review.v1"] = QUALITY_REVIEW_SCHEMA
    review_version: int = Field(ge=1)
    run_id: str
    profile_id: str
    profile_version: int
    profile_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    effective_config_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    lifecycle_series_id: str
    attempt_id: str
    correlation_id: str
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    policy_id: str
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    event_id: str
    event_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_event_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_id: str
    candidate_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    retraining_candidate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    derived_manifest_uri: str
    derived_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    baseline_window_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    current_window_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    triggered_rules: list[str] = Field(min_length=1)
    affected_slices: list[str] = Field(min_length=1)
    metrics: dict[str, float]
    thresholds: dict[str, float]
    state: QualityReviewState
    registration_attempts: int = Field(ge=1)
    duplicate_attempts: int = Field(ge=0)
    stale_attempts: int = Field(ge=0)
    registered_by: str
    registered_at: datetime
    latest_observed_at: datetime
    action_actor: str | None = None
    action_reason: str | None = None
    action_review_version: int | None = Field(default=None, ge=1)
    action_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    action_expires_at: datetime | None = None
    approval_consumed_at: datetime | None = None
    approval_consumption_count: int = Field(default=0, ge=0, le=1)
    audit: list[QualityReviewAudit] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_governance_state(self) -> "LifecycleQualityReview":
        for value in (
            self.registered_at,
            self.latest_observed_at,
            self.action_expires_at,
            self.approval_consumed_at,
        ):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() != timedelta(0)
            ):
                raise ValueError("quality review timestamps must be UTC")
        if self.state == "approved_for_training":
            if not all(
                (
                    self.action_actor,
                    self.action_reason,
                    self.action_review_version,
                    self.action_digest,
                    self.action_expires_at,
                )
            ):
                raise ValueError("training approval requires a complete action binding")
        if self.approval_consumption_count == 1 and self.approval_consumed_at is None:
            raise ValueError("consumed approval requires consumed_at")
        if self.approval_consumed_at is not None and self.approval_consumption_count != 1:
            raise ValueError("approval consumption must be single-use")
        return self


class LifecycleQualityGuardBlocked(RuntimeError):
    def __init__(self, code: str, blockers: list[str] | None = None):
        self.code = code
        self.blockers = sorted(set(blockers or [code]))
        super().__init__(code)


def utc_now() -> datetime:
    return datetime.now(UTC)


def quality_review_path(run: LifecycleRunIdentity) -> Path:
    lifecycle_root = os.getenv("EVM_LIFECYCLE_RUN_ROOT")
    if lifecycle_root:
        return (
            Path(lifecycle_root)
            / run.run_id
            / "quality"
            / "scenario-c-review.json"
        )
    return runtime_path(run.artifact_root) / "quality" / "scenario-c-review.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleQualityGuardBlocked(
            "quality_review_evidence_invalid",
            [f"quality_review_evidence_invalid:{path}"],
        ) from exc
    if not isinstance(payload, dict):
        raise LifecycleQualityGuardBlocked(
            "quality_review_evidence_invalid",
            [f"quality_review_evidence_not_object:{path}"],
        )
    return payload


def _required_file(uri: str, expected_sha256: str, label: str) -> Path:
    path = runtime_path(uri)
    if not path.is_file():
        raise LifecycleQualityGuardBlocked(
            "quality_review_evidence_missing",
            [f"{label}_missing:{uri}"],
        )
    observed = file_digest(path)
    if observed != expected_sha256:
        raise LifecycleQualityGuardBlocked(
            "quality_review_evidence_digest_mismatch",
            [f"{label}_digest_mismatch:expected={expected_sha256}:actual={observed}"],
        )
    return path


def _run_identity_blockers(
    run: LifecycleRunIdentity,
    review: LifecycleQualityReview,
) -> list[str]:
    expected = {
        "run_id": run.run_id,
        "profile_id": run.profile_id,
        "profile_version": run.profile_version,
        "profile_digest": run.profile_digest,
        "effective_config_digest": run.effective_config_digest,
        "lifecycle_series_id": run.lifecycle_series_id,
        "attempt_id": run.attempt_id,
        "correlation_id": run.correlation_id,
        "source_commit": run.source_commit,
    }
    blockers = [
        f"quality_review_{key}_mismatch"
        for key, value in expected.items()
        if getattr(review, key) != value
    ]
    return sorted(blockers)


def _action_digest(
    run: LifecycleRunIdentity,
    review: LifecycleQualityReview,
    *,
    action_review_version: int,
    actor: str,
    reason: str,
    action: QualityReviewAction,
    expires_at: datetime | None,
) -> str:
    return canonical_digest(
        {
            "run_id": run.run_id,
            "profile_digest": run.profile_digest,
            "effective_config_digest": run.effective_config_digest,
            "source_commit": run.source_commit,
            "event_fingerprint": review.event_fingerprint,
            "candidate_id": review.candidate_id,
            "candidate_digest": review.candidate_digest,
            "review_version": action_review_version,
            "actor": actor,
            "reason": reason,
            "action": action,
            "expires_at": expires_at.isoformat() if expires_at else None,
        }
    )


def _write_review(path: Path, review: LifecycleQualityReview) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(review.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_quality_review(run: LifecycleRunIdentity) -> LifecycleQualityReview | None:
    path = quality_review_path(run)
    if not path.is_file():
        return None
    review = LifecycleQualityReview.model_validate(_read_json(path))
    blockers = _run_identity_blockers(run, review)
    if blockers:
        raise LifecycleQualityGuardBlocked("quality_review_identity_mismatch", blockers)
    return review


def register_quality_review(
    run: LifecycleRunIdentity,
    request: LifecycleQualityReviewRegistration,
) -> tuple[LifecycleQualityReview, bool]:
    required_identity = (
        run.lifecycle_series_id,
        run.attempt_id,
        run.correlation_id,
        run.source_commit,
    )
    if not all(required_identity):
        raise LifecycleQualityGuardBlocked("quality_review_run_identity_incomplete")
    policy_path = _required_file(request.policy_uri, request.policy_sha256, "policy")
    identity_path = _required_file(request.identity_uri, request.identity_sha256, "identity")
    event_path = _required_file(
        request.review_event_uri,
        request.review_event_sha256,
        "review_event",
    )
    candidate_path = _required_file(
        request.retraining_candidate_uri,
        request.retraining_candidate_sha256,
        "retraining_candidate",
    )
    _required_file(
        request.derived_manifest_uri,
        request.derived_manifest_sha256,
        "derived_manifest",
    )
    policy = ScenarioCPolicy.model_validate(_read_json(policy_path))
    identity = ScenarioCIdentity.model_validate(_read_json(identity_path))
    event = ReviewEvent.model_validate(_read_json(event_path))
    candidate = RetrainingCandidate.model_validate(_read_json(candidate_path))
    blockers: list[str] = []
    if identity.source_revision != run.source_commit:
        blockers.append("quality_review_source_revision_mismatch")
    if event.policy_id != policy.policy_id:
        blockers.append("quality_review_policy_id_mismatch")
    if event.identity_digest != payload_sha256(identity.model_dump(mode="json")):
        blockers.append("quality_review_identity_digest_mismatch")
    if candidate.event_id != event.event_id:
        blockers.append("quality_review_candidate_event_mismatch")
    if candidate.event_fingerprint != event.fingerprint:
        blockers.append("quality_review_candidate_fingerprint_mismatch")
    if candidate.derived_manifest_digest != request.derived_manifest_sha256:
        blockers.append("quality_review_derived_manifest_mismatch")
    if candidate.source_revision != run.source_commit:
        blockers.append("quality_review_candidate_revision_mismatch")
    if candidate.dataset_version != identity.dataset_version:
        blockers.append("quality_review_candidate_dataset_mismatch")
    if candidate.baseline_model_digest != identity.baseline_model_sha256:
        blockers.append("quality_review_candidate_baseline_model_mismatch")
    if candidate.requested_ct_snapshot_id != identity.ct_snapshot_id:
        blockers.append("quality_review_candidate_ct_snapshot_mismatch")
    if candidate.requested_ct_digest != identity.ct_manifest_sha256:
        blockers.append("quality_review_candidate_ct_digest_mismatch")
    if event.decision != "review_required":
        blockers.append("quality_review_decision_not_review_required")
    if blockers:
        raise LifecycleQualityGuardBlocked("quality_review_registration_blocked", blockers)

    path = quality_review_path(run)
    existing = load_quality_review(run)
    if existing is not None:
        if (
            existing.event_fingerprint != event.fingerprint
            or existing.candidate_digest != candidate.candidate_digest
        ):
            raise LifecycleQualityGuardBlocked(
                "quality_review_registration_conflict",
                ["quality_review_exact_run_already_bound"],
            )
        stale = request.observed_at <= existing.latest_observed_at
        now = utc_now()
        audit = [
            *existing.audit,
            QualityReviewAudit(
                sequence=len(existing.audit) + 1,
                event="quality_review_signal_replayed",
                actor=request.actor,
                reason=request.reason,
                occurred_at=now,
            ),
        ]
        updated = existing.model_copy(
            update={
                "review_version": existing.review_version + 1,
                "registration_attempts": existing.registration_attempts + 1,
                "duplicate_attempts": existing.duplicate_attempts + 1,
                "stale_attempts": existing.stale_attempts + int(stale),
                "latest_observed_at": max(
                    existing.latest_observed_at,
                    request.observed_at,
                ),
                "audit": audit,
            }
        )
        _write_review(path, updated)
        return updated, False

    now = utc_now()
    review = LifecycleQualityReview(
        review_version=1,
        run_id=run.run_id,
        profile_id=run.profile_id,
        profile_version=run.profile_version,
        profile_digest=run.profile_digest,
        effective_config_digest=run.effective_config_digest,
        lifecycle_series_id=str(run.lifecycle_series_id),
        attempt_id=str(run.attempt_id),
        correlation_id=str(run.correlation_id),
        source_commit=str(run.source_commit),
        policy_id=policy.policy_id,
        policy_sha256=request.policy_sha256,
        identity_sha256=request.identity_sha256,
        event_id=event.event_id,
        event_fingerprint=event.fingerprint,
        review_event_sha256=request.review_event_sha256,
        candidate_id=candidate.candidate_id,
        candidate_digest=candidate.candidate_digest,
        retraining_candidate_sha256=request.retraining_candidate_sha256,
        derived_manifest_uri=request.derived_manifest_uri,
        derived_manifest_sha256=request.derived_manifest_sha256,
        baseline_window_digest=event.baseline_window_digest,
        current_window_digest=event.current_window_digest,
        triggered_rules=sorted(event.triggered_rules),
        affected_slices=sorted(event.affected_slices),
        metrics=event.metrics,
        thresholds={
            signal_id: threshold
            for signal_id, threshold in {
                **policy.drift_thresholds(),
                "accuracy_drop": policy.max_accuracy_drop,
                "f1_drop": policy.max_f1_drop,
            }.items()
            if signal_id in event.triggered_rules
        },
        state="review_required",
        registration_attempts=1,
        duplicate_attempts=0,
        stale_attempts=0,
        registered_by=request.actor,
        registered_at=now,
        latest_observed_at=request.observed_at,
        audit=[
            QualityReviewAudit(
                sequence=1,
                event="quality_review_registered",
                actor=request.actor,
                reason=request.reason,
                occurred_at=now,
            )
        ],
    )
    _write_review(path, review)
    return review, True


def apply_quality_review_action(
    run: LifecycleRunIdentity,
    request: LifecycleQualityReviewActionRequest,
) -> LifecycleQualityReview:
    review = load_quality_review(run)
    if review is None:
        raise LifecycleQualityGuardBlocked("quality_review_missing")
    blockers: list[str] = []
    if request.expected_review_version != review.review_version:
        blockers.append("quality_review_version_conflict")
    if request.candidate_id != review.candidate_id:
        blockers.append("quality_review_candidate_id_mismatch")
    if request.candidate_digest != review.candidate_digest:
        blockers.append("quality_review_candidate_digest_mismatch")
    if request.actor == run.actor:
        blockers.append("quality_review_separation_of_duties_failed")
    if review.state == "rejected":
        blockers.append("quality_review_already_rejected")
    if review.state == "approved_for_training":
        blockers.append("quality_review_already_approved")
    if blockers:
        raise LifecycleQualityGuardBlocked("quality_review_action_blocked", blockers)

    now = utc_now()
    expires_at = (
        now + timedelta(seconds=request.approval_ttl_seconds)
        if request.action == "approve_for_training"
        else None
    )
    digest = _action_digest(
        run,
        review,
        action_review_version=review.review_version + 1,
        actor=request.actor,
        reason=request.reason,
        action=request.action,
        expires_at=expires_at,
    )
    state: QualityReviewState = {
        "manual_hold": "manual_hold",
        "reject": "rejected",
        "approve_for_training": "approved_for_training",
    }[request.action]  # type: ignore[assignment]
    event = {
        "manual_hold": "quality_review_manual_hold",
        "reject": "quality_review_rejected",
        "approve_for_training": "quality_review_training_approved",
    }[request.action]
    updated = review.model_copy(
        update={
            "review_version": review.review_version + 1,
            "state": state,
            "action_actor": request.actor,
            "action_reason": request.reason,
            "action_review_version": review.review_version + 1,
            "action_digest": digest,
            "action_expires_at": expires_at,
            "audit": [
                *review.audit,
                QualityReviewAudit(
                    sequence=len(review.audit) + 1,
                    event=event,
                    actor=request.actor,
                    reason=request.reason,
                    occurred_at=now,
                    action_digest=digest,
                ),
            ],
        }
    )
    _write_review(quality_review_path(run), updated)
    return updated


def authorize_training(
    run: LifecycleRunIdentity,
    *,
    consume: bool = True,
) -> tuple[LifecycleQualityReview | None, bool]:
    review = load_quality_review(run)
    if review is None:
        return None, False
    if review.state in {"review_required", "manual_hold"}:
        raise LifecycleQualityGuardBlocked(
            "quality_review_training_hold",
            [
                f"quality_review_state:{review.state}",
                f"quality_review_event:{review.event_id}",
                f"quality_review_candidate:{review.candidate_id}",
            ],
        )
    if review.state == "rejected":
        raise LifecycleQualityGuardBlocked(
            "quality_review_candidate_rejected",
            [f"quality_review_candidate_rejected:{review.candidate_id}"],
        )
    now = utc_now()
    blockers: list[str] = []
    if review.action_actor == run.actor:
        blockers.append("quality_review_separation_of_duties_failed")
    if review.action_expires_at is None or review.action_expires_at <= now:
        blockers.append("quality_review_training_approval_expired")
    expected_digest = _action_digest(
        run,
        review,
        action_review_version=int(review.action_review_version or 0),
        actor=str(review.action_actor or ""),
        reason=str(review.action_reason or ""),
        action="approve_for_training",
        expires_at=review.action_expires_at,
    )
    if review.action_digest != expected_digest:
        blockers.append("quality_review_training_approval_digest_mismatch")
    if blockers:
        raise LifecycleQualityGuardBlocked("quality_review_training_approval_blocked", blockers)
    if not consume:
        return review, False
    if review.approval_consumption_count == 1:
        return review, False

    consumed = review.model_copy(
        update={
            "review_version": review.review_version + 1,
            "approval_consumed_at": now,
            "approval_consumption_count": 1,
            "audit": [
                *review.audit,
                QualityReviewAudit(
                    sequence=len(review.audit) + 1,
                    event="quality_review_training_approval_consumed",
                    actor="lifecycle-worker",
                    reason="Exact approved training plan admitted",
                    occurred_at=now,
                    action_digest=review.action_digest,
                ),
            ],
        }
    )
    _write_review(quality_review_path(run), consumed)
    return consumed, True
