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

from evm.control_panel.admission_queue import (
    QUEUE_ADMISSIONS,
    admission_queue_mode,
    load_admission_queue_config,
    priority_value,
)
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
from evm.control_panel.transactional_store import canonical_digest, get_transactional_store
from evm.observability.otel import trace_span
from evm.observability.trace_context import (
    TraceContextError,
    W3CTraceContext,
    current_trace_context,
)


DEFAULT_LEDGER_ROOT = "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/operations"
PROMOTION_ACTIONS = {"approve_environment_promotion", "promote_model"}
_LEDGER_LOCK = RLock()


class TaskDispatchError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class AirflowTaskProgress:
    completed: int
    running: int
    failed: int
    total: int
    fraction: float
    active_task_ids: tuple[str, ...]


@dataclass(frozen=True)
class QueuedDispatchPlan:
    task: TaskAssignment
    dag_id: str
    dag_run_id: str
    run_path: str


def ledger_transaction(function):
    @wraps(function)
    def synchronized(*args, **kwargs):
        store = get_transactional_store()
        if store.enabled:
            with store.serialized("operations-ledger"):
                with _LEDGER_LOCK:
                    with ledger_file_lock():
                        return function(*args, **kwargs)
        with _LEDGER_LOCK:
            with ledger_file_lock():
                return function(*args, **kwargs)

    return synchronized


def task_ledger_transaction(function):
    """Use row-level PostgreSQL ownership for durable tasks, legacy lock otherwise."""
    legacy = ledger_transaction(function)

    @wraps(function)
    def synchronized(*args, **kwargs):
        store = get_transactional_store()
        durable = store.enabled and admission_queue_mode() == "durable"
        if (
            durable
            and function.__name__ == "create_task_assignment"
            and args
            and not (
                args[0].task_type == "airflow_dag_run"
                and resolve_task_status(args[0]) in {"queued", "pending_confirmation"}
            )
        ):
            return legacy(*args, **kwargs)
        if durable:
            return function(*args, **kwargs)
        return legacy(*args, **kwargs)

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
def ledger_file_lock(
    timeout_seconds: float = 30.0,
    *,
    filename: str = ".operations.lock",
):
    root = ledger_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / filename
    deadline = time.monotonic() + timeout_seconds
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    acquired = False
    while not acquired:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (BlockingIOError, OSError):
            if time.monotonic() >= deadline:
                handle.close()
                raise TaskDispatchError(
                    "operations_ledger_lock_timeout",
                    "Timed out waiting for the cross-process operations ledger lock.",
                    status_code=503,
                )
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def audit(actor: str, event: str, **details: str | int | float | bool | None) -> AuditEvent:
    return AuditEvent(timestamp=utc_now(), actor=actor, event=event, details=details)


def read_tasks() -> TaskAssignmentList:
    store = get_transactional_store()
    if store.enabled:
        payload = store.list_entities("task_assignment")
        return TaskAssignmentList(
            tasks=[TaskAssignment.model_validate(item) for item in payload]
        )
    return TaskAssignmentList(tasks=[TaskAssignment.model_validate(item) for item in read_json_array(task_ledger_path())])


def write_tasks(tasks: TaskAssignmentList) -> None:
    payload = [task.model_dump(mode="json") for task in tasks.tasks]
    store = get_transactional_store()
    if store.enabled:
        store.replace_task_entities(payload)
    write_json_array(task_ledger_path(), payload)


def write_task_json_mirror(tasks: TaskAssignmentList) -> None:
    payload = [task.model_dump(mode="json") for task in tasks.tasks]
    write_json_array(task_ledger_path(), payload)


def read_commands() -> CommandIntentList:
    store = get_transactional_store()
    if store.enabled:
        payload = store.read_collection("command_intents")
        if payload is not None:
            return CommandIntentList(
                commands=[CommandIntent.model_validate(item) for item in payload]
            )
    return CommandIntentList(commands=[CommandIntent.model_validate(item) for item in read_json_array(command_ledger_path())])


def write_commands(commands: CommandIntentList) -> None:
    payload = [command.model_dump(mode="json") for command in commands.commands]
    store = get_transactional_store()
    if store.enabled:
        store.write_collection("command_intents", payload)
    write_json_array(command_ledger_path(), payload)


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


@task_ledger_transaction
def create_task_assignment(request: TaskAssignmentRequest) -> TaskAssignment:
    store = get_transactional_store()
    effective_key = request.idempotency_key or f"generated-{uuid4().hex}"
    request_payload = request.model_dump(mode="json", exclude_none=True)
    if store.enabled:
        replay = store.lookup_idempotency("task.create", effective_key, request_payload)
        if replay is not None:
            if admission_queue_mode() == "durable":
                QUEUE_ADMISSIONS.labels(outcome="replayed", reason="idempotency").inc()
            return TaskAssignment.model_validate(replay)
    durable_admission = (
        admission_queue_mode() == "durable"
        and store.enabled
        and request.task_type == "airflow_dag_run"
    )
    with trace_span(
        "queue.enqueue",
        kind="producer",
        attributes={
            "evm.stage": "queue",
            "messaging.system": (
                "postgresql-durable-queue" if durable_admission else "evm-file-ledger"
            ),
            "messaging.operation.type": "create",
            "evm.task.type": request.task_type,
        },
    ) as active:
        config_payload = dict(request.config_payload)
        config_payload.update(
            {
                "trace_id": active.context.trace_id,
                "traceparent": active.context.traceparent,
                "tracestate": active.context.tracestate,
            }
        )
        traced_request = request.model_copy(
            update={
                "config_payload": config_payload,
                "idempotency_key": effective_key,
            }
        )
        if durable_admission and resolve_task_status(traced_request) == "queued":
            task = _build_task_assignment(traced_request)
            result = store.admit_task_assignment(
                scope="task.create",
                idempotency_key=effective_key,
                request_payload=request_payload,
                task_payload=task.model_dump(mode="json"),
                priority=priority_value(task.priority),
                config=load_admission_queue_config(),
            )
            task = TaskAssignment.model_validate(result.task_payload)
            QUEUE_ADMISSIONS.labels(
                outcome="replayed" if result.replayed else "accepted",
                reason="idempotency" if result.replayed else "within_bounds",
            ).inc()
        elif (
            durable_admission
            and resolve_task_status(traced_request) == "pending_confirmation"
        ):
            task = _build_task_assignment(traced_request)
            result = store.admit_pending_task_assignment(
                scope="task.create",
                idempotency_key=effective_key,
                request_payload=request_payload,
                task_payload=task.model_dump(mode="json"),
                config=load_admission_queue_config(),
            )
            task = TaskAssignment.model_validate(result.task_payload)
            QUEUE_ADMISSIONS.labels(
                outcome="replayed" if result.replayed else "accepted",
                reason="idempotency" if result.replayed else "pending_approval",
            ).inc()
        else:
            task = _create_task_assignment(traced_request)
        if store.enabled and not (
            durable_admission and task.status in {"queued", "pending_confirmation"}
        ):
            store.record_idempotency(
                "task.create",
                effective_key,
                request_payload,
                task.model_dump(mode="json"),
                entity_kind="task_assignment",
                entity_id=task.task_id,
            )
        active.set_attribute("evm.task.status", task.status)
        active.set_attribute("evm.task.id", task.task_id)
        return task


def _create_task_assignment(request: TaskAssignmentRequest) -> TaskAssignment:
    tasks = read_tasks()
    task = _build_task_assignment(request)
    tasks.tasks.insert(0, task)
    write_tasks(tasks)
    return task


def _build_task_assignment(request: TaskAssignmentRequest) -> TaskAssignment:
    status = resolve_task_status(request)
    created_at = utc_now()
    return TaskAssignment(
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


@task_ledger_transaction
def confirm_task_assignment(
    task_id: str,
    request: TaskTransitionRequest,
) -> TaskAssignment | None:
    store = get_transactional_store()
    effective_key = request.idempotency_key or f"generated-{uuid4().hex}"
    request_payload = {
        "task_id": task_id,
        **request.model_dump(mode="json", exclude_none=True),
    }
    if store.enabled:
        replay = store.lookup_idempotency("task.confirm", effective_key, request_payload)
        if replay is not None:
            if admission_queue_mode() == "durable":
                QUEUE_ADMISSIONS.labels(outcome="replayed", reason="idempotency").inc()
            return TaskAssignment.model_validate(replay)
    tasks = read_tasks()
    for index, task in enumerate(tasks.tasks):
        if task.task_id != task_id:
            continue
        if request.expected_version is not None and task.version != request.expected_version:
            raise TaskDispatchError(
                "task_version_conflict",
                f"Expected version {request.expected_version}, current version is {task.version}.",
            )
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
        durable_admission = (
            admission_queue_mode() == "durable"
            and store.enabled
            and task.task_type == "airflow_dag_run"
        )
        task.status = "queued"
        task.version += 1
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
        if durable_admission:
            result = store.admit_task_assignment(
                scope="task.confirm",
                idempotency_key=effective_key,
                request_payload=request_payload,
                task_payload=task.model_dump(mode="json"),
                priority=priority_value(task.priority),
                config=load_admission_queue_config(),
                replace_existing=True,
            )
            task = TaskAssignment.model_validate(result.task_payload)
            QUEUE_ADMISSIONS.labels(
                outcome="replayed" if result.replayed else "accepted",
                reason="idempotency" if result.replayed else "within_bounds",
            ).inc()
        else:
            write_tasks(tasks)
        if store.enabled and not durable_admission:
            store.record_idempotency(
                "task.confirm",
                effective_key,
                request_payload,
                task.model_dump(mode="json"),
                entity_kind="task_assignment",
                entity_id=task.task_id,
            )
        return task
    return None


@ledger_transaction
def dispatch_task_assignment(task_id: str) -> TaskAssignment | None:
    store = get_transactional_store()
    if store.enabled and admission_queue_mode() == "durable":
        task = next((item for item in read_tasks().tasks if item.task_id == task_id), None)
        queue_item = store.get_task_queue_item(task_id=task_id) if task is not None else None
        if queue_item is not None:
            return task
    task = next((item for item in read_tasks().tasks if item.task_id == task_id), None)
    if task is None:
        return None
    store = get_transactional_store()
    effective_key = f"task-dispatch-{task.task_id}-v{task.version}"
    request_payload = {"task_id": task.task_id, "expected_version": task.version}
    if store.enabled:
        replay = store.lookup_idempotency("task.dispatch", effective_key, request_payload)
        if replay is not None:
            return TaskAssignment.model_validate(replay)
    parent = task_trace_context(task)
    with trace_span(
        "queue.dispatch",
        parent=parent,
        kind="consumer",
        attributes={
            "evm.stage": "queue",
            "messaging.system": "evm-file-ledger",
            "messaging.operation.type": "process",
            "evm.task.id": task_id,
        },
    ) as active:
        task = _dispatch_task_assignment(task_id)
        if task is not None:
            active.set_attribute("evm.task.status", task.status)
            if store.enabled:
                store.record_idempotency(
                    "task.dispatch",
                    effective_key,
                    request_payload,
                    task.model_dump(mode="json"),
                    entity_kind="task_assignment",
                    entity_id=task.task_id,
                )
        return task


def dispatch_queued_task_assignment(
    task_id: str,
    *,
    parent: W3CTraceContext | None = None,
) -> TaskAssignment | None:
    """Dispatch a fenced durable item without holding the ledger lock across HTTP."""
    store = get_transactional_store()
    lease = store.bound_task_queue_lease()
    if lease is None or lease.task_id != task_id:
        raise TaskDispatchError(
            "task_queue_lease_required",
            "Durable dispatch requires the exact bound queue lease.",
            status_code=409,
        )
    plan = _prepare_queued_task_dispatch(task_id)
    if plan is None:
        return None
    effective_parent = parent or task_trace_context(plan.task)
    with trace_span(
        "queue.dispatch",
        parent=effective_parent,
        kind="consumer",
        attributes={
            "evm.stage": "queue",
            "messaging.system": "postgresql-durable-queue",
            "messaging.operation.type": "process",
            "evm.task.id": task_id,
        },
    ) as active:
        payload = {
            "dag_run_id": plan.dag_run_id,
            "conf": {
                **plan.task.config_payload,
                "control_panel_task_id": plan.task.task_id,
                "cycle_id": plan.task.cycle_id,
                "resource_profile": plan.task.resource_profile,
                "trace_id": active.context.trace_id,
                "traceparent": active.context.traceparent,
                "tracestate": active.context.tracestate,
            },
        }
        reservation = store.reserve_task_dispatch_effect(
            lease,
            dag_id=plan.dag_id,
            dag_run_id=plan.dag_run_id,
        )
        store.assert_task_queue_lease(lease)
        try:
            response = None
            try:
                response = airflow_api_request(plan.run_path)
            except TaskDispatchError as exc:
                if exc.code != "airflow_dag_run_not_found":
                    raise
                if reservation["state"] in {"submitted", "terminal"}:
                    raise TaskDispatchError(
                        "airflow_effect_missing_after_commit",
                        "A committed deterministic Airflow effect could not be reconciled.",
                        status_code=502,
                    ) from exc
            if response is None:
                store.mark_task_dispatch_effect_submitting(
                    lease,
                    effect_key=str(reservation["effect_key"]),
                )
                response = airflow_api_request(
                    f"/dags/{quote(plan.dag_id, safe='')}/dagRuns",
                    method="POST",
                    payload=payload,
                )
            store.assert_task_queue_lease(lease)
            task = _task_from_runtime_response(plan.task, response, plan)
            runtime_state = str(task.runtime_state or "unknown")
            terminal = task.status in {"done", "failed"}
            store.commit_task_dispatch_effect(
                lease,
                effect_key=str(reservation["effect_key"]),
                runtime_state=runtime_state,
                runtime_payload=response,
                task_payload=task.model_dump(mode="json"),
                terminal=terminal,
                succeeded=task.status == "done" if terminal else True,
            )
            active.set_attribute("evm.task.status", task.status)
            active.set_attribute("evm.task.runtime_state", runtime_state)
            return task
        except TaskDispatchError as exc:
            _record_queued_dispatch_failure(task_id, exc)
            raise


def _prepare_queued_task_dispatch(task_id: str) -> QueuedDispatchPlan | None:
    store = get_transactional_store()
    lease = store.bound_task_queue_lease()
    if lease is None or lease.task_id != task_id:
        raise TaskDispatchError(
            "task_queue_lease_required",
            "Durable dispatch preparation requires the exact bound queue lease.",
            status_code=409,
        )
    current_payload = store.get_entity("task_assignment", task_id)
    task = TaskAssignment.model_validate(current_payload or lease.task_payload)
    if task.task_type != "airflow_dag_run":
        raise TaskDispatchError(
            "task_dispatcher_not_available",
            f"No runtime dispatcher is registered for {task.task_type}.",
        )
    if task.status not in {"queued", "running", "done"}:
        raise TaskDispatchError(
            "task_not_dispatchable",
            f"Task {task_id} is {task.status}; durable dispatch requires queued or running.",
        )
    dag_id = str(
        task.config_payload.get("dag_id")
        or (task.airflow.dag_id if task.airflow else "")
    )
    if not dag_id:
        raise TaskDispatchError("airflow_dag_id_missing", "Airflow DAG ID is required.")
    dag_run_id = f"cp__{task.task_id.replace('task-', '')}"
    return QueuedDispatchPlan(
        task=task,
        dag_id=dag_id,
        dag_run_id=dag_run_id,
        run_path=f"/dags/{quote(dag_id, safe='')}/dagRuns/{quote(dag_run_id, safe='')}",
    )


def _task_from_runtime_response(
    task: TaskAssignment,
    response: dict[str, object],
    plan: QueuedDispatchPlan,
) -> TaskAssignment:
    updated = task.model_copy(deep=True)
    runtime_state = str(response.get("state") or "queued").lower()
    updated.status = (
        "done"
        if runtime_state == "success"
        else "failed"
        if runtime_state in {"failed", "upstream_failed"}
        else "running"
    )
    updated.version += 1
    updated.dispatched_at = updated.dispatched_at or utc_now()
    updated.finished_at = utc_now() if updated.status in {"done", "failed"} else None
    updated.runtime_system = "airflow"
    updated.runtime_id = str(response.get("dag_run_id") or plan.dag_run_id)
    updated.runtime_state = runtime_state
    updated.runtime_url = (
        f"{airflow_api_root()}/dags/{quote(plan.dag_id, safe='')}/dagRuns/"
        f"{quote(updated.runtime_id, safe='')}"
    )
    updated.failure_reason = runtime_state if updated.status == "failed" else None
    updated.audit.append(
        audit(
            updated.owner,
            "task_dispatched" if task.status == "queued" else "task_dispatch_reconciled",
            runtime="airflow",
            runtime_id=updated.runtime_id,
            runtime_state=runtime_state,
        )
    )
    return updated


def _record_queued_dispatch_failure(task_id: str, exc: TaskDispatchError) -> None:
    store = get_transactional_store()

    def record(payload: dict[str, object]) -> dict[str, object]:
        task = TaskAssignment.model_validate(payload)
        task.audit.append(
            audit(
                task.owner,
                "task_dispatch_attempt_failed",
                error_code=exc.code,
                runtime="airflow",
                retryable=exc.retryable,
            )
        )
        task.version += 1
        return task.model_dump(mode="json")

    try:
        store.mutate_entity(
            "task_assignment",
            task_id,
            expected_version=None,
            fallback_payload=None,
            mutate=record,
        )
    except KeyError:
        return


def sync_task_json_mirror_from_store() -> None:
    store = get_transactional_store()
    if store.enabled:
        payload = store.read_collection("task_assignments")
        if payload is None:
            store.refresh_task_mirror_from_authority()
            payload = store.read_collection("task_assignments") or []
        tasks = TaskAssignmentList(
            tasks=[TaskAssignment.model_validate(item) for item in payload]
        )
    else:
        tasks = read_tasks()
    with _LEDGER_LOCK:
        with ledger_file_lock(filename=".task-mirror.lock"):
            write_task_json_mirror(tasks)


def verify_task_json_mirror_parity() -> dict[str, object]:
    store = get_transactional_store()
    if not store.enabled:
        return {"matches": True, "mode": "file"}
    authority = sorted(
        store.list_entities("task_assignment"),
        key=lambda item: str(item.get("task_id", "")),
    )
    collection_parity = store.task_mirror_parity()
    file_payload = sorted(
        read_json_array(task_ledger_path()),
        key=lambda item: str(item.get("task_id", "")),
    )
    authority_digest = canonical_digest(authority)
    file_digest = canonical_digest(file_payload)
    return {
        **collection_parity,
        "file_count": len(file_payload),
        "file_sha256": file_digest,
        "matches": bool(collection_parity["matches"])
        and authority_digest == file_digest,
    }


def task_trace_context(task: TaskAssignment | None) -> W3CTraceContext | None:
    if task is None:
        return None
    traceparent = task.config_payload.get("traceparent")
    if not isinstance(traceparent, str) or not traceparent:
        return None
    tracestate = task.config_payload.get("tracestate")
    try:
        return W3CTraceContext.parse(
            traceparent,
            tracestate=tracestate if isinstance(tracestate, str) else None,
        )
    except TraceContextError:
        return None


def _dispatch_task_assignment(
    task_id: str,
    *,
    failure_is_terminal: bool = True,
    reconcile_existing: bool = False,
) -> TaskAssignment | None:
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
        active_context = current_trace_context()
        if active_context is not None:
            payload["conf"].update(
                {
                    "trace_id": active_context.trace_id,
                    "traceparent": active_context.traceparent,
                    "tracestate": active_context.tracestate,
                }
            )
        run_path = (
            f"/dags/{quote(dag_id, safe='')}/dagRuns/{quote(dag_run_id, safe='')}"
        )
        try:
            response = None
            if reconcile_existing:
                try:
                    response = airflow_api_request(run_path)
                except TaskDispatchError as exc:
                    if exc.code != "airflow_dag_run_not_found":
                        raise
            if response is None:
                try:
                    response = airflow_api_request(
                        f"/dags/{quote(dag_id, safe='')}/dagRuns",
                        method="POST",
                        payload=payload,
                    )
                except TaskDispatchError as exc:
                    if reconcile_existing and exc.code == "airflow_dag_run_conflict":
                        response = airflow_api_request(run_path)
                    else:
                        raise
        except TaskDispatchError as exc:
            task.version += 1
            if failure_is_terminal:
                task.status = "failed"
                task.finished_at = utc_now()
                task.failure_reason = exc.code
            task.audit.append(
                audit(
                    task.owner,
                    "task_dispatch_failed" if failure_is_terminal else "task_dispatch_attempt_failed",
                    error_code=exc.code,
                    runtime="airflow",
                    retryable=exc.retryable,
                )
            )
            tasks.tasks[index] = task
            write_tasks(tasks)
            raise

        task.status = "running"
        task.version += 1
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
    store = get_transactional_store()
    if store.enabled and admission_queue_mode() == "durable":
        return read_tasks()
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
        task.version += 1
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


def reconcile_queued_task_runtime(
    task_id: str,
    *,
    outcome_unknown_timeout_seconds: float | None = None,
) -> TaskAssignment | None:
    """Poll one submitted runtime outside the ledger lock, then close it atomically."""
    task = next((item for item in read_tasks().tasks if item.task_id == task_id), None)
    if task is None or task.status in {"done", "failed", "cancelled"}:
        return task
    store = get_transactional_store()
    queue_item = store.get_task_queue_item(task_id=task_id)
    if queue_item is None or queue_item["state"] not in {
        "runtime_pending",
        "outcome_unknown",
    }:
        raise TaskDispatchError(
            "task_runtime_queue_identity_mismatch",
            f"Task {task_id} has no matching runtime-pending queue row.",
            status_code=409,
        )
    effect = store.get_task_dispatch_effect(queue_id=str(queue_item["queue_id"]))
    if effect is None:
        raise TaskDispatchError(
            "task_runtime_effect_identity_missing",
            f"Task {task_id} has no durable Airflow effect identity.",
            status_code=409,
        )
    if task.runtime_system == "airflow" and task.runtime_url:
        runtime_url = task.runtime_url
    else:
        runtime_url = (
            f"{airflow_api_root()}/dags/{quote(str(effect['dag_id']), safe='')}/dagRuns/"
            f"{quote(str(effect['dag_run_id']), safe='')}"
        )
    try:
        response = airflow_api_request_url(runtime_url)
    except TaskDispatchError as exc:
        if (
            exc.code == "airflow_dag_run_not_found"
            and queue_item["state"] == "outcome_unknown"
            and outcome_unknown_timeout_seconds is not None
        ):
            store.resolve_missing_outcome_unknown(
                queue_id=str(queue_item["queue_id"]),
                task_id=task_id,
                timeout_seconds=outcome_unknown_timeout_seconds,
            )
            return next(
                (item for item in read_tasks().tasks if item.task_id == task_id),
                None,
            )
        raise
    runtime_state = str(response.get("state") or task.runtime_state or "unknown").lower()
    if runtime_state not in {"success", "failed", "upstream_failed"}:
        return task
    plan = QueuedDispatchPlan(
        task=task,
        dag_id=str(effect["dag_id"]),
        dag_run_id=str(effect["dag_run_id"]),
        run_path="",
    )
    updated = _task_from_runtime_response(task, response, plan)
    store.complete_runtime_pending_task(
        queue_id=str(queue_item["queue_id"]),
        task_id=task_id,
        runtime_state=runtime_state,
        succeeded=runtime_state == "success",
        task_payload=updated.model_dump(mode="json"),
    )
    return next((item for item in read_tasks().tasks if item.task_id == task_id), None)


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
        task.version += 1
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
    with trace_span(
        "airflow.rest",
        kind="client",
        attributes={"evm.stage": "airflow", "http.request.method": method},
    ) as active:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Basic {token}",
            **active.context.headers(),
        }
        request = Request(url, data=body, method=method, headers=headers)
        try:
            with urlopen(request, timeout=10) as response:
                active.set_attribute(
                    "http.response.status_code",
                    int(getattr(response, "status", 200)),
                )
                parsed = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            active.set_attribute("http.response.status_code", exc.code)
            if exc.code == 404:
                code = "airflow_dag_run_not_found"
            elif exc.code == 409:
                code = "airflow_dag_run_conflict"
            else:
                code = "airflow_api_rejected"
            raise TaskDispatchError(
                code,
                f"Airflow API returned HTTP {exc.code}.",
                status_code=502,
                retryable=exc.code in {409, 429} or exc.code >= 500,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise TaskDispatchError(
                "airflow_api_unavailable",
                f"Airflow API is unavailable: {exc}",
                status_code=502,
                retryable=True,
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
