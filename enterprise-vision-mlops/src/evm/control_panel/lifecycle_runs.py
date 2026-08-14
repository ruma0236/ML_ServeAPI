from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Literal
from uuid import uuid4

from pydantic import Field

from evm.control_panel.experiment_runs import (
    ModelQualityReview,
    is_metric_quality_review,
    mark_cancellation_requested,
    read_experiment,
    unresolved_quality_review,
)
from evm.control_panel.lifecycle_guards import (
    LifecycleGuardBlocked,
    dispatch_lifecycle_guard,
    seal_lifecycle_guard_artifacts,
)
from evm.control_panel.lifecycle_integrity import (
    LifecycleIntegrityBlocked,
    validate_lifecycle_release_submission,
)
from evm.control_panel.lifecycle_integrity_injection import (
    LifecycleIntegrityInjectionBlocked,
    release_submission_for_admission,
)
from evm.control_panel.lifecycle_quality_guard import (
    LifecycleQualityGuardBlocked,
    LifecycleQualityReviewActionRequest,
    LifecycleQualityReviewRegistration,
    apply_quality_review_action,
    authorize_training,
    quality_review_path,
    register_quality_review,
)
from evm.control_panel.lifecycle_release_guard import (
    LifecycleReleaseGuardBlocked,
    LifecycleReleaseGuardRegistration,
    authorize_release_guard,
    register_release_guard,
    release_guard_path,
)
from evm.control_panel.pipeline_profiles import (
    PipelineProfileRecord,
    get_profile,
    validate_profile,
    validate_profile_replay,
)
from evm.control_panel.readiness_evaluator import runtime_path
from evm.control_panel.schemas import AuditEvent, ContractModel
from evm.observability.trace_context import W3CTraceContext, current_trace_context


LifecycleRunState = Literal[
    "dry_run",
    "queued",
    "running",
    "paused",
    "waiting_approval",
    "blocked",
    "failed",
    "completed",
    "cancelled",
    "rolling_back",
    "rolled_back",
]
LifecycleExecutionMode = Literal["automatic", "stepwise"]
LifecycleStageState = Literal[
    "not_started",
    "queued",
    "running",
    "waiting_approval",
    "blocked",
    "failed",
    "completed",
    "skipped",
    "cancelled",
]
LifecycleRuntime = Literal[
    "control-plane",
    "airflow",
    "kubernetes",
    "mlflow",
    "github-actions",
    "serving",
    "prometheus",
]


class LifecycleRunError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class LifecycleRunRequest(ContractModel):
    profile_id: str
    profile_version: int | None = Field(default=None, ge=1)
    actor: str = Field(min_length=2)
    reason: str = Field(min_length=8)
    dry_run: bool = True
    execution_mode: LifecycleExecutionMode = "automatic"


class LifecycleActionRequest(ContractModel):
    actor: str = Field(min_length=2)
    reason: str = Field(min_length=8)
    expected_version: int = Field(ge=1)


def reject_quality_review(review: ModelQualityReview) -> None:
    raise LifecycleRunError(
        "model_quality_review_unresolved",
        "This exact Blueprint previously failed promotion quality gates. "
        f"Revise and save a new Blueprint before retrying; event={review.event_id}, "
        f"failed_gates={','.join(review.failed_gates)}, "
        f"recommendations={','.join(review.recommendations)}.",
        status_code=422,
    )


def reject_unresolved_quality_review(profile_digest: str) -> None:
    review = unresolved_quality_review(profile_digest)
    if review is not None:
        reject_quality_review(review)


def reject_run_quality_review(run_id: str, profile_digest: str) -> None:
    experiment = read_experiment(run_id)
    if (
        experiment is not None
        and experiment.quality_review is not None
        and experiment.quality_review.state == "review_required"
        and is_metric_quality_review(experiment.quality_review)
    ):
        reject_quality_review(experiment.quality_review)
    reject_unresolved_quality_review(profile_digest)


class LifecycleApprovalRequest(LifecycleActionRequest):
    approver: str = Field(min_length=2)
    candidate_id: str | None = Field(default=None, min_length=1)
    model_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    ct_evaluation_id: str | None = Field(default=None, min_length=1)


class LifecycleStage(ContractModel):
    stage_id: str
    label: str
    runtime: LifecycleRuntime
    state: LifecycleStageState = "not_started"
    progress: float = Field(default=0.0, ge=0, le=1)
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=1, ge=1)
    started_at: str | None = None
    finished_at: str | None = None
    task_id: str | None = None
    runtime_id: str | None = None
    runtime_state: str | None = None
    evidence_uri: str | None = None
    detail: str | None = None
    blockers: list[str] = Field(default_factory=list)


class LifecycleRun(ContractModel):
    schema_version: Literal["evm.lifecycle_run.v1"] = "evm.lifecycle_run.v1"
    run_id: str
    profile_id: str
    profile_version: int
    profile_digest: str
    effective_config_digest: str
    lifecycle_series_id: str | None = None
    attempt_id: str | None = None
    correlation_id: str | None = None
    trace_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    traceparent: str | None = Field(
        default=None,
        pattern=r"^00-[a-f0-9]{32}-[a-f0-9]{16}-[a-f0-9]{2}$",
    )
    tracestate: str | None = None
    source_commit: str | None = None
    source_branch: str | None = None
    state: LifecycleRunState
    version: int = Field(ge=1)
    actor: str
    reason: str
    dry_run: bool
    execution_mode: LifecycleExecutionMode = "automatic"
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    current_stage: str | None = None
    progress: float = Field(default=0.0, ge=0, le=1)
    profile_snapshot_uri: str
    airflow_config_uri: str
    airflow_runtime_uri: str
    model_config_uri: str
    model_runtime_uri: str
    artifact_root: str
    identity_envelope_uri: str | None = None
    component_revision_map_uri: str | None = None
    guard_state_uri: str | None = None
    side_effect_ledger_uri: str | None = None
    guard_decision: Literal["pass", "blocked"] | None = None
    guard_authorities: list[str] = Field(default_factory=list)
    guard_blockers: list[str] = Field(default_factory=list)
    cycle_id: str | None = None
    experiment_id: str | None = None
    cycle_snapshot_uri: str | None = None
    model_matrix_uri: str | None = None
    readiness_uri: str | None = None
    real_test_validation_uri: str | None = None
    ct_snapshot_uri: str | None = None
    ct_evaluation_uri: str | None = None
    data_integrity_uri: str | None = None
    quality_review_uri: str | None = None
    quality_review_state: Literal[
        "review_required",
        "manual_hold",
        "rejected",
        "approved_for_training",
    ] | None = None
    quality_review_event_id: str | None = None
    retraining_candidate_id: str | None = None
    retraining_candidate_digest: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    release_submission_uri: str | None = None
    release_guard_required: bool = False
    release_guard_uri: str | None = None
    release_guard_id: str | None = None
    release_guard_state: Literal[
        "rejected_release",
        "rolled_back",
        "approved_for_release",
    ] | None = None
    release_guard_replay_run_id: str | None = None
    resource_handoff_uri: str | None = None
    deployment_intent_id: str | None = None
    approver: str | None = None
    failure_reason: str | None = None
    blockers: list[str] = Field(default_factory=list)
    stages: list[LifecycleStage] = Field(default_factory=list)
    audit: list[AuditEvent] = Field(default_factory=list)


class LifecycleRunList(ContractModel):
    runs: list[LifecycleRun] = Field(default_factory=list)
    total: int = 0


class LifecycleWorkerState(ContractModel):
    status: Literal["online", "stale", "offline"]
    worker_id: str | None = None
    pid: int | None = None
    source_commit: str | None = None
    source_branch: str | None = None
    started_at: str | None = None
    process_instance_id: str | None = None
    supervisor_lease_id: str | None = None
    fencing_token: int | None = None
    last_seen_at: str | None = None
    current_run_id: str | None = None
    message: str | None = None


STAGE_SPECS: tuple[tuple[str, str, LifecycleRuntime, int], ...] = (
    ("profile_snapshot", "Profile Snapshot", "control-plane", 1),
    ("data_pipeline", "Data Pipeline", "airflow", 2),
    ("model_training", "Model Training", "kubernetes", 2),
    ("model_evaluation", "Model Evaluation", "mlflow", 2),
    ("artifact_readiness", "Artifact Readiness", "control-plane", 3),
    ("ci_ct_gate", "CI / CT Admission", "github-actions", 20),
    ("approval", "Human Approval", "control-plane", 1),
    ("deployment", "Deployment", "kubernetes", 1),
    ("serving_validation", "Serving Validation", "serving", 2),
    ("monitoring", "Monitoring", "prometheus", 3),
)
TERMINAL_STAGE_STATES = {"completed", "skipped", "cancelled"}
ALLOWED_STAGE_TRANSITIONS: dict[LifecycleStageState, set[LifecycleStageState]] = {
    "not_started": {
        "queued",
        "running",
        "waiting_approval",
        "skipped",
        "blocked",
        "cancelled",
    },
    "queued": {"running", "blocked", "failed", "cancelled"},
    "running": {"completed", "blocked", "failed", "cancelled"},
    "waiting_approval": {"completed", "blocked", "cancelled"},
    "blocked": set(),
    "failed": set(),
    "completed": set(),
    "skipped": set(),
    "cancelled": set(),
}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def lifecycle_root() -> Path:
    return Path(
        os.getenv(
            "EVM_LIFECYCLE_RUN_ROOT",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/lifecycle_runs",
        )
    )


def lifecycle_runtime_root() -> str:
    return os.getenv(
        "EVM_LIFECYCLE_RUNTIME_ROOT",
        "/mnt/evm-data/artifacts/w7/lifecycle_runs",
    ).replace("\\", "/").rstrip("/")


def lifecycle_host_root() -> str:
    return os.getenv(
        "EVM_HOST_DATA_ROOT",
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
    ).replace("\\", "/").rstrip("/")


def run_path(run_id: str) -> Path:
    return lifecycle_root() / run_id / "lifecycle_run.json"


def worker_state_path() -> Path:
    return lifecycle_root() / "_worker.json"


def audit(actor: str, event: str, **details: str | int | float | bool | None) -> AuditEvent:
    return AuditEvent(timestamp=utc_now(), actor=actor, event=event, details=details)


def source_revision() -> tuple[str | None, str | None]:
    commit = (
        os.getenv("GIT_COMMIT")
        or os.getenv("EVM_GIT_COMMIT")
        or os.getenv("GITHUB_SHA")
        or None
    )
    branch = (
        os.getenv("GIT_BRANCH")
        or os.getenv("EVM_GIT_BRANCH")
        or os.getenv("GITHUB_HEAD_REF")
        or os.getenv("GITHUB_REF_NAME")
        or None
    )
    return commit, branch


def create_lifecycle_run(request: LifecycleRunRequest) -> LifecycleRun:
    record = get_profile(request.profile_id, request.profile_version)
    if record is None:
        raise LifecycleRunError(
            "pipeline_profile_not_found",
            f"Pipeline profile {request.profile_id} was not found.",
            status_code=404,
        )
    if record.profile.execution_scope != "full_lifecycle":
        raise LifecycleRunError(
            "full_lifecycle_profile_required",
            "LifecycleRun requires a full_lifecycle profile.",
        )
    validation = validate_profile(record.profile)
    if not validation.valid:
        raise LifecycleRunError(
            "pipeline_profile_invalid",
            ", ".join(validation.blockers) or "Pipeline profile is invalid.",
            status_code=422,
        )
    if not request.dry_run and not validation.executable:
        raise LifecycleRunError(
            "pipeline_profile_not_executable",
            ", ".join(validation.blockers) or "Pipeline profile is not executable.",
        )
    if not request.dry_run:
        reject_unresolved_quality_review(record.digest)
    replay_validation = validate_profile_replay(record)
    if replay_validation.status != "ready":
        raise LifecycleRunError(
            "pipeline_profile_replay_blocked",
            ", ".join(replay_validation.blockers)
            or "Pipeline profile replay identity is not ready.",
            status_code=422,
        )
    source_commit, source_branch = source_revision()
    if not request.dry_run and not source_commit:
        raise LifecycleRunError(
            "source_revision_missing",
            "Executable LifecycleRuns require an immutable source commit. "
            "Start the stack through start_local_stack.ps1 or set EVM_GIT_COMMIT.",
            status_code=422,
        )

    created_at = utc_now()
    run_id = f"lifecycle-{created_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid4().hex[:8]}"
    parent_trace = current_trace_context()
    trace_context = parent_trace.child() if parent_trace else W3CTraceContext.new_root()
    snapshot = prepare_runtime_snapshot(run_id, record, trace_context)
    guard_identity = seal_lifecycle_guard_artifacts(
        directory=lifecycle_root() / run_id,
        run_id=run_id,
        profile_id=record.profile_id,
        profile_version=record.version,
        profile_digest=record.digest,
        effective_config_digest=snapshot["effective_config_digest"],
        source_commit=source_commit or "unresolved",
        source_branch=source_branch,
        profile_snapshot_uri=snapshot["profile_snapshot_uri"],
        airflow_config_uri=snapshot["airflow_config_uri"],
        model_config_uri=snapshot["model_config_uri"],
        dirty_state_digest=os.getenv("EVM_GIT_DIRTY_DIGEST"),
        trace_context=trace_context,
    )
    stages = build_stages()
    stages[0] = stages[0].model_copy(
        update={
            "state": "completed",
            "progress": 1.0,
            "attempt": 1,
            "started_at": created_at,
            "finished_at": created_at,
            "evidence_uri": snapshot["profile_snapshot_uri"],
            "detail": f"profile digest {record.digest[:16]}",
        }
    )
    state: LifecycleRunState = "dry_run" if request.dry_run else "queued"
    run = LifecycleRun(
        run_id=run_id,
        profile_id=record.profile_id,
        profile_version=record.version,
        profile_digest=record.digest,
        effective_config_digest=snapshot["effective_config_digest"],
        lifecycle_series_id=guard_identity.lifecycle_series_id,
        attempt_id=guard_identity.attempt_id,
        correlation_id=guard_identity.correlation_id,
        trace_id=guard_identity.trace_id,
        traceparent=guard_identity.traceparent,
        tracestate=guard_identity.tracestate,
        source_commit=source_commit,
        source_branch=source_branch,
        state=state,
        version=1,
        actor=request.actor,
        reason=request.reason,
        dry_run=request.dry_run,
        execution_mode=request.execution_mode,
        created_at=created_at,
        updated_at=created_at,
        current_stage="data_pipeline" if not request.dry_run else None,
        progress=stage_progress(stages),
        profile_snapshot_uri=snapshot["profile_snapshot_uri"],
        airflow_config_uri=snapshot["airflow_config_uri"],
        airflow_runtime_uri=snapshot["airflow_runtime_uri"],
        model_config_uri=snapshot["model_config_uri"],
        model_runtime_uri=snapshot["model_runtime_uri"],
        artifact_root=snapshot["artifact_root"],
        identity_envelope_uri=str(lifecycle_root() / run_id / "identity.envelope.json"),
        component_revision_map_uri=guard_identity.component_revision_map_uri,
        guard_state_uri=str(lifecycle_root() / run_id / "guard_state.json"),
        side_effect_ledger_uri=str(lifecycle_root() / run_id / "side_effect_ledger.json"),
        guard_decision="pass",
        guard_authorities=["D", "E"],
        guard_blockers=[],
        release_guard_required=record.profile.gates.require_controlled_replay,
        blockers=[] if source_commit else ["source_revision_missing"],
        stages=stages,
        audit=[
            audit(
                request.actor,
                "lifecycle_run_created",
                dry_run=request.dry_run,
                profile_id=record.profile_id,
                profile_version=record.version,
                state=state,
            )
        ],
    )
    write_run(run)
    return run


def build_stages() -> list[LifecycleStage]:
    return [
        LifecycleStage(
            stage_id=stage_id,
            label=label,
            runtime=runtime,
            max_attempts=max_attempts,
        )
        for stage_id, label, runtime, max_attempts in STAGE_SPECS
    ]


def prepare_runtime_snapshot(
    run_id: str,
    record: PipelineProfileRecord,
    trace_context: W3CTraceContext,
) -> dict[str, str]:
    directory = lifecycle_root() / run_id
    directory.mkdir(parents=True, exist_ok=False)
    shared_directories = [
        directory,
        directory / "data",
        directory / "model",
        directory / "kubernetes",
        directory / "serving",
        directory / "monitoring",
    ]
    for shared_directory in shared_directories:
        shared_directory.mkdir(parents=True, exist_ok=True)
        try:
            shared_directory.chmod(0o777)
        except OSError as exc:
            raise LifecycleRunError(
                "lifecycle_storage_permission_failed",
                f"Shared lifecycle directory is not writable: {shared_directory}: {exc}",
                status_code=500,
            ) from exc
    profile_path = directory / "profile.snapshot.json"
    airflow_path = directory / "airflow.runtime.json"
    model_path = directory / "model.runtime.json"
    profile_payload = record.profile.model_dump(mode="json")
    airflow_payload = read_object(record.airflow_config_uri)
    model_payload = read_object(record.model_config_uri)
    host_artifact_root = f"{lifecycle_host_root()}/artifacts/w7/lifecycle_runs/{run_id}"
    runtime_artifact_root = f"{lifecycle_runtime_root()}/{run_id}"
    control_plane = {
        "lifecycle_run_id": run_id,
        "profile_id": record.profile_id,
        "profile_version": record.version,
        "profile_digest": record.digest,
        "artifact_root": host_artifact_root,
        "trace_id": trace_context.trace_id,
        "traceparent": trace_context.traceparent,
        "tracestate": trace_context.tracestate,
    }
    airflow_payload.setdefault("control_plane", {}).update(control_plane)
    airflow_payload["control_plane"]["pipeline_stage_scope"] = "data"
    configure_run_scoped_data_outputs(airflow_payload, runtime_artifact_root)
    matrix = model_payload.setdefault("model_matrix", {})
    matrix["matrix_id"] = run_id
    matrix["execution_run_id"] = run_id
    resources = model_payload.setdefault("resources", {})
    resources["artifact_root"] = f"{host_artifact_root}/model"
    inputs = model_payload.setdefault("inputs", {})
    inputs["base_config"] = f"{runtime_artifact_root}/airflow.runtime.json"
    inputs["shard_index"] = f"{runtime_artifact_root}/data/shards/shard_index.json"
    inputs["mlflow_tracking_uri"] = os.getenv(
        "EVM_LIFECYCLE_MLFLOW_URI",
        "http://host.docker.internal:5000",
    )
    model_payload.setdefault("control_plane", {}).update(control_plane)
    model_payload["control_plane"]["runtime_evidence"] = {
        "kubernetes": f"{host_artifact_root}/kubernetes/evidence_index.json",
        "real_test_validation": f"{host_artifact_root}/real_test_validation.json",
        "cycle_snapshot": f"{host_artifact_root}/cycle.snapshot.json",
        "readiness": f"{host_artifact_root}/readiness.json",
    }
    profile_gates = profile_payload.get("gates", {})
    profile_model = profile_payload.get("model", {})
    architecture = str(
        profile_model.get("architecture", "efficientnet-b0")
        if isinstance(profile_model, dict)
        else "efficientnet-b0"
    )
    target_environment = str(
        profile_gates.get("target_environment", "staging")
        if isinstance(profile_gates, dict)
        else "staging"
    )
    target_namespace = str(
        profile_gates.get("target_namespace", "evm-staging")
        if isinstance(profile_gates, dict)
        else "evm-staging"
    )
    model_payload["product"] = {
        "model_name": f"{architecture}-visa-anomaly",
        "target_environment": target_environment,
        "target_namespace": target_namespace,
        "target_deployment": lifecycle_deployment_name(architecture, target_environment),
        "serving_endpoint": os.getenv(
            "EVM_LIFECYCLE_SERVING_ENDPOINT",
            f"http://127.0.0.1:{lifecycle_node_port(target_environment)}",
        ),
    }
    write_json(profile_path, profile_payload)
    write_json(airflow_path, airflow_payload)
    write_json(model_path, model_payload)
    effective_digest = payload_digest(
        {"profile": profile_payload, "airflow": airflow_payload, "model": model_payload}
    )
    snapshot_manifest = {
        "schema_version": "evm.lifecycle_run_snapshot.v1",
        "run_id": run_id,
        "profile_digest": record.digest,
        "effective_config_digest": effective_digest,
        "files": {
            "profile": str(profile_path),
            "airflow": str(airflow_path),
            "model": str(model_path),
        },
    }
    write_json(directory / "snapshot_manifest.json", snapshot_manifest)
    return {
        "profile_snapshot_uri": str(profile_path),
        "airflow_config_uri": str(airflow_path),
        "airflow_runtime_uri": f"{runtime_artifact_root}/airflow.runtime.json",
        "model_config_uri": str(model_path),
        "model_runtime_uri": f"{runtime_artifact_root}/model.runtime.json",
        "artifact_root": host_artifact_root,
        "effective_config_digest": effective_digest,
    }


def configure_run_scoped_data_outputs(
    config: dict[str, object],
    runtime_artifact_root: str,
) -> None:
    pipelines = config.setdefault("pipelines", {})
    if not isinstance(pipelines, dict):
        raise LifecycleRunError(
            "profile_runtime_config_invalid",
            "Airflow runtime config pipelines must be an object.",
            status_code=422,
        )
    data_root = f"{runtime_artifact_root}/data"
    artifacts_root = f"{runtime_artifact_root}/data-artifacts"
    update_pipeline(
        pipelines,
        "dataset_intake_audit",
        {
            "output_dir": f"{data_root}/intake",
            "registry_path": f"{data_root}/intake/source_registry.json",
            "acquisition_plan_path": f"{data_root}/intake/acquisition_plan.json",
            "cleaning_report_path": f"{data_root}/intake/cleaning_benchmark.json",
            "manifest_path": f"{data_root}/intake/import_manifest.jsonl",
            "checkpoint_dir": f"{data_root}/intake/_checkpoints",
        },
    )
    validation_manifest = f"{data_root}/validated/validated_manifest.jsonl"
    quality_manifest = f"{data_root}/quality/quality_manifest.jsonl"
    validated_parquet = f"{data_root}/validated/validated_dataset.parquet"
    update_pipeline(
        pipelines,
        "data_validation",
        {
            "output_manifest": validation_manifest,
            "processed_parquet": f"{data_root}/processed/processed_dataset.parquet",
            "validated_parquet": validated_parquet,
            "dataset_metadata": f"{data_root}/validated/dataset_version.json",
        },
    )
    update_pipeline(
        pipelines,
        "image_quality",
        {
            "input_manifest": validation_manifest,
            "dataset_metadata": f"{data_root}/validated/dataset_version.json",
            "output_manifest": quality_manifest,
            "report_path": f"{data_root}/quality/quality_report.json",
            "baseline_path": f"{data_root}/quality/quality_baseline.json",
        },
    )
    update_pipeline(
        pipelines,
        "curation_workflow",
        {
            "input_manifest": quality_manifest,
            "output_dir": f"{data_root}/curation",
            "state_path": f"{data_root}/curation/curation_state.json",
            "curation_manifest": f"{data_root}/curation/curation_manifest.jsonl",
            "hitl_queue": f"{data_root}/curation/hitl_queue.jsonl",
            "sample_review_manifest": f"{data_root}/curation/sample_review.jsonl",
            "curated_eval_manifest": f"{data_root}/curation/curated_eval_manifest.jsonl",
        },
    )
    update_pipeline(
        pipelines,
        "dataset_shards",
        {
            "input_manifest": quality_manifest,
            "output_dir": f"{data_root}/shards",
            "index_path": f"{data_root}/shards/shard_index.json",
        },
    )
    update_pipeline(
        pipelines,
        "lakehouse_probe",
        {
            "input_parquet": validated_parquet,
            "output_dir": f"{artifacts_root}/lakehouse",
            "probe_report": f"{artifacts_root}/lakehouse/lakehouse_probe.json",
            "tradeoff_matrix": f"{artifacts_root}/lakehouse/engine_tradeoff_matrix.json",
            "recommendation_doc": f"{artifacts_root}/lakehouse/recommendation.md",
        },
    )


def update_pipeline(
    pipelines: dict[str, object],
    pipeline_id: str,
    values: dict[str, object],
) -> None:
    pipeline = pipelines.setdefault(pipeline_id, {})
    if not isinstance(pipeline, dict):
        raise LifecycleRunError(
            "profile_runtime_config_invalid",
            f"Airflow pipeline {pipeline_id} must be an object.",
            status_code=422,
        )
    pipeline.update(values)


def lifecycle_deployment_name(architecture: str, environment: str) -> str:
    architecture_slug = architecture.removeprefix("efficientnet-")
    environment_slug = environment.replace("pre-production", "preprod")
    return f"evm-{architecture_slug}-{environment_slug}"


def lifecycle_node_port(environment: str) -> int:
    return {
        "dev": 30811,
        "test": 30812,
        "staging": 30813,
        "pre-production": 30814,
        "production": 30800,
    }.get(environment, 30813)


def read_runs() -> LifecycleRunList:
    root = lifecycle_root()
    if not root.exists():
        return LifecycleRunList()
    runs: list[LifecycleRun] = []
    for path in root.glob("lifecycle-*/lifecycle_run.json"):
        try:
            runs.append(hydrate_cycle_context(LifecycleRun.model_validate_json(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    runs.sort(key=lambda item: (item.created_at, item.run_id), reverse=True)
    return LifecycleRunList(runs=runs, total=len(runs))


def get_lifecycle_run(run_id: str) -> LifecycleRun | None:
    path = run_path(run_id)
    if not path.is_file():
        return None
    try:
        return hydrate_cycle_context(LifecycleRun.model_validate_json(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def queue_lifecycle_run(run_id: str, request: LifecycleActionRequest) -> LifecycleRun:
    def update(run: LifecycleRun) -> LifecycleRun:
        if run.state != "dry_run":
            raise LifecycleRunError(
                "lifecycle_run_not_dry_run",
                f"LifecycleRun {run_id} is {run.state}; only dry_run can be queued.",
            )
        record = get_profile(run.profile_id, run.profile_version)
        if record is None or record.digest != run.profile_digest:
            raise LifecycleRunError(
                "pipeline_profile_snapshot_mismatch",
                "The saved profile no longer matches the LifecycleRun snapshot.",
            )
        validation = validate_profile(record.profile)
        if not validation.executable:
            raise LifecycleRunError(
                "pipeline_profile_not_executable",
                ", ".join(validation.blockers) or "Pipeline profile is not executable.",
            )
        replay_validation = validate_profile_replay(record)
        if replay_validation.status != "ready":
            raise LifecycleRunError(
                "pipeline_profile_replay_blocked",
                ", ".join(replay_validation.blockers)
                or "Pipeline profile replay identity is not ready.",
                status_code=422,
            )
        reject_unresolved_quality_review(record.digest)
        if not run.source_commit:
            raise LifecycleRunError(
                "source_revision_missing",
                "LifecycleRun cannot be queued without an immutable source commit.",
                status_code=422,
            )
        decision = lifecycle_guard_decision(run, "data_pipeline", "queue")
        run.state = "queued"
        run.dry_run = False
        run.current_stage = "data_pipeline"
        apply_guard_decision(run, decision)
        run.audit.append(audit(request.actor, "lifecycle_run_queued", reason=request.reason))
        return run

    return mutate_run(run_id, request.expected_version, update)


def continue_lifecycle_run(run_id: str, request: LifecycleActionRequest) -> LifecycleRun:
    def update(run: LifecycleRun) -> LifecycleRun:
        if run.execution_mode != "stepwise":
            raise LifecycleRunError(
                "lifecycle_run_not_stepwise",
                f"LifecycleRun {run_id} uses {run.execution_mode} execution; Continue requires stepwise mode.",
            )
        if run.state != "paused" or not run.current_stage:
            raise LifecycleRunError(
                "lifecycle_run_not_paused",
                f"LifecycleRun {run_id} is {run.state}; Continue requires a paused next stage.",
            )
        index = stage_index(run, run.current_stage)
        stage = run.stages[index]
        if stage.state != "not_started":
            raise LifecycleRunError(
                "lifecycle_stage_not_ready_for_continue",
                f"Stage {stage.stage_id} is {stage.state}; not_started is required.",
            )
        assert_dependencies_complete(run, index)
        decision = lifecycle_guard_decision(run, stage.stage_id, "continue")
        run.state = "queued"
        apply_guard_decision(run, decision)
        run.audit.append(
            audit(
                request.actor,
                "lifecycle_stage_continue_requested",
                stage_id=stage.stage_id,
                reason=request.reason,
            )
        )
        return run

    return mutate_run(run_id, request.expected_version, update)


def approve_lifecycle_run(
    run_id: str,
    request: LifecycleApprovalRequest,
) -> LifecycleRun:
    def update(run: LifecycleRun) -> LifecycleRun:
        if run.state != "waiting_approval" or run.current_stage != "approval":
            raise LifecycleRunError(
                "lifecycle_run_not_waiting_approval",
                f"LifecycleRun {run_id} is not waiting at the approval stage.",
            )
        if request.approver != request.actor:
            raise LifecycleRunError(
                "lifecycle_approval_actor_mismatch",
                "The approval actor must match the named approver.",
            )
        if request.approver == run.actor:
            raise LifecycleRunError(
                "lifecycle_requester_approver_conflict",
                "The LifecycleRun requester cannot approve the same run.",
            )
        index = stage_index(run, "approval")
        stage = run.stages[index]
        if stage.state != "waiting_approval":
            raise LifecycleRunError(
                "lifecycle_approval_stage_invalid",
                f"Approval stage is {stage.state}; waiting_approval is required.",
            )
        if not run.release_submission_uri:
            raise LifecycleRunError(
                "lifecycle_release_submission_missing",
                "LifecycleRun has no sealed release submission.",
                status_code=422,
            )
        try:
            admission_submission = release_submission_for_admission(
                run,
                runtime_path(run.release_submission_uri),
            )
            validate_lifecycle_release_submission(
                admission_submission,
                run_id=run.run_id,
                source_commit=str(run.source_commit or ""),
                expected_candidate_id=request.candidate_id,
                expected_model_digest=request.model_digest,
                expected_ct_evaluation_id=request.ct_evaluation_id,
            )
        except LifecycleIntegrityInjectionBlocked as exc:
            raise LifecycleRunError(
                "lifecycle_integrity_injection_blocked",
                ", ".join(exc.blockers),
                status_code=422,
            ) from exc
        except LifecycleIntegrityBlocked as exc:
            raise LifecycleRunError(
                "lifecycle_release_integrity_blocked",
                ", ".join(exc.blockers),
                status_code=422,
            ) from exc
        try:
            authorize_release_guard(run)
        except LifecycleReleaseGuardBlocked as exc:
            raise LifecycleRunError(
                exc.code,
                ", ".join(exc.blockers),
                status_code=422,
            ) from exc
        decision = lifecycle_guard_decision(run, "approval", "approve")
        now = utc_now()
        run.stages[index] = stage.model_copy(
            update={
                "state": "completed",
                "progress": 1.0,
                "finished_at": now,
                "runtime_state": "approved",
                "detail": f"Approved by {request.approver}: {request.reason}",
                "blockers": [],
            }
        )
        run.approver = request.approver
        apply_guard_decision(run, decision)
        run.current_stage = next_stage_id(run)
        run.progress = stage_progress(run.stages)
        run.state = derive_run_state(run)
        run.state = pause_stepwise_run(run, completed_stage_id="approval")
        run.audit.append(
            audit(
                request.actor,
                "lifecycle_run_approved",
                approver=request.approver,
                reason=request.reason,
            )
        )
        return run

    return mutate_run(run_id, request.expected_version, update)


def cancel_lifecycle_run(run_id: str, request: LifecycleActionRequest) -> LifecycleRun:
    def update(run: LifecycleRun) -> LifecycleRun:
        if run.state in {"completed", "cancelled", "rolled_back"}:
            raise LifecycleRunError(
                "lifecycle_run_terminal",
                f"LifecycleRun {run_id} is already {run.state}.",
            )
        mark_cancellation_requested(
            run_id,
            actor=request.actor,
            reason=request.reason,
        )
        for index, stage in enumerate(run.stages):
            if stage.state in {"queued", "running", "waiting_approval", "not_started"}:
                run.stages[index] = stage.model_copy(update={"state": "cancelled"})
        run.state = "cancelled"
        run.finished_at = utc_now()
        run.current_stage = None
        run.audit.append(audit(request.actor, "lifecycle_run_cancelled", reason=request.reason))
        return run

    return mutate_run(run_id, request.expected_version, update)


def retry_lifecycle_run(run_id: str, request: LifecycleActionRequest) -> LifecycleRun:
    def update(run: LifecycleRun) -> LifecycleRun:
        if run.state not in {"failed", "blocked"}:
            raise LifecycleRunError(
                "lifecycle_run_not_retryable",
                f"LifecycleRun {run_id} is {run.state}; retry requires failed or blocked.",
            )
        stage = current_or_failed_stage(run)
        if stage is None:
            raise LifecycleRunError("lifecycle_stage_not_found", "No retryable stage was found.")
        if stage.stage_id == "model_training":
            reject_run_quality_review(run.run_id, run.profile_digest)
            try:
                authorize_training(run, consume=False)
            except LifecycleQualityGuardBlocked as exc:
                raise LifecycleRunError(
                    exc.code,
                    ", ".join(exc.blockers),
                    status_code=422,
                ) from exc
        if stage.attempt >= stage.max_attempts:
            raise LifecycleRunError(
                "lifecycle_stage_attempts_exhausted",
                f"Stage {stage.stage_id} exhausted {stage.max_attempts} attempts.",
            )
        decision = lifecycle_guard_decision(run, stage.stage_id, "retry")
        index = stage_index(run, stage.stage_id)
        run.stages[index] = stage.model_copy(
            update={
                "state": "not_started",
                "progress": 0.0,
                "started_at": None,
                "finished_at": None,
                "task_id": None,
                "runtime_id": None,
                "runtime_state": None,
                "evidence_uri": None,
                "detail": f"Retry requested: {request.reason}",
                "blockers": [],
            }
        )
        run.state = "queued"
        run.current_stage = stage.stage_id
        run.failure_reason = None
        run.blockers = []
        run.finished_at = None
        apply_guard_decision(run, decision)
        run.audit.append(
            audit(
                request.actor,
                "lifecycle_stage_retry_requested",
                stage_id=stage.stage_id,
                reason=request.reason,
                next_attempt=stage.attempt + 1,
            )
        )
        return run

    return mutate_run(run_id, request.expected_version, update)


def register_lifecycle_quality_review(
    run_id: str,
    request: LifecycleQualityReviewRegistration,
) -> LifecycleRun:
    def update(run: LifecycleRun) -> LifecycleRun:
        training = run.stages[stage_index(run, "model_training")]
        if training.state != "not_started" or training.task_id:
            raise LifecycleRunError(
                "quality_review_registration_too_late",
                "Quality review signals must bind before model training admission.",
                status_code=422,
            )
        try:
            review, created = register_quality_review(run, request)
        except LifecycleQualityGuardBlocked as exc:
            raise LifecycleRunError(
                exc.code,
                ", ".join(exc.blockers),
                status_code=422,
            ) from exc
        run.quality_review_uri = str(quality_review_path(run))
        run.quality_review_state = review.state
        run.quality_review_event_id = review.event_id
        run.retraining_candidate_id = review.candidate_id
        run.retraining_candidate_digest = review.candidate_digest
        run.audit.append(
            audit(
                request.actor,
                "lifecycle_quality_review_registered"
                if created
                else "lifecycle_quality_review_signal_replayed",
                reason=request.reason,
                quality_review_event_id=review.event_id,
                retraining_candidate_id=review.candidate_id,
                registration_attempts=review.registration_attempts,
                duplicate_attempts=review.duplicate_attempts,
                stale_attempts=review.stale_attempts,
            )
        )
        return run

    return mutate_run(run_id, request.expected_version, update)


def register_lifecycle_release_guard(
    run_id: str,
    request: LifecycleReleaseGuardRegistration,
) -> LifecycleRun:
    def update(run: LifecycleRun) -> LifecycleRun:
        if run.state != "waiting_approval" or run.current_stage != "approval":
            raise LifecycleRunError(
                "release_guard_registration_stage_invalid",
                "Controlled replay evidence binds only at the release approval boundary.",
                status_code=422,
            )
        try:
            guard = register_release_guard(run, request)
        except LifecycleReleaseGuardBlocked as exc:
            raise LifecycleRunError(
                exc.code,
                ", ".join(exc.blockers),
                status_code=422,
            ) from exc
        run.release_guard_uri = str(release_guard_path(run))
        run.release_guard_id = guard.guard_id
        run.release_guard_state = guard.state
        run.release_guard_replay_run_id = guard.replay_run_id
        run.guard_authorities = sorted(set([*run.guard_authorities, "B"]))
        run.audit.append(
            audit(
                request.actor,
                "lifecycle_release_guard_registered",
                reason=request.reason,
                release_guard_id=guard.guard_id,
                replay_run_id=guard.replay_run_id,
                release_guard_state=guard.state,
                blocker_codes=",".join(guard.blocker_codes),
            )
        )
        return run

    return mutate_run(run_id, request.expected_version, update)


def apply_lifecycle_quality_review_action(
    run_id: str,
    request: LifecycleQualityReviewActionRequest,
) -> LifecycleRun:
    def update(run: LifecycleRun) -> LifecycleRun:
        try:
            review = apply_quality_review_action(run, request)
        except LifecycleQualityGuardBlocked as exc:
            raise LifecycleRunError(
                exc.code,
                ", ".join(exc.blockers),
                status_code=422,
            ) from exc
        run.quality_review_uri = str(quality_review_path(run))
        run.quality_review_state = review.state
        run.quality_review_event_id = review.event_id
        run.retraining_candidate_id = review.candidate_id
        run.retraining_candidate_digest = review.candidate_digest
        run.audit.append(
            audit(
                request.actor,
                "lifecycle_quality_review_action",
                action=request.action,
                reason=request.reason,
                action_digest=review.action_digest,
                quality_review_state=review.state,
                retraining_candidate_id=review.candidate_id,
            )
        )
        return run

    return mutate_run(run_id, request.expected_version, update)


def authorize_lifecycle_quality_training(run_id: str) -> LifecycleRun:
    def update(run: LifecycleRun) -> LifecycleRun:
        try:
            review, consumed = authorize_training(run)
        except LifecycleQualityGuardBlocked as exc:
            raise LifecycleRunError(
                exc.code,
                ", ".join(exc.blockers),
                status_code=422,
            ) from exc
        if review is None:
            return run
        run.quality_review_uri = str(quality_review_path(run))
        run.quality_review_state = review.state
        if consumed:
            run.audit.append(
                audit(
                    "lifecycle-worker",
                    "lifecycle_quality_training_approval_consumed",
                    action_digest=review.action_digest,
                    quality_review_event_id=review.event_id,
                    retraining_candidate_id=review.candidate_id,
                )
            )
        return run

    return mutate_run(run_id, None, update)


def transition_stage(
    run_id: str,
    stage_id: str,
    state: LifecycleStageState,
    *,
    actor: str,
    detail: str | None = None,
    progress: float | None = None,
    task_id: str | None = None,
    runtime_id: str | None = None,
    runtime_state: str | None = None,
    evidence_uri: str | None = None,
    blockers: list[str] | None = None,
) -> LifecycleRun:
    def update(run: LifecycleRun) -> LifecycleRun:
        if run.dry_run:
            raise LifecycleRunError(
                "lifecycle_dry_run_immutable",
                "Dry-run LifecycleRun stages cannot execute; queue the run first.",
            )
        index = stage_index(run, stage_id)
        stage = run.stages[index]
        if state == stage.state:
            return run
        if state not in ALLOWED_STAGE_TRANSITIONS[stage.state]:
            raise LifecycleRunError(
                "lifecycle_stage_transition_invalid",
                f"Stage {stage_id} cannot transition from {stage.state} to {state}.",
            )
        if state in {"queued", "running", "waiting_approval"}:
            assert_dependencies_complete(run, index)
        decision = None
        if state in {"queued", "running", "waiting_approval", "completed", "skipped"}:
            decision = lifecycle_guard_decision(run, stage_id, state)
        now = utc_now()
        next_attempt = stage.attempt
        started_at = stage.started_at
        finished_at = stage.finished_at
        if state == "queued" and stage.state == "not_started":
            next_attempt += 1
            started_at = now
            if not run.started_at:
                run.started_at = now
        if state == "running" and stage.state not in {"running", "queued"}:
            next_attempt += 1
            started_at = now
            if not run.started_at:
                run.started_at = now
        if state in {"completed", "skipped", "failed", "blocked", "cancelled"}:
            finished_at = now
        next_progress = progress
        if next_progress is None:
            if state in {"completed", "skipped"}:
                next_progress = 1.0
            elif state in {"queued", "not_started", "waiting_approval"}:
                next_progress = 0.0
            else:
                next_progress = stage.progress
        stage = stage.model_copy(
            update={
                "state": state,
                "progress": next_progress,
                "attempt": next_attempt,
                "started_at": started_at,
                "finished_at": finished_at,
                "task_id": task_id or stage.task_id,
                "runtime_id": runtime_id or stage.runtime_id,
                "runtime_state": runtime_state or stage.runtime_state,
                "evidence_uri": evidence_uri or stage.evidence_uri,
                "detail": detail or stage.detail,
                "blockers": sorted(set(blockers or [])),
            }
        )
        run.stages[index] = stage
        if decision is not None:
            apply_guard_decision(run, decision)
        run.current_stage = next_stage_id(run)
        run.progress = stage_progress(run.stages)
        run.state = derive_run_state(run)
        run.state = pause_stepwise_run(run, completed_stage_id=stage_id, stage_state=state)
        run.blockers = sorted(
            {blocker for item in run.stages for blocker in item.blockers}
        )
        if state in {"failed", "blocked"}:
            run.failure_reason = detail or (stage.blockers[0] if stage.blockers else state)
        if run.state in {"completed", "failed", "cancelled", "rolled_back"}:
            run.finished_at = now
        run.audit.append(
            audit(
                actor,
                "lifecycle_stage_transition",
                stage_id=stage_id,
                stage_state=state,
                run_state=run.state,
                attempt=stage.attempt,
                detail=detail,
            )
        )
        return run

    return mutate_run(run_id, None, update)


def lifecycle_guard_decision(
    run: LifecycleRun,
    stage_id: str,
    transition: str,
):
    if not run.identity_envelope_uri:
        raise LifecycleRunError(
            "lifecycle_identity_envelope_missing",
            "LifecycleRun has no sealed identity envelope.",
            status_code=422,
        )
    runtime_revisions: dict[str, str | None] = {}
    if os.getenv("EVM_LIFECYCLE_GUARD_REQUIRE_RUNTIME_MATCH", "false").lower() == "true":
        from evm.control_panel.host_runtime import read_host_runtime_supervisor

        supervisor = read_host_runtime_supervisor()
        runtime_revisions = {
            child.name: child.source_commit
            for child in supervisor.children
            if child.name in {"lifecycle_worker", "kubernetes_observer"}
            and child.status == "live"
        }
        if supervisor.status != "healthy":
            runtime_revisions = {
                "lifecycle_worker": None,
                "kubernetes_observer": None,
            }
    try:
        return dispatch_lifecycle_guard(
            directory=runtime_path(run.identity_envelope_uri).parent,
            stage_id=stage_id,
            transition=transition,
            run_identity={
                "run_id": run.run_id,
                "profile_id": run.profile_id,
                "profile_version": run.profile_version,
                "profile_digest": run.profile_digest,
                "effective_config_digest": run.effective_config_digest,
                "source_commit": run.source_commit,
                "profile_snapshot_uri": run.profile_snapshot_uri,
                "airflow_config_uri": run.airflow_config_uri,
                "model_config_uri": run.model_config_uri,
                "lifecycle_series_id": run.lifecycle_series_id,
                "attempt_id": run.attempt_id,
                "correlation_id": run.correlation_id,
            },
            runtime_revisions=runtime_revisions,
            require_runtime_match=(
                os.getenv("EVM_LIFECYCLE_GUARD_REQUIRE_RUNTIME_MATCH", "false").lower()
                == "true"
            ),
        )
    except LifecycleGuardBlocked as exc:
        raise LifecycleRunError(
            "lifecycle_guard_blocked",
            ", ".join(exc.blockers),
            status_code=422,
        ) from exc


def apply_guard_decision(run: LifecycleRun, decision) -> None:
    run.guard_decision = decision.decision
    run.guard_authorities = decision.authorities
    run.guard_blockers = decision.blockers


def update_stage_runtime(
    run_id: str,
    stage_id: str,
    *,
    actor: str,
    runtime_state: str,
    runtime_id: str | None = None,
    progress: float | None = None,
    detail: str | None = None,
) -> LifecycleRun:
    def update(run: LifecycleRun) -> LifecycleRun:
        index = stage_index(run, stage_id)
        stage = run.stages[index]
        previous_state = stage.runtime_state
        previous_id = stage.runtime_id
        previous_progress = stage.progress
        next_progress = stage.progress if progress is None else max(0.0, min(1.0, progress))
        run.stages[index] = stage.model_copy(
            update={
                "runtime_state": runtime_state,
                "runtime_id": runtime_id or stage.runtime_id,
                "progress": next_progress,
                "detail": detail or stage.detail,
            }
        )
        run.progress = stage_progress(run.stages)
        run.audit.append(
            audit(
                actor,
                "lifecycle_stage_runtime_updated",
                stage_id=stage_id,
                previous_runtime_state=previous_state,
                runtime_state=runtime_state,
                previous_runtime_id=previous_id,
                runtime_id=runtime_id or previous_id,
                previous_progress=previous_progress,
                progress=next_progress,
            )
        )
        return run

    return mutate_run(run_id, None, update)


def update_run_evidence(
    run_id: str,
    *,
    actor: str,
    cycle_id: str | None = None,
    cycle_snapshot_uri: str | None = None,
    model_matrix_uri: str | None = None,
    readiness_uri: str | None = None,
    real_test_validation_uri: str | None = None,
    ct_snapshot_uri: str | None = None,
    ct_evaluation_uri: str | None = None,
    data_integrity_uri: str | None = None,
    release_submission_uri: str | None = None,
    resource_handoff_uri: str | None = None,
    deployment_intent_id: str | None = None,
    approver: str | None = None,
) -> LifecycleRun:
    def update(run: LifecycleRun) -> LifecycleRun:
        run.cycle_id = cycle_id or run.cycle_id
        run.cycle_snapshot_uri = cycle_snapshot_uri or run.cycle_snapshot_uri
        run.model_matrix_uri = model_matrix_uri or run.model_matrix_uri
        run.readiness_uri = readiness_uri or run.readiness_uri
        run.real_test_validation_uri = (
            real_test_validation_uri or run.real_test_validation_uri
        )
        run.ct_snapshot_uri = ct_snapshot_uri or run.ct_snapshot_uri
        run.ct_evaluation_uri = ct_evaluation_uri or run.ct_evaluation_uri
        run.data_integrity_uri = data_integrity_uri or run.data_integrity_uri
        run.release_submission_uri = (
            release_submission_uri or run.release_submission_uri
        )
        run.resource_handoff_uri = resource_handoff_uri or run.resource_handoff_uri
        run.deployment_intent_id = deployment_intent_id or run.deployment_intent_id
        run.approver = approver or run.approver
        run.audit.append(audit(actor, "lifecycle_evidence_updated"))
        return run

    return mutate_run(run_id, None, update)


def hydrate_cycle_context(run: LifecycleRun) -> LifecycleRun:
    updates: dict[str, str] = {}
    if not run.cycle_id and run.cycle_snapshot_uri:
        path = runtime_path(run.cycle_snapshot_uri)
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("cycle_id"):
            updates["cycle_id"] = str(payload["cycle_id"])
    if not run.experiment_id:
        from evm.control_panel.experiment_runs import experiment_path

        if experiment_path(run.run_id).is_file():
            updates["experiment_id"] = run.run_id
    return run.model_copy(update=updates) if updates else run


def mark_lifecycle_rollback(
    run_id: str,
    *,
    actor: str,
    state: Literal["rolling_back", "rolled_back"],
    detail: str,
) -> LifecycleRun:
    def update(run: LifecycleRun) -> LifecycleRun:
        if state == "rolling_back" and run.state not in {"failed", "running", "blocked"}:
            raise LifecycleRunError(
                "lifecycle_rollback_not_allowed",
                f"LifecycleRun {run_id} cannot roll back from {run.state}.",
            )
        if state == "rolled_back" and run.state != "rolling_back":
            raise LifecycleRunError(
                "lifecycle_rollback_not_running",
                f"LifecycleRun {run_id} is not rolling back.",
            )
        run.state = state
        run.finished_at = utc_now() if state == "rolled_back" else None
        run.current_stage = None if state == "rolled_back" else run.current_stage
        run.audit.append(
            audit(actor, f"lifecycle_run_{state}", detail=detail)
        )
        return run

    return mutate_run(run_id, None, update)


def mark_lifecycle_rollback_failed(
    run_id: str,
    *,
    actor: str,
    detail: str,
) -> LifecycleRun:
    def update(run: LifecycleRun) -> LifecycleRun:
        if run.state != "rolling_back":
            raise LifecycleRunError(
                "lifecycle_rollback_not_running",
                f"LifecycleRun {run_id} is not rolling back.",
            )
        run.state = "failed"
        run.finished_at = utc_now()
        run.failure_reason = detail
        run.blockers = sorted(set([*run.blockers, "lifecycle_rollback_failed"]))
        run.audit.append(audit(actor, "lifecycle_run_rollback_failed", detail=detail))
        return run

    return mutate_run(run_id, None, update)


def mutate_run(
    run_id: str,
    expected_version: int | None,
    update: Callable[[LifecycleRun], LifecycleRun],
) -> LifecycleRun:
    with run_lock(run_id):
        current = get_lifecycle_run(run_id)
        if current is None:
            raise LifecycleRunError(
                "lifecycle_run_not_found",
                f"LifecycleRun {run_id} was not found.",
                status_code=404,
            )
        if expected_version is not None and current.version != expected_version:
            raise LifecycleRunError(
                "lifecycle_run_version_conflict",
                f"Expected version {expected_version}, current version is {current.version}.",
            )
        updated = update(current.model_copy(deep=True))
        updated.version = current.version + 1
        updated.updated_at = utc_now()
        updated.progress = stage_progress(updated.stages)
        write_run(updated)
        return updated


def derive_run_state(run: LifecycleRun) -> LifecycleRunState:
    states = {stage.state for stage in run.stages}
    if run.state == "cancelled" or "cancelled" in states:
        return "cancelled"
    if "failed" in states:
        return "failed"
    if "blocked" in states:
        return "blocked"
    if "waiting_approval" in states:
        return "waiting_approval"
    if all(stage.state in TERMINAL_STAGE_STATES for stage in run.stages):
        return "completed"
    if "running" in states:
        return "running"
    return "queued"


def pause_stepwise_run(
    run: LifecycleRun,
    *,
    completed_stage_id: str,
    stage_state: LifecycleStageState = "completed",
) -> LifecycleRunState:
    if (
        run.execution_mode == "stepwise"
        and stage_state in {"completed", "skipped"}
        and run.current_stage is not None
        and completed_stage_id != "profile_snapshot"
        and run.state == "queued"
    ):
        return "paused"
    return run.state


def next_stage_id(run: LifecycleRun) -> str | None:
    for stage in run.stages:
        if stage.state not in TERMINAL_STAGE_STATES:
            return stage.stage_id
    return None


def current_or_failed_stage(run: LifecycleRun) -> LifecycleStage | None:
    if run.current_stage:
        match = next((item for item in run.stages if item.stage_id == run.current_stage), None)
        if match and match.state in {"failed", "blocked"}:
            return match
    return next((item for item in run.stages if item.state in {"failed", "blocked"}), None)


def stage_progress(stages: list[LifecycleStage]) -> float:
    if not stages:
        return 0.0
    return round(sum(stage.progress for stage in stages) / len(stages), 4)


def stage_index(run: LifecycleRun, stage_id: str) -> int:
    for index, stage in enumerate(run.stages):
        if stage.stage_id == stage_id:
            return index
    raise LifecycleRunError(
        "lifecycle_stage_not_found",
        f"Lifecycle stage {stage_id} was not found.",
        status_code=404,
    )


def assert_dependencies_complete(run: LifecycleRun, stage_index_value: int) -> None:
    incomplete = [
        stage.stage_id
        for stage in run.stages[:stage_index_value]
        if stage.state not in {"completed", "skipped"}
    ]
    if incomplete:
        raise LifecycleRunError(
            "lifecycle_dependency_incomplete",
            f"Incomplete dependencies: {', '.join(incomplete)}",
        )


def write_run(run: LifecycleRun) -> None:
    path = run_path(run.run_id)
    atomic_write_json(path, run.model_dump(mode="json"))
    atomic_write_json(lifecycle_root() / "latest_lifecycle_run.json", run.model_dump(mode="json"))


def read_worker_state(stale_after_seconds: int = 20) -> LifecycleWorkerState:
    path = worker_state_path()
    if not path.is_file():
        return LifecycleWorkerState(status="offline", message="worker heartbeat is missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        observed = LifecycleWorkerState.model_validate(payload)
    except (OSError, ValueError):
        return LifecycleWorkerState(status="offline", message="worker heartbeat is invalid")
    last_seen = parse_utc(observed.last_seen_at)
    if last_seen is None or (datetime.now(UTC) - last_seen).total_seconds() > stale_after_seconds:
        return observed.model_copy(update={"status": "stale", "message": "worker heartbeat is stale"})
    return observed.model_copy(update={"status": "online", "message": None})


def write_worker_state(state: LifecycleWorkerState) -> None:
    atomic_write_json(worker_state_path(), state.model_dump(mode="json"))


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def read_object(value: str) -> dict[str, object]:
    path = runtime_path(value)
    if not path.is_file():
        raise LifecycleRunError(
            "profile_runtime_config_missing",
            f"Runtime config does not exist: {value}",
            status_code=422,
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LifecycleRunError(
            "profile_runtime_config_invalid",
            f"Runtime config is not valid JSON: {value}",
            status_code=422,
        ) from exc
    if not isinstance(payload, dict):
        raise LifecycleRunError(
            "profile_runtime_config_invalid",
            f"Runtime config is not an object: {value}",
            status_code=422,
        )
    return payload


def payload_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, payload: object) -> None:
    atomic_write_json(path, payload)


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        for attempt in range(8):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.025 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def run_lock(run_id: str, timeout_seconds: float = 5.0):
    directory = lifecycle_root() / run_id
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".transition.lock"
    deadline = time.monotonic() + timeout_seconds
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"{os.getpid()} {utc_now()}".encode("utf-8"))
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 120:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise LifecycleRunError(
                    "lifecycle_run_lock_timeout",
                    f"Timed out acquiring LifecycleRun lock for {run_id}.",
                    status_code=503,
                )
            time.sleep(0.05)
    try:
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        lock_path.unlink(missing_ok=True)
