from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from evm.control_panel.scenario_workloads import (
    GpuLease,
    ScenarioWorkloadError,
    ScenarioWorkloadRun,
    ScenarioWorkloadRunList,
    get_workload_run,
    list_workload_runs,
    read_active_gpu_lease,
)
from evm.control_panel.scenario_workload_control import (
    ScenarioWorkloadApprovalRequest,
    ScenarioWorkloadLaunchRequest,
    ScenarioWorkloadGpuHandoffRequest,
    ScenarioWorkloadPresetCatalog,
    ScenarioWorkloadWorkerHealth,
    issue_staging_approval,
    issue_gpu_handoff_request,
    launch_workload,
    load_preset_catalog,
    read_worker_health,
)
from evm.control_panel.scenario_workload_production import (
    ScenarioProductionApprovalRequest,
    ScenarioProductionIntent,
    ScenarioProductionIntentList,
    ScenarioProductionRequest,
    ScenarioProductionRollbackRequest,
    approve_production_intent,
    create_production_intent,
    current_production_intent,
    get_production_intent,
    list_production_intents,
    request_production_rollback,
)


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel-workloads"])


class WorkloadReleaseGateSummary(BaseModel):
    status: Literal["pass", "blocked", "unavailable"] = "unavailable"
    blockers: list[str] = Field(default_factory=list)
    policy_source: str


class WorkloadEvaluationSummary(BaseModel):
    schema_version: str
    model_family: Literal["vlm", "llm"]
    quality_metrics: dict[str, float] = Field(default_factory=dict)
    operational_metrics: dict[str, float] = Field(default_factory=dict)
    release_gate: WorkloadReleaseGateSummary
    evaluated_at: str | None = None
    evidence_uri: str
    claim_boundary: str | None = None


class ScenarioWorkloadRunView(ScenarioWorkloadRun):
    evaluation_summary: WorkloadEvaluationSummary | None = None
    training_progress: dict[str, object] | None = None
    control_state: dict[str, object] = Field(default_factory=dict)


class ScenarioWorkloadRunListView(BaseModel):
    runs: list[ScenarioWorkloadRunView] = Field(default_factory=list)
    total: int = 0


@router.get("/scenario-workloads", response_model=ScenarioWorkloadRunListView)
def scenario_workload_runs(limit: int = 100) -> ScenarioWorkloadRunListView:
    listed: ScenarioWorkloadRunList = list_workload_runs(limit=limit)
    return ScenarioWorkloadRunListView(
        runs=[_workload_view(run) for run in listed.runs],
        total=listed.total,
    )


@router.get("/scenario-workloads/presets", response_model=ScenarioWorkloadPresetCatalog)
def scenario_workload_presets() -> ScenarioWorkloadPresetCatalog:
    return workload_operation(load_preset_catalog)


@router.get("/scenario-workloads/worker", response_model=ScenarioWorkloadWorkerHealth)
def scenario_workload_worker() -> ScenarioWorkloadWorkerHealth:
    return read_worker_health()


@router.post("/scenario-workloads", response_model=ScenarioWorkloadRunView, status_code=202)
def launch_scenario_workload(request: ScenarioWorkloadLaunchRequest) -> ScenarioWorkloadRunView:
    source_commit = os.getenv("GIT_COMMIT", "").strip() or os.getenv("EVM_GIT_COMMIT", "").strip()
    source_branch = os.getenv("GIT_BRANCH", "").strip() or os.getenv("EVM_GIT_BRANCH", "").strip()
    return _workload_view(
        workload_operation(
            lambda: launch_workload(
                request,
                source_commit=source_commit,
                source_branch=source_branch,
            )
        )
    )


@router.post(
    "/scenario-workloads/{run_id}/approve-gpu-handoff",
    response_model=ScenarioWorkloadRunView,
    status_code=202,
)
def approve_scenario_workload_gpu_handoff(
    run_id: str,
    request: ScenarioWorkloadGpuHandoffRequest,
) -> ScenarioWorkloadRunView:
    workload_operation(lambda: issue_gpu_handoff_request(run_id, request))
    return _workload_view(get_workload_run(run_id))


@router.post(
    "/scenario-workloads/{run_id}/approve-staging",
    response_model=ScenarioWorkloadRunView,
    status_code=202,
)
def approve_scenario_workload_staging(
    run_id: str,
    request: ScenarioWorkloadApprovalRequest,
) -> ScenarioWorkloadRunView:
    workload_operation(lambda: issue_staging_approval(run_id, request))
    return _workload_view(get_workload_run(run_id))


@router.get(
    "/scenario-workloads/production-intents",
    response_model=ScenarioProductionIntentList,
)
def scenario_production_intents(limit: int = 100) -> ScenarioProductionIntentList:
    return list_production_intents(limit=limit)


@router.get(
    "/scenario-workloads/production-intents/current",
    response_model=ScenarioProductionIntent | None,
)
def scenario_current_production_intent() -> ScenarioProductionIntent | None:
    return current_production_intent()


@router.post(
    "/scenario-workloads/{run_id}/production-intents",
    response_model=ScenarioProductionIntent,
    status_code=202,
)
def create_scenario_production_intent(
    run_id: str,
    request: ScenarioProductionRequest,
) -> ScenarioProductionIntent:
    return workload_operation(lambda: create_production_intent(run_id, request))


@router.post(
    "/scenario-workloads/production-intents/{intent_id}/approve",
    response_model=ScenarioProductionIntent,
    status_code=202,
)
def approve_scenario_production_intent(
    intent_id: str,
    request: ScenarioProductionApprovalRequest,
) -> ScenarioProductionIntent:
    return workload_operation(lambda: approve_production_intent(intent_id, request))


@router.post(
    "/scenario-workloads/production-intents/{intent_id}/rollback",
    response_model=ScenarioProductionIntent,
    status_code=202,
)
def rollback_scenario_production_intent(
    intent_id: str,
    request: ScenarioProductionRollbackRequest,
) -> ScenarioProductionIntent:
    return workload_operation(lambda: request_production_rollback(intent_id, request))


@router.get(
    "/scenario-workloads/production-intents/{intent_id}",
    response_model=ScenarioProductionIntent,
)
def scenario_production_intent(intent_id: str) -> ScenarioProductionIntent:
    return workload_operation(lambda: get_production_intent(intent_id))


@router.get("/scenario-workloads/gpu-lease", response_model=GpuLease | None)
def scenario_gpu_lease() -> GpuLease | None:
    return read_active_gpu_lease()


@router.get("/scenario-workloads/{run_id}", response_model=ScenarioWorkloadRunView)
def scenario_workload_run(run_id: str) -> ScenarioWorkloadRunView:
    try:
        return _workload_view(get_workload_run(run_id))
    except ScenarioWorkloadError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc


def workload_operation(operation):
    try:
        return operation()
    except ScenarioWorkloadError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc


def _workload_view(run: ScenarioWorkloadRun) -> ScenarioWorkloadRunView:
    return ScenarioWorkloadRunView.model_validate(
        {
            **run.model_dump(mode="json"),
            "evaluation_summary": _evaluation_summary(run),
            "training_progress": _training_progress(run),
            "control_state": _control_state(run),
        }
    )


def _control_state(run: ScenarioWorkloadRun) -> dict[str, object]:
    root = _resolve_data_path(run.artifact_root)
    handoff = _read_json(root / "gpu-handoff-request.json")
    staging = _read_json(root / "staging-approval.json")
    return {
        "gpu_handoff_state": _approval_state(handoff, consumed=True),
        "gpu_handoff_approver": handoff.get("approver") if handoff else None,
        "staging_approval_state": _approval_state(staging, consumed=False),
        "staging_approver": staging.get("approver") if staging else None,
    }


def _approval_state(payload: dict[str, object] | None, *, consumed: bool) -> str:
    if payload is None:
        return "missing"
    state = str(payload.get("state") or payload.get("decision") or "")
    if consumed and state == "consumed":
        return "consumed"
    if state in {"approved", "approved_for_staging"} or payload.get("decision") == "approved":
        return "approved"
    return "invalid"


def _training_progress(run: ScenarioWorkloadRun) -> dict[str, object] | None:
    path = _resolve_data_path(str(Path(run.artifact_root) / "model" / "training-progress.json"))
    payload = _read_json(path)
    if payload is None or payload.get("schema_version") != "evm.scenario_training_progress.v1":
        return None
    if payload.get("lifecycle_run_id") != run.run_id:
        return None
    return payload


def _evaluation_summary(run: ScenarioWorkloadRun) -> WorkloadEvaluationSummary | None:
    if not run.evaluation_uri:
        return None
    evaluation_path = _resolve_data_path(run.evaluation_uri)
    evaluation = _read_json(evaluation_path)
    if evaluation is None:
        return None
    training_path = evaluation_path.with_name("training-result.json")
    training = _read_json(training_path) or {}
    evaluation_metrics = evaluation.get("metrics")
    training_metrics = training.get("metrics")
    if not isinstance(evaluation_metrics, dict):
        return None
    if not isinstance(training_metrics, dict):
        training_metrics = {}

    if run.identity.model_family == "vlm":
        quality_metrics = _numeric_metrics(
            evaluation_metrics,
            ("accuracy", "parse_rate"),
        )
        evaluated_records = evaluation_metrics.get("record_count")
    else:
        quality_metrics = _numeric_metrics(
            evaluation_metrics,
            ("validation_loss", "mean_token_f1", "nonempty_rate"),
        )
        evaluated_records = evaluation_metrics.get("generated_record_count")

    operational_metrics = _numeric_metrics(
        evaluation_metrics,
        ("p95_latency_seconds",),
    )
    if isinstance(evaluated_records, int | float) and not isinstance(evaluated_records, bool):
        operational_metrics["evaluated_records"] = float(evaluated_records)
    operational_metrics.update(
        _numeric_metrics(
            training_metrics,
            ("peak_gpu_allocated_mib", "training_seconds"),
        )
    )

    blockers = training.get("promotion_blockers")
    gate_blockers = [str(value) for value in blockers] if isinstance(blockers, list) else []
    training_status = training.get("status")
    gate_status: Literal["pass", "blocked", "unavailable"] = (
        "pass" if training_status == "pass" and not gate_blockers
        else "blocked" if training_status in {"pass", "blocked", "failed"} or gate_blockers
        else "unavailable"
    )
    return WorkloadEvaluationSummary(
        schema_version=str(evaluation.get("schema_version") or "unknown"),
        model_family=run.identity.model_family,
        quality_metrics=quality_metrics,
        operational_metrics=operational_metrics,
        release_gate=WorkloadReleaseGateSummary(
            status=gate_status,
            blockers=gate_blockers,
            policy_source=str(training_path),
        ),
        evaluated_at=str(evaluation.get("evaluated_at")) if evaluation.get("evaluated_at") else None,
        evidence_uri=run.evaluation_uri,
        claim_boundary=str(training.get("claim_boundary")) if training.get("claim_boundary") else None,
    )


def _resolve_data_path(uri: str) -> Path:
    direct = Path(uri)
    if direct.is_file():
        return direct
    normalized = uri.replace("\\", "/")
    host_root = os.getenv(
        "EVM_HOST_DATA_ROOT",
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
    ).replace("\\", "/").rstrip("/")
    mount_root = os.getenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data").replace("\\", "/").rstrip("/")
    if normalized.lower().startswith(host_root.lower()):
        return Path(f"{mount_root}{normalized[len(host_root):]}")
    return Path(normalized)


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _numeric_metrics(source: dict[str, object], names: tuple[str, ...]) -> dict[str, float]:
    return {
        name: float(source[name])
        for name in names
        if isinstance(source.get(name), int | float) and not isinstance(source.get(name), bool)
    }
