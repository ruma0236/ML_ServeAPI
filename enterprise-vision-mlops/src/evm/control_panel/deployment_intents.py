from __future__ import annotations

import argparse
import json
import os
import re
import time
from contextlib import contextmanager
from functools import wraps
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from evm.control_panel.cdct import (
    load_ci_evidence,
    validate_ci_evidence,
    with_ci_bundle_digest,
)
from evm.control_panel.cycle_catalog import find_cycle
from evm.control_panel.promotion_policy import evaluate_cycle_promotion
from evm.control_panel.readiness_evaluator import (
    canonical_evidence_uri,
    payload_sha256,
    runtime_path,
)

if TYPE_CHECKING:
    from evm.control_panel.model_candidates import ModelCandidateSelection
from evm.control_panel.schemas import (
    CIEvidenceBundle,
    CIEvidenceValidation,
    CycleRun,
    DeploymentExecutionResult,
    DeploymentIntent,
    DeploymentIntentList,
    DeploymentIntentRequest,
    DeploymentIntentState,
    DeploymentTransition,
    DeploymentTransitionRequest,
    PromotionPolicyDecision,
    PromotionPolicyRequest,
)


DEFAULT_INTENT_ROOT = (
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/deployment_intents"
)
DEFAULT_CI_EVIDENCE_PATH = (
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/ci/latest_ci_evidence.json"
)
DEFAULT_MANIFEST_REFS = {
    "evm-b7-serving": "infra/kubernetes/model-runtime/b7-serving-deployment.yaml",
    "evm-b0-production": (
        "infra/kubernetes/expedited-production-validation/production"
    ),
}
ALLOWED_TARGET_NAMES = set(DEFAULT_MANIFEST_REFS)
LIFECYCLE_TARGET_PATTERN = re.compile(
    r"evm-b(?:0|7)-(?:dev|test|staging|preprod|production)"
)
_LEDGER_LOCK = RLock()


def ledger_transaction(function):
    @wraps(function)
    def synchronized(*args, **kwargs):
        with _LEDGER_LOCK:
            with intent_file_lock():
                return function(*args, **kwargs)

    return synchronized


class DeploymentIntentBlocked(RuntimeError):
    def __init__(self, blockers: list[str], ci_evidence: CIEvidenceValidation | None = None):
        self.blockers = sorted(set(blockers))
        self.ci_evidence = ci_evidence
        super().__init__(", ".join(self.blockers))


class DeploymentIntentNotFound(RuntimeError):
    pass


class DeploymentVersionConflict(RuntimeError):
    pass


class DeploymentTransitionRejected(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def intent_root() -> Path:
    return Path(os.getenv("EVM_DEPLOYMENT_INTENT_ROOT", DEFAULT_INTENT_ROOT))


def ledger_path() -> Path:
    return intent_root() / "deployment_intents.json"


@contextmanager
def intent_file_lock(timeout_seconds: float = 30.0):
    root = intent_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".deployment-intents.lock"
    deadline = time.monotonic() + timeout_seconds
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"{os.getpid()} {utc_now()}".encode("utf-8"))
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > 300:
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise DeploymentTransitionRejected("deployment_ledger_lock_timeout")
            time.sleep(0.05)
    try:
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        path.unlink(missing_ok=True)


def ci_evidence_path() -> str:
    return os.getenv("EVM_CI_EVIDENCE_PATH", DEFAULT_CI_EVIDENCE_PATH)


def read_ci_bundle(path: str | Path) -> CIEvidenceBundle:
    evidence_path = runtime_path(path)
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    return CIEvidenceBundle.model_validate(payload)


def read_intents() -> DeploymentIntentList:
    with _LEDGER_LOCK:
        path = ledger_path()
        if not path.exists():
            return DeploymentIntentList(intents=[])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return DeploymentIntentList(
                intents=[], status="blocked", blockers=["deployment_ledger_malformed"]
            )
        if not isinstance(payload, list):
            return DeploymentIntentList(
                intents=[], status="blocked", blockers=["deployment_ledger_not_array"]
            )
        try:
            intents = [DeploymentIntent.model_validate(item) for item in payload]
        except ValueError:
            return DeploymentIntentList(
                intents=[], status="blocked", blockers=["deployment_ledger_schema_invalid"]
            )
        return DeploymentIntentList(intents=intents)


def write_intents(intents: DeploymentIntentList) -> None:
    if intents.status != "pass" or intents.blockers:
        raise DeploymentIntentBlocked(intents.blockers or ["deployment_ledger_not_writable"])
    with _LEDGER_LOCK:
        atomic_write_json(
            ledger_path(),
            [intent.model_dump(mode="json") for intent in intents.intents],
        )


def latest_intent() -> DeploymentIntent | None:
    intents = read_intents().intents
    return intents[0] if intents else None


def get_intent(intent_id: str) -> DeploymentIntent:
    ledger = read_intents()
    if ledger.status != "pass" or ledger.blockers:
        raise DeploymentIntentBlocked(ledger.blockers)
    intent = next(
        (item for item in ledger.intents if item.intent_id == intent_id),
        None,
    )
    if intent is None:
        raise DeploymentIntentNotFound(intent_id)
    return intent


@ledger_transaction
def create_deployment_intent(
    request: DeploymentIntentRequest,
    *,
    cycle: CycleRun | None = None,
    ci_path: str | Path | None = None,
    manifest_ref: str | Path | None = None,
) -> DeploymentIntent:
    if not request.dry_run:
        raise DeploymentIntentBlocked(["direct_non_dry_run_creation_forbidden"])
    selection: ModelCandidateSelection | None = None
    if cycle is None:
        cycle, selection = resolve_request_cycle(request)
    elif request.model_selection_id:
        from evm.control_panel.model_candidates import get_model_selection

        try:
            selection = get_model_selection(request.model_selection_id)
        except KeyError:
            raise DeploymentIntentBlocked(["model_selection_not_found"]) from None
    expected_commit = expected_ci_commit(cycle)
    selected_ci_path = str(ci_path or ci_evidence_path())
    validation = load_ci_evidence(
        selected_ci_path,
        expected_commit=expected_commit,
        report_uri=intent_root() / "latest_ci_validation.json",
    )
    try:
        bundle = read_ci_bundle(selected_ci_path)
    except (OSError, json.JSONDecodeError, ValueError):
        raise DeploymentIntentBlocked(validation.blockers, validation) from None
    policy = evaluate_intent_policy(cycle, request)
    selected_manifest_ref = str(
        manifest_ref or DEFAULT_MANIFEST_REFS.get(request.target.name, "")
    )
    blockers, evidence = deployment_gate_blockers(
        request,
        cycle,
        bundle,
        validation,
        policy,
        expected_commit=expected_commit,
        allow_pending_approval=True,
        manifest_ref=selected_manifest_ref,
    )
    if selection is not None:
        blockers.extend(selection_blockers(selection, request, cycle, evidence))
    if blockers:
        raise DeploymentIntentBlocked(blockers, validation)

    created_at = utc_now()
    intent_material = {
        "request": request.model_dump(mode="json"),
        "ci_bundle_digest": bundle.bundle_digest,
        "readiness_evaluation_id": cycle.readiness_evaluation.evaluation_id,
        "promotion_policy_id": policy.decision_id,
        "created_at": created_at,
    }
    intent_id = f"deploy-{payload_sha256(intent_material)[:16]}"
    immutable_ci_path = intent_root() / intent_id / "ci_evidence.json"
    atomic_write_json(immutable_ci_path, bundle.model_dump(mode="json"))
    transition = DeploymentTransition(
        from_state="created",
        to_state="dry_run",
        actor=request.actor,
        timestamp=created_at,
        environment=request.target_environment,
        namespace=request.target_namespace,
        artifact_digest=bundle.bundle_digest,
        reason=request.reason,
        result="validated",
    )
    intent = DeploymentIntent(
        **request.model_dump(),
        intent_id=intent_id,
        state="dry_run",
        version=1,
        created_at=created_at,
        updated_at=created_at,
        ci_evidence=validation,
        ci_evidence_uri=canonical_evidence_uri(immutable_ci_path),
        ci_bundle_digest=bundle.bundle_digest,
        readiness_evaluation_id=cycle.readiness_evaluation.evaluation_id,
        promotion_policy=policy,
        model_candidate_id=evidence["model_candidate_id"],
        model_artifact_uri=evidence["model_artifact_uri"],
        model_digest=evidence["model_digest"],
        image_digest=bundle.image_digest,
        config_render_digest=bundle.config_render_digest,
        rollback_reference=evidence["rollback_reference"],
        manifest_ref=selected_manifest_ref,
        audit_uri=canonical_evidence_uri(
            intent_root() / intent_id / "deployment_intent.json"
        ),
        transitions=[transition],
    )
    intents = read_intents()
    intents.intents.insert(0, intent)
    write_intents(intents)
    write_intent_snapshot(intent)
    return intent


def deployment_gate_blockers(
    request: DeploymentIntentRequest,
    cycle: CycleRun,
    bundle: CIEvidenceBundle,
    validation: CIEvidenceValidation,
    policy: PromotionPolicyDecision,
    *,
    expected_commit: str | None,
    allow_pending_approval: bool = False,
    manifest_ref: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    blockers = list(validation.blockers)
    readiness = cycle.readiness_evaluation
    if not validation.valid:
        blockers.append("ci_evidence_not_valid")
    if not expected_commit:
        blockers.append("expected_ci_commit_not_configured")
    elif bundle.commit_sha.lower() != expected_commit.lower():
        blockers.append("ci_commit_mismatch")
    if readiness is None or readiness.decision != "ready":
        blockers.append("artifact_readiness_not_ready")
    if policy.decision == "blocked":
        blockers.append("environment_policy_blocked")
    if policy.decision == "pending_approval" and not allow_pending_approval:
        blockers.append("environment_policy_pending_approval")
    if request.target_environment != policy.target_environment:
        blockers.append("target_environment_policy_mismatch")
    if request.target_namespace != policy.target_namespace:
        blockers.append("target_namespace_policy_mismatch")
    if request.target.namespace != request.target_namespace:
        blockers.append("target_resource_namespace_mismatch")
    if request.target.kind != "Deployment":
        blockers.append("target_kind_not_allowed")
    if not deployment_target_allowed(request.target.name, manifest_ref):
        blockers.append("target_name_not_allowed")

    model_check = readiness_check(cycle, "model_artifact")
    rollback_check = readiness_check(cycle, "rollback_reference")
    model_digest = str(model_check.get("actual_sha256") or "")
    model_artifact_uri = str(model_check.get("evidence_uri") or "")
    model_candidate_id = str(readiness.candidate_id if readiness else "")
    rollback_reference = str(rollback_check.get("evidence_uri") or "")
    if model_check.get("status") != "pass" or not model_digest:
        blockers.append("immutable_model_artifact_not_ready")
    if not model_artifact_uri:
        blockers.append("model_artifact_uri_missing")
    if not model_candidate_id:
        blockers.append("model_candidate_id_missing")
    if rollback_check.get("status") != "pass" or not rollback_reference:
        blockers.append("rollback_reference_not_ready")
    runtime_digest = str(
        readiness_check(cycle, "kubernetes_runtime").get("serving_image_digest") or ""
    )
    if not runtime_digest:
        blockers.append("runtime_image_digest_missing")
    elif bundle.image_digest != runtime_digest:
        blockers.append("ci_runtime_image_digest_mismatch")
    return sorted(set(blockers)), {
        "model_candidate_id": model_candidate_id,
        "model_artifact_uri": model_artifact_uri,
        "model_digest": model_digest,
        "rollback_reference": rollback_reference,
    }


def request_approval(
    intent_id: str,
    request: DeploymentTransitionRequest,
) -> DeploymentIntent:
    def validate_requester(intent: DeploymentIntent) -> dict[str, Any]:
        if request.actor != intent.actor:
            raise DeploymentTransitionRejected("approval_requester_mismatch")
        return {}

    return transition_intent(
        intent_id,
        request,
        allowed_from={"dry_run"},
        to_state="pending_approval",
        result="approval_requested",
        mutate=validate_requester,
    )


def approve_intent(
    intent_id: str,
    request: DeploymentTransitionRequest,
) -> DeploymentIntent:
    def mutate(intent: DeploymentIntent) -> dict[str, Any]:
        if request.actor == intent.actor:
            raise DeploymentTransitionRejected("requester_approver_conflict")
        allowed = intent.promotion_policy.required_approvals
        if allowed and request.actor not in allowed:
            raise DeploymentTransitionRejected("approver_not_allowed")
        return {"approver": request.actor, "approved_at": utc_now()}

    return transition_intent(
        intent_id,
        request,
        allowed_from={"pending_approval"},
        to_state="pending_approval",
        result="approved",
        mutate=mutate,
    )


def queue_intent(
    intent_id: str,
    request: DeploymentTransitionRequest,
    *,
    cycle: CycleRun | None = None,
) -> DeploymentIntent:
    current = get_intent(intent_id)
    if not current.approver:
        raise DeploymentTransitionRejected("approval_missing")
    if request.actor not in {current.actor, current.approver}:
        raise DeploymentTransitionRejected("queue_actor_not_authorized")
    cycle = cycle or current_cycle()
    expected_commit = expected_ci_commit(cycle)
    try:
        bundle = read_ci_bundle(current.ci_evidence_uri)
    except (OSError, json.JSONDecodeError, ValueError):
        raise DeploymentIntentBlocked(["ci_evidence_unavailable_at_queue"]) from None
    validation = validate_ci_evidence(bundle, expected_commit=expected_commit)
    gate_request = DeploymentIntentRequest(
        target_environment=current.target_environment,
        target_namespace=current.target_namespace,
        target=current.target,
        actor=current.actor,
        reason=current.reason,
        dry_run=True,
    )
    policy = evaluate_intent_policy(cycle, gate_request, approver=current.approver)
    blockers, evidence = deployment_gate_blockers(
        gate_request,
        cycle,
        bundle,
        validation,
        policy,
        expected_commit=expected_commit,
        manifest_ref=current.manifest_ref,
    )
    if current.ci_bundle_digest != bundle.bundle_digest:
        blockers.append("ci_bundle_changed_after_creation")
    if current.readiness_evaluation_id != cycle.readiness_evaluation.evaluation_id:
        blockers.append("readiness_changed_after_creation")
    if current.model_digest != evidence["model_digest"]:
        blockers.append("model_artifact_changed_after_creation")
    if current.model_artifact_uri != evidence["model_artifact_uri"]:
        blockers.append("model_artifact_uri_changed_after_creation")
    if current.model_candidate_id != evidence["model_candidate_id"]:
        blockers.append("model_candidate_changed_after_creation")
    if current.promotion_policy.policy_version != policy.policy_version:
        blockers.append("promotion_policy_version_changed_after_creation")
    if blockers:
        raise DeploymentIntentBlocked(blockers, validation)
    return transition_intent(
        intent_id,
        request,
        allowed_from={"pending_approval"},
        to_state="queued",
        result="queued_for_executor",
        mutate=lambda _: {"promotion_policy": policy, "ci_evidence": validation},
    )


def revalidate_queued_intent(
    intent_id: str,
    *,
    cycle: CycleRun | None = None,
) -> DeploymentIntent:
    current = get_intent(intent_id)
    if current.state != "queued":
        raise DeploymentTransitionRejected("executor_requires_queued_intent")
    cycle = cycle or current_cycle()
    expected_commit = expected_ci_commit(cycle)
    try:
        bundle = read_ci_bundle(current.ci_evidence_uri)
    except (OSError, json.JSONDecodeError, ValueError):
        raise DeploymentIntentBlocked(["ci_evidence_unavailable_at_execution"]) from None
    validation = validate_ci_evidence(bundle, expected_commit=expected_commit)
    request = DeploymentIntentRequest(
        target_environment=current.target_environment,
        target_namespace=current.target_namespace,
        target=current.target,
        actor=current.actor,
        reason=current.reason,
        dry_run=True,
    )
    policy = evaluate_intent_policy(cycle, request, approver=current.approver)
    blockers, evidence = deployment_gate_blockers(
        request,
        cycle,
        bundle,
        validation,
        policy,
        expected_commit=expected_commit,
        manifest_ref=current.manifest_ref,
    )
    if current.ci_bundle_digest != bundle.bundle_digest:
        blockers.append("ci_bundle_changed_before_execution")
    if current.readiness_evaluation_id != cycle.readiness_evaluation.evaluation_id:
        blockers.append("readiness_changed_before_execution")
    if current.model_digest != evidence["model_digest"]:
        blockers.append("model_artifact_changed_before_execution")
    if current.model_artifact_uri != evidence["model_artifact_uri"]:
        blockers.append("model_artifact_uri_changed_before_execution")
    if current.model_candidate_id != evidence["model_candidate_id"]:
        blockers.append("model_candidate_changed_before_execution")
    if current.promotion_policy.decision_id != policy.decision_id:
        blockers.append("promotion_policy_changed_before_execution")
    if blockers:
        raise DeploymentIntentBlocked(blockers, validation)
    return current


def mark_applying(intent_id: str, actor: str = "deployment-executor") -> DeploymentIntent:
    intent = get_intent(intent_id)
    return transition_intent(
        intent_id,
        DeploymentTransitionRequest(
            actor=actor,
            reason="executor accepted queued intent",
            expected_version=intent.version,
        ),
        allowed_from={"queued"},
        to_state="applying",
        result="executor_started",
    )


def finish_execution(
    intent_id: str,
    execution: DeploymentExecutionResult,
    actor: str = "deployment-executor",
) -> DeploymentIntent:
    intent = get_intent(intent_id)
    target_state: DeploymentIntentState = (
        "applied" if execution.status == "applied" else "failed"
    )
    return transition_intent(
        intent_id,
        DeploymentTransitionRequest(
            actor=actor,
            reason=f"executor {execution.action} finished",
            expected_version=intent.version,
        ),
        allowed_from={"applying"},
        to_state=target_state,
        result=execution.status,
        mutate=lambda _: {"execution_result": execution},
    )


def mark_rolled_back(
    intent_id: str,
    execution: DeploymentExecutionResult,
    actor: str = "deployment-executor",
) -> DeploymentIntent:
    intent = get_intent(intent_id)
    return transition_intent(
        intent_id,
        DeploymentTransitionRequest(
            actor=actor,
            reason="executor rollback finished",
            expected_version=intent.version,
        ),
        allowed_from={"applied", "failed"},
        to_state="rolled_back",
        result=execution.status,
        mutate=lambda _: {"execution_result": execution},
    )


def transition_intent(
    intent_id: str,
    request: DeploymentTransitionRequest,
    *,
    allowed_from: set[DeploymentIntentState],
    to_state: DeploymentIntentState,
    result: str,
    mutate: Callable[[DeploymentIntent], dict[str, Any]] | None = None,
) -> DeploymentIntent:
    with _LEDGER_LOCK:
        with intent_file_lock():
            intents = read_intents()
            index = next(
                (i for i, item in enumerate(intents.intents) if item.intent_id == intent_id),
                None,
            )
            if index is None:
                raise DeploymentIntentNotFound(intent_id)
            intent = intents.intents[index]
            if request.expected_version != intent.version:
                raise DeploymentVersionConflict(
                    f"expected version {request.expected_version}, current {intent.version}"
                )
            if intent.state not in allowed_from:
                raise DeploymentTransitionRejected(
                    f"state {intent.state} cannot transition to {to_state}"
                )
            updates = mutate(intent) if mutate else {}
            timestamp = utc_now()
            transition = DeploymentTransition(
                from_state=intent.state,
                to_state=to_state,
                actor=request.actor,
                timestamp=timestamp,
                environment=intent.target_environment,
                namespace=intent.target_namespace,
                artifact_digest=intent.ci_bundle_digest,
                reason=request.reason,
                result=result,
            )
            intent = intent.model_copy(
                update={
                    **updates,
                    "state": to_state,
                    "version": intent.version + 1,
                    "updated_at": timestamp,
                    "transitions": [*intent.transitions, transition],
                }
            )
            intents.intents[index] = intent
            write_intents(intents)
            write_intent_snapshot(intent)
            return intent


def readiness_check(cycle: CycleRun, check_id: str) -> dict[str, Any]:
    evaluation = cycle.readiness_evaluation
    if evaluation is None:
        return {}
    check = next((item for item in evaluation.checks if item.check_id == check_id), None)
    if check is None:
        return {}
    return {
        **check.observed,
        "status": check.status,
        "evidence_uri": check.evidence_uri,
    }


def deployment_target_allowed(target_name: str, manifest_ref: str | None) -> bool:
    if target_name in ALLOWED_TARGET_NAMES and not manifest_ref:
        return True
    if target_name in ALLOWED_TARGET_NAMES and manifest_ref == DEFAULT_MANIFEST_REFS[target_name]:
        return True
    if not manifest_ref or not LIFECYCLE_TARGET_PATTERN.fullmatch(target_name):
        return False
    generated_root = os.getenv("EVM_KUBERNETES_GENERATED_MANIFEST_ROOT", "").strip()
    if not generated_root:
        return False
    candidate = Path(manifest_ref).resolve()
    allowed_root = Path(generated_root).resolve()
    return candidate.is_relative_to(allowed_root) and (
        candidate / "kustomization.yaml"
    ).is_file()


def expected_ci_commit(cycle: CycleRun) -> str | None:
    configured = os.getenv("EVM_EXPECTED_CI_COMMIT", "").strip()
    release_ref = cycle.environment.release_ref.strip() if cycle.environment and cycle.environment.release_ref else ""
    candidate = configured or release_ref
    return candidate if len(candidate) == 40 else None


def evaluate_intent_policy(
    cycle: CycleRun,
    request: DeploymentIntentRequest,
    *,
    approver: str | None = None,
) -> PromotionPolicyDecision:
    return evaluate_cycle_promotion(
        cycle,
        PromotionPolicyRequest(
            target_environment=request.target_environment,
            target_namespace=request.target_namespace,
            requester=request.actor,
            approver=approver,
        ),
        persist=True,
    )


def current_cycle() -> CycleRun:
    from evm.control_panel.aggregation import build_latest_cycle

    return build_latest_cycle()


def resolve_request_cycle(
    request: DeploymentIntentRequest,
) -> tuple[CycleRun, ModelCandidateSelection | None]:
    from evm.control_panel.model_candidates import get_model_selection

    latest = current_cycle()
    selection: ModelCandidateSelection | None = None
    requested_cycle_id = request.cycle_id
    if request.model_selection_id:
        try:
            selection = get_model_selection(request.model_selection_id)
        except KeyError:
            raise DeploymentIntentBlocked(["model_selection_not_found"]) from None
        if requested_cycle_id and requested_cycle_id != selection.cycle_id:
            raise DeploymentIntentBlocked(["model_selection_cycle_mismatch"])
        requested_cycle_id = selection.cycle_id
    if not requested_cycle_id:
        return latest, selection
    selected = find_cycle(requested_cycle_id, latest)
    if selected is None:
        raise DeploymentIntentBlocked(["requested_cycle_not_found"])
    return selected, selection


def selection_blockers(
    selection: ModelCandidateSelection,
    request: DeploymentIntentRequest,
    cycle: CycleRun,
    evidence: dict[str, str],
) -> list[str]:
    blockers: list[str] = []
    if request.model_selection_id != selection.selection_id:
        blockers.append("model_selection_identity_mismatch")
    if request.cycle_id and request.cycle_id != selection.cycle_id:
        blockers.append("model_selection_cycle_mismatch")
    if cycle.cycle_id != selection.cycle_id:
        blockers.append("resolved_cycle_selection_mismatch")
    if evidence.get("model_candidate_id") != selection.candidate_id:
        blockers.append("model_selection_candidate_mismatch")
    if evidence.get("model_digest") != selection.artifact_digest:
        blockers.append("model_selection_digest_mismatch")
    if evidence.get("model_artifact_uri") != selection.artifact_uri:
        blockers.append("model_selection_artifact_mismatch")
    if cycle.dataset.version != selection.dataset_version:
        blockers.append("model_selection_dataset_mismatch")
    return blockers


def write_intent_snapshot(intent: DeploymentIntent) -> None:
    atomic_write_json(
        intent_root() / intent.intent_id / "deployment_intent.json",
        intent.model_dump(mode="json"),
    )
    atomic_write_json(
        intent_root() / "latest_deployment_intent.json",
        intent.model_dump(mode="json"),
    )


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate CI evidence and manage deployment intents.")
    parser.add_argument("--ledger-root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate-ci")
    validate_parser.add_argument("--input", required=True)
    validate_parser.add_argument("--expected-commit")
    validate_parser.add_argument("--output")
    validate_parser.add_argument("--require-valid", action="store_true")

    build_parser = subparsers.add_parser("build-ci")
    build_parser.add_argument("--repository", required=True)
    build_parser.add_argument("--workflow-name", required=True)
    build_parser.add_argument("--workflow-run-id", required=True)
    build_parser.add_argument("--workflow-run-attempt", type=int, default=1)
    build_parser.add_argument("--commit-sha", required=True)
    build_parser.add_argument("--ref", required=True)
    build_parser.add_argument("--event", required=True)
    build_parser.add_argument("--conclusion", choices=["success", "failure", "cancelled", "timed_out"], required=True)
    for result_name in (
        "python-test-result",
        "frontend-test-result",
        "evidence-validator-result",
        "compose-config-result",
        "kustomize-render-result",
    ):
        build_parser.add_argument(f"--{result_name}", choices=["pass", "fail"], required=True)
    build_parser.add_argument("--image-digest", required=True)
    build_parser.add_argument("--config-render-digest", required=True)
    build_parser.add_argument("--contract-digest", required=True)
    build_parser.add_argument("--source-uri", required=True)
    build_parser.add_argument("--generated-at")
    build_parser.add_argument("--output", type=Path, required=True)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--input", required=True)
    create_parser.add_argument("--cycle")
    create_parser.add_argument("--ci-evidence")
    create_parser.add_argument("--dry-run", action="store_true")

    for name in ("request-approval", "approve", "queue"):
        transition_parser = subparsers.add_parser(name)
        transition_parser.add_argument("--intent-id", required=True)
        transition_parser.add_argument("--actor", default="operator")
        transition_parser.add_argument("--reason", default=name)
        transition_parser.add_argument("--expected-version", type=int)

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--intent-id", required=True)
    subparsers.add_parser("list")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.ledger_root:
        os.environ["EVM_DEPLOYMENT_INTENT_ROOT"] = args.ledger_root
    if args.command == "validate-ci":
        bundle = read_ci_bundle(args.input)
        validation = validate_ci_evidence(bundle, expected_commit=args.expected_commit)
        rendered = validation.model_dump(mode="json")
        if args.output:
            atomic_write_json(Path(args.output), rendered)
        print(json.dumps(rendered, ensure_ascii=False, indent=2))
        return 2 if args.require_valid and not validation.valid else 0
    if args.command == "build-ci":
        bundle = with_ci_bundle_digest(
            {
                "repository": args.repository,
                "workflow_name": args.workflow_name,
                "workflow_run_id": args.workflow_run_id,
                "workflow_run_attempt": args.workflow_run_attempt,
                "commit_sha": args.commit_sha,
                "ref": args.ref,
                "event": args.event,
                "status": "completed",
                "conclusion": args.conclusion,
                "python_test_result": args.python_test_result,
                "frontend_test_result": args.frontend_test_result,
                "evidence_validator_result": args.evidence_validator_result,
                "compose_config_result": args.compose_config_result,
                "kustomize_render_result": args.kustomize_render_result,
                "image_digest": args.image_digest,
                "config_render_digest": args.config_render_digest,
                "contract_digest": args.contract_digest,
                "source_uri": args.source_uri,
                "generated_at": args.generated_at or utc_now(),
            }
        )
        atomic_write_json(args.output, bundle.model_dump(mode="json"))
        print(json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0
    if args.command == "create":
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        request_payload = payload.get("request", payload)
        request_payload["dry_run"] = bool(args.dry_run or request_payload.get("dry_run", True))
        request = DeploymentIntentRequest.model_validate(request_payload)
        cycle_path = args.cycle or payload.get("cycle_path")
        cycle = (
            CycleRun.model_validate_json(Path(cycle_path).read_text(encoding="utf-8"))
            if cycle_path
            else None
        )
        intent = create_deployment_intent(
            request,
            cycle=cycle,
            ci_path=args.ci_evidence or payload.get("ci_evidence_path"),
        )
        print(json.dumps(intent.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0
    if args.command == "show":
        result: Any = get_intent(args.intent_id)
    elif args.command == "list":
        result = read_intents()
    else:
        current = get_intent(args.intent_id)
        request = DeploymentTransitionRequest(
            actor=args.actor,
            reason=args.reason,
            expected_version=args.expected_version or current.version,
        )
        if args.command == "request-approval":
            result = request_approval(args.intent_id, request)
        elif args.command == "approve":
            result = approve_intent(args.intent_id, request)
        else:
            result = queue_intent(args.intent_id, request)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
