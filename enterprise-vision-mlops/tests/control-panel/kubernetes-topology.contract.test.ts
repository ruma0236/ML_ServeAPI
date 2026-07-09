import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchRuntimeResources, resourcePressure } from "../../apps/control-panel/src/api/controlPanelClient";
import type { RuntimeResourceList } from "../../apps/control-panel/src/api/types";

describe("W7 Kubernetes topology bindings", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads RuntimeResource records from the resource API contract", async () => {
    const payload: RuntimeResourceList = {
      resources: [
        {
          resource_id: "evm-platform:Deployment:evm-api",
          namespace: "evm-platform",
          kind: "Deployment",
          name: "evm-api",
          status: "pass",
          node_pool: "local-compose-platform",
          readiness: "ready",
          restarts: 0,
          cpu_request: "250m",
          memory_request: "512Mi",
          storage_claim: null,
          storage_root: null,
          owner_issue: "EVM-224",
          control_actions: ["view", "restart_dry_run", "scale_dry_run"],
          pressure: "pass",
          related_stages: ["Model Lifecycle"]
        }
      ]
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const resources = await fetchRuntimeResources("http://control-panel.test");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://control-panel.test/control-panel/v1/resources",
      expect.objectContaining({ headers: { Accept: "application/json" } })
    );
    expect(resources[0]).toEqual(expect.objectContaining({ namespace: "evm-platform", name: "evm-api" }));
    expect(resources[0].control_actions).toContain("restart_dry_run");
    expect(resourcePressure(resources[0])).toBe("pass");
  });

  it("falls back to status when pressure is not provided", () => {
    expect(
      resourcePressure({
        resource_id: "evm-pipelines:Job:evm-efficientnet-training",
        namespace: "evm-pipelines",
        kind: "Job",
        name: "evm-efficientnet-training",
        status: "queued",
        node_pool: "windows-rtx-4080-super",
        restarts: 0,
        gpu_request: "1 x RTX 4080 SUPER",
        control_actions: ["view", "rerun_dry_run", "cancel_dry_run"]
      })
    ).toBe("queued");
  });
});
