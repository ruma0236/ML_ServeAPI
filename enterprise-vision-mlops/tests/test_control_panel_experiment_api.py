from __future__ import annotations

from apps.api.main import app


def test_api_exposes_experiment_progress_and_cancel_routes() -> None:
    paths = app.openapi()["paths"]

    assert "/control-panel/v1/experiment-runs" in paths
    assert "/control-panel/v1/experiment-runs/{experiment_id}" in paths
    assert "/control-panel/v1/experiment-runs/{experiment_id}/cancel" in paths
