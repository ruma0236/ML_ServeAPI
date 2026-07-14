import { expect, test } from "@playwright/test";

import { openControlPanelView } from "./helpers/navigation";

test("renders fleet-wide live operations and captures evidence", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Control Panel" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Live Operations" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Control Panel workspaces" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Control Panel views" })).toBeVisible();
  await expect(page.getByLabel("Pipeline activity")).toBeVisible();
  await expect(page.getByLabel("Compute capacity")).toBeVisible();
  await expect(page.getByLabel("Deployment fleet")).toBeVisible();

  await openControlPanelView(page, "Readiness");
  await page.getByRole("button", { name: "Technical Evidence", exact: true }).click();
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
