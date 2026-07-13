import type {
  CommandIntent,
  CommandIntentList,
  CommandIntentRequest,
  ControlPanelDiagnostics,
  CycleRun,
  CycleRunList,
  DecisionRecord,
  DecisionRecordList,
  DecisionRecordRequest,
  DecisionTransitionRequest,
  DeploymentIntent,
  DeploymentIntentList,
  DeploymentIntentRequest,
  DeploymentTransitionRequest,
  DriftReviewTransitionRequest,
  DriftReviewWorkflow,
  LifecycleActionRequest,
  LifecycleApprovalRequest,
  LifecycleRun,
  LifecycleRunList,
  LifecycleRunRequest,
  LifecycleWorkerState,
  ModelComponentCatalog,
  OrchestratorConnectionList,
  PipelineProfileLaunch,
  PipelineProfileLaunchRequest,
  PipelineProfileList,
  PipelineProfileRecord,
  PipelineProfileReplayValidation,
  PipelineProfileValidation,
  PipelineRunProfile,
  PromotionPolicyDecision,
  PromotionPolicyRequest,
  ResourceRef,
  RuntimeResource,
  RuntimeResourceList,
  State,
  TaskAssignment,
  TaskAssignmentList,
  TaskAssignmentRequest,
  TaskTransitionRequest
} from "./types";

export const API_BASE =
  import.meta.env.VITE_CONTROL_PANEL_API_BASE?.replace(/\/$/, "") || "";

export async function fetchLatestCycle(baseUrl = API_BASE): Promise<CycleRun> {
  const response = await fetch(`${baseUrl}/control-panel/v1/cycles/latest`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw new Error(`CycleRun request failed: ${response.status}`);
  }
  return (await response.json()) as CycleRun;
}

export async function fetchCycles(baseUrl = API_BASE): Promise<CycleRunList> {
  const response = await fetch(`${baseUrl}/control-panel/v1/cycles?limit=100`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw new Error(`CycleRun catalog request failed: ${response.status}`);
  }
  return (await response.json()) as CycleRunList;
}

export async function fetchCycle(cycleId: string, baseUrl = API_BASE): Promise<CycleRun> {
  const response = await fetch(`${baseUrl}/control-panel/v1/cycles/${encodeURIComponent(cycleId)}`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw new Error(`CycleRun ${cycleId} request failed: ${response.status}`);
  }
  return (await response.json()) as CycleRun;
}

export async function fetchControlPanelDiagnostics(
  cycleId?: string,
  baseUrl = API_BASE
): Promise<ControlPanelDiagnostics> {
  const query = cycleId ? `?cycle_id=${encodeURIComponent(cycleId)}` : "";
  const response = await fetch(`${baseUrl}/control-panel/v1/diagnostics/latest${query}`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw new Error(`Diagnostics request failed: ${response.status}`);
  }
  return (await response.json()) as ControlPanelDiagnostics;
}

export async function fetchLatestDriftReview(baseUrl = API_BASE): Promise<DriftReviewWorkflow> {
  const response = await fetch(`${baseUrl}/control-panel/v1/drift-reviews/latest`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw new Error(`Drift review request failed: ${response.status}`);
  }
  return (await response.json()) as DriftReviewWorkflow;
}

export async function transitionDriftReview(
  eventId: string,
  request: DriftReviewTransitionRequest,
  baseUrl = API_BASE
): Promise<DriftReviewWorkflow> {
  const response = await fetch(
    `${baseUrl}/control-panel/v1/drift-reviews/${eventId}/transition`,
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(request)
    }
  );
  if (!response.ok) {
    throw await controlPanelError(response, "Drift review transition failed");
  }
  return (await response.json()) as DriftReviewWorkflow;
}

export async function fetchDecisionRecords(baseUrl = API_BASE): Promise<DecisionRecordList> {
  const response = await fetch(`${baseUrl}/control-panel/v1/decisions`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw new Error(`Decision registry request failed: ${response.status}`);
  }
  return (await response.json()) as DecisionRecordList;
}

export async function createDecisionRecord(
  request: DecisionRecordRequest,
  baseUrl = API_BASE
): Promise<DecisionRecord> {
  const response = await fetch(`${baseUrl}/control-panel/v1/decisions`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    throw await controlPanelError(response, "Decision create failed");
  }
  return (await response.json()) as DecisionRecord;
}

export async function transitionDecisionRecord(
  decisionId: string,
  request: DecisionTransitionRequest,
  baseUrl = API_BASE
): Promise<DecisionRecord> {
  const response = await fetch(
    `${baseUrl}/control-panel/v1/decisions/${decisionId}/transition`,
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(request)
    }
  );
  if (!response.ok) {
    throw await controlPanelError(response, "Decision transition failed");
  }
  return (await response.json()) as DecisionRecord;
}

export async function evaluatePromotionPolicy(
  request: PromotionPolicyRequest,
  baseUrl = API_BASE
): Promise<PromotionPolicyDecision> {
  const response = await fetch(`${baseUrl}/control-panel/v1/promotion-policy/evaluate`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    throw new Error(`Promotion policy evaluation failed: ${response.status}`);
  }
  return (await response.json()) as PromotionPolicyDecision;
}

export class ControlPanelApiError extends Error {
  blockers: string[];

  constructor(message: string, blockers: string[] = []) {
    super(message);
    this.name = "ControlPanelApiError";
    this.blockers = blockers;
  }
}

export async function fetchDeploymentIntents(
  baseUrl = API_BASE,
  cycleId?: string
): Promise<DeploymentIntentList> {
  const query = cycleId ? `?cycle_id=${encodeURIComponent(cycleId)}` : "";
  const response = await fetch(`${baseUrl}/control-panel/v1/deployment-intents${query}`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw await controlPanelError(response, "DeploymentIntent request failed");
  }
  return (await response.json()) as DeploymentIntentList;
}

export async function createDeploymentIntent(
  request: DeploymentIntentRequest,
  baseUrl = API_BASE
): Promise<DeploymentIntent> {
  const response = await fetch(`${baseUrl}/control-panel/v1/deployment-intents`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    throw await controlPanelError(response, "DeploymentIntent create failed");
  }
  return (await response.json()) as DeploymentIntent;
}

export async function transitionDeploymentIntent(
  intentId: string,
  action: "request-approval" | "approve" | "queue",
  request: DeploymentTransitionRequest,
  baseUrl = API_BASE
): Promise<DeploymentIntent> {
  const response = await fetch(
    `${baseUrl}/control-panel/v1/deployment-intents/${intentId}/${action}`,
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(request)
    }
  );
  if (!response.ok) {
    throw await controlPanelError(response, `DeploymentIntent ${action} failed`);
  }
  return (await response.json()) as DeploymentIntent;
}

async function controlPanelError(response: Response, fallback: string): Promise<ControlPanelApiError> {
  let message = `${fallback}: ${response.status}`;
  let blockers: string[] = [];
  try {
    const payload = (await response.json()) as {
      detail?: { message?: string; blockers?: string[]; error?: string } | string;
    };
    if (typeof payload.detail === "string") {
      message = payload.detail;
    } else if (payload.detail) {
      blockers = payload.detail.blockers || [];
      message = payload.detail.message || payload.detail.error || message;
    }
  } catch {
    // The HTTP status remains the authoritative fallback for non-JSON failures.
  }
  return new ControlPanelApiError(message, blockers);
}

export async function fetchRuntimeResources(baseUrl = API_BASE): Promise<RuntimeResourceList> {
  const response = await fetch(`${baseUrl}/control-panel/v1/resources`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw new Error(`RuntimeResource request failed: ${response.status}`);
  }
  const payload = (await response.json()) as RuntimeResourceList;
  return payload;
}

export async function fetchOrchestrators(baseUrl = API_BASE): Promise<OrchestratorConnectionList> {
  const response = await fetch(`${baseUrl}/control-panel/v1/orchestrators`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw new Error(`Orchestrator connection request failed: ${response.status}`);
  }
  return (await response.json()) as OrchestratorConnectionList;
}

export async function fetchTaskAssignments(baseUrl = API_BASE): Promise<TaskAssignment[]> {
  const response = await fetch(`${baseUrl}/control-panel/v1/tasks`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw new Error(`TaskAssignment request failed: ${response.status}`);
  }
  const payload = (await response.json()) as TaskAssignmentList;
  return payload.tasks;
}

export async function fetchDefaultTaskAssignment(baseUrl = API_BASE): Promise<TaskAssignmentRequest> {
  const response = await fetch(`${baseUrl}/control-panel/v1/tasks/default`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw new Error(`Default task request failed: ${response.status}`);
  }
  return (await response.json()) as TaskAssignmentRequest;
}

export async function createTaskAssignment(request: TaskAssignmentRequest, baseUrl = API_BASE): Promise<TaskAssignment> {
  const response = await fetch(`${baseUrl}/control-panel/v1/tasks`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    throw new Error(`TaskAssignment create failed: ${response.status}`);
  }
  return (await response.json()) as TaskAssignment;
}

export async function dispatchTaskAssignment(taskId: string, baseUrl = API_BASE): Promise<TaskAssignment> {
  const response = await fetch(`${baseUrl}/control-panel/v1/tasks/${encodeURIComponent(taskId)}/dispatch`, {
    method: "POST",
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw await controlPanelError(response, "Task dispatch failed");
  }
  return (await response.json()) as TaskAssignment;
}

export async function confirmTaskAssignment(
  taskId: string,
  request: TaskTransitionRequest,
  baseUrl = API_BASE
): Promise<TaskAssignment> {
  const response = await fetch(`${baseUrl}/control-panel/v1/tasks/${encodeURIComponent(taskId)}/confirm`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    throw await controlPanelError(response, "Task confirmation failed");
  }
  return (await response.json()) as TaskAssignment;
}

export async function fetchDefaultPipelineProfile(baseUrl = API_BASE): Promise<PipelineRunProfile> {
  const response = await fetch(`${baseUrl}/control-panel/v1/pipeline-profiles/default`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) throw new Error(`Default pipeline profile failed: ${response.status}`);
  return (await response.json()) as PipelineRunProfile;
}

export async function fetchModelComponents(baseUrl = API_BASE): Promise<ModelComponentCatalog> {
  const response = await fetch(`${baseUrl}/control-panel/v1/model-components`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) throw new Error(`Model component catalog failed: ${response.status}`);
  return (await response.json()) as ModelComponentCatalog;
}

export async function fetchPipelineProfiles(baseUrl = API_BASE): Promise<PipelineProfileRecord[]> {
  const response = await fetch(`${baseUrl}/control-panel/v1/pipeline-profiles`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) throw new Error(`Pipeline profile list failed: ${response.status}`);
  return ((await response.json()) as PipelineProfileList).profiles;
}

export async function fetchPipelineProfileReplayValidation(
  profileId: string,
  version: number,
  baseUrl = API_BASE
): Promise<PipelineProfileReplayValidation> {
  const response = await fetch(
    `${baseUrl}/control-panel/v1/pipeline-profiles/${encodeURIComponent(profileId)}/replay-validation?version=${version}`,
    { headers: { Accept: "application/json" } }
  );
  if (!response.ok) throw await controlPanelError(response, "Pipeline profile replay validation failed");
  return (await response.json()) as PipelineProfileReplayValidation;
}

export async function validatePipelineProfile(
  profile: PipelineRunProfile,
  baseUrl = API_BASE
): Promise<PipelineProfileValidation> {
  const response = await fetch(`${baseUrl}/control-panel/v1/pipeline-profiles/validate`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(profile)
  });
  if (!response.ok) throw await controlPanelError(response, "Pipeline profile validation failed");
  return (await response.json()) as PipelineProfileValidation;
}

export async function savePipelineProfile(
  profile: PipelineRunProfile,
  baseUrl = API_BASE
): Promise<PipelineProfileRecord> {
  const response = await fetch(`${baseUrl}/control-panel/v1/pipeline-profiles`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(profile)
  });
  if (!response.ok) throw await controlPanelError(response, "Pipeline profile save failed");
  return (await response.json()) as PipelineProfileRecord;
}

export async function launchPipelineProfile(
  profileId: string,
  version: number,
  request: PipelineProfileLaunchRequest,
  baseUrl = API_BASE
): Promise<PipelineProfileLaunch> {
  const response = await fetch(
    `${baseUrl}/control-panel/v1/pipeline-profiles/${encodeURIComponent(profileId)}/launch?version=${version}`,
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(request)
    }
  );
  if (!response.ok) throw await controlPanelError(response, "Pipeline profile launch failed");
  return (await response.json()) as PipelineProfileLaunch;
}

export async function fetchLifecycleRuns(baseUrl = API_BASE): Promise<LifecycleRunList> {
  const response = await fetch(`${baseUrl}/control-panel/v1/lifecycle-runs`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) throw await controlPanelError(response, "LifecycleRun list failed");
  return (await response.json()) as LifecycleRunList;
}

export async function fetchLifecycleWorker(baseUrl = API_BASE): Promise<LifecycleWorkerState> {
  const response = await fetch(`${baseUrl}/control-panel/v1/lifecycle-runs/worker`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) throw await controlPanelError(response, "Lifecycle worker status failed");
  return (await response.json()) as LifecycleWorkerState;
}

export async function fetchLifecycleRun(
  runId: string,
  baseUrl = API_BASE
): Promise<LifecycleRun> {
  const response = await fetch(
    `${baseUrl}/control-panel/v1/lifecycle-runs/${encodeURIComponent(runId)}`,
    { headers: { Accept: "application/json" } }
  );
  if (!response.ok) throw await controlPanelError(response, "LifecycleRun request failed");
  return (await response.json()) as LifecycleRun;
}

export async function createLifecycleRun(
  request: LifecycleRunRequest,
  baseUrl = API_BASE
): Promise<LifecycleRun> {
  const response = await fetch(`${baseUrl}/control-panel/v1/lifecycle-runs`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) throw await controlPanelError(response, "LifecycleRun create failed");
  return (await response.json()) as LifecycleRun;
}

export async function transitionLifecycleRun(
  runId: string,
  action: "queue" | "cancel" | "retry",
  request: LifecycleActionRequest,
  baseUrl = API_BASE
): Promise<LifecycleRun> {
  const response = await fetch(
    `${baseUrl}/control-panel/v1/lifecycle-runs/${encodeURIComponent(runId)}/${action}`,
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(request)
    }
  );
  if (!response.ok) throw await controlPanelError(response, `LifecycleRun ${action} failed`);
  return (await response.json()) as LifecycleRun;
}

export async function approveLifecycleRun(
  runId: string,
  request: LifecycleApprovalRequest,
  baseUrl = API_BASE
): Promise<LifecycleRun> {
  const response = await fetch(
    `${baseUrl}/control-panel/v1/lifecycle-runs/${encodeURIComponent(runId)}/approve`,
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(request)
    }
  );
  if (!response.ok) throw await controlPanelError(response, "LifecycleRun approval failed");
  return (await response.json()) as LifecycleRun;
}

export async function fetchCommandIntents(baseUrl = API_BASE): Promise<CommandIntent[]> {
  const response = await fetch(`${baseUrl}/control-panel/v1/commands`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw new Error(`CommandIntent request failed: ${response.status}`);
  }
  const payload = (await response.json()) as CommandIntentList;
  return payload.commands;
}

export async function createCommandIntent(request: CommandIntentRequest, baseUrl = API_BASE): Promise<CommandIntent> {
  const response = await fetch(`${baseUrl}/control-panel/v1/commands`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    throw new Error(`CommandIntent create failed: ${response.status}`);
  }
  return (await response.json()) as CommandIntent;
}

export async function confirmCommandIntent(commandId: string, baseUrl = API_BASE): Promise<CommandIntent> {
  const response = await fetch(`${baseUrl}/control-panel/v1/commands/${commandId}/confirm`, {
    method: "POST",
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw new Error(`CommandIntent confirm failed: ${response.status}`);
  }
  return (await response.json()) as CommandIntent;
}

export async function cancelCommandIntent(commandId: string, baseUrl = API_BASE): Promise<CommandIntent> {
  const response = await fetch(`${baseUrl}/control-panel/v1/commands/${commandId}/cancel`, {
    method: "POST",
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw new Error(`CommandIntent cancel failed: ${response.status}`);
  }
  return (await response.json()) as CommandIntent;
}

export interface CycleSummary {
  cycleId: string;
  status: State;
  datasetVersion: string;
  modelVersion: string;
  stageCount: number;
  blockedStages: number;
  artifactCount: number;
  runningStage?: string;
}

export function summarizeCycle(cycle: CycleRun): CycleSummary {
  const blockedStages = cycle.stages.filter((stage) => stage.status === "blocked" || stage.status === "fail").length;
  const runningStage = cycle.stages.find((stage) => stage.status === "running" || stage.status === "queued")?.name;
  return {
    cycleId: cycle.cycle_id,
    status: cycle.status,
    datasetVersion: cycle.dataset.version,
    modelVersion: `${cycle.model.model_name} v${cycle.model.version}`,
    stageCount: cycle.stages.length,
    blockedStages,
    artifactCount: cycle.artifacts.length,
    runningStage
  };
}

export interface ResourceNode {
  id: string;
  namespace: string;
  kind: string;
  name: string;
  status: State;
}

export interface StageSummary {
  stageId: string;
  name: string;
  status: State;
  artifactCount: number;
  metricCount: number;
  resourceCount: number;
  blocker: string;
}

export function summarizeStages(cycle: CycleRun): StageSummary[] {
  return cycle.stages.map((stage) => ({
    stageId: stage.stage_id,
    name: stage.name,
    status: stage.status,
    artifactCount: stage.artifacts.length,
    metricCount: stage.metrics.length,
    resourceCount: stage.resources.length,
    blocker: stage.failure_reason || stage.current_step || "closed"
  }));
}

export function toResourceNodes(cycle: CycleRun): ResourceNode[] {
  const statusByResource = new Map<string, State>();
  for (const stage of cycle.stages) {
    for (const resource of stage.resources) {
      statusByResource.set(resourceKey(resource), stage.status);
    }
  }
  return cycle.resources.map((resource) => ({
    id: resourceKey(resource),
    namespace: resource.namespace,
    kind: resource.kind,
    name: resource.name,
    status: statusByResource.get(resourceKey(resource)) || cycle.status
  }));
}

function resourceKey(resource: ResourceRef): string {
  return `${resource.namespace}:${resource.kind}:${resource.name}`;
}

export function statusTone(status: State | string | null | undefined): "good" | "warn" | "bad" | "idle" | "run" {
  if (status === "pass" || status === "done") return "good";
  if (status === "warn") return "warn";
  if (status === "fail" || status === "blocked" || status === "cancelled") return "bad";
  if (status === "running" || status === "queued" || status === "dry_run" || status === "pending_confirmation") return "run";
  return "idle";
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return new Intl.NumberFormat("en-US").format(value);
}

export function compactUri(value: string | null | undefined): string {
  if (!value) return "-";
  if (value.length <= 54) return value;
  return `${value.slice(0, 24)}...${value.slice(-24)}`;
}

export function resourcePressure(resource: RuntimeResource): State {
  return resource.pressure || resource.status || "unknown";
}

export function commandActionFor(resource: RuntimeResource): CommandIntentRequest["action"] {
  const kind = resource.kind.toLowerCase();
  if (kind === "deployment" || kind === "pod" || kind === "service") return "restart_deployment";
  if (kind === "job") return "run_pipeline_job";
  if (kind === "persistentvolumeclaim") return "run_cd_verification";
  return "trigger_drift_review";
}

export function commandStatusTone(status: string): "good" | "warn" | "bad" | "idle" | "run" {
  if (status === "applied") return "good";
  if (status === "dry_run" || status === "pending_confirmation" || status === "applying") return "run";
  if (status === "cancelled" || status === "failed" || status === "rolled_back") return "bad";
  return "idle";
}
