import { expect, test } from "@playwright/test";

import { openControlPanelView, type ControlPanelView } from "./helpers/navigation";

const tabs: ControlPanelView[] = ["Overview", "Configure", "Runs", "Readiness", "Timeline", "Operate", "Gates", "Release", "Governance"];
const headingsByTab: Record<string, string> = {
  Overview: "Lifecycle State",
  Configure: "Run Blueprint Studio",
  Runs: "Lifecycle Runs",
  Readiness: "Data Readiness",
  Timeline: "Pipeline Timeline",
  Operate: "Task Authoring",
  Gates: "Model Metric Gate",
  Release: "Release Control",
  Governance: "Decision Draft"
};

test("@w7-all-tabs-visual captures every Control Panel tab for the active viewport", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Control Panel" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Lifecycle State" })).toBeVisible();

  const evidenceDir =
    process.env.EVM_W7_ALL_TABS_EVIDENCE_DIR ||
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/all_tabs_visual/latest";

  for (const tab of tabs) {
    await openControlPanelView(page, tab);
    await expect(page.getByRole("button", { name: tab })).toHaveClass(/active/);
    await expect(page.getByRole("heading", { name: headingsByTab[tab] })).toBeVisible();
    if (tab === "Operate") {
      await expect(page.locator(".json-editor textarea")).not.toHaveValue("");
    }
    if (tab === "Runs") {
      await expect(page.getByText("Host Worker").locator("..").getByText("Online")).toBeVisible();
      await expect(
        page.getByRole("button", {
          name: /standard-b0-operator-validation \/ v1 .* Completed 100%/
        })
      ).toBeVisible();
    }

    const documentOverflow = await page.evaluate(
      () => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth
    );
    expect(documentOverflow, `${tab} must not overflow the viewport horizontally`).toBeLessThanOrEqual(1);

    await page.waitForTimeout(250);
    await page.screenshot({
      path: `${evidenceDir}/${testInfo.project.name}-${tab.toLowerCase()}.png`,
      fullPage: true
    });
  }
});
