from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import Field

from evm.control_panel.lifecycle_guards import canonical_digest, file_digest
from evm.control_panel.readiness_evaluator import runtime_path
from evm.control_panel.schemas import ContractModel
from evm.operations.scenario_b_canary import ControlledReplayResult


RELEASE_GUARD_SCHEMA = "evm.lifecycle_release_guard.v1"
ReleaseGuardState = Literal[
    "rejected_release",
    "rolled_back",
    "approved_for_release",
]


class LifecycleReleaseIdentity(Protocol):
    run_id: str
    profile_digest: str
    effective_config_digest: str
    lifecycle_series_id: str | None
    attempt_id: str | None
    correlation_id: str | None
    source_commit: str | None
    artifact_root: str
    release_submission_uri: str | None
    release_guard_required: bool
    release_guard_uri: str | None


class LifecycleReleaseGuardRegistration(ContractModel):
    actor: str = Field(min_length=2)
    reason: str = Field(min_length=8)
    expected_version: int = Field(ge=1)
    evidence_index_uri: str = Field(min_length=1)
    evidence_index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class LifecycleReleaseGuard(ContractModel):
    schema_version: Literal["evm.lifecycle_release_guard.v1"] = RELEASE_GUARD_SCHEMA
    guard_id: str
    run_id: str
    profile_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    effective_config_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    lifecycle_series_id: str
    attempt_id: str
    correlation_id: str
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    replay_run_id: str
    evidence_index_uri: str
    evidence_index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_id: str
    model_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    ct_evaluation_id: str
    release_submission_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    stable_model_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision_state: Literal["blocked_admission", "canary_passed", "rolled_back"]
    state: ReleaseGuardState
    blocker_codes: list[str]
    production_mutated: Literal[False]
    exact_stable_identity_restored: Literal[True]
    decision_seconds: float = Field(ge=0)
    recovery_seconds: float = Field(ge=0)
    registered_by: str
    registered_at: datetime


class LifecycleReleaseGuardBlocked(RuntimeError):
    def __init__(self, code: str, blockers: list[str] | None = None):
        self.code = code
        self.blockers = sorted(set(blockers or [code]))
        super().__init__(code)


def release_guard_path(run: LifecycleReleaseIdentity) -> Path:
    lifecycle_root = os.getenv("EVM_LIFECYCLE_RUN_ROOT")
    if lifecycle_root:
        return Path(lifecycle_root) / run.run_id / "release" / "scenario-b-guard.json"
    return runtime_path(run.artifact_root) / "release" / "scenario-b-guard.json"


def _read_json(path: Path, blocker: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleReleaseGuardBlocked(blocker) from exc
    if not isinstance(payload, dict):
        raise LifecycleReleaseGuardBlocked(blocker)
    return payload


def _required_file(uri: str, expected_sha256: str, label: str) -> Path:
    path = runtime_path(uri)
    if not path.is_file():
        raise LifecycleReleaseGuardBlocked(
            "release_guard_evidence_missing",
            [f"release_guard_{label}_missing:{uri}"],
        )
    observed = file_digest(path)
    if observed != expected_sha256:
        raise LifecycleReleaseGuardBlocked(
            "release_guard_evidence_digest_mismatch",
            [
                f"release_guard_{label}_digest_mismatch:"
                f"expected={expected_sha256}:actual={observed}"
            ],
        )
    return path


def _artifact_paths(index: dict[str, Any]) -> dict[str, Path]:
    artifacts = index.get("artifacts")
    if not isinstance(artifacts, list):
        raise LifecycleReleaseGuardBlocked("release_guard_artifact_index_invalid")
    paths: dict[str, Path] = {}
    blockers: list[str] = []
    for item in artifacts:
        if not isinstance(item, dict):
            blockers.append("release_guard_artifact_entry_invalid")
            continue
        role = str(item.get("role") or "")
        uri = str(item.get("uri") or "")
        digest = str(item.get("sha256") or "")
        if not role or role in paths:
            blockers.append(f"release_guard_artifact_role_cardinality:{role or 'missing'}")
            continue
        try:
            paths[role] = _required_file(uri, digest, role)
        except LifecycleReleaseGuardBlocked as exc:
            blockers.extend(exc.blockers)
    required = {
        "report_core",
        "runtime",
        "candidate_summary_reference",
        "lifecycle_binding",
    }
    blockers.extend(
        f"release_guard_artifact_role_missing:{role}"
        for role in sorted(required - set(paths))
    )
    if blockers:
        raise LifecycleReleaseGuardBlocked("release_guard_artifact_closure_failed", blockers)
    return paths


def _run_identity_blockers(
    run: LifecycleReleaseIdentity,
    guard: LifecycleReleaseGuard,
) -> list[str]:
    expected = {
        "run_id": run.run_id,
        "profile_digest": run.profile_digest,
        "effective_config_digest": run.effective_config_digest,
        "lifecycle_series_id": run.lifecycle_series_id,
        "attempt_id": run.attempt_id,
        "correlation_id": run.correlation_id,
        "source_commit": run.source_commit,
    }
    return sorted(
        f"release_guard_{key}_mismatch"
        for key, value in expected.items()
        if getattr(guard, key) != value
    )


def _write_guard(path: Path, guard: LifecycleReleaseGuard) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(guard.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_release_guard(
    run: LifecycleReleaseIdentity,
) -> LifecycleReleaseGuard | None:
    path = release_guard_path(run)
    if not path.is_file():
        return None
    guard = LifecycleReleaseGuard.model_validate(
        _read_json(path, "release_guard_record_invalid")
    )
    blockers = _run_identity_blockers(run, guard)
    if blockers:
        raise LifecycleReleaseGuardBlocked("release_guard_identity_mismatch", blockers)
    return guard


def register_release_guard(
    run: LifecycleReleaseIdentity,
    request: LifecycleReleaseGuardRegistration,
) -> LifecycleReleaseGuard:
    if not all(
        (
            run.lifecycle_series_id,
            run.attempt_id,
            run.correlation_id,
            run.source_commit,
            run.release_submission_uri,
        )
    ):
        raise LifecycleReleaseGuardBlocked("release_guard_run_identity_incomplete")
    existing = load_release_guard(run)
    if existing is not None:
        if existing.evidence_index_sha256 == request.evidence_index_sha256:
            return existing
        raise LifecycleReleaseGuardBlocked(
            "release_guard_registration_conflict",
            ["release_guard_exact_run_already_bound"],
        )

    submission_path = runtime_path(str(run.release_submission_uri))
    submission = _read_json(submission_path, "release_guard_submission_invalid")
    index_path = _required_file(
        request.evidence_index_uri,
        request.evidence_index_sha256,
        "evidence_index",
    )
    index = _read_json(index_path, "release_guard_artifact_index_invalid")
    paths = _artifact_paths(index)
    try:
        result = ControlledReplayResult.model_validate(
            _read_json(paths["report_core"], "release_guard_report_invalid")
        )
    except ValueError as exc:
        raise LifecycleReleaseGuardBlocked("release_guard_report_invalid") from exc
    runtime = _read_json(paths["runtime"], "release_guard_runtime_invalid")
    candidate = _read_json(
        paths["candidate_summary_reference"],
        "release_guard_candidate_invalid",
    )
    binding = _read_json(paths["lifecycle_binding"], "release_guard_binding_invalid")

    blockers: list[str] = []
    expected_binding = {
        "lifecycle_run_id": run.run_id,
        "lifecycle_series_id": run.lifecycle_series_id,
        "attempt_id": run.attempt_id,
        "correlation_id": run.correlation_id,
        "profile_digest": run.profile_digest,
        "effective_config_digest": run.effective_config_digest,
        "source_commit": run.source_commit,
        "candidate_id": submission.get("candidate_id"),
        "model_digest": submission.get("model_digest"),
        "ct_evaluation_id": submission.get("ct_evaluation_id"),
        "release_submission_digest": submission.get("submission_digest"),
    }
    blockers.extend(
        f"release_guard_binding_{key}_mismatch"
        for key, value in expected_binding.items()
        if binding.get(key) != value
    )
    if binding.get("schema_version") != "evm.lifecycle_release_binding.v1":
        blockers.append("release_guard_binding_schema_mismatch")
    if index.get("schema_version") != "evm.scenario_b_evidence_index.v1":
        blockers.append("release_guard_index_schema_mismatch")
    if index.get("run_id") != result.run_id:
        blockers.append("release_guard_replay_run_mismatch")
    if result.challenger.candidate_id != submission.get("candidate_id"):
        blockers.append("release_guard_candidate_identity_mismatch")
    if result.challenger.model_digest != submission.get("model_digest"):
        blockers.append("release_guard_model_digest_mismatch")
    if candidate.get("candidate_id") != submission.get("candidate_id"):
        blockers.append("release_guard_candidate_summary_identity_mismatch")
    if candidate.get("model_sha256") != submission.get("model_digest"):
        blockers.append("release_guard_candidate_summary_digest_mismatch")
    source = runtime.get("source") if isinstance(runtime.get("source"), dict) else {}
    if source.get("commit") != run.source_commit or source.get("dirty") is not False:
        blockers.append("release_guard_source_revision_mismatch")
    if runtime.get("production_mutated") is not False or result.production_mutated is not False:
        blockers.append("release_guard_production_mutation_detected")
    if runtime.get("stable_identity_unchanged") is not True:
        blockers.append("release_guard_stable_identity_changed")
    cuda = runtime.get("cuda") if isinstance(runtime.get("cuda"), dict) else {}
    if cuda.get("device") != "cuda":
        blockers.append("release_guard_cuda_proof_missing")
    if not result.rollback.exact_identity_restored:
        blockers.append("release_guard_stable_identity_not_restored")
    if result.rollback.restored_model_digest != result.stable.model_digest:
        blockers.append("release_guard_rollback_identity_mismatch")
    if result.policy.total_replay_requests != 1000:
        blockers.append("release_guard_replay_window_not_1000")
    if result.policy.challenger_requests != 100:
        blockers.append("release_guard_challenger_window_not_100")
    if result.policy.min_shadow_requests < 500:
        blockers.append("release_guard_shadow_window_below_500")
    if result.decision.state != "canary_passed" and (
        result.decision.stop_seconds is None
        or result.decision.stop_seconds > result.policy.stop_budget_seconds
    ):
        blockers.append("release_guard_detection_budget_exceeded")
    if result.rollback.duration_seconds > result.policy.rollback_budget_seconds:
        blockers.append("release_guard_recovery_budget_exceeded")
    if result.decision.state == "rolled_back":
        if result.metric_window is None:
            blockers.append("release_guard_metric_window_missing")
        elif result.metric_window.identity_match_fraction != 1:
            blockers.append("release_guard_response_identity_mismatch")
    if blockers:
        raise LifecycleReleaseGuardBlocked("release_guard_registration_blocked", blockers)

    state: ReleaseGuardState = {
        "blocked_admission": "rejected_release",
        "rolled_back": "rolled_back",
        "canary_passed": "approved_for_release",
    }[result.decision.state]
    registered_at = datetime.now(UTC)
    guard_material = {
        **expected_binding,
        "replay_run_id": result.run_id,
        "evidence_index_sha256": request.evidence_index_sha256,
        "decision_state": result.decision.state,
        "state": state,
        "blocker_codes": result.decision.blocker_codes,
    }
    guard = LifecycleReleaseGuard(
        guard_id=f"release-guard-{canonical_digest(guard_material)[:20]}",
        run_id=run.run_id,
        profile_digest=run.profile_digest,
        effective_config_digest=run.effective_config_digest,
        lifecycle_series_id=str(run.lifecycle_series_id),
        attempt_id=str(run.attempt_id),
        correlation_id=str(run.correlation_id),
        source_commit=str(run.source_commit),
        replay_run_id=result.run_id,
        evidence_index_uri=str(index_path.resolve()),
        evidence_index_sha256=request.evidence_index_sha256,
        candidate_id=str(submission.get("candidate_id") or ""),
        model_digest=str(submission.get("model_digest") or ""),
        ct_evaluation_id=str(submission.get("ct_evaluation_id") or ""),
        release_submission_digest=str(submission.get("submission_digest") or ""),
        stable_model_digest=result.stable.model_digest,
        decision_state=result.decision.state,
        state=state,
        blocker_codes=result.decision.blocker_codes,
        production_mutated=False,
        exact_stable_identity_restored=True,
        decision_seconds=float(result.decision.stop_seconds or 0),
        recovery_seconds=float(result.rollback.duration_seconds),
        registered_by=request.actor,
        registered_at=registered_at,
    )
    _write_guard(release_guard_path(run), guard)
    return guard


def authorize_release_guard(
    run: LifecycleReleaseIdentity,
) -> LifecycleReleaseGuard | None:
    guard = load_release_guard(run)
    if guard is None:
        if run.release_guard_required:
            raise LifecycleReleaseGuardBlocked("release_guard_required_missing")
        return None
    if guard.state != "approved_for_release":
        raise LifecycleReleaseGuardBlocked(
            "release_guard_release_blocked",
            [f"release_guard_state:{guard.state}", *guard.blocker_codes],
        )
    return guard
