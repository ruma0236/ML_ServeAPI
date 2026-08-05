import { expect, test } from "@playwright/test";

import { openControlPanelView } from "./helpers/navigation";


test("@pipeline-profile validates effective and unsupported tuning paths", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/");
  await openControlPanelView(page, "Configure");
  const studio = page.getByRole("region", { name: "Pipeline profile studio" });
  await expect(studio.getByRole("heading", { name: "Run Blueprint Studio" })).toBeVisible();
  const fullLifecycleCapability = studio.locator(".capability-list article").filter({ hasText: "One-click full lifecycle" });
  await expect(fullLifecycleCapability).toContainText("wired");
  await expect(studio.getByRole("button", { name: "Queue Full Lifecycle" })).toBeDisabled();

  await studio.getByRole("button", { name: "Training", exact: true }).click();
  await expect(studio.getByRole("button", { name: "Component Catalog" })).toHaveClass(/active/);
  await expect(studio.getByRole("button", { name: "EfficientNet-B0", exact: true })).toBeVisible();
  await expect(studio.getByRole("button", { name: "EfficientNet-B7", exact: true })).toBeVisible();
  await studio.getByRole("button", { name: "EfficientNet-B7", exact: true }).click();
  await expect(studio.getByLabel("Input Size")).toHaveValue("600");
  await studio.getByRole("button", { name: "EfficientNet-B0", exact: true }).click();
  await expect(studio.getByLabel("Input Size")).toHaveValue("224");

  await studio.getByRole("button", { name: "Intent", exact: true }).click();
  await studio.getByRole("button", { name: "Data Cycle", exact: true }).click();
  await expect(studio.locator(".capability-list article").filter({ hasText: "Airflow data-cycle launch" })).toContainText("wired");

  await studio.getByLabel("Profile").fill("ui-profile-contract");
  await studio.getByRole("button", { name: "Review", exact: true }).click();
  await studio.getByRole("button", { name: "Save Version", exact: true }).click();
  await expect(studio.getByText(/ui-profile-contract \/ v\d+/)).toBeVisible();
  await expect(studio.locator(".replay-verification")).toHaveAttribute("data-status", "ready");
  await expect(studio.getByText(/11\/11 identities sealed/)).toBeVisible();
  await expect(studio.getByRole("button", { name: "Queue Data Cycle" })).toBeEnabled();
  await studio.getByRole("button", { name: "Create Dry Run", exact: true }).click();
  await expect(studio.locator(".profile-task")).toContainText("dry run");

  await studio.getByRole("button", { name: "Data", exact: true }).click();
  await expect(studio.getByRole("button", { name: "Custom Manifest" })).toHaveClass(/active/);
  await expect(studio.getByLabel("Source Manifest URI")).not.toHaveValue("");
  await studio.getByText("Cross-validation", { exact: true }).click();
  await expect(studio.getByLabel("Cross-validation")).toBeChecked();
  await expect(
    studio.locator(".capability-list article").filter({ hasText: "Cross-validation executor" })
  ).toContainText("wired");
  await expect(studio.getByText("capability_not_wired:cross_validation_executor")).toHaveCount(0);
  await expect(studio.getByRole("button", { name: "Queue Data Cycle" })).toBeDisabled();
  await studio.getByRole("button", { name: "Review", exact: true }).click();
  await studio.getByRole("button", { name: "Save Version", exact: true }).click();
  await expect(studio.locator(".replay-verification")).toHaveAttribute("data-status", "ready");
  await expect(studio.getByRole("button", { name: "Queue Data Cycle" })).toBeEnabled();

  await studio.getByRole("button", { name: "Config Document", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Pipeline profile JSON document" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
});
