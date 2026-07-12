import { expect, test } from "@playwright/test";


test("@pipeline-profile validates effective and unsupported tuning paths", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  const studio = page.getByRole("region", { name: "Pipeline profile studio" });
  await expect(studio.getByRole("heading", { name: "Pipeline Profile Studio" })).toBeVisible();
  await expect(studio.getByText("capability_not_wired:full_lifecycle_orchestrator")).toBeVisible();
  await expect(studio.getByRole("button", { name: "Queue Data Cycle" })).toBeDisabled();

  await studio.getByRole("button", { name: "Data Cycle", exact: true }).click();
  await expect(studio.getByText("Dry-run validation passed.")).toBeVisible();
  await expect(studio.getByText("capability_not_wired:full_lifecycle_orchestrator")).toHaveCount(0);

  await studio.getByLabel("Profile").fill("ui-profile-contract");
  await studio.getByRole("button", { name: "Save Version", exact: true }).click();
  await expect(studio.getByText(/ui-profile-contract \/ v\d+/)).toBeVisible();
  await expect(studio.getByRole("button", { name: "Queue Data Cycle" })).toBeEnabled();

  await studio.getByText("Cross-validation", { exact: true }).click();
  await expect(studio.getByLabel("Cross-validation")).toBeChecked();
  await expect(studio.getByText("capability_not_wired:cross_validation_executor")).toBeVisible();
  await expect(studio.getByRole("button", { name: "Queue Data Cycle" })).toBeDisabled();

  await studio.getByRole("button", { name: "Config Document", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Pipeline profile JSON document" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
});
