import { afterEach, describe, expect, it, vi } from "vitest";

import {
  cancelCommandIntent,
  confirmCommandIntent,
  createCommandIntent,
  createTaskAssignment,
  fetchCommandIntents,
  fetchTaskAssignments
} from "../../apps/control-panel/src/api/controlPanelClient";
import type { CommandIntent, TaskAssignment, TaskAssignmentRequest } from "../../apps/control-panel/src/api/types";

describe("W7 operations API bindings", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates task assignments through the operations contract", async () => {
    const request: TaskAssignmentRequest = {
      task_type: "airflow_dag_run",
      owner: "ai-infra-sre",
      priority: "normal",
      resource_profile: "local-pipeline-workers",
      config_payload: { dag_id: "enterprise_vision_mlops_daily" },
      dry_run: true
    };
    const response: TaskAssignment = {
      ...request,
      task_id: "task-contract",
      status: "dry_run",
      created_at: "2026-07-09T00:00:00Z",
      queued_at: null,
      audit: [{ timestamp: "2026-07-09T00:00:00Z", actor: "ai-infra-sre", event: "task_assignment_created", details: {} }]
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(response), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);

    const task = await createTaskAssignment(request, "http://control-panel.test");

    expect(task.status).toBe("dry_run");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://control-panel.test/control-panel/v1/tasks",
      expect.objectContaining({ method: "POST", body: JSON.stringify(request) })
    );
  });

  it("loads task and command ledgers", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ tasks: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ commands: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    expect(await fetchTaskAssignments("http://control-panel.test")).toEqual([]);
    expect(await fetchCommandIntents("http://control-panel.test")).toEqual([]);
  });

  it("creates, confirms, and cancels command intents without apply state", async () => {
    const command: CommandIntent = {
      command_id: "cmd-contract",
      action: "restart_deployment",
      target: { namespace: "evm-platform", kind: "Deployment", name: "evm-api" },
      actor: "ai-infra-sre",
      dry_run: true,
      reason: "contract verification",
      parameters: {},
      status: "dry_run",
      created_at: "2026-07-09T00:00:00Z",
      audit: []
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(command), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...command, status: "pending_confirmation" }), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...command, status: "cancelled" }), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);

    expect((await createCommandIntent(command, "http://control-panel.test")).status).toBe("dry_run");
    expect((await confirmCommandIntent(command.command_id, "http://control-panel.test")).status).toBe("pending_confirmation");
    expect((await cancelCommandIntent(command.command_id, "http://control-panel.test")).status).toBe("cancelled");
  });
});
