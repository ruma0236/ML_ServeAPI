import { describe, expect, it } from "vitest";

import type { DeploymentIntent, RuntimeResource } from "../../apps/control-panel/src/api/types";
import { buildDeploymentInventory } from "../../apps/control-panel/src/views/deploymentInventoryModel";

describe("multi-model deployment inventory", () => {
  it("deduplicates intent history and reconciles active, scaled, and rolled-back targets", () => {
    const intents = [
      intent("deploy-prod-new", "2026-07-14T03:00:00Z", "production", "evm-production", "evm-b0-production", "applied", "b0-v6"),
      intent("deploy-prod-old", "2026-07-13T03:00:00Z", "production", "evm-production", "evm-b0-production", "applied", "b0-v5"),
      intent("deploy-stage", "2026-07-14T02:00:00Z", "staging", "evm-staging", "evm-b0-staging", "applied", "b0-v6"),
      intent("deploy-b7", "2026-07-14T01:00:00Z", "staging", "evm-staging", "evm-b7-serving", "rolled_back", "b7-v1")
    ];
    const resources = [
      resource("evm-production", "evm-b0-production", 1, 1, "pass"),
      resource("evm-staging", "evm-b0-staging", 0, 0, "queued"),
      resource("evm-staging", "evm-b7-serving", 0, 0, "queued")
    ];

    const inventory = buildDeploymentInventory(intents, resources);

    expect(inventory).toMatchObject({ active: 1, scaledDown: 1, attention: 0, total: 3 });
    expect(inventory.items.map((item) => [item.targetName, item.runtimeState])).toEqual([
      ["evm-b0-production", "active"],
      ["evm-b0-staging", "scaled_down"],
      ["evm-b7-serving", "rolled_back"]
    ]);
    expect(inventory.items[0].candidateId).toBe("b0-v6");
  });

  it("does not call an applied intent active without a live runtime observation", () => {
    const inventory = buildDeploymentInventory([
      intent("deploy-unverified", "2026-07-14T03:00:00Z", "production", "evm-production", "evm-new-model", "applied", "new-v1")
    ], []);

    expect(inventory.items[0].runtimeState).toBe("unverified");
    expect(inventory.attention).toBe(1);
  });
});

function intent(
  intentId: string,
  updatedAt: string,
  environment: "staging" | "production",
  namespace: string,
  name: string,
  state: DeploymentIntent["state"],
  candidateId: string
): DeploymentIntent {
  return {
    intent_id: intentId,
    updated_at: updatedAt,
    created_at: updatedAt,
    state,
    target_environment: environment,
    target_namespace: namespace,
    target: { namespace, kind: "Deployment", name },
    model_candidate_id: candidateId,
    model_digest: `${candidateId}-digest`,
    image_digest: `${candidateId}-image`,
    cycle_id: `${candidateId}-cycle`
  } as DeploymentIntent;
}

function resource(
  namespace: string,
  name: string,
  ready: number,
  desired: number,
  status: RuntimeResource["status"]
): RuntimeResource {
  return {
    resource_id: `${namespace}:Deployment:${name}`,
    namespace,
    kind: "Deployment",
    name,
    status,
    node_pool: "docker-desktop",
    readiness: desired === ready && desired > 0 ? "ready" : "not_requested",
    restarts: 0,
    control_actions: ["view"],
    related_stages: ["Model Serving"],
    observation_source: "kubernetes_snapshot",
    observation_status: "live",
    observed_at: "2026-07-14T04:00:00Z",
    ready_replicas: ready,
    desired_replicas: desired
  };
}
