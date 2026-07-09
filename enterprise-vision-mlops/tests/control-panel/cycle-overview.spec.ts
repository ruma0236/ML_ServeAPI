import { expect, test } from "@playwright/test";

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
  await expect(page.getByRole("navigation", { name: "Control Panel views" })).toBeVisible();
  await expect(page.locator(".ring-sweep")).toBeVisible();

  const ringGeometry = await page.locator(".cycle-ring").evaluate((ring) => {
    const ringRect = ring.getBoundingClientRect();
    const ringCenterX = ringRect.left + ringRect.width / 2;
    const ringCenterY = ringRect.top + ringRect.height / 2;
    return Array.from(ring.querySelectorAll(".ring-node")).map((node) => {
      const rect = node.getBoundingClientRect();
      const nodeCenterX = rect.left + rect.width / 2;
      const nodeCenterY = rect.top + rect.height / 2;
      return {
        insideRing:
          rect.left >= ringRect.left &&
          rect.right <= ringRect.right &&
          rect.top >= ringRect.top &&
          rect.bottom <= ringRect.bottom,
        distanceFromCenter: Math.hypot(nodeCenterX - ringCenterX, nodeCenterY - ringCenterY)
      };
    });
  });
  expect(ringGeometry.length).toBeGreaterThan(0);
  for (const node of ringGeometry) {
    expect(node.insideRing).toBeTruthy();
    expect(node.distanceFromCenter).toBeGreaterThan(60);
    expect(node.distanceFromCenter).toBeLessThan(100);
  }

  await page.getByRole("button", { name: "Readiness" }).click();
  await expect(page.getByRole("heading", { name: "Real-Test Policy" })).toBeVisible();
  await expect(page.getByText(/efficientnet-b0/).first()).toBeVisible();

  await page.getByRole("button", { name: "Timeline" }).click();
  await expect(page.getByRole("heading", { name: "Pipeline Timeline" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Kubernetes Resource Topology" })).toBeVisible();
  await page.waitForTimeout(1000);

  const screenshotPath =
    process.env.EVM_CONTROL_PANEL_SCREENSHOT ||
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel_ui/evm-225-cycle-overview.png";
  await page.screenshot({ path: screenshotPath, fullPage: true });
});
