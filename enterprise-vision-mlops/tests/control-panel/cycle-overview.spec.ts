import { expect, test } from "@playwright/test";

import { openControlPanelView } from "./helpers/navigation";

test("renders live CycleRun overview and captures evidence", async ({ page }) => {
  const response = await page.request.get("/control-panel/v1/cycles/latest");
  expect(response.ok()).toBeTruthy();
  const cycle = (await response.json()) as {
    dataset: { version: string };
    model: { model_name: string; version: string };
  };
  const modelLabel = `${cycle.model.model_name} v${cycle.model.version}`;

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Control Panel" })).toBeVisible();
  await expect(page.getByText(modelLabel)).toBeVisible();
  await expect(page.getByText(cycle.dataset.version, { exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Control Panel workspaces" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Control Panel views" })).toBeVisible();
  await expect(page.getByRole("img", { name: /Current lifecycle status/ })).toBeVisible();
  await expect(page.locator(".lifecycle-flow-phase")).toHaveCount(6);

  await openControlPanelView(page, "Readiness");
  await expect(page.getByRole("heading", { name: "Real-Test Policy" })).toBeVisible();
  await expect(page.getByText(/efficientnet-b0/).first()).toBeVisible();

  await openControlPanelView(page, "Timeline");
  await expect(page.getByRole("heading", { name: "Pipeline Timeline" })).toBeVisible();
  await page.getByRole("button", { name: "Infrastructure", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Kubernetes Resource Topology" })).toBeVisible();
  await page.waitForTimeout(1000);

  const screenshotPath =
    process.env.EVM_CONTROL_PANEL_SCREENSHOT ||
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel_ui/evm-225-cycle-overview.png";
  await page.screenshot({ path: screenshotPath, fullPage: true });
});
