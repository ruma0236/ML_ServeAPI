import { expect, test } from "@playwright/test";

import { openControlPanelView } from "./helpers/navigation";


test("@w8-stage-workbench selects an evidence-bound candidate for promotion", async ({ page }) => {
  await page.goto("/");
  await openControlPanelView(page, "Stages");
  await expect(page.getByRole("heading", { name: "Stage Workbench" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Stage Handoffs" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Model Candidate Matrix" })).toBeVisible();

  const promote = page.locator("button.primary-action:enabled", { hasText: "Promote" }).first();
  await expect(promote).toBeEnabled({ timeout: 20_000 });
  const selectionResponse = page.waitForResponse(
    (response) => response.url().includes("/model-candidates/")
      && response.url().endsWith("/select")
      && response.request().method() === "POST"
  );
  await promote.click();
  const response = await selectionResponse;
  expect(response.status()).toBe(202);
  const selection = await response.json() as { selection_id: string; cycle_id: string };

  await expect(page).toHaveURL(new RegExp(`candidate=${selection.selection_id}`));
  await expect(page).toHaveURL(new RegExp(`cycle=${selection.cycle_id}`));
  await expect(page.getByRole("heading", { name: "Release Control" })).toBeVisible();
  await expect(
    page.getByLabel("Deployment intent control").getByRole("textbox", { name: "Selection", exact: true })
  ).toHaveValue(selection.selection_id);
});
