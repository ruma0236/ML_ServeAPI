import { describe, expect, it } from "vitest";

import exampleCycle from "../../contracts/control-panel/examples/cycle-run.json";
import { summarizeStages } from "../../apps/control-panel/src/api/controlPanelClient";
import type { CycleRun } from "../../apps/control-panel/src/api/types";

describe("W7 pipeline timeline bindings", () => {
  const cycle = exampleCycle as CycleRun;

  it("summarizes stage artifacts, metrics, resources, and blockers for timeline drilldowns", () => {
    const summaries = summarizeStages(cycle);
    const intake = summaries.find((stage) => stage.stageId === "data-intake");

    expect(intake).toEqual(
      expect.objectContaining({
        name: "Data Intake",
        artifactCount: 1,
        metricCount: 1,
        resourceCount: 1,
        blocker: "closed"
      })
    );
  });

  it("keeps blocked or queued evidence visible instead of treating missing proof as done", () => {
    const enriched: CycleRun = {
      ...cycle,
      stages: [
        ...cycle.stages,
        {
          stage_id: "efficientnet-real-test",
          name: "EfficientNet Real Test Matrix",
          status: "queued",
          started_at: cycle.started_at,
          finished_at: null,
          current_step: "waiting_for_evidence",
          progress: 0,
          failure_reason: "missing_or_blocked_evidence",
          artifacts: [],
          sample_outputs: [],
          metrics: [{ name: "candidate_count", value: 2, status: "queued" }],
          resources: [{ namespace: "evm-pipelines", kind: "Job", name: "evm-efficientnet-training" }]
        }
      ]
    };

    const matrix = summarizeStages(enriched).find((stage) => stage.stageId === "efficientnet-real-test");

    expect(matrix).toEqual(
      expect.objectContaining({
        status: "queued",
        metricCount: 1,
        resourceCount: 1,
        blocker: "missing_or_blocked_evidence"
      })
    );
  });
});
