from __future__ import annotations

from fastapi import APIRouter, HTTPException

from evm.control_panel.host_runtime import HostRuntimeSupervisorHealth, read_host_runtime_supervisor
from evm.control_panel.lifecycle_quality_guard import (
    LifecycleQualityReviewActionRequest,
    LifecycleQualityReviewRegistration,
)
from evm.control_panel.lifecycle_release_guard import LifecycleReleaseGuardRegistration
from evm.control_panel.lifecycle_runs import (
    LifecycleActionRequest,
    LifecycleApprovalRequest,
    LifecycleRun,
    LifecycleRunError,
    LifecycleRunList,
    LifecycleRunRequest,
    LifecycleWorkerState,
    approve_lifecycle_run,
    apply_lifecycle_quality_review_action,
    cancel_lifecycle_run,
    continue_lifecycle_run,
    create_lifecycle_run,
    get_lifecycle_run,
    queue_lifecycle_run,
    read_runs,
    read_worker_state,
    register_lifecycle_quality_review,
    register_lifecycle_release_guard,
    retry_lifecycle_run,
)
from evm.control_panel.stage_handoffs import StageHandoffCatalog, build_stage_handoff_catalog
from evm.control_panel.transactional_store import (
    ControlPlanePoolTimeout,
    ControlPlaneStoreError,
    ControlPlaneTransactionTimeout,
)


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel-lifecycle"])


@router.get("/lifecycle-runs", response_model=LifecycleRunList)
def list_lifecycle_runs() -> LifecycleRunList:
    try:
        return read_runs()
    except ControlPlaneStoreError as exc:
        raise lifecycle_store_http(exc) from exc


@router.get("/lifecycle-runs/worker", response_model=LifecycleWorkerState)
def lifecycle_worker_health() -> LifecycleWorkerState:
    return read_worker_state()


@router.get("/runtime-supervisor", response_model=HostRuntimeSupervisorHealth)
def runtime_supervisor_health() -> HostRuntimeSupervisorHealth:
    return read_host_runtime_supervisor()


@router.get("/stage-handoffs", response_model=StageHandoffCatalog)
def list_stage_handoffs(run_id: str | None = None, limit: int = 250) -> StageHandoffCatalog:
    return build_stage_handoff_catalog(run_id=run_id, limit=limit)


@router.get("/lifecycle-runs/{run_id}", response_model=LifecycleRun)
def read_lifecycle_run(run_id: str) -> LifecycleRun:
    try:
        run = get_lifecycle_run(run_id)
    except ControlPlaneStoreError as exc:
        raise lifecycle_store_http(exc) from exc
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "lifecycle_run_not_found", "run_id": run_id},
        )
    return run


@router.post("/lifecycle-runs", response_model=LifecycleRun, status_code=202)
def create_run(request: LifecycleRunRequest) -> LifecycleRun:
    return lifecycle_operation(lambda: create_lifecycle_run(request))


@router.post("/lifecycle-runs/{run_id}/queue", response_model=LifecycleRun, status_code=202)
def queue_run(run_id: str, request: LifecycleActionRequest) -> LifecycleRun:
    return lifecycle_operation(lambda: queue_lifecycle_run(run_id, request))


@router.post("/lifecycle-runs/{run_id}/cancel", response_model=LifecycleRun, status_code=202)
def cancel_run(run_id: str, request: LifecycleActionRequest) -> LifecycleRun:
    return lifecycle_operation(lambda: cancel_lifecycle_run(run_id, request))


@router.post("/lifecycle-runs/{run_id}/continue", response_model=LifecycleRun, status_code=202)
def continue_run(run_id: str, request: LifecycleActionRequest) -> LifecycleRun:
    return lifecycle_operation(lambda: continue_lifecycle_run(run_id, request))


@router.post("/lifecycle-runs/{run_id}/retry", response_model=LifecycleRun, status_code=202)
def retry_run(run_id: str, request: LifecycleActionRequest) -> LifecycleRun:
    return lifecycle_operation(lambda: retry_lifecycle_run(run_id, request))


@router.post(
    "/lifecycle-runs/{run_id}/quality-review",
    response_model=LifecycleRun,
    status_code=202,
)
def register_quality_review(
    run_id: str,
    request: LifecycleQualityReviewRegistration,
) -> LifecycleRun:
    return lifecycle_operation(
        lambda: register_lifecycle_quality_review(run_id, request)
    )


@router.post(
    "/lifecycle-runs/{run_id}/quality-review/action",
    response_model=LifecycleRun,
    status_code=202,
)
def quality_review_action(
    run_id: str,
    request: LifecycleQualityReviewActionRequest,
) -> LifecycleRun:
    return lifecycle_operation(
        lambda: apply_lifecycle_quality_review_action(run_id, request)
    )


@router.post(
    "/lifecycle-runs/{run_id}/release-guard",
    response_model=LifecycleRun,
    status_code=202,
)
def register_release_guard(
    run_id: str,
    request: LifecycleReleaseGuardRegistration,
) -> LifecycleRun:
    return lifecycle_operation(
        lambda: register_lifecycle_release_guard(run_id, request)
    )


@router.post("/lifecycle-runs/{run_id}/approve", response_model=LifecycleRun, status_code=202)
def approve_run(run_id: str, request: LifecycleApprovalRequest) -> LifecycleRun:
    return lifecycle_operation(lambda: approve_lifecycle_run(run_id, request))


def lifecycle_operation(operation) -> LifecycleRun:
    try:
        return operation()
    except LifecycleRunError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc


def lifecycle_store_http(exc: ControlPlaneStoreError) -> HTTPException:
    return HTTPException(
        status_code=(
            503
            if isinstance(exc, (ControlPlanePoolTimeout, ControlPlaneTransactionTimeout))
            else 409
        ),
        detail={"error": type(exc).__name__, "message": str(exc)},
    )
