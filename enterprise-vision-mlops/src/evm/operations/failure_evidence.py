from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "evm.operational_failure_evidence.v1"
SHA256_PATTERN = r"^[a-f0-9]{64}$"
IDENTITY_FIELDS = (
    "dataset_version",
    "split_digest",
    "model_digest",
    "artifact_digest",
    "image_digest",
    "ct_digest",
    "rollback_digest",
)


def _require_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckEvidence(StrictModel):
    check_id: str = Field(min_length=1)
    passed: bool
    observed: Any
    required: bool = True
    reason_code: str | None = None


class ClosureEvidence(StrictModel):
    decision: Literal["passed", "failed", "blocked", "not_run"]
    required_check_ids: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    completed_at: datetime | None = None

    _validate_completed_at = field_validator("completed_at")(_require_utc)

    @model_validator(mode="after")
    def validate_decision(self) -> "ClosureEvidence":
        if self.decision == "passed" and self.blockers:
            raise ValueError("passed closure cannot contain blockers")
        if self.decision != "passed" and not self.blockers:
            raise ValueError("non-passed closure must explain at least one blocker")
        if self.decision == "passed" and self.completed_at is None:
            raise ValueError("passed closure requires completed_at")
        return self


class TimingEvidence(StrictModel):
    audit_started_at: datetime
    audit_finished_at: datetime
    monotonic_started_ns: int = Field(ge=0)
    monotonic_finished_ns: int = Field(ge=0)
    injection_monotonic_ns: int | None = Field(default=None, ge=0)
    detection_monotonic_ns: int | None = Field(default=None, ge=0)
    recovery_monotonic_ns: int | None = Field(default=None, ge=0)
    detection_seconds: float | None = Field(default=None, ge=0)
    recovery_seconds: float | None = Field(default=None, ge=0)
    sample_cadence_seconds: float = Field(gt=0)
    signal_precedence: list[str] = Field(min_length=1)

    _validate_started_at = field_validator("audit_started_at")(_require_utc)
    _validate_finished_at = field_validator("audit_finished_at")(_require_utc)

    @model_validator(mode="after")
    def validate_timeline(self) -> "TimingEvidence":
        if self.audit_finished_at < self.audit_started_at:
            raise ValueError("audit_finished_at precedes audit_started_at")
        if self.monotonic_finished_ns < self.monotonic_started_ns:
            raise ValueError("monotonic clock moved backwards")
        if len(set(self.signal_precedence)) != len(self.signal_precedence):
            raise ValueError("signal_precedence must be ordered and unique")

        live_values = (
            self.injection_monotonic_ns,
            self.detection_monotonic_ns,
            self.recovery_monotonic_ns,
        )
        if any(value is not None for value in live_values):
            if any(value is None for value in live_values):
                raise ValueError("live timing requires injection, detection, and recovery points")
            assert self.injection_monotonic_ns is not None
            assert self.detection_monotonic_ns is not None
            assert self.recovery_monotonic_ns is not None
            if not (
                self.monotonic_started_ns
                <= self.injection_monotonic_ns
                <= self.detection_monotonic_ns
                <= self.recovery_monotonic_ns
                <= self.monotonic_finished_ns
            ):
                raise ValueError("live monotonic points are out of order")
            expected_detection = (
                self.detection_monotonic_ns - self.injection_monotonic_ns
            ) / 1_000_000_000
            expected_recovery = (
                self.recovery_monotonic_ns - self.injection_monotonic_ns
            ) / 1_000_000_000
            if self.detection_seconds is None or self.recovery_seconds is None:
                raise ValueError("live timing requires measured durations")
            if abs(self.detection_seconds - expected_detection) > 0.01:
                raise ValueError("detection_seconds must come from the monotonic clock")
            if abs(self.recovery_seconds - expected_recovery) > 0.01:
                raise ValueError("recovery_seconds must come from the monotonic clock")
        elif self.detection_seconds is not None or self.recovery_seconds is not None:
            raise ValueError("durations cannot exist without live monotonic points")
        return self


class SourceEvidence(StrictModel):
    commit: str = Field(min_length=7)
    branch: str = Field(min_length=1)
    dirty: bool
    api_revision: str = Field(min_length=1)
    worker_revision: str = Field(min_length=1)
    observer_revision: str = Field(min_length=1)


class EnvironmentEvidence(StrictModel):
    cluster_context: str
    node: str
    namespaces: list[str] = Field(min_length=1)
    hardware: dict[str, Any]
    runtime_versions: dict[str, str]


class IdentityEvidence(StrictModel):
    dataset_version: str | None = None
    split_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    model_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    artifact_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    image_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    ct_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    rollback_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)


class ApprovalEvidence(StrictModel):
    required: bool
    decision: Literal["not_required", "pending", "approved", "rejected", "consumed"]
    approval_id: str | None = None
    run_id: str | None = None
    target_uid: str | None = None
    action_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    source_revision: str | None = None
    expires_at: datetime | None = None
    consumed_at: datetime | None = None
    single_use: bool = True

    _validate_expires_at = field_validator("expires_at")(_require_utc)
    _validate_consumed_at = field_validator("consumed_at")(_require_utc)

    @model_validator(mode="after")
    def validate_binding(self) -> "ApprovalEvidence":
        if not self.required and self.decision != "not_required":
            raise ValueError("approval decision must be not_required when approval is optional")
        if self.decision == "consumed":
            required = (
                self.approval_id,
                self.run_id,
                self.target_uid,
                self.action_digest,
                self.source_revision,
                self.expires_at,
                self.consumed_at,
            )
            if not all(required) or not self.single_use:
                raise ValueError("consumed approval requires an exact single-use binding")
        return self


class InjectionEvidence(StrictModel):
    method: str
    action: str
    target: dict[str, str]
    expected_effect: str
    blast_radius: str
    performed: bool


class SignalEvidence(StrictModel):
    signal_id: str
    source: str
    observed_at: datetime
    healthy: bool
    detail: dict[str, Any] = Field(default_factory=dict)

    _validate_observed_at = field_validator("observed_at")(_require_utc)


class DecisionEvidence(StrictModel):
    expected: str
    observed: str
    blocker_codes: list[str] = Field(default_factory=list)


class RecoveryEvidence(StrictModel):
    action: str
    target_identity: dict[str, str]
    result: str


class ArtifactEvidence(StrictModel):
    uri: str
    sha256: str = Field(pattern=SHA256_PATTERN)
    media_type: str
    evidence_role: Literal["run_evidence", "baseline_reference"]


class PortfolioEvidence(StrictModel):
    competencies: list[str] = Field(min_length=1)
    interview_questions: list[str] = Field(min_length=1)
    trade_offs: list[str] = Field(min_length=1)
    factual_claims: list[str] = Field(min_length=1)
    prohibited_claims: list[str] = Field(min_length=1)


class OperationalFailureReport(StrictModel):
    schema_version: Literal[SCHEMA_VERSION]
    scenario_id: Literal["A", "B", "C", "D", "E", "CROSS"]
    run_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]+$")
    claim_class: Literal["local_operational_validation"]
    status: Literal["passed", "failed", "blocked", "rolled_back"]
    started_at: datetime
    finished_at: datetime
    actor: str = Field(min_length=1)
    approval: ApprovalEvidence
    source: SourceEvidence
    environment: EnvironmentEvidence
    identities: IdentityEvidence
    identity_requirements: list[Literal[
        "dataset_version",
        "split_digest",
        "model_digest",
        "artifact_digest",
        "image_digest",
        "ct_digest",
        "rollback_digest",
    ]] = Field(default_factory=list)
    preconditions: list[CheckEvidence]
    injection: InjectionEvidence
    signals: list[SignalEvidence]
    decision: DecisionEvidence
    mitigation: dict[str, Any]
    recovery: RecoveryEvidence
    postconditions: list[CheckEvidence]
    artifacts: list[ArtifactEvidence]
    limitations: list[str] = Field(min_length=1)
    portfolio: PortfolioEvidence
    timing: TimingEvidence
    readiness_closure: ClosureEvidence
    live_proof_closure: ClosureEvidence

    _validate_started_at = field_validator("started_at")(_require_utc)
    _validate_finished_at = field_validator("finished_at")(_require_utc)

    @model_validator(mode="after")
    def validate_report_contract(self) -> "OperationalFailureReport":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at precedes started_at")
        if self.started_at != self.timing.audit_started_at:
            raise ValueError("top-level started_at must match timing audit_started_at")
        if self.finished_at != self.timing.audit_finished_at:
            raise ValueError("top-level finished_at must match timing audit_finished_at")

        checks = {check.check_id: check for check in self.preconditions + self.postconditions}
        if len(checks) != len(self.preconditions) + len(self.postconditions):
            raise ValueError("check_id values must be unique")
        for closure_name, closure in (
            ("readiness", self.readiness_closure),
            ("live_proof", self.live_proof_closure),
        ):
            missing = [check_id for check_id in closure.required_check_ids if check_id not in checks]
            if missing:
                raise ValueError(f"{closure_name} closure references unknown checks: {missing}")
            failed = [
                check_id
                for check_id in closure.required_check_ids
                if not checks[check_id].passed
            ]
            if closure.decision == "passed" and failed:
                raise ValueError(f"{closure_name} closure contains failed checks: {failed}")

        if self.live_proof_closure.decision == "passed":
            if self.approval.required and self.approval.decision != "consumed":
                raise ValueError("live proof requires a consumed approval binding")
            if self.approval.run_id and self.approval.run_id != self.run_id:
                raise ValueError("approval run_id does not match evidence run_id")
            if self.approval.target_uid and (
                self.approval.target_uid != self.injection.target.get("uid")
            ):
                raise ValueError("approval target_uid does not match injection target")
            missing_identities = [
                field_name
                for field_name in self.identity_requirements
                if not getattr(self.identities, field_name)
            ]
            if missing_identities:
                raise ValueError(f"live proof identity subset is incomplete: {missing_identities}")
            if not any(item.evidence_role == "run_evidence" for item in self.artifacts):
                raise ValueError("P0 baseline references cannot close a live proof")
            if not self.injection.performed:
                raise ValueError("live proof cannot pass without a performed injection")
            if self.status != "passed":
                raise ValueError("passed live proof requires overall passed status")
        elif self.status == "passed":
            raise ValueError("overall status cannot pass before live proof closure")
        return self


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_artifact_index(report: OperationalFailureReport) -> list[str]:
    errors: list[str] = []
    for artifact in report.artifacts:
        if artifact.evidence_role != "run_evidence":
            continue
        path = Path(artifact.uri)
        if not path.is_absolute():
            errors.append(f"artifact_not_absolute:{artifact.uri}")
            continue
        if not path.is_file():
            errors.append(f"artifact_missing:{artifact.uri}")
            continue
        observed = sha256_file(path)
        if observed != artifact.sha256:
            errors.append(f"artifact_digest_mismatch:{artifact.uri}")
    return errors


def validate_closure(report: OperationalFailureReport, required: str) -> list[str]:
    closure = (
        report.readiness_closure if required == "readiness" else report.live_proof_closure
    )
    errors = validate_artifact_index(report)
    if closure.decision != "passed":
        errors.append(f"{required}_closure_{closure.decision}")
        errors.extend(closure.blockers)
    if required == "live_proof" and report.status != "passed":
        errors.append(f"overall_status_{report.status}")
    return sorted(set(errors))
