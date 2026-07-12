from __future__ import annotations

import pytest
from fastapi import HTTPException

from apps.api import control_panel_lifecycle
from apps.api.main import app
from evm.control_panel.lifecycle_runs import LifecycleRunError, LifecycleRunRequest


def test_api_exposes_guarded_lifecycle_run_routes() -> None:
    paths = app.openapi()["paths"]

    assert "/control-panel/v1/lifecycle-runs" in paths
    assert "/control-panel/v1/lifecycle-runs/worker" in paths
    assert "/control-panel/v1/lifecycle-runs/{run_id}" in paths
    assert "/control-panel/v1/lifecycle-runs/{run_id}/queue" in paths
    assert "/control-panel/v1/lifecycle-runs/{run_id}/cancel" in paths
    assert "/control-panel/v1/lifecycle-runs/{run_id}/retry" in paths
    assert "/control-panel/v1/lifecycle-runs/{run_id}/approve" in paths
    assert "/control-panel/v1/lifecycle-runs/{run_id}/complete" not in paths
    assert "/control-panel/v1/lifecycle-runs/{run_id}/transition-stage" not in paths


def test_create_route_preserves_lifecycle_error_code(monkeypatch) -> None:
    def blocked(_request):
        raise LifecycleRunError(
            "pipeline_profile_not_executable",
            "full_lifecycle_orchestrator is unavailable",
            status_code=422,
        )

    monkeypatch.setattr(control_panel_lifecycle, "create_lifecycle_run", blocked)
    request = LifecycleRunRequest(
        profile_id="profile-1",
        actor="ml-platform",
        reason="Validate API error propagation",
        dry_run=False,
    )

    with pytest.raises(HTTPException) as exc:
        control_panel_lifecycle.create_run(request)

    assert exc.value.status_code == 422
    assert exc.value.detail == {
        "error": "pipeline_profile_not_executable",
        "message": "full_lifecycle_orchestrator is unavailable",
    }


def test_read_route_returns_explicit_not_found(monkeypatch) -> None:
    monkeypatch.setattr(control_panel_lifecycle, "get_lifecycle_run", lambda _run_id: None)

    with pytest.raises(HTTPException) as exc:
        control_panel_lifecycle.read_lifecycle_run("lifecycle-missing")

    assert exc.value.status_code == 404
    assert exc.value.detail["error"] == "lifecycle_run_not_found"
