from __future__ import annotations

import json

import pytest

from evm.control_panel import operations
from evm.control_panel.operations import TaskDispatchError
from evm.control_panel.schemas import TaskAssignmentRequest, TaskTransitionRequest


class FakeResponse:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def request(task_type: str = "airflow_dag_run") -> TaskAssignmentRequest:
    return TaskAssignmentRequest(
        cycle_id="cycle-real-1",
        task_type=task_type,
        owner="ml-platform",
        priority="normal",
        resource_profile="local-pipeline-workers",
        approval_policy="auto",
        config_payload={"dag_id": "enterprise_vision_mlops_daily"},
        dry_run=False,
    )


def test_airflow_task_dispatch_and_runtime_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path))
    responses = iter(
        [
            FakeResponse({"dag_run_id": "cp__run", "state": "queued"}),
            FakeResponse({"dag_run_id": "cp__run", "state": "success"}),
        ]
    )
    monkeypatch.setattr(operations, "urlopen", lambda *_args, **_kwargs: next(responses))

    created = operations.create_task_assignment(request())
    dispatched = operations.dispatch_task_assignment(created.task_id)
    synced = operations.sync_running_tasks()

    assert dispatched is not None
    assert dispatched.status == "running"
    assert dispatched.runtime_system == "airflow"
    assert synced.tasks[0].status == "done"
    assert synced.tasks[0].runtime_state == "success"
    assert synced.tasks[0].cycle_id == "cycle-real-1"


def test_non_airflow_task_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path))
    created = operations.create_task_assignment(request("kubernetes_job"))

    with pytest.raises(TaskDispatchError) as exc:
        operations.dispatch_task_assignment(created.task_id)

    assert exc.value.code == "task_dispatcher_not_available"


def test_manual_task_confirmation_transitions_to_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path))
    manual = request().model_copy(update={"approval_policy": "manual"})

    created = operations.create_task_assignment(manual)
    confirmed = operations.confirm_task_assignment(
        created.task_id,
        TaskTransitionRequest(actor="ml-platform", reason="operator confirmed"),
    )

    assert created.status == "pending_confirmation"
    assert confirmed is not None
    assert confirmed.status == "queued"
    assert confirmed.queued_at
    assert confirmed.audit[-1].event == "task_assignment_confirmed"


def test_external_approval_task_cannot_use_manual_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path))
    protected = request().model_copy(update={"approval_policy": "two_person"})
    created = operations.create_task_assignment(protected)

    with pytest.raises(TaskDispatchError) as exc:
        operations.confirm_task_assignment(
            created.task_id,
            TaskTransitionRequest(actor="approver", reason="attempt local approval"),
        )

    assert exc.value.code == "task_external_approval_required"
