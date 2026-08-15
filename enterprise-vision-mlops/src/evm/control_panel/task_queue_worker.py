from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from prometheus_client import start_http_server

from evm.control_panel.admission_queue import (
    QUEUE_DLQ,
    QUEUE_DURABLE_BYTES,
    QUEUE_DURABLE_DEPTH,
    QUEUE_IN_FLIGHT,
    QUEUE_LOCAL_BYTES,
    QUEUE_LOCAL_DEPTH,
    QUEUE_OLDEST_AGE_SECONDS,
    QUEUE_PROCESS_RSS_BYTES,
    QUEUE_RETRIES,
    QUEUE_TERMINALS,
    QUEUE_WORK_SECONDS,
    QUEUE_WORKER_CAPACITY,
    AdmissionQueueConfig,
    admission_queue_mode,
    load_admission_queue_config,
)
from evm.control_panel.operations import (
    update_task_runtime,
)
from evm.control_panel.transactional_store import (
    ControlPlanePoolTimeout,
    ControlPlaneStoreError,
    TaskQueueLease,
    get_transactional_store,
)


@dataclass(frozen=True)
class WorkerIdentity:
    owner: str
    process_instance_id: str
    pid: int
    source_revision: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def resource_class(lease: TaskQueueLease) -> str:
    payload = lease.task_payload.get("config_payload", {})
    requested = str(payload.get("resource_class", "cpu")).strip().lower()
    return "gpu" if requested == "gpu" else "cpu"


def process_rss_bytes() -> int:
    statm = Path("/proc/self/statm")
    if statm.exists():
        pages = int(statm.read_text(encoding="ascii").split()[1])
        return pages * int(os.sysconf("SC_PAGE_SIZE"))
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value if sys.platform == "darwin" else value * 1024
    except (ImportError, OSError, ValueError):
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
        QUEUE_WORKER_CAPACITY.labels(resource_class="cpu").set(self.cpu_target)
        QUEUE_WORKER_CAPACITY.labels(resource_class="gpu").set(config.gpu_workers)

    async def run(self, *, once: bool = False) -> None:
        self._cpu_consumers = [
            asyncio.create_task(self._consumer("cpu", index), name=f"cpu-consumer-{index}")
            for index in range(self.config.cpu_workers_max)
        ]
        self._gpu_consumer = asyncio.create_task(
            self._consumer("gpu", 0), name="gpu-consumer-0"
        )
        try:
            while not self.stop_event.is_set():
                self.store.reconcile_task_queue(max_attempts=self.config.max_attempts)
                snapshot = self.store.task_queue_snapshot()
                self._observe_snapshot(snapshot)
                self._adjust_cpu_target(snapshot.active_depth)
                rss = process_rss_bytes()
                QUEUE_PROCESS_RSS_BYTES.set(rss)
                if rss > self.config.rss_cap_bytes:
                    raise RuntimeError(
                        f"task queue worker RSS {rss} exceeded cap {self.config.rss_cap_bytes}"
                    )
                await self._fill_local_queues()
                self._write_heartbeat("online", snapshot=snapshot, rss_bytes=rss)
                if once and snapshot.active_depth == 0 and self.local_count == 0:
                    break
                await asyncio.sleep(self.config.poll_interval_seconds)
        except BaseException as exc:
            self._fatal_error = exc
            self.stop_event.set()
            raise
        finally:
            await self._shutdown_consumers()
            final = self.store.task_queue_snapshot()
            self._observe_snapshot(final)
            self._write_heartbeat(
                "failed" if self._fatal_error else "stopped",
                snapshot=final,
                rss_bytes=process_rss_bytes(),
                error=type(self._fatal_error).__name__ if self._fatal_error else None,
            )

    def request_stop(self) -> None:
        self.stop_event.set()

    async def _fill_local_queues(self) -> None:
        available_count = self.config.local_max_depth - self.local_count
        available_bytes = self.config.local_max_bytes - self.local_bytes
        if available_count <= 0 or available_bytes <= 0:
            return
        leases = await asyncio.to_thread(
            self.store.claim_task_queue_items,
            owner=self.identity.owner,
            max_items=available_count,
            max_bytes=available_bytes,
            lease_seconds=self.config.lease_seconds,
            scan_limit=self.config.durable_max_depth,
        )
        for lease in leases:
            queue = self.gpu_queue if resource_class(lease) == "gpu" else self.cpu_queue
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
            self.in_flight[kind] += 1
            QUEUE_IN_FLIGHT.labels(resource_class=kind).set(self.in_flight[kind])
            started = time.monotonic()
            try:
                await self._execute_lease(lease)
            finally:
                QUEUE_WORK_SECONDS.labels(outcome="attempt").observe(
                    time.monotonic() - started
                )
                self.in_flight[kind] -= 1
                self.local_count -= 1
                self.local_bytes -= lease.payload_bytes
                QUEUE_IN_FLIGHT.labels(resource_class=kind).set(self.in_flight[kind])
                self._observe_local()
                queue.task_done()

    async def _execute_lease(self, lease: TaskQueueLease) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "evm.control_panel.task_queue_executor",
                "--task-id",
                lease.task_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.config.work_timeout_seconds,
                )
            except TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except TimeoutError:
                    process.kill()
                    await process.wait()
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
                    failure_class=str(result.get("failure_class") or "executor_failed"),
                    transient=str(result.get("outcome")) == "transient",
                )
                return
            task_status = str(result.get("task_status") or "unknown")
            if task_status not in {"running", "done"}:
                await self._handle_failure(
                    lease,
                    failure_class=f"unexpected_task_state_{task_status}",
                    transient=False,
                )
                return
            await asyncio.to_thread(
                self.store.complete_task_queue_item,
                lease,
                state="completed",
                reason="runtime_dispatch_committed",
            )
            QUEUE_TERMINALS.labels(
                state="completed", reason="runtime_dispatch_committed"
            ).inc()
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
        QUEUE_RETRIES.labels(outcome=state, failure_class=failure_class).inc()
        if state == "dlq":
            QUEUE_DLQ.labels(reason=failure_class).inc()
            QUEUE_TERMINALS.labels(state="dlq", reason=failure_class).inc()
            await asyncio.to_thread(
                update_task_runtime,
                lease.task_id,
                actor="task-queue-worker",
                event="task_queue_dlq",
                status="failed",
                runtime_state="dlq",
                failure_reason=str(result.get("terminal_reason") or failure_class),
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

    def _observe_snapshot(self, snapshot) -> None:
        states = ("available", "retry_wait", "leased", "terminal")
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
    await worker.run(once=args.once)


def main() -> None:
    asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    main()
