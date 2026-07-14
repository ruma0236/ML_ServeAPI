import { describe, expect, it } from "vitest";

import type { RuntimeResource, State } from "../../apps/control-panel/src/api/types";
import { summarizeComputeResources, summarizeComputeTelemetry } from "../../apps/control-panel/src/views/CycleOverview";

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

  it("aggregates live utilization instead of treating GPU allocation as load", () => {
    const summary = summarizeComputeTelemetry({
      schema_version: "evm.compute_telemetry.v1",
      source: "host_probe",
      status: "live",
      observed_at: "2026-07-14T05:30:00Z",
      cpu_utilization_percent: 42.5,
      memory_utilization_percent: 50,
      memory_used_bytes: 32 * 1024 ** 3,
      memory_total_bytes: 64 * 1024 ** 3,
      accelerators: [
        {
          index: 0,
          vendor: "nvidia",
          name: "NVIDIA RTX 4080 SUPER",
          utilization_percent: 64,
          memory_used_mib: 4096,
          memory_total_mib: 16384,
          temperature_c: 55,
          power_draw_w: 180,
          power_limit_w: 320
        }
      ]
    });

    expect(summary.cpuUtilizationPercent).toBe(42.5);
    expect(summary.gpuUtilizationPercent).toBe(64);
    expect(summary.gpuMemoryUtilizationPercent).toBe(25);
    expect(summary.gpuTemperatureC).toBe(55);
    expect(summary.gpuPowerDrawW).toBe(180);
  });

  it("does not present stale telemetry as a current utilization value", () => {
    const summary = summarizeComputeTelemetry({
      schema_version: "evm.compute_telemetry.v1",
      source: "host_probe",
      status: "stale",
      observed_at: "2026-07-14T05:00:00Z",
      cpu_utilization_percent: 99,
      memory_utilization_percent: 99,
      accelerators: [{
        index: 0,
        vendor: "nvidia",
        name: "NVIDIA GPU",
        utilization_percent: 99
      }]
    });

    expect(summary.cpuUtilizationPercent).toBeNull();
    expect(summary.gpuUtilizationPercent).toBeNull();
    expect(summary.status).toBe("stale");
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
