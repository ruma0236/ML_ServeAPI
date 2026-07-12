import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createLifecycleRun,
  fetchLifecycleRuns,
  transitionLifecycleRun
} from "../../apps/control-panel/src/api/controlPanelClient";
import type { LifecycleRun } from "../../apps/control-panel/src/api/types";


const run = lifecycleRun();


describe("LifecycleRun API contract", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists, creates, and retries a versioned run", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ runs: [run], total: 1 }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(run), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...run, version: 4, state: "queued" }), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);

    expect((await fetchLifecycleRuns("http://panel.test")).total).toBe(1);
    await createLifecycleRun({
      profile_id: run.profile_id,
      profile_version: 1,
      actor: "ml-platform",
      reason: "Contract lifecycle creation",
      dry_run: true
    }, "http://panel.test");
    await transitionLifecycleRun(run.run_id, "retry", {
      actor: "ml-platform",
      reason: "Retry blocked stage",
      expected_version: 3
    }, "http://panel.test");

    expect(fetchMock.mock.calls[0][0]).toBe("http://panel.test/control-panel/v1/lifecycle-runs");
    expect(fetchMock.mock.calls[2][0]).toBe(
      `http://panel.test/control-panel/v1/lifecycle-runs/${run.run_id}/retry`
    );
    expect(JSON.parse(fetchMock.mock.calls[2][1].body).expected_version).toBe(3);
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
