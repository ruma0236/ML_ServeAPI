from __future__ import annotations

import argparse
import json
import os
import threading
import time
from dataclasses import dataclass

from evm.control_panel.lifecycle_orchestrator import process_lifecycle_run
from evm.control_panel.lifecycle_runs import (
    LifecycleWorkerState,
    read_runs,
    utc_now,
    write_worker_state,
)


@dataclass
class WorkerContext:
    worker_id: str
    started_at: str
    current_run_id: str | None = None
    stop: bool = False


def heartbeat_loop(context: WorkerContext, lock: threading.Lock, interval: float) -> None:
    while True:
        with lock:
            if context.stop:
                return
            current_run_id = context.current_run_id
        try:
            write_worker_state(
                LifecycleWorkerState(
                    status="online",
                    worker_id=context.worker_id,
                    pid=os.getpid(),
                    source_commit=os.getenv("EVM_GIT_COMMIT") or None,
                    source_branch=os.getenv("EVM_GIT_BRANCH") or None,
                    started_at=context.started_at,
                    last_seen_at=utc_now(),
                    current_run_id=current_run_id,
                )
            )
        except OSError as exc:
            print(
                json.dumps(
                    {
                        "worker_id": context.worker_id,
                        "heartbeat_error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        time.sleep(interval)


def runnable_run_ids(run_id: str | None = None) -> list[str]:
    if run_id:
        return [run_id]
    return [
        run.run_id
        for run in reversed(read_runs().runs)
        if run.state in {"queued", "running"}
    ]


def run_worker(
    *,
    run_id: str | None = None,
    once: bool = False,
    poll_interval: float = 3.0,
    heartbeat_interval: float = 5.0,
    worker_id: str | None = None,
) -> int:
    context = WorkerContext(
        worker_id=worker_id or f"lifecycle-worker-{os.getpid()}",
        started_at=utc_now(),
    )
    lock = threading.Lock()
    heartbeat = threading.Thread(
        target=heartbeat_loop,
        args=(context, lock, heartbeat_interval),
        daemon=True,
    )
    heartbeat.start()
    exit_code = 0
    try:
        while True:
            candidates = runnable_run_ids(run_id)
            for candidate in candidates:
                with lock:
                    context.current_run_id = candidate
                try:
                    result = process_lifecycle_run(candidate)
                    print(
                        json.dumps(
                            {
                                "run_id": result.run_id,
                                "state": result.state,
                                "current_stage": result.current_stage,
                                "progress": result.progress,
                                "version": result.version,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    if result.state in {"failed", "blocked"}:
                        exit_code = 2
                except Exception as exc:
                    exit_code = 1
                    print(
                        json.dumps(
                            {
                                "run_id": candidate,
                                "worker_error": str(exc),
                                "error_type": type(exc).__name__,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                finally:
                    with lock:
                        context.current_run_id = None
            if once:
                return exit_code
            time.sleep(poll_interval)
    finally:
        with lock:
            context.stop = True
            context.current_run_id = None
        heartbeat.join(timeout=max(1.0, heartbeat_interval + 1.0))
        write_worker_state(
            LifecycleWorkerState(
                status="offline",
                worker_id=context.worker_id,
                pid=os.getpid(),
                source_commit=os.getenv("EVM_GIT_COMMIT") or None,
                source_branch=os.getenv("EVM_GIT_BRANCH") or None,
                started_at=context.started_at,
                last_seen_at=utc_now(),
                message="worker stopped",
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run dependency-aware LifecycleRun stages.")
    parser.add_argument("--run-id")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=3.0)
    parser.add_argument("--heartbeat-interval", type=float, default=5.0)
    parser.add_argument("--worker-id", default="windows-docker-desktop-lifecycle-worker")
    args = parser.parse_args()
    return run_worker(
        run_id=args.run_id,
        once=args.once,
        poll_interval=args.poll_interval,
        heartbeat_interval=args.heartbeat_interval,
        worker_id=args.worker_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
