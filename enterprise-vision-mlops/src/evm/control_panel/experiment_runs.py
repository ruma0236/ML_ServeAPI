from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from pydantic import Field

from evm.control_panel.schemas import ContractModel


ExperimentState = Literal[
    "planned",
    "running",
    "cancelling",
    "cancelled",
    "completed",
    "blocked",
    "failed",
]
TrialState = Literal["planned", "running", "completed", "blocked", "cancelled"]
TrainingPhase = Literal[
    "preparing",
    "training",
    "validating",
    "final_refit",
    "completed",
]
QualityReviewState = Literal["review_required", "resolved"]
_EXPERIMENT_LOCK = RLock()


class FoldResult(ContractModel):
    repeat: int = Field(ge=0)
    fold: int = Field(ge=0)
    state: TrialState
    seed: int
    train_records: int = Field(ge=0)
    validation_records: int = Field(ge=0)
    metrics: dict[str, float] = Field(default_factory=dict)
    mlflow_run_id: str | None = None
    artifact_uri: str | None = None
    blocker: str | None = None


class TrialResult(ContractModel):
    trial_id: str
    state: TrialState
    parameters: dict[str, Any] = Field(default_factory=dict)
    folds: list[FoldResult] = Field(default_factory=list)
    aggregate_metrics: dict[str, float] = Field(default_factory=dict)
    score: float | None = None
    blocker: str | None = None


class TrainingTelemetry(ContractModel):
    unit_role: Literal["cross_validation", "final_refit"]
    phase: TrainingPhase
    trial_id: str | None = None
    repeat: int | None = Field(default=None, ge=0)
    fold: int | None = Field(default=None, ge=0)
    epoch: int = Field(default=0, ge=0)
    epochs: int = Field(default=0, ge=0)
    step: int = Field(default=0, ge=0)
    steps: int = Field(default=0, ge=0)
    optimizer_steps: int = Field(default=0, ge=0)
    unit_progress: float = Field(default=0.0, ge=0, le=1)
    train_loss: float | None = None
    validation_metrics: dict[str, float] = Field(default_factory=dict)
    updated_at: str


class ModelQualityReview(ContractModel):
    schema_version: Literal["evm.model_quality_review.v1"] = (
        "evm.model_quality_review.v1"
    )
    event_id: str
    event_type: Literal["model_quality_regression"] = "model_quality_regression"
    state: QualityReviewState
    fingerprint: str
    source_profile_digest: str
    dataset_version: str
    selected_trial_id: str | None = None
    selected_parameters: dict[str, Any] = Field(default_factory=dict)
    candidate_id: str
    observed_metrics: dict[str, float] = Field(default_factory=dict)
    policy_thresholds: dict[str, float] = Field(default_factory=dict)
    failed_gates: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    repeat_guard: Literal["block_same_profile"] = "block_same_profile"
    evidence_uri: str
    created_at: str


class ExperimentRun(ContractModel):
    schema_version: Literal["evm.experiment_run.v1"] = "evm.experiment_run.v1"
    experiment_id: str
    lifecycle_run_id: str
    profile_name: str
    profile_digest: str
    dataset_version: str
    source_manifest_sha256: str
    holdout_split: str
    holdout_sha256: str
    mode: Literal["manual", "grid", "bayesian"]
    primary_metric: Literal["accuracy", "f1", "auroc"]
    seed: int
    folds: int = Field(ge=2)
    repeats: int = Field(ge=1)
    requested_trials: int = Field(ge=1)
    total_units: int = Field(ge=1)
    completed_units: int = Field(default=0, ge=0)
    progress: float = Field(default=0.0, ge=0, le=1)
    state: ExperimentState
    gpu_quota: int = Field(ge=0)
    scheduled_parallelism: int = Field(ge=1)
    parent_mlflow_run_id: str | None = None
    selected_trial_id: str | None = None
    selected_parameters: dict[str, Any] = Field(default_factory=dict)
    fold_manifest_uri: str | None = None
    comparison_matrix_uri: str | None = None
    final_model_matrix_uri: str | None = None
    trials: list[TrialResult] = Field(default_factory=list)
    training_telemetry: TrainingTelemetry | None = None
    quality_review: ModelQualityReview | None = None
    blockers: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None


class ExperimentRunList(ContractModel):
    runs: list[ExperimentRun] = Field(default_factory=list)
    total: int = 0


class ExperimentCancelRequest(ContractModel):
    actor: str = Field(min_length=2)
    reason: str = Field(min_length=8)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def experiment_root() -> Path:
    return Path(
        os.getenv(
            "EVM_EXPERIMENT_RUN_ROOT",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w8/experiment_runs",
        )
    )


def safe_experiment_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,127}", value):
        raise ValueError("invalid_experiment_id")
    return value


def experiment_dir(experiment_id: str) -> Path:
    return experiment_root() / safe_experiment_id(experiment_id)


def experiment_path(experiment_id: str) -> Path:
    return experiment_dir(experiment_id) / "experiment_run.json"


def cancellation_path(experiment_id: str) -> Path:
    return experiment_dir(experiment_id) / "cancel.requested.json"


def write_experiment(run: ExperimentRun) -> ExperimentRun:
    with _EXPERIMENT_LOCK:
        run.updated_at = utc_now()
        atomic_write_json(experiment_path(run.experiment_id), run.model_dump(mode="json"))
    return run


def read_experiment(experiment_id: str) -> ExperimentRun | None:
    path = experiment_path(experiment_id)
    if not path.is_file():
        return None
    try:
        return ExperimentRun.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def read_experiments(limit: int = 100) -> ExperimentRunList:
    root = experiment_root()
    if not root.is_dir():
        return ExperimentRunList()
    runs: list[ExperimentRun] = []
    for path in sorted(
        root.glob("*/experiment_run.json"),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )[: max(1, min(limit, 500))]:
        try:
            run = ExperimentRun.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        runs.append(run)
    return ExperimentRunList(runs=runs, total=len(runs))


def unresolved_quality_review(profile_digest: str) -> ModelQualityReview | None:
    for run in read_experiments(limit=500).runs:
        review = run.quality_review
        if (
            review is not None
            and review.state == "review_required"
            and review.source_profile_digest == profile_digest
        ):
            return review
    return None


def request_cancellation(
    experiment_id: str,
    *,
    actor: str,
    reason: str,
) -> ExperimentRun | None:
    run = read_experiment(experiment_id)
    if run is None:
        return None
    if run.state in {"completed", "cancelled", "blocked", "failed"}:
        return run
    mark_cancellation_requested(experiment_id, actor=actor, reason=reason)
    run.state = "cancelling"
    return write_experiment(run)


def mark_cancellation_requested(
    experiment_id: str,
    *,
    actor: str,
    reason: str,
) -> None:
    atomic_write_json(
        cancellation_path(experiment_id),
        {
            "schema_version": "evm.experiment_cancel.v1",
            "experiment_id": safe_experiment_id(experiment_id),
            "actor": actor,
            "reason": reason,
            "requested_at": utc_now(),
        },
    )


def cancellation_requested(experiment_id: str) -> bool:
    return cancellation_path(experiment_id).is_file()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
