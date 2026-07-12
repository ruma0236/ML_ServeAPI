import { afterEach, describe, expect, it, vi } from "vitest";

import {
  cancelCommandIntent,
  confirmTaskAssignment,
  confirmCommandIntent,
  createCommandIntent,
  createTaskAssignment,
  dispatchTaskAssignment,
  evaluatePromotionPolicy,
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

  it("dispatches queued Airflow assignments through the runtime route", async () => {
    const response = {
      task_id: "task-runtime",
      task_type: "airflow_dag_run",
      owner: "ai-infra-sre",
      priority: "normal",
      resource_profile: "local-pipeline-workers",
      config_payload: { dag_id: "enterprise_vision_mlops_daily" },
      dry_run: false,
      status: "running",
      created_at: "2026-07-12T00:00:00Z",
      runtime_system: "airflow",
      runtime_id: "cp__runtime",
      runtime_state: "queued",
      audit: []
    } satisfies TaskAssignment;
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(response), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);

    expect((await dispatchTaskAssignment("task-runtime", "http://control-panel.test")).status).toBe("running");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://control-panel.test/control-panel/v1/tasks/task-runtime/dispatch",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("confirms a manual task before dispatch", async () => {
    const response = {
      task_id: "task-manual",
      task_type: "airflow_dag_run",
      owner: "ai-infra-sre",
      priority: "normal",
      resource_profile: "local-pipeline-workers",
      approval_policy: "manual",
      config_payload: { dag_id: "enterprise_vision_mlops_daily" },
      dry_run: false,
      status: "queued",
      created_at: "2026-07-12T00:00:00Z",
      queued_at: "2026-07-12T00:01:00Z",
      audit: []
    } satisfies TaskAssignment;
    const transition = { actor: "ai-infra-sre", reason: "operator confirmed" };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(response), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);

    expect((await confirmTaskAssignment("task-manual", transition, "http://control-panel.test")).status).toBe("queued");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://control-panel.test/control-panel/v1/tasks/task-manual/confirm",
      expect.objectContaining({ method: "POST", body: JSON.stringify(transition) })
    );
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

  it("evaluates target environment and namespace through the server policy contract", async () => {
    const response = {
      schema_version: "evm.w7.promotion_policy.v1",
      decision_id: "promotion-contract",
      policy_version: "2026.07.w7.evm-233.v1",
      decision: "pending_approval",
      status: "queued",
      target_environment: "production",
      target_namespace: "evm-production",
      requester: "ml-platform",
      approver: null,
      approval_policy: "two-person-production-approval",
      evaluated_at: "2026-07-10T00:00:00Z",
      input_digest: "a".repeat(64),
      required_checks: ["ownership", "namespace", "readiness", "ci", "approval"],
      required_approvals: ["ai-infra-sre"],
      reason_codes: ["approver_required"],
      checks: [],
      audit_uri: null
    } as const;
    const request = {
      target_environment: "production",
      target_namespace: "evm-production",
      requester: "ml-platform",
      approver: null
    } as const;
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(response), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const decision = await evaluatePromotionPolicy(request, "http://control-panel.test");

    expect(decision.decision).toBe("pending_approval");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://control-panel.test/control-panel/v1/promotion-policy/evaluate",
      expect.objectContaining({ method: "POST", body: JSON.stringify(request) })
    );
  });
});
