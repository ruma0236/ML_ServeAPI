import type {
  DeploymentIntent,
  DeploymentIntentState,
  EnvironmentTier,
  RuntimeResource,
  State
} from "../api/types";

export type DeploymentRuntimeState =
  | "active"
  | "scaled_down"
  | "degraded"
  | "pending"
  | "failed"
  | "rolled_back"
  | "unverified";

export interface DeploymentInventoryItem {
  id: string;
  targetName: string;
  targetKind: string;
  namespace: string;
  environment: EnvironmentTier | "unknown";
  candidateId: string;
  modelDigest: string;
  imageDigest: string;
  cycleId: string;
  intentId: string;
  intentState: DeploymentIntentState | "untracked";
  runtimeState: DeploymentRuntimeState;
  runtimeStatus: State;
  readiness: string;
  readyReplicas: number | null;
  desiredReplicas: number | null;
  observationStatus: string;
  observationSource: string;
  reason: string;
  updatedAt: string;
}

export interface DeploymentInventorySummary {
  items: DeploymentInventoryItem[];
  active: number;
  scaledDown: number;
  attention: number;
  total: number;
}

export function buildDeploymentInventory(
  intents: DeploymentIntent[],
  resources: RuntimeResource[]
): DeploymentInventorySummary {
  const latestIntents = latestIntentByTarget(intents);
  const runtimeByTarget = new Map(
    resources
      .filter((resource) => resource.kind === "Deployment")
      .map((resource) => [targetKey(resource.namespace, resource.kind, resource.name), resource])
  );
  const keys = new Set(latestIntents.keys());
  for (const [key, resource] of runtimeByTarget) {
    if (isModelRuntime(resource)) keys.add(key);
  }

  const items = [...keys].map((key) => {
    const intent = latestIntents.get(key);
    const runtime = runtimeByTarget.get(key);
    return inventoryItem(key, intent, runtime);
  }).sort(compareInventoryItems);

  return {
    items,
    active: items.filter((item) => item.runtimeState === "active").length,
    scaledDown: items.filter((item) => item.runtimeState === "scaled_down").length,
    attention: items.filter((item) => ["degraded", "pending", "failed", "unverified"].includes(item.runtimeState)).length,
    total: items.length
  };
}

function latestIntentByTarget(intents: DeploymentIntent[]): Map<string, DeploymentIntent> {
  const sorted = [...intents].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
  const result = new Map<string, DeploymentIntent>();
  for (const intent of sorted) {
    const key = targetKey(intent.target_namespace, intent.target.kind, intent.target.name);
    if (!result.has(key)) result.set(key, intent);
  }
  return result;
}

function inventoryItem(
  key: string,
  intent?: DeploymentIntent,
  runtime?: RuntimeResource
): DeploymentInventoryItem {
  const [namespace, targetKind, ...nameParts] = key.split(":");
  const targetName = nameParts.join(":");
  return {
    id: key,
    targetName,
    targetKind,
    namespace,
    environment: intent?.target_environment || environmentFromNamespace(namespace),
    candidateId: intent?.model_candidate_id || runtime?.name || "untracked model workload",
    modelDigest: intent?.model_digest || "",
    imageDigest: intent?.image_digest || "",
    cycleId: intent?.cycle_id || "",
    intentId: intent?.intent_id || "",
    intentState: intent?.state || "untracked",
    runtimeState: resolveRuntimeState(intent, runtime),
    runtimeStatus: runtime?.status || "unknown",
    readiness: runtime?.readiness || "unobserved",
    readyReplicas: runtime?.ready_replicas ?? null,
    desiredReplicas: runtime?.desired_replicas ?? null,
    observationStatus: runtime?.observation_status || "unavailable",
    observationSource: runtime?.observation_source || "deployment_ledger",
    reason: runtime?.reason || "",
    updatedAt: runtime?.observed_at || intent?.updated_at || ""
  };
}

function resolveRuntimeState(
  intent?: DeploymentIntent,
  runtime?: RuntimeResource
): DeploymentRuntimeState {
  if (intent?.state === "rolled_back") return "rolled_back";
  if (intent?.state === "failed") return "failed";
  if (intent && ["dry_run", "pending_approval", "queued", "applying"].includes(intent.state)) {
    return "pending";
  }
  const liveRuntime = runtime?.observation_source === "kubernetes_snapshot"
    && runtime.observation_status === "live";
  if (liveRuntime && runtime.desired_replicas === 0) return "scaled_down";
  if (liveRuntime && (runtime.status === "fail" || runtime.status === "blocked")) return "degraded";
  if (liveRuntime && (runtime.desired_replicas || 0) > 0) {
    return (runtime.ready_replicas || 0) >= (runtime.desired_replicas || 0)
      ? "active"
      : "degraded";
  }
  if (intent?.state === "applied") return "unverified";
  return liveRuntime ? "degraded" : "unverified";
}

function isModelRuntime(resource: RuntimeResource): boolean {
  return resource.related_stages?.some((stage) => stage.toLowerCase().includes("serving")) === true
    || /^evm-(?:b0|b7|model-|.*-serving)/.test(resource.name);
}

function environmentFromNamespace(namespace: string): EnvironmentTier | "unknown" {
  if (namespace.includes("production") && !namespace.includes("pre-production")) return "production";
  if (namespace.includes("pre-production")) return "pre-production";
  if (namespace.includes("staging")) return "staging";
  if (namespace.includes("test")) return "test";
  if (namespace.includes("dev")) return "dev";
  return "unknown";
}

function targetKey(namespace: string, kind: string, name: string): string {
  return `${namespace}:${kind}:${name}`;
}

function compareInventoryItems(left: DeploymentInventoryItem, right: DeploymentInventoryItem): number {
  const rank: Record<DeploymentRuntimeState, number> = {
    active: 0,
    degraded: 1,
    pending: 2,
    unverified: 3,
    scaled_down: 4,
    failed: 5,
    rolled_back: 6
  };
  return rank[left.runtimeState] - rank[right.runtimeState]
    || left.environment.localeCompare(right.environment)
    || left.targetName.localeCompare(right.targetName);
}
