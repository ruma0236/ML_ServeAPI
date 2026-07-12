import { defineConfig, devices } from "@playwright/test";

const configuredWorkers = Number.parseInt(
  process.env.EVM_CONTROL_PANEL_E2E_WORKERS || "1",
  10
);

export default defineConfig({
  testDir: "../../tests/control-panel",
  testMatch: /.*\.spec\.ts/,
  // Live API state and F-drive evidence paths are shared across scenarios.
  workers: configuredWorkers > 0 ? configuredWorkers : 1,
  outputDir:
    process.env.EVM_CONTROL_PANEL_TEST_OUTPUT ||
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel_ui/playwright",
  timeout: 30_000,
  expect: {
    timeout: 10_000
  },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:5174",
    trace: "retain-on-failure",
    video: "retain-on-failure",
    screenshot: "only-on-failure"
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5174",
    url: "http://127.0.0.1:5174",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } }
    },
    {
      name: "MobileChrome",
      use: { ...devices["Pixel 5"] }
    }
  ]
});
