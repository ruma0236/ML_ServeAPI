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

EnvironmentTier = Literal["dev", "test", "staging", "pre-production", "production"]
PromotionPolicyDecisionState = Literal["allow", "pending_approval", "blocked"]
CheckResult = Literal["pass", "fail"]
DeploymentIntentState = Literal[
    "dry_run",
    "pending_approval",
    "queued",
    "applying",
    "applied",
    "failed",
    "rolled_back",
]
ResourceObservationSource = Literal["cycle_projection", "kubernetes_snapshot"]
ResourceObservationStatus = Literal["live", "stale", "projected", "unavailable"]

TaskStatus = Literal["draft", "dry_run", "queued", "pending_confirmation", "blocked"]
CommandStatus = Literal["draft", "dry_run", "pending_confirmation", "applying", "applied", "cancelled", "failed", "rolled_back"]
TaskType = Literal["airflow_dag_run", "mlflow_run", "kubernetes_job"]
TaskPriority = Literal["low", "normal", "high", "urgent"]
CommandAction = Literal[
    "restart_deployment",
    "scale_deployment",
    "run_pipeline_job",
    "cancel_job",
    "trigger_airflow_dag",
    "pause_airflow_dag",
    "resume_airflow_dag",
    "run_cd_verification",
    "run_ct_evaluation",
    "trigger_drift_review",
    "approve_environment_promotion",
    "promote_model",
    "rollback_model",
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
    observation_source: ResourceObservationSource = "cycle_projection"
    observation_status: ResourceObservationStatus = "projected"
    observed_at: str | None = None
    observation_message: str | None = None
    reason: str | None = None
    desired_replicas: int | None = Field(default=None, ge=0)
    ready_replicas: int | None = Field(default=None, ge=0)
    gpu_capacity: str | None = None


class RuntimeResourceList(ContractModel):
    resources: list[RuntimeResource]
    observation_status: ResourceObservationStatus = "unavailable"
    observed_at: str | None = None
    snapshot_age_seconds: float | None = Field(default=None, ge=0)
    cluster_context: str | None = None
    snapshot_uri: str | None = None
    observation_message: str | None = None


class KubernetesResourceSnapshot(ContractModel):
    schema_version: Literal["evm.w7.kubernetes_resource_snapshot.v1"]
    cluster_context: str
    observed_at: str
    collection_status: Literal["pass", "fail"]
    resource_status: State
    message: str | None = None
    resources: list[RuntimeResource] = Field(default_factory=list)


class AuditEvent(ContractModel):
    timestamp: str
    actor: str
    event: str
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class OrgContext(ContractModel):
    team_id: str
    department: str
    service_scope: Literal["internal-team", "internal-department", "external-production"]
    product_area: str | None = None
    data_owner: str | None = None
    model_owner: str | None = None
    ops_owner: str | None = None
    ownership_status: State = "unknown"
    missing_owners: list[str] = Field(default_factory=list)


class EnvironmentRef(ContractModel):
    name: str
    tier: EnvironmentTier
    promotion_state: Literal["draft", "candidate", "approved", "blocked", "deployed", "rolled_back"]
    cluster: str | None = None
    namespace: str | None = None
    release_ref: str | None = None
    approval_policy: str | None = None
    promotion_blockers: list[str] = Field(default_factory=list)


class PromotionPolicyRequest(ContractModel):
    target_environment: EnvironmentTier
    target_namespace: str
    requester: str
    approver: str | None = None


class PromotionPolicyInput(PromotionPolicyRequest):
    org_context: OrgContext | None = None
    readiness_decision: Literal["ready", "blocked"] = "blocked"
    ci_status: State = "unknown"
    cd_status: State = "unknown"
    ct_status: State = "unknown"
    model_digest: str | None = None
    image_digest: str | None = None
    rollback_reference: str | None = None
    rollback_ready: bool = False
    candidate_id: str = ""
    dataset_version: str = ""
    release_ref: str | None = None


class PromotionPolicyCheck(ContractModel):
    check_id: str
    status: State
    required: bool = True
    reason_code: str | None = None
    evidence: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class PromotionPolicyDecision(ContractModel):
    schema_version: str = "evm.w7.promotion_policy.v1"
    decision_id: str
    policy_version: str
    decision: PromotionPolicyDecisionState
    status: State
    target_environment: EnvironmentTier
    target_namespace: str
    requester: str
    approver: str | None = None
    approval_policy: str
    evaluated_at: str
    input_digest: str
    required_checks: list[str] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    checks: list[PromotionPolicyCheck] = Field(default_factory=list)
    audit_uri: str | None = None


class CIEvidenceBundle(ContractModel):
    schema_version: str = "evm.w7.ci_evidence.v1"
    repository: str
    workflow_name: str
    workflow_run_id: str
    workflow_run_attempt: int = Field(default=1, ge=1)
    commit_sha: str
    ref: str
    event: str
    status: Literal["completed"]
    conclusion: Literal["success", "failure", "cancelled", "timed_out"]
    python_test_result: CheckResult
    frontend_test_result: CheckResult
    evidence_validator_result: CheckResult
    compose_config_result: CheckResult
    kustomize_render_result: CheckResult
    image_digest: str
    config_render_digest: str
    contract_digest: str
    source_uri: str
    generated_at: str
    bundle_digest: str


class CIEvidenceValidation(ContractModel):
    schema_version: str = "evm.w7.ci_evidence_validation.v1"
    validation_id: str
    valid: bool
    status: State
    workflow_run_id: str
    commit_sha: str
    checked_at: str
    input_digest: str
    checks: dict[str, State] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    source_uri: str | None = None
    report_uri: str | None = None


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


class ReadinessEvidenceCheck(ContractModel):
    check_id: str
    category: Literal["data", "model", "runtime"]
    status: State
    required: bool = True
    evidence_uri: str | None = None
    evidence_digest: str | None = None
    observed: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)


class ArtifactReadinessEvaluation(ContractModel):
    schema_version: str = "evm.w7.artifact_readiness.v1"
    evaluation_id: str
    decision: Literal["ready", "blocked"]
    status: State
    data_status: State
    model_status: State
    runtime_status: State
    candidate_id: str
    dataset_version: str
    evaluated_at: str
    input_digest: str
    checks: list[ReadinessEvidenceCheck]
    blockers: list[str] = Field(default_factory=list)
    report_uri: str | None = None


class DataPipelineReadiness(ContractModel):
    contract_status: State
    quality_status: State
    lineage_status: State
    replay_ready: bool
    source_policy_uri: str | None = None
    quality_report_uri: str | None = None
    lineage_uri: str | None = None
    backfill_window: str | None = None
    owner_approval_required: bool = True
    owner_approval_status: State = "unknown"
    owner_approval_actor: str | None = None
    blockers: list[str] = Field(default_factory=list)


class ExperimentPipelineReadiness(ContractModel):
    tracking_status: State
    evaluation_status: State
    registry_status: State
    promotion_ready: bool
    experiment_uri: str | None = None
    model_card_uri: str | None = None
    evaluation_report_uri: str | None = None
    rollback_ready: bool = False
    owner_approval_required: bool = True
    owner_approval_status: State = "unknown"
    owner_approval_actor: str | None = None
    blockers: list[str] = Field(default_factory=list)


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
    review_queue_count: int = Field(default=0, ge=0)
    severity: Literal["none", "low", "medium", "high", "critical"] = "none"
    recommended_action: str = ""
    retraining_candidate_required: bool = False
    measurement_status: Literal["measured", "legacy_queue", "unavailable"] = "unavailable"
    review_event_id: str | None = None
    review_event_type: Literal["review_required", "within_policy"] | None = None
    review_event_status: Literal["open", "acknowledged", "approved", "closed"] | None = None
    model_candidate_id: str | None = None
    reference_window_id: str | None = None
    current_window_id: str | None = None
    reference_record_count: int = Field(default=0, ge=0)
    current_record_count: int = Field(default=0, ge=0)
    input_category_js: float | None = None
    predicted_class_js: float | None = None
    confidence_psi: float | None = None
    mean_confidence_drop: float | None = None
    low_confidence_rate_increase: float | None = None
    reference_low_confidence_rate: float | None = None
    current_low_confidence_rate: float | None = None
    reference_confidence_quantiles: dict[str, float] = Field(default_factory=dict)
    current_confidence_quantiles: dict[str, float] = Field(default_factory=dict)
    thresholds: dict[str, float] = Field(default_factory=dict)
    triggered_rules: list[str] = Field(default_factory=list)
    label_review_queue_uri: str | None = None
    approval_required: bool = False
    automatic_retraining: bool = False


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
    gate_report_uri: str | None = None
    promotion_decision: Literal["allow", "block", "manual_review"] = "manual_review"
    block_reason: str | None = None
    verification_summary: dict[str, State] = Field(default_factory=dict)


class TaskAssignmentRequest(ContractModel):
    task_type: TaskType
    owner: str
    priority: TaskPriority
    resource_profile: str
    config_payload: dict[str, str | int | float | bool | None | list[str] | dict[str, str | int | float | bool | None]]
    requester_team: str | None = None
    environment: EnvironmentRef | None = None
    approval_policy: str | None = None
    airflow: AirflowRef | None = None
    mlflow: MLflowRef | None = None
    cdct_gate: CDCTGate | None = None
    dry_run: bool = True


class TaskAssignment(TaskAssignmentRequest):
    task_id: str
    status: TaskStatus
    created_at: str
    queued_at: str | None = None
    audit: list[AuditEvent] = Field(default_factory=list)


class TaskAssignmentList(ContractModel):
    tasks: list[TaskAssignment]


class CommandIntentRequest(ContractModel):
    action: CommandAction
    target: ResourceRef
    actor: str
    dry_run: bool
    reason: str
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class CommandIntent(CommandIntentRequest):
    command_id: str
    status: CommandStatus
    created_at: str
    confirmed_at: str | None = None
    applied_at: str | None = None
    rollback_command_id: str | None = None
    promotion_policy: PromotionPolicyDecision | None = None
    audit: list[AuditEvent] = Field(default_factory=list)


class CommandIntentList(ContractModel):
    commands: list[CommandIntent]


class DeploymentIntentRequest(ContractModel):
    target_environment: EnvironmentTier
    target_namespace: str
    target: ResourceRef
    actor: str
    reason: str
    dry_run: bool = True


class DeploymentTransition(ContractModel):
    from_state: DeploymentIntentState | Literal["created"]
    to_state: DeploymentIntentState
    actor: str
    timestamp: str
    environment: EnvironmentTier
    namespace: str
    artifact_digest: str
    reason: str
    result: str


class DeploymentExecutionResult(ContractModel):
    action: Literal["apply", "rollback"]
    status: Literal["applied", "failed", "rolled_back"]
    started_at: str
    finished_at: str
    command: list[str] = Field(default_factory=list)
    exit_code: int
    stdout_uri: str | None = None
    stderr_uri: str | None = None


class DeploymentIntent(DeploymentIntentRequest):
    intent_id: str
    state: DeploymentIntentState
    version: int = Field(ge=1)
    created_at: str
    updated_at: str
    ci_evidence: CIEvidenceValidation
    ci_evidence_uri: str
    ci_bundle_digest: str
    readiness_evaluation_id: str
    promotion_policy: PromotionPolicyDecision
    model_candidate_id: str
    model_artifact_uri: str
    model_digest: str
    image_digest: str
    config_render_digest: str
    rollback_reference: str
    manifest_ref: str
    audit_uri: str | None = None
    approver: str | None = None
    approved_at: str | None = None
    transitions: list[DeploymentTransition] = Field(default_factory=list)
    execution_result: DeploymentExecutionResult | None = None


class DeploymentIntentList(ContractModel):
    intents: list[DeploymentIntent]
    status: State = "pass"
    blockers: list[str] = Field(default_factory=list)


class DeploymentTransitionRequest(ContractModel):
    actor: str
    reason: str
    expected_version: int = Field(ge=1)


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
    readiness_evaluation: ArtifactReadinessEvaluation | None = None
    promotion_policy: PromotionPolicyDecision | None = None
    ci_evidence: CIEvidenceValidation | None = None
    latest_deployment_intent: DeploymentIntent | None = None
    model_matrix: ModelExperimentMatrix | None = None
    metrics: list[Metric] = Field(default_factory=list)
    promotion_gate: PromotionGate | None = None
    drift: DriftState | None = None
    cdct_gate: CDCTGate | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
