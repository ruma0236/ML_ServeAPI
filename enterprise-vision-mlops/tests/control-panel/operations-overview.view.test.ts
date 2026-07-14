import { describe, expect, it } from "vitest";

import type { RuntimeResource, State } from "../../apps/control-panel/src/api/types";
import { summarizeComputeResources } from "../../apps/control-panel/src/views/CycleOverview";

describe("fleet operations compute summary", () => {
  it("counts live pod GPU allocation without historic job or controller duplication", () => {
    const summary = summarizeComputeResources([
      resource("Node", "docker-desktop", "pass", { gpu_capacity: "1" }),
      resource("Deployment", "evm-b0-production", "pass", {
        gpu_request: "1 x GPU",
        desired_replicas: 1,
        ready_replicas: 1
      }),
      resource("Pod", "evm-b0-production-live", "pass", { gpu_request: "1 x GPU" }),
      resource("Pod", "evm-b0-production-rejected", "fail", { gpu_request: "1 x GPU" }),
      resource("Job", "completed-training-a", "done", { gpu_request: "1 x GPU" }),
      resource("Job", "completed-training-b", "done", { gpu_request: "1 x GPU" }),
      resource("Deployment", "scaled-to-zero", "queued", {
        gpu_request: "1 x GPU",
        desired_replicas: 0,
        ready_replicas: 0
      })
    ]);

    expect(summary.gpuCapacity).toBe(1);
    expect(summary.gpuAllocated).toBe(1);
    expect(summary.totalWorkloads).toBe(1);
    expect(summary.readyWorkloads).toBe(1);
    expect(summary.failedWorkloads).toBe(0);
  });
});

function resource(
  kind: string,
  name: string,
  status: State,
  overrides: Partial<RuntimeResource> = {}
): RuntimeResource {
  return {
    resource_id: `evm:${kind}:${name}`,
    namespace: kind === "Node" ? "_cluster" : "evm-test",
    kind,
    name,
    status,
    node_pool: "docker-desktop",
    restarts: 0,
    control_actions: ["view"],
    observation_source: "kubernetes_snapshot",
    observation_status: "live",
    ...overrides
  };
}
