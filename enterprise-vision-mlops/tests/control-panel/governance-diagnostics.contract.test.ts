import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createDecisionRecord,
  fetchControlPanelDiagnostics,
  fetchDecisionRecords,
  fetchLatestDriftReview,
  transitionDecisionRecord,
  transitionDriftReview
} from "../../apps/control-panel/src/api/controlPanelClient";

describe("Diagnostics and governance API contract", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("uses independent live-source read endpoints", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ diagnostics: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ event_id: "drift-1", status: "open" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ decisions: [] }), { status: 200 }));

    await fetchControlPanelDiagnostics("http://control-panel.test");
    await fetchLatestDriftReview("http://control-panel.test");
    await fetchDecisionRecords("http://control-panel.test");

    expect(fetchMock.mock.calls[0][0]).toBe("http://control-panel.test/control-panel/v1/diagnostics/latest");
    expect(fetchMock.mock.calls[1][0]).toBe("http://control-panel.test/control-panel/v1/drift-reviews/latest");
    expect(fetchMock.mock.calls[2][0]).toBe("http://control-panel.test/control-panel/v1/decisions");
  });

  it("serializes drift preview and audited decision transitions", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: "open", projected_status: "acknowledged" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ decision_id: "decision-1", state: "draft" }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ decision_id: "decision-1", state: "review" }), { status: 202 }));

    await transitionDriftReview("drift-1", {
      target_status: "acknowledged",
      actor: "ml-platform",
      reason: "preview measured drift evidence",
      expected_status: "open",
      dry_run: true
    }, "http://control-panel.test");
    await createDecisionRecord({
      subject_type: "model_candidate",
      title: "B7 decision",
      summary: "Review the real CUDA evidence bundle.",
      owner: "ml-platform",
      evidence_uris: [],
      metadata: {}
    }, "http://control-panel.test");
    await transitionDecisionRecord("decision-1", {
      target_state: "review",
      actor: "ml-platform",
      reason: "submit evidence for review",
      expected_version: 1
    }, "http://control-panel.test");

    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({ dry_run: true });
    expect(fetchMock.mock.calls[1][0]).toBe("http://control-panel.test/control-panel/v1/decisions");
    expect(fetchMock.mock.calls[2][0]).toContain("/decisions/decision-1/transition");
  });
});
