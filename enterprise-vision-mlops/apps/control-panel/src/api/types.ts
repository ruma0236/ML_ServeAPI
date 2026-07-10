export type State =
  | "unknown"
  | "queued"
  | "running"
  | "pass"
  | "warn"
  | "fail"
  | "blocked"
  | "done"
  | "cancelled";

export type EnvironmentTier = "dev" | "test" | "staging" | "pre-production" | "production";
export type PromotionPolicyDecisionState = "allow" | "pending_approval" | "blocked";
export type DeploymentIntentState =
  | "dry_run"
  | "pending_approval"
  | "queued"
  | "applying"
  | "applied"
  | "failed"
  | "rolled_back";

export interface Metric {
  name: string;
  value: number;
  unit?: string | null;
  threshold?: number | null;
  status?: State | null;
}

export interface ArtifactRef {
  name: string;
  uri: string;
  artifact_type: string;
  mime_type?: string | null;
  preview_uri?: string | null;
}

export interface ResourceRef {
  namespace: string;
  kind: string;
  name: string;
  uid?: string | null;
}

export interface RuntimeResource {
  resource_id: string;
  namespace: string;
  kind: string;
  name: string;
  status: State;
  node_pool: string;
  readiness?: string | null;
  restarts: number;
  cpu_request?: string | null;
  memory_request?: string | null;
  gpu_request?: string | null;
  storage_claim?: string | null;
  storage_root?: string | null;
  last_transition_time?: string | null;
  owner_issue?: string | null;
  control_actions: string[];
  pressure?: State;
  related_stages?: string[];
  observation_source?: "cycle_projection" | "kubernetes_snapshot";
  observation_status?: "live" | "stale" | "projected" | "unavailable";
  observed_at?: string | null;
  observation_message?: string | null;
  reason?: string | null;
  desired_replicas?: number | null;
  ready_replicas?: number | null;
  gpu_capacity?: string | null;
}

export interface RuntimeResourceList {
  resources: RuntimeResource[];
  observation_status: "live" | "stale" | "projected" | "unavailable";
  observed_at?: string | null;
  snapshot_age_seconds?: number | null;
  cluster_context?: string | null;
  snapshot_uri?: string | null;
  observation_message?: string | null;
}

export type TaskStatus = "draft" | "dry_run" | "queued" | "pending_confirmation" | "blocked";
export type TaskType = "airflow_dag_run" | "mlflow_run" | "kubernetes_job";
export type TaskPriority = "low" | "normal" | "high" | "urgent";
export type CommandStatus =
  | "draft"
  | "dry_run"
  | "pending_confirmation"
  | "applying"
  | "applied"
  | "cancelled"
  | "failed"
  | "rolled_back";
export type CommandAction =
  | "restart_deployment"
  | "scale_deployment"
  | "run_pipeline_job"
  | "cancel_job"
  | "trigger_airflow_dag"
  | "pause_airflow_dag"
  | "resume_airflow_dag"
  | "run_cd_verification"
  | "run_ct_evaluation"
  | "trigger_drift_review"
  | "approve_environment_promotion"
  | "promote_model"
  | "rollback_model";

export interface AuditEvent {
  timestamp: string;
  actor: string;
  event: string;
  details: Record<string, string | number | boolean | null>;
}

export interface OrgContext {
  team_id: string;
  department: string;
  service_scope: string;
  product_area?: string | null;
  data_owner?: string | null;
  model_owner?: string | null;
  ops_owner?: string | null;
  ownership_status?: State;
  missing_owners?: string[];
}

export interface EnvironmentRef {
  name: string;
  tier: EnvironmentTier;
  promotion_state: string;
  cluster?: string | null;
  namespace?: string | null;
  release_ref?: string | null;
  approval_policy?: string | null;
  promotion_blockers?: string[];
}

export interface PromotionPolicyRequest {
  target_environment: EnvironmentTier;
  target_namespace: string;
  requester: string;
  approver?: string | null;
}

export interface PromotionPolicyCheck {
  check_id: string;
  status: State;
  required: boolean;
  reason_code?: string | null;
  evidence: Record<string, string | number | boolean | null>;
}

export interface PromotionPolicyDecision {
  schema_version: string;
  decision_id: string;
  policy_version: string;
  decision: PromotionPolicyDecisionState;
  status: State;
  target_environment: EnvironmentTier;
  target_namespace: string;
  requester: string;
  approver?: string | null;
  approval_policy: string;
  evaluated_at: string;
  input_digest: string;
  required_checks: string[];
  required_approvals: string[];
  reason_codes: string[];
  checks: PromotionPolicyCheck[];
  audit_uri?: string | null;
}

export interface CIEvidenceValidation {
  schema_version: string;
  validation_id: string;
  valid: boolean;
  status: State;
  workflow_run_id: string;
  commit_sha: string;
  checked_at: string;
  input_digest: string;
  checks: Record<string, State>;
  blockers: string[];
  source_uri?: string | null;
  report_uri?: string | null;
}

export interface DeploymentIntentRequest {
  target_environment: EnvironmentTier;
  target_namespace: string;
  target: ResourceRef;
  actor: string;
  reason: string;
  dry_run: boolean;
}

export interface DeploymentTransition {
  from_state: DeploymentIntentState | "created";
  to_state: DeploymentIntentState;
  actor: string;
  timestamp: string;
  environment: EnvironmentTier;
  namespace: string;
  artifact_digest: string;
  reason: string;
  result: string;
}

export interface DeploymentExecutionResult {
  action: "apply" | "rollback";
  status: "applied" | "failed" | "rolled_back";
  started_at: string;
  finished_at: string;
  command: string[];
  exit_code: number;
  stdout_uri?: string | null;
  stderr_uri?: string | null;
}

export interface DeploymentIntent extends DeploymentIntentRequest {
  intent_id: string;
  state: DeploymentIntentState;
  version: number;
  created_at: string;
  updated_at: string;
  ci_evidence: CIEvidenceValidation;
  ci_evidence_uri: string;
  ci_bundle_digest: string;
  readiness_evaluation_id: string;
  promotion_policy: PromotionPolicyDecision;
  model_candidate_id: string;
  model_artifact_uri: string;
  model_digest: string;
  image_digest: string;
  config_render_digest: string;
  rollback_reference: string;
  manifest_ref: string;
  approver?: string | null;
  approved_at?: string | null;
  transitions: DeploymentTransition[];
  execution_result?: DeploymentExecutionResult | null;
}

export interface DeploymentIntentList {
  intents: DeploymentIntent[];
  status: State;
  blockers: string[];
}

export interface DeploymentTransitionRequest {
  actor: string;
  reason: string;
  expected_version: number;
}

export interface AirflowRef {
  mode: string;
  control_mode: string;
  dag_id: string;
  dag_run_id: string;
  task_id?: string | null;
  connection_status: State;
  url?: string | null;
}

export interface MLflowRef {
  experiment_id: string;
  run_id: string;
  model_name: string;
  model_version: string;
  url?: string | null;
}

export interface DatasetVersion {
  dataset_id: string;
  version: string;
  record_count: number;
  storage_uri: string;
  quality_status: State;
  domain_pack?: string | null;
  split?: Record<string, number>;
  schema_valid_rate?: number | null;
}

export interface ModelVersion {
  model_name: string;
  version: string;
  stage: string;
  model_type: string;
  registry_uri: string;
  source_run_id?: string;
  dataset_version?: string;
}

export interface ReadinessEvidenceCheck {
  check_id: string;
  category: "data" | "model" | "runtime";
  status: State;
  required: boolean;
  evidence_uri?: string | null;
  evidence_digest?: string | null;
  observed: Record<string, string | number | boolean | null>;
  blockers: string[];
}

export interface ArtifactReadinessEvaluation {
  schema_version: string;
  evaluation_id: string;
  decision: "ready" | "blocked";
  status: State;
  data_status: State;
  model_status: State;
  runtime_status: State;
  candidate_id: string;
  dataset_version: string;
  evaluated_at: string;
  input_digest: string;
  checks: ReadinessEvidenceCheck[];
  blockers: string[];
  report_uri?: string | null;
}

export interface DataPipelineReadiness {
  contract_status: State;
  quality_status: State;
  lineage_status: State;
  replay_ready: boolean;
  source_policy_uri?: string | null;
  quality_report_uri?: string | null;
  lineage_uri?: string | null;
  backfill_window?: string | null;
  owner_approval_required?: boolean;
  owner_approval_status?: State;
  owner_approval_actor?: string | null;
  blockers?: string[];
}

export interface ExperimentPipelineReadiness {
  tracking_status: State;
  evaluation_status: State;
  registry_status: State;
  promotion_ready: boolean;
  experiment_uri?: string | null;
  model_card_uri?: string | null;
  evaluation_report_uri?: string | null;
  rollback_ready?: boolean;
  owner_approval_required?: boolean;
  owner_approval_status?: State;
  owner_approval_actor?: string | null;
  blockers?: string[];
}

export interface PromotionGate {
  decision: string;
  status: State;
  blockers: string[];
  thresholds?: Record<string, number>;
}

export interface DriftState {
  status: State;
  data_drift_status: State;
  prediction_drift_status: State;
  action: string;
  reference_dataset_version?: string | null;
  current_dataset_version?: string | null;
  drifting_columns?: string[];
  drift_score?: number | null;
  report_uri?: string | null;
  review_queue_count?: number;
  severity?: "none" | "low" | "medium" | "high" | "critical";
  recommended_action?: string;
  retraining_candidate_required?: boolean;
  measurement_status?: "measured" | "legacy_queue" | "unavailable";
  review_event_id?: string | null;
  review_event_type?: "review_required" | "within_policy" | null;
  review_event_status?: "open" | "acknowledged" | "approved" | "closed" | null;
  model_candidate_id?: string | null;
  reference_window_id?: string | null;
  current_window_id?: string | null;
  reference_record_count?: number;
  current_record_count?: number;
  input_category_js?: number | null;
  predicted_class_js?: number | null;
  confidence_psi?: number | null;
  mean_confidence_drop?: number | null;
  low_confidence_rate_increase?: number | null;
  reference_low_confidence_rate?: number | null;
  current_low_confidence_rate?: number | null;
  reference_confidence_quantiles?: Record<string, number>;
  current_confidence_quantiles?: Record<string, number>;
  thresholds?: Record<string, number>;
  triggered_rules?: string[];
  label_review_queue_uri?: string | null;
  approval_required?: boolean;
  automatic_retraining?: boolean;
}

export interface CDCTGate {
  status: State;
  ci_status: State;
  cd_status: State;
  ct_status: State;
  required_checks: string[];
  passed_checks: string[];
  failed_checks: string[];
  pipeline_run_uri?: string | null;
  ct_trigger?: string | null;
  approved_by?: string | null;
  promotion_blockers: string[];
  gate_report_uri?: string | null;
  promotion_decision?: "allow" | "block" | "manual_review";
  block_reason?: string | null;
  verification_summary?: Record<string, State>;
}

export interface TaskAssignmentRequest {
  task_type: TaskType;
  owner: string;
  priority: TaskPriority;
  resource_profile: string;
  requester_team?: string | null;
  environment?: EnvironmentRef | null;
  approval_policy?: string | null;
  airflow?: AirflowRef | null;
  mlflow?: MLflowRef | null;
  cdct_gate?: CDCTGate | null;
  config_payload: Record<string, string | number | boolean | null | string[] | Record<string, string | number | boolean | null>>;
  dry_run: boolean;
}

export interface TaskAssignment extends TaskAssignmentRequest {
  task_id: string;
  status: TaskStatus;
  created_at: string;
  queued_at?: string | null;
  audit: AuditEvent[];
}

export interface TaskAssignmentList {
  tasks: TaskAssignment[];
}

export interface CommandIntentRequest {
  action: CommandAction;
  target: ResourceRef;
  actor: string;
  dry_run: boolean;
  reason: string;
  parameters: Record<string, string | number | boolean | null>;
}

export interface CommandIntent extends CommandIntentRequest {
  command_id: string;
  status: CommandStatus;
  created_at: string;
  confirmed_at?: string | null;
  applied_at?: string | null;
  rollback_command_id?: string | null;
  promotion_policy?: PromotionPolicyDecision | null;
  audit: AuditEvent[];
}

export interface CommandIntentList {
  commands: CommandIntent[];
}

export interface ServingState {
  status: State;
  endpoint: string;
  model_loaded: boolean;
  model_version: string;
  placeholder?: boolean | null;
  p95_latency_ms?: number | null;
  healthy_targets?: number | null;
}

export interface RealTestPolicy {
  mock_allowed: boolean;
  smoke_allowed: boolean;
  requires_real_dataset: boolean;
  requires_real_training: boolean;
  minimum_records?: number | null;
  dataset_version?: string | null;
  notes?: string | null;
}

export interface ModelCandidate {
  candidate_id: string;
  framework: "torch";
  architecture: "efficientnet-b0" | "efficientnet-b7";
  backbone: string;
  status: State;
  dataset_version: string;
  resource_profile: string;
  conditions: Record<string, string | number | boolean | null>;
  metrics: Metric[];
  run_uri?: string | null;
  artifact_uri?: string | null;
  promotion_blockers: string[];
}

export interface ModelExperimentMatrix {
  matrix_id: string;
  status: State;
  execution_mode: string;
  real_test_policy: RealTestPolicy;
  candidates: ModelCandidate[];
  framework?: "torch" | null;
}

export interface PipelineStage {
  stage_id: string;
  name: string;
  status: State;
  started_at?: string | null;
  finished_at?: string | null;
  current_step?: string | null;
  progress: number;
  failure_reason?: string | null;
  artifacts: ArtifactRef[];
  sample_outputs: ArtifactRef[];
  metrics: Metric[];
  resources: ResourceRef[];
}

export interface CycleRun {
  cycle_id: string;
  status: State;
  started_at: string;
  finished_at?: string | null;
  owner_issue: string;
  tenant?: OrgContext | null;
  environment?: EnvironmentRef | null;
  airflow?: AirflowRef | null;
  mlflow?: MLflowRef | null;
  data_pipeline?: DataPipelineReadiness | null;
  experiment_pipeline?: ExperimentPipelineReadiness | null;
  readiness_evaluation?: ArtifactReadinessEvaluation | null;
  promotion_policy?: PromotionPolicyDecision | null;
  ci_evidence?: CIEvidenceValidation | null;
  latest_deployment_intent?: DeploymentIntent | null;
  dataset: DatasetVersion;
  model: ModelVersion;
  model_matrix?: ModelExperimentMatrix | null;
  metrics: Metric[];
  promotion_gate?: PromotionGate | null;
  drift?: DriftState | null;
  cdct_gate?: CDCTGate | null;
  serving: ServingState;
  stages: PipelineStage[];
  resources: ResourceRef[];
  artifacts: ArtifactRef[];
}
