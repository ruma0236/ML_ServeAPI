from __future__ import annotations

from evm.control_panel.org_context import promotion_claim_allowed
from evm.control_panel.schemas import (
    EnvironmentRef,
    OrgContext,
    PromotionPolicyDecision,
    State,
)


def build_environment_ref(
    blockers: list[str] | None = None,
    release_ref: str = "",
    *,
    policy_decision: PromotionPolicyDecision | None = None,
) -> EnvironmentRef:
    blockers = blockers or []
    if policy_decision is not None:
        promotion_state = (
            "blocked"
            if policy_decision.decision == "blocked"
            else "approved"
            if policy_decision.decision == "allow" and policy_decision.target_environment == "production"
            else "candidate"
        )
        return EnvironmentRef(
            name=f"local-{policy_decision.target_environment}",
            tier=policy_decision.target_environment,
            promotion_state=promotion_state,
            cluster="docker-desktop",
            namespace=policy_decision.target_namespace,
            release_ref=release_ref,
            approval_policy=policy_decision.approval_policy,
            promotion_blockers=policy_decision.reason_codes,
        )
    return EnvironmentRef(
        name="local-shadow",
        tier="staging",
        promotion_state="blocked" if blockers else "candidate",
        cluster="docker-desktop",
        namespace="evm-platform",
        release_ref=release_ref,
        approval_policy="manual-owner-approval",
        promotion_blockers=blockers,
    )


def environment_scope_status(environment: EnvironmentRef, org_context: OrgContext | None) -> State:
    if org_context is None or not promotion_claim_allowed(org_context):
        return "blocked"
    if environment.promotion_state in {"blocked", "rolled_back"}:
        return "blocked"
    if environment.promotion_state in {"approved", "deployed", "candidate"}:
        return "pass"
    return "unknown"
