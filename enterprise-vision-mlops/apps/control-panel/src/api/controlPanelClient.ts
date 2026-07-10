import type {
  CommandIntent,
  CommandIntentList,
  CommandIntentRequest,
  CycleRun,
  DeploymentIntent,
  DeploymentIntentList,
  DeploymentIntentRequest,
  DeploymentTransitionRequest,
  PromotionPolicyDecision,
  PromotionPolicyRequest,
  ResourceRef,
  RuntimeResource,
  RuntimeResourceList,
  State,
  TaskAssignment,
  TaskAssignmentList,
  TaskAssignmentRequest
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

export async function fetchDeploymentIntents(baseUrl = API_BASE): Promise<DeploymentIntentList> {
  const response = await fetch(`${baseUrl}/control-panel/v1/deployment-intents`, {
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
