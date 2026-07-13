import type { Page } from "@playwright/test";


export type ControlPanelView =
  | "Overview"
  | "Configure"
  | "Runs"
  | "Readiness"
  | "Timeline"
  | "Operate"
  | "Gates"
  | "Release"
  | "Governance";


const workspaceByView: Record<ControlPanelView, "Monitor" | "Build" | "Release" | "Govern"> = {
  Overview: "Monitor",
  Configure: "Build",
  Runs: "Monitor",
  Readiness: "Release",
  Timeline: "Monitor",
  Operate: "Build",
  Gates: "Release",
  Release: "Release",
  Governance: "Govern"
};


const labelByView: Record<ControlPanelView, string> = {
  Overview: "Command Center",
  Configure: "Blueprint",
  Runs: "Runs",
  Readiness: "Readiness",
  Timeline: "Pipeline",
  Operate: "Task Studio",
  Gates: "Quality & Drift",
  Release: "Promotion",
  Governance: "Audit"
};


export function controlPanelViewLabel(view: ControlPanelView): string {
  return labelByView[view];
}


export async function openControlPanelView(page: Page, view: ControlPanelView): Promise<void> {
  await page.getByRole("button", { name: workspaceByView[view], exact: true }).click();
  await page.getByRole("button", { name: labelByView[view], exact: true }).click();
}
