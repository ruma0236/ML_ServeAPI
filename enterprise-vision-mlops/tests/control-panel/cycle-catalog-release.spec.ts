import { expect, test } from "@playwright/test";

test("@w7-control-plane selects CycleRuns and exposes the release cockpit", async ({ page, request }) => {
  const response = await request.get("/control-panel/v1/cycles?limit=100");
  expect(response.ok()).toBeTruthy();
  const catalog = (await response.json()) as {
    latest_cycle_id: string;
    total: number;
    cycles: Array<{ cycle_id: string; live: boolean }>;
  };
  expect(catalog.total).toBeGreaterThan(0);

  await page.goto("/");
  const selector = page.getByLabel("CycleRun selector").getByRole("combobox");
  await expect(selector).toHaveValue(catalog.latest_cycle_id);
  await expect(selector.locator("option")).toHaveCount(catalog.total);

  if (catalog.cycles.length > 1) {
    const historical = catalog.cycles.find((cycle) => !cycle.live) || catalog.cycles[1];
    await selector.selectOption(historical.cycle_id);
    await expect(page.locator(".footer-line")).toContainText(historical.cycle_id);
    await selector.selectOption(catalog.latest_cycle_id);
    await expect(page.locator(".footer-line")).toContainText(catalog.latest_cycle_id);
  }

  await page.getByRole("button", { name: "Release" }).click();
  await expect(page.getByRole("heading", { name: "Release Control" })).toBeVisible();
  await expect(page.getByLabel("Release pipeline stages").locator("article")).toHaveCount(7);
  await expect(page.locator(".release-outcome")).toContainText("eligible");
  await expect(
    page.getByLabel("Release pipeline stages").locator("article").filter({ hasText: "Production Monitoring" })
  ).toContainText("p95");
  await expect(page.getByRole("heading", { name: "Deployment Intent" })).toBeVisible();
  await expect(page.getByLabel("Approver")).toHaveValue("release-manager");
  await expect(
    page.getByRole("textbox", { name: "Reason", exact: true })
  ).toHaveValue("Promote verified efficientnet-b0-visa-anomaly candidate");
  await expect(page.getByRole("link", { name: /Grafana/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /MLflow/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /Prometheus/ })).toBeVisible();
});
