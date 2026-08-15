from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
TelemetryObservationStatus = Literal["live", "stale", "unavailable"]
DiagnosticStatus = Literal["pass", "warn", "blocked", "fail"]
SourceSyncStatus = Literal["live", "stale", "error", "unavailable"]
DriftReviewStatus = Literal["open", "acknowledged", "approved", "closed"]
DecisionState = Literal["draft", "review", "approved", "rejected"]
DecisionSubjectType = Literal[
    "experiment",
    "prompt_change",
    "model_candidate",
    "evaluation_policy",
    "drift_review",
    "serving_change",
]

TaskStatus = Literal[
    "draft",
    "dry_run",
    "queued",
    "pending_confirmation",
    "running",
    "done",
    "failed",
    "cancelled",
    "blocked",
]
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


class AcceleratorTelemetry(ContractModel):
    index: int = Field(ge=0)
    vendor: Literal["nvidia"] = "nvidia"
    name: str
    uuid: str | None = None
    utilization_percent: float | None = Field(default=None, ge=0, le=100)
    engine_utilization_percent: float | None = Field(default=None, ge=0, le=100)
    engine_utilization_source: Literal["windows_pdh"] | None = None
    busiest_engine: str | None = None
    memory_used_mib: float | None = Field(default=None, ge=0)
    memory_total_mib: float | None = Field(default=None, ge=0)
    temperature_c: float | None = None
    power_draw_w: float | None = Field(default=None, ge=0)
    power_limit_w: float | None = Field(default=None, ge=0)


class ComputeTelemetry(ContractModel):
    schema_version: Literal["evm.compute_telemetry.v1"] = "evm.compute_telemetry.v1"
    source: Literal["host_probe"] = "host_probe"
    status: TelemetryObservationStatus
    observed_at: str
    cpu_utilization_percent: float | None = Field(default=None, ge=0, le=100)
    memory_utilization_percent: float | None = Field(default=None, ge=0, le=100)
    memory_used_bytes: int | None = Field(default=None, ge=0)
    memory_total_bytes: int | None = Field(default=None, ge=0)
    accelerators: list[AcceleratorTelemetry] = Field(default_factory=list)
    message: str | None = None


class RuntimeResourceList(ContractModel):
    resources: list[RuntimeResource]
    observation_status: ResourceObservationStatus = "unavailable"
    observed_at: str | None = None
    snapshot_age_seconds: float | None = Field(default=None, ge=0)
    cluster_context: str | None = None
    snapshot_uri: str | None = None
    observation_message: str | None = None
    compute_telemetry: ComputeTelemetry | None = None


class KubernetesResourceSnapshot(ContractModel):
    schema_version: Literal["evm.w7.kubernetes_resource_snapshot.v1"]
    cluster_context: str
    observed_at: str
    collection_status: Literal["pass", "fail"]
    resource_status: State
    observer_id: str | None = None
    pid: int | None = None
    process_started_at: str | None = None
    process_instance_id: str | None = None
    source_commit: str | None = None
    source_branch: str | None = None
    supervisor_lease_id: str | None = None
    fencing_token: int | None = None
    message: str | None = None
    resources: list[RuntimeResource] = Field(default_factory=list)
    compute_telemetry: ComputeTelemetry | None = None


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


class OrchestratorConnection(ContractModel):
    orchestrator: Literal["airflow", "mlflow", "kubernetes", "remote-worker"]
    mode: str
    control_mode: str
    status: State
    base_url: str | None = None
    namespace: str | None = None
    config_ref: ResourceRef | None = None
    supported_actions: list[str] = Field(default_factory=list)
    notes: str | None = None
    checked_at: str
    blockers: list[str] = Field(default_factory=list)


class OrchestratorConnectionList(ContractModel):
    orchestrators: list[OrchestratorConnection] = Field(default_factory=list)
    checked_at: str
    status: State


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
    execution_mode: Literal["parallel", "sequential", "single-gpu", "blocked"]
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


class SourceFreshness(ContractModel):
    source_id: str
    status: SourceSyncStatus
    observed_at: str | None = None
    age_seconds: float | None = Field(default=None, ge=0)
    poll_interval_seconds: float | None = Field(default=None, ge=0)
    message: str | None = None


class StatusDiagnostic(ContractModel):
    diagnostic_id: str
    status: Literal["warn", "blocked", "fail"]
    scope: Literal[
        "cycle",
        "stage",
        "metric",
        "readiness",
        "promotion",
        "drift",
        "cdct",
        "resource",
        "sync",
    ]
    component: str
    code: str
    summary: str
    remediation: str
    source: str
    evidence_uri: str | None = None
    observed_at: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ControlPanelDiagnostics(ContractModel):
    schema_version: Literal["evm.control_panel.diagnostics.v1"]
    generated_at: str
    cycle_id: str
    status: DiagnosticStatus
    blocked_count: int = Field(default=0, ge=0)
    warn_count: int = Field(default=0, ge=0)
    fail_count: int = Field(default=0, ge=0)
    sources: list[SourceFreshness] = Field(default_factory=list)
    diagnostics: list[StatusDiagnostic] = Field(default_factory=list)
    state_digest: str
    snapshot_uri: str | None = None
    audit_uri: str | None = None


class DriftReviewTransition(ContractModel):
    from_status: DriftReviewStatus
    to_status: DriftReviewStatus
    actor: str
    reason: str
    timestamp: str


class DriftReviewWorkflow(ContractModel):
    schema_version: Literal["evm.drift_review.workflow.v1"]
    event_id: str
    event_type: str
    status: DriftReviewStatus
    candidate_id: str
    dataset_version: str
    triggered_rules: list[str] = Field(default_factory=list)
    review_queue_count: int = Field(default=0, ge=0)
    evidence_uri: str | None = None
    label_review_queue_uri: str | None = None
    approval_required: bool = True
    automatic_retraining: bool = False
    automatic_deployment: bool = False
    automatic_promotion: bool = False
    next_actions: list[DriftReviewStatus] = Field(default_factory=list)
    transitions: list[DriftReviewTransition] = Field(default_factory=list)
    updated_at: str | None = None
    dry_run: bool = False
    projected_status: DriftReviewStatus | None = None
    audit_uri: str | None = None


class DriftReviewTransitionRequest(ContractModel):
    target_status: Literal["acknowledged", "approved", "closed"]
    actor: str = Field(min_length=2)
    reason: str = Field(min_length=8)
    expected_status: DriftReviewStatus
    dry_run: bool = True


class DecisionTransition(ContractModel):
    from_state: DecisionState
    to_state: DecisionState
    actor: str
    reason: str
    timestamp: str


class DecisionRecordRequest(ContractModel):
    subject_type: DecisionSubjectType
    title: str = Field(min_length=4)
    summary: str = Field(min_length=8)
    owner: str = Field(min_length=2)
    evidence_uris: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionTransitionRequest(ContractModel):
    target_state: DecisionState
    actor: str = Field(min_length=2)
    reason: str = Field(min_length=8)
    expected_version: int = Field(ge=1)


class DecisionRecord(DecisionRecordRequest):
    decision_id: str
    state: DecisionState
    version: int = Field(ge=1)
    created_at: str
    updated_at: str
    transitions: list[DecisionTransition] = Field(default_factory=list)


class DecisionRecordList(ContractModel):
    decisions: list[DecisionRecord] = Field(default_factory=list)
    status: State = "pass"
    blockers: list[str] = Field(default_factory=list)


class CTDatasetSnapshot(ContractModel):
    schema_version: Literal["evm.ct_dataset_snapshot.v1"] = "evm.ct_dataset_snapshot.v1"
    snapshot_id: str
    lifecycle_run_id: str
    profile_id: str
    profile_version: int = Field(ge=1)
    profile_digest: str
    dataset_version: str
    split: Literal["validation", "test"]
    record_count: int = Field(ge=1)
    byte_count: int = Field(ge=1)
    records_sha256: str
    source_records_sha256: str | None = None
    source_index_uri: str
    source_index_sha256: str
    source_identity_sha256: str
    manifest_uri: str
    manifest_sha256: str
    snapshot_uri: str
    snapshot_digest: str
    isolation_root: str
    immutable: bool
    training_mount_isolated: bool
    status: State
    blockers: list[str] = Field(default_factory=list)
    created_at: str


class CTSnapshotCreateRequest(ContractModel):
    source_shard_index_uri: str
    lifecycle_run_id: str
    profile_id: str
    profile_version: int = Field(ge=1)
    profile_digest: str
    split: Literal["validation", "test"] = "test"


class CTEvaluation(ContractModel):
    schema_version: Literal["evm.ct_evaluation.v1"] = "evm.ct_evaluation.v1"
    evaluation_id: str
    lifecycle_run_id: str
    snapshot_id: str
    candidate_id: str
    dataset_version: str
    status: State
    decision: Literal["pass", "block"]
    evaluated_at: str
    snapshot_digest: str
    expected_manifest_sha256: str
    observed_manifest_sha256: str
    expected_records_sha256: str
    observed_records_sha256: str
    ct_record_count: int = Field(ge=0)
    training_record_count: int = Field(ge=0)
    overlap_count: int = Field(ge=0)
    mutated: bool
    training_mount_isolated: bool
    model_artifact_uri: str | None = None
    model_sha256: str | None = None
    device: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    metric_thresholds: dict[str, float] = Field(default_factory=dict)
    checks: dict[str, State] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    snapshot_uri: str
    fold_manifest_uri: str | None = None
    training_job_manifest_uri: str | None = None
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
    gate_report_uri: str | None = None
    promotion_decision: Literal["allow", "block", "manual_review"] = "manual_review"
    block_reason: str | None = None
    verification_summary: dict[str, State] = Field(default_factory=dict)
    ct_snapshot_id: str | None = None
    ct_snapshot_digest: str | None = None
    ct_evaluation_id: str | None = None
    ct_evidence_uri: str | None = None


class TaskAssignmentRequest(ContractModel):
    cycle_id: str | None = None
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
    idempotency_key: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    @field_validator("config_payload")
    @classmethod
    def bounded_config_payload(cls, value):
        if len(value) > 64:
            raise ValueError("config_payload cannot contain more than 64 fields")
        for key, item in value.items():
            if len(key) > 128:
                raise ValueError("config_payload keys cannot exceed 128 characters")
            if isinstance(item, str) and len(item) > 8192:
                raise ValueError("config_payload string values cannot exceed 8192 characters")
            if isinstance(item, list):
                if len(item) > 128:
                    raise ValueError("config_payload lists cannot contain more than 128 items")
                if any(len(entry) > 2048 for entry in item):
                    raise ValueError("config_payload list strings cannot exceed 2048 characters")
            if isinstance(item, dict):
                if len(item) > 64:
                    raise ValueError("nested config_payload cannot contain more than 64 fields")
                if any(len(str(nested_key)) > 128 for nested_key in item):
                    raise ValueError(
                        "nested config_payload keys cannot exceed 128 characters"
                    )
                if any(
                    isinstance(nested_value, str) and len(nested_value) > 8192
                    for nested_value in item.values()
                ):
                    raise ValueError(
                        "nested config_payload strings cannot exceed 8192 characters"
                    )
        return value


class TaskAssignment(TaskAssignmentRequest):
    task_id: str
    version: int = Field(default=1, ge=1)
    status: TaskStatus
    created_at: str
    queued_at: str | None = None
    dispatched_at: str | None = None
    finished_at: str | None = None
    runtime_system: str | None = None
    runtime_id: str | None = None
    runtime_url: str | None = None
    runtime_state: str | None = None
    runtime_evidence_uri: str | None = None
    failure_reason: str | None = None
    audit: list[AuditEvent] = Field(default_factory=list)


class TaskAssignmentList(ContractModel):
    tasks: list[TaskAssignment]


class TaskTransitionRequest(ContractModel):
    actor: str
    reason: str
    expected_version: int | None = Field(default=None, ge=1)
    idempotency_key: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


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
    cycle_id: str | None = None
    model_selection_id: str | None = None
    target_environment: EnvironmentTier
    target_namespace: str
    target: ResourceRef
    actor: str
    reason: str
    dry_run: bool = True
    idempotency_key: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


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
    idempotency_key: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


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
    ct_snapshot: CTDatasetSnapshot | None = None
    ct_evaluation: CTEvaluation | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)


class CycleRunSummary(ContractModel):
    cycle_id: str
    status: State
    started_at: str
    finished_at: str | None = None
    dataset_id: str
    dataset_version: str
    model_name: str
    model_version: str
    model_stage: str
    environment: EnvironmentTier | None = None
    owner_issue: str
    stage_count: int = Field(ge=0)
    progress: float = Field(ge=0, le=1)
    source_uri: str | None = None
    live: bool = False


class CycleRunList(ContractModel):
    cycles: list[CycleRunSummary]
    latest_cycle_id: str
    selected_cycle_id: str | None = None
    total: int = Field(ge=0)
