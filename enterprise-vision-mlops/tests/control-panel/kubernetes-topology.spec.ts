import { expect, test } from "@playwright/test";

test("@w7-kubernetes-topology renders API-backed resource topology and drilldown", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Timeline" }).click();

  await expect(page.getByRole("heading", { name: "Kubernetes Resource Topology" })).toBeVisible();
  await expect(page.getByText("evm-platform").first()).toBeVisible();
  await expect(page.getByText("evm-pipelines").first()).toBeVisible();
  await expect(page.getByText("SERVICE").first()).toBeVisible();
  await expect(page.getByText("PERSISTENTVOLUMECLAIM").first()).toBeVisible();

  await page.getByRole("button", { name: /Deployment evm-api/ }).click();
  await expect(page.getByLabel("Resource detail")).toContainText("Dry-run Actions");
  await expect(page.getByLabel("Resource detail")).toContainText("restart_dry_run");
  await expect(page.getByLabel("Resource detail")).toContainText("Pressure");

  await page.getByRole("button", { name: /Job evm-efficientnet-training/ }).click();
  await expect(page.getByLabel("Resource detail")).toContainText("RTX 4080");
  await expect(page.getByLabel("Resource detail")).toContainText("rerun_dry_run");

  const screenshotPath =
    process.env.EVM_KUBERNETES_TOPOLOGY_SCREENSHOT ||
    `${
      process.env.EVM_W7_TOPOLOGY_TIMELINE_EVIDENCE_DIR ||
      "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/topology_timeline/latest"
    }/${testInfo.project.name}-kubernetes-topology.png`;
  await page.screenshot({ path: screenshotPath, fullPage: true });
});
