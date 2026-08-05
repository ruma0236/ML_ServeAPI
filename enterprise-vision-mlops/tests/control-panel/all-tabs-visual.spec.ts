import { expect, test } from "@playwright/test";

import {
  controlPanelViewLabel,
  openControlPanelView,
  type ControlPanelView
} from "./helpers/navigation";

const tabs: ControlPanelView[] = ["Overview", "Configure", "Stages", "Runs", "Readiness", "Timeline", "Operate", "Gates", "Release", "Governance"];
const headingsByTab: Record<string, string> = {
  Overview: "Live Operations",
  Configure: "Run Blueprint Studio",
  Stages: "Stage Workbench",
  Runs: "Lifecycle Runs",
  Readiness: "Data Readiness",
  Timeline: "Pipeline Timeline",
  Operate: "Task Authoring",
  Gates: "Model Metric Gate",
  Release: "Deployed Models",
  Governance: "Decision Queue"
};

test("@w7-all-tabs-visual captures every Control Panel tab for the active viewport", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Control Panel" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Live Operations" })).toBeVisible();

  const evidenceDir =
    process.env.EVM_W7_ALL_TABS_EVIDENCE_DIR ||
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/all_tabs_visual/latest";

  for (const tab of tabs) {
    await openControlPanelView(page, tab);
    await expect(
      page.getByRole("button", { name: controlPanelViewLabel(tab), exact: true })
    ).toHaveClass(/active/);
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
    if (tab === "Stages") {
      await expect(page.locator(".candidate-matrix-row").first()).toBeVisible({ timeout: 20_000 });
      await expect(page.getByText(/promotion-ready \/ \d+ total$/)).not.toHaveText(/^0 /);
      await expect(page.getByRole("button", { name: "Promotion Ready", exact: true })).toHaveClass(/active/);
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
