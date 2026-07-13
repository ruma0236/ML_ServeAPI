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


const workspaceByView: Record<ControlPanelView, "Observe" | "Design" | "Validate" | "Govern"> = {
  Overview: "Observe",
  Configure: "Design",
  Runs: "Observe",
  Readiness: "Validate",
  Timeline: "Observe",
  Operate: "Design",
  Gates: "Validate",
  Release: "Validate",
  Governance: "Govern"
};


export async function openControlPanelView(page: Page, view: ControlPanelView): Promise<void> {
  await page.getByRole("button", { name: workspaceByView[view], exact: true }).click();
  await page.getByRole("button", { name: view, exact: true }).click();
}
