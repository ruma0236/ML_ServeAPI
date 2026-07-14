import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ModelCandidateCatalog,
  ModelCandidateSelection,
  StageHandoffCatalog
} from "../../apps/control-panel/src/api/types";


const api = vi.hoisted(() => ({
  fetchStageHandoffs: vi.fn(),
  fetchModelCandidates: vi.fn(),
  transitionLifecycleRun: vi.fn(),
  selectModelCandidate: vi.fn()
}));

vi.mock("../../apps/control-panel/src/api/controlPanelClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../apps/control-panel/src/api/controlPanelClient")>()),
  ...api
}));

import { StageWorkbench } from "../../apps/control-panel/src/views/StageWorkbench";


describe("Stage Workbench", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    api.fetchStageHandoffs.mockResolvedValue(handoffCatalog());
    api.fetchModelCandidates.mockResolvedValue(candidateCatalog());
    api.transitionLifecycleRun.mockResolvedValue({});
    api.selectModelCandidate.mockResolvedValue(selection());
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("continues a ready stage and promotes an auditable candidate selection", async () => {
    const onPromote = vi.fn();
    await act(async () => root.render(<StageWorkbench onPromote={onPromote} />));
    await flushUpdates();

    expect(container.textContent).toContain("Model Training");
    expect(container.textContent).toContain("effnet-b0-ready");
    expect(container.textContent).toContain("1 inputs / 0 outputs");
    expect(container.textContent).toContain("previous_stage_evidence");
    const buttons = [...container.querySelectorAll<HTMLButtonElement>("button")];
    const continueButton = buttons.find((button) => button.textContent?.includes("Continue"));
    const promoteButton = buttons.find((button) => button.textContent?.includes("Promote"));
    expect(continueButton).toBeDefined();
    expect(promoteButton).toBeDefined();

    await act(async () => continueButton?.click());
    await flushUpdates();
    expect(api.transitionLifecycleRun).toHaveBeenCalledWith(
      "lifecycle-stepwise",
      "continue",
      expect.objectContaining({ expected_version: 4 })
    );

    await act(async () => promoteButton?.click());
    await flushUpdates();
    expect(api.selectModelCandidate).toHaveBeenCalledWith(
      "candidate-ready",
      expect.objectContaining({ actor: "ml-platform-operator" })
    );
    expect(onPromote).toHaveBeenCalledWith(selection());
  });
});


function handoffCatalog(): StageHandoffCatalog {
  return {
    schema_version: "evm.stage_handoff_catalog.v1",
    total: 1,
    ready: 1,
    active: 0,
    blocked: 0,
    handoffs: [{
      handoff_id: "lifecycle-stepwise:model_training",
      run_id: "lifecycle-stepwise",
      run_state: "paused",
      run_version: 4,
      execution_mode: "stepwise",
      profile_id: "profile-b0",
      profile_version: 2,
      stage_id: "model_training",
      stage_label: "Model Training",
      stage_state: "not_started",
      runtime: "kubernetes",
      bucket: "ready",
      previous_stage_id: "data_pipeline",
      next_stage_id: "model_evaluation",
      progress: 0,
      eligible_actions: ["continue", "inspect"],
      input_refs: { previous_stage_evidence: "evidence://data" },
      output_refs: {},
      blockers: [],
      updated_at: "2026-07-14T00:00:00Z"
    }]
  };
}


function candidateCatalog(): ModelCandidateCatalog {
  return {
    schema_version: "evm.model_candidate_catalog.v1",
    total: 1,
    selectable: 1,
    candidates: [{
      candidate_key: "candidate-ready",
      candidate_id: "effnet-b0-ready",
      cycle_id: "cycle-ready",
      lifecycle_run_id: "lifecycle-ready",
      matrix_id: "matrix-ready",
      architecture: "efficientnet-b0",
      framework: "torch",
      dataset_id: "visa",
      dataset_version: "visa-v1",
      model_version: "1",
      resource_profile: "gpu-local",
      status: "pass",
      metrics: { accuracy: 0.95, f1: 0.81, auroc: 0.98 },
      metric_thresholds: {},
      artifact_uri: "F:/model.pt",
      artifact_digest: "a".repeat(64),
      readiness_decision: "ready",
      ct_decision: "pass",
      source_commit: "b".repeat(40),
      environment: "staging",
      selectable: true,
      blockers: [],
      started_at: "2026-07-14T00:00:00Z",
      live: false
    }]
  };
}


function selection(): ModelCandidateSelection {
  return {
    schema_version: "evm.model_candidate_selection.v1",
    selection_id: "selection-ready",
    candidate_key: "candidate-ready",
    candidate_id: "effnet-b0-ready",
    cycle_id: "cycle-ready",
    lifecycle_run_id: "lifecycle-ready",
    matrix_id: "matrix-ready",
    dataset_version: "visa-v1",
    artifact_uri: "F:/model.pt",
    artifact_digest: "a".repeat(64),
    metrics: { accuracy: 0.95 },
    actor: "ml-platform-operator",
    reason: "Select verified candidate for governed promotion",
    created_at: "2026-07-14T00:00:00Z",
    status: "selected",
    audit_uri: "F:/selection.json"
  };
}


async function flushUpdates(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}
