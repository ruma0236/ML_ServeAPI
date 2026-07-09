from __future__ import annotations

from evm.control_panel.org_context import promotion_claim_allowed
from evm.control_panel.schemas import EnvironmentRef, OrgContext, State


def build_environment_ref(blockers: list[str], release_ref: str = "") -> EnvironmentRef:
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
