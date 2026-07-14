import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import exampleCycle from "../../contracts/control-panel/examples/cycle-run.json";
import type {
  ControlPanelDiagnostics,
  CycleRun,
  CycleRunList,
  DecisionRecordList,
  DriftReviewWorkflow,
  LifecycleRun,
  OrchestratorConnectionList,
  RuntimeResourceList
} from "../../apps/control-panel/src/api/types";

const api = vi.hoisted(() => ({
  fetchCycles: vi.fn(),
  fetchCycle: vi.fn(),
  fetchLatestCycle: vi.fn(),
  fetchOrchestrators: vi.fn(),
  fetchRuntimeResources: vi.fn(),
  fetchControlPanelDiagnostics: vi.fn(),
  fetchLatestDriftReview: vi.fn(),
  fetchDecisionRecords: vi.fn(),
  fetchExperimentRun: vi.fn(),
  fetchLifecycleRun: vi.fn(),
  fetchLifecycleRuns: vi.fn(),
  fetchLifecycleWorker: vi.fn()
}));

vi.mock("../../apps/control-panel/src/api/controlPanelClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../apps/control-panel/src/api/controlPanelClient")>()),
  ...api
}));

import { App } from "../../apps/control-panel/src/App";

const resources: RuntimeResourceList = {
  resources: [],
  observation_status: "live",
  observed_at: "2026-07-12T00:00:00Z"
};
const orchestrators: OrchestratorConnectionList = {
  orchestrators: [
    {
      orchestrator: "airflow",
      mode: "external-compose",
      control_mode: "rest-api",
      status: "pass",
      base_url: "http://airflow-webserver:8080/api/v1",
      supported_actions: ["trigger_dag"],
      checked_at: "2026-07-12T00:00:00Z",
      blockers: []
    }
  ],
  checked_at: "2026-07-12T00:00:00Z",
  status: "pass"
};
const diagnostics: ControlPanelDiagnostics = {
  schema_version: "evm.control_panel.diagnostics.v1",
  generated_at: "2026-07-12T00:00:00Z",
  cycle_id: exampleCycle.cycle_id,
  status: "blocked",
  blocked_count: 1,
  warn_count: 0,
  fail_count: 0,
  sources: [],
  diagnostics: [],
  state_digest: "stable-digest"
};
const drift: DriftReviewWorkflow = {
  schema_version: "evm.drift_review.workflow.v1",
  event_id: "drift-1",
  event_type: "review_required",
  status: "acknowledged",
  candidate_id: "effnet-b7-img600-finetune-adamw",
  dataset_version: "visa-open-data-test",
  triggered_rules: [],
  review_queue_count: 128,
  approval_required: true,
  automatic_retraining: false,
  automatic_deployment: false,
  automatic_promotion: false,
  next_actions: ["approved"],
  transitions: [],
  dry_run: false
};
const decisions: DecisionRecordList = {
  status: "pass",
  blockers: [],
  decisions: [
    {
      decision_id: "decision-1",
      subject_type: "serving_change",
      title: "Retain verified B7 rollback in staging",
      summary: "Keep production blocked until measured drift review is closed.",
      owner: "ml-platform",
      evidence_uris: [],
      metadata: {},
      state: "approved",
      version: 3,
      created_at: "2026-07-12T00:00:00Z",
      updated_at: "2026-07-12T00:00:00Z",
      transitions: []
    }
  ]
};

describe("Control Panel source synchronization", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.history.replaceState({}, "", "/");
    const catalog: CycleRunList = {
      cycles: [{
        cycle_id: exampleCycle.cycle_id,
        status: exampleCycle.status,
        started_at: exampleCycle.started_at,
        finished_at: exampleCycle.finished_at,
        dataset_id: exampleCycle.dataset.dataset_id,
        dataset_version: exampleCycle.dataset.version,
        model_name: exampleCycle.model.model_name,
        model_version: exampleCycle.model.version,
        model_stage: exampleCycle.model.stage,
        environment: exampleCycle.environment?.tier,
        owner_issue: exampleCycle.owner_issue,
        stage_count: exampleCycle.stages.length,
        progress: 1,
        live: true
      }],
      latest_cycle_id: exampleCycle.cycle_id,
      total: 1
    };
    api.fetchCycles.mockResolvedValue(catalog);
    api.fetchCycle.mockResolvedValue(exampleCycle as CycleRun);
    api.fetchLatestCycle.mockResolvedValue(exampleCycle as CycleRun);
    api.fetchOrchestrators.mockResolvedValue(orchestrators);
    api.fetchRuntimeResources.mockResolvedValue(resources);
    api.fetchControlPanelDiagnostics.mockResolvedValue(diagnostics);
    api.fetchLatestDriftReview.mockResolvedValue(drift);
    api.fetchDecisionRecords.mockResolvedValue(decisions);
    api.fetchExperimentRun.mockResolvedValue(null);
    api.fetchLifecycleRuns.mockResolvedValue({ runs: [], total: 0 });
    api.fetchLifecycleWorker.mockResolvedValue({ status: "online", worker_id: "worker-1" });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("retains the previous source value when one refresh source fails", async () => {
    await act(async () => root.render(<App />));
    await flushUpdates();
    expect(container.textContent).toContain("Live 5s");

    api.fetchDecisionRecords.mockRejectedValueOnce(new Error("decision source unavailable"));
    const refresh = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Refresh cycle"]'
    );
    expect(refresh).not.toBeNull();
    await act(async () => refresh?.click());
    await flushUpdates();
    expect(container.textContent).toContain("Partial");

    const govern = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent === "Govern"
    );
    expect(govern).toBeDefined();
    await act(async () => govern?.click());
    const governance = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent === "Decisions"
    );
    expect(governance).toBeDefined();
    await act(async () => governance?.click());
    expect(container.textContent).toContain("Retain verified B7 rollback in staging");
    expect(container.textContent).not.toContain("API unavailable");
    expect(new URLSearchParams(window.location.search).get("view")).toBe("governance");
    expect(localStorage.getItem("evm.control-panel.selected-tab")).toBeNull();
  });

  it("ignores legacy origin-scoped state and keeps the newest Runs context in the URL", async () => {
    const staleRun = lifecycleRun("lifecycle-old", "cycle-old");
    const latestRun = lifecycleRun("lifecycle-new", exampleCycle.cycle_id);
    api.fetchLifecycleRuns.mockResolvedValue({ runs: [latestRun], total: 1 });
    window.history.replaceState({}, "", "/?view=runs");
    localStorage.setItem("evm.control-panel.selected-cycle", staleRun.cycle_id);
    localStorage.setItem("evm.control-panel.selected-run", staleRun.run_id);
    localStorage.setItem("evm.control-panel.selected-tab", "release");

    await act(async () => root.render(<App />));
    await flushUpdates();
    expect(api.fetchLifecycleRun).not.toHaveBeenCalled();
    expect(new URLSearchParams(window.location.search).get("run")).toBe(latestRun.run_id);
    expect(new URLSearchParams(window.location.search).get("cycle")).toBe(latestRun.cycle_id);
    expect(localStorage.getItem("evm.control-panel.selected-run")).toBeNull();
    expect(localStorage.getItem("evm.control-panel.selected-cycle")).toBeNull();
    expect(localStorage.getItem("evm.control-panel.selected-tab")).toBeNull();

    const release = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent === "Deploy"
    );
    await act(async () => release?.click());
    const promotion = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent === "Models"
    );
    await act(async () => promotion?.click());

    expect(container.textContent).toContain(latestRun.run_id);
    expect(container.textContent).not.toContain(staleRun.run_id);
  });

  it("shows Connecting during the first request instead of reporting a false degradation", async () => {
    let resolveLatest: ((cycle: CycleRun) => void) | undefined;
    api.fetchLatestCycle.mockReturnValue(new Promise<CycleRun>((resolve) => {
      resolveLatest = resolve;
    }));

    await act(async () => root.render(<App />));
    await flushUpdates();
    expect(container.textContent).toContain("Connecting");
    expect(container.textContent).toContain("Synchronizing CycleRun");
    expect(container.textContent).not.toContain("Degraded");
    expect(container.textContent).not.toContain("Unavailable");

    await act(async () => resolveLatest?.(exampleCycle as CycleRun));
    await flushUpdates();
    expect(container.textContent).toContain("Live 5s");
    expect(container.textContent).toContain("LIVE DATA");
  });

  it("uses a shareable historical URL and clears it when returning to LIVE", async () => {
    const historical = {
      ...(exampleCycle as CycleRun),
      cycle_id: "cycle-historical-001",
      started_at: "2026-07-10T00:00:00Z"
    };
    const catalog = await api.fetchCycles();
    api.fetchCycles.mockResolvedValue({
      ...catalog,
      total: 2,
      cycles: [
        { ...catalog.cycles[0], cycle_id: historical.cycle_id, live: false },
        catalog.cycles[0]
      ]
    });
    api.fetchCycle.mockImplementation((cycleId: string) => Promise.resolve(
      cycleId === historical.cycle_id ? historical : exampleCycle as CycleRun
    ));
    window.history.replaceState({}, "", `/?cycle=${historical.cycle_id}`);

    await act(async () => root.render(<App />));
    await flushUpdates();
    expect(api.fetchCycle).toHaveBeenCalledWith(historical.cycle_id);
    expect(container.textContent).toContain("HISTORICAL SNAPSHOT");
    expect(container.textContent).toContain("Return to Live");
    expect(new URLSearchParams(window.location.search).get("cycle")).toBe(historical.cycle_id);

    const returnToLive = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent?.includes("Return to Live")
    );
    await act(async () => returnToLive?.click());
    await flushUpdates();
    expect(new URLSearchParams(window.location.search).has("cycle")).toBe(false);
    expect(container.textContent).toContain("LIVE DATA");
    expect(container.textContent).not.toContain("HISTORICAL SNAPSHOT");
  });

  it("does not let a late historical refresh overwrite a newer LIVE selection", async () => {
    const historical = {
      ...(exampleCycle as CycleRun),
      cycle_id: "cycle-historical-race",
      status: "pass" as const
    };
    const catalog = await api.fetchCycles();
    api.fetchCycles.mockResolvedValue({
      ...catalog,
      total: 2,
      cycles: [
        { ...catalog.cycles[0], cycle_id: historical.cycle_id, status: "pass", live: false },
        catalog.cycles[0]
      ]
    });
    let historicalRequests = 0;
    let resolveLateHistorical: ((cycle: CycleRun) => void) | undefined;
    api.fetchCycle.mockImplementation((cycleId: string) => {
      if (cycleId !== historical.cycle_id) return Promise.resolve(exampleCycle as CycleRun);
      historicalRequests += 1;
      if (historicalRequests === 1) return Promise.resolve(historical);
      return new Promise<CycleRun>((resolve) => {
        resolveLateHistorical = resolve;
      });
    });
    window.history.replaceState({}, "", `/?cycle=${historical.cycle_id}`);

    await act(async () => root.render(<App />));
    await flushUpdates();
    expect(container.querySelector(".footer-line")?.textContent).toContain(historical.cycle_id);

    const refresh = container.querySelector<HTMLButtonElement>('button[aria-label="Refresh cycle"]');
    await act(async () => refresh?.click());
    await flushUpdates();
    const returnToLive = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent?.includes("Return to Live")
    );
    await act(async () => returnToLive?.click());
    await flushUpdates();
    expect(container.querySelector(".footer-line")?.textContent).toContain(exampleCycle.cycle_id);

    await act(async () => resolveLateHistorical?.(historical));
    await flushUpdates();
    expect(container.querySelector(".footer-line")?.textContent).toContain(exampleCycle.cycle_id);
    expect(container.querySelector(".footer-line")?.textContent).not.toContain(historical.cycle_id);
    expect(new URLSearchParams(window.location.search).has("cycle")).toBe(false);
  });
});

async function flushUpdates(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}


function lifecycleRun(runId: string, cycleId: string): LifecycleRun {
  const stageIds = [
    "profile_snapshot",
    "data_pipeline",
    "model_training",
    "model_evaluation",
    "artifact_readiness",
    "ci_ct_gate",
    "approval",
    "deployment",
    "serving_validation",
    "monitoring"
  ];
  return {
    schema_version: "evm.lifecycle_run.v1",
    run_id: runId,
    profile_id: "standard-b0-manual-tuning",
    profile_version: 6,
    profile_digest: "a".repeat(64),
    effective_config_digest: "b".repeat(64),
    source_commit: "c".repeat(40),
    source_branch: "codex/mac-mini-worker",
    reason: "Lifecycle context test",
    actor: "ml-platform",
    state: "completed",
    current_stage: null,
    progress: 1,
    dry_run: false,
    cycle_id: cycleId,
    blockers: [],
    failure_reason: null,
    stages: stageIds.map((stageId) => ({
      stage_id: stageId,
      label: stageId.replaceAll("_", " "),
      runtime: stageId === "data_pipeline" ? "airflow" : "control-plane",
      state: "completed" as const,
      progress: 1,
      attempt: 1,
      max_attempts: 1,
      blockers: []
    })),
    audit: [],
    version: 1,
    created_at: "2026-07-13T00:00:00Z",
    updated_at: "2026-07-13T00:10:00Z"
  };
}
