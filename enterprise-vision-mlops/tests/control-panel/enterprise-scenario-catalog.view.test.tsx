import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  EnterpriseScenarioCatalog as ScenarioCatalog,
  TaskAssignment
} from "../../apps/control-panel/src/api/types";


const api = vi.hoisted(() => ({
  fetchEnterpriseScenarios: vi.fn(),
  fetchTaskAssignments: vi.fn(),
  launchScenarioIntake: vi.fn()
}));

vi.mock("../../apps/control-panel/src/api/controlPanelClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../apps/control-panel/src/api/controlPanelClient")>()),
  ...api
}));

import { EnterpriseScenarioCatalog } from "../../apps/control-panel/src/views/EnterpriseScenarioCatalog";


describe("Enterprise scenario catalog", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    api.fetchEnterpriseScenarios.mockResolvedValue(catalog());
    api.launchScenarioIntake.mockResolvedValue(task("running", "queued"));
    api.fetchTaskAssignments.mockResolvedValue([task("done", "success")]);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("reconciles an Airflow launch to its terminal task state", async () => {
    await act(async () => root.render(
      <EnterpriseScenarioCatalog owner="ml-platform" onApplyProfile={vi.fn()} />
    ));
    await flushUpdates();

    const run = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent?.includes("Run Intake")
    );
    expect(run).toBeDefined();
    await act(async () => run?.click());
    await flushUpdates();
    await flushUpdates();

    expect(api.fetchTaskAssignments).toHaveBeenCalled();
    expect(container.textContent).toContain("done");
    expect(container.textContent).toContain("success");
  });
});


function catalog(): ScenarioCatalog {
  return {
    schema_version: "evm.enterprise_scenario_catalog.v1",
    catalog_digest: "a".repeat(64),
    scenarios: [{
      scenario_id: "banking77-intent-classification",
      display_name: "Banking Intent Routing",
      department: "Customer Operations",
      business_outcome: "Route customer requests with immutable evidence.",
      modality: "text",
      readiness: "intake_ready",
      data_readiness: "not_started",
      model_readiness: "not_implemented",
      deployment_readiness: "not_implemented",
      intake_supported: true,
      dataset: {
        dataset_id: "banking77",
        dataset_name: "BANKING77",
        dataset_version: "revision-1",
        source_url: "https://example.test/banking77",
        source_revision: "revision-1",
        license_id: "CC-BY-4.0",
        license_url: "https://creativecommons.org/licenses/by/4.0/",
        usage_policy: "test",
        manifest_uri: "F:/data/manifest.jsonl",
        split_manifest_uri: "F:/data/split.json",
        source_size_bytes: 1024
      },
      recipe_id: "banking-v1",
      recipe_version: "1.0.0",
      transforms: [],
      blockers: ["text_training_adapter_not_implemented"],
      intake_state: null,
      config_uri: "configs/scenarios/banking.json",
      runtime_config_uri: "/opt/airflow/evm_project/configs/scenarios/banking.json"
    }]
  };
}


function task(status: TaskAssignment["status"], runtimeState: string): TaskAssignment {
  return {
    cycle_id: "scenario:banking77-intent-classification",
    task_type: "airflow_dag_run",
    owner: "ml-platform",
    priority: "normal",
    resource_profile: "local-pipeline-workers",
    config_payload: { scenario_id: "banking77-intent-classification" },
    dry_run: false,
    task_id: "task-scenario-1",
    status,
    created_at: "2026-07-14T00:00:00Z",
    runtime_state: runtimeState,
    audit: []
  };
}


async function flushUpdates(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}
