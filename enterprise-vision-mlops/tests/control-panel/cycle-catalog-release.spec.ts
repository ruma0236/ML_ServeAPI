import { expect, test } from "@playwright/test";

test("@w7-control-plane selects CycleRuns and exposes the release cockpit", async ({ page, request }) => {
  const response = await request.get("/control-panel/v1/cycles?limit=100");
  expect(response.ok()).toBeTruthy();
  const catalog = (await response.json()) as {
    latest_cycle_id: string;
    total: number;
    cycles: Array<{ cycle_id: string; live: boolean }>;
  };
  const latestResponse = await request.get("/control-panel/v1/cycles/latest");
  expect(latestResponse.ok()).toBeTruthy();
  const latest = (await latestResponse.json()) as {
    environment?: { tier?: string };
    latest_deployment_intent?: { target_environment?: string; approver?: string };
    tenant?: { ops_owner?: string };
    model: { model_name: string };
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
  const targetEnvironment = latest.latest_deployment_intent?.target_environment || latest.environment?.tier || "unknown";
  await expect(page.locator(".release-outcome")).toContainText(`${targetEnvironment} target`);
  await expect(page.locator(".release-outcome")).toContainText(/verified|blocked|running|completed|failed/);
  await expect(
    page.getByLabel("Release pipeline stages").locator("article").filter({ hasText: /Monitoring/ })
  ).toContainText(/p95|Latency evidence unavailable|Not scheduled|up/);
  await expect(page.getByRole("heading", { name: "Deployment Intent" })).toBeVisible();
  await expect(page.getByLabel("Approver")).toHaveValue(
    latest.latest_deployment_intent?.approver || latest.tenant?.ops_owner || "ai-infra-sre"
  );
  await expect(
    page.getByRole("textbox", { name: "Reason", exact: true })
  ).toHaveValue(`Promote verified ${latest.model.model_name} candidate`);
  if (targetEnvironment !== "production") {
    await expect(page.getByRole("alert")).toContainText("deployment_target_not_production");
  }
  await expect(page.getByRole("link", { name: /Grafana/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /MLflow/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /Prometheus/ })).toBeVisible();
});
