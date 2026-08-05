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


def _workload_view(run: ScenarioWorkloadRun) -> ScenarioWorkloadRunView:
    return ScenarioWorkloadRunView.model_validate(
        {
            **run.model_dump(mode="json"),
            "evaluation_summary": _evaluation_summary(run),
        }
    )


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
