import { expect, test } from "@playwright/test";

test("@w7-pipeline-timeline renders stage timeline and intermediate-result drilldown", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Timeline" }).click();

  await expect(page.getByRole("heading", { name: "Pipeline Timeline" })).toBeVisible();
  await expect(page.getByLabel("Stage detail")).toContainText("Metrics");
  await expect(page.getByLabel("Stage detail")).toContainText("Artifacts");
  await expect(page.getByLabel("Stage detail")).toContainText("Resources");
  await expect(page.getByLabel("Stage detail")).toContainText("Sample Outputs");

  await page.locator(".timeline").getByRole("button", { name: /EfficientNet Real Test Matrix/ }).click();
  await expect(page.getByLabel("Stage detail")).toContainText("candidate_count");
  await expect(page.getByLabel("Stage detail")).toContainText("evm-efficientnet-training");
  await expect(page.getByLabel("Stage detail")).toContainText("closed");

  const screenshotPath =
    process.env.EVM_PIPELINE_TIMELINE_SCREENSHOT ||
    `${
      process.env.EVM_W7_TOPOLOGY_TIMELINE_EVIDENCE_DIR ||
      "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/topology_timeline/latest"
    }/${testInfo.project.name}-pipeline-timeline.png`;
  await page.screenshot({ path: screenshotPath, fullPage: true });
});
