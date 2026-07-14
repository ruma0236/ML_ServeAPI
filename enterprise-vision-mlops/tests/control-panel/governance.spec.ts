import { expect, test } from "@playwright/test";

import { openControlPanelView } from "./helpers/navigation";

test("@post-w7-governance renders the decision registry without mutating it", async ({ page }, testInfo) => {
  await page.goto("/");
  await openControlPanelView(page, "Governance");

  await expect(page.getByRole("heading", { name: "Decision Queue" })).toBeVisible();
  await page.getByText("New Decision", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Create Draft" })).toBeVisible();
  await expect(page.getByLabel("Runtime diagnostics")).toBeVisible();

  const evidenceDir =
    process.env.EVM_W7_GOVERNANCE_EVIDENCE_DIR ||
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/governance/control_panel/latest";
  await page.screenshot({
    path: `${evidenceDir}/${testInfo.project.name}-governance.png`,
    fullPage: true
  });
});
