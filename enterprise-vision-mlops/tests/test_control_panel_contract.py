from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from apps.api.control_panel import get_cycle, latest_cycle, list_resources
from evm.control_panel.schemas import CycleRun, RuntimeResourceList
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
    assert cycle.environment.approval_policy == "manual-owner-approval"
    assert cycle.data_pipeline is not None
    assert cycle.data_pipeline.owner_approval_actor == "data-platform"
    assert cycle.experiment_pipeline is not None
    assert cycle.experiment_pipeline.rollback_ready is True
    assert cycle.readiness_evaluation is not None
    assert cycle.readiness_evaluation.decision == "blocked"
    assert cycle.drift is not None
    assert cycle.drift.review_queue_count == 1
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
    assert "owner_approval_status" in schemas["DataPipelineReadiness"]["properties"]
    assert "rollback_ready" in schemas["ExperimentPipelineReadiness"]["properties"]
    assert "blockers" in schemas["ExperimentPipelineReadiness"]["properties"]
    assert "checks" in schemas["ArtifactReadinessEvaluation"]["properties"]
    assert "evidence_digest" in schemas["ReadinessEvidenceCheck"]["properties"]
    assert "readiness_evaluation" in schemas["CycleRun"]["properties"]
    assert "review_queue_count" in schemas["DriftState"]["properties"]
    assert "recommended_action" in schemas["DriftState"]["properties"]
    assert "promotion_decision" in schemas["CDCTGate"]["properties"]
    assert "block_reason" in schemas["CDCTGate"]["properties"]


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


def test_control_panel_cycle_lookup_404_for_unknown_id():
    with pytest.raises(HTTPException) as exc:
        get_cycle("not-the-latest")

    assert exc.value.status_code == 404
    assert exc.value.detail["error"] == "cycle_not_found"
