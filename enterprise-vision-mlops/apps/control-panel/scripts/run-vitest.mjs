import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(scriptDir, "..");
const repoRoot = path.resolve(appRoot, "..", "..");
const vitestEntry = path.join(appRoot, "node_modules", "vitest", "vitest.mjs");
const requestedTests = process.argv.slice(2).filter((arg) => arg !== "--run");
const defaultTests = [
  "tests/control-panel/cycle-overview.contract.test.ts",
  "tests/control-panel/deployment-inventory.view.test.ts",
  "tests/control-panel/deployment-intent.contract.test.ts",
  "tests/control-panel/enterprise-readiness.contract.test.ts",
  "tests/control-panel/gate-risk.contract.test.ts",
  "tests/control-panel/guard-incident-timeline.view.test.tsx",
  "tests/control-panel/governance-diagnostics.contract.test.ts",
  "tests/control-panel/kubernetes-topology.contract.test.ts",
  "tests/control-panel/lifecycle-runs.contract.test.tsx",
  "tests/control-panel/lifecycle-runs.view.test.tsx",
  "tests/control-panel/live-sync.contract.test.tsx",
  "tests/control-panel/operations-overview.view.test.ts",
  "tests/control-panel/operations.contract.test.ts",
  "tests/control-panel/pipeline-profile.contract.test.ts",
  "tests/control-panel/pipeline-timeline.contract.test.ts",
  "tests/control-panel/scenario-workloads.view.test.tsx",
  "tests/control-panel/stage-workbench.view.test.tsx"
];

const result = spawnSync(
  process.execPath,
  [
    vitestEntry,
    "run",
    ...(requestedTests.length ? requestedTests : defaultTests),
    "--config",
    "apps/control-panel/vite.config.ts",
    "--dir",
    "."
  ],
  {
    cwd: repoRoot,
    stdio: "inherit"
  }
);

process.exit(result.status ?? 1);
