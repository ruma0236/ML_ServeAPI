from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


State = Literal[
    "unknown",
    "queued",
    "running",
    "pass",
    "warn",
    "fail",
    "blocked",
    "done",
    "cancelled",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class Metric(ContractModel):
    name: str
    value: float
    unit: str | None = None
    threshold: float | None = None
    status: State | None = None


class ArtifactRef(ContractModel):
    name: str
    uri: str
    artifact_type: str
    mime_type: str | None = None
    preview_uri: str | None = None


class ResourceRef(ContractModel):
    namespace: str
    kind: str
    name: str
    uid: str | None = None


class RuntimeResource(ContractModel):
    resource_id: str
    namespace: str
    kind: str
    name: str
    status: State
    node_pool: str
    readiness: str | None = None
    restarts: int = Field(default=0, ge=0)
    cpu_request: str | None = None
    memory_request: str | None = None
    gpu_request: str | None = None
    storage_claim: str | None = None
    storage_root: str | None = None
    last_transition_time: str | None = None
    owner_issue: str | None = None
    control_actions: list[str] = Field(default_factory=list)
    pressure: State = "unknown"
    related_stages: list[str] = Field(default_factory=list)


class RuntimeResourceList(ContractModel):
    resources: list[RuntimeResource]


class OrgContext(ContractModel):
    team_id: str
    department: str
    service_scope: Literal["internal-team", "internal-department", "external-production"]
    product_area: str | None = None
    data_owner: str | None = None
    model_owner: str | None = None
    ops_owner: str | None = None


class EnvironmentRef(ContractModel):
    name: str
    tier: Literal["dev", "test", "staging", "pre-production", "production"]
    promotion_state: Literal["draft", "candidate", "approved", "blocked", "deployed", "rolled_back"]
    cluster: str | None = None
    namespace: str | None = None
    release_ref: str | None = None


class AirflowRef(ContractModel):
    mode: Literal["external-compose", "in-cluster", "managed-service"] = "external-compose"
    control_mode: Literal["read-only", "rest-api", "cli-bridge", "kubernetes-operator"] = "rest-api"
    dag_id: str = ""
    dag_run_id: str = ""
    task_id: str | None = None
    namespace: str | None = None
    deployment_name: str | None = None
    contract_config_map: str | None = None
    connection_status: State = "unknown"
    url: str | None = None


class MLflowRef(ContractModel):
    experiment_id: str = ""
    run_id: str = ""
    model_name: str = ""
    model_version: str = ""
    url: str | None = None


class DataPipelineReadiness(ContractModel):
    contract_status: State
    quality_status: State
    lineage_status: State
    replay_ready: bool
    source_policy_uri: str | None = None
    quality_report_uri: str | None = None
    lineage_uri: str | None = None
    backfill_window: str | None = None


class ExperimentPipelineReadiness(ContractModel):
    tracking_status: State
    evaluation_status: State
    registry_status: State
    promotion_ready: bool
    experiment_uri: str | None = None
    model_card_uri: str | None = None
    evaluation_report_uri: str | None = None


class DatasetVersion(ContractModel):
    dataset_id: str
    version: str
    record_count: int = Field(ge=0)
    storage_uri: str
    quality_status: State
    domain_pack: str | None = None
    split: dict[str, int] = Field(default_factory=dict)
    schema_valid_rate: float | None = Field(default=None, ge=0, le=1)


class ModelVersion(ContractModel):
    model_name: str
    version: str
    stage: str
    model_type: str
    registry_uri: str
    source_run_id: str = ""
    dataset_version: str = ""


class RealTestPolicy(ContractModel):
    mock_allowed: bool
    smoke_allowed: bool
    requires_real_dataset: bool
    requires_real_training: bool
    minimum_records: int | None = Field(default=None, ge=1)
    dataset_version: str | None = None
    notes: str | None = None


class ModelCandidate(ContractModel):
    candidate_id: str
    framework: Literal["torch"]
    architecture: Literal["efficientnet-b0", "efficientnet-b7"]
    backbone: str
    status: State
    dataset_version: str
    resource_profile: str
    conditions: dict[str, str | int | float | bool | None]
    metrics: list[Metric] = Field(default_factory=list)
    run_uri: str | None = None
    artifact_uri: str | None = None
    promotion_blockers: list[str] = Field(default_factory=list)


class ModelExperimentMatrix(ContractModel):
    matrix_id: str
    status: State
    execution_mode: Literal["parallel", "sequential", "blocked"]
    real_test_policy: RealTestPolicy
    candidates: list[ModelCandidate]
    framework: Literal["torch"] | None = "torch"


class PromotionGate(ContractModel):
    decision: str
    status: State
    blockers: list[str] = Field(default_factory=list)
    thresholds: dict[str, float] = Field(default_factory=dict)


class DriftState(ContractModel):
    status: State
    data_drift_status: State
    prediction_drift_status: State
    action: Literal["none", "label_review", "retrain_candidate", "block_promotion", "rollback_review"]
    reference_dataset_version: str | None = None
    current_dataset_version: str | None = None
    drifting_columns: list[str] = Field(default_factory=list)
    drift_score: float | None = None
    report_uri: str | None = None


class CDCTGate(ContractModel):
    status: State
    ci_status: State
    cd_status: State
    ct_status: State
    required_checks: list[str]
    passed_checks: list[str] = Field(default_factory=list)
    failed_checks: list[str] = Field(default_factory=list)
    pipeline_run_uri: str | None = None
    ct_trigger: Literal["new-data", "new-code", "drift", "schedule", "manual"] | None = None
    approved_by: str | None = None
    promotion_blockers: list[str] = Field(default_factory=list)


class ServingState(ContractModel):
    status: State
    endpoint: str
    model_loaded: bool
    model_version: str
    placeholder: bool | None = None
    p95_latency_ms: float | None = None
    healthy_targets: int | None = None


class PipelineStage(ContractModel):
    stage_id: str
    name: str
    status: State
    started_at: str | None
    artifacts: list[ArtifactRef]
    metrics: list[Metric]
    finished_at: str | None = None
    current_step: str | None = None
    progress: float = Field(default=0, ge=0, le=1)
    failure_reason: str | None = None
    sample_outputs: list[ArtifactRef] = Field(default_factory=list)
    resources: list[ResourceRef] = Field(default_factory=list)


class CycleRun(ContractModel):
    cycle_id: str
    status: State
    started_at: str
    stages: list[PipelineStage]
    dataset: DatasetVersion
    model: ModelVersion
    serving: ServingState
    resources: list[ResourceRef]
    finished_at: str | None = None
    owner_issue: str = "EVM-224"
    tenant: OrgContext | None = None
    environment: EnvironmentRef | None = None
    airflow: AirflowRef | None = None
    mlflow: MLflowRef | None = None
    data_pipeline: DataPipelineReadiness | None = None
    experiment_pipeline: ExperimentPipelineReadiness | None = None
    model_matrix: ModelExperimentMatrix | None = None
    metrics: list[Metric] = Field(default_factory=list)
    promotion_gate: PromotionGate | None = None
    drift: DriftState | None = None
    cdct_gate: CDCTGate | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
