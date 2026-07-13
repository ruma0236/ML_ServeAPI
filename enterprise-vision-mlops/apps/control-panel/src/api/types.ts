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

export interface OrchestratorConnection {
  orchestrator: "airflow" | "mlflow" | "kubernetes" | "remote-worker";
  mode: string;
  control_mode: string;
  status: State;
  base_url?: string | null;
  namespace?: string | null;
  config_ref?: ResourceRef | null;
  supported_actions: string[];
  notes?: string | null;
  checked_at: string;
  blockers: string[];
}

export interface OrchestratorConnectionList {
  orchestrators: OrchestratorConnection[];
  checked_at: string;
  status: State;
}

export type TaskStatus =
  | "draft"
  | "dry_run"
  | "queued"
  | "pending_confirmation"
  | "running"
  | "done"
  | "failed"
  | "cancelled"
  | "blocked";
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
  cycle_id?: string | null;
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
  audit_uri?: string | null;
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

export interface SourceFreshness {
  source_id: string;
  status: "live" | "stale" | "error" | "unavailable";
  observed_at?: string | null;
  age_seconds?: number | null;
  poll_interval_seconds?: number | null;
  message?: string | null;
}

export interface StatusDiagnostic {
  diagnostic_id: string;
  status: "warn" | "blocked" | "fail";
  scope: "cycle" | "stage" | "metric" | "readiness" | "promotion" | "drift" | "cdct" | "resource" | "sync";
  component: string;
  code: string;
  summary: string;
  remediation: string;
  source: string;
  evidence_uri?: string | null;
  observed_at?: string | null;
  details: Record<string, unknown>;
}

export interface ControlPanelDiagnostics {
  schema_version: "evm.control_panel.diagnostics.v1";
  generated_at: string;
  cycle_id: string;
  status: "pass" | "warn" | "blocked" | "fail";
  blocked_count: number;
  warn_count: number;
  fail_count: number;
  sources: SourceFreshness[];
  diagnostics: StatusDiagnostic[];
  state_digest: string;
  snapshot_uri?: string | null;
  audit_uri?: string | null;
}

export type DriftReviewStatus = "open" | "acknowledged" | "approved" | "closed";

export interface DriftReviewTransition {
  from_status: DriftReviewStatus;
  to_status: DriftReviewStatus;
  actor: string;
  reason: string;
  timestamp: string;
}

export interface DriftReviewWorkflow {
  schema_version: "evm.drift_review.workflow.v1";
  event_id: string;
  event_type: string;
  status: DriftReviewStatus;
  candidate_id: string;
  dataset_version: string;
  triggered_rules: string[];
  review_queue_count: number;
  evidence_uri?: string | null;
  label_review_queue_uri?: string | null;
  approval_required: boolean;
  automatic_retraining: boolean;
  automatic_deployment: boolean;
  automatic_promotion: boolean;
  next_actions: DriftReviewStatus[];
  transitions: DriftReviewTransition[];
  updated_at?: string | null;
  dry_run: boolean;
  projected_status?: DriftReviewStatus | null;
  audit_uri?: string | null;
}

export interface DriftReviewTransitionRequest {
  target_status: "acknowledged" | "approved" | "closed";
  actor: string;
  reason: string;
  expected_status: DriftReviewStatus;
  dry_run: boolean;
}

export type DecisionState = "draft" | "review" | "approved" | "rejected";
export type DecisionSubjectType =
  | "experiment"
  | "prompt_change"
  | "model_candidate"
  | "evaluation_policy"
  | "drift_review"
  | "serving_change";

export interface DecisionRecordRequest {
  subject_type: DecisionSubjectType;
  title: string;
  summary: string;
  owner: string;
  evidence_uris: string[];
  metadata: Record<string, unknown>;
}

export interface DecisionTransition {
  from_state: DecisionState;
  to_state: DecisionState;
  actor: string;
  reason: string;
  timestamp: string;
}

export interface DecisionRecord extends DecisionRecordRequest {
  decision_id: string;
  state: DecisionState;
  version: number;
  created_at: string;
  updated_at: string;
  transitions: DecisionTransition[];
}

export interface DecisionRecordList {
  decisions: DecisionRecord[];
  status: State;
  blockers: string[];
}

export interface DecisionTransitionRequest {
  target_state: DecisionState;
  actor: string;
  reason: string;
  expected_version: number;
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
  cycle_id?: string | null;
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
  dispatched_at?: string | null;
  finished_at?: string | null;
  runtime_system?: string | null;
  runtime_id?: string | null;
  runtime_url?: string | null;
  runtime_state?: string | null;
  runtime_evidence_uri?: string | null;
  failure_reason?: string | null;
  audit: AuditEvent[];
}

export interface TaskAssignmentList {
  tasks: TaskAssignment[];
}

export interface TaskTransitionRequest {
  actor: string;
  reason: string;
}

export type PipelineCapabilityStatus = "wired" | "partial" | "not_wired";
export type PipelineExecutionScope = "data_cycle" | "full_lifecycle";
export type PipelinePlanState = "not_started" | "ready" | "blocked";

export interface PipelineSplitPolicy {
  seed: number;
  train: number;
  validation: number;
  test: number;
  stratified: boolean;
  cross_validation_enabled: boolean;
  cross_validation_folds: number;
  holdout_split: "validation" | "test";
  immutable_holdout: boolean;
  allow_holdout_in_training: boolean;
}

export interface PipelineDataProfile {
  dataset_name: string;
  dataset_version: string;
  source_manifest_uri: string;
  split_manifest_uri: string;
  split_manifest_sha256: string;
  fail_on_empty: boolean;
  fail_on_quality_error: boolean;
  duplicate_severity: "info" | "warn" | "error";
  dimension_severity: "info" | "warn" | "error";
  max_review_samples: number;
  records_per_shard: number;
  split: PipelineSplitPolicy;
}

export interface PipelineHyperparameterSearchSpace {
  learning_rates: number[];
  weight_decays: number[];
  batch_sizes: number[];
  optimizers: Array<"adamw" | "sgd">;
  freeze_backbone_options: boolean[];
}

export interface PipelineModelProfile {
  framework: "torch";
  component_id: string;
  component_version: string;
  architecture: string;
  pretrained: boolean;
  freeze_backbone: boolean;
  input_size: number;
  batch_size: number;
  epochs: number;
  optimizer: "adamw" | "sgd";
  learning_rate: number;
  weight_decay: number;
  mixed_precision: boolean;
  class_weighted_loss: boolean;
  early_stop_metric: "accuracy" | "f1" | "auroc";
  early_stop_threshold: number;
  early_stop_min_epochs: number;
  early_stop_patience: number;
  tuning_mode: "manual" | "grid" | "bayesian";
  max_trials: number;
  search_space: PipelineHyperparameterSearchSpace;
}

export interface ModelComponent {
  component_id: string;
  version: string;
  display_name: string;
  status: "approved" | "deprecated" | "blocked";
  framework: "torch";
  architecture: string;
  backbone: string;
  runtime_adapter: string;
  default_input_size: number;
  supported_input_sizes: number[];
  source_revision: string;
  training_image: string;
  serving_image: string;
}

export interface ModelComponentCatalog {
  schema_version: "evm.model_component_catalog.v1";
  components: ModelComponent[];
  catalog_digest: string;
}

export interface PipelineExperimentProfile {
  mlflow_experiment_name: string;
  primary_metric: "accuracy" | "f1" | "auroc";
  repeats: number;
  ab_test_enabled: boolean;
  control_candidate_id?: string | null;
  challenger_candidate_id?: string | null;
  challenger_traffic_percent: number;
}

export interface PipelineGateProfile {
  promotion_min_accuracy: number;
  promotion_min_f1: number;
  promotion_min_auroc: number;
  isolated_ct_dataset_required: boolean;
  ct_dataset_split: "validation" | "test";
  require_ci: boolean;
  require_cd: boolean;
  require_ct: boolean;
  require_drift_review: boolean;
  approval_policy: "manual" | "two_person" | "change_ticket";
  target_environment: EnvironmentTier;
  target_namespace: string;
}

export interface PipelineResourceProfile {
  compute_target: "windows-rtx-4080-super" | "mac-mini-m4-pro" | "cpu-local";
  gpu_count: number;
  cpu_request: number;
  memory_gb: number;
  max_parallel_trials: number;
}

export interface PipelineRunProfile {
  schema_version: "evm.pipeline_profile.v1";
  profile_name: string;
  description: string;
  owner: string;
  execution_scope: PipelineExecutionScope;
  base_airflow_config: string;
  base_model_config: string;
  data: PipelineDataProfile;
  model: PipelineModelProfile;
  experiment: PipelineExperimentProfile;
  gates: PipelineGateProfile;
  resources: PipelineResourceProfile;
}

export interface PipelineCapability {
  capability_id: string;
  label: string;
  status: PipelineCapabilityStatus;
  active: boolean;
  detail: string;
}

export interface PipelinePlanStage {
  stage_id: string;
  label: string;
  runtime: "airflow" | "mlflow" | "kubernetes" | "control-plane";
  state: PipelinePlanState;
  progress: number;
  detail: string;
}

export interface PipelineProfileValidation {
  status: "ready" | "blocked";
  valid: boolean;
  executable: boolean;
  checked_at: string;
  blockers: string[];
  warnings: string[];
  capabilities: PipelineCapability[];
  stages: PipelinePlanStage[];
}

export interface PipelineProfileRecord {
  profile_id: string;
  version: number;
  digest: string;
  created_at: string;
  profile: PipelineRunProfile;
  validation: PipelineProfileValidation;
  profile_uri: string;
  airflow_config_uri: string;
  airflow_runtime_uri: string;
  model_config_uri: string;
  model_runtime_uri: string;
  profile_snapshot_sha256: string;
  source_manifest_sha256: string;
  split_manifest_file_sha256: string;
  airflow_config_sha256: string;
  model_config_sha256: string;
  model_component_catalog_sha256: string;
  reproducibility_digest: string;
}

export interface PipelineReplayCheck {
  check_id: string;
  status: "pass" | "fail";
  expected: string;
  observed: string;
  evidence_uri: string;
}

export interface PipelineProfileReplayValidation {
  schema_version: "evm.pipeline_profile_replay_validation.v1";
  profile_id: string;
  version: number;
  status: "ready" | "blocked";
  reproducibility_digest: string;
  checked_at: string;
  checks: PipelineReplayCheck[];
  blockers: string[];
}

export interface PipelineProfileList {
  profiles: PipelineProfileRecord[];
}

export interface PipelineProfileLaunchRequest {
  actor: string;
  reason: string;
  dry_run: boolean;
}

export interface PipelineProfileLaunch {
  profile_id: string;
  version: number;
  validation: PipelineProfileValidation;
  task?: TaskAssignment | null;
}

export type LifecycleRunState =
  | "dry_run"
  | "queued"
  | "running"
  | "waiting_approval"
  | "blocked"
  | "failed"
  | "completed"
  | "cancelled"
  | "rolling_back"
  | "rolled_back";

export type LifecycleStageState =
  | "not_started"
  | "queued"
  | "running"
  | "waiting_approval"
  | "blocked"
  | "failed"
  | "completed"
  | "skipped"
  | "cancelled";

export interface LifecycleStage {
  stage_id: string;
  label: string;
  runtime: "control-plane" | "airflow" | "kubernetes" | "mlflow" | "github-actions" | "serving" | "prometheus";
  state: LifecycleStageState;
  progress: number;
  attempt: number;
  max_attempts: number;
  started_at?: string | null;
  finished_at?: string | null;
  task_id?: string | null;
  runtime_id?: string | null;
  runtime_state?: string | null;
  evidence_uri?: string | null;
  detail?: string | null;
  blockers: string[];
}

export interface LifecycleRun {
  schema_version: "evm.lifecycle_run.v1";
  run_id: string;
  profile_id: string;
  profile_version: number;
  profile_digest: string;
  effective_config_digest: string;
  source_commit?: string | null;
  source_branch?: string | null;
  state: LifecycleRunState;
  version: number;
  actor: string;
  reason: string;
  dry_run: boolean;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  current_stage?: string | null;
  progress: number;
  profile_snapshot_uri: string;
  airflow_config_uri: string;
  airflow_runtime_uri: string;
  model_config_uri: string;
  model_runtime_uri: string;
  artifact_root: string;
  cycle_id?: string | null;
  cycle_snapshot_uri?: string | null;
  model_matrix_uri?: string | null;
  readiness_uri?: string | null;
  real_test_validation_uri?: string | null;
  resource_handoff_uri?: string | null;
  deployment_intent_id?: string | null;
  approver?: string | null;
  failure_reason?: string | null;
  blockers: string[];
  stages: LifecycleStage[];
  audit: AuditEvent[];
}

export interface LifecycleRunList {
  runs: LifecycleRun[];
  total: number;
}

export interface LifecycleWorkerState {
  status: "online" | "stale" | "offline";
  worker_id?: string | null;
  pid?: number | null;
  started_at?: string | null;
  last_seen_at?: string | null;
  current_run_id?: string | null;
  message?: string | null;
}

export type ExperimentRunState =
  | "planned"
  | "running"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "blocked"
  | "failed";

export interface ExperimentFoldResult {
  repeat: number;
  fold: number;
  state: "planned" | "running" | "completed" | "blocked" | "cancelled";
  seed: number;
  train_records: number;
  validation_records: number;
  metrics: Record<string, number>;
  mlflow_run_id?: string | null;
  artifact_uri?: string | null;
  blocker?: string | null;
}

export interface ExperimentTrialResult {
  trial_id: string;
  state: "planned" | "running" | "completed" | "blocked" | "cancelled";
  parameters: Record<string, string | number | boolean>;
  folds: ExperimentFoldResult[];
  aggregate_metrics: Record<string, number>;
  score?: number | null;
  blocker?: string | null;
}

export interface ExperimentRun {
  schema_version: "evm.experiment_run.v1";
  experiment_id: string;
  lifecycle_run_id: string;
  profile_name: string;
  profile_digest: string;
  dataset_version: string;
  source_manifest_sha256: string;
  holdout_split: string;
  holdout_sha256: string;
  mode: "manual" | "grid" | "bayesian";
  primary_metric: "accuracy" | "f1" | "auroc";
  seed: number;
  folds: number;
  repeats: number;
  requested_trials: number;
  total_units: number;
  completed_units: number;
  progress: number;
  state: ExperimentRunState;
  gpu_quota: number;
  scheduled_parallelism: number;
  parent_mlflow_run_id?: string | null;
  selected_trial_id?: string | null;
  selected_parameters: Record<string, string | number | boolean>;
  fold_manifest_uri?: string | null;
  comparison_matrix_uri?: string | null;
  final_model_matrix_uri?: string | null;
  trials: ExperimentTrialResult[];
  blockers: string[];
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface LifecycleRunRequest {
  profile_id: string;
  profile_version?: number | null;
  actor: string;
  reason: string;
  dry_run: boolean;
}

export interface LifecycleActionRequest {
  actor: string;
  reason: string;
  expected_version: number;
}

export interface LifecycleApprovalRequest extends LifecycleActionRequest {
  approver: string;
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

export interface CycleRunSummary {
  cycle_id: string;
  status: State;
  started_at: string;
  finished_at?: string | null;
  dataset_id: string;
  dataset_version: string;
  model_name: string;
  model_version: string;
  model_stage: string;
  environment?: EnvironmentTier | null;
  owner_issue: string;
  stage_count: number;
  progress: number;
  source_uri?: string | null;
  live: boolean;
}

export interface CycleRunList {
  cycles: CycleRunSummary[];
  latest_cycle_id: string;
  selected_cycle_id?: string | null;
  total: number;
}
