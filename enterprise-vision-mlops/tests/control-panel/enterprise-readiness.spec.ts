import { expect, test } from "@playwright/test";

import { openControlPanelView } from "./helpers/navigation";

test("@w7-enterprise-readiness renders service scope, readiness gates, and theme controls", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Control Panel" })).toBeVisible();

  await openControlPanelView(page, "Readiness");
  await expect(page.getByText(/Ready for promotion|Review required/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Data Readiness" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Model Readiness" })).toBeVisible();

  await page.getByRole("button", { name: "Technical Evidence", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Enterprise Scope" })).toBeVisible();
  await expect(page.getByText("Owner Coverage")).toBeVisible();
  await expect(page.getByText("Promotion Decision")).toBeVisible();
  await expect(page.getByLabel("Owner coverage").getByText("data-platform")).toBeVisible();
  await expect(page.getByLabel("Owner coverage").getByText("ml-platform")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Data Pipeline Checklist" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Model Pipeline Checklist" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Artifact Evidence Decision" })).toBeVisible();
  await page.getByLabel("Artifact evidence evaluation").locator("summary").click();
  await expect(page.getByLabel("Artifact evidence evaluation").getByText("quality gate")).toBeVisible();
  await expect(page.getByLabel("Artifact evidence evaluation").getByText("kubernetes runtime")).toBeVisible();
  await expect(page.getByText("Owner approval")).toHaveCount(2);
  await expect(page.getByLabel("Enterprise readiness checklist").getByText("data-platform")).toBeVisible();
  await expect(page.getByLabel("Enterprise readiness checklist").getByText("ml-platform")).toBeVisible();

  await page.getByLabel("Target environment").selectOption("production");
  await expect(page.getByLabel("Target namespace")).toHaveValue("evm-production");
  await page.getByLabel("Enterprise service scope").getByText("Approver", { exact: true }).locator("..").getByRole("textbox").fill("release-manager-ui-test");
  await expect(page.getByLabel("Promotion policy decision").getByText("production", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Promotion policy decision").getByText("two-person-production-approval")).toBeVisible();
  await expect(page.getByLabel("Promotion policy checks").getByText("approval", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Release Decision", exact: true }).click();

  await expect(page.locator(":root")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "Toggle theme" }).click();
  await expect(page.locator(":root")).toHaveAttribute("data-theme", "light");
  await page.getByRole("button", { name: "Toggle theme" }).click();
  await expect(page.locator(":root")).toHaveAttribute("data-theme", "dark");

  const screenshotPath =
    process.env.EVM_ENTERPRISE_READINESS_SCREENSHOT ||
    `${
      process.env.EVM_W7_ENTERPRISE_READINESS_EVIDENCE_DIR ||
      "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/enterprise_readiness/latest"
    }/${testInfo.project.name}-enterprise-readiness.png`;
  await page.screenshot({ path: screenshotPath, fullPage: true });
});
