from __future__ import annotations

import json
import os
import time
from base64 import b64encode
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from evm.control_panel.aggregation import build_latest_cycle
from evm.control_panel.promotion_policy import PromotionPolicyDenied, evaluate_cycle_promotion
from evm.control_panel.schemas import (
    AuditEvent,
    CDCTGate,
    CommandIntent,
    CommandIntentList,
    CommandIntentRequest,
    CommandStatus,
    PromotionPolicyDecision,
    PromotionPolicyRequest,
    TaskAssignment,
    TaskAssignmentList,
    TaskAssignmentRequest,
    TaskStatus,
    TaskTransitionRequest,
)


DEFAULT_LEDGER_ROOT = "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/operations"
PROMOTION_ACTIONS = {"approve_environment_promotion", "promote_model"}
_LEDGER_LOCK = RLock()


class TaskDispatchError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class AirflowTaskProgress:
    completed: int
    running: int
    failed: int
    total: int
    fraction: float
    active_task_ids: tuple[str, ...]


def ledger_transaction(function):
    @wraps(function)
    def synchronized(*args, **kwargs):
        with _LEDGER_LOCK:
            with ledger_file_lock():
                return function(*args, **kwargs)

    return synchronized


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ledger_root() -> Path:
    return Path(os.getenv("EVM_CONTROL_PANEL_LEDGER_ROOT", DEFAULT_LEDGER_ROOT))


def task_ledger_path() -> Path:
    return ledger_root() / "task_assignments.json"


def command_ledger_path() -> Path:
    return ledger_root() / "command_intents.json"


@contextmanager
def ledger_file_lock(timeout_seconds: float = 30.0):
    root = ledger_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".operations.lock"
    deadline = time.monotonic() + timeout_seconds
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"{os.getpid()} {utc_now()}".encode("utf-8"))
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > 300:
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TaskDispatchError(
                    "operations_ledger_lock_timeout",
                    "Timed out waiting for the cross-process operations ledger lock.",
                    status_code=503,
                )
            time.sleep(0.05)
    try:
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        path.unlink(missing_ok=True)


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
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


@ledger_transaction
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
        failure_reason=(
            "task_dispatcher_not_available"
            if status == "blocked" and request.task_type != "airflow_dag_run"
            else None
        ),
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


@ledger_transaction
def confirm_task_assignment(
    task_id: str,
    request: TaskTransitionRequest,
) -> TaskAssignment | None:
    tasks = read_tasks()
    for index, task in enumerate(tasks.tasks):
        if task.task_id != task_id:
            continue
        if task.status != "pending_confirmation":
            raise TaskDispatchError(
                "task_not_pending_confirmation",
                f"Task {task_id} is {task.status}; confirmation requires pending_confirmation.",
            )
        if task.approval_policy in {"two_person", "change_ticket"}:
            raise TaskDispatchError(
                "task_external_approval_required",
                f"Task {task_id} requires an external {task.approval_policy} approval record.",
            )
        task.status = "queued"
        task.queued_at = utc_now()
        task.audit.append(
            audit(
                request.actor,
                "task_assignment_confirmed",
                reason=request.reason,
                approval_policy=task.approval_policy,
            )
        )
        tasks.tasks[index] = task
        write_tasks(tasks)
        return task
    return None


@ledger_transaction
def dispatch_task_assignment(task_id: str) -> TaskAssignment | None:
    tasks = read_tasks()
    for index, task in enumerate(tasks.tasks):
        if task.task_id != task_id:
            continue
        if task.task_type != "airflow_dag_run":
            raise TaskDispatchError(
                "task_dispatcher_not_available",
                f"No runtime dispatcher is registered for {task.task_type}.",
            )
        if task.status != "queued":
            raise TaskDispatchError(
                "task_not_queued",
                f"Task {task_id} must be queued before dispatch; current status is {task.status}.",
            )

        dag_id = str(task.config_payload.get("dag_id") or (task.airflow.dag_id if task.airflow else ""))
        if not dag_id:
            raise TaskDispatchError("airflow_dag_id_missing", "Airflow DAG ID is required.")
        dag_run_id = f"cp__{task.task_id.replace('task-', '')}"
        payload = {
            "dag_run_id": dag_run_id,
            "conf": {
                **task.config_payload,
                "control_panel_task_id": task.task_id,
                "cycle_id": task.cycle_id,
            },
        }
        try:
            response = airflow_api_request(
                f"/dags/{quote(dag_id, safe='')}/dagRuns",
                method="POST",
                payload=payload,
            )
        except TaskDispatchError as exc:
            task.status = "failed"
            task.finished_at = utc_now()
            task.failure_reason = exc.code
            task.audit.append(
                audit(task.owner, "task_dispatch_failed", error_code=exc.code, runtime="airflow")
            )
            tasks.tasks[index] = task
            write_tasks(tasks)
            raise

        task.status = "running"
        task.dispatched_at = utc_now()
        task.runtime_system = "airflow"
        task.runtime_id = str(response.get("dag_run_id") or dag_run_id)
        task.runtime_state = str(response.get("state") or "queued")
        task.runtime_url = (
            f"{airflow_api_root()}/dags/{quote(dag_id, safe='')}/dagRuns/"
            f"{quote(task.runtime_id, safe='')}"
        )
        task.audit.append(
            audit(
                task.owner,
                "task_dispatched",
                runtime="airflow",
                runtime_id=task.runtime_id,
                runtime_state=task.runtime_state,
            )
        )
        tasks.tasks[index] = task
        write_tasks(tasks)
        return task
    return None


@ledger_transaction
def sync_running_tasks(limit: int = 20) -> TaskAssignmentList:
    tasks = read_tasks()
    changed = False
    running = 0
    for index, task in enumerate(tasks.tasks):
        if task.status != "running" or task.runtime_system != "airflow" or not task.runtime_url:
            continue
        if running >= limit:
            break
        running += 1
        try:
            response = airflow_api_request_url(task.runtime_url)
        except TaskDispatchError:
            continue
        runtime_state = str(response.get("state") or task.runtime_state or "unknown")
        mapped_status: TaskStatus
        if runtime_state == "success":
            mapped_status = "done"
        elif runtime_state in {"failed", "upstream_failed"}:
            mapped_status = "failed"
        else:
            mapped_status = "running"
        if mapped_status == task.status and runtime_state == task.runtime_state:
            continue
        previous = task.runtime_state
        task.runtime_state = runtime_state
        task.status = mapped_status
        if mapped_status in {"done", "failed"}:
            task.finished_at = utc_now()
        if mapped_status == "failed":
            task.failure_reason = runtime_state
        task.audit.append(
            audit(
                "airflow-runtime-sync",
                "task_runtime_state_changed",
                previous_state=previous,
                runtime_state=runtime_state,
                status=mapped_status,
            )
        )
        tasks.tasks[index] = task
        changed = True
    if changed:
        write_tasks(tasks)
    return tasks


@ledger_transaction
def update_task_runtime(
    task_id: str,
    *,
    actor: str,
    event: str,
    status: TaskStatus,
    runtime_system: str | None = None,
    runtime_id: str | None = None,
    runtime_url: str | None = None,
    runtime_state: str | None = None,
    runtime_evidence_uri: str | None = None,
    failure_reason: str | None = None,
) -> TaskAssignment | None:
    tasks = read_tasks()
    for index, task in enumerate(tasks.tasks):
        if task.task_id != task_id:
            continue
        task.status = status
        task.runtime_system = runtime_system or task.runtime_system
        task.runtime_id = runtime_id or task.runtime_id
        task.runtime_url = runtime_url or task.runtime_url
        task.runtime_state = runtime_state or task.runtime_state
        task.runtime_evidence_uri = runtime_evidence_uri or task.runtime_evidence_uri
        task.failure_reason = failure_reason
        if status == "running" and not task.dispatched_at:
            task.dispatched_at = utc_now()
        if status in {"done", "failed", "cancelled"}:
            task.finished_at = utc_now()
        task.audit.append(
            audit(
                actor,
                event,
                status=status,
                runtime=task.runtime_system,
                runtime_state=task.runtime_state,
                failure_reason=failure_reason,
            )
        )
        tasks.tasks[index] = task
        write_tasks(tasks)
        return task
    return None


def airflow_api_root() -> str:
    return os.getenv(
        "EVM_AIRFLOW_API_URL",
        "http://127.0.0.1:8080/api/v1",
    ).rstrip("/")


def airflow_api_request(path: str, *, method: str = "GET", payload: dict[str, object] | None = None) -> dict[str, object]:
    return airflow_api_request_url(f"{airflow_api_root()}{path}", method=method, payload=payload)


def airflow_api_request_url(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    username = os.getenv("EVM_AIRFLOW_API_USERNAME", os.getenv("AIRFLOW_ADMIN_USERNAME", "admin"))
    password = os.getenv("EVM_AIRFLOW_API_PASSWORD", os.getenv("AIRFLOW_ADMIN_PASSWORD", "admin"))
    token = b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Basic {token}",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise TaskDispatchError(
            "airflow_api_rejected",
            f"Airflow API returned HTTP {exc.code}.",
            status_code=502,
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise TaskDispatchError(
            "airflow_api_unavailable",
            f"Airflow API is unavailable: {exc}",
            status_code=502,
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskDispatchError(
            "airflow_api_invalid_response",
            "Airflow API returned an invalid JSON response.",
            status_code=502,
        ) from exc
    if not isinstance(parsed, dict):
        raise TaskDispatchError(
            "airflow_api_invalid_response",
            "Airflow API returned a non-object response.",
            status_code=502,
        )
    return parsed


def airflow_task_progress(task: TaskAssignment) -> AirflowTaskProgress | None:
    if task.runtime_system != "airflow" or not task.runtime_url:
        return None
    try:
        payload = airflow_api_request_url(f"{task.runtime_url.rstrip('/')}/taskInstances")
    except TaskDispatchError:
        return None
    raw_instances = payload.get("task_instances")
    if not isinstance(raw_instances, list) or not raw_instances:
        return None
    instances = [item for item in raw_instances if isinstance(item, dict)]
    if not instances:
        return None
    terminal_states = {"success", "skipped", "removed", "failed", "upstream_failed"}
    failed_states = {"failed", "upstream_failed"}
    completed = sum(1 for item in instances if item.get("state") in terminal_states)
    failed = sum(1 for item in instances if item.get("state") in failed_states)
    running = sum(1 for item in instances if item.get("state") == "running")
    active = tuple(
        sorted(
            str(item.get("task_id"))
            for item in instances
            if item.get("state") in {"running", "queued", "deferred", "up_for_retry"}
            and item.get("task_id")
        )
    )
    total = len(instances)
    return AirflowTaskProgress(
        completed=completed,
        running=running,
        failed=failed,
        total=total,
        fraction=completed / total,
        active_task_ids=active,
    )


def resolve_task_status(request: TaskAssignmentRequest) -> TaskStatus:
    if request.dry_run:
        return "dry_run"
    kubernetes_bridge = (
        request.task_type == "kubernetes_job"
        and request.config_payload.get("adapter") == "host-kubectl-bridge"
    )
    if request.task_type != "airflow_dag_run" and not kubernetes_bridge:
        return "blocked"
    if has_blocking_gate(request.cdct_gate):
        return "blocked"
    if request.approval_policy in {"manual", "two_person", "change_ticket"}:
        return "pending_confirmation"
    return "queued"


def has_blocking_gate(gate: CDCTGate | None) -> bool:
    if gate is None:
        return False
    return gate.status == "blocked" and bool(gate.failed_checks or gate.promotion_blockers)


@ledger_transaction
def create_command_intent(request: CommandIntentRequest) -> CommandIntent:
    commands = read_commands()
    created_at = utc_now()
    status: CommandStatus = "dry_run" if request.dry_run else "pending_confirmation"
    promotion_policy = command_promotion_policy(request)
    if promotion_policy and not request.dry_run and promotion_policy.decision != "allow":
        raise PromotionPolicyDenied(promotion_policy)
    command = CommandIntent(
        **request.model_dump(),
        command_id=f"cmd-{created_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid4().hex[:8]}",
        status=status,
        created_at=created_at,
        promotion_policy=promotion_policy,
        audit=[
            audit(
                request.actor,
                "command_intent_created",
                action=request.action,
                dry_run=request.dry_run,
                status=status,
                target=f"{request.target.namespace}/{request.target.kind}/{request.target.name}",
                promotion_decision=promotion_policy.decision if promotion_policy else None,
                promotion_decision_id=promotion_policy.decision_id if promotion_policy else None,
            )
        ],
    )
    commands.commands.insert(0, command)
    write_commands(commands)
    return command


def command_promotion_policy(request: CommandIntentRequest) -> PromotionPolicyDecision | None:
    if request.action not in PROMOTION_ACTIONS:
        return None
    cycle = build_latest_cycle()
    parameters = request.parameters
    default_environment = cycle.environment.tier if cycle.environment else "staging"
    target_environment = str(parameters.get("target_environment") or default_environment)
    if target_environment not in {"dev", "test", "staging", "pre-production", "production"}:
        target_environment = default_environment
    target_namespace = request.target.namespace
    requester = request.actor
    approver = cycle.promotion_policy.approver if cycle.promotion_policy else None
    if request.action == "approve_environment_promotion":
        requester = cycle.promotion_policy.requester if cycle.promotion_policy else request.actor
        approver = request.actor
    policy_request = PromotionPolicyRequest(
        target_environment=target_environment,  # type: ignore[arg-type]
        target_namespace=target_namespace,
        requester=requester,
        approver=approver,
    )
    return evaluate_cycle_promotion(cycle, policy_request, persist=True)


@ledger_transaction
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


@ledger_transaction
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
        cycle_id=cycle.cycle_id,
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
