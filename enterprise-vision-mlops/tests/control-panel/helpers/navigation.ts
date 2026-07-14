import type { Page } from "@playwright/test";


export type ControlPanelView =
  | "Overview"
  | "Configure"
  | "Stages"
  | "Runs"
  | "Readiness"
  | "Timeline"
  | "Operate"
  | "Gates"
  | "Release"
  | "Governance";


const workspaceByView: Record<ControlPanelView, "Overview" | "Build" | "Deploy" | "Govern"> = {
  Overview: "Overview",
  Configure: "Build",
  Stages: "Build",
  Runs: "Overview",
  Readiness: "Deploy",
  Timeline: "Overview",
  Operate: "Build",
  Gates: "Deploy",
  Release: "Deploy",
  Governance: "Govern"
};


const labelByView: Record<ControlPanelView, string> = {
  Overview: "Operations",
  Configure: "Pipeline Studio",
  Stages: "Handoffs",
  Runs: "Runs",
  Readiness: "Readiness",
  Timeline: "Resources",
  Operate: "Runtime Tasks",
  Gates: "Quality & Drift",
  Release: "Models",
  Governance: "Decisions"
};


export function controlPanelViewLabel(view: ControlPanelView): string {
  return labelByView[view];
}


export async function openControlPanelView(page: Page, view: ControlPanelView): Promise<void> {
  await page.getByRole("button", { name: workspaceByView[view], exact: true }).click();
  await page.getByRole("button", { name: labelByView[view], exact: true }).click();
}
