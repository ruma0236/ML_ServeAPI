from __future__ import annotations

from fastapi import APIRouter, HTTPException

from evm.control_panel.scenario_workloads import (
    GpuLease,
    ScenarioWorkloadError,
    ScenarioWorkloadRun,
    ScenarioWorkloadRunList,
    get_workload_run,
    list_workload_runs,
    read_active_gpu_lease,
)


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel-workloads"])


@router.get("/scenario-workloads", response_model=ScenarioWorkloadRunList)
def scenario_workload_runs(limit: int = 100) -> ScenarioWorkloadRunList:
    return list_workload_runs(limit=limit)


@router.get("/scenario-workloads/gpu-lease", response_model=GpuLease | None)
def scenario_gpu_lease() -> GpuLease | None:
    return read_active_gpu_lease()


@router.get("/scenario-workloads/{run_id}", response_model=ScenarioWorkloadRun)
def scenario_workload_run(run_id: str) -> ScenarioWorkloadRun:
    try:
        return get_workload_run(run_id)
    except ScenarioWorkloadError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc
