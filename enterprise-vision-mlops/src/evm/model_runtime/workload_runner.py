from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TextIO

import requests

from evm.control_panel.operations import read_tasks, sync_running_tasks
from evm.control_panel.scenario_workloads import (
    ScenarioWorkloadError,
    ScenarioWorkloadRequest,
    acquire_gpu_lease,
    assert_gpu_lease_owner,
    create_workload_run,
    fail_workload_run,
    get_workload_run,
    release_gpu_lease,
    seal_workload_run,
    transition_workload_stage,
    update_workload_results,
    workload_artifact_path,
)
from evm.control_panel.scenario_workload_control import read_staging_approval
from evm.control_panel.scenarios import ScenarioIntakeLaunchRequest, launch_scenario_intake
from evm.model_runtime.common import (
    ModelRuntimeError,
    atomic_write_json,
    file_sha256,
    payload_sha256,
    read_jsonl,
    split_records,
    utc_now,
)
from evm.model_runtime.llm import QwenTrainingConfig, train_qwen_qlora
from evm.model_runtime.vlm import SmolVlmTrainingConfig, train_smolvlm_lora
from evm.model_runtime.workload_gpu_handoff import (
    acquire_workload_gpu_handoff,
    release_workload_gpu_handoff,
)


ModelFamily = Literal["vlm", "llm"]


@dataclass(frozen=True)
class ScenarioExecutionConfig:
    scenario_id: str
    model_family: ModelFamily
    model_repository: str
    model_revision: str
    model_dir: Path
    data_view_uri: str
    source_commit: str
    source_branch: str
    actor: str
    reason: str
    staging_approver: str
    staging_reason: str
    serving_port: int
    max_steps: int
    quality_disposition_uri: str | None = None
    quantization_requested: str = "none"
    airflow_timeout_seconds: int = 900
    serving_timeout_seconds: int = 90
    prometheus_timeout_seconds: int = 60
    mlflow_tracking_uri: str = "http://127.0.0.1:5000"
    prometheus_uri: str = "http://127.0.0.1:9090"
    prometheus_target_path: Path = Path(
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/"
        "artifacts/w7/prometheus-targets/lifecycle-serving.json"
    )
    run_id: str | None = None
    external_staging_approval: bool = False
    approval_timeout_seconds: int = 3600
    external_gpu_handoff: bool = False


@dataclass
class RunningServer:
    process: subprocess.Popen[str]
    log_handle: TextIO
    log_path: Path
    endpoint: str


def run_real_scenario_lifecycle(config: ScenarioExecutionConfig) -> dict[str, Any]:
    model_bundle = verify_base_model_bundle(config)
    if not port_available(config.serving_port):
        raise ModelRuntimeError(f"serving_port_unavailable:{config.serving_port}")
    if config.run_id:
        run = get_workload_run(config.run_id)
        assert_requested_run_identity(config, run)
    else:
        request = ScenarioWorkloadRequest(
            scenario_id=config.scenario_id,
            model_family=config.model_family,
            model_repository=config.model_repository,
            model_revision=config.model_revision,
            adaptation_method="lora" if config.model_family == "vlm" else "qlora",
            quantization_requested=config.quantization_requested,  # type: ignore[arg-type]
            actor=config.actor,
            reason=config.reason,
            dry_run=False,
            source_commit=config.source_commit,
            source_branch=config.source_branch,
            dirty_worktree=False,
            quality_disposition_uri=config.quality_disposition_uri,
            data_view_uri=config.data_view_uri,
        )
        run = create_workload_run(request)
    lease = None
    gpu_handoff_path: Path | None = None
    server: RunningServer | None = None
    target_backup = read_optional_bytes(config.prometheus_target_path)
    active_stage = "data_intake"
    try:
        start_stage(run.run_id, "data_intake")
        intake = run_airflow_intake(config)
        finish_stage(run.run_id, "data_intake", intake, detail="Airflow intake completed")
        active_stage = "identity_quality_gate"
        identity_evidence = {
            "schema_version": "evm.scenario_identity_quality_evidence.v1",
            "status": "pass",
            "run_id": run.run_id,
            "identity": run.identity.model_dump(mode="json"),
            "verified_files": {
                "manifest": verify_file(run.identity.manifest_uri, run.identity.manifest_sha256),
                "split_manifest": verify_file(
                    run.identity.split_manifest_uri,
                    run.identity.split_manifest_sha256,
                ),
            },
            "base_model_bundle": model_bundle,
            "observed_at": utc_now(),
        }
        complete_stage(
            run.run_id,
            "identity_quality_gate",
            identity_evidence,
            detail="Exact data, model, and source identities verified",
        )
        active_stage = "gpu_lease"
        start_stage(run.run_id, active_stage)
        if config.external_gpu_handoff:
            gpu_handoff_path = acquire_workload_gpu_handoff(
                run,
                timeout_seconds=config.approval_timeout_seconds,
            )
        lease = acquire_gpu_lease(run.run_id, owner_pid=os.getpid(), ttl_seconds=7200)
        lease_evidence = {
            "schema_version": "evm.scenario_gpu_lease_evidence.v1",
            "status": "pass",
            "lease": lease.model_dump(mode="json"),
            "exclusive_target": "cuda:0",
            "observed_at": utc_now(),
        }
        finish_stage(run.run_id, active_stage, lease_evidence, "Exclusive GPU lease acquired")
        assert_gpu_lease_owner(run.run_id)

        active_stage = "adaptation"
        start_stage(run.run_id, active_stage)
        model_root = workload_artifact_path(run.artifact_root) / "model"
        training = train_model(config, run.run_id, run.identity.data_identity_sha256, model_root)
        if training["status"] != "pass":
            raise ModelRuntimeError(
                "training_quality_blocked:" + ",".join(training.get("promotion_blockers") or [])
            )
        finish_stage(
            run.run_id,
            active_stage,
            training,
            f"Real {config.model_family.upper()} adapter training completed on CUDA",
            evidence_uri=str(model_root / "training-result.json"),
        )
        run = update_workload_results(
            run.run_id,
            actor="scenario-workload-runtime",
            mlflow_run_id=training["mlflow_run_id"],
            model_artifact_uri=training["model_artifact_uri"],
            model_artifact_sha256=training["model_artifact_sha256"],
            evaluation_uri=training["evaluation_uri"],
            runtime_versions=training["runtime_versions"],
            peak_gpu_allocated_mib=training["peak_gpu_allocated_mib"],
            peak_gpu_reserved_mib=training["peak_gpu_reserved_mib"],
            quantization_observed=training["quantization"],
        )

        active_stage = "experiment_tracking"
        mlflow_evidence = verify_mlflow_run(
            config.mlflow_tracking_uri,
            training["mlflow_run_id"],
            expected_artifacts={"adapter_model.safetensors", "adapted-evaluation.json"},
        )
        complete_stage(
            run.run_id,
            active_stage,
            mlflow_evidence,
            detail="MLflow run and uploaded artifacts verified",
        )

        active_stage = "isolated_evaluation"
        evaluation = json.loads(Path(training["evaluation_uri"]).read_text(encoding="utf-8"))
        complete_stage(
            run.run_id,
            active_stage,
            {
                "schema_version": "evm.scenario_isolated_evaluation_evidence.v1",
                "status": "pass",
                "run_id": run.run_id,
                "evaluation": evaluation,
                "promotion_blockers": training["promotion_blockers"],
                "observed_at": utc_now(),
            },
            detail="Held-out bounded evaluation passed local guardrails",
        )

        active_stage = "artifact_seal"
        artifact_digest = file_sha256(Path(training["model_artifact_uri"]))
        if artifact_digest != training["model_artifact_sha256"]:
            raise ModelRuntimeError("model_artifact_digest_changed")
        complete_stage(
            run.run_id,
            active_stage,
            {
                "schema_version": "evm.scenario_artifact_seal_evidence.v1",
                "status": "pass",
                "run_id": run.run_id,
                "artifact_uri": training["model_artifact_uri"],
                "artifact_sha256": artifact_digest,
                "identity_sha256": run.identity.identity_sha256,
                "sealed_at": utc_now(),
            },
            detail="Adapter bytes re-hashed before staging approval",
        )

        active_stage = "approval"
        start_stage(run.run_id, active_stage, waiting=True)
        approval = (
            wait_for_staging_approval(config, run)
            if config.external_staging_approval
            else staging_approval(config, run, artifact_digest)
        )
        finish_stage(
            run.run_id,
            active_stage,
            approval,
            "Identity-bound local staging approval recorded",
        )

        active_stage = "staging_serving"
        start_stage(run.run_id, active_stage)
        assert_gpu_lease_owner(run.run_id)
        server = start_staging_server(config, run, training)
        serving = verify_staging_inference(config, run, server.endpoint)
        finish_stage(
            run.run_id,
            active_stage,
            serving,
            "Exact adapter served and real CUDA inference completed",
        )
        run = update_workload_results(
            run.run_id,
            actor="scenario-workload-runtime",
            serving_endpoint=server.endpoint,
            metrics_endpoint=f"{server.endpoint}/metrics",
        )

        active_stage = "observability"
        start_stage(run.run_id, active_stage)
        write_prometheus_target(config.prometheus_target_path, config, run)
        observability = verify_observability(config, run, server.endpoint)
        stop_staging_server(server)
        server = None
        restore_optional_bytes(config.prometheus_target_path, target_backup)
        observability["staging_runtime_state"] = "retired_after_validation"
        observability["target_cleanup"] = "restored"
        finish_stage(
            run.run_id,
            active_stage,
            observability,
            "Prometheus scrape passed; bounded staging runtime retired",
        )
        run = update_workload_results(
            run.run_id,
            actor="scenario-workload-runtime",
            runtime_versions={
                **run.runtime_versions,
                "staging_runtime_state": "retired_after_validation",
            },
        )
        release_gpu_lease(
            run.run_id,
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token,
            reason="scenario_staging_validation_completed",
        )
        lease = None
        if gpu_handoff_path is not None:
            release_workload_gpu_handoff(
                run,
                gpu_handoff_path,
                reason="scenario_staging_validation_completed",
            )
            gpu_handoff_path = None
        completed = seal_workload_run(run.run_id, actor=config.actor)
        return completed.model_dump(mode="json")
    except Exception as exc:
        failure_path = workload_artifact_path(run.artifact_root) / "failure.json"
        atomic_write_json(
            failure_path,
            {
                "schema_version": "evm.scenario_workload_failure.v1",
                "run_id": run.run_id,
                "active_stage": active_stage,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "failed_at": utc_now(),
            },
        )
        mark_stage_failed(run.run_id, active_stage, failure_path, exc)
        if server is not None:
            stop_staging_server(server)
        restore_optional_bytes(config.prometheus_target_path, target_backup)
        if lease is not None:
            try:
                release_gpu_lease(
                    run.run_id,
                    lease_id=lease.lease_id,
                    fencing_token=lease.fencing_token,
                    reason=f"scenario_failed:{active_stage}",
                )
            except ScenarioWorkloadError:
                pass
        if gpu_handoff_path is not None:
            try:
                release_workload_gpu_handoff(
                    run,
                    gpu_handoff_path,
                    reason=f"scenario_failed:{active_stage}",
                )
            except (ModelRuntimeError, OSError, ValueError):
                pass
        raise


def verify_base_model_bundle(config: ScenarioExecutionConfig) -> dict[str, Any]:
    if not config.model_dir.is_dir():
        raise ModelRuntimeError(f"base_model_missing:{config.model_dir}")
    if config.model_dir.name != config.model_revision:
        raise ModelRuntimeError(
            "base_model_revision_path_mismatch:"
            f"expected={config.model_revision}:actual={config.model_dir.name}"
        )

    common = ["config.json"]
    family_files = (
        ["preprocessor_config.json", "processor_config.json", "tokenizer_config.json"]
        if config.model_family == "vlm"
        else ["tokenizer_config.json"]
    )
    missing = [name for name in [*common, *family_files] if not (config.model_dir / name).is_file()]
    weight_files = sorted(config.model_dir.glob("*.safetensors"))
    if not weight_files:
        missing.append("*.safetensors")
    if missing:
        raise ModelRuntimeError("base_model_bundle_incomplete:" + ",".join(missing))

    verified_paths = [*(config.model_dir / name for name in [*common, *family_files]), *weight_files]
    return {
        "status": "pass",
        "model_repository": config.model_repository,
        "model_revision": config.model_revision,
        "model_dir": str(config.model_dir),
        "files": {
            path.name: {"size_bytes": path.stat().st_size, "sha256": file_sha256(path)}
            for path in verified_paths
        },
    }


def assert_requested_run_identity(config: ScenarioExecutionConfig, run: Any) -> None:
    expected = {
        "scenario_id": config.scenario_id,
        "model_family": config.model_family,
        "model_repository": config.model_repository,
        "model_revision": config.model_revision,
        "source_commit": config.source_commit,
        "source_branch": config.source_branch,
        "data_view_uri": config.data_view_uri,
    }
    actual = {
        "scenario_id": run.identity.scenario_id,
        "model_family": run.identity.model_family,
        "model_repository": run.identity.model_repository,
        "model_revision": run.identity.model_revision,
        "source_commit": run.identity.source_commit,
        "source_branch": run.identity.source_branch,
        "data_view_uri": run.identity.data_view_uri,
    }
    mismatches = [key for key, value in expected.items() if actual.get(key) != value]
    if run.state != "queued":
        mismatches.append("state")
    if mismatches:
        raise ModelRuntimeError("scenario_execution_request_mismatch:" + ",".join(mismatches))


def run_airflow_intake(config: ScenarioExecutionConfig) -> dict[str, Any]:
    task = launch_scenario_intake(
        config.scenario_id,
        ScenarioIntakeLaunchRequest(
            actor=config.actor,
            reason=f"{config.reason}; exact source {config.source_commit}",
            dry_run=False,
            source_commit=config.source_commit,
        ),
    )
    deadline = time.monotonic() + config.airflow_timeout_seconds
    while task.status not in {"done", "failed", "cancelled"}:
        if time.monotonic() >= deadline:
            raise ModelRuntimeError(f"airflow_intake_timeout:{task.task_id}")
        time.sleep(2)
        tasks = sync_running_tasks(limit=50)
        found = next((item for item in tasks.tasks if item.task_id == task.task_id), None)
        if found is None:
            found = next(
                (item for item in read_tasks().tasks if item.task_id == task.task_id), None
            )
        if found is None:
            raise ModelRuntimeError(f"airflow_task_missing:{task.task_id}")
        task = found
    if task.status != "done" or task.runtime_state != "success":
        raise ModelRuntimeError(
            f"airflow_intake_failed:{task.task_id}:{task.runtime_state or task.status}"
        )
    return {
        "schema_version": "evm.scenario_airflow_intake_evidence.v1",
        "status": "pass",
        "task_id": task.task_id,
        "dag_run_id": task.runtime_id,
        "runtime_state": task.runtime_state,
        "runtime_url": task.runtime_url,
        "source_commit": config.source_commit,
        "finished_at": task.finished_at,
        "observed_at": utc_now(),
    }


def train_model(
    config: ScenarioExecutionConfig,
    run_id: str,
    data_identity_sha256: str,
    output_dir: Path,
) -> dict[str, Any]:
    run = get_workload_run(run_id)
    shared = {
        "model_dir": config.model_dir,
        "manifest_path": Path(run.identity.manifest_uri),
        "output_dir": output_dir,
        "model_repository": config.model_repository,
        "model_revision": config.model_revision,
        "data_identity_sha256": data_identity_sha256,
        "source_commit": config.source_commit,
        "lifecycle_run_id": run_id,
        "max_steps": config.max_steps,
        "mlflow_tracking_uri": config.mlflow_tracking_uri,
        "progress_path": output_dir / "training-progress.json",
    }
    if config.model_family == "vlm":
        return train_smolvlm_lora(SmolVlmTrainingConfig(**shared))
    return train_qwen_qlora(QwenTrainingConfig(**shared))


def start_stage(run_id: str, stage_id: str, *, waiting: bool = False) -> None:
    transition_workload_stage(
        run_id,
        stage_id,
        "waiting_approval" if waiting else "running",
        actor="scenario-workload-runtime",
        detail="Awaiting an identity-bound staging decision" if waiting else "Stage executing",
    )


def complete_stage(
    run_id: str,
    stage_id: str,
    payload: dict[str, Any],
    detail: str,
) -> None:
    start_stage(run_id, stage_id)
    finish_stage(run_id, stage_id, payload, detail)


def finish_stage(
    run_id: str,
    stage_id: str,
    payload: dict[str, Any],
    detail: str,
    *,
    evidence_uri: str | None = None,
) -> None:
    path = Path(evidence_uri) if evidence_uri else Path(get_workload_run(run_id).artifact_root) / (
        f"{stage_id}-evidence.json"
    )
    if evidence_uri is None:
        atomic_write_json(path, payload)
    transition_workload_stage(
        run_id,
        stage_id,
        "completed",
        actor="scenario-workload-runtime",
        evidence_uri=str(path),
        detail=detail,
    )


def mark_stage_failed(run_id: str, stage_id: str, path: Path, exc: Exception) -> None:
    try:
        run = get_workload_run(run_id)
        stage = next(item for item in run.stages if item.stage_id == stage_id)
        if stage.state in {"completed", "skipped"}:
            fail_workload_run(
                run_id,
                actor="scenario-workload-runtime",
                blocker=f"{type(exc).__name__}:{exc}",
                evidence_uri=str(path),
            )
            return
        target_state = "blocked" if stage.state == "waiting_approval" else "failed"
        transition_workload_stage(
            run_id,
            stage_id,
            target_state,
            actor="scenario-workload-runtime",
            evidence_uri=str(path),
            detail=str(exc),
            blockers=[f"{type(exc).__name__}:{exc}"],
        )
    except (OSError, ValueError, ScenarioWorkloadError):
        pass


def staging_approval(
    config: ScenarioExecutionConfig,
    run: Any,
    artifact_sha256: str,
) -> dict[str, Any]:
    material = {
        "run_id": run.run_id,
        "identity_sha256": run.identity.identity_sha256,
        "artifact_sha256": artifact_sha256,
        "source_commit": config.source_commit,
        "target_environment": "local-staging",
        "action": "start_bounded_validation_server",
    }
    return {
        "schema_version": "evm.scenario_staging_approval.v1",
        "decision": "approved",
        **material,
        "action_digest": payload_sha256(material),
        "approver": config.staging_approver,
        "reason": config.staging_reason,
        "production_promotion": False,
        "approved_at": utc_now(),
        "claim_boundary": "Single-operator approval for controlled local staging only.",
    }


def wait_for_staging_approval(
    config: ScenarioExecutionConfig,
    run: Any,
) -> dict[str, Any]:
    deadline = time.monotonic() + config.approval_timeout_seconds
    while time.monotonic() < deadline:
        approval = read_staging_approval(run.run_id)
        if approval is not None:
            return approval
        time.sleep(2)
    raise ModelRuntimeError("scenario_staging_approval_timeout")


def start_staging_server(
    config: ScenarioExecutionConfig,
    run: Any,
    training: dict[str, Any],
) -> RunningServer:
    log_path = workload_artifact_path(run.artifact_root) / "staging-serving.log"
    log_handle = log_path.open("w", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "evm.model_runtime.serving",
        "--model-family",
        config.model_family,
        "--base-model-dir",
        str(config.model_dir),
        "--adapter-dir",
        str(Path(training["model_artifact_uri"]).parent),
        "--model-repository",
        config.model_repository,
        "--model-revision",
        config.model_revision,
        "--model-artifact-sha256",
        training["model_artifact_sha256"],
        "--data-identity-sha256",
        run.identity.data_identity_sha256,
        "--source-commit",
        config.source_commit,
        "--lifecycle-run-id",
        run.run_id,
        "--quantization",
        training["quantization"],
        "--port",
        str(config.serving_port),
    ]
    process = subprocess.Popen(
        command,
        cwd=repository_root(),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    server = RunningServer(
        process=process,
        log_handle=log_handle,
        log_path=log_path,
        endpoint=f"http://127.0.0.1:{config.serving_port}",
    )
    deadline = time.monotonic() + config.serving_timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log_handle.flush()
            raise ModelRuntimeError(
                f"staging_server_exited:{process.returncode}:{tail_text(log_path)}"
            )
        try:
            response = requests.get(f"{server.endpoint}/ready", timeout=3)
            if response.status_code == 200:
                ready = response.json()
                assert_ready_identity(run, training, ready)
                return server
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1)
    stop_staging_server(server)
    raise ModelRuntimeError(f"staging_server_timeout:{tail_text(log_path)}")


def verify_staging_inference(
    config: ScenarioExecutionConfig,
    run: Any,
    endpoint: str,
) -> dict[str, Any]:
    records = split_records(read_jsonl(Path(run.identity.manifest_uri)))
    record = records["test"][0]
    if config.model_family == "vlm":
        request = {
            "model_family": "vlm",
            "image_uri": record["image_uri"],
            "image_sha256": record["image_sha256"],
            "question": record["question"],
            "choices": record["choices"],
            "max_new_tokens": 8,
        }
    else:
        request = {
            "model_family": "llm",
            "instruction": record["instruction"],
            "context": record.get("context") or None,
            "max_new_tokens": 64,
        }
    response = requests.post(f"{endpoint}/infer", json=request, timeout=90)
    if response.status_code != 200:
        raise ModelRuntimeError(f"staging_inference_failed:{response.status_code}:{response.text}")
    payload = response.json()
    if payload.get("model_artifact_sha256") != run.model_artifact_sha256:
        raise ModelRuntimeError("staging_inference_identity_mismatch")
    if not str(payload.get("output") or "").strip():
        raise ModelRuntimeError("staging_inference_output_empty")
    return {
        "schema_version": "evm.scenario_staging_serving_evidence.v1",
        "status": "pass",
        "run_id": run.run_id,
        "endpoint": endpoint,
        "request_sample_id": record["sample_id"],
        "response": payload,
        "real_cuda": True,
        "production_mutation": False,
        "observed_at": utc_now(),
    }


def verify_observability(
    config: ScenarioExecutionConfig,
    run: Any,
    endpoint: str,
) -> dict[str, Any]:
    raw = requests.get(f"{endpoint}/metrics", timeout=10)
    if raw.status_code != 200 or "evm_scenario_model_info" not in raw.text:
        raise ModelRuntimeError("staging_metrics_contract_missing")
    deadline = time.monotonic() + config.prometheus_timeout_seconds
    query = (
        'up{job="evm-lifecycle-serving",evm_model_family="'
        f'{config.model_family}",evm_environment="local-staging"'
        "}"
    )
    expected_instance = f"host.docker.internal:{config.serving_port}"
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = requests.get(
            f"{config.prometheus_uri.rstrip('/')}/api/v1/query",
            params={"query": query},
            timeout=10,
        )
        if response.status_code == 200:
            last_payload = response.json()
            results = last_payload.get("data", {}).get("result", [])
            matching = [
                item
                for item in results
                if item.get("metric", {}).get("instance") == expected_instance
                and item.get("value", [None, "0"])[1] == "1"
            ]
            if len(matching) == 1:
                return {
                    "schema_version": "evm.scenario_observability_evidence.v1",
                    "status": "pass",
                    "run_id": run.run_id,
                    "metrics_endpoint": f"{endpoint}/metrics",
                    "prometheus_query": query,
                    "prometheus_result": matching,
                    "expected_instance": expected_instance,
                    "identity_metric_present": True,
                    "observed_at": utc_now(),
                }
        time.sleep(2)
    raise ModelRuntimeError(
        f"prometheus_scenario_target_not_up:{json.dumps(last_payload, ensure_ascii=True)}"
    )


def verify_mlflow_run(
    tracking_uri: str,
    run_id: str,
    *,
    expected_artifacts: set[str],
) -> dict[str, Any]:
    run_response = requests.get(
        f"{tracking_uri.rstrip('/')}/api/2.0/mlflow/runs/get",
        params={"run_id": run_id},
        timeout=15,
    )
    if run_response.status_code != 200:
        raise ModelRuntimeError(f"mlflow_run_read_failed:{run_response.status_code}")
    run_payload = run_response.json().get("run", {})
    if run_payload.get("info", {}).get("status") != "FINISHED":
        raise ModelRuntimeError("mlflow_run_not_finished")
    artifact_response = requests.get(
        f"{tracking_uri.rstrip('/')}/api/2.0/mlflow/artifacts/list",
        params={"run_id": run_id, "path": "evidence"},
        timeout=15,
    )
    if artifact_response.status_code != 200:
        raise ModelRuntimeError(f"mlflow_artifact_list_failed:{artifact_response.status_code}")
    files = artifact_response.json().get("files", [])
    names = {Path(str(item.get("path") or "")).name for item in files}
    missing = sorted(expected_artifacts - names)
    if missing:
        raise ModelRuntimeError("mlflow_artifacts_missing:" + ",".join(missing))
    return {
        "schema_version": "evm.scenario_mlflow_evidence.v1",
        "status": "pass",
        "mlflow_run_id": run_id,
        "run_status": "FINISHED",
        "artifact_names": sorted(names),
        "artifact_root": run_payload.get("info", {}).get("artifact_uri"),
        "observed_at": utc_now(),
    }


def assert_ready_identity(run: Any, training: dict[str, Any], ready: dict[str, Any]) -> None:
    expected = {
        "lifecycle_run_id": run.run_id,
        "model_artifact_sha256": training["model_artifact_sha256"],
        "data_identity_sha256": run.identity.data_identity_sha256,
        "source_commit": run.identity.source_commit,
    }
    mismatches = [key for key, value in expected.items() if ready.get(key) != value]
    if mismatches:
        raise ModelRuntimeError("staging_ready_identity_mismatch:" + ",".join(mismatches))


def write_prometheus_target(path: Path, config: ScenarioExecutionConfig, run: Any) -> None:
    payload = [
        {
            "targets": [f"host.docker.internal:{config.serving_port}"],
            "labels": {
                "evm_model_family": config.model_family,
                "evm_environment": "local-staging",
                "evm_target_slot": "scenario-staging",
            },
        }
    ]
    atomic_write_json(path, payload)


def stop_staging_server(server: RunningServer) -> None:
    if server.process.poll() is None:
        server.process.terminate()
        try:
            server.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.process.kill()
            server.process.wait(timeout=10)
    server.log_handle.close()


def verify_file(uri: str, expected_sha256: str) -> dict[str, Any]:
    path = Path(uri)
    actual = file_sha256(path)
    if actual != expected_sha256:
        raise ModelRuntimeError(f"file_identity_mismatch:{path.name}")
    return {"uri": str(path), "sha256": actual, "size_bytes": path.stat().st_size}


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def read_optional_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def restore_optional_bytes(path: Path, payload: bytes | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if payload is None:
        path.unlink(missing_ok=True)
    else:
        temporary = path.with_suffix(path.suffix + ".restore")
        temporary.write_bytes(payload)
        temporary.replace(path)


def tail_text(path: Path, lines: int = 30) -> str:
    if not path.is_file():
        return "log_missing"
    return " | ".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one real governed VLM or LLM lifecycle on the host CUDA runtime."
    )
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--model-family", choices=("vlm", "llm"), required=True)
    parser.add_argument("--model-repository", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--data-view-uri", required=True)
    parser.add_argument("--quality-disposition-uri")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-branch", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--staging-approver", required=True)
    parser.add_argument("--staging-reason", required=True)
    parser.add_argument("--serving-port", type=int, required=True)
    parser.add_argument("--max-steps", type=int, required=True)
    parser.add_argument("--quantization-requested", default="none")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_real_scenario_lifecycle(
        ScenarioExecutionConfig(
            scenario_id=args.scenario_id,
            model_family=args.model_family,
            model_repository=args.model_repository,
            model_revision=args.model_revision,
            model_dir=args.model_dir,
            data_view_uri=args.data_view_uri,
            quality_disposition_uri=args.quality_disposition_uri,
            source_commit=args.source_commit,
            source_branch=args.source_branch,
            actor=args.actor,
            reason=args.reason,
            staging_approver=args.staging_approver,
            staging_reason=args.staging_reason,
            serving_port=args.serving_port,
            max_steps=args.max_steps,
            quantization_requested=args.quantization_requested,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
