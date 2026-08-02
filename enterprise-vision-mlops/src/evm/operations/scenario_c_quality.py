from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evm.operations.failure_scenarios import atomic_write_json, exclusive_lock
from evm.pipelines.drift_review.run import evaluate_measured_drift


SHA256_PATTERN = r"^[a-f0-9]{64}$"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def payload_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScenarioCPolicy(StrictModel):
    schema_version: Literal["evm.scenario_c_policy.v1"]
    policy_id: str = Field(min_length=1)
    max_batch_decision_seconds: float = Field(gt=0)
    max_missing_required_fields: int = Field(ge=0)
    max_duplicate_sample_ids: int = Field(ge=0)
    max_duplicate_content_digests: int = Field(ge=0)
    min_label_coverage: float = Field(ge=0, le=1)
    min_content_digest_coverage: float = Field(ge=0, le=1)
    max_input_category_js: float = Field(ge=0)
    max_predicted_class_js: float = Field(ge=0)
    max_confidence_psi: float = Field(ge=0)
    max_mean_confidence_drop: float = Field(ge=0)
    max_low_confidence_rate_increase: float = Field(ge=0)
    max_accuracy_drop: float = Field(ge=0, le=1)
    max_f1_drop: float = Field(ge=0, le=1)
    low_confidence_threshold: float = Field(ge=0, le=1)
    signal_precedence: list[
        Literal["identity", "schema", "data_distribution", "model_quality"]
    ]

    @model_validator(mode="after")
    def validate_precedence(self) -> "ScenarioCPolicy":
        expected = {"identity", "schema", "data_distribution", "model_quality"}
        if set(self.signal_precedence) != expected or len(self.signal_precedence) != 4:
            raise ValueError("signal_precedence must contain every signal family once")
        return self

    def drift_thresholds(self) -> dict[str, float]:
        return {
            "input_category_js": self.max_input_category_js,
            "predicted_class_js": self.max_predicted_class_js,
            "confidence_psi": self.max_confidence_psi,
            "mean_confidence_drop": self.max_mean_confidence_drop,
            "low_confidence_rate_increase": self.max_low_confidence_rate_increase,
        }


class ScenarioCIdentity(StrictModel):
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    shard_index_sha256: str = Field(pattern=SHA256_PATTERN)
    baseline_candidate_id: str = Field(min_length=1)
    baseline_architecture: str = Field(min_length=1)
    baseline_model_sha256: str = Field(pattern=SHA256_PATTERN)
    ct_snapshot_id: str = Field(min_length=1)
    ct_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    source_revision: str = Field(min_length=7)


class PredictionRecord(StrictModel):
    sample_id: str
    content_sha256: str
    image_uri: str
    class_name: str
    actual_label: str
    predicted_label: str
    confidence: float = Field(ge=0, le=1)


class WindowQuality(StrictModel):
    record_count: int = Field(ge=0)
    missing_required_fields: int = Field(ge=0)
    duplicate_sample_ids: int = Field(ge=0)
    duplicate_content_digests: int = Field(ge=0)
    label_coverage: float = Field(ge=0, le=1)
    content_digest_coverage: float = Field(ge=0, le=1)
    accuracy: float = Field(ge=0, le=1)
    f1_macro: float = Field(ge=0, le=1)


class MeasuredSignal(StrictModel):
    signal_id: str
    family: Literal["identity", "schema", "data_distribution", "model_quality"]
    observed: float
    threshold: float
    comparison: Literal["max", "min"]
    breached: bool


class QualityDecision(StrictModel):
    state: Literal["within_policy", "review_required", "blocked_invalid_evidence"]
    triggered_rules: list[str] = Field(default_factory=list)
    blocker_codes: list[str] = Field(default_factory=list)
    signals: list[MeasuredSignal] = Field(default_factory=list)
    baseline_quality: WindowQuality
    current_quality: WindowQuality
    metrics: dict[str, float]

    @model_validator(mode="after")
    def validate_state(self) -> "QualityDecision":
        if self.state == "within_policy" and (self.triggered_rules or self.blocker_codes):
            raise ValueError("within-policy decision cannot contain triggers or blockers")
        if self.state == "review_required" and not self.triggered_rules:
            raise ValueError("review-required decision needs a triggered rule")
        if self.state == "blocked_invalid_evidence" and not self.blocker_codes:
            raise ValueError("invalid evidence decision needs a blocker")
        return self


class ReviewEvent(StrictModel):
    schema_version: Literal["evm.scenario_c_review_event.v1"] = (
        "evm.scenario_c_review_event.v1"
    )
    event_id: str
    fingerprint: str = Field(pattern=SHA256_PATTERN)
    policy_id: str
    identity_digest: str = Field(pattern=SHA256_PATTERN)
    baseline_window_digest: str = Field(pattern=SHA256_PATTERN)
    current_window_digest: str = Field(pattern=SHA256_PATTERN)
    decision: Literal["review_required"]
    triggered_rules: list[str] = Field(min_length=1)
    metrics: dict[str, float]
    affected_slices: list[str] = Field(min_length=1)
    created_at: datetime
    automatic_retraining: Literal[False] = False
    automatic_deployment: Literal[False] = False
    automatic_promotion: Literal[False] = False

    _validate_created_at = field_validator("created_at")(_utc)


class RetrainingProfile(StrictModel):
    profile_id: str
    architecture: str
    framework: Literal["torch"]
    seed: int
    max_epochs: int = Field(ge=1)
    early_stop_patience: int = Field(ge=1)
    metric_names: list[str] = Field(min_length=1)
    automatic_training: Literal[False] = False
    automatic_deployment: Literal[False] = False
    automatic_promotion: Literal[False] = False


class RetrainingCandidate(StrictModel):
    schema_version: Literal["evm.scenario_c_retraining_candidate.v1"] = (
        "evm.scenario_c_retraining_candidate.v1"
    )
    candidate_id: str
    candidate_digest: str = Field(pattern=SHA256_PATTERN)
    event_id: str
    event_fingerprint: str = Field(pattern=SHA256_PATTERN)
    dataset_version: str
    derived_manifest_digest: str = Field(pattern=SHA256_PATTERN)
    baseline_model_digest: str = Field(pattern=SHA256_PATTERN)
    training_profile: RetrainingProfile
    training_profile_digest: str = Field(pattern=SHA256_PATTERN)
    source_revision: str
    requested_ct_snapshot_id: str
    requested_ct_digest: str = Field(pattern=SHA256_PATTERN)
    state: Literal["awaiting_manual_review"] = "awaiting_manual_review"
    created_at: datetime
    automatic_training: Literal[False] = False
    automatic_deployment: Literal[False] = False
    automatic_promotion: Literal[False] = False

    _validate_created_at = field_validator("created_at")(_utc)


class CandidateEvaluation(StrictModel):
    evaluation_id: str
    candidate_id: str
    candidate_digest: str = Field(pattern=SHA256_PATTERN)
    status: Literal["not_run", "pass", "fail"]
    metrics: dict[str, float] = Field(default_factory=dict)
    metric_thresholds: dict[str, float] = Field(default_factory=dict)
    model_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    mlflow_run_uri: str | None = None
    ct_snapshot_id: str | None = None
    ct_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    ct_status: Literal["not_run", "pass", "fail"] = "not_run"
    blockers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evaluation(self) -> "CandidateEvaluation":
        if self.status == "pass":
            required = (
                self.model_digest,
                self.mlflow_run_uri,
                self.ct_snapshot_id,
                self.ct_digest,
            )
            if not all(required) or self.ct_status != "pass" or self.blockers:
                raise ValueError("passing evaluation requires model, MLflow, and CT evidence")
        elif not self.blockers:
            raise ValueError("non-passing evaluation requires blockers")
        return self


class CandidateApproval(StrictModel):
    approval_id: str
    candidate_id: str
    candidate_digest: str = Field(pattern=SHA256_PATTERN)
    decision: Literal["manual_hold", "rejected", "approved"]
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=8)
    issued_at: datetime
    expires_at: datetime

    _validate_issued_at = field_validator("issued_at")(_utc)
    _validate_expires_at = field_validator("expires_at")(_utc)

    @model_validator(mode="after")
    def validate_expiry(self) -> "CandidateApproval":
        if self.expires_at <= self.issued_at:
            raise ValueError("approval expiry must follow issue time")
        return self


class ReleaseDependencies(StrictModel):
    scenario_b_release_controls_passed: bool
    scenario_e_integrity_passed: bool
    production_live_canary_authorized: bool


class CandidateGateDecision(StrictModel):
    state: Literal["blocked", "limited_release_handoff"]
    blockers: list[str] = Field(default_factory=list)
    candidate_id: str
    candidate_digest: str = Field(pattern=SHA256_PATTERN)
    approval_decision: Literal["manual_hold", "rejected", "approved"]
    limited_release_eligible: bool
    deployment_intent_created: Literal[False] = False
    production_mutated: Literal[False] = False
    evaluated_at: datetime

    _validate_evaluated_at = field_validator("evaluated_at")(_utc)

    @model_validator(mode="after")
    def validate_gate(self) -> "CandidateGateDecision":
        if self.state == "limited_release_handoff":
            if self.blockers or not self.limited_release_eligible:
                raise ValueError("eligible handoff cannot contain blockers")
        elif not self.blockers or self.limited_release_eligible:
            raise ValueError("blocked gate requires blockers and false eligibility")
        return self


class RegistrationResult(StrictModel):
    event_created: bool
    candidate_created: bool
    event_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    attempt_count: int = Field(ge=1)


class RegistryConflict(RuntimeError):
    pass


def _coverage(records: list[PredictionRecord], field_name: str) -> float:
    if not records:
        return 0.0
    return sum(bool(getattr(record, field_name)) for record in records) / len(records)


def _macro_f1(records: list[PredictionRecord]) -> float:
    labels = sorted({record.actual_label for record in records} | {record.predicted_label for record in records})
    if not labels:
        return 0.0
    scores: list[float] = []
    for label in labels:
        true_positive = sum(
            record.actual_label == label and record.predicted_label == label
            for record in records
        )
        false_positive = sum(
            record.actual_label != label and record.predicted_label == label
            for record in records
        )
        false_negative = sum(
            record.actual_label == label and record.predicted_label != label
            for record in records
        )
        denominator = (2 * true_positive) + false_positive + false_negative
        scores.append((2 * true_positive / denominator) if denominator else 0.0)
    return sum(scores) / len(scores)


def summarize_window(records: list[PredictionRecord]) -> WindowQuality:
    sample_ids = [record.sample_id for record in records]
    content_digests = [record.content_sha256 for record in records]
    required_fields = (
        "sample_id",
        "content_sha256",
        "image_uri",
        "class_name",
        "actual_label",
        "predicted_label",
    )
    missing = sum(
        not getattr(record, field_name)
        for record in records
        for field_name in required_fields
    )
    accuracy = (
        sum(record.actual_label == record.predicted_label for record in records) / len(records)
        if records
        else 0.0
    )
    return WindowQuality(
        record_count=len(records),
        missing_required_fields=missing,
        duplicate_sample_ids=len(sample_ids) - len(set(sample_ids)),
        duplicate_content_digests=len(content_digests) - len(set(content_digests)),
        label_coverage=_coverage(records, "actual_label"),
        content_digest_coverage=_coverage(records, "content_sha256"),
        accuracy=accuracy,
        f1_macro=_macro_f1(records),
    )


def _schema_blockers(policy: ScenarioCPolicy, quality: WindowQuality, prefix: str) -> list[str]:
    blockers: list[str] = []
    if quality.record_count == 0:
        blockers.append(f"{prefix}_window_empty")
    if quality.missing_required_fields > policy.max_missing_required_fields:
        blockers.append(f"{prefix}_missing_required_fields")
    if quality.duplicate_sample_ids > policy.max_duplicate_sample_ids:
        blockers.append(f"{prefix}_duplicate_sample_ids")
    if quality.duplicate_content_digests > policy.max_duplicate_content_digests:
        blockers.append(f"{prefix}_duplicate_content_digests")
    if quality.label_coverage < policy.min_label_coverage:
        blockers.append(f"{prefix}_label_coverage_below_minimum")
    if quality.content_digest_coverage < policy.min_content_digest_coverage:
        blockers.append(f"{prefix}_content_digest_coverage_below_minimum")
    return blockers


def evaluate_quality_windows(
    *,
    policy: ScenarioCPolicy,
    baseline: list[PredictionRecord],
    current: list[PredictionRecord],
    identity_valid: bool = True,
) -> QualityDecision:
    baseline_quality = summarize_window(baseline)
    current_quality = summarize_window(current)
    blockers = [] if identity_valid else ["identity_mismatch"]
    blockers.extend(_schema_blockers(policy, baseline_quality, "baseline"))
    blockers.extend(_schema_blockers(policy, current_quality, "current"))
    if blockers:
        return QualityDecision(
            state="blocked_invalid_evidence",
            blocker_codes=sorted(set(blockers)),
            baseline_quality=baseline_quality,
            current_quality=current_quality,
            metrics={},
        )

    evaluation = evaluate_measured_drift(
        reference_predictions=[record.model_dump(mode="json") for record in baseline],
        current_predictions=[record.model_dump(mode="json") for record in current],
        thresholds=policy.drift_thresholds(),
        low_confidence_threshold=policy.low_confidence_threshold,
    )
    metrics = {key: float(value) for key, value in evaluation["metrics"].items()}
    metrics["accuracy_drop"] = max(0.0, baseline_quality.accuracy - current_quality.accuracy)
    metrics["f1_drop"] = max(0.0, baseline_quality.f1_macro - current_quality.f1_macro)

    thresholds = {
        **policy.drift_thresholds(),
        "accuracy_drop": policy.max_accuracy_drop,
        "f1_drop": policy.max_f1_drop,
    }
    families = {
        "input_category_js": "data_distribution",
        "predicted_class_js": "model_quality",
        "confidence_psi": "model_quality",
        "mean_confidence_drop": "model_quality",
        "low_confidence_rate_increase": "model_quality",
        "accuracy_drop": "model_quality",
        "f1_drop": "model_quality",
    }
    signals = [
        MeasuredSignal(
            signal_id=signal_id,
            family=families[signal_id],  # type: ignore[arg-type]
            observed=metrics[signal_id],
            threshold=thresholds[signal_id],
            comparison="max",
            breached=metrics[signal_id] > thresholds[signal_id],
        )
        for signal_id in thresholds
    ]
    triggered = [signal.signal_id for signal in signals if signal.breached]
    return QualityDecision(
        state="review_required" if triggered else "within_policy",
        triggered_rules=triggered,
        signals=signals,
        baseline_quality=baseline_quality,
        current_quality=current_quality,
        metrics=metrics,
    )


def window_digest(records: list[PredictionRecord]) -> str:
    material = [record.model_dump(mode="json") for record in records]
    material.sort(key=lambda item: item["sample_id"])
    return payload_sha256(material)


def build_review_event(
    *,
    policy: ScenarioCPolicy,
    identity: ScenarioCIdentity,
    baseline: list[PredictionRecord],
    current: list[PredictionRecord],
    decision: QualityDecision,
    affected_slices: list[str],
    created_at: datetime,
) -> ReviewEvent:
    if decision.state != "review_required":
        raise ValueError("review event requires a review_required decision")
    identity_digest = payload_sha256(identity.model_dump(mode="json"))
    baseline_digest = window_digest(baseline)
    current_digest = window_digest(current)
    material = {
        "policy": policy.model_dump(mode="json"),
        "identity_digest": identity_digest,
        "baseline_window_digest": baseline_digest,
        "current_window_digest": current_digest,
        "metrics": decision.metrics,
        "triggered_rules": decision.triggered_rules,
        "affected_slices": sorted(affected_slices),
    }
    fingerprint = payload_sha256(material)
    return ReviewEvent(
        event_id=f"quality-review-{fingerprint[:20]}",
        fingerprint=fingerprint,
        policy_id=policy.policy_id,
        identity_digest=identity_digest,
        baseline_window_digest=baseline_digest,
        current_window_digest=current_digest,
        decision="review_required",
        triggered_rules=decision.triggered_rules,
        metrics=decision.metrics,
        affected_slices=sorted(affected_slices),
        created_at=created_at,
    )


def build_retraining_candidate(
    *,
    event: ReviewEvent,
    identity: ScenarioCIdentity,
    profile: RetrainingProfile,
    derived_manifest_digest: str,
    created_at: datetime,
) -> RetrainingCandidate:
    profile_digest = payload_sha256(profile.model_dump(mode="json"))
    material = {
        "event_fingerprint": event.fingerprint,
        "dataset_version": identity.dataset_version,
        "derived_manifest_digest": derived_manifest_digest,
        "baseline_model_digest": identity.baseline_model_sha256,
        "training_profile_digest": profile_digest,
        "source_revision": identity.source_revision,
        "ct_snapshot_id": identity.ct_snapshot_id,
        "ct_manifest_sha256": identity.ct_manifest_sha256,
    }
    candidate_digest = payload_sha256(material)
    return RetrainingCandidate(
        candidate_id=f"retrain-{candidate_digest[:20]}",
        candidate_digest=candidate_digest,
        event_id=event.event_id,
        event_fingerprint=event.fingerprint,
        dataset_version=identity.dataset_version,
        derived_manifest_digest=derived_manifest_digest,
        baseline_model_digest=identity.baseline_model_sha256,
        training_profile=profile,
        training_profile_digest=profile_digest,
        source_revision=identity.source_revision,
        requested_ct_snapshot_id=identity.ct_snapshot_id,
        requested_ct_digest=identity.ct_manifest_sha256,
        created_at=created_at,
    )


class ScenarioCRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "registry.json"
        self.lock_path = root / ".registry.lock"

    def register(
        self,
        event: ReviewEvent,
        candidate: RetrainingCandidate,
    ) -> RegistrationResult:
        if candidate.event_id != event.event_id or candidate.event_fingerprint != event.fingerprint:
            raise RegistryConflict("candidate_event_identity_mismatch")
        with exclusive_lock(self.lock_path):
            payload = self._load()
            events = payload["events"]
            candidates = payload["candidates"]
            event_payload = event.model_dump(mode="json")
            candidate_payload = candidate.model_dump(mode="json")
            existing_event = events.get(event.event_id)
            existing_candidate = candidates.get(candidate.candidate_id)
            if existing_event is not None and existing_event != event_payload:
                raise RegistryConflict("event_identity_payload_conflict")
            if existing_candidate is not None and existing_candidate != candidate_payload:
                raise RegistryConflict("candidate_identity_payload_conflict")
            event_created = existing_event is None
            candidate_created = existing_candidate is None
            events[event.event_id] = event_payload
            candidates[candidate.candidate_id] = candidate_payload
            payload["attempt_count"] = int(payload.get("attempt_count", 0)) + 1
            atomic_write_json(self.path, payload)
            return RegistrationResult(
                event_created=event_created,
                candidate_created=candidate_created,
                event_count=len(events),
                candidate_count=len(candidates),
                attempt_count=payload["attempt_count"],
            )

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {
                "schema_version": "evm.scenario_c_registry.v1",
                "events": {},
                "candidates": {},
                "attempt_count": 0,
            }
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "evm.scenario_c_registry.v1":
            raise RegistryConflict("registry_schema_mismatch")
        if not isinstance(payload.get("events"), dict) or not isinstance(
            payload.get("candidates"), dict
        ):
            raise RegistryConflict("registry_payload_malformed")
        return payload


def evaluate_candidate_gate(
    *,
    candidate: RetrainingCandidate,
    evaluation: CandidateEvaluation,
    approval: CandidateApproval,
    dependencies: ReleaseDependencies,
    requester: str,
    evaluated_at: datetime,
) -> CandidateGateDecision:
    blockers: list[str] = []
    if evaluation.candidate_id != candidate.candidate_id:
        blockers.append("evaluation_candidate_mismatch")
    if evaluation.candidate_digest != candidate.candidate_digest:
        blockers.append("evaluation_candidate_digest_mismatch")
    if approval.candidate_id != candidate.candidate_id:
        blockers.append("approval_candidate_mismatch")
    if approval.candidate_digest != candidate.candidate_digest:
        blockers.append("approval_candidate_digest_mismatch")
    if evaluated_at > approval.expires_at:
        blockers.append("approval_expired")

    if approval.decision == "manual_hold":
        blockers.append("manual_hold")
    elif approval.decision == "rejected":
        blockers.append("candidate_rejected")
    else:
        if approval.actor == requester:
            blockers.append("approval_separation_of_duties_failed")
        if evaluation.status != "pass":
            blockers.append("candidate_evaluation_not_passed")
        if evaluation.ct_status != "pass":
            blockers.append("isolated_ct_not_passed")
        if not dependencies.scenario_b_release_controls_passed:
            blockers.append("scenario_b_release_controls_not_passed")
        if not dependencies.scenario_e_integrity_passed:
            blockers.append("scenario_e_integrity_not_passed")
        if not dependencies.production_live_canary_authorized:
            blockers.append("production_live_canary_not_authorized")

    blockers.extend(evaluation.blockers)
    blockers = sorted(set(blockers))
    eligible = not blockers
    return CandidateGateDecision(
        state="limited_release_handoff" if eligible else "blocked",
        blockers=blockers,
        candidate_id=candidate.candidate_id,
        candidate_digest=candidate.candidate_digest,
        approval_decision=approval.decision,
        limited_release_eligible=eligible,
        deployment_intent_created=False,
        production_mutated=False,
        evaluated_at=evaluated_at,
    )
