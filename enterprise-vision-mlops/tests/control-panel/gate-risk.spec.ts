import { expect, test } from "@playwright/test";

import { openControlPanelView } from "./helpers/navigation";

test("@w7-drift-cdct renders drift review and CD/CT promotion gate details", async ({ page }, testInfo) => {
  await page.goto("/");
  await openControlPanelView(page, "Gates");

  await expect(page.getByRole("heading", { name: "Model Metric Gate" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Drift Review" })).toBeVisible();
  await expect(page.locator(".drift-workflow-rail .active")).toHaveText("closed");
  await expect(page.getByRole("button", { name: "Preview" })).toHaveCount(0);
  await expect(page.getByText("Recommended Action")).toBeVisible();
  await expect(page.getByText("measured drift remains within policy")).toBeVisible();
  await expect(page.getByLabel("Drift action rail").getByText("Label Review")).toBeVisible();
  await expect(page.getByText("Auto Retraining")).toBeVisible();
  await expect(page.getByText("disabled")).toBeVisible();
  await expect(page.getByText("Input Category JS")).toBeVisible();
  await expect(page.getByLabel("Confidence quantile comparison")).toContainText("Baseline");
  await expect(page.getByLabel("Triggered drift rules")).toContainText("no policy threshold exceeded");
  await expect(page.getByLabel("Drift action rail")).toContainText("Approval Pending");
  await expect(page.getByLabel("Drift action rail")).toContainText("No Auto Retrain");
  await expect(page.locator(".drift-detail-list dt").filter({ hasText: "Baseline" })).toBeVisible();
  await expect(page.locator(".drift-detail-list dt").filter({ hasText: "Current" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Release Admission" })).toBeVisible();
  await expect(page.getByText("Block Reason")).toBeVisible();
  await expect(page.getByLabel("CD/CT check matrix")).toContainText("model_evaluation");
  await expect(page.getByLabel("CD/CT check matrix")).toContainText("drift_review");
  await expect(page.getByLabel("CD/CT check matrix")).toContainText("promotion_gate");
  await expect(page.locator(".cdct-detail-list")).toContainText("Approved By");
  await expect(page.locator(".cdct-detail-list").getByText(/pending|[a-z0-9._-]+/).last()).toBeVisible();
  await openControlPanelView(page, "Release");
  await expect(page.getByRole("heading", { name: "Deployed Models" })).toBeVisible();
  await page.getByText("Selected release evidence", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "Release Control" })).toBeVisible();
  await page.getByText("Promotion workflow", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "Deployment Intent" })).toBeVisible();
  await expect(page.getByLabel("Deployment admission signals")).toContainText("CI Evidence");
  await expect(page.getByLabel("Deployment admission signals")).toContainText("Environment Policy");

  await page.getByLabel("Runtime diagnostics").locator("summary").first().click();
  await expect(page.getByLabel("Control Panel synchronization sources")).toContainText("Cycle Catalog");
  await expect(page.getByLabel("Blocked and warning reasons")).toContainText(/No blocked or warning diagnostics|blocked|warning|not production/);

  const screenshotPath =
    process.env.EVM_DRIFT_CDCT_SCREENSHOT ||
    `${
      process.env.EVM_W7_DRIFT_CDCT_EVIDENCE_DIR ||
      "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/drift_cdct/latest"
    }/${testInfo.project.name}-gate-risk.png`;
  await page.screenshot({ path: screenshotPath, fullPage: true });
});
