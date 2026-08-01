from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evm.operations.failure_evidence import (
    CheckEvidence,
    IdentityEvidence,
    OperationalFailureReport,
    SourceEvidence,
    sha256_file,
    validate_closure,
)
from evm.operations.failure_scenarios import (
    ApprovalBinding,
    ApprovalStore,
    ScenarioStateStore,
    TargetRef,
    action_digest,
    atomic_write_json,
)
from evm.operations.runtime_adapters import HttpAdapter, KubernetesAdapter, ScenarioACollector
from evm.operations.scenario_a_runner import (
    ScenarioAConfig,
    TextRunner,
    _run_text,
    collect_runtime_source,
    discover_scenario_a_selectors,
)
from evm.operations.target_health import evaluate_scenario_a_health


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


def payload_sha256(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required:{path}")
    return payload


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScenarioAIdentityBundle(StrictModel):
    identities: IdentityEvidence
    checks: list[CheckEvidence]
    blockers: list[str]
    ct_evaluation_id: str | None


class ScenarioAPreflight(StrictModel):
    schema_version: Literal["evm.scenario_a_preflight.v1"]
    run_id: str
    checked_at: datetime
    decision: Literal["passed", "blocked"]
    source: SourceEvidence
    target: TargetRef
    deployment_uid: str
    action: Literal["delete_pod_with_uid_precondition"]
    action_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    identities: IdentityEvidence
    checks: list[CheckEvidence]
    blockers: list[str]
    rollback_path: str
    rollback_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    baseline_report_path: str
    reconciliation_plan_path: str
    maintenance_impact: str

    _validate_checked_at = field_validator("checked_at")(_utc)

    @model_validator(mode="after")
    def validate_decision(self) -> "ScenarioAPreflight":
        if self.decision == "passed" and self.blockers:
            raise ValueError("passed preflight cannot contain blockers")
        if self.decision == "blocked" and not self.blockers:
            raise ValueError("blocked preflight requires blockers")
        return self


class ApprovalResult(StrictModel):
    binding: ApprovalBinding
    path: Path
    state_revision: int


def deployment_rollback_payload(deployment: dict[str, Any]) -> dict[str, Any]:
    metadata = deployment.get("metadata") or {}
    spec = deployment.get("spec") or {}
    return {
        "schema_version": "evm.scenario_a_rollback_target.v1",
        "api_version": deployment.get("apiVersion"),
        "kind": deployment.get("kind"),
        "metadata": {
            "namespace": metadata.get("namespace"),
            "name": metadata.get("name"),
            "uid": metadata.get("uid"),
            "generation": metadata.get("generation"),
        },
        "spec": {
            "replicas": spec.get("replicas"),
            "selector": spec.get("selector"),
            "strategy": spec.get("strategy"),
            "template": spec.get("template"),
        },
    }


def _file_check(check_id: str, path: Path, expected: str) -> CheckEvidence:
    exists = path.is_file()
    observed = sha256_file(path) if exists else None
    passed = exists and observed == expected
    return CheckEvidence(
        check_id=check_id,
        passed=passed,
        observed={"path": str(path), "expected": expected, "observed": observed},
        reason_code=None if passed else f"{check_id}_failed",
    )


def evaluate_identity_bundle(
    config: ScenarioAConfig,
    *,
    observed_image_digest: str,
    rollback_digest: str,
) -> ScenarioAIdentityBundle:
    artifacts = config.artifacts
    checks = [
        _file_check("identity_model_artifact", artifacts.model_path, artifacts.model_sha256),
        _file_check(
            "identity_split_manifest",
            artifacts.split_manifest_path,
            artifacts.split_manifest_sha256,
        ),
        _file_check(
            "identity_readiness_manifest",
            artifacts.readiness_manifest_path,
            artifacts.readiness_manifest_sha256,
        ),
    ]
    candidate = read_json(artifacts.candidate_summary_path) if artifacts.candidate_summary_path.is_file() else {}
    candidate_passed = (
        candidate.get("status") == "pass"
        and candidate.get("candidate_id") == config.identity.candidate_id
        and candidate.get("dataset_version") == config.identity.dataset_version
        and candidate.get("model_sha256") == artifacts.model_sha256
        and not candidate.get("promotion_blockers")
    )
    checks.append(
        CheckEvidence(
            check_id="identity_candidate_summary",
            passed=candidate_passed,
            observed={
                "path": str(artifacts.candidate_summary_path),
                "candidate_id": candidate.get("candidate_id"),
                "dataset_version": candidate.get("dataset_version"),
                "model_sha256": candidate.get("model_sha256"),
                "status": candidate.get("status"),
                "promotion_blockers": candidate.get("promotion_blockers"),
            },
            reason_code=None if candidate_passed else "identity_candidate_summary_failed",
        )
    )
    ct = read_json(artifacts.ct_report_path) if artifacts.ct_report_path.is_file() else {}
    ct_passed = (
        ct.get("status") == "pass"
        and ct.get("decision") == "pass"
        and ct.get("candidate_id") == config.identity.candidate_id
        and ct.get("dataset_version") == config.identity.dataset_version
        and ct.get("model_sha256") == artifacts.model_sha256
        and ct.get("device") == "cuda"
        and ct.get("overlap_count") == 0
        and ct.get("mutated") is False
        and ct.get("training_mount_isolated") is True
        and not ct.get("blockers")
    )
    checks.append(
        CheckEvidence(
            check_id="identity_isolated_ct",
            passed=ct_passed,
            observed={
                "path": str(artifacts.ct_report_path),
                "evaluation_id": ct.get("evaluation_id"),
                "candidate_id": ct.get("candidate_id"),
                "model_sha256": ct.get("model_sha256"),
                "device": ct.get("device"),
                "record_count": ct.get("ct_record_count"),
                "overlap_count": ct.get("overlap_count"),
                "status": ct.get("status"),
            },
            reason_code=None if ct_passed else "identity_isolated_ct_failed",
        )
    )
    expected_image = config.identity.image_digest.removeprefix("sha256:")
    image_passed = observed_image_digest.removeprefix("sha256:") == expected_image
    checks.append(
        CheckEvidence(
            check_id="identity_serving_image",
            passed=image_passed,
            observed={"expected": expected_image, "observed": observed_image_digest},
            reason_code=None if image_passed else "identity_serving_image_failed",
        )
    )
    rollback_passed = len(rollback_digest) == 64
    checks.append(
        CheckEvidence(
            check_id="identity_rollback_target",
            passed=rollback_passed,
            observed=rollback_digest,
            reason_code=None if rollback_passed else "identity_rollback_target_failed",
        )
    )
    ct_digest = sha256_file(artifacts.ct_report_path) if artifacts.ct_report_path.is_file() else None
    identities = IdentityEvidence(
        dataset_version=config.identity.dataset_version,
        split_digest=artifacts.split_manifest_sha256,
        model_digest=artifacts.model_sha256,
        artifact_digest=artifacts.readiness_manifest_sha256,
        image_digest=expected_image,
        ct_digest=ct_digest,
        rollback_digest=rollback_digest,
    )
    blockers = [str(check.reason_code) for check in checks if not check.passed]
    return ScenarioAIdentityBundle(
        identities=identities,
        checks=checks,
        blockers=blockers,
        ct_evaluation_id=str(ct.get("evaluation_id")) if ct.get("evaluation_id") else None,
    )


def _same_daemonset_template(before: Path, after: Path) -> bool:
    if not before.is_file() or not after.is_file():
        return False
    before_payload = read_json(before)
    after_payload = read_json(after)
    return (
        (before_payload.get("metadata") or {}).get("uid")
        == (after_payload.get("metadata") or {}).get("uid")
        and (before_payload.get("metadata") or {}).get("generation")
        == (after_payload.get("metadata") or {}).get("generation")
        and ((before_payload.get("spec") or {}).get("template"))
        == ((after_payload.get("spec") or {}).get("template"))
    )


def _cooldown_check(config: ScenarioAConfig, now: datetime) -> CheckEvidence:
    path = config.execution.evidence_root / "A" / "_series" / "production-b0.json"
    if not path.is_file():
        return CheckEvidence(check_id="series_cooldown_ready", passed=True, observed="no_prior_run")
    payload = read_json(path)
    runs = payload.get("runs") or []
    if not runs:
        return CheckEvidence(check_id="series_cooldown_ready", passed=True, observed="empty_series")
    completed = datetime.fromisoformat(str(runs[-1]["completed_at"]).replace("Z", "+00:00"))
    elapsed = (now - completed).total_seconds()
    passed = elapsed >= config.execution.cooldown_seconds
    return CheckEvidence(
        check_id="series_cooldown_ready",
        passed=passed,
        observed={"elapsed_seconds": elapsed, "required_seconds": config.execution.cooldown_seconds},
        reason_code=None if passed else "cooldown_active",
    )


def prepare_scenario_a_preflight(
    *,
    config: ScenarioAConfig,
    project_root: Path,
    run_id: str,
    kubernetes: KubernetesAdapter | None = None,
    http: HttpAdapter | None = None,
    text_runner: TextRunner = _run_text,
) -> ScenarioAPreflight:
    run_root = config.execution.evidence_root / "A" / run_id
    state_store = ScenarioStateStore(config.execution.evidence_root / "A")
    state = state_store.load(run_id)
    if state.state != "non_disruptive_validated":
        raise ValueError(f"preflight_state_required:non_disruptive_validated:actual={state.state}")
    baseline_path = run_root / "report.json"
    baseline = OperationalFailureReport.model_validate_json(baseline_path.read_text(encoding="utf-8"))
    kube = kubernetes or KubernetesAdapter()
    selectors = discover_scenario_a_selectors(config, kube)
    observation = ScenarioACollector(kube, http or HttpAdapter()).collect(selectors)
    health = evaluate_scenario_a_health(observation, config.identity)
    source = collect_runtime_source(
        project_root=project_root,
        config=config,
        runner=text_runner,
    )
    rollback_payload = deployment_rollback_payload(observation.deployment)
    rollback_digest = payload_sha256(rollback_payload)
    rollback_path = run_root / "rollback-target.json"
    atomic_write_json(rollback_path, rollback_payload)
    identity = evaluate_identity_bundle(
        config,
        observed_image_digest=health.observed_identity["image_digest"],
        rollback_digest=rollback_digest,
    )
    now = datetime.now(timezone.utc)
    expected_uid = str(baseline.injection.target.get("uid") or "")
    reconciliation_path = run_root / "device-plugin-reconciliation-plan.json"
    reconciliation = read_json(reconciliation_path) if reconciliation_path.is_file() else {}
    checks = [
        CheckEvidence(
            check_id="baseline_readiness_closure",
            passed=not validate_closure(baseline, "readiness"),
            observed={"path": str(baseline_path), "decision": baseline.readiness_closure.decision},
            reason_code=(
                None if not validate_closure(baseline, "readiness") else "baseline_readiness_failed"
            ),
        ),
        CheckEvidence(
            check_id="exact_target_uid_stable",
            passed=selectors.pod.uid == expected_uid,
            observed={"baseline": expected_uid, "current": selectors.pod.uid},
            reason_code=None if selectors.pod.uid == expected_uid else "target_uid_changed",
        ),
        CheckEvidence(
            check_id="target_health_ready",
            passed=health.decision == "passed",
            observed={"decision": health.decision, "blockers": health.blockers},
            reason_code=None if health.decision == "passed" else "target_unhealthy",
        ),
        CheckEvidence(
            check_id="source_revision_converged",
            passed=(
                source.revision_converged
                and not source.source.dirty
                and source.source.commit == baseline.source.commit
            ),
            observed={
                "current": source.model_dump(mode="json"),
                "baseline_commit": baseline.source.commit,
            },
            reason_code=(
                None
                if source.revision_converged
                and not source.source.dirty
                and source.source.commit == baseline.source.commit
                else "revision_or_dirty_state_mismatch"
            ),
        ),
        CheckEvidence(
            check_id="device_plugin_reconciliation_non_mutating",
            passed=(
                reconciliation.get("mutation_performed") is False
                and reconciliation.get("decision") in {"no_change", "change_required"}
                and _same_daemonset_template(
                    run_root / "device-plugin-before.json",
                    run_root / "device-plugin-after.json",
                )
            ),
            observed={
                "decision": reconciliation.get("decision"),
                "mutation_performed": reconciliation.get("mutation_performed"),
            },
            reason_code=(
                None
                if reconciliation.get("mutation_performed") is False
                and reconciliation.get("decision") in {"no_change", "change_required"}
                and _same_daemonset_template(
                    run_root / "device-plugin-before.json",
                    run_root / "device-plugin-after.json",
                )
                else "reconciliation_mutation_or_diff_detected"
            ),
        ),
        _cooldown_check(config, now),
    ] + identity.checks
    blockers = [str(check.reason_code) for check in checks if not check.passed]
    target = TargetRef(
        namespace=selectors.pod.namespace or "",
        name=selectors.pod.name,
        uid=selectors.pod.uid,
    )
    action = "delete_pod_with_uid_precondition"
    preflight = ScenarioAPreflight(
        schema_version="evm.scenario_a_preflight.v1",
        run_id=run_id,
        checked_at=now,
        decision="passed" if not blockers else "blocked",
        source=source.source,
        target=target,
        deployment_uid=selectors.deployment.uid,
        action=action,
        action_digest=action_digest(
            run_id=run_id,
            action=action,
            target=target,
            source_revision=source.source.commit,
        ),
        identities=identity.identities,
        checks=checks,
        blockers=blockers,
        rollback_path=str(rollback_path.resolve()),
        rollback_digest=rollback_digest,
        baseline_report_path=str(baseline_path.resolve()),
        reconciliation_plan_path=str(reconciliation_path.resolve()),
        maintenance_impact=(
            "one single-replica production B0 Pod restart; a short endpoint interruption is expected"
        ),
    )
    atomic_write_json(run_root / "preflight.json", preflight.model_dump(mode="json"))
    next_state = "pending_approval" if preflight.decision == "passed" else "blocked"
    state_store.transition(
        run_id,
        next_state=next_state,
        expected_revision=state.revision,
        reason=f"scenario_a_preflight_{preflight.decision}",
        now=now,
    )
    return preflight


def issue_scenario_a_approval(
    *,
    config: ScenarioAConfig,
    run_id: str,
    approver: str,
    ttl_seconds: int,
) -> ApprovalResult:
    run_root = config.execution.evidence_root / "A" / run_id
    preflight = ScenarioAPreflight.model_validate_json(
        (run_root / "preflight.json").read_text(encoding="utf-8")
    )
    if preflight.decision != "passed":
        raise ValueError("approval_rejected_preflight_not_passed")
    state_store = ScenarioStateStore(config.execution.evidence_root / "A")
    state = state_store.load(run_id)
    if state.state != "pending_approval":
        raise ValueError(f"approval_state_required:pending_approval:actual={state.state}")
    approval_store = ApprovalStore(config.execution.evidence_root / "A")
    binding = approval_store.issue(
        run_id=run_id,
        target=preflight.target,
        action=preflight.action,
        source_revision=preflight.source.commit,
        approver=approver,
        ttl_seconds=ttl_seconds,
    )
    if binding.action_digest != preflight.action_digest:
        raise ValueError("approval_action_digest_mismatch")
    updated = state_store.transition(
        run_id,
        next_state="approved",
        expected_revision=state.revision,
        reason=f"maintenance_approval_issued:{binding.approval_id}",
    )
    path = approval_store.root / f"{binding.approval_id}.json"
    atomic_write_json(
        run_root / "approval-reference.json",
        {
            "approval_id": binding.approval_id,
            "approval_path": str(path.resolve()),
            "action_digest": binding.action_digest,
            "expires_at": binding.expires_at.isoformat(),
            "single_use": binding.single_use,
        },
    )
    return ApprovalResult(binding=binding, path=path, state_revision=updated.revision)
