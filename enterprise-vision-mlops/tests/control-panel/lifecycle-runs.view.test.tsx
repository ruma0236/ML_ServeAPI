import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LifecycleRun } from "../../apps/control-panel/src/api/types";


const api = vi.hoisted(() => ({
  fetchLifecycleRuns: vi.fn(),
  fetchLifecycleWorker: vi.fn(),
  transitionLifecycleRun: vi.fn(),
  approveLifecycleRun: vi.fn()
}));

vi.mock("../../apps/control-panel/src/api/controlPanelClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../apps/control-panel/src/api/controlPanelClient")>()),
  ...api
}));

import { LifecycleRuns } from "../../apps/control-panel/src/views/LifecycleRuns";


const run = lifecycleRun();


describe("Lifecycle Runs view", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    api.fetchLifecycleRuns.mockResolvedValue({ runs: [run], total: 1 });
    api.fetchLifecycleWorker.mockResolvedValue({
      status: "online",
      worker_id: "worker-1",
      current_run_id: run.run_id,
      last_seen_at: "2026-07-12T00:00:00Z"
    });
    api.transitionLifecycleRun.mockResolvedValue({ ...run, state: "queued", version: 4 });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("shows live progress, explicit blocker, and sends retry with optimistic version", async () => {
    await act(async () => root.render(<LifecycleRuns />));
    await flushUpdates();

    expect(container.textContent).toContain("Processing");
    expect(container.textContent).toContain("Blocked");
    expect(container.textContent).toContain("artifact_readiness_blocked");
    expect(container.textContent).toContain("40%");
    expect(container.textContent).not.toContain("1/3");
    expect(container.textContent).toContain("2 retries left");
    const progress = container.querySelector('[role="progressbar"][aria-label="artifact readiness progress"]');
    expect(progress?.getAttribute("aria-valuenow")).toBe("0");
    const retry = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent?.includes("Retry Stage")
    );
    expect(retry).toBeDefined();
    await act(async () => retry?.click());
    await flushUpdates();

    expect(api.transitionLifecycleRun).toHaveBeenCalledWith(
      run.run_id,
      "retry",
      expect.objectContaining({ expected_version: 3 })
    );
  });

  it("renders completion separately from retry capacity", async () => {
    await act(async () => root.render(<LifecycleRuns />));
    await flushUpdates();

    const completed = container.querySelector('[aria-label="profile snapshot: Completed"]');
    expect(completed?.textContent).toContain("100%");
    expect(completed?.textContent).not.toContain("1/3");
    expect(completed?.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")).toBe("100");
  });

  it("synchronizes the selected run snapshot with the shared workspace", async () => {
    const onCycleContext = vi.fn();
    await act(async () => root.render(<LifecycleRuns onCycleContext={onCycleContext} />));
    await flushUpdates();

    expect(onCycleContext).toHaveBeenCalledWith("cycle-contract-1");
    expect(container.textContent).toContain("cycle-contract-1");
  });

  it("disables retry when the current stage exhausted its policy", async () => {
    const exhausted = {
      ...run,
      current_stage: "artifact_readiness",
      stages: run.stages.map((stage) => stage.stage_id === "artifact_readiness"
        ? { ...stage, attempt: stage.max_attempts }
        : stage)
    };
    api.fetchLifecycleRuns.mockResolvedValue({ runs: [exhausted], total: 1 });
    await act(async () => root.render(<LifecycleRuns />));
    await flushUpdates();

    const retry = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent?.includes("Retry Exhausted")
    );
    expect(retry?.disabled).toBe(true);
  });

  it("keeps a legacy dry-run fail closed when source provenance is missing", async () => {
    const legacy = {
      ...run,
      run_id: "lifecycle-20260712T122721-4c896dd0",
      state: "dry_run" as const,
      dry_run: true,
      source_commit: null,
      source_branch: null,
      current_stage: null,
      progress: 0.1,
      failure_reason: null,
      blockers: [],
      stages: run.stages.map((stage, index) => ({
        ...stage,
        state: index === 0 ? "completed" as const : "not_started" as const,
        progress: index === 0 ? 1 : 0,
        blockers: []
      }))
    };
    api.fetchLifecycleRuns.mockResolvedValue({ runs: [legacy], total: 1 });
    api.fetchLifecycleWorker.mockResolvedValue({ status: "online", worker_id: "worker-1" });

    await act(async () => root.render(<LifecycleRuns />));
    await flushUpdates();

    expect(container.textContent).toContain("Ready to Queue");
    expect(container.textContent).toContain("source_revision_missing");
    const queue = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent?.includes("Queue Run")
    );
    expect(queue).toBeDefined();
    expect(queue?.disabled).toBe(true);
  });
});


function lifecycleRun(): LifecycleRun {
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
    run_id: "lifecycle-contract-1",
    profile_id: "b0-production",
    profile_version: 1,
    profile_digest: "a".repeat(64),
    effective_config_digest: "b".repeat(64),
    source_commit: "c".repeat(40),
    source_branch: "main",
    state: "blocked",
    version: 3,
    actor: "ml-platform",
    reason: "Contract lifecycle run",
    dry_run: false,
    created_at: "2026-07-12T00:00:00Z",
    updated_at: "2026-07-12T00:10:00Z",
    current_stage: "artifact_readiness",
    progress: 0.4,
    profile_snapshot_uri: "F:/runs/1/profile.json",
    airflow_config_uri: "F:/runs/1/airflow.json",
    airflow_runtime_uri: "/mnt/evm-data/runs/1/airflow.json",
    model_config_uri: "F:/runs/1/model.json",
    model_runtime_uri: "/mnt/evm-data/runs/1/model.json",
    artifact_root: "F:/runs/1",
    cycle_id: "cycle-contract-1",
    failure_reason: "artifact_readiness_blocked",
    blockers: ["artifact_readiness_blocked"],
    stages: stageIds.map((stageId, index) => ({
      stage_id: stageId,
      label: stageId.replaceAll("_", " "),
      runtime: index === 1 ? "airflow" : index === 2 ? "kubernetes" : "control-plane",
      state: index < 4 ? "completed" : index === 4 ? "blocked" : "not_started",
      progress: index < 4 ? 1 : 0,
      attempt: index <= 4 ? 1 : 0,
      max_attempts: 3,
      blockers: index === 4 ? ["artifact_readiness_blocked"] : []
    })),
    audit: []
  };
}


async function flushUpdates(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}
