from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psutil
from prometheus_client import start_http_server

from evm.control_panel.admission_queue import (
    QUEUE_COMPACTIONS,
    QUEUE_CONSUMER_FAILURES,
    QUEUE_CPU_SCALE_EVENTS,
    QUEUE_DLQ,
    QUEUE_DURABLE_BYTES,
    QUEUE_DURABLE_DEPTH,
    QUEUE_HISTORY_BYTES,
    QUEUE_HISTORY_ROWS,
    QUEUE_IN_FLIGHT,
    QUEUE_LEASE_RENEWALS,
    QUEUE_LOCAL_BYTES,
    QUEUE_LOCAL_DEPTH,
    QUEUE_OLDEST_AGE_SECONDS,
    QUEUE_PROCESS_RSS_BYTES,
    QUEUE_PROCESS_RSS_SLOPE_BYTES_PER_MINUTE,
    QUEUE_RETRIES,
    QUEUE_TERMINALS,
    QUEUE_WORK_SECONDS,
    QUEUE_WORKER_CAPACITY,
    QUEUE_WORKER_LIVE_CONSUMERS,
    AdmissionQueueConfig,
    admission_queue_mode,
    bounded_queue_metric_reason,
    load_admission_queue_config,
    task_resource_class,
)
from evm.control_panel.operations import (
    TaskDispatchError,
    reconcile_queued_task_runtime,
    sync_task_json_mirror_from_store,
    verify_task_json_mirror_parity,
)
from evm.control_panel.transactional_store import (
    ControlPlanePoolTimeout,
    ControlPlaneStoreError,
    TaskQueueLease,
    get_transactional_store,
)
from evm.observability.otel import (
    configure_tracing,
    runtime_service_version,
    shutdown_tracing,
    trace_span,
)
from evm.observability.trace_context import TraceContextError, W3CTraceContext


@dataclass(frozen=True)
class WorkerIdentity:
    owner: str
    process_instance_id: str
    pid: int
    source_revision: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def resource_class(lease: TaskQueueLease) -> str:
    mapped = task_resource_class(lease.task_payload)
    if mapped != lease.resource_class:
        raise RuntimeError("durable resource-class identity mismatch")
    return mapped


def process_tree_rss_bytes(pid: int | None = None) -> int:
    root_pid = pid or os.getpid()
    try:
        root = psutil.Process(root_pid)
        processes = [root, *root.children(recursive=True)]
        rss = 0
        for process in processes:
            try:
                rss += int(process.memory_info().rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return rss
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return 0


def process_rss_bytes(pid: int | None = None) -> int:
    try:
        return int(psutil.Process(pid or os.getpid()).memory_info().rss)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return 0


class BoundedTaskQueueWorker:
    def __init__(
        self,
        *,
        config: AdmissionQueueConfig,
        identity: WorkerIdentity,
        heartbeat_path: Path | None = None,
    ) -> None:
        self.config = config
        self.identity = identity
        self.heartbeat_path = heartbeat_path
        self.store = get_transactional_store()
        if not self.store.enabled:
            raise RuntimeError("the durable task queue requires PostgreSQL control-plane mode")
        if admission_queue_mode() != "durable":
            raise RuntimeError("EVM_TASK_ADMISSION_MODE=durable is required")
        self.cpu_queue: asyncio.Queue[TaskQueueLease] = asyncio.Queue(
            maxsize=config.local_max_depth
        )
        self.gpu_queue: asyncio.Queue[TaskQueueLease] = asyncio.Queue(
            maxsize=config.local_max_depth
        )
        self.stop_event = asyncio.Event()
        self.local_bytes = 0
        self.local_count = 0
        self.in_flight = {"cpu": 0, "gpu": 0}
        self.cpu_target = config.cpu_workers_min
        self.idle_polls = 0
        self._cpu_consumers: list[asyncio.Task[None]] = []
        self._gpu_consumer: asyncio.Task[None] | None = None
        self._fatal_error: BaseException | None = None
        self._last_consumer_error: str | None = None
        self._consumer_failures = {"cpu": 0, "gpu": 0}
        self._loop_count = 0
        self._parent_create_time = psutil.Process(os.getpid()).create_time()
        self._executor_processes: dict[int, asyncio.subprocess.Process] = {}
        self._rss_samples: deque[tuple[float, int]] = deque()
        self._rss_warmup_deadline = (
            time.monotonic() + config.rss_slope_warmup_seconds
        )
        QUEUE_WORKER_CAPACITY.labels(resource_class="cpu").set(self.cpu_target)
        QUEUE_WORKER_CAPACITY.labels(resource_class="gpu").set(config.gpu_workers)
        QUEUE_WORKER_LIVE_CONSUMERS.labels(resource_class="cpu").set(0)
        QUEUE_WORKER_LIVE_CONSUMERS.labels(resource_class="gpu").set(0)

    async def run(self, *, once: bool = False) -> None:
        await asyncio.to_thread(
            self.store.verify_task_queue_cutover,
            mode="durable",
            config=self.config,
        )
        self._start_consumers()
        try:
            while not self.stop_event.is_set():
                self._loop_count += 1
                await self._supervise_consumers()
                reconciliation = await asyncio.to_thread(
                    self.store.reconcile_task_queue,
                    config=self.config,
                    include_transitions=True,
                )
                self._observe_reconciliation(reconciliation)
                await self._reconcile_runtime_pending()
                if self._loop_count % 10 == 0:
                    await self._compact_and_observe_history()
                snapshot = self.store.task_queue_snapshot()
                self._observe_snapshot(snapshot)
                self._adjust_cpu_target(snapshot.dispatchable_depth("cpu"))
                tree_rss = process_tree_rss_bytes()
                parent_rss = process_rss_bytes()
                QUEUE_PROCESS_RSS_BYTES.set(tree_rss)
                if tree_rss <= 0 or parent_rss <= 0:
                    raise RuntimeError("task queue process-tree RSS is unavailable")
                if tree_rss > self.config.rss_cap_bytes:
                    raise RuntimeError(
                        "task queue worker process-tree RSS "
                        f"{tree_rss} exceeded cap {self.config.rss_cap_bytes}"
                    )
                self._observe_rss_slope(parent_rss)
                await self._fill_local_queues()
                self._write_heartbeat(
                    "degraded" if self._last_consumer_error else "online",
                    snapshot=snapshot,
                    rss_bytes=tree_rss,
                    error=self._last_consumer_error,
                )
                if once and snapshot.active_depth == 0 and self.local_count == 0:
                    break
                await asyncio.sleep(self.config.poll_interval_seconds)
        except BaseException as exc:
            self._fatal_error = exc
            self.stop_event.set()
            raise
        finally:
            await self._shutdown_consumers()
            await asyncio.to_thread(sync_task_json_mirror_from_store)
            final = self.store.task_queue_snapshot()
            self._observe_snapshot(final)
            self._write_heartbeat(
                "failed" if self._fatal_error else "stopped",
                snapshot=final,
                rss_bytes=process_tree_rss_bytes(),
                error=type(self._fatal_error).__name__ if self._fatal_error else None,
            )

    def _start_consumers(self) -> None:
        self._cpu_consumers = [
            self._new_consumer("cpu", index)
            for index in range(self.config.cpu_workers_max)
        ]
        self._gpu_consumer = self._new_consumer("gpu", 0)

    def _new_consumer(self, kind: str, index: int) -> asyncio.Task[None]:
        return asyncio.create_task(
            self._consumer(kind, index),
            name=f"{kind}-consumer-{index}",
        )

    async def _supervise_consumers(self) -> None:
        for index, task in enumerate(list(self._cpu_consumers)):
            if task.done() and not self.stop_event.is_set():
                self._record_consumer_failure("cpu", task)
                self._cpu_consumers[index] = self._new_consumer("cpu", index)
        if (
            self._gpu_consumer is not None
            and self._gpu_consumer.done()
            and not self.stop_event.is_set()
        ):
            self._record_consumer_failure("gpu", self._gpu_consumer)
            self._gpu_consumer = self._new_consumer("gpu", 0)
        live_cpu = sum(
            1
            for index, task in enumerate(self._cpu_consumers)
            if index < self.cpu_target and not task.done()
        )
        live_gpu = int(self._gpu_consumer is not None and not self._gpu_consumer.done())
        QUEUE_WORKER_LIVE_CONSUMERS.labels(resource_class="cpu").set(live_cpu)
        QUEUE_WORKER_LIVE_CONSUMERS.labels(resource_class="gpu").set(live_gpu)

    def _record_consumer_failure(self, kind: str, task: asyncio.Task[None]) -> None:
        try:
            error = task.exception()
        except asyncio.CancelledError:
            error = None
        failure_class = type(error).__name__ if error is not None else "unexpected_exit"
        self._consumer_failures[kind] += 1
        self._last_consumer_error = f"{kind}:{failure_class}"
        QUEUE_CONSUMER_FAILURES.labels(
            resource_class=kind,
            failure_class=failure_class,
        ).inc()

    def request_stop(self) -> None:
        self.stop_event.set()

    async def _fill_local_queues(self) -> None:
        for kind, queue, target in (
            ("gpu", self.gpu_queue, self.config.gpu_workers),
            ("cpu", self.cpu_queue, self.cpu_target),
        ):
            resource_slots = target - self.in_flight[kind] - queue.qsize()
            available_count = min(
                resource_slots,
                self.config.local_max_depth - self.local_count,
            )
            available_bytes = self.config.local_max_bytes - self.local_bytes
            if available_count <= 0 or available_bytes <= 0:
                continue
            leases = await asyncio.to_thread(
                self.store.claim_task_queue_items,
                owner=self.identity.owner,
                max_items=available_count,
                max_bytes=available_bytes,
                lease_seconds=self.config.lease_seconds,
                scan_limit=self.config.durable_max_depth,
                resource_class=kind,
                max_outstanding=(
                    self.config.gpu_downstream_max_outstanding
                    if kind == "gpu"
                    else self.config.cpu_downstream_max_outstanding
                ),
            )
            for lease in leases:
                if resource_class(lease) != kind:
                    raise RuntimeError("claimed task entered the wrong resource queue")
                self.local_count += 1
                self.local_bytes += lease.payload_bytes
                queue.put_nowait(lease)
        self._observe_local()

    async def _consumer(self, kind: str, index: int) -> None:
        queue = self.gpu_queue if kind == "gpu" else self.cpu_queue
        while True:
            if self.stop_event.is_set() and queue.empty():
                return
            if kind == "cpu" and index >= self.cpu_target:
                await asyncio.sleep(self.config.poll_interval_seconds)
                continue
            try:
                lease = await asyncio.wait_for(
                    queue.get(), timeout=self.config.poll_interval_seconds
                )
            except TimeoutError:
                continue
            started = time.monotonic()
            execution_started = False
            try:
                lease = await asyncio.to_thread(
                    self.store.begin_task_queue_attempt,
                    lease,
                    lease_seconds=self.config.lease_seconds,
                )
                self.in_flight[kind] += 1
                execution_started = True
                QUEUE_IN_FLIGHT.labels(resource_class=kind).set(self.in_flight[kind])
                await self._execute_lease(lease)
                self._last_consumer_error = None
            except ControlPlaneStoreError as exc:
                self._consumer_failures[kind] += 1
                self._last_consumer_error = f"{kind}:{type(exc).__name__}"
                QUEUE_CONSUMER_FAILURES.labels(
                    resource_class=kind,
                    failure_class=type(exc).__name__,
                ).inc()
                await asyncio.sleep(self.config.poll_interval_seconds)
            finally:
                QUEUE_WORK_SECONDS.labels(outcome="attempt").observe(
                    time.monotonic() - started
                )
                if execution_started:
                    self.in_flight[kind] = max(0, self.in_flight[kind] - 1)
                self.local_count -= 1
                self.local_bytes -= lease.payload_bytes
                QUEUE_IN_FLIGHT.labels(resource_class=kind).set(self.in_flight[kind])
                self._observe_local()
                queue.task_done()

    async def _execute_lease(self, lease: TaskQueueLease) -> None:
        parent = None
        traceparent = lease.task_payload.get("config_payload", {}).get("traceparent")
        if isinstance(traceparent, str) and traceparent:
            try:
                parent = W3CTraceContext.parse(traceparent)
            except TraceContextError:
                parent = None
        try:
            with trace_span(
                "task_queue.execute",
                parent=parent,
                kind="consumer",
                attributes={
                    "evm.stage": "queue",
                    "messaging.system": "postgresql-durable-queue",
                    "messaging.operation.type": "process",
                    "evm.task.resource_class": lease.resource_class,
                },
            ) as active:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "evm.control_panel.task_queue_executor",
                    "--task-id",
                    lease.task_id,
                    "--queue-id",
                    lease.queue_id,
                    "--lease-owner",
                    lease.lease_owner,
                    "--lease-epoch",
                    str(lease.lease_epoch),
                    "--parent-pid",
                    str(os.getpid()),
                    "--parent-create-time",
                    repr(self._parent_create_time),
                    "--traceparent",
                    active.context.traceparent,
                    "--tracestate",
                    active.context.tracestate or "",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**os.environ, "EVM_CONTROL_PLANE_AUTO_MIGRATE": "false"},
                )
                self._executor_processes[process.pid] = process
                try:
                    try:
                        stdout, _stderr = await self._communicate_with_lease_renewal(
                            process,
                            lease,
                        )
                    except TimeoutError:
                        await self._terminate_executor(process)
                        await self._handle_failure(
                            lease,
                            failure_class="work_timeout",
                            transient=True,
                        )
                        return
                    result = self._executor_result(stdout)
                    if process.returncode != 0:
                        await self._handle_failure(
                            lease,
                            failure_class=str(
                                result.get("failure_class") or "executor_failed"
                            ),
                            transient=str(result.get("outcome")) == "transient",
                        )
                        return
                    task_status = str(result.get("task_status") or "unknown")
                    queue_state = str(result.get("queue_state") or "unknown")
                    expected = {
                        "running": "runtime_pending",
                        "done": "completed",
                        "failed": "failed",
                    }
                    if task_status not in expected or queue_state != expected[task_status]:
                        raise ControlPlaneStoreError(
                            "executor task/queue terminal semantics diverged"
                        )
                    active.set_attribute("evm.task.status", task_status)
                    active.set_attribute("evm.queue.state", queue_state)
                    if queue_state in {"completed", "failed"}:
                        QUEUE_TERMINALS.labels(
                            state=queue_state,
                            reason="runtime_terminal",
                        ).inc()
                except asyncio.CancelledError:
                    await self._terminate_executor(process)
                    raise
                finally:
                    self._executor_processes.pop(process.pid, None)
        except ControlPlanePoolTimeout:
            await self._handle_failure(
                lease,
                failure_class="control_plane_pool_timeout",
                transient=True,
            )
        except ControlPlaneStoreError:
            raise
        except (ConnectionError, OSError) as exc:
            await self._handle_failure(
                lease,
                failure_class=type(exc).__name__.lower(),
                transient=True,
            )
        except Exception as exc:
            await self._handle_failure(
                lease,
                failure_class=type(exc).__name__.lower(),
                transient=False,
            )

    async def _communicate_with_lease_renewal(
        self,
        process: asyncio.subprocess.Process,
        lease: TaskQueueLease,
    ) -> tuple[bytes, bytes]:
        communication = asyncio.create_task(process.communicate())
        renewal = asyncio.create_task(self._renew_lease_until_done(lease, communication))
        try:
            done, _pending = await asyncio.wait(
                {communication, renewal},
                timeout=self.config.work_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError
            if renewal in done:
                error = renewal.exception()
                if error is not None:
                    await self._terminate_executor(process)
                    raise error
            if not communication.done():
                raise TimeoutError
            return communication.result()
        finally:
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)
            if not communication.done():
                communication.cancel()
                await asyncio.gather(communication, return_exceptions=True)

    async def _renew_lease_until_done(
        self,
        lease: TaskQueueLease,
        communication: asyncio.Task[tuple[bytes, bytes]],
    ) -> None:
        while not communication.done():
            await asyncio.sleep(self.config.lease_renew_interval_seconds)
            if communication.done():
                return
            try:
                await asyncio.to_thread(
                    self.store.renew_task_queue_lease,
                    lease,
                    lease_seconds=self.config.lease_seconds,
                )
                QUEUE_LEASE_RENEWALS.labels(outcome="renewed").inc()
            except ControlPlaneStoreError:
                QUEUE_LEASE_RENEWALS.labels(outcome="failed").inc()
                raise

    @staticmethod
    async def _terminate_executor(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            root = psutil.Process(process.pid)
            descendants = root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            descendants = []
            root = None
        for descendant in reversed(descendants):
            try:
                descendant.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if root is not None:
            try:
                root.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except TimeoutError:
            for descendant in reversed(descendants):
                try:
                    descendant.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if process.returncode is None:
                process.kill()
            await process.wait()

    @staticmethod
    def _executor_result(stdout: bytes) -> dict[str, object]:
        lines = [line for line in stdout.decode("utf-8", errors="replace").splitlines() if line]
        if not lines:
            return {"outcome": "permanent", "failure_class": "executor_empty_result"}
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError:
            return {"outcome": "permanent", "failure_class": "executor_invalid_result"}
        return payload if isinstance(payload, dict) else {
            "outcome": "permanent",
            "failure_class": "executor_invalid_result",
        }

    async def _handle_failure(
        self,
        lease: TaskQueueLease,
        *,
        failure_class: str,
        transient: bool,
    ) -> None:
        result = await asyncio.to_thread(
            self.store.reschedule_task_queue_item,
            lease,
            failure_class=failure_class,
            transient=transient,
            config=self.config,
        )
        state = str(result["state"])
        metric_failure = bounded_queue_metric_reason(failure_class)
        QUEUE_RETRIES.labels(outcome=state, failure_class=metric_failure).inc()
        if state == "dlq":
            QUEUE_DLQ.labels(reason=metric_failure).inc()
            QUEUE_TERMINALS.labels(state="dlq", reason=metric_failure).inc()

    def _observe_reconciliation(self, result: dict[str, object]) -> None:
        transitions = result.get("transitions")
        if isinstance(transitions, list):
            for transition in transitions:
                if not isinstance(transition, dict):
                    continue
                state = str(transition.get("state") or "unknown")
                reason = str(transition.get("reason") or "unknown")
                if state in {"expired", "dlq"}:
                    metric_reason = bounded_queue_metric_reason(reason)
                    QUEUE_TERMINALS.labels(state=state, reason=metric_reason).inc()
                    if state == "dlq":
                        QUEUE_DLQ.labels(reason=metric_reason).inc()

    async def _reconcile_runtime_pending(self) -> None:
        pending = await asyncio.to_thread(
            self.store.claim_runtime_pending_for_poll,
            max_items=self.config.runtime_poll_batch_size,
            poll_interval_seconds=self.config.runtime_poll_interval_seconds,
        )
        for item in pending:
            task_id = str(item["task_id"])
            try:
                with trace_span(
                    "task_queue.runtime_reconcile",
                    kind="consumer",
                    attributes={
                        "evm.stage": "queue",
                        "messaging.operation.type": "process",
                    },
                ):
                    task = await asyncio.to_thread(
                        reconcile_queued_task_runtime,
                        task_id,
                        outcome_unknown_timeout_seconds=(
                            self.config.runtime_terminal_timeout_seconds
                        ),
                    )
                if task is not None and task.status in {"done", "failed"}:
                    QUEUE_TERMINALS.labels(
                        state="completed" if task.status == "done" else "failed",
                        reason="runtime_terminal",
                    ).inc()
            except (TaskDispatchError, ControlPlaneStoreError):
                continue

    async def _compact_and_observe_history(self) -> None:
        compacted = await asyncio.to_thread(
            self.store.compact_task_queue_history,
            config=self.config,
        )
        for history_class in ("queue", "effect", "task"):
            count = int(compacted.get(f"{history_class}_rows", 0))
            if count:
                QUEUE_COMPACTIONS.labels(history_class=history_class).inc(count)
        history = await asyncio.to_thread(self.store.task_queue_history_snapshot)
        QUEUE_HISTORY_ROWS.labels(history_class="queue").set(history.queue_rows)
        QUEUE_HISTORY_BYTES.labels(history_class="queue").set(history.queue_bytes)
        QUEUE_HISTORY_ROWS.labels(history_class="effect").set(history.effect_rows)
        QUEUE_HISTORY_BYTES.labels(history_class="effect").set(history.effect_bytes)
        QUEUE_HISTORY_ROWS.labels(history_class="task").set(history.task_rows)
        QUEUE_HISTORY_BYTES.labels(history_class="task").set(history.task_bytes)
        QUEUE_HISTORY_ROWS.labels(history_class="mirror").set(history.mirror_rows)
        QUEUE_HISTORY_BYTES.labels(history_class="mirror").set(history.mirror_bytes)
        QUEUE_HISTORY_ROWS.labels(history_class="idempotency").set(
            history.idempotency_rows
        )
        QUEUE_HISTORY_BYTES.labels(history_class="idempotency").set(
            history.idempotency_bytes
        )
        for history_class in ("queue", "effect", "task"):
            QUEUE_HISTORY_ROWS.labels(history_class=f"{history_class}_compacted").set(
                history.compacted_rows.get(history_class, 0)
            )
            QUEUE_HISTORY_BYTES.labels(history_class=f"{history_class}_compacted").set(
                history.compacted_bytes.get(history_class, 0)
            )
        snapshot = await asyncio.to_thread(self.store.task_queue_snapshot)
        stable = (
            snapshot.active_depth == 0
            and self.local_count == 0
            and sum(self.in_flight.values()) == 0
        )
        if stable:
            await asyncio.to_thread(sync_task_json_mirror_from_store)
            parity = await asyncio.to_thread(verify_task_json_mirror_parity)
            if not parity["matches"]:
                raise ControlPlaneStoreError(
                    "PostgreSQL and JSON rollback mirror diverged after queue drain"
                )

    def _adjust_cpu_target(self, durable_depth: int) -> None:
        previous = self.cpu_target
        if durable_depth >= max(2, self.cpu_target * 2) and self.cpu_target < self.config.cpu_workers_max:
            self.cpu_target += 1
            self.idle_polls = 0
        elif durable_depth == 0 and self.in_flight["cpu"] == 0 and self.cpu_queue.empty():
            self.idle_polls += 1
            if self.idle_polls >= 10 and self.cpu_target > self.config.cpu_workers_min:
                self.cpu_target -= 1
                self.idle_polls = 0
        else:
            self.idle_polls = 0
        if previous != self.cpu_target:
            QUEUE_WORKER_CAPACITY.labels(resource_class="cpu").set(self.cpu_target)
            QUEUE_CPU_SCALE_EVENTS.labels(
                direction="up" if self.cpu_target > previous else "down"
            ).inc()

    def _observe_snapshot(self, snapshot) -> None:
        states = (
            "available",
            "retry_wait",
            "leased",
            "runtime_pending",
            "outcome_unknown",
            "terminal",
        )
        for state in states:
            if state == "terminal":
                terminal_states = ("completed", "failed", "dlq", "expired", "cancelled")
                depth = sum(snapshot.state_counts.get(item, 0) for item in terminal_states)
                payload_bytes = sum(snapshot.state_bytes.get(item, 0) for item in terminal_states)
            else:
                depth = snapshot.state_counts.get(state, 0)
                payload_bytes = snapshot.state_bytes.get(state, 0)
            QUEUE_DURABLE_DEPTH.labels(state=state).set(depth)
            QUEUE_DURABLE_BYTES.labels(state=state).set(payload_bytes)
        QUEUE_OLDEST_AGE_SECONDS.set(snapshot.oldest_age_seconds)

    def _observe_local(self) -> None:
        QUEUE_LOCAL_DEPTH.set(self.local_count)
        QUEUE_LOCAL_BYTES.set(self.local_bytes)

    def _observe_rss_slope(self, rss_bytes: int) -> None:
        observed_at = time.monotonic()
        if observed_at < self._rss_warmup_deadline:
            self._rss_samples.clear()
            QUEUE_PROCESS_RSS_SLOPE_BYTES_PER_MINUTE.set(0)
            return
        self._rss_samples.append((observed_at, rss_bytes))
        window = self.config.rss_slope_window_seconds
        while self._rss_samples and observed_at - self._rss_samples[0][0] > window:
            self._rss_samples.popleft()
        if len(self._rss_samples) < 2:
            QUEUE_PROCESS_RSS_SLOPE_BYTES_PER_MINUTE.set(0)
            return
        elapsed = self._rss_samples[-1][0] - self._rss_samples[0][0]
        if elapsed <= 0:
            return
        if elapsed < window * 0.95:
            QUEUE_PROCESS_RSS_SLOPE_BYTES_PER_MINUTE.set(0)
            return
        slope = (
            (self._rss_samples[-1][1] - self._rss_samples[0][1])
            / elapsed
            * 60.0
        )
        QUEUE_PROCESS_RSS_SLOPE_BYTES_PER_MINUTE.set(slope)
        if slope > self.config.rss_slope_tolerance_bytes_per_minute:
            raise RuntimeError(
                "task queue worker parent RSS slope "
                f"{slope:.3f} exceeded frozen tolerance "
                f"{self.config.rss_slope_tolerance_bytes_per_minute}"
            )

    async def _shutdown_consumers(self) -> None:
        deadline = time.monotonic() + self.config.drain_timeout_seconds
        while self.local_count and time.monotonic() < deadline:
            await asyncio.sleep(self.config.poll_interval_seconds)
        queues = (self.cpu_queue, self.gpu_queue)
        for queue in queues:
            while not queue.empty():
                lease = queue.get_nowait()
                try:
                    await asyncio.to_thread(
                        self.store.release_task_queue_lease,
                        lease,
                        reason="graceful_shutdown_before_start",
                    )
                finally:
                    self.local_count -= 1
                    self.local_bytes -= lease.payload_bytes
                    queue.task_done()
        tasks = [*self._cpu_consumers]
        if self._gpu_consumer is not None:
            tasks.append(self._gpu_consumer)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for process in list(self._executor_processes.values()):
            await self._terminate_executor(process)
        self._executor_processes.clear()
        self._observe_local()

    def _write_heartbeat(
        self,
        status: str,
        *,
        snapshot,
        rss_bytes: int,
        error: str | None = None,
    ) -> None:
        if self.heartbeat_path is None:
            return
        payload = {
            "schema_version": "evm.task_queue_worker_heartbeat.v1",
            "observed_at": utc_now(),
            "status": status,
            "pid": self.identity.pid,
            "process_instance_id": self.identity.process_instance_id,
            "source_revision": self.identity.source_revision,
            "config_version": self.config.profile_version,
            "config_sha256": self.config.sha256,
            "durable_depth": snapshot.active_depth,
            "durable_bytes": snapshot.active_bytes,
            "local_depth": self.local_count,
            "local_bytes": self.local_bytes,
            "cpu_target": self.cpu_target,
            "gpu_workers": self.config.gpu_workers,
            "live_consumers": {
                "cpu": sum(
                    1
                    for index, task in enumerate(self._cpu_consumers)
                    if index < self.cpu_target and not task.done()
                ),
                "gpu": int(
                    self._gpu_consumer is not None and not self._gpu_consumer.done()
                ),
            },
            "consumer_failures": dict(self._consumer_failures),
            "in_flight": dict(self.in_flight),
            "rss_bytes": rss_bytes,
            "error": error,
        }
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.heartbeat_path.with_suffix(
            f"{self.heartbeat_path.suffix}.{uuid4().hex}.tmp"
        )
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.heartbeat_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the bounded durable task queue worker.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--worker-id", default="local-task-queue-worker")
    parser.add_argument("--heartbeat-path", default=None)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-metrics-server", action="store_true")
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> None:
    config = (
        AdmissionQueueConfig.from_path(args.config)
        if args.config
        else load_admission_queue_config()
    )
    identity = WorkerIdentity(
        owner=f"{args.worker_id}:{os.getpid()}:{uuid4().hex}",
        process_instance_id=uuid4().hex,
        pid=os.getpid(),
        source_revision=os.getenv("EVM_GIT_COMMIT", "unknown"),
    )
    heartbeat = Path(args.heartbeat_path) if args.heartbeat_path else None
    worker = BoundedTaskQueueWorker(
        config=config,
        identity=identity,
        heartbeat_path=heartbeat,
    )
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, worker.request_stop)
        except NotImplementedError:
            pass
    if not args.no_metrics_server:
        start_http_server(config.metrics_port)
    configure_tracing("evm-task-queue-worker", service_version=runtime_service_version())
    try:
        await worker.run(once=args.once)
    finally:
        shutdown_tracing()


def main() -> None:
    asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    main()
