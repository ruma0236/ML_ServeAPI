import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ControlPanelApiError,
  createDeploymentIntent,
  fetchDeploymentIntents,
  transitionDeploymentIntent
} from "../../apps/control-panel/src/api/controlPanelClient";
import type { DeploymentIntentRequest } from "../../apps/control-panel/src/api/types";

const request: DeploymentIntentRequest = {
  target_environment: "staging",
  target_namespace: "evm-staging",
  target: { namespace: "evm-staging", kind: "Deployment", name: "evm-b7-serving" },
  actor: "ml-platform",
  reason: "contract test",
  dry_run: true
};

describe("Deployment intent API contract", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("uses the guarded list, create, and transition routes", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ intents: [], status: "pass", blockers: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ intent_id: "deploy-1", state: "dry_run" }), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ intent_id: "deploy-1", state: "pending_approval" }), { status: 202 }));

    await fetchDeploymentIntents("http://control-panel.test");
    await createDeploymentIntent(request, "http://control-panel.test");
    await transitionDeploymentIntent(
      "deploy-1",
      "request-approval",
      { actor: "ml-platform", reason: "request approval", expected_version: 1 },
      "http://control-panel.test"
    );

    expect(fetchMock.mock.calls[0][0]).toBe("http://control-panel.test/control-panel/v1/deployment-intents");
    expect(fetchMock.mock.calls[1][1]?.method).toBe("POST");
    expect(fetchMock.mock.calls[2][0]).toContain("/deployment-intents/deploy-1/request-approval");
  });

  it("preserves server blockers from a denied admission", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({ detail: { error: "deployment_intent_blocked", blockers: ["ci_evidence_missing"] } }),
        { status: 409, headers: { "Content-Type": "application/json" } }
      )
    );

    await expect(createDeploymentIntent(request, "http://control-panel.test")).rejects.toMatchObject<ControlPanelApiError>({
      blockers: ["ci_evidence_missing"]
    });
  });
});
