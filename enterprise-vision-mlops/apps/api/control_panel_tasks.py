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


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel-tasks"])


@router.get("/tasks", response_model=TaskAssignmentList)
def list_task_assignments(refresh_runtime: bool = True) -> TaskAssignmentList:
    return sync_running_tasks() if refresh_runtime else read_tasks()


@router.get("/tasks/default", response_model=TaskAssignmentRequest)
def default_task_assignment() -> TaskAssignmentRequest:
    return default_task_request()


@router.post("/tasks", response_model=TaskAssignment, status_code=202)
def create_task(request: TaskAssignmentRequest) -> TaskAssignment:
    return create_task_assignment(request)


@router.post("/tasks/{task_id}/dispatch", response_model=TaskAssignment, status_code=202)
def dispatch_task(task_id: str) -> TaskAssignment:
    try:
        task = dispatch_task_assignment(task_id)
    except TaskDispatchError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc), "task_id": task_id},
        ) from exc
    if task is None:
        raise HTTPException(status_code=404, detail={"error": "task_not_found", "task_id": task_id})
    return task


@router.post("/tasks/{task_id}/confirm", response_model=TaskAssignment, status_code=202)
def confirm_task(task_id: str, request: TaskTransitionRequest) -> TaskAssignment:
    try:
        task = confirm_task_assignment(task_id, request)
    except TaskDispatchError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc), "task_id": task_id},
        ) from exc
    if task is None:
        raise HTTPException(status_code=404, detail={"error": "task_not_found", "task_id": task_id})
    return task
