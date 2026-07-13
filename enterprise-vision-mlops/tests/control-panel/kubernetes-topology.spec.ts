import { expect, test } from "@playwright/test";

import { openControlPanelView } from "./helpers/navigation";

test("@w7-kubernetes-topology renders API-backed resource topology and drilldown", async ({ page, request }, testInfo) => {
  const resourceResponse = await request.get("http://127.0.0.1:8000/control-panel/v1/resources");
  expect(resourceResponse.ok()).toBe(true);
  const resourcePayload = await resourceResponse.json();
  const trainingResource = resourcePayload.resources.find(
    (resource: { kind: string; name: string }) => resource.kind === "Job" && resource.name === "evm-b7-training"
  );
  expect(trainingResource).toBeTruthy();

  await page.goto("/");
  await openControlPanelView(page, "Timeline");
  await page.getByRole("button", { name: "Infrastructure", exact: true }).click();

  await expect(page.getByRole("heading", { name: "Kubernetes Resource Topology" })).toBeVisible();
  await expect(page.getByText("evm-training").first()).toBeVisible();
  await expect(page.getByText("SERVICE").first()).toBeVisible();
  await expect(page.getByText("PERSISTENTVOLUMECLAIM").first()).toBeVisible();
  await expect(page.locator(".observation-state")).toContainText("live");

  const trainingButton = page.getByRole("button", {
    name: `${trainingResource.kind} ${trainingResource.name} ${trainingResource.status}`,
    exact: true
  });
  await trainingButton.click();
  const resourceDetail = page.getByLabel("Resource detail");
  await expect(resourceDetail).toContainText("kubernetes_snapshot");
  await expect(resourceDetail).toContainText(trainingResource.reason);
  if (trainingResource.message) {
    await expect(resourceDetail).toContainText(trainingResource.message);
  }

  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(page.getByText("evm-platform").first()).toBeVisible();
  await expect(page.getByText("evm-pipelines").first()).toBeVisible();

  await page.getByRole("button", { name: /Deployment evm-api/ }).click();
  await expect(page.getByLabel("Resource detail")).toContainText("Dry-run Actions");
  await expect(page.getByLabel("Resource detail")).toContainText("restart_dry_run");
  await expect(page.getByLabel("Resource detail")).toContainText("Pressure");

  await page.getByRole("button", { name: /Job evm-efficientnet-training/ }).click();
  await expect(page.getByLabel("Resource detail")).toContainText("RTX 4080");
  await expect(page.getByLabel("Resource detail")).toContainText("rerun_dry_run");

  await trainingButton.click();

  const screenshotPath =
    process.env.EVM_KUBERNETES_TOPOLOGY_SCREENSHOT ||
    `${
      process.env.EVM_W7_TOPOLOGY_TIMELINE_EVIDENCE_DIR ||
      "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/topology_timeline/latest"
    }/${testInfo.project.name}-kubernetes-topology.png`;
  await page.screenshot({ path: screenshotPath, fullPage: true });
});
