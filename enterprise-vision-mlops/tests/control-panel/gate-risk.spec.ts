import { expect, test } from "@playwright/test";

test("@w7-drift-cdct renders drift review and CD/CT promotion gate details", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Gates" }).click();

  await expect(page.getByRole("heading", { name: "Promotion Gate" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Drift Review" })).toBeVisible();
  await expect(page.getByText("Recommended Action")).toBeVisible();
  await expect(page.getByLabel("Drift action rail").getByText("Label Review")).toBeVisible();
  await expect(page.getByText("Reference")).toBeVisible();
  await expect(page.getByText("Current")).toBeVisible();
  await expect(page.getByRole("heading", { name: "CD/CT Gate" })).toBeVisible();
  await expect(page.getByText("Block Reason")).toBeVisible();
  await expect(page.getByLabel("CD/CT check matrix")).toContainText("model_evaluation");
  await expect(page.getByLabel("CD/CT check matrix")).toContainText("drift_review");
  await expect(page.getByLabel("CD/CT check matrix")).toContainText("promotion_gate");
  await expect(page.getByRole("heading", { name: "Deployment Intent" })).toBeVisible();
  await expect(page.getByLabel("Deployment admission signals")).toContainText("CI Evidence");
  await expect(page.getByLabel("Deployment admission signals")).toContainText("Environment Policy");

  const screenshotPath =
    process.env.EVM_DRIFT_CDCT_SCREENSHOT ||
    `${
      process.env.EVM_W7_DRIFT_CDCT_EVIDENCE_DIR ||
      "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/drift_cdct/latest"
    }/${testInfo.project.name}-gate-risk.png`;
  await page.screenshot({ path: screenshotPath, fullPage: true });
});
