from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from apps.api.control_panel import (
    cycle_snapshot,
    evaluate_promotion_policy,
    get_cycle,
    latest_cycle,
    list_resources,
    invalidate_cycle_cache,
)
from evm.control_panel.schemas import (
    ControlPanelDiagnostics,
    CycleRun,
    DecisionRecord,
    DecisionRecordList,
    DriftState,
    DriftReviewWorkflow,
    PromotionPolicyRequest,
    RuntimeResourceList,
)
from evm.control_panel.validate_cycle_run import validate_cycle_run


def test_cycle_run_example_conforms_to_pydantic_and_openapi_component():
    payload = json.loads(open("contracts/control-panel/examples/cycle-run.json", encoding="utf-8").read())

    cycle = CycleRun.model_validate(payload)
    report = validate_cycle_run(
        payload,
        openapi_path=Path("contracts/control-panel/control-panel.openapi.json"),
    )

    assert cycle.cycle_id == payload["cycle_id"]
    assert cycle.tenant is not None
    assert cycle.tenant.ownership_status == "pass"
    assert cycle.environment is not None
    assert cycle.environment.approval_policy == "owner-gated"
    assert cycle.promotion_policy is not None
    assert cycle.promotion_policy.decision == "blocked"
    assert cycle.data_pipeline is not None
    assert cycle.data_pipeline.owner_approval_actor == "data-platform"
    assert cycle.experiment_pipeline is not None
    assert cycle.experiment_pipeline.rollback_ready is True
    assert cycle.readiness_evaluation is not None
    assert cycle.readiness_evaluation.decision == "blocked"
    assert cycle.drift is not None
    assert cycle.drift.review_queue_count == 128
    assert cycle.drift.measurement_status == "measured"
    assert cycle.drift.review_event_type == "review_required"
    assert cycle.drift.automatic_retraining is False
    assert cycle.drift.recommended_action
    assert cycle.cdct_gate is not None
    assert cycle.cdct_gate.promotion_decision == "block"
    assert cycle.cdct_gate.block_reason
    assert report["valid"] is True
    assert "cycle_id" in report["required_fields"]


def test_openapi_components_expose_enterprise_readiness_fields():
    openapi = json.loads(Path("contracts/control-panel/control-panel.openapi.json").read_text(encoding="utf-8"))
    schemas = openapi["components"]["schemas"]

    assert "ownership_status" in schemas["OrgContext"]["properties"]
    assert "promotion_blockers" in schemas["EnvironmentRef"]["properties"]
    assert "reason_codes" in schemas["PromotionPolicyDecision"]["properties"]
    assert "promotion_policy" in schemas["CycleRun"]["properties"]
    assert "owner_approval_status" in schemas["DataPipelineReadiness"]["properties"]
    assert "rollback_ready" in schemas["ExperimentPipelineReadiness"]["properties"]
    assert "blockers" in schemas["ExperimentPipelineReadiness"]["properties"]
    assert "checks" in schemas["ArtifactReadinessEvaluation"]["properties"]
    assert "evidence_digest" in schemas["ReadinessEvidenceCheck"]["properties"]
    assert "readiness_evaluation" in schemas["CycleRun"]["properties"]
    assert "review_queue_count" in schemas["DriftState"]["properties"]
    assert "recommended_action" in schemas["DriftState"]["properties"]
    assert "measurement_status" in schemas["DriftState"]["properties"]
    assert "review_event_type" in schemas["DriftState"]["properties"]
    assert "input_category_js" in schemas["DriftState"]["properties"]
    assert "confidence_psi" in schemas["DriftState"]["properties"]
    assert "triggered_rules" in schemas["DriftState"]["properties"]
    assert "automatic_retraining" in schemas["DriftState"]["properties"]
    assert "promotion_decision" in schemas["CDCTGate"]["properties"]
    assert "block_reason" in schemas["CDCTGate"]["properties"]
    assert "ci_evidence" in schemas["CycleRun"]["properties"]
    assert "latest_deployment_intent" in schemas["CycleRun"]["properties"]
    assert "DeploymentIntent" in schemas
    assert "CIEvidenceValidation" in schemas
    assert "observation_source" in schemas["RuntimeResource"]["properties"]
    assert "observation_status" in schemas["RuntimeResourceList"]["properties"]
    assert "/control-panel/v1/deployment-intents" in openapi["paths"]
    assert "/control-panel/v1/deployment-intents/{intent_id}/queue" in openapi["paths"]
    assert "/control-panel/v1/diagnostics/latest" in openapi["paths"]
    assert "/control-panel/v1/drift-reviews/latest" in openapi["paths"]
    assert "/control-panel/v1/drift-reviews/{event_id}/transition" in openapi["paths"]
    assert "/control-panel/v1/decisions" in openapi["paths"]
    assert "/control-panel/v1/decisions/{decision_id}/transition" in openapi["paths"]
    assert set(ControlPanelDiagnostics.model_fields).issubset(
        schemas["ControlPanelDiagnostics"]["properties"]
    )
    assert set(DriftReviewWorkflow.model_fields).issubset(
        schemas["DriftReviewWorkflow"]["properties"]
    )
    assert "DecisionRecord" in schemas
    assert set(DecisionRecordList.model_fields).issubset(
        schemas["DecisionRecordList"]["properties"]
    )
    assert set(DecisionRecord.model_fields).issubset(
        {
            *schemas["DecisionRecordRequest"]["properties"],
            *schemas["DecisionRecord"]["allOf"][1]["properties"],
        }
    )


def test_openapi_drift_state_covers_every_pydantic_field():
    openapi = json.loads(
        Path("contracts/control-panel/control-panel.openapi.json").read_text(encoding="utf-8")
    )
    properties = openapi["components"]["schemas"]["DriftState"]["properties"]

    assert set(DriftState.model_fields).issubset(properties)


def test_control_panel_latest_cycle_route_returns_contract_payload(monkeypatch):
    monkeypatch.delenv("MODEL_REGISTRY_PATH", raising=False)

    cycle = latest_cycle()

    cycle = CycleRun.model_validate(cycle.model_dump())
    assert cycle.owner_issue == "EVM-224"
    assert cycle.dataset.version
    assert cycle.model.registry_uri
    assert cycle.stages


def test_control_panel_resources_route_returns_runtime_resource_contract(monkeypatch):
    monkeypatch.delenv("MODEL_REGISTRY_PATH", raising=False)

    payload = list_resources()
    resources = RuntimeResourceList.model_validate(payload.model_dump()).resources
    openapi = json.loads(Path("contracts/control-panel/control-panel.openapi.json").read_text(encoding="utf-8"))

    assert resources
    assert any(resource.name == "evm-api" for resource in resources)
    assert any(resource.name == "evm-efficientnet-training" and resource.gpu_request for resource in resources)
    assert any(resource.kind == "Service" and resource.name == "evm-api" for resource in resources)
    assert any(resource.kind == "PersistentVolumeClaim" and resource.storage_root for resource in resources)
    assert all(resource.node_pool for resource in resources)
    assert "/control-panel/v1/resources" in openapi["paths"]
    assert "RuntimeResource" in openapi["components"]["schemas"]
    assert payload.observation_status in {"live", "stale", "unavailable"}


def test_control_panel_cycle_lookup_404_for_unknown_id():
    with pytest.raises(HTTPException) as exc:
        get_cycle("not-the-latest")

    assert exc.value.status_code == 404
    assert exc.value.detail["error"] == "cycle_not_found"


def test_cycle_snapshot_reuses_one_aggregation_within_short_ttl(monkeypatch):
    cycle = CycleRun.model_validate_json(
        Path("contracts/control-panel/examples/cycle-run.json").read_text(encoding="utf-8")
    )
    calls = 0

    def build():
        nonlocal calls
        calls += 1
        return cycle

    monkeypatch.setattr("apps.api.control_panel.build_latest_cycle", build)
    monkeypatch.setenv("EVM_CONTROL_PANEL_CACHE_TTL_SECONDS", "30")
    invalidate_cycle_cache()

    assert cycle_snapshot().cycle_id == cycle.cycle_id
    assert cycle_snapshot().cycle_id == cycle.cycle_id
    assert calls == 1


def test_promotion_policy_route_recomputes_target_environment_on_server(
    tmp_path, monkeypatch
):
    cycle = CycleRun.model_validate_json(
        Path("contracts/control-panel/examples/cycle-run.json").read_text(encoding="utf-8")
    )
    monkeypatch.setattr("apps.api.control_panel.build_latest_cycle", lambda: cycle)
    monkeypatch.setenv("EVM_PROMOTION_POLICY_EVIDENCE_ROOT", str(tmp_path))

    decision = evaluate_promotion_policy(
        PromotionPolicyRequest(
            target_environment="production",
            target_namespace="evm-production",
            requester="ml-platform",
            approver=None,
        )
    )

    assert decision.target_environment == "production"
    assert decision.target_namespace == "evm-production"
    assert decision.decision == "blocked"
    assert "readiness_not_ready" in decision.reason_codes
    assert decision.audit_uri
