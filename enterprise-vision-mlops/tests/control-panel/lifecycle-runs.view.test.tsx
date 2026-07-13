import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ExperimentRun, LifecycleRun } from "../../apps/control-panel/src/api/types";


const api = vi.hoisted(() => ({
  fetchLifecycleRuns: vi.fn(),
  fetchExperimentRun: vi.fn(),
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
    api.fetchExperimentRun.mockResolvedValue(null);
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

  it("renders live cross-validation progress and MLflow lineage", async () => {
    api.fetchExperimentRun.mockResolvedValue(experimentRun());

    await act(async () => root.render(<LifecycleRuns />));
    await flushUpdates();

    expect(api.fetchExperimentRun).toHaveBeenCalledWith(run.run_id);
    expect(container.textContent).toContain("Experiment Search");
    expect(container.textContent).toContain("grid / 2-fold / seed 20260713");
    expect(container.textContent).toContain("2/5");
    expect(container.textContent).toContain("mlflow-pare");
    expect(container.textContent).toContain("trial-001");
    expect(container.textContent).toContain("learning rate");
    expect(container.textContent).toContain("F1 94.0%");
    expect(
      container.querySelector('[aria-label="Experiment search progress"]')?.getAttribute("aria-valuenow")
    ).toBe("40");
  });

  it("uses experiment units for the active model-training stage progress", async () => {
    const training = {
      ...run,
      state: "running" as const,
      current_stage: "model_training",
      progress: 0.2,
      failure_reason: null,
      blockers: [],
      stages: run.stages.map((stage) => stage.stage_id === "model_training"
        ? { ...stage, state: "running" as const, progress: 0 }
        : stage)
    };
    api.fetchLifecycleRuns.mockResolvedValue({ runs: [training], total: 1 });
    api.fetchExperimentRun.mockResolvedValue(experimentRun());

    await act(async () => root.render(<LifecycleRuns />));
    await flushUpdates();

    const progress = container.querySelector(
      '[role="progressbar"][aria-label="model training progress"]'
    );
    expect(progress?.getAttribute("aria-valuenow")).toBe("40");
    expect(progress?.getAttribute("aria-valuetext")).toBe("In Progress, 40%");
  });

  it("shows live epoch telemetry and requires a Blueprint revision after quality regression", async () => {
    const onOpenBlueprint = vi.fn();
    api.fetchExperimentRun.mockResolvedValue({
      ...experimentRun(),
      state: "blocked",
      training_telemetry: {
        unit_role: "cross_validation",
        phase: "training",
        trial_id: "trial-002",
        repeat: 0,
        fold: 1,
        epoch: 3,
        epochs: 6,
        step: 51,
        steps: 102,
        optimizer_steps: 255,
        unit_progress: 0.42,
        train_loss: 0.1842,
        validation_metrics: { f1: 0.462 },
        updated_at: "2026-07-13T00:04:00Z"
      },
      quality_review: {
        schema_version: "evm.model_quality_review.v1",
        event_id: "model-quality-1234",
        event_type: "model_quality_regression",
        state: "review_required",
        fingerprint: "a".repeat(64),
        source_profile_digest: "d".repeat(64),
        dataset_version: "visa-open-data-e35d93d5561f",
        selected_trial_id: "trial-001",
        selected_parameters: { learning_rate: 0.0003 },
        candidate_id: "efficientnet-b0",
        observed_metrics: { f1: 0.462 },
        policy_thresholds: { f1: 0.75 },
        failed_gates: ["f1<0.75"],
        recommendations: ["unfreeze_backbone", "expand_learning_rate_search"],
        repeat_guard: "block_same_profile",
        evidence_uri: "F:/evidence/model_quality_review.json",
        created_at: "2026-07-13T00:05:00Z"
      }
    });

    await act(async () => root.render(<LifecycleRuns onOpenBlueprint={onOpenBlueprint} />));
    await flushUpdates();

    expect(container.textContent).toContain("Epoch3/6");
    expect(container.textContent).toContain("Step51/102");
    expect(container.textContent).toContain("model quality regression");
    expect(container.textContent).toContain("46.2%");
    expect(container.textContent).toContain("policy 75.0%");
    const retry = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent?.includes("Blueprint Revision Required")
    );
    expect(retry?.disabled).toBe(true);
    const tune = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent?.includes("Tune Blueprint")
    );
    await act(async () => tune?.click());
    expect(onOpenBlueprint).toHaveBeenCalledOnce();
  });

  it("renders a completed legacy approval as approved", async () => {
    const approved = {
      ...run,
      state: "completed" as const,
      current_stage: null,
      progress: 1,
      failure_reason: null,
      blockers: [],
      stages: run.stages.map((stage) => stage.stage_id === "approval"
        ? { ...stage, state: "completed" as const, progress: 1, runtime_state: "two_person_approval_required" }
        : stage)
    };
    api.fetchLifecycleRuns.mockResolvedValue({ runs: [approved], total: 1 });
    await act(async () => root.render(<LifecycleRuns />));
    await flushUpdates();

    const approval = container.querySelector('[aria-label="approval: Completed"]');
    expect(approval?.textContent).toContain("control-plane / approved");
    expect(approval?.textContent).not.toContain("two_person_approval_required");
  });

  it("synchronizes the selected run snapshot with the shared workspace", async () => {
    const onCycleContext = vi.fn();
    await act(async () => root.render(<LifecycleRuns onCycleContext={onCycleContext} />));
    await flushUpdates();

    expect(onCycleContext).toHaveBeenCalledWith(expect.objectContaining({
      run_id: run.run_id,
      cycle_id: "cycle-contract-1"
    }));
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


function experimentRun(): ExperimentRun {
  return {
    schema_version: "evm.experiment_run.v1",
    experiment_id: run.run_id,
    lifecycle_run_id: run.run_id,
    profile_name: "w8-b0-grid",
    profile_digest: "d".repeat(64),
    dataset_version: "visa-open-data-e35d93d5561f",
    source_manifest_sha256: "e".repeat(64),
    holdout_split: "test",
    holdout_sha256: "f".repeat(64),
    mode: "grid",
    primary_metric: "f1",
    seed: 20260713,
    folds: 2,
    repeats: 1,
    requested_trials: 2,
    total_units: 5,
    completed_units: 2,
    progress: 0.4,
    state: "running",
    gpu_quota: 1,
    scheduled_parallelism: 1,
    parent_mlflow_run_id: "mlflow-parent-123456",
    selected_parameters: {},
    trials: [{
      trial_id: "trial-001",
      state: "completed",
      parameters: { learning_rate: 0.0003 },
      folds: [{
        repeat: 0,
        fold: 0,
        state: "completed",
        seed: 20260713,
        train_records: 4320,
        validation_records: 4320,
        metrics: { accuracy: 0.95, f1: 0.94, auroc: 0.97 },
        mlflow_run_id: "child-run-123456"
      }],
      aggregate_metrics: { f1_mean: 0.94 },
      score: 0.94
    }],
    blockers: [],
    created_at: "2026-07-13T00:00:00Z",
    updated_at: "2026-07-13T00:05:00Z",
    started_at: "2026-07-13T00:00:10Z"
  };
}


async function flushUpdates(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}
