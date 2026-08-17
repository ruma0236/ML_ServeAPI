from __future__ import annotations

import hashlib
import json
import math
import os
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import ConfigDict, Field, field_validator

from evm.control_panel.schemas import AuditEvent, ContractModel
from evm.control_panel.scenarios import EnterpriseScenario, ScenarioCatalogError, get_scenario
from evm.core.config import map_runtime_data_path


WorkloadModelFamily = Literal["vlm", "llm"]
WorkloadRunState = Literal[
    "dry_run",
    "queued",
    "running",
    "waiting_approval",
    "blocked",
    "failed",
    "completed",
    "cancelled",
]
WorkloadStageState = Literal[
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
WorkloadRuntime = Literal[
    "airflow",
    "control-plane",
    "windows-host-cuda",
    "mlflow",
    "serving",
    "prometheus",
]
AdaptationMethod = Literal["lora", "qlora", "inference_only"]
QuantizationMode = Literal["none", "int8", "int4_nf4"]
CapacityProbeFamily = Literal[
    "logistic",
    "probabilistic",
    "online-linear",
    "branch-heavy",
    "incremental",
]


STAGE_SPECS: tuple[tuple[str, str, WorkloadRuntime], ...] = (
    ("data_intake", "Data Intake", "airflow"),
    ("identity_quality_gate", "Identity And Quality Gate", "control-plane"),
    ("gpu_lease", "Exclusive GPU Lease", "windows-host-cuda"),
    ("adaptation", "Bounded Model Adaptation", "windows-host-cuda"),
    ("experiment_tracking", "MLflow Tracking", "mlflow"),
    ("isolated_evaluation", "Isolated Evaluation", "windows-host-cuda"),
    ("artifact_seal", "Artifact Seal", "control-plane"),
    ("approval", "Release Approval", "control-plane"),
    ("staging_serving", "Staging Serving", "serving"),
    ("observability", "Inference And Observability", "prometheus"),
)
ALLOWED_STAGE_TRANSITIONS: dict[WorkloadStageState, set[WorkloadStageState]] = {
    "not_started": {
        "queued",
        "running",
        "waiting_approval",
        "blocked",
        "skipped",
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
TERMINAL_STAGE_STATES = {"completed", "skipped", "cancelled"}
MUTABLE_RESULT_FIELDS = {
    "mlflow_run_id",
    "model_artifact_uri",
    "model_artifact_sha256",
    "evaluation_uri",
    "serving_endpoint",
    "metrics_endpoint",
    "runtime_versions",
    "peak_gpu_allocated_mib",
    "peak_gpu_reserved_mib",
    "quantization_observed",
}


class CapacityProbeRequest(ContractModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evm.s3_capacity_probe_request.v1"] = (
        "evm.s3_capacity_probe_request.v1"
    )
    probe_family: CapacityProbeFamily
    dataset_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    features: list[float] = Field(min_length=28, max_length=28)

    @field_validator("features")
    @classmethod
    def validate_finite_features(cls, values: list[float]) -> list[float]:
        if any(not math.isfinite(value) for value in values):
            raise ValueError("capacity probe features must be finite")
        return values


class CapacityProbeStageTimings(ContractModel):
    model_config = ConfigDict(extra="forbid")

    admission_wait_ms: float = Field(default=0, ge=0)
    queue_wait_ms: float = Field(default=0, ge=0)
    validation_ms: float = Field(ge=0)
    transform_ms: float = Field(ge=0)
    prediction_ms: float = Field(ge=0)
    compute_ms: float = Field(default=0, ge=0)
    total_ms: float = Field(ge=0)


class CapacityProbeRuntime(ContractModel):
    model_config = ConfigDict(extra="forbid")

    api_replica_id: str = Field(min_length=1, max_length=64)
    cpu_worker_count: int = Field(ge=1, le=64)
    worker_slot: int = Field(ge=0, le=63)
    canonical_request_bytes: int = Field(ge=1)


class CapacityProbeResponse(ContractModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evm.s3_capacity_probe_response.v1"] = (
        "evm.s3_capacity_probe_response.v1"
    )
    probe_family: CapacityProbeFamily
    dataset_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    prediction: Literal[0, 1]
    positive_probability: float = Field(ge=0, le=1)
    timings: CapacityProbeStageTimings
    runtime: CapacityProbeRuntime | None = None


class CapacityProbeDescriptor(ContractModel):
    model_config = ConfigDict(extra="forbid")

    probe_family: CapacityProbeFamily
    algorithm: str = Field(min_length=3)
    model_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    feature_count: Literal[28] = 28


class CapacityProbeCatalog(ContractModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evm.s3_capacity_probe_catalog.v1"] = (
        "evm.s3_capacity_probe_catalog.v1"
    )
    dataset_id: Literal["uci-higgs"] = "uci-higgs"
    dataset_version: str = Field(min_length=1)
    dataset_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    split_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_uri: str = Field(min_length=8)
    source_doi: Literal["10.24432/C5V312"] = "10.24432/C5V312"
    license: Literal["CC BY 4.0"] = "CC BY 4.0"
    feature_count: Literal[28] = 28
    probes: list[CapacityProbeDescriptor] = Field(min_length=5, max_length=5)


class ScenarioWorkloadError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ScenarioWorkloadRequest(ContractModel):
    scenario_id: str = Field(min_length=2)
    model_family: WorkloadModelFamily
    model_repository: str = Field(min_length=3)
    model_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    processor_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    adaptation_method: AdaptationMethod
    quantization_requested: QuantizationMode = "none"
    actor: str = Field(min_length=2)
    reason: str = Field(min_length=8)
    dry_run: bool = True
    source_commit: str | None = Field(default=None, pattern=r"^[a-f0-9]{7,40}$")
    source_branch: str | None = None
    dirty_worktree: bool = False
    quality_disposition_uri: str | None = None
    data_view_uri: str | None = None


class WorkloadIdentity(ContractModel):
    scenario_id: str
    dataset_id: str
    dataset_version: str
    manifest_uri: str
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    split_manifest_uri: str
    split_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    data_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    quality_status: str
    quality_report_uri: str
    quality_disposition_uri: str | None = None
    data_view_uri: str | None = None
    model_family: WorkloadModelFamily
    model_repository: str
    model_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    processor_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    source_commit: str | None = None
    source_branch: str | None = None
    dirty_worktree: bool = False
    compute_backend: Literal["windows-host-cuda"] = "windows-host-cuda"
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class WorkloadStage(ContractModel):
    stage_id: str
    label: str
    runtime: WorkloadRuntime
    state: WorkloadStageState = "not_started"
    progress: float = Field(default=0.0, ge=0, le=1)
    started_at: str | None = None
    finished_at: str | None = None
    evidence_uri: str | None = None
    detail: str | None = None
    blockers: list[str] = Field(default_factory=list)


class GpuLease(ContractModel):
    schema_version: Literal["evm.scenario_gpu_lease.v1"] = "evm.scenario_gpu_lease.v1"
    lease_id: str
    fencing_token: str
    run_id: str
    scenario_id: str
    model_family: WorkloadModelFamily
    owner_pid: int = Field(ge=1)
    source_commit: str
    acquired_at: str
    expires_at: str
    state: Literal["active", "released", "expired"] = "active"
    released_at: str | None = None
    release_reason: str | None = None


class ScenarioWorkloadRun(ContractModel):
    schema_version: Literal["evm.scenario_workload_run.v1"] = (
        "evm.scenario_workload_run.v1"
    )
    run_id: str
    state: WorkloadRunState
    version: int = Field(ge=1)
    actor: str
    reason: str
    dry_run: bool
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    current_stage: str | None = None
    progress: float = Field(default=0.0, ge=0, le=1)
    identity: WorkloadIdentity
    adaptation_method: AdaptationMethod
    quantization_requested: QuantizationMode
    quantization_observed: str | None = None
    artifact_root: str
    gpu_lease_id: str | None = None
    gpu_fencing_token: str | None = None
    gpu_lease_state: Literal["not_acquired", "active", "released"] = "not_acquired"
    mlflow_run_id: str | None = None
    model_artifact_uri: str | None = None
    model_artifact_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    evaluation_uri: str | None = None
    serving_endpoint: str | None = None
    metrics_endpoint: str | None = None
    runtime_versions: dict[str, str] = Field(default_factory=dict)
    peak_gpu_allocated_mib: float | None = Field(default=None, ge=0)
    peak_gpu_reserved_mib: float | None = Field(default=None, ge=0)
    evidence_index_uri: str | None = None
    evidence_index_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    blockers: list[str] = Field(default_factory=list)
    stages: list[WorkloadStage] = Field(default_factory=list)
    audit: list[AuditEvent] = Field(default_factory=list)


class ScenarioWorkloadRunList(ContractModel):
    runs: list[ScenarioWorkloadRun] = Field(default_factory=list)
    total: int = 0


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def audit(actor: str, event: str, **details: str | int | float | bool | None) -> AuditEvent:
    return AuditEvent(timestamp=utc_now(), actor=actor, event=event, details=details)


def workload_root() -> Path:
    return Path(
        os.getenv(
            "EVM_SCENARIO_WORKLOAD_ROOT",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scenario_workloads",
        )
    )


def canonical_workload_root() -> Path:
    return Path(
        os.getenv(
            "EVM_SCENARIO_WORKLOAD_CANONICAL_ROOT",
            str(workload_root()),
        )
    )


def workload_artifact_path(value: str | Path) -> Path:
    normalized = str(value).replace("\\", "/")
    canonical = str(canonical_workload_root()).replace("\\", "/").rstrip("/")
    runtime = workload_root()
    if canonical and normalized.lower().startswith(canonical.lower()):
        relative = normalized[len(canonical) :].lstrip("/")
        return runtime / Path(relative)
    return map_runtime_data_path(value)


def canonical_data_path(value: str | Path) -> Path:
    normalized = str(value).replace("\\", "/")
    host_root = os.getenv("EVM_HOST_DATA_ROOT", "").replace("\\", "/").rstrip("/")
    mount_root = os.getenv("EVM_DATA_MOUNT_ROOT", "").replace("\\", "/").rstrip("/")
    if host_root and mount_root and normalized.lower().startswith(mount_root.lower()):
        normalized = f"{host_root}{normalized[len(mount_root):]}"
    return Path(normalized)


def resolve_existing_data_path(value: str | Path) -> Path:
    candidates = (
        workload_artifact_path(value),
        canonical_data_path(value),
        map_runtime_data_path(value),
        Path(value),
    )
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).replace("\\", "/").lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate
    return candidates[0]


def gpu_lease_root() -> Path:
    return Path(
        os.getenv(
            "EVM_SCENARIO_GPU_LEASE_ROOT",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/runtime/gpu-lease",
        )
    )


def run_path(run_id: str) -> Path:
    return workload_root() / run_id / "workload_run.json"


def create_workload_run(request: ScenarioWorkloadRequest) -> ScenarioWorkloadRun:
    try:
        scenario = get_scenario(request.scenario_id)
    except (OSError, ValueError, ScenarioCatalogError) as exc:
        raise ScenarioWorkloadError(
            "scenario_catalog_invalid", str(exc), status_code=422
        ) from exc
    if scenario is None:
        raise ScenarioWorkloadError("scenario_not_found", request.scenario_id, status_code=404)
    validate_family(scenario, request.model_family)
    source_commit = request.source_commit or os.getenv("EVM_GIT_COMMIT", "").strip() or None
    source_branch = request.source_branch or os.getenv("EVM_GIT_BRANCH", "").strip() or None
    if not request.dry_run and not source_commit:
        raise ScenarioWorkloadError(
            "source_revision_missing", "Executable workload requires an exact source revision.", status_code=422
        )
    if not request.dry_run and request.dirty_worktree:
        raise ScenarioWorkloadError(
            "dirty_worktree_blocked", "Executable workload requires a clean source tree.", status_code=422
        )
    identity = resolve_workload_identity(
        scenario,
        request,
        source_commit=source_commit,
        source_branch=source_branch,
    )
    created_at = utc_now()
    run_id = (
        f"scenario-workload-{created_at.replace(':', '').replace('-', '').replace('Z', '')}"
        f"-{uuid4().hex[:8]}"
    )
    artifact_root = canonical_workload_root() / run_id
    stages = [
        WorkloadStage(stage_id=stage_id, label=label, runtime=runtime)
        for stage_id, label, runtime in STAGE_SPECS
    ]
    state: WorkloadRunState = "dry_run" if request.dry_run else "queued"
    run = ScenarioWorkloadRun(
        run_id=run_id,
        state=state,
        version=1,
        actor=request.actor,
        reason=request.reason,
        dry_run=request.dry_run,
        created_at=created_at,
        updated_at=created_at,
        identity=identity,
        adaptation_method=request.adaptation_method,
        quantization_requested=request.quantization_requested,
        artifact_root=str(artifact_root),
        stages=stages,
        audit=[
            audit(
                request.actor,
                "scenario_workload_created",
                state=state,
                scenario_id=request.scenario_id,
                model_family=request.model_family,
                identity_sha256=identity.identity_sha256,
            )
        ],
    )
    write_run(run)
    return run


def validate_family(scenario: EnterpriseScenario, family: WorkloadModelFamily) -> None:
    expected = "image_text" if family == "vlm" else "text"
    if scenario.modality != expected:
        raise ScenarioWorkloadError(
            "scenario_model_family_mismatch",
            f"{family} requires {expected}; scenario is {scenario.modality}.",
            status_code=422,
        )


def resolve_workload_identity(
    scenario: EnterpriseScenario,
    request: ScenarioWorkloadRequest,
    *,
    source_commit: str | None,
    source_branch: str | None,
) -> WorkloadIdentity:
    manifest_path = mapped_path(scenario.dataset.manifest_uri)
    split_path = mapped_path(scenario.dataset.split_manifest_uri)
    quality_path = quality_report_path(scenario)
    manifest_sha256 = require_file_sha256(manifest_path, "scenario_manifest")
    source_manifest_sha256 = manifest_sha256
    split_sha256 = require_file_sha256(split_path, "scenario_split_manifest")
    split = read_json_object(split_path, "scenario_split_manifest")
    quality = read_json_object(quality_path, "scenario_quality_report")
    expected_manifest = str(split.get("manifest_sha256") or "")
    if expected_manifest != manifest_sha256:
        raise ScenarioWorkloadError(
            "scenario_manifest_identity_mismatch",
            f"split={expected_manifest or 'missing'} actual={manifest_sha256}",
            status_code=422,
        )
    data_identity = str(split.get("identity_sha256") or "")
    if not is_sha256(data_identity):
        raise ScenarioWorkloadError(
            "scenario_data_identity_missing", "Split manifest has no valid identity.", status_code=422
        )
    quality_status = str(quality.get("status") or "")
    disposition_uri: str | None = None
    if quality_status != "pass":
        if quality_status != "review_required" or not request.quality_disposition_uri:
            raise ScenarioWorkloadError(
                "scenario_quality_not_approved",
                f"Quality status is {quality_status or 'missing'}.",
                status_code=422,
            )
        disposition = validate_quality_disposition(
            request.quality_disposition_uri,
            dataset_version=scenario.dataset.dataset_version,
            input_manifest_sha256=manifest_sha256,
        )
        disposition_uri = request.quality_disposition_uri
        manifest_path = mapped_path(str(disposition["output_manifest_uri"]))
        split_path = mapped_path(str(disposition["output_split_manifest_uri"]))
        manifest_sha256 = require_file_sha256(manifest_path, "approved_manifest")
        split_sha256 = require_file_sha256(split_path, "approved_split_manifest")
        if manifest_sha256 != disposition["output_manifest_sha256"]:
            raise ScenarioWorkloadError(
                "quality_disposition_manifest_mismatch",
                "Approved output manifest digest does not match bytes.",
                status_code=422,
            )
        approved_split = read_json_object(split_path, "approved_split_manifest")
        data_identity = str(approved_split.get("identity_sha256") or "")
        if data_identity != disposition["output_identity_sha256"]:
            raise ScenarioWorkloadError(
                "quality_disposition_identity_mismatch",
                "Approved split identity does not match the disposition.",
                status_code=422,
            )
        quality_status = "approved"
    data_view_uri: str | None = None
    if request.data_view_uri:
        view = validate_data_view(
            request.data_view_uri,
            dataset_version=scenario.dataset.dataset_version,
            input_manifest_sha256={source_manifest_sha256, manifest_sha256},
        )
        data_view_uri = request.data_view_uri
        manifest_path = mapped_path(str(view["output_manifest_uri"]))
        split_path = mapped_path(str(view["output_split_manifest_uri"]))
        manifest_sha256 = require_file_sha256(manifest_path, "data_view_manifest")
        split_sha256 = require_file_sha256(split_path, "data_view_split_manifest")
        if manifest_sha256 != view["output_manifest_sha256"]:
            raise ScenarioWorkloadError(
                "data_view_manifest_mismatch",
                "Data-view manifest digest does not match bytes.",
                status_code=422,
            )
        view_split = read_json_object(split_path, "data_view_split_manifest")
        data_identity = str(view_split.get("identity_sha256") or "")
        if data_identity != view["output_identity_sha256"]:
            raise ScenarioWorkloadError(
                "data_view_identity_mismatch",
                "Data-view split identity does not match the contract.",
                status_code=422,
            )
    processor_revision = request.processor_revision or request.model_revision
    material = {
        "scenario_id": scenario.scenario_id,
        "dataset_id": scenario.dataset.dataset_id,
        "dataset_version": scenario.dataset.dataset_version,
        "manifest_sha256": manifest_sha256,
        "split_manifest_sha256": split_sha256,
        "data_identity_sha256": data_identity,
        "data_view_uri": data_view_uri or "",
        "model_family": request.model_family,
        "model_repository": request.model_repository,
        "model_revision": request.model_revision,
        "processor_revision": processor_revision,
        "source_commit": source_commit or "",
        "source_branch": source_branch or "",
        "dirty_worktree": request.dirty_worktree,
        "compute_backend": "windows-host-cuda",
    }
    return WorkloadIdentity(
        scenario_id=scenario.scenario_id,
        dataset_id=scenario.dataset.dataset_id,
        dataset_version=scenario.dataset.dataset_version,
        manifest_uri=str(canonical_data_path(manifest_path)),
        manifest_sha256=manifest_sha256,
        split_manifest_uri=str(canonical_data_path(split_path)),
        split_manifest_sha256=split_sha256,
        data_identity_sha256=data_identity,
        quality_status=quality_status,
        quality_report_uri=str(canonical_data_path(quality_path)),
        quality_disposition_uri=(
            str(canonical_data_path(disposition_uri)) if disposition_uri else None
        ),
        data_view_uri=str(canonical_data_path(data_view_uri)) if data_view_uri else None,
        model_family=request.model_family,
        model_repository=request.model_repository,
        model_revision=request.model_revision,
        processor_revision=processor_revision,
        source_commit=source_commit,
        source_branch=source_branch,
        dirty_worktree=request.dirty_worktree,
        identity_sha256=payload_sha256(material),
    )


def validate_quality_disposition(
    uri: str,
    *,
    dataset_version: str,
    input_manifest_sha256: str,
) -> dict[str, Any]:
    payload = read_json_object(mapped_path(uri), "quality_disposition")
    blockers: list[str] = []
    if payload.get("schema_version") != "evm.scenario_quality_disposition.v1":
        blockers.append("quality_disposition_schema_invalid")
    if payload.get("decision") != "approved":
        blockers.append("quality_disposition_not_approved")
    if payload.get("dataset_version") != dataset_version:
        blockers.append("quality_disposition_dataset_version_mismatch")
    if payload.get("input_manifest_sha256") != input_manifest_sha256:
        blockers.append("quality_disposition_input_manifest_mismatch")
    for key in ("output_manifest_sha256", "output_identity_sha256"):
        if not is_sha256(str(payload.get(key) or "")):
            blockers.append(f"{key}_invalid")
    for key in ("output_manifest_uri", "output_split_manifest_uri", "approver", "approved_at"):
        if not str(payload.get(key) or "").strip():
            blockers.append(f"{key}_missing")
    if blockers:
        raise ScenarioWorkloadError(
            "quality_disposition_invalid", ",".join(sorted(blockers)), status_code=422
        )
    return payload


def validate_data_view(
    uri: str,
    *,
    dataset_version: str,
    input_manifest_sha256: str | set[str],
) -> dict[str, Any]:
    payload = read_json_object(mapped_path(uri), "scenario_data_view")
    blockers: list[str] = []
    if payload.get("schema_version") != "evm.scenario_data_view.v1":
        blockers.append("data_view_schema_invalid")
    if payload.get("status") != "pass":
        blockers.append("data_view_not_passing")
    if payload.get("source_dataset_version") != dataset_version:
        blockers.append("data_view_dataset_version_mismatch")
    accepted_input_digests = (
        {input_manifest_sha256}
        if isinstance(input_manifest_sha256, str)
        else input_manifest_sha256
    )
    if payload.get("input_manifest_sha256") not in accepted_input_digests:
        blockers.append("data_view_input_manifest_mismatch")
    for key in ("output_manifest_sha256", "output_identity_sha256"):
        if not is_sha256(str(payload.get(key) or "")):
            blockers.append(f"{key}_invalid")
    for key in ("output_manifest_uri", "output_split_manifest_uri", "recipe_id"):
        if not str(payload.get(key) or "").strip():
            blockers.append(f"{key}_missing")
    if blockers:
        raise ScenarioWorkloadError("data_view_invalid", ",".join(sorted(blockers)), status_code=422)
    return payload


def transition_workload_stage(
    run_id: str,
    stage_id: str,
    state: WorkloadStageState,
    *,
    actor: str,
    expected_version: int | None = None,
    evidence_uri: str | None = None,
    detail: str | None = None,
    blockers: list[str] | None = None,
) -> ScenarioWorkloadRun:
    with file_lock(workload_root() / run_id / ".transition.lock", "workload_transition"):
        run = get_workload_run(run_id)
        if expected_version is not None and run.version != expected_version:
            raise ScenarioWorkloadError(
                "workload_version_conflict",
                f"Expected {expected_version}; found {run.version}.",
            )
        index = stage_index(run, stage_id)
        stage = run.stages[index]
        if state not in ALLOWED_STAGE_TRANSITIONS[stage.state]:
            raise ScenarioWorkloadError(
                "workload_stage_transition_invalid",
                f"{stage_id}: {stage.state} -> {state}",
            )
        if state not in {"blocked", "cancelled"}:
            incomplete = [
                item.stage_id
                for item in run.stages[:index]
                if item.state not in {"completed", "skipped"}
            ]
            if incomplete:
                raise ScenarioWorkloadError(
                    "workload_dependency_incomplete", ",".join(incomplete)
                )
        now = utc_now()
        if state in {"queued", "running", "waiting_approval"} and not stage.started_at:
            stage.started_at = now
        if state in {"completed", "skipped", "blocked", "failed", "cancelled"}:
            stage.finished_at = now
        stage.state = state
        stage.progress = 1.0 if state in {"completed", "skipped"} else 0.0
        stage.evidence_uri = evidence_uri or stage.evidence_uri
        stage.detail = detail
        stage.blockers = sorted(set(blockers or []))
        run.stages[index] = stage
        run.version += 1
        run.updated_at = now
        run.current_stage = stage_id
        if run.started_at is None and state in {"queued", "running"}:
            run.started_at = now
        run.progress = round(
            sum(item.progress for item in run.stages) / max(len(run.stages), 1), 6
        )
        run.blockers = sorted(
            {blocker for item in run.stages for blocker in item.blockers if blocker}
        )
        if state == "blocked":
            run.state = "blocked"
        elif state == "failed":
            run.state = "failed"
        elif state == "cancelled":
            run.state = "cancelled"
        elif state == "waiting_approval":
            run.state = "waiting_approval"
        else:
            run.state = "running"
        run.audit.append(
            audit(
                actor,
                "scenario_workload_stage_transitioned",
                stage_id=stage_id,
                stage_state=state,
                evidence_uri=evidence_uri,
            )
        )
        write_run(run)
        return run


def update_workload_results(
    run_id: str,
    *,
    actor: str,
    expected_version: int | None = None,
    **updates: Any,
) -> ScenarioWorkloadRun:
    unknown = sorted(set(updates) - MUTABLE_RESULT_FIELDS)
    if unknown:
        raise ScenarioWorkloadError("workload_result_field_forbidden", ",".join(unknown))
    with file_lock(workload_root() / run_id / ".transition.lock", "workload_update"):
        run = get_workload_run(run_id)
        if expected_version is not None and run.version != expected_version:
            raise ScenarioWorkloadError(
                "workload_version_conflict", f"Expected {expected_version}; found {run.version}."
            )
        for key, value in updates.items():
            setattr(run, key, value)
        run.version += 1
        run.updated_at = utc_now()
        run.audit.append(audit(actor, "scenario_workload_results_updated", fields=",".join(sorted(updates))))
        validated = ScenarioWorkloadRun.model_validate(run.model_dump(mode="json"))
        write_run(validated)
        return validated


def acquire_gpu_lease(
    run_id: str,
    *,
    owner_pid: int | None = None,
    ttl_seconds: int = 7200,
) -> GpuLease:
    if ttl_seconds < 60:
        raise ScenarioWorkloadError("gpu_lease_ttl_invalid", str(ttl_seconds), status_code=422)
    run = get_workload_run(run_id)
    if run.dry_run:
        raise ScenarioWorkloadError("gpu_lease_dry_run_forbidden", run_id)
    if not run.identity.source_commit:
        raise ScenarioWorkloadError("gpu_lease_source_revision_missing", run_id, status_code=422)
    root = gpu_lease_root()
    with file_lock(root / ".gpu-lease.lock", "gpu_lease"):
        current = read_active_gpu_lease()
        now = datetime.now(UTC).replace(microsecond=0)
        if current is not None and current.state == "active":
            if parse_utc(current.expires_at) > now:
                if current.run_id == run_id:
                    return current
                raise ScenarioWorkloadError(
                    "gpu_lease_conflict", f"GPU is leased by {current.run_id}."
                )
            archive_lease(current.model_copy(update={"state": "expired"}))
        acquired_at = now.isoformat().replace("+00:00", "Z")
        lease = GpuLease(
            lease_id=f"gpu-lease-{uuid4().hex}",
            fencing_token=uuid4().hex,
            run_id=run_id,
            scenario_id=run.identity.scenario_id,
            model_family=run.identity.model_family,
            owner_pid=owner_pid or os.getpid(),
            source_commit=run.identity.source_commit,
            acquired_at=acquired_at,
            expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z"),
        )
        atomic_write_json(root / "active.json", lease.model_dump(mode="json"))
    with file_lock(workload_root() / run_id / ".transition.lock", "workload_update"):
        run = get_workload_run(run_id)
        run.gpu_lease_id = lease.lease_id
        run.gpu_fencing_token = lease.fencing_token
        run.gpu_lease_state = "active"
        run.version += 1
        run.updated_at = utc_now()
        run.audit.append(
            audit(
                "scenario-workload-runtime",
                "gpu_lease_acquired",
                lease_id=lease.lease_id,
                owner_pid=lease.owner_pid,
            )
        )
        write_run(run)
    return lease


def assert_gpu_lease_owner(run_id: str) -> GpuLease:
    run = get_workload_run(run_id)
    lease = read_active_gpu_lease()
    if (
        lease is None
        or lease.state != "active"
        or lease.run_id != run_id
        or lease.lease_id != run.gpu_lease_id
        or lease.fencing_token != run.gpu_fencing_token
        or parse_utc(lease.expires_at) <= datetime.now(UTC)
    ):
        raise ScenarioWorkloadError("gpu_lease_identity_mismatch", run_id)
    return lease


def release_gpu_lease(run_id: str, *, lease_id: str, fencing_token: str, reason: str) -> GpuLease:
    root = gpu_lease_root()
    with file_lock(root / ".gpu-lease.lock", "gpu_lease"):
        lease = read_active_gpu_lease()
        if (
            lease is None
            or lease.run_id != run_id
            or lease.lease_id != lease_id
            or lease.fencing_token != fencing_token
        ):
            raise ScenarioWorkloadError("gpu_lease_release_identity_mismatch", run_id)
        released = lease.model_copy(
            update={
                "state": "released",
                "released_at": utc_now(),
                "release_reason": reason,
            }
        )
        archive_lease(released)
        (root / "active.json").unlink(missing_ok=True)
    with file_lock(workload_root() / run_id / ".transition.lock", "workload_update"):
        run = get_workload_run(run_id)
        run.gpu_lease_state = "released"
        run.version += 1
        run.updated_at = utc_now()
        run.audit.append(
            audit(
                "scenario-workload-runtime",
                "gpu_lease_released",
                lease_id=lease_id,
                reason=reason,
            )
        )
        write_run(run)
    return released


def seal_workload_run(run_id: str, *, actor: str) -> ScenarioWorkloadRun:
    with file_lock(workload_root() / run_id / ".transition.lock", "workload_seal"):
        run = get_workload_run(run_id)
        incomplete = [
            stage.stage_id for stage in run.stages if stage.state not in TERMINAL_STAGE_STATES
        ]
        if incomplete:
            raise ScenarioWorkloadError("workload_stages_incomplete", ",".join(incomplete))
        if run.gpu_lease_state != "released":
            raise ScenarioWorkloadError("workload_gpu_lease_not_released", run.gpu_lease_state)
        required = {
            "mlflow_run_id": run.mlflow_run_id,
            "model_artifact_uri": run.model_artifact_uri,
            "model_artifact_sha256": run.model_artifact_sha256,
            "evaluation_uri": run.evaluation_uri,
            "serving_endpoint": run.serving_endpoint,
            "metrics_endpoint": run.metrics_endpoint,
        }
        missing = sorted(key for key, value in required.items() if not value)
        if missing:
            raise ScenarioWorkloadError("workload_result_identity_incomplete", ",".join(missing))
        entries: list[dict[str, Any]] = []
        for stage in run.stages:
            if not stage.evidence_uri:
                raise ScenarioWorkloadError(
                    "workload_stage_evidence_missing", stage.stage_id
                )
            path = resolve_existing_data_path(stage.evidence_uri)
            digest = require_file_sha256(path, f"stage_{stage.stage_id}_evidence")
            entries.append(
                {
                    "stage_id": stage.stage_id,
                    "uri": str(path),
                    "size_bytes": path.stat().st_size,
                    "sha256": digest,
                }
            )
        artifact_path = resolve_existing_data_path(str(run.model_artifact_uri))
        artifact_digest = require_file_sha256(artifact_path, "model_artifact")
        if artifact_digest != run.model_artifact_sha256:
            raise ScenarioWorkloadError(
                "workload_model_artifact_digest_mismatch", artifact_digest
            )
        index_path = workload_artifact_path(run.artifact_root) / "evidence-index.json"
        payload = {
            "schema_version": "evm.scenario_workload_evidence_index.v1",
            "run_id": run.run_id,
            "identity_sha256": run.identity.identity_sha256,
            "model_artifact_sha256": artifact_digest,
            "entries": entries,
            "entry_count": len(entries),
            "sealed_at": utc_now(),
        }
        atomic_write_json(index_path, payload)
        index_sha256 = file_sha256(index_path)
        run.evidence_index_uri = str(index_path)
        run.evidence_index_sha256 = index_sha256
        run.state = "completed"
        run.progress = 1.0
        run.finished_at = utc_now()
        run.updated_at = run.finished_at
        run.version += 1
        run.audit.append(
            audit(
                actor,
                "scenario_workload_evidence_sealed",
                evidence_index_sha256=index_sha256,
                entry_count=len(entries),
            )
        )
        write_run(run)
        return run


def fail_workload_run(
    run_id: str,
    *,
    actor: str,
    blocker: str,
    evidence_uri: str,
) -> ScenarioWorkloadRun:
    with file_lock(workload_root() / run_id / ".transition.lock", "workload_failure"):
        run = get_workload_run(run_id)
        if run.state in {"failed", "blocked", "completed", "cancelled"}:
            return run
        now = utc_now()
        run.state = "failed"
        run.blockers = sorted(set([*run.blockers, blocker]))
        run.finished_at = now
        run.updated_at = now
        run.version += 1
        run.audit.append(
            audit(
                actor,
                "scenario_workload_closure_failed",
                blocker=blocker,
                evidence_uri=evidence_uri,
            )
        )
        write_run(run)
        return run


def get_workload_run(run_id: str) -> ScenarioWorkloadRun:
    path = run_path(run_id)
    if not path.is_file():
        raise ScenarioWorkloadError("scenario_workload_not_found", run_id, status_code=404)
    try:
        return ScenarioWorkloadRun.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise ScenarioWorkloadError(
            "scenario_workload_invalid", run_id, status_code=500
        ) from exc


def list_workload_runs(limit: int = 100) -> ScenarioWorkloadRunList:
    runs: list[ScenarioWorkloadRun] = []
    for path in workload_root().glob("*/workload_run.json"):
        try:
            runs.append(
                ScenarioWorkloadRun.model_validate(json.loads(path.read_text(encoding="utf-8")))
            )
        except (OSError, ValueError):
            continue
    runs.sort(key=lambda item: item.created_at, reverse=True)
    return ScenarioWorkloadRunList(runs=runs[: max(1, min(limit, 500))], total=len(runs))


def read_active_gpu_lease() -> GpuLease | None:
    path = gpu_lease_root() / "active.json"
    if not path.is_file():
        return None
    try:
        return GpuLease.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise ScenarioWorkloadError("gpu_lease_state_invalid", str(path), status_code=503) from exc


def archive_lease(lease: GpuLease) -> None:
    path = gpu_lease_root() / "history" / f"{lease.lease_id}.json"
    atomic_write_json(path, lease.model_dump(mode="json"))


def stage_index(run: ScenarioWorkloadRun, stage_id: str) -> int:
    for index, stage in enumerate(run.stages):
        if stage.stage_id == stage_id:
            return index
    raise ScenarioWorkloadError("workload_stage_not_found", stage_id, status_code=404)


def quality_report_path(scenario: EnterpriseScenario) -> Path:
    manifest = mapped_path(scenario.dataset.manifest_uri)
    return manifest.parent.parent / "evidence" / "quality_report.json"


def mapped_path(value: str | Path) -> Path:
    return map_runtime_data_path(value)


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ScenarioWorkloadError(f"{label}_missing", str(path), status_code=422)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScenarioWorkloadError(f"{label}_invalid", str(path), status_code=422) from exc
    if not isinstance(payload, dict):
        raise ScenarioWorkloadError(f"{label}_invalid", str(path), status_code=422)
    return payload


def require_file_sha256(path: Path, label: str) -> str:
    if not path.is_file():
        raise ScenarioWorkloadError(f"{label}_missing", str(path), status_code=422)
    return file_sha256(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def payload_sha256(payload: object) -> str:
    material = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def write_run(run: ScenarioWorkloadRun) -> None:
    atomic_write_json(run_path(run.run_id), run.model_dump(mode="json"))
    atomic_write_json(workload_root() / "latest-workload-run.json", run.model_dump(mode="json"))


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
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
def file_lock(path: Path, label: str, timeout_seconds: float = 10.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"{os.getpid()} {utc_now()}".encode("utf-8"))
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > 300:
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise ScenarioWorkloadError(f"{label}_lock_timeout", str(path), status_code=503)
            time.sleep(0.05)
    try:
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        path.unlink(missing_ok=True)
