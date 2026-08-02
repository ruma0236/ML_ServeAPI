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

export type GuardIncidentPlaneStatus = "live" | "stale" | "unavailable";

export interface GuardIncidentTiming {
  collection_delay_ms?: number | null;
  correlation_overhead_ms?: number | null;
  containment_seconds?: number | null;
  recovery_seconds?: number | null;
}

export interface GuardIncident {
  incident_id: string;
  correlation_id: string;
  state: string;
  root_fingerprint: string;
  event_count: number;
  causal_edge_count: number;
  blockers: string[];
  target_class?: string | null;
  target_identity_digest?: string | null;
  owner_id?: string | null;
  fencing_token?: number | null;
  lease_expires_at_utc?: string | null;
  authorized_recommendation_count: number;
  timing: GuardIncidentTiming;
  child_evidence_uris: string[];
  created_at_utc: string;
  updated_at_utc: string;
}

export interface RecoveryLeaseView {
  lease_id: string;
  incident_id: string;
  owner_id: string;
  fencing_token: number;
  state: "active" | "released" | "expired" | "fenced";
  expires_at_utc: string;
  target: {
    target_class: string;
    identity_digest: string;
  };
}

export interface RecoveryActionView {
  action_key: string;
  incident_id: string;
  target_class: string;
  owner_id: string;
  fencing_token: number;
  action: string;
  state: "authorized_recommendation";
  recorded_at_utc: string;
  external_mutation_dispatched: false;
}

export interface GuardIncidentPlane {
  schema_version: "evm.guard_incident_plane.v1";
  status: GuardIncidentPlaneStatus;
  generated_at_utc: string;
  source_revision: string;
  policy_version: string;
  mutation_endpoint_available: false;
  incidents: GuardIncident[];
  leases: RecoveryLeaseView[];
  actions: RecoveryActionView[];
  blocked_decision_count: number;
  active_blockers: string[];
  evidence_root?: string | null;
}

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

export interface AcceleratorTelemetry {
  index: number;
  vendor: "nvidia";
  name: string;
  uuid?: string | null;
  utilization_percent?: number | null;
  engine_utilization_percent?: number | null;
  engine_utilization_source?: "windows_pdh" | null;
  busiest_engine?: string | null;
  memory_used_mib?: number | null;
  memory_total_mib?: number | null;
  temperature_c?: number | null;
  power_draw_w?: number | null;
  power_limit_w?: number | null;
}

export interface ComputeTelemetry {
  schema_version: "evm.compute_telemetry.v1";
  source: "host_probe";
  status: "live" | "stale" | "unavailable";
  observed_at: string;
  cpu_utilization_percent?: number | null;
  memory_utilization_percent?: number | null;
  memory_used_bytes?: number | null;
  memory_total_bytes?: number | null;
  accelerators: AcceleratorTelemetry[];
  message?: string | null;
}

export interface RuntimeResourceList {
  resources: RuntimeResource[];
  observation_status: "live" | "stale" | "projected" | "unavailable";
  observed_at?: string | null;
  snapshot_age_seconds?: number | null;
  cluster_context?: string | null;
  snapshot_uri?: string | null;
  observation_message?: string | null;
  compute_telemetry?: ComputeTelemetry | null;
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
  warnings: string[];
  quality_status?: string | null;
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
  model_selection_id?: string | null;
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
  ct_snapshot_id?: string | null;
  ct_snapshot_digest?: string | null;
  ct_evaluation_id?: string | null;
  ct_evidence_uri?: string | null;
}

export interface CTDatasetSnapshot {
  schema_version: "evm.ct_dataset_snapshot.v1";
  snapshot_id: string;
  lifecycle_run_id: string;
  profile_id: string;
  profile_version: number;
  profile_digest: string;
  dataset_version: string;
  split: "validation" | "test";
  record_count: number;
  byte_count: number;
  records_sha256: string;
  source_index_uri: string;
  source_index_sha256: string;
  source_identity_sha256: string;
  manifest_uri: string;
  manifest_sha256: string;
  snapshot_uri: string;
  snapshot_digest: string;
  isolation_root: string;
  immutable: boolean;
  training_mount_isolated: boolean;
  status: State;
  blockers: string[];
  created_at: string;
}

export interface CTEvaluation {
  schema_version: "evm.ct_evaluation.v1";
  evaluation_id: string;
  lifecycle_run_id: string;
  snapshot_id: string;
  candidate_id: string;
  dataset_version: string;
  status: State;
  decision: "pass" | "block";
  evaluated_at: string;
  snapshot_digest: string;
  expected_manifest_sha256: string;
  observed_manifest_sha256: string;
  expected_records_sha256: string;
  observed_records_sha256: string;
  ct_record_count: number;
  training_record_count: number;
  overlap_count: number;
  mutated: boolean;
  training_mount_isolated: boolean;
  model_artifact_uri?: string | null;
  model_sha256?: string | null;
  device?: string | null;
  metrics: Record<string, number>;
  metric_thresholds: Record<string, number>;
  checks: Record<string, State>;
  blockers: string[];
  snapshot_uri: string;
  fold_manifest_uri?: string | null;
  training_job_manifest_uri?: string | null;
  report_uri?: string | null;
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

export interface ModelComponentRegistrationRequest {
  component: ModelComponent;
  actor: string;
  reason: string;
}

export interface ModelComponentRegistration {
  schema_version: "evm.model_component_registration.v1";
  component: ModelComponent;
  actor: string;
  reason: string;
  registered_at: string;
  registry_uri: string;
  catalog_digest: string;
}

export type ScenarioModality = "image" | "text" | "image_text";
export type ScenarioReadiness =
  | "verified_full_lifecycle"
  | "data_ready"
  | "intake_ready"
  | "running"
  | "blocked";

export interface ScenarioTransform {
  transform_id: string;
  parameters: Record<string, unknown>;
}

export interface ScenarioDataset {
  dataset_id: string;
  dataset_name: string;
  dataset_version: string;
  source_url: string;
  source_revision: string;
  license_id: string;
  license_url: string;
  usage_policy: string;
  manifest_uri: string;
  split_manifest_uri: string;
  source_size_bytes: number;
}

export interface ScenarioIntakeState {
  status: string;
  phase: string;
  progress: number;
  records_processed: number;
  records_output: number;
  updated_at?: string | null;
  blockers: string[];
  warnings: string[];
  quality_status?: string | null;
  quality_report_uri?: string | null;
  source_registry_uri?: string | null;
}

export interface EnterpriseScenario {
  scenario_id: string;
  display_name: string;
  department: string;
  business_outcome: string;
  modality: ScenarioModality;
  readiness: ScenarioReadiness;
  data_readiness: "verified" | "ready" | "review_required" | "running" | "failed" | "not_started";
  model_readiness: "verified" | "not_implemented" | "blocked";
  deployment_readiness: "verified" | "not_implemented" | "blocked";
  intake_supported: boolean;
  profile_template?: PipelineRunProfile | null;
  model_component_id?: string | null;
  dataset: ScenarioDataset;
  recipe_id: string;
  recipe_version: string;
  transforms: ScenarioTransform[];
  blockers: string[];
  intake_state?: ScenarioIntakeState | null;
  config_uri: string;
  runtime_config_uri: string;
}

export interface EnterpriseScenarioCatalog {
  schema_version: "evm.enterprise_scenario_catalog.v1";
  catalog_digest: string;
  scenarios: EnterpriseScenario[];
}

export interface ScenarioIntakeLaunchRequest {
  actor: string;
  reason: string;
  dry_run: boolean;
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
  approval_policy: "manual" | "two_person" | "change_ticket" | "automated_non_production";
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
  | "paused"
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
  lifecycle_series_id?: string | null;
  attempt_id?: string | null;
  correlation_id?: string | null;
  source_commit?: string | null;
  source_branch?: string | null;
  state: LifecycleRunState;
  version: number;
  actor: string;
  reason: string;
  dry_run: boolean;
  execution_mode: "automatic" | "stepwise";
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
  identity_envelope_uri?: string | null;
  component_revision_map_uri?: string | null;
  guard_state_uri?: string | null;
  side_effect_ledger_uri?: string | null;
  guard_decision?: "pass" | "blocked" | null;
  guard_authorities?: string[];
  guard_blockers?: string[];
  cycle_id?: string | null;
  experiment_id?: string | null;
  cycle_snapshot_uri?: string | null;
  model_matrix_uri?: string | null;
  readiness_uri?: string | null;
  real_test_validation_uri?: string | null;
  ct_snapshot_uri?: string | null;
  ct_evaluation_uri?: string | null;
  data_integrity_uri?: string | null;
  release_submission_uri?: string | null;
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

export interface ExperimentTrainingTelemetry {
  unit_role: "cross_validation" | "final_refit";
  phase: "preparing" | "training" | "validating" | "final_refit" | "completed";
  trial_id?: string | null;
  repeat?: number | null;
  fold?: number | null;
  epoch: number;
  epochs: number;
  step: number;
  steps: number;
  optimizer_steps: number;
  unit_progress: number;
  train_loss?: number | null;
  validation_metrics: Record<string, number>;
  updated_at: string;
}

export interface ModelQualityReview {
  schema_version: "evm.model_quality_review.v1";
  event_id: string;
  event_type: "model_quality_regression";
  state: "review_required" | "resolved";
  fingerprint: string;
  source_profile_digest: string;
  dataset_version: string;
  selected_trial_id?: string | null;
  selected_parameters: Record<string, string | number | boolean>;
  candidate_id: string;
  observed_metrics: Record<string, number>;
  policy_thresholds: Record<string, number>;
  failed_gates: string[];
  recommendations: string[];
  repeat_guard: "block_same_profile";
  evidence_uri: string;
  created_at: string;
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
  training_telemetry?: ExperimentTrainingTelemetry | null;
  quality_review?: ModelQualityReview | null;
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
  execution_mode?: "automatic" | "stepwise";
}

export type StageHandoffBucket = "ready" | "active" | "blocked" | "completed" | "consumed" | "cancelled" | "waiting";

export interface StageHandoff {
  handoff_id: string;
  run_id: string;
  run_state: string;
  run_version: number;
  execution_mode: "automatic" | "stepwise";
  profile_id: string;
  profile_version: number;
  stage_id: string;
  stage_label: string;
  stage_state: string;
  runtime: string;
  bucket: StageHandoffBucket;
  previous_stage_id?: string | null;
  next_stage_id?: string | null;
  progress: number;
  eligible_actions: Array<"continue" | "retry" | "inspect">;
  input_refs: Record<string, string>;
  output_refs: Record<string, string>;
  blockers: string[];
  detail?: string | null;
  updated_at: string;
}

export interface StageHandoffCatalog {
  schema_version: "evm.stage_handoff_catalog.v1";
  handoffs: StageHandoff[];
  total: number;
  ready: number;
  active: number;
  blocked: number;
}

export interface ModelCandidateRecord {
  candidate_key: string;
  candidate_id: string;
  cycle_id: string;
  lifecycle_run_id?: string | null;
  matrix_id: string;
  architecture: string;
  framework: string;
  dataset_id: string;
  dataset_version: string;
  model_version: string;
  resource_profile: string;
  status: string;
  metrics: Record<string, number>;
  metric_thresholds: Record<string, number>;
  mlflow_run_uri?: string | null;
  artifact_uri?: string | null;
  artifact_digest?: string | null;
  readiness_decision: string;
  ct_decision: string;
  source_commit?: string | null;
  environment?: string | null;
  selectable: boolean;
  blockers: string[];
  started_at: string;
  live: boolean;
}

export interface ModelCandidateCatalog {
  schema_version: "evm.model_candidate_catalog.v1";
  candidates: ModelCandidateRecord[];
  total: number;
  selectable: number;
}

export interface ModelCandidateSelectionRequest {
  actor: string;
  reason: string;
}

export interface ModelCandidateSelection {
  schema_version: "evm.model_candidate_selection.v1";
  selection_id: string;
  candidate_key: string;
  candidate_id: string;
  cycle_id: string;
  lifecycle_run_id?: string | null;
  matrix_id: string;
  dataset_version: string;
  artifact_uri: string;
  artifact_digest: string;
  metrics: Record<string, number>;
  actor: string;
  reason: string;
  created_at: string;
  status: "selected";
  audit_uri: string;
}

export interface LifecycleActionRequest {
  actor: string;
  reason: string;
  expected_version: number;
}

export interface LifecycleApprovalRequest extends LifecycleActionRequest {
  approver: string;
  candidate_id?: string | null;
  model_digest?: string | null;
  ct_evaluation_id?: string | null;
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
  ct_snapshot?: CTDatasetSnapshot | null;
  ct_evaluation?: CTEvaluation | null;
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
