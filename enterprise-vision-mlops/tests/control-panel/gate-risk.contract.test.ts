import { describe, expect, it } from "vitest";

import exampleCycle from "../../contracts/control-panel/examples/cycle-run.json";
import type { CycleRun } from "../../apps/control-panel/src/api/types";

describe("Gate and risk UI bindings", () => {
  const cycle = exampleCycle as CycleRun;

  it("keeps drift review fields separate and actionable", () => {
    expect(cycle.drift?.data_drift_status).toBe("warn");
    expect(cycle.drift?.prediction_drift_status).toBe("pass");
    expect(cycle.drift?.measurement_status).toBe("measured");
    expect(cycle.drift?.reference_window_id).toBe("validation-all-products");
    expect(cycle.drift?.current_window_id).toBe("test-pcb3-intake");
    expect(cycle.drift?.action).toBe("label_review");
    expect(cycle.drift?.review_event_type).toBe("review_required");
    expect(cycle.drift?.review_queue_count).toBe(128);
    expect(cycle.drift?.triggered_rules).toEqual(["input_category_js"]);
    expect(cycle.drift?.automatic_retraining).toBe(false);
    expect(cycle.drift?.approval_required).toBe(true);
  });

  it("keeps CI, CD, and CT gate fields separate from promotion blockers", () => {
    expect(cycle.cdct_gate?.ci_status).toBe("pass");
    expect(cycle.cdct_gate?.cd_status).toBe("pass");
    expect(cycle.cdct_gate?.ct_status).toBe("blocked");
    expect(cycle.cdct_gate?.promotion_decision).toBe("block");
    expect(cycle.cdct_gate?.failed_checks).toEqual(["model_evaluation", "drift_review", "promotion_gate"]);
    expect(cycle.cdct_gate?.promotion_blockers).toEqual(["accuracy<0.7", "auroc<0.65"]);
    expect(cycle.cdct_gate?.verification_summary?.model_evaluation).toBe("blocked");
  });
});
