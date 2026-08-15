from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.api.control_panel_tasks import create_task, default_task_assignment, list_task_assignments
from evm.control_panel.schemas import TaskAssignmentRequest


def test_task_assignment_dry_run_and_queue_states(tmp_path, monkeypatch):
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path))

    default_request = default_task_assignment()
    dry_run_task = create_task(default_request)

    assert dry_run_task.status == "dry_run"
    assert dry_run_task.queued_at is None
    assert dry_run_task.airflow is not None
    assert dry_run_task.airflow.mode == "external-compose"
    assert dry_run_task.audit[0].event == "task_assignment_created"

    queue_request = TaskAssignmentRequest.model_validate(
        {
            **default_request.model_dump(),
            "dry_run": False,
            "approval_policy": "auto",
            "cdct_gate": None,
        }
    )
    queued_task = create_task(queue_request)

    tasks = list_task_assignments().tasks
    assert queued_task.status == "queued"
    assert queued_task.queued_at is not None
    assert [task.task_id for task in tasks] == [queued_task.task_id, dry_run_task.task_id]


def test_task_assignment_blocks_failed_cdct_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path))
    request = default_task_assignment()
    request.dry_run = False
    request.approval_policy = "auto"

    task = create_task(request)

    assert task.status == "blocked"
    assert task.queued_at is None
    assert task.cdct_gate is not None
    assert task.cdct_gate.failed_checks


def test_task_assignment_config_payload_has_bounded_shape():
    default_request = default_task_assignment()

    with pytest.raises(ValidationError):
        TaskAssignmentRequest.model_validate(
            {
                **default_request.model_dump(),
                "config_payload": {f"field-{index}": index for index in range(65)},
            }
        )
    with pytest.raises(ValidationError):
        TaskAssignmentRequest.model_validate(
            {
                **default_request.model_dump(),
                "config_payload": {"items": ["value"] * 129},
            }
        )
