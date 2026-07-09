from __future__ import annotations

from evm.control_panel.environment import build_environment_ref, environment_scope_status
from evm.control_panel.org_context import build_default_org_context, owner_status_fields, promotion_claim_allowed
from evm.control_panel.readiness import (
    build_data_readiness,
    build_experiment_readiness,
    data_readiness_blockers,
    experiment_readiness_blockers,
)
from evm.control_panel.schemas import OrgContext


def test_default_org_context_closes_required_owner_coverage():
    context = build_default_org_context()

    assert context.service_scope == "internal-department"
    assert context.ownership_status == "pass"
    assert context.missing_owners == []
    assert promotion_claim_allowed(context) is True


def test_missing_owner_blocks_promotion_claim():
    context = OrgContext(
        team_id="mvi-platform",
        department="ai-infra",
        service_scope="external-production",
        data_owner="data-platform",
        model_owner=None,
        ops_owner="ai-infra-sre",
    )

    assert owner_status_fields(context) == {
        "ownership_status": "blocked",
        "missing_owners": ["model_owner"],
    }
    assert promotion_claim_allowed(context) is False


def test_environment_scope_requires_owner_coverage_and_clear_promotion_gate():
    context = build_default_org_context()
    clean_environment = build_environment_ref([])
    blocked_environment = build_environment_ref(["accuracy<0.7"])

    assert environment_scope_status(clean_environment, context) == "pass"
    assert environment_scope_status(blocked_environment, context) == "blocked"


def test_data_readiness_blockers_cover_contract_quality_lineage_and_replay():
    blockers = data_readiness_blockers(
        contract_status="blocked",
        quality_status="warn",
        lineage_status="unknown",
        replay_ready=False,
    )

    assert blockers == [
        "source_policy_or_contract_missing",
        "quality_report_not_passing",
        "lineage_evidence_missing",
        "replay_or_backfill_not_ready",
    ]


def test_readiness_builders_expose_owner_approval_and_evidence(tmp_path):
    contract_path = tmp_path / "data_contract.toml"
    contract_path.write_text("[contract]\nname='mvi'\n", encoding="utf-8")
    context = build_default_org_context()

    data_readiness = build_data_readiness(
        contract_path=contract_path,
        quality_status="pass",
        lineage_exists=True,
        replay_ready=True,
        source_policy_uri="domain_packs/manufacturing_visual_inspection/data_contract.toml",
        quality_report_uri="F:/EnterpriseMLOps_Data/quality.json",
        lineage_uri="F:/EnterpriseMLOps_Data/lineage.json",
        backfill_window="manual-local",
        org_context=context,
    )
    experiment_readiness = build_experiment_readiness(
        registry_exists=True,
        blockers=["accuracy<0.7"],
        experiment_uri="http://localhost:5000",
        model_card_uri="F:/EnterpriseMLOps_Data/model-card.json",
        evaluation_report_uri="docs/reviews/w5.md",
        org_context=context,
    )

    assert data_readiness.owner_approval_status == "pass"
    assert data_readiness.owner_approval_actor == "data-platform"
    assert data_readiness.blockers == []
    assert experiment_readiness.rollback_ready is True
    assert experiment_readiness.owner_approval_actor == "ml-platform"
    assert experiment_readiness.owner_approval_status == "blocked"
    assert "accuracy<0.7" in experiment_readiness.blockers


def test_experiment_readiness_blockers_deduplicate_gate_failures():
    blockers = experiment_readiness_blockers(
        tracking_status="blocked",
        evaluation_status="blocked",
        registry_status="blocked",
        promotion_ready=False,
        extra_blockers=["accuracy<0.7", "accuracy<0.7"],
    )

    assert blockers == [
        "accuracy<0.7",
        "evaluation_not_passing",
        "mlflow_tracking_missing",
        "owner_or_gate_promotion_blocked",
        "registry_artifact_missing",
    ]
