from __future__ import annotations

from evm.control_panel.schemas import OrgContext, State


REQUIRED_OWNER_FIELDS = ("data_owner", "model_owner", "ops_owner")


def build_default_org_context() -> OrgContext:
    context = OrgContext(
        team_id="mvi-platform",
        department="ai-infra",
        product_area="manufacturing-visual-inspection",
        service_scope="internal-department",
        data_owner="data-platform",
        model_owner="ml-platform",
        ops_owner="ai-infra-sre",
    )
    return context.model_copy(update=owner_status_fields(context))


def missing_owners(context: OrgContext) -> list[str]:
    return [field for field in REQUIRED_OWNER_FIELDS if not getattr(context, field)]


def owner_status(context: OrgContext) -> State:
    return "pass" if not missing_owners(context) else "blocked"


def owner_status_fields(context: OrgContext) -> dict[str, object]:
    missing = missing_owners(context)
    return {
        "ownership_status": "pass" if not missing else "blocked",
        "missing_owners": missing,
    }


def promotion_claim_allowed(context: OrgContext) -> bool:
    return owner_status(context) == "pass"
