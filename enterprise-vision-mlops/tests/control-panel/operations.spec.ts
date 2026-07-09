import { expect, test } from "@playwright/test";

test("@w7-operations creates guarded tasks and command intents", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Operate" }).click();

  await expect(page.getByRole("heading", { name: "Task Assignment And Command Control" })).toBeVisible();
  await expect(page.getByText("external-compose").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Task Authoring" })).toBeVisible();

  await page.getByRole("button", { name: "Dry-run" }).first().click();
  await expect(page.getByText("dry_run").first()).toBeVisible();

  await page.locator("label").filter({ hasText: "Approval" }).getByRole("combobox").selectOption("auto");
  await page.getByRole("button", { name: "Queue", exact: true }).click();
  await expect(page.getByText("queued").first()).toBeVisible();

  await page.getByRole("button", { name: "Dry-run" }).nth(1).click();
  await expect(page.getByText("command_intent_created").first()).toBeVisible();
  await page.getByRole("button", { name: "Confirm" }).first().click();
  await expect(page.getByText("pending_confirmation").first()).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).first().click();
  await expect(page.getByText("cancelled").first()).toBeVisible();

  const screenshotPath =
    process.env.EVM_OPERATIONS_SCREENSHOT ||
    `${
      process.env.EVM_W7_OPERATIONS_EVIDENCE_DIR ||
      "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/operations_ui/latest"
    }/${testInfo.project.name}-operations.png`;
  await page.screenshot({ path: screenshotPath, fullPage: true });
});
