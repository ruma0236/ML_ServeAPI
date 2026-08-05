import { spawn, spawnSync } from "node:child_process";
import { access, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";

import { chromium } from "@playwright/test";


const baseUrl = process.env.EVM_CONTROL_PANEL_DEMO_URL || "http://127.0.0.1:4173";
const apiUrl = process.env.EVM_CONTROL_PANEL_API_URL || "http://127.0.0.1:8000";
const mlflowUrl = process.env.EVM_MLFLOW_URL || "http://127.0.0.1:5000";
const outputRoot = process.env.EVM_CONTROL_PANEL_60FPS_ROOT ||
  "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/control-panel-editorial-refresh/2026-08-06/video/60fps";
const ffmpegPath = process.env.EVM_FFMPEG_PATH ||
  "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/control-panel-editorial-refresh/2026-08-06/video-tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe";
const rawRoot = path.join(outputRoot, "raw");
const finalRoot = path.join(outputRoot, "final");
const logRoot = path.join(outputRoot, "logs");
const rawPath = path.join(rawRoot, "control-panel-vlm-llm-60fps-capture.mkv");
const finalPath = path.join(finalRoot, "control-panel-vlm-llm-demo-60fps.mp4");
const seekProofPath = path.join(finalRoot, "seek-proof-00m30s.png");
const captureLogPath = path.join(logRoot, "ffmpeg-gdigrab.log");
const muxLogPath = path.join(logRoot, "ffmpeg-faststart-mux.log");
const manifestPath = path.join(outputRoot, "recording-manifest-60fps.json");

await access(ffmpegPath);
await Promise.all([
  mkdir(rawRoot, { recursive: true }),
  mkdir(finalRoot, { recursive: true }),
  mkdir(logRoot, { recursive: true })
]);

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
const [vlmMlflowRun, llmMlflowRun] = await Promise.all([
  fetch(`${mlflowUrl}/api/2.0/mlflow/runs/get?run_id=${encodeURIComponent(completedVlm.mlflow_run_id)}`).then(requireJson),
  fetch(`${mlflowUrl}/api/2.0/mlflow/runs/get?run_id=${encodeURIComponent(completedLlm.mlflow_run_id)}`).then(requireJson)
]);
const vlmMlflowRunUrl = `${mlflowUrl}/#/experiments/${vlmMlflowRun.run.info.experiment_id}/runs/${completedVlm.mlflow_run_id}`;
const llmMlflowRunUrl = `${mlflowUrl}/#/experiments/${llmMlflowRun.run.info.experiment_id}/runs/${completedLlm.mlflow_run_id}`;
const vlmMlflowRunName = mlflowRunName(vlmMlflowRun);
const llmMlflowRunName = mlflowRunName(llmMlflowRun);

const browserErrors = [];
const scenes = [];
const profileRoot = await mkdtemp(path.join(rawRoot, "chromium-profile-"));
const targetUrl = `${baseUrl}/?cycle=${encodeURIComponent(selectedCycle.cycle_id)}&view=workloads`;
const windowTitleFragment = `EVM 60FPS Demo ${Date.now()}`;
let context;
let captureProcess;

try {
  context = await chromium.launchPersistentContext(profileRoot, {
    headless: false,
    viewport: { width: 1440, height: 860 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
    args: [
      `--app=${targetUrl}`,
      "--window-position=0,0",
      "--window-size=1440,900",
      "--force-device-scale-factor=1",
      "--disable-gpu",
      "--disable-background-timer-throttling",
      "--disable-renderer-backgrounding"
    ]
  });
  const page = context.pages()[0] || await context.newPage();
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(`console:${message.text()}`);
  });
  page.on("pageerror", (error) => browserErrors.push(`page:${error.message}`));

  await page.goto(targetUrl, { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: "AI Workloads", exact: true }).waitFor();
  await page.evaluate((title) => { document.title = title; }, windowTitleFragment);
  await page.bringToFront();
  await page.waitForTimeout(1200);
  const windowTitle = await waitForWindowTitle(windowTitleFragment);
  const windowBounds = pinWindowTopmost(windowTitle);
  await page.waitForTimeout(500);
  const layout = await validatePageLayout(page);
  await ensureCompletedFilter(page);
  await completedRunRow(page, completedVlm).waitFor();

  await setOverlay(
    page,
    "Completed view에서 실제 완료된 VLM·LLM 실행만 transformer workload ledger로 확인합니다.",
    page.getByRole("heading", { name: "AI Workloads", exact: true }),
    "01 / 10"
  );
  const preflightPixels = await validateDesktopCapture();
  const capture = startCapture();
  captureProcess = capture.process;
  await capture.ready;
  const capturedAt = new Date().toISOString();
  const captureStartedMs = Date.now();

  await holdScene(page, "transformer-workload-ledger", 4200, captureStartedMs);

  await setOverlay(
    page,
    "SmolVLM과 Qwen의 완료 run을 선택해 model family별 lifecycle과 metric schema를 비교합니다.",
    page.locator(".scenario-kpis"),
    "02 / 10"
  );
  await holdScene(page, "workload-status-ledger", 2800, captureStartedMs);

  await clickScene(
    page,
    completedRunRow(page, completedVlm),
    "실제 CUDA LoRA로 실행한 SmolVLM-500M-Instruct run을 선택합니다.",
    "03 / 10",
    1000,
    captureStartedMs,
    "select-vlm"
  );
  await page.getByLabel("VLM evaluation metrics").waitFor();
  await setOverlay(
    page,
    "Data Intake부터 MLflow, Release Approval, Staging Serving, Inference 관측까지 10단계가 완료됐습니다.",
    page.locator(".scenario-stage-flow"),
    "03 / 10"
  );
  await holdScene(page, "smolvlm-stage-flow", 4800, captureStartedMs);
  await page.getByLabel("VLM evaluation metrics").scrollIntoViewIfNeeded();
  await setOverlay(
    page,
    "VLM은 Accuracy·Parse Rate 품질 지표와 latency·VRAM 운영 지표를 분리해 release gate에 연결합니다.",
    page.getByLabel("VLM evaluation metrics"),
    "04 / 10"
  );
  await holdScene(page, "smolvlm-metrics-and-gate", 5200, captureStartedMs);

  await centerInViewport(page.locator(".scenario-facts"));
  await setOverlay(
    page,
    "데이터 identity, RTX 4080 사용량, LoRA artifact와 해제된 GPU lease를 같은 run에 결합합니다.",
    page.locator(".scenario-facts"),
    "05 / 10"
  );
  await holdScene(page, "smolvlm-runtime-and-artifact", 3600, captureStartedMs);

  await centerInViewport(page.locator(".scenario-evidence-strip"));
  await setOverlay(
    page,
    "Source·model revision·evidence digest와 retired staging 상태가 재현 가능한 실행 경계를 만듭니다.",
    page.locator(".scenario-evidence-strip"),
    "05 / 10"
  );
  await holdScene(page, "smolvlm-identity-evidence", 3200, captureStartedMs);

  await clickScene(
    page,
    completedRunRow(page, completedLlm),
    "같은 control surface에서 실제 Qwen2.5-0.5B QLoRA run으로 전환합니다.",
    "07 / 10",
    900,
    captureStartedMs,
    "select-llm"
  );
  await page.getByLabel("LLM evaluation metrics").waitFor();
  await setOverlay(
    page,
    "동일한 10단계 lifecycle이 LLM 실행에도 적용되고 model family만 교체됩니다.",
    page.locator(".scenario-stage-flow"),
    "07 / 10"
  );
  await holdScene(page, "qwen-stage-flow", 4200, captureStartedMs);

  await page.getByLabel("LLM evaluation metrics").scrollIntoViewIfNeeded();
  await setOverlay(
    page,
    "LLM은 Validation Loss·Token F1·Non-empty Rate로 스키마가 바뀌며 공통 latency·VRAM은 유지됩니다.",
    page.getByLabel("LLM evaluation metrics"),
    "08 / 10"
  );
  await holdScene(page, "qwen-metrics-and-gate", 5200, captureStartedMs);

  await centerInViewport(page.locator(".scenario-evidence-strip"));
  await setOverlay(
    page,
    "Qwen도 dataset·revision·artifact digest와 retired staging 상태를 독립 evidence로 보존합니다.",
    page.locator(".scenario-evidence-strip"),
    "09 / 10"
  );
  await holdScene(page, "qwen-identity-evidence", 3400, captureStartedMs);

  await page.goto(vlmMlflowRunUrl, { waitUntil: "domcontentloaded" });
  await page.getByText(vlmMlflowRunName, { exact: true }).first().waitFor();
  await setOverlay(
    page,
    "SmolVLM 실행은 MLflow experiment 9의 FINISHED run과 실제 metric·parameter evidence로 추적됩니다.",
    page.locator("body"),
    "09 / 10"
  );
  await holdScene(page, "smolvlm-mlflow-run", 5200, captureStartedMs);

  await setOverlay(
    page,
    "동일한 MLflow tracking surface에서 Qwen QLoRA run으로 전환합니다.",
    page.locator("body"),
    "09 / 10"
  );
  await page.goto(llmMlflowRunUrl, { waitUntil: "domcontentloaded" });
  await page.getByText(llmMlflowRunName, { exact: true }).first().waitFor();
  await setOverlay(
    page,
    "Qwen 실행도 별도 MLflow experiment 10의 FINISHED run으로 metric과 QLoRA parameter를 추적합니다.",
    page.locator("body"),
    "09 / 10"
  );
  await holdScene(page, "qwen-mlflow-run", 5200, captureStartedMs);

  await setOverlay(
    page,
    "결론: SmolVLM과 Qwen은 실제 CUDA·MLflow·release gate·bounded staging 검증을 통과했고 production 승격은 주장하지 않습니다.",
    page.locator("body"),
    "10 / 10"
  );
  await holdScene(page, "transformer-validation-boundary", 6000, captureStartedMs);

  captureProcess.stdin.write("q\n");
  const captureResult = await capture.finished;
  await writeFile(captureLogPath, captureResult.stderr, "utf8");
  if (captureResult.code !== 0) {
    throw new Error(`60fps capture failed with code ${captureResult.code}`);
  }
  captureProcess = null;

  const muxResult = await runFfmpeg([
    "-y",
    "-i", rawPath,
    "-f", "lavfi",
    "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
    "-map", "0:v:0",
    "-map", "1:a:0",
    "-vf", "minterpolate=fps=60:mi_mode=blend",
    "-c:v", "h264_nvenc",
    "-preset", "p4",
    "-tune", "hq",
    "-rc", "vbr",
    "-cq", "18",
    "-b:v", "0",
    "-pix_fmt", "yuv420p",
    "-r", "60",
    "-fps_mode", "cfr",
    "-g", "120",
    "-c:a", "aac",
    "-b:a", "128k",
    "-shortest",
    "-movflags", "+faststart",
    finalPath
  ]);
  await writeFile(muxLogPath, muxResult.stderr, "utf8");
  if (muxResult.code !== 0) throw new Error(`Faststart mux failed with code ${muxResult.code}`);

  const seekResult = await runFfmpeg([
    "-y", "-ss", "00:00:30", "-i", finalPath,
    "-frames:v", "1", "-update", "1", seekProofPath
  ]);
  if (seekResult.code !== 0) throw new Error("30-second seek proof failed");
  const finalFramePixels = await validateFinalFrame();

  const inspectResult = spawnSync(ffmpegPath, ["-hide_banner", "-i", finalPath], {
    encoding: "utf8",
    windowsHide: true
  });
  const mediaInfo = `${inspectResult.stdout || ""}\n${inspectResult.stderr || ""}`;
  const captureStats = parseCaptureStats(captureResult.stderr);
  const atomBytes = await readFile(finalPath);
  const probeWindow = atomBytes.subarray(0, Math.min(atomBytes.length, 1024 * 1024));
  const moovOffset = probeWindow.indexOf(Buffer.from("moov"));
  const mdatOffset = probeWindow.indexOf(Buffer.from("mdat"));
  const sixtyFpsDeclared = /\b60 fps\b/.test(mediaInfo);
  const h264Declared = /Video: h264/.test(mediaInfo);
  const aacDeclared = /Audio: aac/.test(mediaInfo);
  if (!sixtyFpsDeclared || !h264Declared || !aacDeclared || moovOffset < 0 || mdatOffset < 0 || moovOffset > mdatOffset) {
    throw new Error("Final 60fps H.264/AAC faststart validation failed");
  }
  if (browserErrors.length) throw new Error(`Browser errors recorded: ${browserErrors.join(" | ")}`);

  await writeFile(manifestPath, JSON.stringify({
    schema_version: "evm.control_panel_editorial_demo_60fps.v1",
    recorded_at: capturedAt,
    base_url: baseUrl,
    cycle_id: selectedCycle.cycle_id,
    vlm_run_id: completedVlm.run_id,
    llm_run_id: completedLlm.run_id,
    capture: {
      method: "Windows gdigrab source capture with an HWND-pinned topmost Chromium app window; delivery cadence normalized with blend interpolation",
      requested_fps: 60,
      output_width: 1440,
      output_height: 900,
      window_title: windowTitle,
      window_bounds: windowBounds,
      page_layout: layout,
      preflight_pixels: preflightPixels,
      ...captureStats
    },
    validation: {
      declared_60_fps: sixtyFpsDeclared,
      h264: h264Declared,
      aac: aacDeclared,
      faststart: moovOffset < mdatOffset,
      moov_offset: moovOffset,
      mdat_offset: mdatOffset,
      seek_proof_seconds: 30,
      seek_proof_pixels: finalFramePixels,
      delivery_fps: 60,
      cadence_normalization: "minterpolate=fps=60:mi_mode=blend",
      browser_errors: browserErrors
    },
    scenes,
    raw_capture: rawPath,
    final_video: finalPath,
    seek_proof: seekProofPath,
    claim_boundary: "Read-only controlled-local SmolVLM and Qwen demonstration using completed real CUDA runs; source capture cadence is recorded separately and the delivery video is blend-interpolated to 60 fps; bounded staging validation only, with no new training, production promotion, HA or user-traffic claim."
  }, null, 2), "utf8");

  console.log(finalPath);
} finally {
  if (captureProcess && !captureProcess.killed) {
    captureProcess.stdin.write("q\n");
  }
  if (context) await context.close().catch(() => {});
  const resolvedProfile = path.resolve(profileRoot);
  const resolvedRawRoot = `${path.resolve(rawRoot)}${path.sep}`;
  if (resolvedProfile.startsWith(resolvedRawRoot)) {
    await rm(profileRoot, { recursive: true, force: true }).catch(() => {});
  }
}


function startCapture() {
  const args = [
    "-y",
    "-thread_queue_size", "1024",
    "-f", "gdigrab",
    "-framerate", "60",
    "-draw_mouse", "0",
    "-offset_x", "0",
    "-offset_y", "0",
    "-video_size", "1440x900",
    "-i", "desktop",
    "-an",
    "-c:v", "h264_nvenc",
    "-preset", "p4",
    "-tune", "hq",
    "-rc", "vbr",
    "-cq", "18",
    "-b:v", "0",
    "-pix_fmt", "yuv420p",
    "-fps_mode", "passthrough",
    "-g", "120",
    "-sc_threshold", "0",
    "-f", "matroska",
    rawPath
  ];
  const processHandle = spawn(ffmpegPath, args, {
    stdio: ["pipe", "ignore", "pipe"],
    windowsHide: true
  });
  let stderr = "";
  let readyResolve;
  let readyReject;
  const ready = new Promise((resolve, reject) => {
    readyResolve = resolve;
    readyReject = reject;
  });
  const timeout = setTimeout(() => readyReject(new Error("FFmpeg capture did not become ready")), 12_000);
  processHandle.stderr.on("data", (chunk) => {
    const text = chunk.toString();
    stderr += text;
    if (text.includes("Press [q]")) {
      clearTimeout(timeout);
      readyResolve();
    }
  });
  const finished = new Promise((resolve) => {
    processHandle.on("close", (code) => resolve({ code, stderr }));
  });
  processHandle.on("error", (error) => {
    clearTimeout(timeout);
    readyReject(error);
  });
  return { process: processHandle, ready, finished };
}


async function runFfmpeg(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(ffmpegPath, args, {
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}


async function waitForWindowTitle(fragment) {
  const escaped = fragment.replaceAll("'", "''");
  const command = [
    `$fragment='${escaped}'`,
    "$match=Get-Process | Where-Object { $_.MainWindowTitle -like \"*$fragment*\" } | Select-Object -First 1 -ExpandProperty MainWindowTitle",
    "if($match){$match}"
  ].join("; ");
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    const result = spawnSync("powershell.exe", ["-NoProfile", "-Command", command], {
      encoding: "utf8",
      windowsHide: true
    });
    const title = String(result.stdout || "").trim();
    if (title) return title;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Chromium window title not found for ${fragment}`);
}


function completedRunRow(page, run) {
  const modelName = run.identity.model_repository.split("/").at(-1);
  return page.locator(".scenario-run-list > button", { hasText: modelName }).filter({
    hasText: "completed"
  }).first();
}


function mlflowRunName(payload) {
  const runName = payload.run.data.tags.find((tag) => tag.key === "mlflow.runName")?.value;
  if (!runName) throw new Error("MLflow run name tag is required for visual verification.");
  return runName;
}


async function ensureCompletedFilter(page) {
  const completedFilter = page.getByRole("button", { name: "Show completed workloads", exact: true });
  await completedFilter.waitFor();
  if (await completedFilter.getAttribute("aria-pressed") !== "true") {
    await completedFilter.click();
  }
}


async function validatePageLayout(page) {
  const layout = await page.evaluate(() => {
    const shell = document.querySelector(".shell")?.getBoundingClientRect();
    return {
      inner_width: window.innerWidth,
      inner_height: window.innerHeight,
      device_pixel_ratio: window.devicePixelRatio,
      shell: shell ? {
        left: Math.round(shell.left),
        top: Math.round(shell.top),
        right: Math.round(shell.right),
        bottom: Math.round(shell.bottom),
        width: Math.round(shell.width),
        height: Math.round(shell.height)
      } : null
    };
  });
  if (
    layout.inner_width !== 1440 ||
    layout.inner_height !== 860 ||
    !layout.shell ||
    layout.shell.left < 0 ||
    layout.shell.right > layout.inner_width
  ) {
    throw new Error(`Control Panel layout is clipped or unexpected: ${JSON.stringify(layout)}`);
  }
  return layout;
}


async function centerInViewport(locator) {
  await locator.evaluate((element) => element.scrollIntoView({ block: "center", inline: "nearest" }));
}


async function clickScene(page, locator, caption, step, settleMs, captureStartedMs, sceneId) {
  await locator.scrollIntoViewIfNeeded();
  await setOverlay(page, caption, locator, step);
  await page.waitForTimeout(750);
  await locator.click();
  await holdScene(page, sceneId, settleMs, captureStartedMs);
}


async function holdScene(page, sceneId, durationMs, captureStartedMs) {
  scenes.push({
    scene_id: sceneId,
    started_ms: Date.now() - captureStartedMs,
    duration_ms: durationMs
  });
  await page.waitForTimeout(durationMs);
}


async function setOverlay(page, caption, locator, step) {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  const x = box ? Math.round(box.x + Math.min(box.width * 0.72, box.width - 20)) : 1080;
  const y = box ? Math.round(box.y + Math.min(box.height * 0.5, box.height - 18)) : 540;
  await page.evaluate(({ captionText, pointerX, pointerY, stepText }) => {
    let style = document.querySelector("#evm-demo-60-style");
    if (!style) {
      style = document.createElement("style");
      style.id = "evm-demo-60-style";
      style.textContent = `
        #evm-demo-60-caption { position: fixed; z-index: 2147483646; left: 50%; bottom: 20px; transform: translateX(-50%); max-width: min(840px, calc(100vw - 48px)); padding: 10px 16px; border: 1px solid rgba(182,255,59,.42); border-radius: 7px; color: #f7f8f4; background: rgba(5,6,5,.91); box-shadow: 0 10px 34px rgba(0,0,0,.42); font: 600 14px/1.45 system-ui, sans-serif; text-align: center; pointer-events: none; }
        #evm-demo-60-pointer { position: fixed; z-index: 2147483647; width: 16px; height: 16px; border: 2px solid #fff; border-radius: 50%; background: #b6ff3b; box-shadow: 0 0 0 5px rgba(182,255,59,.20), 0 0 18px rgba(182,255,59,.86); pointer-events: none; transition: left .22s ease, top .22s ease; }
        #evm-demo-60-step { position: fixed; z-index: 2147483646; top: 18px; right: 18px; padding: 6px 9px; border: 1px solid rgba(182,255,59,.30); border-radius: 6px; color: #b6ff3b; background: rgba(5,6,5,.86); font: 700 11px/1 system-ui, sans-serif; pointer-events: none; }
      `;
      document.head.appendChild(style);
    }
    let captionElement = document.querySelector("#evm-demo-60-caption");
    if (!captionElement) {
      captionElement = document.createElement("div");
      captionElement.id = "evm-demo-60-caption";
      document.body.appendChild(captionElement);
    }
    let pointer = document.querySelector("#evm-demo-60-pointer");
    if (!pointer) {
      pointer = document.createElement("div");
      pointer.id = "evm-demo-60-pointer";
      document.body.appendChild(pointer);
    }
    let stepElement = document.querySelector("#evm-demo-60-step");
    if (!stepElement) {
      stepElement = document.createElement("div");
      stepElement.id = "evm-demo-60-step";
      document.body.appendChild(stepElement);
    }
    captionElement.textContent = captionText;
    stepElement.textContent = stepText;
    pointer.style.left = `${Math.max(6, Math.min(innerWidth - 24, pointerX - 8))}px`;
    pointer.style.top = `${Math.max(6, Math.min(innerHeight - 24, pointerY - 8))}px`;
  }, { captionText: caption, pointerX: x, pointerY: y, stepText: step });
  await page.mouse.move(x, y);
}


function parseCaptureStats(log) {
  const progress = log.split(/[\r\n]+/).filter((line) => line.includes("frame=") && line.includes("time="));
  const last = progress.at(-1) || "";
  const frameMatch = last.match(/frame=\s*(\d+)/);
  const timeMatch = last.match(/time=(\d{2}:\d{2}:\d{2}(?:\.\d+)?)/);
  if (!frameMatch || !timeMatch) {
    return { encoded_frames: null, encoded_duration_seconds: null, measured_fps: null };
  }
  const durationSeconds = durationToSeconds(timeMatch[1]);
  const frames = Number(frameMatch[1]);
  const duplicateMatch = last.match(/dup=\s*(\d+)/);
  const dropMatch = last.match(/drop=\s*(\d+)/);
  return {
    encoded_frames: frames,
    encoded_duration_seconds: durationSeconds,
    measured_fps: durationSeconds > 0 ? Number((frames / durationSeconds).toFixed(3)) : null,
    duplicated_frames: duplicateMatch ? Number(duplicateMatch[1]) : 0,
    dropped_frames: dropMatch ? Number(dropMatch[1]) : 0
  };
}


async function validateDesktopCapture() {
  const probePath = path.join(rawRoot, "desktop-preflight.rgb");
  const result = await runFfmpeg([
    "-y",
    "-f", "gdigrab",
    "-framerate", "1",
    "-draw_mouse", "0",
    "-offset_x", "0",
    "-offset_y", "0",
    "-video_size", "1440x900",
    "-i", "desktop",
    "-frames:v", "1",
    "-pix_fmt", "rgb24",
    "-f", "rawvideo",
    probePath
  ]);
  if (result.code !== 0) throw new Error("Pinned desktop capture preflight failed");
  const stats = pixelStats(await readFile(probePath));
  await rm(probePath, { force: true });
  assertVisiblePixels(stats, "Pinned desktop capture preflight");
  return stats;
}


function pinWindowTopmost(windowTitle) {
  const escaped = windowTitle.replaceAll("'", "''");
  const script = `
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class EvmWindow {
  [StructLayout(LayoutKind.Sequential)]
  public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
  [DllImport("user32.dll", SetLastError=true)] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
}
'@
$target = Get-Process | Where-Object { $_.MainWindowTitle -eq '${escaped}' } | Select-Object -First 1
if (-not $target) { throw 'target_window_not_found' }
$handle = $target.MainWindowHandle
[void][EvmWindow]::ShowWindowAsync($handle, 9)
[void][EvmWindow]::SetWindowPos($handle, [IntPtr](-1), 0, 0, 1440, 900, 0x0040)
Start-Sleep -Milliseconds 300
$rect = New-Object EvmWindow+RECT
[void][EvmWindow]::GetWindowRect($handle, [ref]$rect)
[PSCustomObject]@{ handle = $handle.ToInt64(); left = $rect.Left; top = $rect.Top; width = $rect.Right - $rect.Left; height = $rect.Bottom - $rect.Top } | ConvertTo-Json -Compress
`;
  const encoded = Buffer.from(script, "utf16le").toString("base64");
  const result = spawnSync("powershell.exe", ["-NoProfile", "-EncodedCommand", encoded], {
    encoding: "utf8",
    windowsHide: true
  });
  if (result.status !== 0) {
    throw new Error(`Failed to pin Chromium window: ${result.stderr || result.stdout}`);
  }
  const bounds = JSON.parse(String(result.stdout || "{}").trim());
  if (bounds.left !== 0 || bounds.top !== 0 || bounds.width < 1400 || bounds.height < 860) {
    throw new Error(`Pinned Chromium bounds are unsafe: ${JSON.stringify(bounds)}`);
  }
  return bounds;
}


async function validateFinalFrame() {
  const probePath = path.join(rawRoot, "final-30s-proof.rgb");
  const result = await runFfmpeg([
    "-y",
    "-ss", "00:00:30",
    "-i", finalPath,
    "-frames:v", "1",
    "-pix_fmt", "rgb24",
    "-f", "rawvideo",
    probePath
  ]);
  if (result.code !== 0) throw new Error("Final frame pixel proof failed");
  const stats = pixelStats(await readFile(probePath));
  await rm(probePath, { force: true });
  assertVisiblePixels(stats, "Final frame pixel proof");
  return stats;
}


function pixelStats(bytes) {
  const stride = 97;
  let count = 0;
  let sum = 0;
  let sumSquares = 0;
  let minimum = 255;
  let maximum = 0;
  let bright = 0;
  for (let index = 0; index < bytes.length; index += stride) {
    const value = bytes[index];
    count += 1;
    sum += value;
    sumSquares += value * value;
    minimum = Math.min(minimum, value);
    maximum = Math.max(maximum, value);
    if (value > 32) bright += 1;
  }
  const mean = count ? sum / count : 0;
  const variance = count ? Math.max(0, sumSquares / count - mean * mean) : 0;
  return {
    sampled_channels: count,
    minimum,
    maximum,
    mean: Number(mean.toFixed(3)),
    standard_deviation: Number(Math.sqrt(variance).toFixed(3)),
    bright_ratio: count ? Number((bright / count).toFixed(6)) : 0
  };
}


function assertVisiblePixels(stats, label) {
  if (stats.maximum < 48 || stats.standard_deviation < 3 || stats.bright_ratio < 0.002) {
    throw new Error(`${label} is blank or visually degenerate: ${JSON.stringify(stats)}`);
  }
}


function durationToSeconds(value) {
  const [hours, minutes, seconds] = value.split(":");
  return Number(hours) * 3600 + Number(minutes) * 60 + Number(seconds);
}


async function requireJson(response) {
  if (!response.ok) throw new Error(`${response.url} returned ${response.status}`);
  return response.json();
}
