const API_BASE = process.env.EVM_CONTROL_PANEL_E2E_API_URL || "http://127.0.0.1:8000";

interface WorkerState {
  status?: string;
  message?: string | null;
}

interface ResourceState {
  observation_status?: string;
  snapshot_age_seconds?: number | null;
  observation_message?: string | null;
}

export default async function globalSetup(): Promise<void> {
  if (process.env.EVM_CONTROL_PANEL_E2E_SKIP_RUNTIME_PREFLIGHT === "1") return;

  let worker: WorkerState = {};
  let resources: ResourceState = {};
  let lastError = "";
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      const [workerResponse, resourceResponse] = await Promise.all([
        fetch(`${API_BASE}/control-panel/v1/lifecycle-runs/worker`),
        fetch(`${API_BASE}/control-panel/v1/resources`)
      ]);
      if (!workerResponse.ok || !resourceResponse.ok) {
        throw new Error(`worker=${workerResponse.status} resources=${resourceResponse.status}`);
      }
      worker = await workerResponse.json() as WorkerState;
      resources = await resourceResponse.json() as ResourceState;
      if (worker.status === "online" && resources.observation_status === "live") return;
      lastError = runtimeSummary(worker, resources);
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }

  throw new Error([
    `Control Panel live-runtime preflight failed: ${lastError || "unknown runtime state"}`,
    "Start the required host services before E2E:",
    "  powershell -ExecutionPolicy Bypass -File scripts/dev/start_kubernetes_observer.ps1 -Restart",
    "  powershell -ExecutionPolicy Bypass -File scripts/dev/start_lifecycle_worker.ps1 -Restart",
    "Set EVM_CONTROL_PANEL_E2E_SKIP_RUNTIME_PREFLIGHT=1 only for intentionally isolated UI tests."
  ].join("\n"));
}

function runtimeSummary(worker: WorkerState, resources: ResourceState): string {
  const age = resources.snapshot_age_seconds == null
    ? "unknown"
    : `${Math.round(resources.snapshot_age_seconds)}s`;
  return [
    `worker=${worker.status || "unknown"}`,
    `worker_message=${worker.message || "none"}`,
    `kubernetes=${resources.observation_status || "unknown"}`,
    `snapshot_age=${age}`,
    `kubernetes_message=${resources.observation_message || "none"}`
  ].join(" ");
}
