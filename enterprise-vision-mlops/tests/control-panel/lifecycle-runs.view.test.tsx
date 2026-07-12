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
