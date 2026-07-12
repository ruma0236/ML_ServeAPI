from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import Field

from evm.control_panel.operations import create_task_assignment
from evm.control_panel.schemas import (
    ContractModel,
    TaskAssignment,
    TaskAssignmentRequest,
)


CapabilityStatus = Literal["wired", "partial", "not_wired"]
ExecutionScope = Literal["data_cycle", "full_lifecycle"]
PlanState = Literal["not_started", "ready", "blocked"]
_PROFILE_LOCK = RLock()


class SplitPolicy(ContractModel):
    seed: int = Field(default=20260712, ge=1)
    train: float = Field(default=0.6, gt=0, lt=1)
    validation: float = Field(default=0.2, gt=0, lt=1)
    test: float = Field(default=0.2, gt=0, lt=1)
    stratified: bool = True
    cross_validation_enabled: bool = False
    cross_validation_folds: int = Field(default=5, ge=2, le=20)
    holdout_split: Literal["validation", "test"] = "test"
    immutable_holdout: bool = True
    allow_holdout_in_training: bool = False


class DataProfile(ContractModel):
    dataset_name: str = "visa"
    dataset_version: str = "visa-open-data-e35d93d5561f"
    source_manifest_uri: str = (
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/validated/visa/"
        "mvi_quality_manifest.jsonl"
    )
    split_manifest_uri: str = (
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/validated/visa/"
        "shards/shard_index.json"
    )
    split_manifest_sha256: str = (
        "64043adeeca6654467b842c7b5bb8fc64ce8a0b2c78ca158623164f829a38cd0"
    )
    fail_on_empty: bool = True
    fail_on_quality_error: bool = True
    duplicate_severity: Literal["info", "warn", "error"] = "warn"
    dimension_severity: Literal["info", "warn", "error"] = "warn"
    max_review_samples: int = Field(default=128, ge=0)
    records_per_shard: int = Field(default=512, ge=16, le=10000)
    split: SplitPolicy = Field(default_factory=SplitPolicy)


class ModelProfile(ContractModel):
    framework: Literal["torch"] = "torch"
    architecture: Literal["efficientnet-b0", "efficientnet-b7"] = "efficientnet-b0"
    pretrained: bool = True
    freeze_backbone: bool = False
    input_size: int = Field(default=224, ge=64, le=1024)
    batch_size: int = Field(default=64, ge=1, le=512)
    epochs: int = Field(default=20, ge=1, le=500)
    optimizer: Literal["adamw", "sgd"] = "adamw"
    learning_rate: float = Field(default=0.0001, gt=0, le=1)
    weight_decay: float = Field(default=0.0001, ge=0, le=1)
    mixed_precision: bool = True
    class_weighted_loss: bool = True
    early_stop_metric: Literal["accuracy", "f1", "auroc"] = "accuracy"
    early_stop_threshold: float = Field(default=0.93, gt=0, le=1)
    early_stop_min_epochs: int = Field(default=2, ge=1)
    early_stop_patience: int = Field(default=3, ge=1, le=50)
    tuning_mode: Literal["manual", "grid", "bayesian"] = "manual"
    max_trials: int = Field(default=1, ge=1, le=200)


class ExperimentProfile(ContractModel):
    mlflow_experiment_name: str = "enterprise-vision-profile-runs"
    primary_metric: Literal["accuracy", "f1", "auroc"] = "f1"
    repeats: int = Field(default=1, ge=1, le=20)
    ab_test_enabled: bool = False
    control_candidate_id: str | None = None
    challenger_candidate_id: str | None = None
    challenger_traffic_percent: int = Field(default=10, ge=1, le=99)


class GateProfile(ContractModel):
    promotion_min_accuracy: float = Field(default=0.80, ge=0, le=1)
    promotion_min_f1: float = Field(default=0.75, ge=0, le=1)
    promotion_min_auroc: float = Field(default=0.80, ge=0, le=1)
    isolated_ct_dataset_required: bool = True
    ct_dataset_split: Literal["validation", "test"] = "test"
    require_ci: bool = True
    require_cd: bool = True
    require_ct: bool = True
    require_drift_review: bool = True
    approval_policy: Literal["manual", "two_person", "change_ticket"] = "two_person"
    target_environment: Literal["dev", "test", "staging", "pre-production", "production"] = "staging"
    target_namespace: str = "evm-staging"


class ResourceProfile(ContractModel):
    compute_target: Literal["windows-rtx-4080-super", "mac-mini-m4-pro", "cpu-local"] = (
        "windows-rtx-4080-super"
    )
    gpu_count: int = Field(default=1, ge=0, le=8)
    cpu_request: int = Field(default=6, ge=1, le=64)
    memory_gb: int = Field(default=16, ge=2, le=256)
    max_parallel_trials: int = Field(default=1, ge=1, le=16)


class PipelineRunProfile(ContractModel):
    schema_version: Literal["evm.pipeline_profile.v1"] = "evm.pipeline_profile.v1"
    profile_name: str = "standard-b0-manual-tuning"
    description: str = "Human-authored real-data pipeline profile."
    owner: str = "ml-platform"
    execution_scope: ExecutionScope = "full_lifecycle"
    base_airflow_config: str = "configs/airflow.toml"
    base_model_config: str = "configs/w7_efficientnet_real_test.toml"
    data: DataProfile = Field(default_factory=DataProfile)
    model: ModelProfile = Field(default_factory=ModelProfile)
    experiment: ExperimentProfile = Field(default_factory=ExperimentProfile)
    gates: GateProfile = Field(default_factory=GateProfile)
    resources: ResourceProfile = Field(default_factory=ResourceProfile)


class PipelineCapability(ContractModel):
    capability_id: str
    label: str
    status: CapabilityStatus
    active: bool
    detail: str


class PipelinePlanStage(ContractModel):
    stage_id: str
    label: str
    runtime: Literal["airflow", "mlflow", "kubernetes", "control-plane"]
    state: PlanState
    progress: float = Field(ge=0, le=1)
    detail: str


class PipelineProfileValidation(ContractModel):
    status: Literal["ready", "blocked"]
    valid: bool
    executable: bool
    checked_at: str
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    capabilities: list[PipelineCapability] = Field(default_factory=list)
    stages: list[PipelinePlanStage] = Field(default_factory=list)


class PipelineProfileRecord(ContractModel):
    profile_id: str
    version: int = Field(ge=1)
    digest: str
    created_at: str
    profile: PipelineRunProfile
    validation: PipelineProfileValidation
    profile_uri: str
    airflow_config_uri: str
    airflow_runtime_uri: str
    model_config_uri: str
    model_runtime_uri: str


class PipelineProfileList(ContractModel):
    profiles: list[PipelineProfileRecord] = Field(default_factory=list)


class PipelineProfileLaunchRequest(ContractModel):
    actor: str = "ml-platform"
    reason: str = "Launch validated pipeline profile"
    dry_run: bool = True


class PipelineProfileLaunch(ContractModel):
    profile_id: str
    version: int
    validation: PipelineProfileValidation
    task: TaskAssignment | None = None


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def profile_root() -> Path:
    return Path(
        os.getenv(
            "EVM_PIPELINE_PROFILE_ROOT",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/pipeline_profiles",
        )
    )


def runtime_root() -> str:
    return os.getenv(
        "EVM_PIPELINE_PROFILE_RUNTIME_ROOT",
        "/mnt/evm-data/artifacts/w7/pipeline_profiles",
    ).rstrip("/")


def project_root() -> Path:
    configured = os.getenv("EVM_PROJECT_ROOT", "").strip()
    if configured:
        candidate = Path(configured)
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path(__file__).resolve().parents[3]


def default_profile() -> PipelineRunProfile:
    return PipelineRunProfile()


def validate_profile(profile: PipelineRunProfile) -> PipelineProfileValidation:
    blockers: list[str] = []
    warnings: list[str] = []
    split = profile.data.split
    split_total = split.train + split.validation + split.test
    if abs(split_total - 1.0) > 0.000001:
        blockers.append("split_ratios_must_sum_to_one")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", profile.data.split_manifest_sha256):
        blockers.append("split_manifest_sha256_invalid")
    if allowed_base_config(profile.base_airflow_config) is None:
        blockers.append("airflow_base_config_not_allowed")
    if allowed_base_config(profile.base_model_config) is None:
        blockers.append("model_base_config_not_allowed")
    source_manifest = resolve_data_path(profile.data.source_manifest_uri)
    if source_manifest is None or not source_manifest.is_file():
        blockers.append("source_manifest_not_found")
    elif source_manifest.stat().st_size == 0:
        blockers.append("source_manifest_empty")
    split_manifest = resolve_data_path(profile.data.split_manifest_uri)
    if split_manifest is None or not split_manifest.is_file():
        blockers.append("split_manifest_not_found")
    else:
        try:
            split_payload = json.loads(split_manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            blockers.append("split_manifest_invalid_json")
        else:
            split_identity = None
            if isinstance(split_payload, dict):
                split_identity = split_payload.get("identity_sha256") or split_payload.get(
                    "source_shard_identity_sha256"
                )
            if not split_identity:
                blockers.append("split_manifest_identity_missing")
            elif str(split_identity).lower() != profile.data.split_manifest_sha256.lower():
                blockers.append("split_manifest_identity_mismatch")
    if split.allow_holdout_in_training:
        blockers.append("holdout_training_overlap_forbidden")
    if profile.gates.isolated_ct_dataset_required:
        if not split.immutable_holdout:
            blockers.append("ct_holdout_must_be_immutable")
        if profile.gates.ct_dataset_split != split.holdout_split:
            blockers.append("ct_split_must_match_holdout_split")
    if profile.model.early_stop_min_epochs > profile.model.epochs:
        blockers.append("early_stop_min_epochs_exceeds_epochs")
    if profile.model.early_stop_threshold < profile.gates.promotion_min_accuracy:
        warnings.append("early_stop_threshold_below_promotion_accuracy")
    if profile.model.architecture == "efficientnet-b0" and profile.model.input_size != 224:
        warnings.append("efficientnet_b0_nonstandard_input_size")
    if profile.model.architecture == "efficientnet-b7" and profile.model.input_size != 600:
        warnings.append("efficientnet_b7_nonstandard_input_size")
    if profile.model.architecture.startswith("efficientnet") and profile.resources.gpu_count < 1:
        blockers.append("efficientnet_gpu_required")
    if profile.resources.max_parallel_trials > profile.model.max_trials:
        warnings.append("parallel_trials_exceed_requested_trials")
    if profile.experiment.ab_test_enabled:
        if not profile.experiment.control_candidate_id or not profile.experiment.challenger_candidate_id:
            blockers.append("ab_test_candidates_required")
        if profile.experiment.control_candidate_id == profile.experiment.challenger_candidate_id:
            blockers.append("ab_test_candidates_must_differ")
    if profile.gates.target_environment == "production" and profile.gates.approval_policy != "two_person":
        blockers.append("production_requires_two_person_approval")

    capabilities = capability_matrix(profile)
    required_capability_blockers = [
        f"capability_not_wired:{item.capability_id}"
        for item in capabilities
        if item.active and item.status != "wired"
    ]
    blockers.extend(required_capability_blockers)
    blockers = unique(blockers)
    warnings = unique(warnings)
    valid = not any(not item.startswith("capability_not_wired:") for item in blockers)
    executable = valid and not blockers
    return PipelineProfileValidation(
        status="ready" if executable else "blocked",
        valid=valid,
        executable=executable,
        checked_at=utc_now(),
        blockers=blockers,
        warnings=warnings,
        capabilities=capabilities,
        stages=build_plan(profile, blockers),
    )


def capability_matrix(profile: PipelineRunProfile) -> list[PipelineCapability]:
    return [
        capability(
            "versioned_profile",
            "Versioned run profile",
            "wired",
            True,
            "Typed profile, immutable digest, and F-drive version history are implemented.",
        ),
        capability(
            "data_runtime_config",
            "Data pipeline parameters",
            "wired",
            True,
            "Split, quality, curation, and validation values render into the Airflow runtime config.",
        ),
        capability(
            "manual_hyperparameters",
            "Manual model hyperparameters",
            "wired",
            True,
            "The selected EfficientNet candidate renders into a pinned model runtime config.",
        ),
        capability(
            "validation_isolation",
            "Immutable CT holdout",
            "wired" if profile.gates.isolated_ct_dataset_required else "partial",
            profile.gates.isolated_ct_dataset_required,
            "Split digest, holdout split, and no-training-overlap are validated fail closed.",
        ),
        capability(
            "cross_validation_executor",
            "Cross-validation executor",
            "not_wired",
            profile.data.split.cross_validation_enabled,
            "Fold policy is modeled, but no fold fan-out/aggregate runtime exists yet.",
        ),
        capability(
            "automated_tuning_executor",
            "Automated search executor",
            "not_wired",
            profile.model.tuning_mode != "manual",
            "Manual values are wired; grid/Bayesian trial orchestration is not implemented.",
        ),
        capability(
            "ab_traffic_router",
            "A/B traffic router",
            "not_wired",
            profile.experiment.ab_test_enabled,
            "Candidate metadata is modeled, but no KServe/Ingress traffic split mutation exists.",
        ),
        capability(
            "full_lifecycle_orchestrator",
            "One-click full lifecycle",
            "wired",
            profile.execution_scope == "full_lifecycle",
            "LifecycleRun coordinates Airflow data, Kubernetes GPU training, MLflow evidence, readiness, CI/CT, approval, deployment, serving validation, monitoring, retry, and exact rollback.",
        ),
        capability(
            "airflow_data_dispatch",
            "Airflow data-cycle launch",
            "wired",
            profile.execution_scope == "data_cycle",
            "A validated data profile can create a guarded Airflow task assignment.",
        ),
    ]


def capability(
    capability_id: str,
    label: str,
    status: CapabilityStatus,
    active: bool,
    detail: str,
) -> PipelineCapability:
    return PipelineCapability(
        capability_id=capability_id,
        label=label,
        status=status,
        active=active,
        detail=detail,
    )


def build_plan(profile: PipelineRunProfile, blockers: list[str]) -> list[PipelinePlanStage]:
    blocked = bool(blockers)
    cv_active = profile.data.split.cross_validation_enabled
    ab_active = profile.experiment.ab_test_enabled
    full = profile.execution_scope == "full_lifecycle"
    stages = [
        ("profile", "Validate and snapshot profile", "control-plane", "ready", 0.0, "Schema, policy, and capability preflight."),
        ("intake", "Data intake and contract", "airflow", "ready", 0.0, profile.data.dataset_version),
        ("quality", "Validation and quality gates", "airflow", "ready", 0.0, "Fail-closed validation and image quality policy."),
        ("split", "Split and holdout isolation", "airflow", "ready", 0.0, profile.data.split.holdout_split),
        (
            "cross_validation",
            "Cross-validation folds",
            "kubernetes",
            "blocked" if cv_active else "not_started",
            0.0,
            f"{profile.data.split.cross_validation_folds} folds" if cv_active else "Disabled",
        ),
        ("training", "Model training and MLflow", "kubernetes", "blocked" if full else "not_started", 0.0, profile.model.architecture),
        ("evaluation", "Evaluation and readiness", "mlflow", "blocked" if full else "not_started", 0.0, profile.experiment.primary_metric),
        ("ab_test", "A/B validation", "kubernetes", "blocked" if ab_active else "not_started", 0.0, "Enabled" if ab_active else "Disabled"),
        ("cdct", "CI / CT / CD gates", "control-plane", "blocked" if full else "not_started", 0.0, profile.gates.target_environment),
        ("release", "Approval and deployment", "kubernetes", "blocked" if full else "not_started", 0.0, profile.gates.target_namespace),
    ]
    if not blocked:
        stages[0] = ("profile", "Validate and snapshot profile", "control-plane", "ready", 1.0, "Dry-run validation passed.")
    return [
        PipelinePlanStage(
            stage_id=stage_id,
            label=label,
            runtime=runtime,  # type: ignore[arg-type]
            state=state,  # type: ignore[arg-type]
            progress=progress,
            detail=detail,
        )
        for stage_id, label, runtime, state, progress, detail in stages
    ]


def save_profile(profile: PipelineRunProfile) -> PipelineProfileRecord:
    validation = validate_profile(profile)
    if not validation.valid:
        raise ValueError(
            "pipeline profile validation failed: " + ", ".join(validation.blockers)
        )
    digest = profile_digest(profile)
    profile_id = slug(profile.profile_name)
    with _PROFILE_LOCK:
        existing = next(
            (item for item in read_profiles().profiles if item.profile_id == profile_id and item.digest == digest),
            None,
        )
        if existing:
            return existing
        versions = [item.version for item in read_profiles().profiles if item.profile_id == profile_id]
        version = max(versions, default=0) + 1
        directory = profile_root() / profile_id / f"v{version}"
        directory.mkdir(parents=True, exist_ok=False)
        profile_path = directory / "profile.json"
        validation_path = directory / "validation.json"
        airflow_path = directory / "airflow.runtime.json"
        model_path = directory / "model.runtime.json"
        airflow_runtime_uri = f"{runtime_root()}/{profile_id}/v{version}/airflow.runtime.json"
        model_runtime_uri = f"{runtime_root()}/{profile_id}/v{version}/model.runtime.json"
        write_json(profile_path, profile.model_dump(mode="json"))
        write_json(validation_path, validation.model_dump(mode="json"))
        write_json(airflow_path, render_airflow_config(profile))
        write_json(
            model_path,
            render_model_config(profile, profile_id, version, airflow_runtime_uri),
        )
        created_at = utc_now()
        record = PipelineProfileRecord(
            profile_id=profile_id,
            version=version,
            digest=digest,
            created_at=created_at,
            profile=profile,
            validation=validation,
            profile_uri=str(profile_path),
            airflow_config_uri=str(airflow_path),
            airflow_runtime_uri=airflow_runtime_uri,
            model_config_uri=str(model_path),
            model_runtime_uri=model_runtime_uri,
        )
        write_json(directory / "manifest.json", record.model_dump(mode="json"))
        return record


def read_profiles() -> PipelineProfileList:
    root = profile_root()
    if not root.exists():
        return PipelineProfileList()
    records: list[PipelineProfileRecord] = []
    for path in root.glob("*/v*/manifest.json"):
        try:
            records.append(PipelineProfileRecord.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    records.sort(key=lambda item: (item.created_at, item.version), reverse=True)
    return PipelineProfileList(profiles=records)


def get_profile(profile_id: str, version: int | None = None) -> PipelineProfileRecord | None:
    matches = [item for item in read_profiles().profiles if item.profile_id == profile_id]
    if version is not None:
        return next((item for item in matches if item.version == version), None)
    return max(matches, key=lambda item: item.version, default=None)


def launch_profile(
    record: PipelineProfileRecord,
    request: PipelineProfileLaunchRequest,
) -> PipelineProfileLaunch:
    validation = validate_profile(record.profile)
    if record.profile.execution_scope != "data_cycle" or not validation.executable:
        return PipelineProfileLaunch(
            profile_id=record.profile_id,
            version=record.version,
            validation=validation,
        )
    task_request = TaskAssignmentRequest(
        task_type="airflow_dag_run",
        owner=request.actor,
        priority="normal",
        resource_profile="local-pipeline-workers",
        requester_team="mvi-platform",
        approval_policy="manual",
        dry_run=request.dry_run,
        config_payload={
            "dag_id": "enterprise_vision_mlops_daily",
            "pipeline_profile_id": record.profile_id,
            "pipeline_profile_version": record.version,
            "profile_digest": record.digest,
            "pipeline_config_uri": record.airflow_runtime_uri,
            "reason": request.reason,
        },
    )
    return PipelineProfileLaunch(
        profile_id=record.profile_id,
        version=record.version,
        validation=validation,
        task=create_task_assignment(task_request),
    )


def render_airflow_config(profile: PipelineRunProfile) -> dict[str, object]:
    config = load_toml(profile.base_airflow_config)
    config.setdefault("project", {})["environment"] = f"profile-{slug(profile.profile_name)}"
    pipelines = config.setdefault("pipelines", {})
    pipelines.setdefault("dataset_intake_audit", {}).update(
        dataset_version=profile.data.dataset_version,
        max_quality_samples=profile.data.max_review_samples,
        fail_on_empty=profile.data.fail_on_empty,
    )
    pipelines.setdefault("data_validation", {}).update(
        dataset_name=profile.data.dataset_name,
        input_manifest=runtime_data_uri(profile.data.source_manifest_uri),
        fail_on_empty=profile.data.fail_on_empty,
    )
    pipelines.setdefault("image_quality", {}).update(
        dataset_name=profile.data.dataset_name,
        dataset_version=profile.data.dataset_version,
        input_manifest=runtime_data_uri(profile.data.source_manifest_uri),
        fail_on_error=profile.data.fail_on_quality_error,
        fail_on_empty=profile.data.fail_on_empty,
        duplicate_hash_severity=profile.data.duplicate_severity,
        dimension_mismatch_severity=profile.data.dimension_severity,
    )
    pipelines.setdefault("dataset_shards", {}).update(
        input_manifest=runtime_data_uri(profile.data.source_manifest_uri),
        records_per_shard=profile.data.records_per_shard,
        split_seed=profile.data.split.seed,
        split_ratios={
            "train": profile.data.split.train,
            "validation": profile.data.split.validation,
            "test": profile.data.split.test,
        },
    )
    pipelines.setdefault("curation_workflow", {}).update(
        input_manifest=runtime_data_uri(profile.data.source_manifest_uri),
        sample_seed=profile.data.split.seed,
        max_review_samples=profile.data.max_review_samples,
        eval_splits=["validation", "test"],
        fail_on_empty=profile.data.fail_on_empty,
    )
    pipelines.setdefault("training", {}).update(
        input_manifest=runtime_data_uri(profile.data.source_manifest_uri),
        min_accuracy=profile.gates.promotion_min_accuracy,
        min_f1=profile.gates.promotion_min_f1,
        min_auroc=profile.gates.promotion_min_auroc,
    )
    config["control_plane"] = profile.model_dump(mode="json")
    return config


def render_model_config(
    profile: PipelineRunProfile,
    profile_id: str,
    version: int,
    airflow_runtime_uri: str,
) -> dict[str, object]:
    config = load_toml(profile.base_model_config)
    candidate_id = f"{profile.model.architecture}-profile-{profile_id}-v{version}"
    host_data_root = os.getenv(
        "EVM_HOST_DATA_ROOT",
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
    ).replace("\\", "/").rstrip("/")
    config.setdefault("model_matrix", {}).update(
        matrix_id=f"profile-{profile_id}-v{version}",
        dataset_version=profile.data.dataset_version,
        selected_candidate_id=candidate_id,
        dataset_policy="real_versioned_profile",
        mock_allowed=False,
        smoke_allowed=False,
        requires_real_dataset=True,
        requires_real_training=True,
        rollback_registry_path=(
            f"{host_data_root}/artifacts/registry/"
            f"{profile.model.architecture}/rollback.json"
        ),
    )
    config.setdefault("resources", {}).update(
        primary_gpu=profile.resources.compute_target,
        max_b0_parallel_jobs=(profile.resources.max_parallel_trials if profile.model.architecture == "efficientnet-b0" else 0),
        max_b7_parallel_jobs=(profile.resources.max_parallel_trials if profile.model.architecture == "efficientnet-b7" else 0),
        allow_cpu_fallback=profile.resources.gpu_count == 0,
    )
    config.setdefault("inputs", {}).update(
        base_config=airflow_runtime_uri,
        shard_index=runtime_data_uri(profile.data.split_manifest_uri),
        shard_identity_sha256=profile.data.split_manifest_sha256,
        mlflow_experiment_name=profile.experiment.mlflow_experiment_name,
    )
    config["execution"] = {
        "num_workers": max(1, min(profile.resources.cpu_request, 16)),
        "pin_memory": profile.resources.gpu_count > 0,
    }
    config["candidates"] = [
        {
            "candidate_id": candidate_id,
            "architecture": profile.model.architecture,
            "backbone": f"torchvision.models.{profile.model.architecture.replace('-', '_')}",
            "input_size": profile.model.input_size,
            "pretrained": profile.model.pretrained,
            "freeze_backbone": profile.model.freeze_backbone,
            "optimizer": profile.model.optimizer,
            "learning_rate": profile.model.learning_rate,
            "weight_decay": profile.model.weight_decay,
            "batch_size": profile.model.batch_size,
            "mixed_precision": profile.model.mixed_precision,
            "class_weighted_loss": profile.model.class_weighted_loss,
            "resource_profile": profile.resources.compute_target,
            "max_parallel_jobs": profile.resources.max_parallel_trials,
            "epochs": profile.model.epochs,
            "early_stop_accuracy": profile.model.early_stop_threshold,
            "early_stop_min_epochs": profile.model.early_stop_min_epochs,
            "early_stop_patience": profile.model.early_stop_patience,
        }
    ]
    config.setdefault("acceptance", {}).update(
        promotion_min_accuracy=profile.gates.promotion_min_accuracy,
        promotion_min_f1=profile.gates.promotion_min_f1,
        promotion_min_auroc=profile.gates.promotion_min_auroc,
        seed=profile.data.split.seed,
        early_stop_accuracy=profile.model.early_stop_threshold,
        early_stop_min_epochs=profile.model.early_stop_min_epochs,
        require_mlflow_run=True,
        require_split_manifest=True,
        require_training_history=True,
        require_confusion_matrix=True,
        require_gpu_profile=profile.resources.gpu_count > 0,
    )
    config["control_plane"] = profile.model_dump(mode="json")
    return config


def load_toml(value: str) -> dict[str, object]:
    path = allowed_base_config(value)
    if path is None:
        raise ValueError(f"base config is outside the project config allowlist: {value}")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def allowed_base_config(value: str) -> Path | None:
    config_root = (project_root() / "configs").resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = project_root() / candidate
    candidate = candidate.resolve()
    if candidate.suffix.lower() != ".toml":
        return None
    if not candidate.is_relative_to(config_root) or not candidate.is_file():
        return None
    return candidate


def resolve_data_path(value: str) -> Path | None:
    normalized = value.replace("\\", "/")
    host_root = os.getenv(
        "EVM_HOST_DATA_ROOT",
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
    ).replace("\\", "/").rstrip("/")
    mount_root = os.getenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data").replace("\\", "/").rstrip("/")
    candidates = [Path(value)]
    if normalized.lower().startswith(host_root.lower()):
        candidates.append(Path(f"{mount_root}{normalized[len(host_root):]}"))
    if normalized.lower().startswith(mount_root.lower()):
        candidates.append(Path(f"{host_root}{normalized[len(mount_root):]}"))
    return next((candidate for candidate in candidates if candidate.exists()), None)


def runtime_data_uri(value: str) -> str:
    normalized = value.replace("\\", "/")
    host_root = os.getenv(
        "EVM_HOST_DATA_ROOT",
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
    ).replace("\\", "/").rstrip("/")
    mount_root = os.getenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data").replace("\\", "/").rstrip("/")
    if normalized.lower().startswith(host_root.lower()):
        return f"{mount_root}{normalized[len(host_root):]}"
    return normalized


def profile_digest(profile: PipelineRunProfile) -> str:
    payload = json.dumps(
        profile.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned[:80] or "pipeline-profile"


def write_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
