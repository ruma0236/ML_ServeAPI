import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(scriptDir, "..");
const playwrightCli = path.join(appRoot, "node_modules", "@playwright", "test", "cli.js");

const result = spawnSync(process.execPath, [playwrightCli, "test", "--config", "playwright.config.ts", ...process.argv.slice(2)], {
  cwd: appRoot,
  env: {
    ...process.env,
    NODE_PATH: path.join(appRoot, "node_modules")
  },
  stdio: "inherit"
});

process.exit(result.status ?? 1);
