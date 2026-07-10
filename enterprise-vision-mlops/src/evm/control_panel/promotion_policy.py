from __future__ import annotations

import argparse
import json
import os
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from evm.control_panel.readiness_evaluator import (
    canonical_evidence_uri,
    payload_sha256,
    runtime_path,
)
from evm.control_panel.schemas import (
    CycleRun,
    EnvironmentTier,
    PromotionPolicyCheck,
    PromotionPolicyDecision,
    PromotionPolicyInput,
    PromotionPolicyRequest,
    State,
)


DEFAULT_POLICY_PATH = Path("configs/promotion_policy.toml")
DEFAULT_NAMESPACES: dict[EnvironmentTier, str] = {
    "dev": "evm-dev",
    "test": "evm-test",
    "staging": "evm-staging",
    "pre-production": "evm-pre-production",
    "production": "evm-production",
}
CHECK_ORDER = [
    "ownership",
    "namespace",
    "readiness",
    "ci",
    "cd",
    "ct",
    "model_digest",
    "image_digest",
    "rollback_reference",
    "approval",
    "separation_of_duties",
]


class PromotionPolicyDenied(RuntimeError):
    def __init__(self, decision: PromotionPolicyDecision):
        self.decision = decision
        reasons = ", ".join(decision.reason_codes)
        super().__init__(f"promotion policy {decision.decision}: {reasons}")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_policy(path: str | Path = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    policy_path = Path(os.getenv("EVM_PROMOTION_POLICY_PATH", str(path)))
    with policy_path.open("rb") as fp:
        payload = tomllib.load(fp)
    valid_policy = isinstance(payload.get("policy"), dict)
    valid_environments = isinstance(payload.get("environments"), dict)
    if not valid_policy or not valid_environments:
        raise ValueError(f"promotion policy is malformed: {policy_path}")
    return payload


def immutable_digest(value: str | None, *, image: bool = False) -> bool:
    if not value:
        return False
    pattern = r"(?:.+@)?sha256:[0-9a-fA-F]{64}" if image else r"(?:sha256:)?[0-9a-fA-F]{64}"
    return re.fullmatch(pattern, value.strip()) is not None


def evaluate_promotion_policy(
    inputs: PromotionPolicyInput,
    *,
    policy_path: str | Path = DEFAULT_POLICY_PATH,
    evidence_root: str | Path | None = None,
    persist: bool = False,
    evaluated_at: str | None = None,
) -> PromotionPolicyDecision:
    config = load_policy(policy_path)
    policy_config = config["policy"]
    environment_config = config["environments"].get(inputs.target_environment)
    timestamp = evaluated_at or utc_now()
    stable_inputs = inputs.model_dump(mode="json")
    input_digest = payload_sha256(stable_inputs)
    decision_id = f"promotion-{input_digest[:16]}"

    if not isinstance(environment_config, dict):
        decision = PromotionPolicyDecision(
            decision_id=decision_id,
            policy_version=str(policy_config.get("version", "unknown")),
            decision="blocked",
            status="blocked",
            target_environment=inputs.target_environment,
            target_namespace=inputs.target_namespace,
            requester=inputs.requester,
            approver=inputs.approver,
            approval_policy="undefined",
            evaluated_at=timestamp,
            input_digest=input_digest,
            required_checks=["environment"],
            reason_codes=["target_environment_unknown"],
            checks=[
                PromotionPolicyCheck(
                    check_id="environment",
                    status="blocked",
                    reason_code="target_environment_unknown",
                    evidence={"target_environment": inputs.target_environment},
                )
            ],
        )
        if persist:
            return persist_decision(decision, stable_inputs, policy_config, evidence_root)
        return decision

    required_checks = [str(item) for item in environment_config.get("required_checks", [])]
    allowed_namespaces = [str(item) for item in environment_config.get("allowed_namespaces", [])]
    approval_roles = [str(item) for item in environment_config.get("approval_roles", [])]
    check_values = check_evidence(inputs, allowed_namespaces, approval_roles)
    checks: list[PromotionPolicyCheck] = []
    hard_reasons: list[str] = []
    pending_reasons: list[str] = []

    for check_id in CHECK_ORDER:
        status, reason_code, evidence = check_values[check_id]
        required = check_id in required_checks
        checks.append(
            PromotionPolicyCheck(
                check_id=check_id,
                status=status,
                required=required,
                reason_code=reason_code if required and status != "pass" else None,
                evidence=evidence,
            )
        )
        if not required or status == "pass":
            continue
        if check_id == "approval" and reason_code == "approver_required":
            pending_reasons.append(reason_code)
            continue
        if check_id == "separation_of_duties" and not inputs.approver:
            continue
        hard_reasons.append(reason_code)

    if hard_reasons:
        policy_decision = "blocked"
        status: State = "blocked"
    elif pending_reasons:
        policy_decision = "pending_approval"
        status = "queued"
    else:
        policy_decision = "allow"
        status = "pass"

    decision = PromotionPolicyDecision(
        decision_id=decision_id,
        policy_version=str(policy_config.get("version", "unknown")),
        decision=policy_decision,
        status=status,
        target_environment=inputs.target_environment,
        target_namespace=inputs.target_namespace,
        requester=inputs.requester,
        approver=inputs.approver,
        approval_policy=str(environment_config.get("approval_policy", "undefined")),
        evaluated_at=timestamp,
        input_digest=input_digest,
        required_checks=required_checks,
        required_approvals=approval_roles,
        reason_codes=sorted(set(hard_reasons + pending_reasons)),
        checks=checks,
    )
    if persist:
        return persist_decision(decision, stable_inputs, policy_config, evidence_root)
    return decision


def check_evidence(
    inputs: PromotionPolicyInput,
    allowed_namespaces: list[str],
    approval_roles: list[str],
) -> dict[str, tuple[State, str, dict[str, str | int | float | bool | None]]]:
    ownership_ok = bool(
        inputs.org_context
        and inputs.org_context.ownership_status == "pass"
        and not inputs.org_context.missing_owners
    )
    namespace_ok = inputs.target_namespace in allowed_namespaces
    readiness_ok = inputs.readiness_decision == "ready"
    model_digest_ok = immutable_digest(inputs.model_digest)
    image_digest_ok = immutable_digest(inputs.image_digest, image=True)
    rollback_ok = bool(inputs.rollback_ready and inputs.rollback_reference)
    approver_present = bool(inputs.approver)
    approver_allowed = approver_present and (
        not approval_roles or inputs.approver in approval_roles
    )
    separated = approver_present and inputs.approver != inputs.requester

    return {
        "ownership": check_value(ownership_ok, "ownership_incomplete", owner_evidence(inputs)),
        "namespace": check_value(
            namespace_ok,
            "namespace_not_allowed",
            {
                "target_namespace": inputs.target_namespace,
                "allowed_namespaces": ",".join(allowed_namespaces),
            },
        ),
        "readiness": check_value(
            readiness_ok,
            "readiness_not_ready",
            {"readiness_decision": inputs.readiness_decision},
        ),
        "ci": state_check(inputs.ci_status, "ci_not_passing"),
        "cd": state_check(inputs.cd_status, "cd_not_passing"),
        "ct": state_check(inputs.ct_status, "ct_not_passing"),
        "model_digest": check_value(
            model_digest_ok,
            "immutable_model_digest_missing",
            {"model_digest": inputs.model_digest or ""},
        ),
        "image_digest": check_value(
            image_digest_ok,
            "immutable_image_digest_missing",
            {"image_digest": inputs.image_digest or ""},
        ),
        "rollback_reference": check_value(
            rollback_ok,
            "rollback_reference_missing_or_invalid",
            {
                "rollback_reference": inputs.rollback_reference or "",
                "rollback_ready": inputs.rollback_ready,
            },
        ),
        "approval": (
            ("pass", "", {"approver": inputs.approver or ""})
            if approver_allowed
            else (
                "blocked",
                "approver_not_allowed" if approver_present else "approver_required",
                {"approver": inputs.approver or "", "allowed_approvers": ",".join(approval_roles)},
            )
        ),
        "separation_of_duties": (
            ("pass", "", {"requester": inputs.requester, "approver": inputs.approver or ""})
            if separated
            else (
                "blocked" if approver_present else "unknown",
                "requester_approver_conflict" if approver_present else "approver_required",
                {"requester": inputs.requester, "approver": inputs.approver or ""},
            )
        ),
    }


def owner_evidence(inputs: PromotionPolicyInput) -> dict[str, str | int | float | bool | None]:
    context = inputs.org_context
    return {
        "team_id": context.team_id if context else "",
        "ownership_status": context.ownership_status if context else "unknown",
        "missing_owners": ",".join(context.missing_owners) if context else "org_context_missing",
    }


def check_value(
    passed: bool,
    reason_code: str,
    evidence: dict[str, str | int | float | bool | None],
) -> tuple[State, str, dict[str, str | int | float | bool | None]]:
    return ("pass" if passed else "blocked", "" if passed else reason_code, evidence)


def state_check(
    status: State,
    reason_code: str,
) -> tuple[State, str, dict[str, str | int | float | bool | None]]:
    passed = status in {"pass", "done"}
    return check_value(passed, reason_code, {"status": status})


def promotion_input_from_cycle(
    cycle: CycleRun,
    request: PromotionPolicyRequest | None = None,
) -> PromotionPolicyInput:
    target_environment = request.target_environment if request else default_environment()
    target_namespace = request.target_namespace if request else os.getenv(
        "EVM_TARGET_NAMESPACE",
        DEFAULT_NAMESPACES[target_environment],
    )
    requester = request.requester if request else os.getenv(
        "EVM_PROMOTION_REQUESTER",
        cycle.tenant.model_owner if cycle.tenant and cycle.tenant.model_owner else "ml-platform",
    )
    approver = request.approver if request else os.getenv("EVM_PROMOTION_APPROVER") or None
    readiness = cycle.readiness_evaluation
    cdct = cycle.cdct_gate
    model_artifact = readiness_check(cycle, "model_artifact")
    kubernetes_runtime = readiness_check(cycle, "kubernetes_runtime")
    rollback = readiness_check(cycle, "rollback_reference")
    return PromotionPolicyInput(
        org_context=cycle.tenant,
        target_environment=target_environment,
        target_namespace=target_namespace,
        requester=requester,
        approver=approver,
        readiness_decision=readiness.decision if readiness else "blocked",
        ci_status=cdct.ci_status if cdct else "unknown",
        cd_status=cdct.cd_status if cdct else "unknown",
        ct_status=cdct.ct_status if cdct else "unknown",
        model_digest=str(model_artifact.get("actual_sha256") or ""),
        image_digest=str(kubernetes_runtime.get("serving_image_digest") or ""),
        rollback_reference=rollback.get("evidence_uri") or None,
        rollback_ready=bool(rollback.get("status") == "pass"),
        candidate_id=readiness.candidate_id if readiness else "",
        dataset_version=cycle.dataset.version,
        release_ref=cycle.environment.release_ref if cycle.environment else None,
    )


def readiness_check(cycle: CycleRun, check_id: str) -> dict[str, Any]:
    evaluation = cycle.readiness_evaluation
    if not evaluation:
        return {}
    check = next((item for item in evaluation.checks if item.check_id == check_id), None)
    if check is None:
        return {}
    return {
        **check.observed,
        "status": check.status,
        "evidence_uri": check.evidence_uri,
        "evidence_digest": check.evidence_digest,
    }


def evaluate_cycle_promotion(
    cycle: CycleRun,
    request: PromotionPolicyRequest | None = None,
    *,
    policy_path: str | Path = DEFAULT_POLICY_PATH,
    evidence_root: str | Path | None = None,
    persist: bool = False,
) -> PromotionPolicyDecision:
    return evaluate_promotion_policy(
        promotion_input_from_cycle(cycle, request),
        policy_path=policy_path,
        evidence_root=evidence_root,
        persist=persist,
    )


def default_environment() -> EnvironmentTier:
    value = os.getenv("EVM_TARGET_ENVIRONMENT", "staging")
    return PromotionPolicyRequest.model_validate(
        {
            "target_environment": value,
            "target_namespace": DEFAULT_NAMESPACES.get(value, "invalid"),
            "requester": "policy-default",
        }
    ).target_environment


def persist_decision(
    decision: PromotionPolicyDecision,
    stable_inputs: dict[str, Any],
    policy_config: dict[str, Any],
    evidence_root: str | Path | None,
) -> PromotionPolicyDecision:
    configured_root = (
        os.getenv("EVM_PROMOTION_POLICY_EVIDENCE_ROOT")
        or evidence_root
        or policy_config.get("evidence_root")
    )
    if not configured_root:
        return decision.model_copy(
            update={
                "decision": "blocked",
                "status": "blocked",
                "reason_codes": sorted(
                    set(decision.reason_codes + ["audit_evidence_root_missing"])
                ),
            }
        )
    root = runtime_path(str(configured_root))
    decision_path = root / decision.decision_id / "policy_decision.json"
    decision_with_uri = decision.model_copy(
        update={"audit_uri": canonical_evidence_uri(decision_path)}
    )
    payload = {
        "schema_version": "evm.w7.promotion_policy_audit.v1",
        "decision": decision_with_uri.model_dump(mode="json"),
        "evaluated_inputs": stable_inputs,
    }
    try:
        atomic_write_json(decision_path, payload)
        atomic_write_json(root / "latest_promotion_policy.json", payload)
    except OSError:
        return decision.model_copy(
            update={
                "decision": "blocked",
                "status": "blocked",
                "audit_uri": None,
                "reason_codes": sorted(set(decision.reason_codes + ["audit_persistence_failed"])),
            }
        )
    return decision_with_uri


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate an enterprise environment promotion policy."
    )
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--evidence-root")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--require-allow", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inputs = PromotionPolicyInput.model_validate(
        json.loads(args.fixture.read_text(encoding="utf-8"))
    )
    decision = evaluate_promotion_policy(
        inputs,
        policy_path=args.policy,
        evidence_root=args.evidence_root,
        persist=args.persist,
    )
    rendered = json.dumps(decision.model_dump(mode="json"), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 2 if args.require_allow and decision.decision != "allow" else 0


if __name__ == "__main__":
    raise SystemExit(main())
