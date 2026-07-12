from __future__ import annotations

import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from evm.control_panel.aggregation import build_latest_cycle
from evm.control_panel.deployment_executor import execute_apply, execute_rollback
from evm.control_panel.deployment_intents import (
    approve_intent,
    create_deployment_intent,
    get_intent,
    queue_intent,
    request_approval,
)
from evm.control_panel.kubernetes_task_executor import execute_kubernetes_task
from evm.control_panel.lifecycle_kubernetes import (
    LifecycleKubernetesError,
    ServingBundle,
    build_training_evidence,
    materialize_serving_bundle,
    materialize_training_bundle,
)
from evm.control_panel.lifecycle_runs import (
    LifecycleRun,
    LifecycleRunError,
    get_lifecycle_run,
    lifecycle_root,
    mark_lifecycle_rollback,
    mark_lifecycle_rollback_failed,
    transition_stage,
    update_run_evidence,
)
from evm.control_panel.operations import (
    TaskDispatchError,
    create_task_assignment,
    dispatch_task_assignment,
    read_tasks,
    sync_running_tasks,
)
from evm.control_panel.real_test_policy import validate_real_test_evidence
from evm.control_panel.readiness_evaluator import runtime_path
from evm.control_panel.schemas import (
    CycleRun,
    DeploymentIntentRequest,
    DeploymentTransitionRequest,
    ResourceRef,
    TaskAssignment,
    TaskAssignmentRequest,
)
from evm.core.http import request_json


Runner = Callable[..., subprocess.CompletedProcess[str]]
HttpClient = Callable[..., tuple[int, dict[str, Any] | list[Any] | str]]


class LifecycleStageBlocked(RuntimeError):
    def __init__(self, code: str, blockers: list[str] | None = None):
        self.code = code
        self.blockers = sorted(set(blockers or [code]))
        super().__init__(code)


def process_lifecycle_run(
    run_id: str,
    *,
    runner: Runner = subprocess.run,
    http_client: HttpClient = request_json,
) -> LifecycleRun:
    run = required_run(run_id)
    if run.state in {
        "dry_run",
        "waiting_approval",
        "blocked",
        "failed",
        "completed",
        "cancelled",
        "rolling_back",
        "rolled_back",
    }:
        return run
    if not run.current_stage:
        return run
    handler = STAGE_HANDLERS.get(run.current_stage)
    if handler is None:
        return fail_stage(run, "lifecycle_stage_handler_missing", [run.current_stage])
    try:
        return handler(run, runner=runner, http_client=http_client)
    except LifecycleStageBlocked as exc:
        return block_stage(required_run(run_id), exc.code, exc.blockers)
    except TaskDispatchError as exc:
        return fail_stage(
            required_run(run_id),
            f"{exc.code}: {exc}",
            [exc.code, str(exc)],
        )
    except LifecycleRunError as exc:
        return fail_stage(
            required_run(run_id),
            f"{exc.code}: {exc}",
            [exc.code, str(exc)],
        )
    except (
        LifecycleKubernetesError,
        OSError,
        ValueError,
        RuntimeError,
    ) as exc:
        return fail_stage(
            required_run(run_id),
            str(exc) or type(exc).__name__,
            [type(exc).__name__, str(exc) or "runtime_error"],
        )


def process_data_pipeline(
    run: LifecycleRun,
    *,
    runner: Runner,
    http_client: HttpClient,
) -> LifecycleRun:
    del runner, http_client
    stage = stage_for(run, "data_pipeline")
    task = task_for(stage.task_id)
    if stage.state == "not_started":
        task = create_task_assignment(
            TaskAssignmentRequest(
                cycle_id=run.run_id,
                task_type="airflow_dag_run",
                owner=run.actor,
                priority="high",
                resource_profile="local-pipeline-workers",
                requester_team="ml-platform",
                approval_policy="auto",
                config_payload={
                    "dag_id": "enterprise_vision_mlops_daily",
                    "lifecycle_run_id": run.run_id,
                    "pipeline_profile_id": run.profile_id,
                    "pipeline_profile_version": run.profile_version,
                    "profile_digest": run.profile_digest,
                    "effective_config_digest": run.effective_config_digest,
                    "pipeline_config_uri": run.airflow_runtime_uri,
                    "pipeline_stage_scope": "data",
                },
                dry_run=False,
            )
        )
        run = transition_stage(
            run.run_id,
            "data_pipeline",
            "queued",
            actor="lifecycle-worker",
            task_id=task.task_id,
            runtime_state=task.status,
            detail="Airflow data-only DAG run queued",
        )
        stage = stage_for(run, "data_pipeline")
    if task is None:
        raise LifecycleStageBlocked("airflow_task_missing")
    if task.status == "queued":
        task = dispatch_task_assignment(task.task_id)
        if task is None:
            raise LifecycleStageBlocked("airflow_task_disappeared")
    if task.status == "running" and stage.state == "queued":
        run = transition_stage(
            run.run_id,
            "data_pipeline",
            "running",
            actor="lifecycle-worker",
            task_id=task.task_id,
            runtime_id=task.runtime_id,
            runtime_state=task.runtime_state,
            detail="Airflow DAG run is executing isolated data stages",
        )
    task = next(
        (item for item in sync_running_tasks().tasks if item.task_id == task.task_id),
        task,
    )
    if task.status == "done":
        current = required_run(run.run_id)
        if stage_for(current, "data_pipeline").state == "queued":
            current = transition_stage(
                run.run_id,
                "data_pipeline",
                "running",
                actor="lifecycle-worker",
                task_id=task.task_id,
                runtime_id=task.runtime_id,
                runtime_state=task.runtime_state,
            )
        return transition_stage(
            current.run_id,
            "data_pipeline",
            "completed",
            actor="lifecycle-worker",
            task_id=task.task_id,
            runtime_id=task.runtime_id,
            runtime_state=task.runtime_state,
            evidence_uri=task.runtime_url,
            detail="Airflow data pipeline completed",
        )
    if task.status in {"failed", "blocked", "cancelled"}:
        raise LifecycleStageBlocked(
            f"airflow_task_{task.status}",
            [task.failure_reason or f"airflow_task_{task.status}"],
        )
    return required_run(run.run_id)


def process_model_training(
    run: LifecycleRun,
    *,
    runner: Runner,
    http_client: HttpClient,
) -> LifecycleRun:
    del http_client
    ensure_generated_manifest_root()
    bundle = materialize_training_bundle(run)
    stage = stage_for(run, "model_training")
    task = task_for(stage.task_id)
    if stage.state == "not_started":
        task = create_task_assignment(
            TaskAssignmentRequest(
                cycle_id=run.run_id,
                task_type="kubernetes_job",
                owner=run.actor,
                priority="high",
                resource_profile="docker-desktop-gpu",
                requester_team="ml-platform",
                approval_policy="auto",
                config_payload={
                    "adapter": "host-kubectl-bridge",
                    "manifest_dir": str(bundle.manifest_dir),
                    "namespace": bundle.namespace,
                    "job_name": bundle.job_name,
                    "timeout_seconds": int(
                        os.getenv("EVM_LIFECYCLE_TRAINING_TIMEOUT_SECONDS", "7200")
                    ),
                    "delete_existing": True,
                    "lifecycle_run_id": run.run_id,
                },
                dry_run=False,
            )
        )
        run = transition_stage(
            run.run_id,
            "model_training",
            "queued",
            actor="lifecycle-worker",
            task_id=task.task_id,
            runtime_id=f"{bundle.namespace}/job/{bundle.job_name}",
            runtime_state=task.status,
            evidence_uri=str(bundle.manifest_dir),
            detail=f"Kubernetes GPU Job queued for {bundle.candidate_id}",
        )
        stage = stage_for(run, "model_training")
    if task is None:
        raise LifecycleStageBlocked("kubernetes_training_task_missing")
    if task.status in {"queued", "running"}:
        if stage.state == "queued":
            run = transition_stage(
                run.run_id,
                "model_training",
                "running",
                actor="lifecycle-worker",
                task_id=task.task_id,
                runtime_id=f"{bundle.namespace}/job/{bundle.job_name}",
                runtime_state="applying" if task.status == "queued" else task.runtime_state,
                evidence_uri=str(bundle.manifest_dir),
                detail="Docker Desktop Kubernetes GPU training is executing",
            )
        task = execute_kubernetes_task(task.task_id, runner=runner)
    if task.status != "done":
        raise LifecycleStageBlocked(
            f"kubernetes_training_{task.status}",
            [task.failure_reason or f"kubernetes_training_{task.status}"],
        )
    evidence, evidence_path = build_training_evidence(run, task, bundle, runner=runner)
    if evidence["status"] != "pass":
        raise LifecycleStageBlocked(
            "kubernetes_training_evidence_blocked",
            [str(item) for item in evidence["blockers"]],
        )
    return transition_stage(
        run.run_id,
        "model_training",
        "completed",
        actor="lifecycle-worker",
        task_id=task.task_id,
        runtime_id=task.runtime_id,
        runtime_state=task.runtime_state,
        evidence_uri=str(evidence_path),
        detail=(
            f"Real GPU training completed; MLflow run {evidence['mlflow_run_id']}"
        ),
    )


def process_model_evaluation(
    run: LifecycleRun,
    *,
    runner: Runner,
    http_client: HttpClient,
) -> LifecycleRun:
    del runner, http_client
    run = ensure_stage_running(run, "model_evaluation", "Evaluating real model evidence")
    with run_source_environment(run):
        airflow_path = runtime_path(run.airflow_config_uri)
        model_path = runtime_path(run.model_config_uri)
        cycle = build_latest_cycle(airflow_path, model_path)
        report = validate_real_test_evidence(cycle, model_path)
        report_path = configured_evidence_path(run, "real_test_validation")
        write_json(report_path, report)
        if not report.get("valid"):
            violations = [
                str(item.get("code") or "real_test_violation")
                for item in report.get("violations", [])
                if isinstance(item, dict)
            ]
            raise LifecycleStageBlocked("real_test_validation_failed", violations)
        cycle = build_latest_cycle(airflow_path, model_path)
    cycle_path = configured_evidence_path(run, "cycle_snapshot")
    write_json(cycle_path, cycle.model_dump(mode="json"))
    matrix_path = model_matrix_path(run)
    update_run_evidence(
        run.run_id,
        actor="lifecycle-worker",
        cycle_snapshot_uri=str(cycle_path),
        model_matrix_uri=str(matrix_path),
        real_test_validation_uri=str(report_path),
        readiness_uri=(
            cycle.readiness_evaluation.report_uri
            if cycle.readiness_evaluation is not None
            else None
        ),
    )
    if cycle.model_matrix is None or cycle.model_matrix.status != "pass":
        raise LifecycleStageBlocked("model_matrix_not_pass")
    return transition_stage(
        run.run_id,
        "model_evaluation",
        "completed",
        actor="lifecycle-worker",
        runtime_id=cycle.mlflow.run_id,
        runtime_state="FINISHED",
        evidence_uri=str(report_path),
        detail=f"Real-test evidence passed for {cycle.model_matrix.matrix_id}",
    )


def process_artifact_readiness(
    run: LifecycleRun,
    *,
    runner: Runner,
    http_client: HttpClient,
) -> LifecycleRun:
    del runner, http_client
    run = ensure_stage_running(run, "artifact_readiness", "Evaluating immutable artifacts")
    cycle = rebuild_cycle(run)
    evaluation = cycle.readiness_evaluation
    if evaluation is None:
        raise LifecycleStageBlocked("readiness_evaluation_missing")
    update_run_evidence(
        run.run_id,
        actor="lifecycle-worker",
        readiness_uri=evaluation.report_uri,
        cycle_snapshot_uri=str(configured_evidence_path(run, "cycle_snapshot")),
    )
    if evaluation.decision != "ready":
        raise LifecycleStageBlocked("artifact_readiness_blocked", evaluation.blockers)
    return transition_stage(
        run.run_id,
        "artifact_readiness",
        "completed",
        actor="lifecycle-worker",
        runtime_id=evaluation.evaluation_id,
        runtime_state=evaluation.decision,
        evidence_uri=evaluation.report_uri,
        detail=f"Artifact readiness passed with {len(evaluation.checks)} evidence checks",
    )


def process_ci_ct_gate(
    run: LifecycleRun,
    *,
    runner: Runner,
    http_client: HttpClient,
) -> LifecycleRun:
    del runner, http_client
    run = ensure_stage_running(run, "ci_ct_gate", "Validating CI and continuous test evidence")
    if not run.source_commit or len(run.source_commit) != 40:
        raise LifecycleStageBlocked("lifecycle_source_commit_missing")
    cycle = rebuild_cycle(run)
    gate = cycle.cdct_gate
    if not cycle.ci_evidence.valid or gate.status != "pass":
        raise LifecycleStageBlocked(
            "ci_ct_gate_blocked",
            [*cycle.ci_evidence.blockers, *gate.failed_checks, *gate.promotion_blockers],
        )
    return transition_stage(
        run.run_id,
        "ci_ct_gate",
        "completed",
        actor="lifecycle-worker",
        runtime_id=cycle.ci_evidence.workflow_run_id,
        runtime_state="success",
        evidence_uri=cycle.ci_evidence.source_uri,
        detail="CI evidence and isolated CT admission passed",
    )


def process_approval(
    run: LifecycleRun,
    *,
    runner: Runner,
    http_client: HttpClient,
) -> LifecycleRun:
    del runner, http_client
    return transition_stage(
        run.run_id,
        "approval",
        "waiting_approval",
        actor="lifecycle-worker",
        runtime_state="two_person_approval_required",
        detail="Automated gates passed; independent release approval is required",
    )


def process_deployment(
    run: LifecycleRun,
    *,
    runner: Runner,
    http_client: HttpClient,
) -> LifecycleRun:
    del http_client
    if not run.approver:
        raise LifecycleStageBlocked("lifecycle_approver_missing")
    ensure_generated_manifest_root()
    run = ensure_stage_running(run, "deployment", "Applying guarded deployment intent")
    cycle = rebuild_cycle(run)
    serving = materialize_serving_bundle(run, cycle)
    intent = get_intent(run.deployment_intent_id) if run.deployment_intent_id else None
    if intent is None:
        profile = read_json(runtime_path(run.profile_snapshot_uri))
        gates = object_value(profile, "gates")
        intent = create_deployment_intent(
            DeploymentIntentRequest(
                cycle_id=run.run_id,
                target_environment=str(gates.get("target_environment") or "staging"),
                target_namespace=serving.namespace,
                target=ResourceRef(
                    namespace=serving.namespace,
                    kind="Deployment",
                    name=serving.deployment_name,
                ),
                actor=run.actor,
                reason=f"LifecycleRun {run.run_id} guarded deployment",
                dry_run=True,
            ),
            cycle=cycle,
            manifest_ref=serving.manifest_dir,
        )
        update_run_evidence(
            run.run_id,
            actor="lifecycle-worker",
            deployment_intent_id=intent.intent_id,
        )
    if intent.state == "dry_run":
        intent = request_approval(
            intent.intent_id,
            DeploymentTransitionRequest(
                actor=run.actor,
                reason="LifecycleRun automated gates passed",
                expected_version=intent.version,
            ),
        )
    if intent.state == "pending_approval" and not intent.approver:
        intent = approve_intent(
            intent.intent_id,
            DeploymentTransitionRequest(
                actor=run.approver,
                reason="LifecycleRun independent approval recorded",
                expected_version=intent.version,
            ),
        )
    if intent.state == "pending_approval":
        intent = queue_intent(
            intent.intent_id,
            DeploymentTransitionRequest(
                actor=run.approver,
                reason="Queue approved LifecycleRun deployment",
                expected_version=intent.version,
            ),
            cycle=cycle,
        )
    if intent.state == "queued":
        intent = execute_apply(
            intent.intent_id,
            runner=runner,
            require_enabled=False,
            cycle=cycle,
        )
    if intent.state != "applied":
        failed = transition_stage(
            run.run_id,
            "deployment",
            "failed",
            actor="lifecycle-worker",
            runtime_id=intent.intent_id,
            runtime_state=intent.state,
            evidence_uri=intent.audit_uri,
            detail=f"Deployment intent ended in {intent.state}",
            blockers=[f"deployment_intent_{intent.state}"],
        )
        return rollback_lifecycle(failed, intent.intent_id, runner, "deployment_apply_failed")
    return transition_stage(
        run.run_id,
        "deployment",
        "completed",
        actor="lifecycle-worker",
        runtime_id=intent.intent_id,
        runtime_state=intent.state,
        evidence_uri=intent.audit_uri,
        detail=f"Deployment {serving.namespace}/{serving.deployment_name} applied",
    )


def process_serving_validation(
    run: LifecycleRun,
    *,
    runner: Runner,
    http_client: HttpClient,
) -> LifecycleRun:
    run = ensure_stage_running(run, "serving_validation", "Probing deployed real model")
    cycle = rebuild_cycle(run)
    serving = materialize_serving_bundle(run, cycle)
    sample_uri = first_sample_uri(run)
    ready_status, ready_payload = http_client("GET", f"{serving.endpoint}/ready", timeout=15)
    predict_status, prediction = http_client(
        "POST",
        f"{serving.endpoint}/predict",
        payload={"image_uri": sample_uri},
        timeout=60,
    )
    expected_candidate = cycle.readiness_evaluation.candidate_id if cycle.readiness_evaluation else ""
    expected_digest = model_digest(cycle)
    blockers: list[str] = []
    if ready_status != 200 or not isinstance(ready_payload, dict):
        blockers.append("serving_readiness_failed")
    elif (
        ready_payload.get("candidate_id") != expected_candidate
        or ready_payload.get("model_sha256") != expected_digest
    ):
        blockers.append("serving_readiness_identity_mismatch")
    if predict_status != 200 or not isinstance(prediction, dict):
        blockers.append("serving_prediction_failed")
    elif (
        prediction.get("candidate_id") != expected_candidate
        or prediction.get("model_sha256") != expected_digest
        or not str(prediction.get("device") or "").startswith("cuda")
    ):
        blockers.append("serving_prediction_identity_mismatch")
    evidence = {
        "schema_version": "evm.lifecycle_serving_validation.v1",
        "run_id": run.run_id,
        "endpoint": serving.endpoint,
        "sample_uri": sample_uri,
        "ready_status": ready_status,
        "ready": ready_payload,
        "predict_status": predict_status,
        "prediction": prediction,
        "blockers": blockers,
        "status": "pass" if not blockers else "blocked",
    }
    path = Path(run.artifact_root) / "serving" / "validation.json"
    write_json(path, evidence)
    if blockers:
        failed = transition_stage(
            run.run_id,
            "serving_validation",
            "failed",
            actor="lifecycle-worker",
            runtime_id=serving.deployment_name,
            runtime_state="probe_failed",
            evidence_uri=str(path),
            detail="Serving identity or CUDA inference validation failed",
            blockers=blockers,
        )
        if not run.deployment_intent_id:
            return failed
        return rollback_lifecycle(
            failed,
            run.deployment_intent_id,
            runner,
            "serving_validation_failed",
        )
    return transition_stage(
        run.run_id,
        "serving_validation",
        "completed",
        actor="lifecycle-worker",
        runtime_id=serving.deployment_name,
        runtime_state="ready",
        evidence_uri=str(path),
        detail=f"CUDA inference passed at {serving.endpoint}",
    )


def process_monitoring(
    run: LifecycleRun,
    *,
    runner: Runner,
    http_client: HttpClient,
) -> LifecycleRun:
    del runner
    run = ensure_stage_running(run, "monitoring", "Registering and validating Prometheus scrape")
    cycle = rebuild_cycle(run)
    serving = materialize_serving_bundle(run, cycle)
    write_prometheus_target(run, serving)
    metrics_status, metrics_payload = http_client(
        "GET", f"{serving.endpoint}/metrics", timeout=15
    )
    target_payload: dict[str, Any] | list[Any] | str = {}
    target_status = 0
    deadline = time.monotonic() + float(
        os.getenv("EVM_LIFECYCLE_PROMETHEUS_WAIT_SECONDS", "45")
    )
    while time.monotonic() <= deadline:
        target_status, target_payload = http_client(
            "GET",
            f"{os.getenv('EVM_PROMETHEUS_URL', 'http://127.0.0.1:9090').rstrip('/')}/api/v1/targets",
            timeout=10,
        )
        if prometheus_target_up(target_payload, serving.endpoint):
            break
        time.sleep(3)
    blockers: list[str] = []
    metrics_text = metrics_payload if isinstance(metrics_payload, str) else json.dumps(metrics_payload)
    if metrics_status != 200 or "evm_serving_model_loaded" not in metrics_text:
        blockers.append("serving_metrics_endpoint_invalid")
    if target_status != 200 or not prometheus_target_up(target_payload, serving.endpoint):
        blockers.append("prometheus_target_not_up")
    evidence = {
        "schema_version": "evm.lifecycle_monitoring_validation.v1",
        "run_id": run.run_id,
        "endpoint": serving.endpoint,
        "metrics_status": metrics_status,
        "prometheus_status": target_status,
        "target_up": prometheus_target_up(target_payload, serving.endpoint),
        "blockers": blockers,
        "status": "pass" if not blockers else "blocked",
    }
    path = Path(run.artifact_root) / "monitoring" / "validation.json"
    write_json(path, evidence)
    if blockers:
        raise LifecycleStageBlocked("monitoring_validation_blocked", blockers)
    return transition_stage(
        run.run_id,
        "monitoring",
        "completed",
        actor="lifecycle-worker",
        runtime_id="prometheus",
        runtime_state="up",
        evidence_uri=str(path),
        detail="Prometheus is scraping the deployed lifecycle model",
    )


STAGE_HANDLERS = {
    "data_pipeline": process_data_pipeline,
    "model_training": process_model_training,
    "model_evaluation": process_model_evaluation,
    "artifact_readiness": process_artifact_readiness,
    "ci_ct_gate": process_ci_ct_gate,
    "approval": process_approval,
    "deployment": process_deployment,
    "serving_validation": process_serving_validation,
    "monitoring": process_monitoring,
}


def rebuild_cycle(run: LifecycleRun) -> CycleRun:
    with run_source_environment(run):
        cycle = build_latest_cycle(
            runtime_path(run.airflow_config_uri),
            runtime_path(run.model_config_uri),
        )
    path = configured_evidence_path(run, "cycle_snapshot")
    write_json(path, cycle.model_dump(mode="json"))
    update_run_evidence(
        run.run_id,
        actor="lifecycle-worker",
        cycle_snapshot_uri=str(path),
        readiness_uri=(cycle.readiness_evaluation.report_uri if cycle.readiness_evaluation else None),
    )
    return cycle


def rollback_lifecycle(
    run: LifecycleRun,
    intent_id: str,
    runner: Runner,
    reason: str,
) -> LifecycleRun:
    mark_lifecycle_rollback(
        run.run_id,
        actor="lifecycle-worker",
        state="rolling_back",
        detail=reason,
    )
    try:
        intent = execute_rollback(intent_id, runner=runner, require_enabled=False)
    except Exception as exc:
        return mark_lifecycle_rollback_failed(
            run.run_id,
            actor="lifecycle-worker",
            detail=f"{type(exc).__name__}: {exc}",
        )
    if intent.state != "rolled_back":
        return mark_lifecycle_rollback_failed(
            run.run_id,
            actor="lifecycle-worker",
            detail=f"Deployment intent rollback ended in {intent.state}",
        )
    return mark_lifecycle_rollback(
        run.run_id,
        actor="lifecycle-worker",
        state="rolled_back",
        detail=f"Exact rollback completed by {intent.intent_id}",
    )


def required_run(run_id: str) -> LifecycleRun:
    run = get_lifecycle_run(run_id)
    if run is None:
        raise LifecycleRunError(
            "lifecycle_run_not_found",
            f"LifecycleRun {run_id} was not found.",
            status_code=404,
        )
    return run


def stage_for(run: LifecycleRun, stage_id: str):
    return next(item for item in run.stages if item.stage_id == stage_id)


def task_for(task_id: str | None) -> TaskAssignment | None:
    if not task_id:
        return None
    return next((item for item in read_tasks().tasks if item.task_id == task_id), None)


def ensure_stage_running(run: LifecycleRun, stage_id: str, detail: str) -> LifecycleRun:
    stage = stage_for(run, stage_id)
    if stage.state == "not_started":
        return transition_stage(
            run.run_id,
            stage_id,
            "running",
            actor="lifecycle-worker",
            detail=detail,
        )
    if stage.state != "running":
        raise LifecycleStageBlocked(f"stage_not_runnable:{stage.state}")
    return run


def block_stage(run: LifecycleRun, detail: str, blockers: list[str]) -> LifecycleRun:
    if not run.current_stage:
        return run
    stage = stage_for(run, run.current_stage)
    if stage.state not in {"not_started", "queued", "running"}:
        return run
    return transition_stage(
        run.run_id,
        stage.stage_id,
        "blocked",
        actor="lifecycle-worker",
        detail=detail,
        blockers=blockers,
    )


def fail_stage(run: LifecycleRun, detail: str, blockers: list[str]) -> LifecycleRun:
    if not run.current_stage:
        return run
    stage = stage_for(run, run.current_stage)
    if stage.state not in {"queued", "running"}:
        return block_stage(run, detail, blockers)
    return transition_stage(
        run.run_id,
        stage.stage_id,
        "failed",
        actor="lifecycle-worker",
        detail=detail,
        blockers=blockers,
    )


def configured_evidence_path(run: LifecycleRun, key: str) -> Path:
    model = read_json(runtime_path(run.model_config_uri))
    value = object_value(object_value(model, "control_plane"), "runtime_evidence").get(key)
    if not value:
        raise ValueError(f"runtime_evidence_path_missing:{key}")
    return runtime_path(str(value))


def model_matrix_path(run: LifecycleRun) -> Path:
    model = read_json(runtime_path(run.model_config_uri))
    root = runtime_path(str(object_value(model, "resources").get("artifact_root") or ""))
    return root / "latest_model_matrix.json"


def model_digest(cycle: CycleRun) -> str:
    evaluation = cycle.readiness_evaluation
    if evaluation is None:
        return ""
    check = next(
        (item for item in evaluation.checks if item.check_id == "model_artifact"),
        None,
    )
    return str(check.observed.get("actual_sha256") or "") if check else ""


def first_sample_uri(run: LifecycleRun) -> str:
    profile = read_json(runtime_path(run.profile_snapshot_uri))
    source = runtime_path(str(object_value(profile, "data").get("source_manifest_uri") or ""))
    if not source.is_file():
        raise LifecycleStageBlocked("serving_sample_manifest_missing")
    with source.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict) and payload.get("image_uri"):
                return str(payload["image_uri"])
    raise LifecycleStageBlocked("serving_sample_image_missing")


def write_prometheus_target(run: LifecycleRun, serving: ServingBundle) -> Path:
    target = serving.endpoint.removeprefix("http://").removeprefix("https://")
    host, port = target.rsplit(":", 1)
    if host in {"127.0.0.1", "localhost"}:
        target = f"host.docker.internal:{port}"
    path = Path(
        os.getenv(
            "EVM_PROMETHEUS_FILE_SD_PATH",
            f"{os.getenv('EVM_HOST_DATA_ROOT', 'F:/EnterpriseMLOps_Data/enterprise-vision-mlops')}/artifacts/w7/prometheus-targets/lifecycle-serving.json",
        )
    )
    write_json(
        path,
        [
            {
                "targets": [target],
                "labels": {
                    "job": "evm-lifecycle-serving",
                    "lifecycle_run_id": run.run_id,
                    "namespace": serving.namespace,
                    "deployment": serving.deployment_name,
                },
            }
        ],
    )
    return path


def prometheus_target_up(payload: object, endpoint: str) -> bool:
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    endpoint_target = endpoint.removeprefix("http://").removeprefix("https://")
    port = endpoint_target.rsplit(":", 1)[-1]
    for item in data.get("activeTargets", []):
        if not isinstance(item, dict):
            continue
        scrape_url = str(item.get("scrapeUrl") or "")
        if f":{port}/metrics" in scrape_url and item.get("health") == "up":
            return True
    return False


def ensure_generated_manifest_root() -> None:
    os.environ.setdefault("EVM_KUBERNETES_GENERATED_MANIFEST_ROOT", str(lifecycle_root()))


@contextmanager
def run_source_environment(run: LifecycleRun):
    previous = os.environ.get("EVM_EXPECTED_CI_COMMIT")
    if run.source_commit:
        os.environ["EVM_EXPECTED_CI_COMMIT"] = run.source_commit
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("EVM_EXPECTED_CI_COMMIT", None)
        else:
            os.environ["EVM_EXPECTED_CI_COMMIT"] = previous


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def object_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
