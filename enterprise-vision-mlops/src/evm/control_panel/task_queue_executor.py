from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import sys
import threading
import time

import psutil

from evm.control_panel.operations import TaskDispatchError, dispatch_queued_task_assignment
from evm.control_panel.transactional_store import (
    ControlPlaneStoreError,
    get_transactional_store,
)
from evm.observability.otel import (
    configure_tracing,
    runtime_service_version,
    shutdown_tracing,
    trace_span,
)
from evm.observability.trace_context import TraceContextError, W3CTraceContext


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute one claimed durable task.")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--queue-id", required=True)
    parser.add_argument("--lease-owner", required=True)
    parser.add_argument("--lease-epoch", required=True, type=int)
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--parent-create-time", required=True, type=float)
    parser.add_argument("--traceparent", required=True)
    parser.add_argument("--tracestate", default=None)
    return parser.parse_args()


def bind_parent_lifetime(parent_pid: int, parent_create_time: float) -> None:
    """Terminate this executor if its exact worker process disappears."""
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None)
        if libc.prctl(1, signal.SIGKILL) != 0:
            raise OSError("failed to bind executor lifetime to its worker parent")

    def parent_is_exact() -> bool:
        try:
            parent = psutil.Process(parent_pid)
            return (
                parent.is_running()
                and abs(parent.create_time() - parent_create_time) < 0.01
                and os.getppid() == parent_pid
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return False

    if not parent_is_exact():
        raise RuntimeError("executor parent identity is not live")

    def monitor() -> None:
        while True:
            time.sleep(0.1)
            if not parent_is_exact():
                os._exit(137)

    threading.Thread(
        target=monitor,
        name="task-queue-parent-monitor",
        daemon=True,
    ).start()


def main() -> None:
    args = parse_args()
    bind_parent_lifetime(args.parent_pid, args.parent_create_time)
    configure_tracing("evm-task-queue-executor", service_version=runtime_service_version())
    try:
        try:
            parent = W3CTraceContext.parse(
                args.traceparent,
                tracestate=args.tracestate,
            )
        except TraceContextError as exc:
            raise ControlPlaneStoreError("executor trace context is invalid") from exc
        with trace_span(
            "task_queue.executor",
            parent=parent,
            kind="consumer",
            attributes={"evm.stage": "queue"},
        ) as active:
            store = get_transactional_store()
            lease = store.load_task_queue_lease(
                queue_id=args.queue_id,
                task_id=args.task_id,
                lease_owner=args.lease_owner,
                lease_epoch=args.lease_epoch,
            )
            with store.bind_task_queue_lease(lease):
                task = dispatch_queued_task_assignment(
                    args.task_id,
                    parent=active.context,
                )
        if task is None:
            print(json.dumps({"outcome": "permanent", "failure_class": "task_missing"}))
            raise SystemExit(65)
        queue_item = store.get_task_queue_item(queue_id=args.queue_id)
        print(
            json.dumps(
                {
                    "outcome": "completed",
                    "task_status": task.status,
                    "queue_state": queue_item["state"] if queue_item else "missing",
                    "runtime_state": task.runtime_state,
                    "runtime_id": task.runtime_id,
                }
            )
        )
    except TaskDispatchError as exc:
        print(
            json.dumps(
                {
                    "outcome": "transient" if exc.retryable else "permanent",
                    "failure_class": exc.code,
                }
            )
        )
        raise SystemExit(75 if exc.retryable else 65) from exc
    except ControlPlaneStoreError as exc:
        print(
            json.dumps(
                {
                    "outcome": "transient",
                    "failure_class": type(exc).__name__,
                }
            )
        )
        raise SystemExit(75) from exc
    finally:
        shutdown_tracing()


if __name__ == "__main__":
    main()
