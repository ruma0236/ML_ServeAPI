from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from evm.control_panel.experiment_runs import (
    ExperimentCancelRequest,
    ExperimentRun,
    ExperimentRunList,
    read_experiment,
    read_experiments,
    request_cancellation,
)


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel-experiments"])


@router.get("/experiment-runs", response_model=ExperimentRunList)
def list_experiment_runs(limit: int = Query(default=100, ge=1, le=500)) -> ExperimentRunList:
    return read_experiments(limit)


@router.get("/experiment-runs/{experiment_id}", response_model=ExperimentRun)
def get_experiment_run(experiment_id: str) -> ExperimentRun:
    try:
        run = read_experiment(experiment_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_experiment_id", "experiment_id": experiment_id},
        ) from exc
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "experiment_run_not_found", "experiment_id": experiment_id},
        )
    return run


@router.post(
    "/experiment-runs/{experiment_id}/cancel",
    response_model=ExperimentRun,
    status_code=202,
)
def cancel_experiment_run(
    experiment_id: str,
    request: ExperimentCancelRequest,
) -> ExperimentRun:
    try:
        run = request_cancellation(
            experiment_id,
            actor=request.actor,
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_experiment_id", "experiment_id": experiment_id},
        ) from exc
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "experiment_run_not_found", "experiment_id": experiment_id},
        )
    return run
