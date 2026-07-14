import { expect, test } from "@playwright/test";

import { openControlPanelView } from "./helpers/navigation";

test("@w7-operations creates guarded tasks and command intents", async ({ page }, testInfo) => {
  await page.goto("/");
  await openControlPanelView(page, "Operate");

  await expect(page.getByRole("heading", { name: "Task Assignment And Command Control" })).toBeVisible();
  await expect(page.getByText("external-compose").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Task Authoring" })).toBeVisible();

  await page.getByRole("button", { name: "Validate Task", exact: true }).click();
  await expect(page.getByText("dry run", { exact: true }).first()).toBeVisible();

  await page.locator("label").filter({ hasText: "Approval" }).getByRole("combobox").selectOption("manual");
  await page.getByRole("button", { name: "Queue Intent", exact: true }).click();
  await expect(page.getByText("pending confirmation", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Preview Command", exact: true }).click();
  const focusedCommand = page.locator('[data-focused-command="true"]');
  await expect(focusedCommand).toContainText("command_intent_created");
  await focusedCommand.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(focusedCommand).toContainText("pending confirmation");
  await focusedCommand.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(focusedCommand).toContainText("cancelled");

  const screenshotPath =
    process.env.EVM_OPERATIONS_SCREENSHOT ||
    `${
      process.env.EVM_W7_OPERATIONS_EVIDENCE_DIR ||
      "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/operations_ui/latest"
    }/${testInfo.project.name}-operations.png`;
  await page.screenshot({ path: screenshotPath, fullPage: true });
});
