from __future__ import annotations

from fastapi import APIRouter, HTTPException

from evm.control_panel.operations import (
    TaskDispatchError,
    confirm_task_assignment,
    create_task_assignment,
    default_task_request,
    dispatch_task_assignment,
    read_tasks,
    sync_running_tasks,
)
from evm.control_panel.schemas import (
    TaskAssignment,
    TaskAssignmentList,
    TaskAssignmentRequest,
    TaskTransitionRequest,
)
from evm.control_panel.transactional_store import (
    ControlPlaneAdmissionRejected,
    ControlPlaneItemTooLarge,
    ControlPlanePoolTimeout,
    ControlPlaneStoreError,
    ControlPlaneTransactionTimeout,
)


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel-tasks"])


@router.get("/tasks", response_model=TaskAssignmentList)
def list_task_assignments(refresh_runtime: bool = True) -> TaskAssignmentList:
    try:
        return sync_running_tasks() if refresh_runtime else read_tasks()
    except ControlPlaneStoreError as exc:
        raise store_http(exc) from exc


@router.get("/tasks/default", response_model=TaskAssignmentRequest)
def default_task_assignment() -> TaskAssignmentRequest:
    return default_task_request()


@router.post(
    "/tasks",
    response_model=TaskAssignment,
    status_code=202,
    responses={
        413: {"description": "Canonical task payload exceeds the per-item byte limit."},
        429: {
            "description": "Durable queue depth or aggregate byte capacity is exhausted.",
            "headers": {
                "Retry-After": {
                    "description": "Integer seconds before bounded admission should be retried.",
                    "schema": {"type": "integer"},
                }
            },
        },
    },
)
def create_task(request: TaskAssignmentRequest) -> TaskAssignment:
    try:
        return create_task_assignment(request)
    except (TaskDispatchError, ControlPlaneStoreError) as exc:
        raise task_http(exc) from exc


@router.post("/tasks/{task_id}/dispatch", response_model=TaskAssignment, status_code=202)
def dispatch_task(task_id: str) -> TaskAssignment:
    try:
        task = dispatch_task_assignment(task_id)
    except (TaskDispatchError, ControlPlaneStoreError) as exc:
        raise task_http(exc, task_id=task_id) from exc
    if task is None:
        raise HTTPException(status_code=404, detail={"error": "task_not_found", "task_id": task_id})
    return task


@router.post("/tasks/{task_id}/confirm", response_model=TaskAssignment, status_code=202)
def confirm_task(task_id: str, request: TaskTransitionRequest) -> TaskAssignment:
    try:
        task = confirm_task_assignment(task_id, request)
    except (TaskDispatchError, ControlPlaneStoreError) as exc:
        raise task_http(exc, task_id=task_id) from exc
    if task is None:
        raise HTTPException(status_code=404, detail={"error": "task_not_found", "task_id": task_id})
    return task


def task_http(
    exc: TaskDispatchError | ControlPlaneStoreError,
    *,
    task_id: str | None = None,
) -> HTTPException:
    if isinstance(exc, TaskDispatchError):
        status_code = exc.status_code
        code = exc.code
    else:
        if isinstance(exc, ControlPlaneItemTooLarge):
            status_code = 413
        elif isinstance(exc, ControlPlaneAdmissionRejected):
            status_code = 429
        else:
            status_code = (
                503
                if isinstance(exc, (ControlPlanePoolTimeout, ControlPlaneTransactionTimeout))
                else 409
            )
        code = type(exc).__name__
    details = {"error": code, "message": str(exc), "task_id": task_id}
    headers = None
    if isinstance(exc, ControlPlaneItemTooLarge):
        details.update(
            {
                "payload_bytes": exc.payload_bytes,
                "max_item_bytes": exc.max_item_bytes,
            }
        )
    elif isinstance(exc, ControlPlaneAdmissionRejected):
        details.update(
            {
                "reason": exc.reason,
                "current_depth": exc.current_depth,
                "current_bytes": exc.current_bytes,
            }
        )
        headers = {"Retry-After": str(exc.retry_after_seconds)}
    return HTTPException(
        status_code=status_code,
        detail=details,
        headers=headers,
    )


def store_http(exc: ControlPlaneStoreError) -> HTTPException:
    return task_http(exc)
