from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path

from evm.control_panel.scenario_workload_control import (
    ScenarioWorkloadExecutionRequest,
    load_execution_request,
    worker_state_path,
)
from evm.control_panel.scenario_workload_production import (
    current_production_intent,
    list_production_intents,
)
from evm.control_panel.scenario_workloads import (
    get_workload_run,
    list_workload_runs,
    transition_workload_stage,
    workload_artifact_path,
)
from evm.model_runtime.common import atomic_write_json, utc_now
from evm.model_runtime.scenario_workload_production import (
    apply_production_intent,
    recover_incomplete_production_intents,
    reconcile_applied_intent,
    rollback_production_intent,
)
from evm.model_runtime.workload_runner import ScenarioExecutionConfig, run_real_scenario_lifecycle


def write_heartbeat(
    *,
    worker_id: str,
    started_at: str,
    current_run_id: str | None,
    current_intent_id: str | None,
) -> None:
    atomic_write_json(
        worker_state_path(),
        {
            "schema_version": "evm.scenario_workload_worker.v1",
            "status": "busy" if current_run_id or current_intent_id else "online",
            "worker_id": worker_id,
            "pid": os.getpid(),
            "source_commit": os.getenv("EVM_GIT_COMMIT") or None,
            "source_branch": os.getenv("EVM_GIT_BRANCH") or None,
            "started_at": started_at,
            "last_seen_at": utc_now(),
            "current_run_id": current_run_id,
            "current_intent_id": current_intent_id,
            "message": None,
        },
    )


def queued_run_ids() -> list[str]:
    return [
        run.run_id
        for run in reversed(list_workload_runs(limit=500).runs)
        if run.state == "queued"
        and (workload_artifact_path(run.artifact_root) / "execution-request.json").is_file()
    ]


def queued_intent_ids() -> list[str]:
    return [
        intent.intent_id
        for intent in reversed(list_production_intents(limit=500).intents)
        if intent.state in {"queued", "rollback_requested"}
    ]


def block_unstarted_run(run_id: str, exc: Exception) -> None:
    try:
        run = get_workload_run(run_id)
        if run.state != "queued":
            return
        evidence_path = workload_artifact_path(run.artifact_root) / "worker-admission-block.json"
        blocker = f"scenario_workload_worker_admission_blocked:{type(exc).__name__}:{exc}"
        atomic_write_json(
            evidence_path,
            {
                "schema_version": "evm.scenario_workload_worker_admission.v1",
                "status": "blocked",
                "run_id": run_id,
                "source_commit": run.identity.source_commit,
                "blockers": [blocker],
                "observed_at": utc_now(),
            },
        )
        transition_workload_stage(
            run_id,
            "data_intake",
            "blocked",
            actor="scenario-workload-worker",
            evidence_uri=str(evidence_path),
            detail="Worker admission failed closed before external side effects",
            blockers=[blocker],
        )
    except Exception:
        return


def execution_config(request: ScenarioWorkloadExecutionRequest) -> ScenarioExecutionConfig:
    preset = request.preset
    return ScenarioExecutionConfig(
        scenario_id=preset.scenario_id,
        model_family=preset.model_family,
        model_repository=preset.model_repository,
        model_revision=preset.model_revision,
        model_dir=Path(preset.model_dir),
        data_view_uri=preset.data_view_uri,
        quality_disposition_uri=preset.quality_disposition_uri,
        source_commit=request.source_commit,
        source_branch=request.source_branch,
        actor=request.requested_by,
        reason=request.reason,
        staging_approver="external-control-panel-approval",
        staging_reason="Await independent Control Panel approval.",
        serving_port=preset.staging_port,
        max_steps=preset.max_steps,
        quantization_requested=preset.quantization_requested,
        run_id=request.run_id,
        external_staging_approval=True,
        external_gpu_handoff=True,
    )


def run_worker(*, once: bool, poll_interval: float, worker_id: str) -> int:
    started_at = utc_now()
    exit_code = 0
    current_run_id: list[str | None] = [None]
    current_intent_id: list[str | None] = [None]
    stop = threading.Event()

    def heartbeat_loop() -> None:
        while not stop.wait(5):
            write_heartbeat(
                worker_id=worker_id,
                started_at=started_at,
                current_run_id=current_run_id[0],
                current_intent_id=current_intent_id[0],
            )

    heartbeat = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat.start()
    try:
        recover_incomplete_production_intents()
        while True:
            production = current_production_intent()
            if production is not None and production.state == "applied":
                current_intent_id[0] = production.intent_id
                write_heartbeat(
                    worker_id=worker_id,
                    started_at=started_at,
                    current_run_id=None,
                    current_intent_id=production.intent_id,
                )
                production = reconcile_applied_intent(production)
                current_intent_id[0] = None

            for intent_id in queued_intent_ids():
                current_intent_id[0] = intent_id
                write_heartbeat(
                    worker_id=worker_id,
                    started_at=started_at,
                    current_run_id=None,
                    current_intent_id=intent_id,
                )
                try:
                    intent = next(
                        item
                        for item in list_production_intents(limit=500).intents
                        if item.intent_id == intent_id
                    )
                    if intent.state == "queued":
                        apply_production_intent(intent_id)
                    else:
                        rollback_production_intent(intent_id)
                except Exception as exc:
                    exit_code = 1
                    print(
                        json.dumps(
                            {
                                "intent_id": intent_id,
                                "worker_error": str(exc),
                                "error_type": type(exc).__name__,
                            }
                        ),
                        flush=True,
                    )
                finally:
                    current_intent_id[0] = None
                    write_heartbeat(
                        worker_id=worker_id,
                        started_at=started_at,
                        current_run_id=None,
                        current_intent_id=None,
                    )

            if current_production_intent() is not None:
                if once:
                    return exit_code
                time.sleep(max(0.5, poll_interval))
                continue

            candidates = queued_run_ids()
            for run_id in candidates:
                current_run_id[0] = run_id
                write_heartbeat(
                    worker_id=worker_id,
                    started_at=started_at,
                    current_run_id=run_id,
                    current_intent_id=None,
                )
                try:
                    request = load_execution_request(run_id)
                    expected_commit = os.getenv("EVM_GIT_COMMIT", "").strip()
                    if expected_commit and request.source_commit != expected_commit:
                        raise RuntimeError(
                            "scenario_workload_worker_revision_mismatch:"
                            f"request={request.source_commit}:worker={expected_commit}"
                        )
                    result = run_real_scenario_lifecycle(execution_config(request))
                    print(
                        json.dumps(
                            {
                                "run_id": result["run_id"],
                                "state": result["state"],
                                "progress": result["progress"],
                            }
                        ),
                        flush=True,
                    )
                except Exception as exc:
                    exit_code = 1
                    block_unstarted_run(run_id, exc)
                    print(
                        json.dumps(
                            {
                                "run_id": run_id,
                                "worker_error": str(exc),
                                "error_type": type(exc).__name__,
                            }
                        ),
                        flush=True,
                    )
                finally:
                    current_run_id[0] = None
                    write_heartbeat(
                        worker_id=worker_id,
                        started_at=started_at,
                        current_run_id=None,
                        current_intent_id=None,
                    )
            if once:
                return exit_code
            write_heartbeat(
                worker_id=worker_id,
                started_at=started_at,
                current_run_id=None,
                current_intent_id=None,
            )
            time.sleep(max(0.5, poll_interval))
    finally:
        stop.set()
        heartbeat.join(timeout=6)


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute queued real VLM/LLM scenario workloads.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=3.0)
    parser.add_argument("--worker-id", default="windows-rtx4080-scenario-workload-worker")
    args = parser.parse_args()
    return run_worker(once=args.once, poll_interval=args.poll_interval, worker_id=args.worker_id)


if __name__ == "__main__":
    raise SystemExit(main())
