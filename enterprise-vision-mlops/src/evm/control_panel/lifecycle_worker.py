from __future__ import annotations

import argparse
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from evm.control_panel.lifecycle_orchestrator import process_lifecycle_run
from evm.control_panel.lifecycle_runs import (
    LifecycleWorkerState,
    read_runs,
    utc_now,
    write_worker_state,
)
from evm.operations.scenario_d_supervision import (
    LifecycleRunClaim,
    LifecycleRunClaimStore,
    current_process_started_at,
)
from evm.observability.otel import configure_tracing, shutdown_tracing


@dataclass
class WorkerContext:
    worker_id: str
    started_at: str
    process_instance_id: str
    source_commit: str | None
    supervisor_lease_id: str | None
    fencing_token: int | None
    claim_store: LifecycleRunClaimStore | None
    current_run_id: str | None = None
    current_claim: LifecycleRunClaim | None = None
    stop: bool = False


def heartbeat_loop(context: WorkerContext, lock: threading.Lock, interval: float) -> None:
    while True:
        with lock:
            if context.stop:
                return
            current_run_id = context.current_run_id
            if context.current_claim is not None and context.claim_store is not None:
                try:
                    context.current_claim = context.claim_store.renew(context.current_claim)
                except (OSError, RuntimeError, ValueError) as exc:
                    print(
                        json.dumps(
                            {
                                "worker_id": context.worker_id,
                                "run_id": context.current_run_id,
                                "claim_error": str(exc),
                                "error_type": type(exc).__name__,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
        try:
            write_worker_state(
                LifecycleWorkerState(
                    status="online",
                    worker_id=context.worker_id,
                    pid=os.getpid(),
                    source_commit=os.getenv("EVM_GIT_COMMIT") or None,
                    source_branch=os.getenv("EVM_GIT_BRANCH") or None,
                    started_at=context.started_at,
                    process_instance_id=context.process_instance_id,
                    supervisor_lease_id=context.supervisor_lease_id,
                    fencing_token=context.fencing_token,
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
    source_commit = os.getenv("EVM_GIT_COMMIT") or None
    supervisor_lease_id = os.getenv("EVM_SUPERVISOR_LEASE_ID") or None
    fencing_token_value = os.getenv("EVM_SUPERVISOR_FENCING_TOKEN")
    fencing_token = int(fencing_token_value) if fencing_token_value else None
    process_instance_id = os.getenv("EVM_PROCESS_INSTANCE_ID") or f"worker-{os.getpid()}"
    claim_root = Path(
        os.getenv(
            "EVM_LIFECYCLE_CLAIM_ROOT",
            str(Path(os.getenv("EVM_LIFECYCLE_RUN_ROOT", ".")) / "_claims"),
        )
    )
    claim_store = (
        LifecycleRunClaimStore(
            claim_root,
            ttl_seconds=float(os.getenv("EVM_LIFECYCLE_CLAIM_TTL_SECONDS", "30")),
        )
        if source_commit and supervisor_lease_id and fencing_token
        else None
    )
    context = WorkerContext(
        worker_id=worker_id or f"lifecycle-worker-{os.getpid()}",
        started_at=current_process_started_at().isoformat(),
        process_instance_id=process_instance_id,
        source_commit=source_commit,
        supervisor_lease_id=supervisor_lease_id,
        fencing_token=fencing_token,
        claim_store=claim_store,
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
                if context.claim_store is None:
                    exit_code = 1
                    print(
                        json.dumps(
                            {
                                "run_id": candidate,
                                "worker_error": "lifecycle_supervisor_identity_missing",
                                "error_type": "LifecycleClaimBlocked",
                            }
                        ),
                        flush=True,
                    )
                    continue
                claim_result = context.claim_store.acquire(
                    run_id=candidate,
                    worker_id=context.worker_id,
                    worker_pid=os.getpid(),
                    process_instance_id=context.process_instance_id,
                    source_commit=context.source_commit,
                    supervisor_lease_id=context.supervisor_lease_id,
                    fencing_token=context.fencing_token,
                )
                if not claim_result.acquired or claim_result.claim is None:
                    print(
                        json.dumps(
                            {
                                "run_id": candidate,
                                "worker_event": "lifecycle_claim_blocked",
                                "reason": claim_result.reason,
                            }
                        ),
                        flush=True,
                    )
                    continue
                with lock:
                    context.current_run_id = candidate
                    context.current_claim = claim_result.claim
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
                    claim = None
                    with lock:
                        claim = context.current_claim
                        context.current_run_id = None
                        context.current_claim = None
                    if claim is not None:
                        try:
                            context.claim_store.release(claim)
                        except (OSError, RuntimeError, ValueError) as exc:
                            exit_code = 1
                            print(
                                json.dumps(
                                    {
                                        "run_id": candidate,
                                        "claim_release_error": str(exc),
                                        "error_type": type(exc).__name__,
                                    }
                                ),
                                flush=True,
                            )
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
                process_instance_id=context.process_instance_id,
                supervisor_lease_id=context.supervisor_lease_id,
                fencing_token=context.fencing_token,
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
    configure_tracing("evm-lifecycle-worker", service_version="0.1.0")
    try:
        return run_worker(
            run_id=args.run_id,
            once=args.once,
            poll_interval=args.poll_interval,
            heartbeat_interval=args.heartbeat_interval,
            worker_id=args.worker_id,
        )
    finally:
        shutdown_tracing()


if __name__ == "__main__":
    raise SystemExit(main())
