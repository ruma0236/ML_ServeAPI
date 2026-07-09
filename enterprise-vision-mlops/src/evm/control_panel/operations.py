from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from evm.control_panel.aggregation import build_latest_cycle
from evm.control_panel.schemas import (
    AuditEvent,
    CDCTGate,
    CommandIntent,
    CommandIntentList,
    CommandIntentRequest,
    CommandStatus,
    TaskAssignment,
    TaskAssignmentList,
    TaskAssignmentRequest,
    TaskStatus,
)


DEFAULT_LEDGER_ROOT = "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/operations"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ledger_root() -> Path:
    return Path(os.getenv("EVM_CONTROL_PANEL_LEDGER_ROOT", DEFAULT_LEDGER_ROOT))


def task_ledger_path() -> Path:
    return ledger_root() / "task_assignments.json"


def command_ledger_path() -> Path:
    return ledger_root() / "command_intents.json"


def audit(actor: str, event: str, **details: str | int | float | bool | None) -> AuditEvent:
    return AuditEvent(timestamp=utc_now(), actor=actor, event=event, details=details)


def read_tasks() -> TaskAssignmentList:
    return TaskAssignmentList(tasks=[TaskAssignment.model_validate(item) for item in read_json_array(task_ledger_path())])


def write_tasks(tasks: TaskAssignmentList) -> None:
    write_json_array(task_ledger_path(), [task.model_dump(mode="json") for task in tasks.tasks])


def read_commands() -> CommandIntentList:
    return CommandIntentList(commands=[CommandIntent.model_validate(item) for item in read_json_array(command_ledger_path())])


def write_commands(commands: CommandIntentList) -> None:
    write_json_array(command_ledger_path(), [command.model_dump(mode="json") for command in commands.commands])


def read_json_array(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def write_json_array(path: Path, payload: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def create_task_assignment(request: TaskAssignmentRequest) -> TaskAssignment:
    tasks = read_tasks()
    status = resolve_task_status(request)
    created_at = utc_now()
    task = TaskAssignment(
        **request.model_dump(),
        task_id=f"task-{created_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid4().hex[:8]}",
        status=status,
        created_at=created_at,
        queued_at=created_at if status == "queued" else None,
        audit=[
            audit(
                request.owner,
                "task_assignment_created",
                task_type=request.task_type,
                dry_run=request.dry_run,
                status=status,
                approval_policy=request.approval_policy,
            )
        ],
    )
    tasks.tasks.insert(0, task)
    write_tasks(tasks)
    return task


def resolve_task_status(request: TaskAssignmentRequest) -> TaskStatus:
    if request.dry_run:
        return "dry_run"
    if has_blocking_gate(request.cdct_gate):
        return "blocked"
    if request.approval_policy in {"manual", "two_person", "change_ticket"}:
        return "pending_confirmation"
    return "queued"


def has_blocking_gate(gate: CDCTGate | None) -> bool:
    if gate is None:
        return False
    return gate.status == "blocked" and bool(gate.failed_checks or gate.promotion_blockers)


def create_command_intent(request: CommandIntentRequest) -> CommandIntent:
    commands = read_commands()
    created_at = utc_now()
    status: CommandStatus = "dry_run" if request.dry_run else "pending_confirmation"
    command = CommandIntent(
        **request.model_dump(),
        command_id=f"cmd-{created_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid4().hex[:8]}",
        status=status,
        created_at=created_at,
        audit=[
            audit(
                request.actor,
                "command_intent_created",
                action=request.action,
                dry_run=request.dry_run,
                status=status,
                target=f"{request.target.namespace}/{request.target.kind}/{request.target.name}",
            )
        ],
    )
    commands.commands.insert(0, command)
    write_commands(commands)
    return command


def confirm_command_intent(command_id: str, actor: str = "operator") -> CommandIntent | None:
    commands = read_commands()
    for index, command in enumerate(commands.commands):
        if command.command_id != command_id:
            continue
        if command.status in {"cancelled", "applied", "rolled_back"}:
            command.audit.append(audit(actor, "command_confirm_rejected", status=command.status))
        else:
            command.status = "pending_confirmation"
            command.confirmed_at = utc_now()
            command.audit.append(audit(actor, "command_confirmed", status=command.status, mutation_applied=False))
        commands.commands[index] = command
        write_commands(commands)
        return command
    return None


def cancel_command_intent(command_id: str, actor: str = "operator") -> CommandIntent | None:
    commands = read_commands()
    for index, command in enumerate(commands.commands):
        if command.command_id != command_id:
            continue
        command.status = "cancelled"
        command.audit.append(audit(actor, "command_cancelled", mutation_applied=False))
        commands.commands[index] = command
        write_commands(commands)
        return command
    return None


def default_task_request() -> TaskAssignmentRequest:
    cycle = build_latest_cycle()
    return TaskAssignmentRequest(
        task_type="airflow_dag_run",
        owner=cycle.tenant.ops_owner if cycle.tenant and cycle.tenant.ops_owner else "ai-infra-sre",
        requester_team=cycle.tenant.team_id if cycle.tenant else "mvi-platform",
        priority="normal",
        resource_profile="local-pipeline-workers",
        environment=cycle.environment,
        approval_policy="manual",
        airflow=cycle.airflow,
        mlflow=cycle.mlflow,
        cdct_gate=cycle.cdct_gate,
        dry_run=True,
        config_payload={
            "dag_id": cycle.airflow.dag_id if cycle.airflow else "enterprise_vision_mlops_daily",
            "dataset_version": cycle.dataset.version,
            "model_version": cycle.model.version,
            "stage": "efficientnet-real-test",
        },
    )
