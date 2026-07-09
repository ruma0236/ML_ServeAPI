import { describe, expect, it } from "vitest";

import exampleCycle from "../../contracts/control-panel/examples/cycle-run.json";
import { statusTone, summarizeCycle, toResourceNodes } from "../../apps/control-panel/src/api/controlPanelClient";
import type { CycleRun } from "../../apps/control-panel/src/api/types";

describe("Control Panel CycleRun bindings", () => {
  const cycle = exampleCycle as CycleRun;

  it("summarizes the live CycleRun fields used by the overview", () => {
    const summary = summarizeCycle(cycle);

    expect(summary.cycleId).toBe(cycle.cycle_id);
    expect(summary.datasetVersion).toBe(cycle.dataset.version);
    expect(summary.modelVersion).toContain(cycle.model.model_name);
    expect(summary.stageCount).toBe(cycle.stages.length);
    expect(summary.artifactCount).toBe(cycle.artifacts.length);
  });

  it("maps pipeline resources without losing namespace and kind", () => {
    const nodes = toResourceNodes(cycle);

    expect(nodes.length).toBe(cycle.resources.length);
    expect(nodes[0]).toEqual(
      expect.objectContaining({
        namespace: cycle.resources[0].namespace,
        kind: cycle.resources[0].kind,
        name: cycle.resources[0].name
      })
    );
  });

  it("keeps blocked, running, and passing states visually distinct", () => {
    expect(statusTone("pass")).toBe("good");
    expect(statusTone("running")).toBe("run");
    expect(statusTone("blocked")).toBe("bad");
    expect(statusTone("unknown")).toBe("idle");
  });
});
