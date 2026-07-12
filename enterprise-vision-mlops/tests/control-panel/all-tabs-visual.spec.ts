import { expect, test } from "@playwright/test";

const tabs = ["Overview", "Readiness", "Timeline", "Operate", "Gates", "Release", "Governance"];
const headingsByTab: Record<string, string> = {
  Overview: "Cycle State",
  Readiness: "Data Readiness",
  Timeline: "Pipeline Timeline",
  Operate: "Task Authoring",
  Gates: "Promotion Gate",
  Release: "Release Control",
  Governance: "Decision Draft"
};

test("@w7-all-tabs-visual captures every Control Panel tab for the active viewport", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Control Panel" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Cycle State" })).toBeVisible();

  const evidenceDir =
    process.env.EVM_W7_ALL_TABS_EVIDENCE_DIR ||
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/all_tabs_visual/latest";

  for (const tab of tabs) {
    await page.getByRole("button", { name: tab }).click();
    await expect(page.getByRole("button", { name: tab })).toHaveClass(/active/);
    await expect(page.getByRole("heading", { name: headingsByTab[tab] })).toBeVisible();
    if (tab === "Operate") {
      await expect(page.locator(".json-editor textarea")).not.toHaveValue("");
    }
    await page.waitForTimeout(250);
    await page.screenshot({
      path: `${evidenceDir}/${testInfo.project.name}-${tab.toLowerCase()}.png`,
      fullPage: true
    });
  }
});
