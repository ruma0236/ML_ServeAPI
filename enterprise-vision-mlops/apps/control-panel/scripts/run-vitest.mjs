import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(scriptDir, "..");
const repoRoot = path.resolve(appRoot, "..", "..");
const vitestEntry = path.join(appRoot, "node_modules", "vitest", "vitest.mjs");

const result = spawnSync(
  process.execPath,
  [
    vitestEntry,
    "run",
    "tests/control-panel/cycle-overview.contract.test.ts",
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
