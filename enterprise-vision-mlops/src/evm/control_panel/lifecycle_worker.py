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
    LifecycleRun,
    LifecycleWorkerState,
    audit,
    read_runs,
    reconcile_lifecycle_run_mirrors,
    utc_now,
    write_run_file,
    write_worker_state,
)
from evm.operations.scenario_d_supervision import (
    LifecycleRunClaim,
    LifecycleRunClaimStore,
    TransactionalLifecycleRunClaimStore,
    current_process_started_at,
)
from evm.control_panel.transactional_store import (
    ControlPlaneLeaseConflict,
    canonical_digest,
    get_transactional_store,
)
from evm.observability.otel import (
    configure_tracing,
    runtime_service_version,
    shutdown_tracing,
)


@dataclass
class WorkerContext:
    worker_id: str
    started_at: str
    process_instance_id: str
    source_commit: str | None
    supervisor_lease_id: str | None
    fencing_token: int | None
    claim_store: LifecycleRunClaimStore | TransactionalLifecycleRunClaimStore | None
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


def _s1_probe_root(run_id: str) -> Path | None:
    if os.getenv("EVM_S1_WORKER_LOSS_PROBE_ENABLED", "false").lower() != "true":
        return None
    expected_run_id = os.getenv("EVM_S1_WORKER_LOSS_RUN_ID")
    schema = os.getenv("EVM_CONTROL_PLANE_DATABASE_SCHEMA", "")
    root_value = os.getenv("EVM_S1_WORKER_LOSS_EVIDENCE_ROOT")
    if expected_run_id != run_id:
        return None
    if not schema.startswith("evm_s1_"):
        raise RuntimeError("s1_worker_loss_probe_requires_isolated_schema")
    if not root_value:
        raise RuntimeError("s1_worker_loss_probe_evidence_root_missing")
    root = Path(root_value).resolve()
    lifecycle = Path(os.getenv("EVM_LIFECYCLE_RUN_ROOT", ".")).resolve()
    if root == lifecycle or lifecycle not in root.parents:
        raise RuntimeError("s1_worker_loss_probe_requires_run_scoped_evidence_root")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_exclusive_json(path: Path, payload: object) -> bool:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return True


def _complete_s1_worker_loss_probe(
    run_id: str,
    claim: LifecycleRunClaim,
    probe_root: Path,
) -> LifecycleRun:
    store = get_transactional_store()
    if not store.enabled:
        raise RuntimeError("s1_worker_loss_probe_requires_transactional_store")
    completed_at = utc_now()

    def complete_run(run: LifecycleRun) -> LifecycleRun:
        if run.state not in {"queued", "running"}:
            raise RuntimeError(f"s1_worker_loss_probe_run_not_active:{run.state}")
        for index, stage in enumerate(run.stages):
            if stage.state not in {"completed", "skipped", "cancelled"}:
                run.stages[index] = stage.model_copy(
                    update={
                        "state": "completed",
                        "progress": 1.0,
                        "attempt": max(1, stage.attempt),
                        "started_at": stage.started_at or completed_at,
                        "finished_at": completed_at,
                        "runtime_state": "s1_worker_loss_probe_completed",
                        "detail": "Isolated S1 worker-loss recovery probe completed.",
                        "blockers": [],
                    }
                )
        run.state = "completed"
        run.version += 1
        run.updated_at = completed_at
        run.current_stage = None
        run.progress = 1.0
        run.finished_at = completed_at
        run.failure_reason = None
        run.blockers = []
        run.audit.append(
            audit(
                "s1-runtime-probe",
                "s1_worker_loss_recovery_committed",
                claim_epoch=claim.claim_epoch,
                process_instance_id=claim.process_instance_id,
            )
        )
        return run

    with store.transaction("s1_worker_loss_atomic_completion"):
        result_payload = store.mutate_entity(
            "lifecycle_run",
            run_id,
            expected_version=None,
            fallback_payload=None,
            mutate=lambda payload: complete_run(LifecycleRun.model_validate(payload)).model_dump(
                mode="json"
            ),
        )
        result = LifecycleRun.model_validate(result_payload)
        for action in ("lifecycle_terminal", "deployment_reservation", "artifact_publication"):
            action_digest = canonical_digest({"run_id": run_id, "action": action})
            side_effect_key = canonical_digest(
                {
                    "run_id": run_id,
                    "attempt_id": result.attempt_id,
                    "stage_id": "s1_worker_loss_probe",
                    "action": action,
                    "action_digest": action_digest,
                }
            )
            payload = {
                "schema_version": "evm.lifecycle_side_effect.v1",
                "side_effect_key": side_effect_key,
                "lifecycle_series_id": result.lifecycle_series_id or run_id,
                "lifecycle_run_id": run_id,
                "attempt_id": result.attempt_id or "s1-worker-loss-attempt",
                "correlation_id": result.correlation_id or run_id,
                "stage_id": "s1_worker_loss_probe",
                "action": action,
                "action_digest": action_digest,
                "state": "reserved",
                "runtime_id": None,
                "evidence_uri": None,
                "reserved_at": completed_at,
                "updated_at": completed_at,
            }
            persisted, _ = store.reserve_side_effect(payload)
            store.complete_side_effect(
                str(persisted["side_effect_key"]),
                state="completed",
                runtime_id=f"s1-worker-epoch-{claim.claim_epoch}",
                evidence_uri="private://s1-worker-loss/runtime-proof",
                updated_at=completed_at,
            )
    commit_payload = {
        "run_id": run_id,
        "worker_pid": os.getpid(),
        "process_instance_id": claim.process_instance_id,
        "claim_epoch": claim.claim_epoch,
        "database_version": result.version,
        "completed_at": completed_at,
    }
    _write_exclusive_json(probe_root / "recovery_commit.json", commit_payload)
    if os.getenv(
        "EVM_S1_WORKER_LOSS_INJECT_MIRROR_GAP", "false"
    ).lower() == "true" and _write_exclusive_json(
        probe_root / "mirror_gap_injected.json", commit_payload
    ):
        os._exit(86)
    write_run_file(result)
    _write_exclusive_json(
        probe_root / "mirror_written.json",
        {**commit_payload, "mirror_written_at": utc_now()},
    )
    return result


def _process_s1_worker_loss_probe(
    run_id: str,
    claim: LifecycleRunClaim,
) -> LifecycleRun | None:
    probe_root = _s1_probe_root(run_id)
    if probe_root is None:
        return None
    first_claim_path = probe_root / "first_claim.json"
    first_owner = _write_exclusive_json(
        first_claim_path,
        claim.model_dump(mode="json"),
    )
    if first_owner:
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "worker_event": "s1_worker_loss_claim_held",
                    "worker_pid": os.getpid(),
                    "claim_epoch": claim.claim_epoch,
                }
            ),
            flush=True,
        )
        hold_seconds = float(os.getenv("EVM_S1_WORKER_LOSS_HOLD_SECONDS", "120"))
        deadline = time.monotonic() + hold_seconds
        while time.monotonic() < deadline:
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
        raise RuntimeError("s1_worker_loss_probe_was_not_interrupted")
    return _complete_s1_worker_loss_probe(run_id, claim, probe_root)


def attempt_s1_stale_commit(run_id: str, claim_path: Path) -> int:
    probe_root = _s1_probe_root(run_id)
    if probe_root is None or claim_path.resolve().parent != probe_root:
        raise RuntimeError("s1_stale_commit_probe_identity_mismatch")
    claim = LifecycleRunClaim.model_validate_json(claim_path.read_text(encoding="utf-8"))
    store = get_transactional_store()
    try:
        with store.bind_claim(claim.model_dump(mode="json")):
            store.mutate_entity(
                "lifecycle_run",
                run_id,
                expected_version=None,
                fallback_payload=None,
                mutate=lambda payload: {**payload, "version": int(payload["version"]) + 1},
            )
    except ControlPlaneLeaseConflict as exc:
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "worker_event": "s1_stale_owner_commit_blocked",
                    "reason": str(exc),
                    "claim_epoch": claim.claim_epoch,
                }
            )
        )
        return 0
    raise RuntimeError("s1_stale_owner_commit_was_not_blocked")


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
    transactional_store = get_transactional_store()
    repaired_mirrors = reconcile_lifecycle_run_mirrors()
    expected_probe_run = os.getenv("EVM_S1_WORKER_LOSS_RUN_ID")
    if expected_probe_run and expected_probe_run in repaired_mirrors:
        probe_root = _s1_probe_root(expected_probe_run)
        if probe_root is not None:
            _write_exclusive_json(
                probe_root / "mirror_reconciled.json",
                {
                    "run_id": expected_probe_run,
                    "worker_pid": os.getpid(),
                    "process_instance_id": process_instance_id,
                    "reconciled_at": utc_now(),
                },
            )
    claim_ttl_seconds = float(os.getenv("EVM_LIFECYCLE_CLAIM_TTL_SECONDS", "30"))
    claim_store = None
    if source_commit and supervisor_lease_id and fencing_token:
        claim_store = (
            TransactionalLifecycleRunClaimStore(ttl_seconds=claim_ttl_seconds)
            if transactional_store.enabled
            else LifecycleRunClaimStore(claim_root, ttl_seconds=claim_ttl_seconds)
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
                    if transactional_store.enabled:
                        with transactional_store.bind_claim(
                            context.current_claim.model_dump(mode="json")
                        ):
                            result = _process_s1_worker_loss_probe(
                                candidate,
                                context.current_claim,
                            ) or process_lifecycle_run(candidate)
                    else:
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
    parser.add_argument("--runtime-scope", default="default")
    parser.add_argument("--s1-stale-claim-path", type=Path)
    args = parser.parse_args()
    configure_tracing(
        "evm-lifecycle-worker",
        service_version=runtime_service_version(),
    )
    try:
        if args.s1_stale_claim_path is not None:
            if not args.run_id:
                raise SystemExit("--run-id is required with --s1-stale-claim-path")
            return attempt_s1_stale_commit(args.run_id, args.s1_stale_claim_path)
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
