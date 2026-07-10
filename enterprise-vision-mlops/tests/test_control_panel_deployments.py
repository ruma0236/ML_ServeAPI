from __future__ import annotations

import pytest
from fastapi import HTTPException

from apps.api import control_panel_deployments
from apps.api.main import app
from evm.control_panel.deployment_intents import (
    DeploymentIntentBlocked,
    DeploymentVersionConflict,
)
from evm.control_panel.schemas import (
    DeploymentIntentRequest,
    DeploymentTransitionRequest,
    ResourceRef,
)


def request() -> DeploymentIntentRequest:
    return DeploymentIntentRequest(
        target_environment="staging",
        target_namespace="evm-staging",
        target=ResourceRef(
            namespace="evm-staging", kind="Deployment", name="evm-b7-serving"
        ),
        actor="ml-platform",
        reason="EVM-235 API boundary test",
        dry_run=True,
    )


def test_api_exposes_guarded_transitions_but_no_direct_apply_route():
    paths = app.openapi()["paths"]

    assert "/control-panel/v1/deployment-intents" in paths
    assert "/control-panel/v1/deployment-intents/{intent_id}/request-approval" in paths
    assert "/control-panel/v1/deployment-intents/{intent_id}/approve" in paths
    assert "/control-panel/v1/deployment-intents/{intent_id}/queue" in paths
    assert "/control-panel/v1/deployment-intents/{intent_id}/apply" not in paths


def test_create_route_preserves_server_admission_blockers(monkeypatch):
    def blocked(_request):
        raise DeploymentIntentBlocked(["ci_evidence_missing", "artifact_readiness_not_ready"])

    monkeypatch.setattr(control_panel_deployments, "create_deployment_intent", blocked)

    with pytest.raises(HTTPException) as exc:
        control_panel_deployments.create_intent(request())

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "deployment_intent_blocked"
    assert exc.value.detail["blockers"] == [
        "artifact_readiness_not_ready",
        "ci_evidence_missing",
    ]


def test_transition_route_maps_optimistic_lock_conflict(monkeypatch):
    def conflict(_intent_id, _request):
        raise DeploymentVersionConflict("expected version 1, current 2")

    monkeypatch.setattr(control_panel_deployments, "queue_intent", conflict)

    with pytest.raises(HTTPException) as exc:
        control_panel_deployments.queue_deployment_intent(
            "deploy-1",
            DeploymentTransitionRequest(
                actor="ai-infra-sre", reason="queue", expected_version=1
            ),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "deployment_intent_version_conflict"
