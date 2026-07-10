import { describe, expect, it } from "vitest";

import exampleCycle from "../../contracts/control-panel/examples/cycle-run.json";
import type { CycleRun } from "../../apps/control-panel/src/api/types";

describe("Enterprise readiness UI bindings", () => {
  const cycle = exampleCycle as CycleRun;

  it("keeps tenant owner coverage available for service-scope filtering", () => {
    expect(cycle.tenant?.service_scope).toBe("internal-department");
    expect(cycle.tenant?.ownership_status).toBe("pass");
    expect(cycle.tenant?.missing_owners).toEqual([]);
    expect(cycle.tenant?.data_owner).toBe("data-platform");
    expect(cycle.tenant?.model_owner).toBe("ml-platform");
    expect(cycle.tenant?.ops_owner).toBe("ai-infra-sre");
  });

  it("keeps environment approval and blocker state available for promotion gates", () => {
    expect(cycle.environment?.approval_policy).toBe("owner-gated");
    expect(cycle.environment?.promotion_blockers).toEqual(["readiness_not_ready"]);
    expect(cycle.environment?.cluster).toBe("docker-desktop");
    expect(cycle.environment?.namespace).toBe("evm-staging");
    expect(cycle.promotion_policy?.decision).toBe("blocked");
    expect(cycle.promotion_policy?.required_checks).toEqual(["ownership", "namespace", "readiness", "ci"]);
    expect(cycle.promotion_policy?.reason_codes).toEqual(["readiness_not_ready"]);
  });

  it("keeps data and model readiness evidence wired to owner approval fields", () => {
    expect(cycle.data_pipeline?.owner_approval_required).toBe(true);
    expect(cycle.data_pipeline?.owner_approval_status).toBe("pass");
    expect(cycle.data_pipeline?.owner_approval_actor).toBe("data-platform");
    expect(cycle.data_pipeline?.blockers).toEqual([]);

    expect(cycle.experiment_pipeline?.rollback_ready).toBe(true);
    expect(cycle.experiment_pipeline?.owner_approval_required).toBe(true);
    expect(cycle.experiment_pipeline?.owner_approval_status).toBe("blocked");
    expect(cycle.experiment_pipeline?.owner_approval_actor).toBe("ml-platform");
    expect(cycle.experiment_pipeline?.blockers).toEqual(["accuracy<0.7", "auroc<0.65"]);
  });

  it("binds artifact-content checks and their deterministic blocker decision", () => {
    expect(cycle.readiness_evaluation?.decision).toBe("blocked");
    expect(cycle.readiness_evaluation?.candidate_id).toBe("effnet-b7-img600-finetune-adamw");
    expect(cycle.readiness_evaluation?.checks.map((check) => check.check_id)).toEqual([
      "data_contract",
      "mlflow_run",
      "kubernetes_runtime"
    ]);
    expect(cycle.readiness_evaluation?.blockers).toContain("mlflow_metrics_missing");
  });
});
