import { mkdir, copyFile, writeFile } from "node:fs/promises";
import path from "node:path";

import { chromium } from "@playwright/test";


const baseUrl = process.env.EVM_CONTROL_PANEL_DEMO_URL || "http://127.0.0.1:4173";
const apiUrl = process.env.EVM_CONTROL_PANEL_API_URL || "http://127.0.0.1:8000";
const outputRoot = process.env.EVM_CONTROL_PANEL_DEMO_ROOT ||
  "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/control-panel-editorial-refresh/2026-08-06/video";
const rawRoot = path.join(outputRoot, "raw");
const screenshotRoot = process.env.EVM_CONTROL_PANEL_SCREENSHOT_ROOT ||
  path.resolve(outputRoot, "../after/screenshots");

await mkdir(rawRoot, { recursive: true });
await mkdir(screenshotRoot, { recursive: true });

const [catalog, workloadLedger] = await Promise.all([
  fetch(`${apiUrl}/control-panel/v1/cycles?limit=100`).then(requireJson),
  fetch(`${apiUrl}/control-panel/v1/scenario-workloads`).then(requireJson)
]);
const selectedCycle = catalog.cycles.find((cycle) => cycle.status === "pass" && !cycle.live);
const completedVlm = workloadLedger.runs.find(
  (run) => run.state === "completed" && run.identity.model_family === "vlm"
);
const completedLlm = workloadLedger.runs.find(
  (run) => run.state === "completed" && run.identity.model_family === "llm"
);
if (!selectedCycle || !completedVlm || !completedLlm) {
  throw new Error("A passing CycleRun and completed VLM/LLM evidence are required.");
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  colorScheme: "dark",
  recordVideo: { dir: rawRoot, size: { width: 1440, height: 900 } }
});
const page = await context.newPage();
const video = page.video();
const browserErrors = [];
const scenes = [];
page.on("console", (message) => {
  if (message.type() === "error") browserErrors.push(`console:${message.text()}`);
});
page.on("pageerror", (error) => browserErrors.push(`page:${error.message}`));

const cycleQuery = encodeURIComponent(selectedCycle.cycle_id);
await page.goto(`${baseUrl}/?cycle=${cycleQuery}`, { waitUntil: "networkidle" });
await page.getByRole("heading", { name: "Live Operations" }).waitFor();
await scene(
  "먼저 실행 중인 파이프라인, 배포 모델, CPU/GPU 사용률을 한 화면에서 확인합니다.",
  1120,
  570,
  7000,
  "overview"
);

await page.getByRole("button", { name: "Build", exact: true }).click();
await page.getByRole("button", { name: "AI Workloads", exact: true }).click();
await page.getByRole("heading", { name: "AI Workloads", exact: true }).waitFor();
await scene(
  "AI Workloads는 intake부터 staging 관측까지 실제 transformer 실행 이력을 3초마다 동기화합니다.",
  760,
  285,
  6500,
  "workload-ledger"
);

await page.getByRole("button", { name: "Show completed workloads", exact: true }).click();
await scene(
  "Completed 필터로 실패 RCA는 보존하면서 승인된 실행만 빠르게 비교합니다.",
  865,
  355,
  5500,
  "completed-filter"
);

await selectRun(page, completedVlm.run_id);
await scene(
  "SmolVLM은 ScienceQA view 32/8/8과 RTX 4080 CUDA LoRA로 10단계를 완료했습니다.",
  1010,
  540,
  7500,
  "vlm-lifecycle"
);
await scene(
  "VLM 품질은 Accuracy와 Parse rate, 운영 지표는 latency, VRAM, 학습 시간으로 분리합니다.",
  1040,
  765,
  8500,
  "vlm-metrics"
);

await selectRun(page, completedLlm.run_id);
await scene(
  "LLM을 선택하면 같은 UI가 Validation loss, Token F1, Non-empty rate 스키마로 전환됩니다.",
  1030,
  765,
  8500,
  "llm-metrics"
);

await page.locator(".scenario-evidence-strip").scrollIntoViewIfNeeded();
await scene(
  "모델·데이터·source revision과 artifact digest를 고정하고, bounded staging은 검증 후 retire합니다.",
  1080,
  805,
  6500,
  "identity-and-staging"
);

await page.getByRole("button", { name: "Release / Deploy: Gate & promote", exact: true }).click();
await page.getByRole("heading", { name: "Deployed Models", exact: true }).waitFor();
await scene(
  "Release inventory는 1/1 production target과 scaled-down staging 대상을 명확히 분리합니다.",
  1050,
  430,
  7500,
  "release-inventory"
);

await page.getByRole("button", { name: "Observe: Runtime & decisions", exact: true }).click();
await page.getByRole("heading", { name: "Live Operations", exact: true }).waitFor();
await scene(
  "마지막으로 실제 host telemetry와 pipeline 상태가 다시 Overview에 수렴하는지 확인합니다.",
  1120,
  570,
  7500,
  "observe"
);

await context.close();
const recordedPath = await video.path();
const mobileEvidence = await captureMobileEvidence(browser, selectedCycle.cycle_id);
await browser.close();
const stableRawPath = path.join(rawRoot, "control-panel-vlm-llm-demo.webm");
await copyFile(recordedPath, stableRawPath);
await writeFile(
  path.join(outputRoot, "recording-manifest.json"),
  JSON.stringify({
    schema_version: "evm.control_panel_editorial_demo.v1",
    recorded_at: new Date().toISOString(),
    base_url: baseUrl,
    cycle_id: selectedCycle.cycle_id,
    vlm_run_id: completedVlm.run_id,
    llm_run_id: completedLlm.run_id,
    vlm_evaluation: completedVlm.evaluation_summary,
    llm_evaluation: completedLlm.evaluation_summary,
    scenes,
    mobile_evidence: mobileEvidence,
    browser_errors: browserErrors,
    raw_video: stableRawPath,
    overlays: "Recording-only captions and static laser pointer; application data is unchanged."
  }, null, 2),
  "utf8"
);
if (browserErrors.length) {
  throw new Error(`Browser errors recorded: ${browserErrors.join(" | ")}`);
}
console.log(stableRawPath);


async function captureMobileEvidence(activeBrowser, cycleId) {
  const mobileContext = await activeBrowser.newContext({
    viewport: { width: 390, height: 844 },
    colorScheme: "dark"
  });
  const mobilePage = await mobileContext.newPage();
  const errors = [];
  const captures = [];
  mobilePage.on("console", (message) => {
    if (message.type() === "error") errors.push(`console:${message.text()}`);
  });
  mobilePage.on("pageerror", (error) => errors.push(`page:${error.message}`));

  await mobilePage.goto(`${baseUrl}/?cycle=${encodeURIComponent(cycleId)}`, { waitUntil: "networkidle" });
  await mobilePage.getByRole("heading", { name: "Live Operations" }).waitFor();
  captures.push(await saveMobileCapture(mobilePage, "overview", "mobile-overview-390x844.png"));

  await mobilePage.getByRole("button", { name: "Build", exact: true }).click();
  await mobilePage.getByRole("button", { name: "AI Workloads", exact: true }).click();
  await mobilePage.getByRole("heading", { name: "AI Workloads", exact: true }).waitFor();
  await mobilePage.getByRole("button", { name: "Show completed workloads", exact: true }).click();
  await selectRun(mobilePage, completedVlm.run_id);
  await mobilePage.locator(".scenario-evaluation").scrollIntoViewIfNeeded();
  captures.push(await saveMobileCapture(
    mobilePage,
    "vlm-metrics",
    "mobile-ai-workloads-vlm-success-390x844.png"
  ));

  await mobilePage.getByRole("button", { name: "Release / Deploy: Gate & promote", exact: true }).click();
  await mobilePage.getByRole("heading", { name: "Deployed Models", exact: true }).waitFor();
  await mobilePage.evaluate(() => scrollTo(0, 0));
  captures.push(await saveMobileCapture(mobilePage, "release", "mobile-models-390x844.png"));

  await mobileContext.close();
  if (errors.length) throw new Error(`Mobile browser errors recorded: ${errors.join(" | ")}`);
  return { viewport: { width: 390, height: 844 }, captures, browser_errors: errors };
}


async function saveMobileCapture(activePage, view, filename) {
  const layout = await activePage.evaluate(() => ({
    viewport_width: innerWidth,
    viewport_height: innerHeight,
    document_width: document.documentElement.scrollWidth
  }));
  if (layout.document_width > layout.viewport_width + 1) {
    throw new Error(`${view} overflows mobile viewport: ${JSON.stringify(layout)}`);
  }
  const outputPath = path.join(screenshotRoot, filename);
  await activePage.screenshot({ path: outputPath });
  return { view, output_path: outputPath, layout };
}


async function selectRun(activePage, runId) {
  const run = runId === completedVlm.run_id ? completedVlm : completedLlm;
  const modelName = run.identity.model_repository.split("/").at(-1);
  const row = activePage.locator(".scenario-run-list > button", { hasText: modelName }).filter({
    hasText: "completed"
  });
  try {
    await row.first().waitFor({ state: "visible", timeout: 10_000 });
  } catch {
    throw new Error(`Completed workload row not found for ${modelName}`);
  }
  await row.first().click();
  await activePage.locator(".scenario-workload-detail h2", { hasText: modelName }).waitFor();
}


async function scene(caption, x, y, durationMs, sceneId) {
  await page.evaluate(({ captionText, pointerX, pointerY }) => {
    let style = document.querySelector("#evm-demo-overlay-style");
    if (!style) {
      style = document.createElement("style");
      style.id = "evm-demo-overlay-style";
      style.textContent = `
        #evm-demo-caption { position: fixed; z-index: 2147483646; left: 50%; bottom: 22px; transform: translateX(-50%); max-width: min(760px, calc(100vw - 40px)); padding: 9px 14px; border: 1px solid rgba(182,255,59,.38); border-radius: 7px; color: #f5f6f2; background: rgba(5,6,5,.88); box-shadow: 0 8px 30px rgba(0,0,0,.34); font: 600 13px/1.45 system-ui, sans-serif; text-align: center; }
        #evm-demo-laser { position: fixed; z-index: 2147483647; width: 15px; height: 15px; border: 2px solid #fff; border-radius: 50%; background: #b6ff3b; box-shadow: 0 0 0 5px rgba(182,255,59,.20), 0 0 16px rgba(182,255,59,.78); pointer-events: none; }
      `;
      document.head.appendChild(style);
    }
    let captionElement = document.querySelector("#evm-demo-caption");
    if (!captionElement) {
      captionElement = document.createElement("div");
      captionElement.id = "evm-demo-caption";
      document.body.appendChild(captionElement);
    }
    let laser = document.querySelector("#evm-demo-laser");
    if (!laser) {
      laser = document.createElement("div");
      laser.id = "evm-demo-laser";
      document.body.appendChild(laser);
    }
    captionElement.textContent = captionText;
    laser.style.left = `${pointerX - 7}px`;
    laser.style.top = `${pointerY - 7}px`;
  }, { captionText: caption, pointerX: x, pointerY: y });
  await page.mouse.move(x, y);
  scenes.push({ scene_id: sceneId, caption, pointer: { x, y }, duration_ms: durationMs });
  await page.waitForTimeout(durationMs);
}


async function requireJson(response) {
  if (!response.ok) throw new Error(`${response.url} returned ${response.status}`);
  return response.json();
}
