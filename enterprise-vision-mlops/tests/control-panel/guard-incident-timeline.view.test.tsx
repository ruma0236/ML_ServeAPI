import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";

import type { GuardIncidentPlane } from "../../apps/control-panel/src/api/types";
import { GuardIncidentTimeline } from "../../apps/control-panel/src/views/GuardIncidentTimeline";


const snapshot: GuardIncidentPlane = {
  schema_version: "evm.guard_incident_plane.v1",
  status: "live",
  generated_at_utc: "2026-08-02T15:00:00Z",
  source_revision: "c0bf42277ec4e227b9a38e0326e638eada736026",
  policy_version: "recovery-coordination-v1",
  mutation_endpoint_available: false,
  incidents: [{
    incident_id: "inc-ui-current-0001",
    correlation_id: "correlation-ui-current-0001",
    state: "recovery_owned",
    root_fingerprint: "f".repeat(64),
    event_count: 3,
    causal_edge_count: 2,
    blockers: [],
    target_class: "production-b0",
    target_identity_digest: "a".repeat(64),
    owner_id: "scenario-a-controller",
    fencing_token: 4,
    lease_expires_at_utc: "2026-08-02T15:00:20Z",
    authorized_recommendation_count: 1,
    timing: {
      collection_delay_ms: 5000,
      correlation_overhead_ms: 31.2,
      containment_seconds: 0.2,
      recovery_seconds: 10.1
    },
    child_evidence_uris: ["F:/evidence/scenario-a/validation-report.json"],
    created_at_utc: "2026-08-02T14:59:54Z",
    updated_at_utc: "2026-08-02T15:00:00Z"
  }],
  leases: [{
    lease_id: "lease-ui-current-0001",
    incident_id: "inc-ui-current-0001",
    owner_id: "scenario-a-controller",
    fencing_token: 4,
    state: "active",
    expires_at_utc: "2026-08-02T15:00:20Z",
    target: { target_class: "production-b0", identity_digest: "a".repeat(64) }
  }],
  actions: [{
    action_key: "b".repeat(64),
    incident_id: "inc-ui-current-0001",
    target_class: "production-b0",
    owner_id: "scenario-a-controller",
    fencing_token: 4,
    action: "recommend-exact-restart",
    state: "authorized_recommendation",
    recorded_at_utc: "2026-08-02T15:00:00Z",
    external_mutation_dispatched: false
  }],
  blocked_decision_count: 2,
  active_blockers: [],
  evidence_root: "F:/evidence/recovery-proof"
};


describe("GuardIncidentTimeline", () => {
  it("renders exact owner, timing, evidence counts, and no mutation control", async () => {
    const client = vi.fn().mockResolvedValue(snapshot);
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(<GuardIncidentTimeline client={client} pollMs={60_000} />);
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Recovery Owned");
    expect(container.textContent).toContain("scenario-a-controller / fence 4");
    expect(container.textContent).toContain("31.2 ms");
    expect(container.textContent).toContain("10.1 s");
    expect(container.textContent).toContain("3 events");
    expect(container.textContent).toContain("Not exposed");
    expect(container.querySelectorAll("button")).toHaveLength(0);

    await act(async () => root.unmount());
    container.remove();
  });

  it("renders stale snapshots as historical instead of an active recovery", async () => {
    const client = vi.fn().mockResolvedValue({ ...snapshot, status: "stale" });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(<GuardIncidentTimeline client={client} pollMs={60_000} />);
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Snapshot stale");
    expect(container.textContent).toContain("Historical incident state");
    expect(container.querySelector(".incident-row")?.className).toContain("state-warn");
    expect(container.querySelector(".incident-row .status-badge")?.textContent).toBe("stale");

    await act(async () => root.unmount());
    container.remove();
  });
});
