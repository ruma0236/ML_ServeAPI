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
}

export interface RuntimeResourceList {
  resources: RuntimeResource[];
}

export interface OrgContext {
  team_id: string;
  department: string;
  service_scope: string;
  product_area?: string | null;
  data_owner?: string | null;
  model_owner?: string | null;
  ops_owner?: string | null;
}

export interface EnvironmentRef {
  name: string;
  tier: string;
  promotion_state: string;
  cluster?: string | null;
  namespace?: string | null;
  release_ref?: string | null;
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

export interface DataPipelineReadiness {
  contract_status: State;
  quality_status: State;
  lineage_status: State;
  replay_ready: boolean;
  source_policy_uri?: string | null;
  quality_report_uri?: string | null;
  lineage_uri?: string | null;
  backfill_window?: string | null;
}

export interface ExperimentPipelineReadiness {
  tracking_status: State;
  evaluation_status: State;
  registry_status: State;
  promotion_ready: boolean;
  experiment_uri?: string | null;
  model_card_uri?: string | null;
  evaluation_report_uri?: string | null;
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
