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
    CTDatasetSnapshot,
    CTEvaluation,
    CycleRun,
    DecisionRecord,
    DecisionRecordList,
    DriftState,
    DriftReviewWorkflow,
    PromotionPolicyRequest,
    RuntimeResourceList,
)
from evm.control_panel.pipeline_profiles import (
    HyperparameterSearchSpace,
    PipelineProfileLaunch,
    PipelineProfileList,
    PipelineProfileRecord,
    PipelineProfileValidation,
    PipelineRunProfile,
)
from evm.control_panel.experiment_runs import (
    ExperimentCancelRequest,
    ExperimentRun,
    ExperimentRunList,
    FoldResult,
    TrialResult,
)
from evm.control_panel.lifecycle_runs import (
    LifecycleActionRequest,
    LifecycleApprovalRequest,
    LifecycleRun,
    LifecycleRunList,
    LifecycleRunRequest,
    LifecycleStage,
    LifecycleWorkerState,
)
from evm.control_panel.model_candidates import (
    ModelCandidateCatalog,
    ModelCandidateRecord,
    ModelCandidateSelection,
    ModelCandidateSelectionRequest,
)
from evm.control_panel.stage_handoffs import StageHandoff, StageHandoffCatalog
from evm.control_panel.validate_cycle_run import validate_cycle_run


def schema_properties(schema: dict) -> dict:
    properties = dict(schema.get("properties") or {})
    for item in schema.get("allOf") or []:
        if isinstance(item, dict):
            properties.update(schema_properties(item))
    return properties


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
    assert "ct_snapshot" in schemas["CycleRun"]["properties"]
    assert "ct_evaluation" in schemas["CycleRun"]["properties"]
    assert set(CTDatasetSnapshot.model_fields).issubset(
        schemas["CTDatasetSnapshot"]["properties"]
    )
    assert set(CTEvaluation.model_fields).issubset(
        schemas["CTEvaluation"]["properties"]
    )
    assert "latest_deployment_intent" in schemas["CycleRun"]["properties"]
    assert "DeploymentIntent" in schemas
    assert "CIEvidenceValidation" in schemas
    assert "observation_source" in schemas["RuntimeResource"]["properties"]
    assert "observation_status" in schemas["RuntimeResourceList"]["properties"]
    assert "/control-panel/v1/deployment-intents" in openapi["paths"]
    assert "/control-panel/v1/cycles" in openapi["paths"]
    assert "/control-panel/v1/tasks/{task_id}/dispatch" in openapi["paths"]
    assert "/control-panel/v1/tasks/{task_id}/confirm" in openapi["paths"]
    assert "/control-panel/v1/orchestrators" in openapi["paths"]
    assert "/control-panel/v1/deployment-intents/{intent_id}/queue" in openapi["paths"]
    assert "/control-panel/v1/diagnostics/latest" in openapi["paths"]
    assert "/control-panel/v1/ct/snapshots" in openapi["paths"]
    assert "/control-panel/v1/ct/snapshots/latest" in openapi["paths"]
    assert "/control-panel/v1/ct/evaluations/latest" in openapi["paths"]
    assert "/control-panel/v1/drift-reviews/latest" in openapi["paths"]
    assert "/control-panel/v1/drift-reviews/{event_id}/transition" in openapi["paths"]
    assert "/control-panel/v1/decisions" in openapi["paths"]
    assert "/control-panel/v1/decisions/{decision_id}/transition" in openapi["paths"]
    assert "/control-panel/v1/pipeline-profiles/default" in openapi["paths"]
    assert "/control-panel/v1/pipeline-profiles" in openapi["paths"]
    assert "/control-panel/v1/pipeline-profiles/validate" in openapi["paths"]
    assert "/control-panel/v1/pipeline-profiles/{profile_id}" in openapi["paths"]
    assert "/control-panel/v1/pipeline-profiles/{profile_id}/launch" in openapi["paths"]
    assert "/control-panel/v1/pipeline-profiles/{profile_id}/replay-validation" in openapi["paths"]
    assert "ModelComponentCatalog" in schemas
    assert "PipelineProfileReplayValidation" in schemas
    assert "reproducibility_digest" in schemas["PipelineProfileRecord"]["properties"]
    assert "/control-panel/v1/lifecycle-runs" in openapi["paths"]
    assert "/control-panel/v1/lifecycle-runs/worker" in openapi["paths"]
    assert "/control-panel/v1/lifecycle-runs/{run_id}" in openapi["paths"]
    assert "/control-panel/v1/lifecycle-runs/{run_id}/queue" in openapi["paths"]
    assert "/control-panel/v1/lifecycle-runs/{run_id}/continue" in openapi["paths"]
    assert "/control-panel/v1/lifecycle-runs/{run_id}/retry" in openapi["paths"]
    assert "/control-panel/v1/lifecycle-runs/{run_id}/approve" in openapi["paths"]
    assert "/control-panel/v1/stage-handoffs" in openapi["paths"]
    assert "/control-panel/v1/model-candidates" in openapi["paths"]
    assert "/control-panel/v1/model-candidates/{candidate_key}/select" in openapi["paths"]
    assert "/control-panel/v1/model-selections/{selection_id}" in openapi["paths"]
    assert "/control-panel/v1/experiment-runs" in openapi["paths"]
    assert "/control-panel/v1/experiment-runs/{experiment_id}" in openapi["paths"]
    assert "/control-panel/v1/experiment-runs/{experiment_id}/cancel" in openapi["paths"]
    assert set(ControlPanelDiagnostics.model_fields).issubset(
        schemas["ControlPanelDiagnostics"]["properties"]
    )
    assert set(DriftReviewWorkflow.model_fields).issubset(
        schemas["DriftReviewWorkflow"]["properties"]
    )
    assert "DecisionRecord" in schemas
    assert "CycleRunSummary" in schemas
    assert set(schemas["CycleRunList"]["required"]) == {"cycles", "latest_cycle_id", "total"}
    assert "cycle_id" in schemas["TaskAssignmentRequest"]["properties"]
    assert "runtime_state" in schema_properties(schemas["TaskAssignment"])
    assert set(DecisionRecordList.model_fields).issubset(
        schemas["DecisionRecordList"]["properties"]
    )
    assert set(DecisionRecord.model_fields).issubset(
        {
            *schema_properties(schemas["DecisionRecordRequest"]),
            *schema_properties(schemas["DecisionRecord"]),
        }
    )
    assert set(PipelineRunProfile.model_fields).issubset(
        schemas["PipelineRunProfile"]["properties"]
    )
    assert set(PipelineProfileValidation.model_fields).issubset(
        schemas["PipelineProfileValidation"]["properties"]
    )
    assert set(PipelineProfileRecord.model_fields).issubset(
        schemas["PipelineProfileRecord"]["properties"]
    )
    assert set(PipelineProfileList.model_fields).issubset(
        schemas["PipelineProfileList"]["properties"]
    )
    assert set(PipelineProfileLaunch.model_fields).issubset(
        schemas["PipelineProfileLaunch"]["properties"]
    )
    assert set(LifecycleRun.model_fields).issubset(schemas["LifecycleRun"]["properties"])
    assert set(LifecycleRunList.model_fields).issubset(schemas["LifecycleRunList"]["properties"])
    assert set(LifecycleStage.model_fields).issubset(schemas["LifecycleStage"]["properties"])
    assert set(LifecycleWorkerState.model_fields).issubset(
        schemas["LifecycleWorkerState"]["properties"]
    )
    assert set(LifecycleRunRequest.model_fields).issubset(
        schemas["LifecycleRunRequest"]["properties"]
    )
    assert set(LifecycleActionRequest.model_fields).issubset(
        schemas["LifecycleActionRequest"]["properties"]
    )
    approval_properties = {
        *schema_properties(schemas["LifecycleActionRequest"]),
        *schema_properties(schemas["LifecycleApprovalRequest"]),
    }
    assert set(LifecycleApprovalRequest.model_fields).issubset(approval_properties)
    assert set(StageHandoff.model_fields).issubset(schemas["StageHandoff"]["properties"])
    assert set(StageHandoffCatalog.model_fields).issubset(
        schemas["StageHandoffCatalog"]["properties"]
    )
    assert set(ModelCandidateRecord.model_fields).issubset(
        schemas["ModelCandidateRecord"]["properties"]
    )
    assert set(ModelCandidateCatalog.model_fields).issubset(
        schemas["ModelCandidateCatalog"]["properties"]
    )
    assert set(ModelCandidateSelectionRequest.model_fields).issubset(
        schemas["ModelCandidateSelectionRequest"]["properties"]
    )
    assert set(ModelCandidateSelection.model_fields).issubset(
        schemas["ModelCandidateSelection"]["properties"]
    )
    assert set(HyperparameterSearchSpace.model_fields).issubset(
        schemas["HyperparameterSearchSpace"]["properties"]
    )
    assert set(FoldResult.model_fields).issubset(schemas["FoldResult"]["properties"])
    assert set(TrialResult.model_fields).issubset(
        schemas["TrialResult"]["properties"]
    )
    assert set(ExperimentRun.model_fields).issubset(schemas["ExperimentRun"]["properties"])
    assert set(ExperimentRunList.model_fields).issubset(
        schemas["ExperimentRunList"]["properties"]
    )
    assert set(ExperimentCancelRequest.model_fields).issubset(
        schemas["ExperimentCancelRequest"]["properties"]
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
