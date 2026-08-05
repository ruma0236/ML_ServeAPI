import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ScenarioWorkloadRun } from "../../apps/control-panel/src/api/types";


const api = vi.hoisted(() => ({ fetchScenarioWorkloads: vi.fn() }));

vi.mock("../../apps/control-panel/src/api/controlPanelClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../apps/control-panel/src/api/controlPanelClient")>()),
  ...api
}));

import { ScenarioWorkloads } from "../../apps/control-panel/src/views/ScenarioWorkloads";


describe("AI Workloads view", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    api.fetchScenarioWorkloads.mockResolvedValue({
      runs: [workload("vlm", "completed"), workload("llm", "completed"), workload("llm", "failed")],
      total: 3
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("shows exact model identity, progress, and bounded staging state", async () => {
    await act(async () => root.render(<ScenarioWorkloads />));
    await flushUpdates();

    expect(container.textContent).toContain("SmolVLM-500M-Instruct");
    expect(container.textContent).toContain("100%");
    expect(container.textContent).toContain("0.75 GiB peak allocated");
    expect(container.textContent).toContain("retired_after_validation");
    expect(container.textContent).toContain("GPU lease released");
    expect(container.textContent).toContain("VLM metric schema");
    expect(container.textContent).toContain("Accuracy75.0%");
    expect(container.textContent).toContain("Parse rate100.0%");
    expect(container.textContent).toContain("P95 latency0.49 s");
  });

  it("switches to the LLM-specific metric schema without inventing VLM metrics", async () => {
    await act(async () => root.render(<ScenarioWorkloads />));
    await flushUpdates();
    const completedLlm = [...container.querySelectorAll<HTMLButtonElement>("aside button")].find(
      (button) => button.textContent?.includes("Qwen2.5") && button.textContent?.includes("completed")
    );
    await act(async () => completedLlm?.click());

    expect(container.textContent).toContain("LLM metric schema");
    expect(container.textContent).toContain("Validation loss1.918");
    expect(container.textContent).toContain("Token F134.4%");
    expect(container.textContent).toContain("Non-empty rate100.0%");
    expect(container.textContent).not.toContain("Parse rate");
  });

  it("surfaces the failed run blocker after selection", async () => {
    await act(async () => root.render(<ScenarioWorkloads />));
    await flushUpdates();
    const failed = [...container.querySelectorAll<HTMLButtonElement>("aside button")].find(
      (button) => button.textContent?.includes("Qwen2.5") && button.textContent?.includes("failed")
    );
    await act(async () => failed?.click());

    expect(container.textContent).toContain("mlflow_write_failed");
    expect(container.textContent).toContain("Bounded Model Adaptation");
  });
});


function workload(family: "vlm" | "llm", state: "completed" | "failed"): ScenarioWorkloadRun {
  return {
    schema_version: "evm.scenario_workload_run.v1",
    run_id: `${family}-${state}-run-1`,
    state,
    version: 2,
    actor: "operator",
    reason: "Validate one real transformer workload",
    dry_run: false,
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:01:00Z",
    progress: state === "completed" ? 1 : 0.3,
    current_stage: state === "failed" ? "adaptation" : "observability",
    identity: {
      scenario_id: family === "vlm" ? "scienceqa-vlm-evaluation" : "dolly-instruction-tuning",
      dataset_id: family === "vlm" ? "scienceqa" : "dolly",
      dataset_version: "dataset-v1",
      manifest_uri: "F:/manifest.jsonl",
      manifest_sha256: "a".repeat(64),
      split_manifest_uri: "F:/split.json",
      split_manifest_sha256: "b".repeat(64),
      data_identity_sha256: "c".repeat(64),
      quality_status: "pass",
      quality_report_uri: "F:/quality.json",
      model_family: family,
      model_repository: family === "vlm" ? "HuggingFaceTB/SmolVLM-500M-Instruct" : "Qwen/Qwen2.5-0.5B-Instruct",
      model_revision: "d".repeat(40),
      processor_revision: "d".repeat(40),
      source_commit: "e".repeat(40),
      dirty_worktree: false,
      compute_backend: "windows-host-cuda",
      identity_sha256: "f".repeat(64)
    },
    adaptation_method: family === "vlm" ? "lora" : "qlora",
    quantization_requested: family === "vlm" ? "none" : "int4_nf4",
    quantization_observed: family === "vlm" ? "none" : "int4_nf4",
    artifact_root: "F:/artifacts/run-1",
    gpu_lease_state: "released",
    mlflow_run_id: "mlflow-run-1",
    model_artifact_sha256: "1".repeat(64),
    evaluation_uri: state === "completed" ? "F:/artifacts/run-1/model/adapted-evaluation.json" : null,
    evaluation_summary: state === "completed" ? {
      schema_version: family === "vlm" ? "evm.scenario_vlm_evaluation.v1" : "evm.scenario_llm_evaluation.v1",
      model_family: family,
      quality_metrics: family === "vlm" ? {
        accuracy: 0.75,
        parse_rate: 1
      } : {
        validation_loss: 1.918441,
        mean_token_f1: 0.34365,
        nonempty_rate: 1
      },
      operational_metrics: {
        p95_latency_seconds: family === "vlm" ? 0.492075 : 5.113666,
        evaluated_records: 8,
        peak_gpu_allocated_mib: 768,
        training_seconds: 12.5
      },
      release_gate: {
        status: "pass",
        blockers: [],
        policy_source: "F:/artifacts/run-1/model/training-result.json"
      },
      evaluated_at: "2026-08-05T00:01:00Z",
      evidence_uri: "F:/artifacts/run-1/model/adapted-evaluation.json",
      claim_boundary: "Bounded local evaluation."
    } : null,
    runtime_versions: {
      gpu_name: "NVIDIA GeForce RTX 4080 SUPER",
      staging_runtime_state: state === "completed" ? "retired_after_validation" : "not_started"
    },
    peak_gpu_allocated_mib: 768,
    evidence_index_sha256: state === "completed" ? "2".repeat(64) : null,
    blockers: state === "failed" ? ["mlflow_write_failed"] : [],
    stages: [{
      stage_id: "adaptation",
      label: "Bounded Model Adaptation",
      runtime: "windows-host-cuda",
      state,
      progress: state === "completed" ? 1 : 0,
      blockers: state === "failed" ? ["mlflow_write_failed"] : []
    }],
    audit: []
  };
}


async function flushUpdates(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}
