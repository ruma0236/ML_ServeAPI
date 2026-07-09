import type { CycleRun, ResourceRef, State } from "./types";

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
  if (status === "running" || status === "queued") return "run";
  return "idle";
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return new Intl.NumberFormat("en-US").format(value);
}
