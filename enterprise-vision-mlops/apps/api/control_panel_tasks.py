from __future__ import annotations

from fastapi import APIRouter

from evm.control_panel.operations import create_task_assignment, default_task_request, read_tasks
from evm.control_panel.schemas import TaskAssignment, TaskAssignmentList, TaskAssignmentRequest


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel-tasks"])


@router.get("/tasks", response_model=TaskAssignmentList)
def list_task_assignments() -> TaskAssignmentList:
    return read_tasks()


@router.get("/tasks/default", response_model=TaskAssignmentRequest)
def default_task_assignment() -> TaskAssignmentRequest:
    return default_task_request()


@router.post("/tasks", response_model=TaskAssignment, status_code=202)
def create_task(request: TaskAssignmentRequest) -> TaskAssignment:
    return create_task_assignment(request)
