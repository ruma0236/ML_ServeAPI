import { expect, test } from "@playwright/test";

test("renders live CycleRun overview and captures evidence", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Control Panel" })).toBeVisible();
  await expect(page.getByText("vision-baseline v10")).toBeVisible();
  await expect(page.getByText("visa-open-data-f1f1c9ee9922", { exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Control Panel views" })).toBeVisible();

  await page.getByRole("button", { name: "Readiness" }).click();
  await expect(page.getByRole("heading", { name: "Real-Test Policy" })).toBeVisible();
  await expect(page.getByText(/efficientnet-b0/).first()).toBeVisible();

  await page.getByRole("button", { name: "Timeline" }).click();
  await expect(page.getByRole("heading", { name: "Pipeline Timeline" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Resource Topology" })).toBeVisible();
  await page.waitForTimeout(1000);

  const screenshotPath =
    process.env.EVM_CONTROL_PANEL_SCREENSHOT ||
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel_ui/evm-225-cycle-overview.png";
  await page.screenshot({ path: screenshotPath, fullPage: true });
});
