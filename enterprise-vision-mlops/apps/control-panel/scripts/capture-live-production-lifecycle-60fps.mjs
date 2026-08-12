import { createHash } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import { access, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";

import { chromium } from "@playwright/test";


const baseUrl = process.env.EVM_CONTROL_PANEL_DEMO_URL || "http://127.0.0.1:4173";
const apiUrl = process.env.EVM_CONTROL_PANEL_API_URL || "http://127.0.0.1:8000";
const mlflowUrl = process.env.EVM_MLFLOW_URL || "http://127.0.0.1:5000";
const prometheusUrl = process.env.EVM_PROMETHEUS_URL || "http://127.0.0.1:9090";
const projectRoot = process.env.EVM_PROJECT_ROOT || process.cwd().replace(/[\\/]apps[\\/]control-panel$/, "");
const expectedExecutionCommit = process.env.EVM_EXPECTED_EXECUTION_COMMIT || gitRevision(projectRoot);
const expectedExecutionBranch = process.env.EVM_EXPECTED_EXECUTION_BRANCH || gitBranch(projectRoot);
const outputBase = process.env.EVM_LIVE_LIFECYCLE_VIDEO_ROOT ||
  "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/control-panel-live-production-lifecycle/2026-08-12";
const ffmpegPath = process.env.EVM_FFMPEG_PATH ||
  "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/control-panel-editorial-refresh/2026-08-06/video-tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe";
const presetId = "smolvlm-scienceqa-local-production";
const sessionId = `smolvlm-${timestampToken()}`;
const outputRoot = path.join(outputBase, "sessions", sessionId);
const rawRoot = path.join(outputRoot, "video", "raw");
const finalRoot = path.join(outputRoot, "video", "final");
const logRoot = path.join(outputRoot, "logs");
const evidenceRoot = path.join(outputRoot, "evidence");
const frameRoot = path.join(outputRoot, "screenshots");
const rawPath = path.join(rawRoot, "smolvlm-live-local-production-lifecycle-60fps.mkv");
const finalPath = path.join(finalRoot, "smolvlm-live-local-production-lifecycle-60fps.mp4");
const captureLogPath = path.join(logRoot, "ffmpeg-gdigrab.log");
const muxLogPath = path.join(logRoot, "ffmpeg-faststart-mux.log");
const eventLogPath = path.join(logRoot, "lifecycle-events.jsonl");
const manifestPath = path.join(outputRoot, "recording-manifest.json");
const failurePath = path.join(evidenceRoot, "capture-failure.json");
const seekProofPath = path.join(finalRoot, "seek-proof.png");
const forbiddenPresentationTerms = ["prototype", "프로토타입", "1차 결과물"];

await Promise.all([
  access(ffmpegPath),
  mkdir(rawRoot, { recursive: true }),
  mkdir(finalRoot, { recursive: true }),
  mkdir(logRoot, { recursive: true }),
  mkdir(evidenceRoot, { recursive: true }),
  mkdir(frameRoot, { recursive: true })
]);

const initialPreflight = await runtimeSnapshot({ includeB0Inference: true });
assertInitialPreflight(initialPreflight);
await writeJson(path.join(evidenceRoot, "runtime-preflight.json"), initialPreflight);
reportProgress("preflight-passed", {
  execution_revision: initialPreflight.control_plane.source_commit,
  execution_branch: initialPreflight.control_plane.source_branch,
  worker_revision: initialPreflight.worker.source_commit,
  gpu_allocatable: initialPreflight.gpu_allocatable,
  b0_ready: initialPreflight.b0.available,
  session_root: outputRoot
});

if (process.env.EVM_CAPTURE_PREFLIGHT_ONLY === "1") {
  console.log(JSON.stringify({ status: "pass", output_root: outputRoot, preflight: initialPreflight }, null, 2));
  process.exit(0);
}

const browserErrors = [];
const captions = [];
const scenes = [];
const lifecycleEvents = [];
const profileRoot = await mkdtemp(path.join(rawRoot, "chromium-profile-"));
const targetUrl = `${baseUrl}/?view=workloads`;
const windowTitleFragment = `SmolVLM Local Production Lifecycle ${Date.now()}`;
const requester = "ml-engineer";
const approver = "ai-platform-sre";
const reason = `Execute a fresh identity-bound SmolVLM lifecycle for local production evidence ${sessionId}`;
const approvalReason = "Approve the exact run after data, quality, artifact, runtime, and release evidence review";
let context;
let captureProcess;
let captureFinished;
let captureStartedMs = 0;
let runId = "";
let intentId = "";
let capturedAt = "";

try {
  context = await chromium.launchPersistentContext(profileRoot, {
    headless: false,
    viewport: { width: 1440, height: 860 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
    httpCredentials: { username: "admin", password: "admin" },
    args: [
      `--app=${targetUrl}`,
      "--window-position=0,0",
      "--window-size=1440,900",
      "--force-device-scale-factor=1",
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
  await page.waitForTimeout(700);
  const windowTitle = await waitForWindowTitle(windowTitleFragment);
  const windowBounds = pinWindowTopmost(windowTitle);
  const layout = await validatePageLayout(page);
  const preflightPixels = await validateDesktopCapture();

  await page.getByLabel("Workload Preset").selectOption(presetId);
  await page.getByLabel("Run Requester").fill(requester);
  await page.getByLabel("Run Reason").fill(reason);
  await page.getByLabel("Independent Approver").first().fill(approver);
  await page.getByLabel("Approval Reason").first().fill(approvalReason);

  const capture = startCapture();
  captureProcess = capture.process;
  captureFinished = capture.finished;
  await capture.ready;
  captureStartedMs = Date.now();
  capturedAt = new Date().toISOString();
  reportProgress("recording-started", { captured_at: capturedAt, raw_path: rawPath });

  await setOverlay(
    page,
    "ScienceQA 데이터 뷰와 SmolVLM 모델 revision을 선택합니다. 이 실행은 32/8/8개 레코드로 제한된 실제 CUDA 검증이며 벤치마크 성능 주장이 아닙니다.",
    page.locator(".scenario-command-center"),
    "01 / 09"
  );
  await page.screenshot({ path: path.join(frameRoot, "01-governed-vlm-preset.png") });
  await holdScene("governed-vlm-preset", 4500);

  const runsBefore = new Set(initialPreflight.runs.map((run) => run.run_id));
  const launchButton = page.getByRole("button", { name: "Launch real workload", exact: true });
  await waitForEnabled(launchButton, 30_000);
  await launchButton.click();
  const createdRun = await waitForNewWorkload(runsBefore, 60_000);
  runId = createdRun.run_id;
  assertRunRevision(createdRun);
  recordWorkloadEvent(createdRun, "ui-launch");
  await page.getByText(reason, { exact: true }).waitFor({ timeout: 30_000 });
  await setOverlay(
    page,
    `새 run ${shortId(runId)}이 생성됐습니다. 이후 데이터, 모델, 소스, 승인, 배포 증거는 모두 이 identity에 결합됩니다.`,
    page.locator(".scenario-workload-detail > header"),
    "02 / 09"
  );
  await holdScene("fresh-run-identity", 3500);

  const gpuButton = page.getByRole("button", { name: /Authorize GPU handoff/ });
  await waitForRunControl(gpuButton, runId, 60_000, "GPU handoff");
  await setOverlay(
    page,
    "단일 RTX 4080의 현재 소유자인 B0 Deployment를 정확한 UID로 확인한 뒤, 이 run에만 한 번 사용할 수 있는 GPU handoff를 승인합니다.",
    gpuButton,
    "03 / 09"
  );
  await holdScene("exact-gpu-handoff", 3500);
  await gpuButton.click();
  await waitForWorkload(
    runId,
    (run) => ["approved", "consumed"].includes(run.control_state?.gpu_handoff_state),
    60_000,
    "GPU handoff approval was not observed"
  );

  let stagingApproved = false;
  let lastStage = "";
  let lastTrainingStep = 0;
  const runDeadline = Date.now() + 2 * 60 * 60 * 1000;
  while (Date.now() < runDeadline) {
    const run = await fetchJson(`${apiUrl}/control-panel/v1/scenario-workloads/${encodeURIComponent(runId)}`);
    recordWorkloadEvent(run, "runtime-poll");
    assertRunRevision(run);
    if (["failed", "blocked", "cancelled"].includes(run.state)) {
      throw new Error(`SmolVLM lifecycle ${run.state}: ${(run.blockers || []).join(",") || run.current_stage}`);
    }

    if (run.current_stage && run.current_stage !== lastStage) {
      lastStage = run.current_stage;
      await refreshWorkloads(page);
      const locator = stageLocator(page, run.current_stage);
      await setOverlay(page, stageCaption(run.current_stage), locator, stageStep(run.current_stage));
      await holdScene(`stage-${run.current_stage}`, 1800);
      reportProgress("workload-stage", { run_id: runId, state: run.state, stage: run.current_stage });
    }

    const currentStep = Number(run.training_progress?.current_step || 0);
    if (currentStep > lastTrainingStep) {
      lastTrainingStep = currentStep;
      await refreshWorkloads(page);
      const telemetry = page.getByLabel("Live training progress");
      if (await telemetry.count()) {
        await setOverlay(
          page,
          `실제 LoRA 학습이 CUDA에서 ${currentStep}/${run.training_progress.max_steps} step까지 진행됐습니다. loss와 갱신 시각은 실행 artifact에서 읽습니다.`,
          telemetry,
          "05 / 09"
        );
        if (currentStep === 1 || currentStep === run.training_progress.max_steps || currentStep % 2 === 0) {
          await holdScene(`training-step-${currentStep}`, 900);
        }
      }
    }

    if (run.state === "waiting_approval" && run.current_stage === "approval" && !stagingApproved) {
      await refreshWorkloads(page);
      const stagingButton = page.getByRole("button", { name: /Approve staging/ });
      await waitForEnabled(stagingButton, 30_000);
      await setOverlay(
        page,
        "학습 artifact, MLflow run, VLM 평가 metric과 digest가 고정된 뒤 독립 승인자가 staging CUDA inference를 허용합니다.",
        page.locator(".scenario-action-rail"),
        "06 / 09"
      );
      await page.screenshot({ path: path.join(frameRoot, "02-staging-approval-gate.png") });
      await holdScene("staging-approval-gate", 4000);
      await stagingButton.click();
      stagingApproved = true;
    }

    if (run.state === "completed") break;
    await page.waitForTimeout(1000);
  }

  const completedRun = await fetchJson(`${apiUrl}/control-panel/v1/scenario-workloads/${encodeURIComponent(runId)}`);
  assertCompletedRun(completedRun);
  recordWorkloadEvent(completedRun, "workload-completed");
  await refreshWorkloads(page);
  const evaluation = page.getByLabel("VLM evaluation metrics");
  await evaluation.scrollIntoViewIfNeeded();
  await setOverlay(
    page,
    "VLM 품질 지표인 held-out accuracy와 parse rate를 공통 운영 지표인 latency, VRAM, 학습 시간과 분리해 release gate에 연결합니다.",
    evaluation,
    "07 / 09"
  );
  await page.screenshot({ path: path.join(frameRoot, "03-vlm-evaluation-release-evidence.png") });
  await holdScene("vlm-evaluation-release-evidence", 5000);

  const createIntentButton = page.getByRole("button", { name: /Create production intent/ });
  await waitForEnabled(createIntentButton, 30_000);
  await setOverlay(
    page,
    "완료 상태만으로 배포하지 않습니다. adapter, evaluation, evidence index와 동일 revision의 local CI 결과를 다시 검증해 production intent를 생성합니다.",
    createIntentButton,
    "08 / 09"
  );
  await holdScene("production-intent-admission", 4000);
  await createIntentButton.click();
  const pendingIntent = await waitForIntent(
    (intent) => intent.run_id === runId && intent.state === "pending_approval",
    60_000,
    "Production intent did not reach pending_approval"
  );
  intentId = pendingIntent.intent_id;
  recordIntentEvent(pendingIntent, "ui-intent-created");
  await refreshWorkloads(page);

  const productionButton = page.getByRole("button", { name: /Approve local production/ });
  await waitForEnabled(productionButton, 30_000);
  await setOverlay(
    page,
    "요청자와 다른 운영 승인자가 정확한 action digest를 승인합니다. worker는 이후에도 identity와 B0 GPU holder를 다시 검증합니다.",
    page.locator(".scenario-production-intent"),
    "08 / 09"
  );
  await holdScene("independent-production-approval", 4000);
  await productionButton.click();

  let lastIntentState = "";
  const deploymentDeadline = Date.now() + 10 * 60 * 1000;
  while (Date.now() < deploymentDeadline) {
    const intent = await fetchJson(
      `${apiUrl}/control-panel/v1/scenario-workloads/production-intents/${encodeURIComponent(intentId)}`
    );
    recordIntentEvent(intent, "deployment-poll");
    if (intent.state !== lastIntentState) {
      lastIntentState = intent.state;
      await refreshWorkloads(page);
      await setOverlay(
        page,
        productionCaption(intent.state),
        page.locator(".scenario-production-intent"),
        "09 / 09"
      );
      await holdScene(`production-${intent.state}`, 1800);
      reportProgress("production-intent", { intent_id: intentId, state: intent.state });
    }
    if (intent.state === "failed") {
      throw new Error(`Local production failed: ${(intent.blockers || []).join(",")}`);
    }
    if (intent.state === "applied") break;
    await page.waitForTimeout(1000);
  }

  const finalIntent = await fetchJson(
    `${apiUrl}/control-panel/v1/scenario-workloads/production-intents/${encodeURIComponent(intentId)}`
  );
  if (finalIntent.state !== "applied") throw new Error("Local production intent did not reach applied");
  const finalEvidence = await validateAppliedProduction(completedRun, finalIntent);
  await writeJson(path.join(evidenceRoot, "workload-run.json"), completedRun);
  await writeJson(path.join(evidenceRoot, "production-intent.json"), finalIntent);
  await writeJson(path.join(evidenceRoot, "production-ready.json"), finalEvidence.ready);
  await writeJson(path.join(evidenceRoot, "production-inference.json"), finalEvidence.inference);
  await writeJson(path.join(evidenceRoot, "prometheus-query.json"), finalEvidence.prometheus);
  await writeJson(path.join(evidenceRoot, "mlflow-run.json"), finalEvidence.mlflow);

  await refreshWorkloads(page);
  await page.locator(".scenario-production-banner").scrollIntoViewIfNeeded();
  await setOverlay(
    page,
    "동일한 SmolVLM adapter가 local-production에 적용됐습니다. UI 상태, /ready identity, 실제 CUDA inference와 Prometheus up이 모두 일치합니다.",
    page.locator(".scenario-production-banner"),
    "09 / 09"
  );
  await page.screenshot({ path: path.join(frameRoot, "04-local-production-applied.png") });
  await holdScene("local-production-applied", 5500);

  const experimentId = finalEvidence.mlflow.run.info.experiment_id;
  await page.goto(
    `${mlflowUrl}/#/experiments/${encodeURIComponent(experimentId)}/runs/${encodeURIComponent(completedRun.mlflow_run_id)}`,
    { waitUntil: "domcontentloaded" }
  );
  await page.waitForTimeout(3500);
  await setOverlay(
    page,
    `MLflow의 exact run ${shortId(completedRun.mlflow_run_id)}에서 실제 파라미터, metric과 adapter artifact를 독립적으로 추적합니다.`,
    page.locator("body"),
    "EVIDENCE"
  );
  await page.screenshot({ path: path.join(frameRoot, "05-mlflow-exact-run.png") });
  await holdScene("mlflow-exact-run", 5500);

  await page.goto(`${finalIntent.target.endpoint}/ready`, { waitUntil: "domcontentloaded" });
  await page.getByText(/SmolVLM-500M-Instruct/).waitFor({ timeout: 30_000 });
  await setOverlay(
    page,
    "서빙 readiness는 모델 family, base revision, adapter digest, data identity, source revision, run ID와 CUDA 상태를 함께 반환합니다.",
    page.locator("body"),
    "EVIDENCE"
  );
  await page.screenshot({ path: path.join(frameRoot, "06-production-ready-identity.png") });
  await holdScene("production-ready-identity", 5000);

  await page.goto(`${prometheusUrl}/targets?search=${encodeURIComponent(runId)}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3500);
  await setOverlay(
    page,
    "Prometheus가 방금 적용한 run ID와 local-production 환경의 /metrics를 직접 scrape하며 target up을 확인합니다.",
    page.locator("body"),
    "EVIDENCE"
  );
  await page.screenshot({ path: path.join(frameRoot, "07-prometheus-production-target.png") });
  await holdScene("prometheus-production-target", 5000);

  await page.goto(targetUrl, { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: "AI Workloads", exact: true }).waitFor();
  await page.getByText(reason, { exact: true }).waitFor({ timeout: 30_000 });
  await setOverlay(
    page,
    "한 fresh run이 intake, identity gate, GPU 학습, MLflow 평가, staging 검증, 독립 승인과 관측 가능한 local-production 배포까지 닫혔습니다.",
    page.locator(".scenario-workload-detail"),
    "COMPLETE"
  );
  await page.screenshot({ path: path.join(frameRoot, "08-complete-lifecycle.png"), fullPage: true });
  await holdScene("complete-lifecycle", 6000);

  captureProcess.stdin.write("q\n");
  const captureResult = await captureFinished;
  await writeFile(captureLogPath, captureResult.stderr, "utf8");
  if (captureResult.code !== 0) throw new Error(`60fps capture failed with code ${captureResult.code}`);
  captureProcess = null;

  await writeFile(eventLogPath, lifecycleEvents.map((event) => JSON.stringify(event)).join("\n") + "\n", "utf8");
  const muxResult = await runFfmpeg([
    "-y", "-i", rawPath,
    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
    "-map", "0:v:0", "-map", "1:a:0",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest",
    "-movflags", "+faststart", finalPath
  ]);
  await writeFile(muxLogPath, muxResult.stderr, "utf8");
  if (muxResult.code !== 0) throw new Error(`Faststart mux failed with code ${muxResult.code}`);

  const mediaValidation = await validateFinalMedia();
  const fileHashes = {
    raw_capture_sha256: await sha256File(rawPath),
    final_video_sha256: await sha256File(finalPath),
    workload_run_sha256: await sha256File(path.join(evidenceRoot, "workload-run.json")),
    production_intent_sha256: await sha256File(path.join(evidenceRoot, "production-intent.json"))
  };
  const manifest = {
    schema_version: "evm.smolvlm_live_local_production_recording.v1",
    recorded_at: capturedAt,
    session_id: sessionId,
    run_id: runId,
    production_intent_id: intentId,
    execution_revision: completedRun.identity.source_commit,
    presentation_revision: gitRevision(projectRoot),
    model: {
      family: completedRun.identity.model_family,
      repository: completedRun.identity.model_repository,
      revision: completedRun.identity.model_revision,
      artifact_sha256: completedRun.model_artifact_sha256
    },
    data: {
      dataset_id: completedRun.identity.dataset_id,
      dataset_version: completedRun.identity.dataset_version,
      identity_sha256: completedRun.identity.data_identity_sha256,
      split_counts: initialPreflight.selected_preset.record_counts
    },
    flow: [
      "ui_launch", "airflow_intake", "identity_quality_gate", "approved_gpu_handoff",
      "cuda_lora_adaptation", "mlflow_tracking", "held_out_vlm_evaluation",
      "artifact_seal", "independent_staging_approval", "cuda_staging_inference",
      "prometheus_observation", "local_ci_admission", "independent_production_approval",
      "local_production_cuda_inference", "prometheus_production_observation"
    ],
    capture: {
      requested_fps: 60,
      resolution: "1440x900",
      real_elapsed_time: true,
      waiting_periods_shortened: false,
      window_title: windowTitle,
      window_bounds: windowBounds,
      page_layout: layout,
      preflight_pixels: preflightPixels
    },
    validation: mediaValidation,
    scenes,
    captions,
    browser_errors: browserErrors,
    initial_preflight: initialPreflight,
    final_postconditions: finalEvidence.postconditions,
    file_hashes: fileHashes,
    raw_capture: rawPath,
    final_video: finalPath,
    screenshots: frameRoot,
    event_log: eventLogPath,
    claim_boundary: "Real bounded SmolVLM LoRA lifecycle and local-production CUDA serving on one Windows host, one Docker Desktop Kubernetes node, and one RTX 4080; no benchmark, HA, customer-traffic, load, business A/B, or multi-cluster claim."
  };
  await writeJson(manifestPath, manifest);
  reportProgress("recording-complete", {
    run_id: runId,
    intent_id: intentId,
    duration_seconds: mediaValidation.duration_seconds,
    final_video: finalPath,
    manifest: manifestPath
  });
  console.log(JSON.stringify({ status: "pass", final_video: finalPath, manifest: manifestPath }, null, 2));
} catch (error) {
  const runSnapshot = runId ? await fetchJson(
    `${apiUrl}/control-panel/v1/scenario-workloads/${encodeURIComponent(runId)}`
  ).catch(() => null) : null;
  const intentSnapshot = intentId ? await fetchJson(
    `${apiUrl}/control-panel/v1/scenario-workloads/production-intents/${encodeURIComponent(intentId)}`
  ).catch(() => null) : null;
  const runtimeAfterFailure = await runtimeSnapshot({ includeB0Inference: false }).catch(() => null);
  const failure = {
    schema_version: "evm.smolvlm_live_local_production_capture_failure.v1",
    failed_at: new Date().toISOString(),
    session_id: sessionId,
    run_id: runId || null,
    production_intent_id: intentId || null,
    message: error instanceof Error ? error.message : String(error),
    run_snapshot: runSnapshot,
    intent_snapshot: intentSnapshot,
    runtime_after_failure: runtimeAfterFailure,
    lifecycle_events: lifecycleEvents,
    browser_errors: browserErrors,
    raw_capture: rawPath
  };
  await writeJson(failurePath, failure).catch(() => {});
  reportProgress("capture-failed", { run_id: runId || null, message: failure.message });
  throw error;
} finally {
  if (captureProcess && !captureProcess.killed) captureProcess.stdin.write("q\n");
  if (context) await context.close().catch(() => {});
  const resolvedProfile = path.resolve(profileRoot);
  const resolvedRawRoot = `${path.resolve(rawRoot)}${path.sep}`;
  if (resolvedProfile.startsWith(resolvedRawRoot)) {
    await rm(profileRoot, { recursive: true, force: true }).catch(() => {});
  }
}


async function runtimeSnapshot({ includeB0Inference }) {
  const [
    controlPlane,
    worker,
    presetCatalog,
    runCatalog,
    intentCatalog,
    gpuLease,
    prometheusTargets,
    controlPanelHealth,
    airflowHealth,
    mlflowHealth,
    prometheusHealth,
    ciEvidence
  ] = await Promise.all([
    fetchJson(`${apiUrl}/ready`),
    fetchJson(`${apiUrl}/control-panel/v1/scenario-workloads/worker`),
    fetchJson(`${apiUrl}/control-panel/v1/scenario-workloads/presets`),
    fetchJson(`${apiUrl}/control-panel/v1/scenario-workloads?limit=500`),
    fetchJson(`${apiUrl}/control-panel/v1/scenario-workloads/production-intents?limit=500`),
    fetchJson(`${apiUrl}/control-panel/v1/scenario-workloads/gpu-lease`),
    fetchJson(`${prometheusUrl}/api/v1/targets`),
    fetchText(`${baseUrl}/healthz`),
    fetchJson("http://127.0.0.1:8080/health"),
    fetchText(`${mlflowUrl}/health`),
    fetchText(`${prometheusUrl}/-/ready`),
    readFile(
      "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scenario_workloads/_production/local-ci-evidence.json",
      "utf8"
    ).then(JSON.parse)
  ]);
  const nodes = commandJson(["kubectl", "get", "nodes", "-o", "json"]);
  const daemonSets = commandJson(["kubectl", "-n", "kube-system", "get", "daemonsets", "-o", "json"]);
  const deployment = commandJson([
    "kubectl", "-n", "evm-production", "get", "deployment/evm-b0-production", "-o", "json"
  ]);
  const labels = deployment.spec?.selector?.matchLabels || {};
  const selector = Object.entries(labels).sort().map(([key, value]) => `${key}=${value}`).join(",");
  const pods = commandJson([
    "kubectl", "-n", "evm-production", "get", "pods", "-l", selector, "-o", "json"
  ]);
  const devicePlugin = daemonSets.items.find((item) =>
    String(item.metadata?.name || "").includes("nvidia") ||
    (item.spec?.template?.spec?.containers || []).some((container) => String(container.name || "").includes("nvidia"))
  );
  const desired = Number(deployment.spec?.replicas || 0);
  const available = Number(deployment.status?.availableReplicas || 0);
  const readyPods = pods.items.filter((item) =>
    item.status?.phase === "Running" && !item.metadata?.deletionTimestamp &&
    (item.status?.conditions || []).some((condition) => condition.type === "Ready" && condition.status === "True")
  );
  let b0Ready = null;
  let b0Inference = null;
  if (includeB0Inference && desired === 1 && available === 1) {
    b0Ready = await fetchJson("http://127.0.0.1:30800/ready");
    b0Inference = await fetchJson("http://127.0.0.1:30800/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_uri: "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/raw/industrial/visa/candle/Data/Images/Anomaly/000.JPG"
      })
    });
  }
  return {
    checked_at: new Date().toISOString(),
    control_plane: controlPlane,
    worker,
    selected_preset: presetCatalog.presets.find((preset) => preset.preset_id === presetId) || null,
    runs: runCatalog.runs,
    production_intents: intentCatalog.intents,
    gpu_lease: gpuLease,
    local_ci: ciEvidence,
    services: {
      control_panel: controlPanelHealth,
      airflow: airflowHealth,
      mlflow: mlflowHealth,
      prometheus: prometheusHealth
    },
    gpu_allocatable: nodes.items.reduce(
      (sum, node) => sum + Number(node.status?.allocatable?.["nvidia.com/gpu"] || 0),
      0
    ),
    device_plugin: devicePlugin ? {
      namespace: devicePlugin.metadata?.namespace,
      name: devicePlugin.metadata?.name,
      desired: Number(devicePlugin.status?.desiredNumberScheduled || 0),
      ready: Number(devicePlugin.status?.numberReady || 0)
    } : null,
    b0: {
      namespace: deployment.metadata.namespace,
      name: deployment.metadata.name,
      uid: deployment.metadata.uid,
      selector,
      desired,
      available,
      ready_pods: readyPods.map((pod) => ({ name: pod.metadata.name, uid: pod.metadata.uid })),
      ready: b0Ready,
      inference: b0Inference
    },
    prometheus_up_targets: prometheusTargets.data.activeTargets.filter((target) => target.health === "up")
  };
}


function assertInitialPreflight(preflight) {
  const activeRuns = preflight.runs.filter((run) => ["queued", "running", "waiting_approval"].includes(run.state));
  const activeIntents = preflight.production_intents.filter((intent) =>
    ["pending_approval", "queued", "applying", "applied", "rollback_requested", "rolling_back"].includes(intent.state)
  );
  if (!preflight.selected_preset || preflight.selected_preset.model_family !== "vlm") {
    throw new Error("The governed SmolVLM preset is unavailable");
  }
  if (preflight.control_plane.source_commit !== expectedExecutionCommit) {
    throw new Error(`Control-plane revision mismatch: ${preflight.control_plane.source_commit}`);
  }
  if (preflight.control_plane.source_branch !== expectedExecutionBranch) {
    throw new Error(`Control-plane branch mismatch: ${preflight.control_plane.source_branch}`);
  }
  if (preflight.worker.source_commit !== expectedExecutionCommit) {
    throw new Error(`Scenario worker revision mismatch: ${preflight.worker.source_commit}`);
  }
  if (preflight.worker.source_branch !== expectedExecutionBranch) {
    throw new Error(`Scenario worker branch mismatch: ${preflight.worker.source_branch}`);
  }
  if (preflight.worker.status !== "online" || preflight.worker.current_run_id || preflight.worker.current_intent_id) {
    throw new Error(`Scenario worker is not idle and online: ${preflight.worker.status}`);
  }
  if (
    preflight.local_ci.schema_version !== "evm.scenario_local_ci_evidence.v1" ||
    preflight.local_ci.status !== "pass" ||
    preflight.local_ci.source_commit !== expectedExecutionCommit ||
    preflight.local_ci.scoped_worktree_dirty ||
    !Array.isArray(preflight.local_ci.commands) ||
    preflight.local_ci.commands.length < 6 ||
    preflight.local_ci.commands.some((command) => command.status !== "pass" || command.exit_code !== 0)
  ) {
    throw new Error("Exact-revision local CI admission is not passing");
  }
  if (activeRuns.length) throw new Error(`Another transformer workload is active: ${activeRuns.map((run) => run.run_id).join(",")}`);
  if (activeIntents.length) throw new Error(`Another production intent is active: ${activeIntents.map((intent) => intent.intent_id).join(",")}`);
  if (preflight.gpu_lease !== null) throw new Error("A scenario GPU lease is already active");
  if (preflight.gpu_allocatable !== 1) throw new Error(`Expected one allocatable GPU, got ${preflight.gpu_allocatable}`);
  if (!preflight.device_plugin || preflight.device_plugin.desired !== 1 || preflight.device_plugin.ready !== 1) {
    throw new Error("NVIDIA device plugin is not exact 1/1");
  }
  if (preflight.b0.desired !== 1 || preflight.b0.available !== 1 || preflight.b0.ready_pods.length !== 1) {
    throw new Error("Known-good B0 GPU holder is not exact 1/1");
  }
  if (!preflight.b0.ready?.cuda_available || !String(preflight.b0.inference?.device || "").startsWith("cuda")) {
    throw new Error("Known-good B0 CUDA readiness and inference preflight failed");
  }
  if (preflight.services.airflow?.scheduler?.status !== "healthy") throw new Error("Airflow scheduler is not healthy");
  if (!String(preflight.services.mlflow).toLowerCase().includes("ok")) throw new Error("MLflow health check failed");
  if (!String(preflight.services.prometheus).toLowerCase().includes("ready")) throw new Error("Prometheus is not ready");
}


async function validateAppliedProduction(run, intent) {
  const ready = await fetchJson(`${intent.target.endpoint}/ready`);
  const record = await firstTestRecord(run.identity.manifest_uri);
  const inference = await fetchJson(`${intent.target.endpoint}/infer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model_family: "vlm",
      image_uri: record.image_uri,
      image_sha256: record.image_sha256,
      question: record.question,
      choices: record.choices,
      max_new_tokens: 8
    })
  });
  const query = `up{job="evm-lifecycle-serving",evm_run_id="${run.run_id}",evm_environment="local-production"}`;
  const prometheus = await fetchJson(`${prometheusUrl}/api/v1/query?query=${encodeURIComponent(query)}`);
  const mlflow = await fetchJson(`${mlflowUrl}/api/2.0/mlflow/runs/get?run_id=${encodeURIComponent(run.mlflow_run_id)}`);
  const finalRuntime = await runtimeSnapshot({ includeB0Inference: false });
  const readyExpected = {
    status: "ready",
    environment: "local-production",
    model_family: run.identity.model_family,
    model_repository: run.identity.model_repository,
    model_revision: run.identity.model_revision,
    model_artifact_sha256: run.model_artifact_sha256,
    data_identity_sha256: run.identity.data_identity_sha256,
    source_commit: run.identity.source_commit,
    lifecycle_run_id: run.run_id
  };
  const readyMismatches = Object.entries(readyExpected)
    .filter(([key, value]) => ready[key] !== value)
    .map(([key]) => key);
  if (readyMismatches.length) throw new Error(`Production ready identity mismatch: ${readyMismatches.join(",")}`);
  if (!ready.runtime?.cuda_available) throw new Error("Production readiness does not report CUDA");
  if (inference.model_artifact_sha256 !== run.model_artifact_sha256 || !String(inference.output || "").trim()) {
    throw new Error("Production inference output or artifact identity is invalid");
  }
  if (mlflow.run?.info?.status !== "FINISHED") throw new Error("Exact MLflow run is not FINISHED");
  if (!prometheus.data?.result?.some((item) => item.value?.[1] === "1")) {
    throw new Error("Exact local-production Prometheus target is not up");
  }
  if (finalRuntime.b0.desired !== 0 || finalRuntime.b0.available !== 0 || finalRuntime.b0.ready_pods.length !== 0) {
    throw new Error("B0 holder was not released while transformer local-production owns the GPU");
  }
  if (finalRuntime.device_plugin?.ready !== 1 || finalRuntime.gpu_allocatable !== 1) {
    throw new Error("GPU allocatable or device plugin changed during local-production apply");
  }
  return {
    ready,
    inference,
    prometheus,
    mlflow,
    postconditions: {
      status: "pass",
      checked_at: new Date().toISOString(),
      run_state: run.state,
      production_intent_state: intent.state,
      exact_ready_identity: true,
      real_cuda_inference: true,
      prometheus_up: true,
      mlflow_finished: true,
      b0_holder_replicas: finalRuntime.b0.desired,
      gpu_allocatable: finalRuntime.gpu_allocatable,
      device_plugin_ready: finalRuntime.device_plugin.ready,
      production_endpoint: intent.target.endpoint
    }
  };
}


function assertRunRevision(run) {
  if (run.identity?.source_commit !== expectedExecutionCommit) {
    throw new Error(`Workload source revision mismatch: ${run.identity?.source_commit || "missing"}`);
  }
}


function assertCompletedRun(run) {
  assertRunRevision(run);
  if (run.state !== "completed" || run.progress !== 1 || run.blockers?.length) {
    throw new Error(`Workload did not complete cleanly: ${run.state}`);
  }
  const incomplete = run.stages.filter((stage) => stage.state !== "completed");
  if (incomplete.length) throw new Error(`Incomplete workload stages: ${incomplete.map((stage) => stage.stage_id).join(",")}`);
  if (run.identity.model_family !== "vlm" || !run.identity.model_repository.includes("SmolVLM")) {
    throw new Error("Completed workload is not the selected SmolVLM run");
  }
  if (run.evaluation_summary?.release_gate?.status !== "pass") throw new Error("VLM release gate is not passing");
  for (const metric of ["accuracy", "parse_rate"]) {
    if (typeof run.evaluation_summary?.quality_metrics?.[metric] !== "number") {
      throw new Error(`Required VLM metric is missing: ${metric}`);
    }
  }
  if (!run.model_artifact_sha256 || !run.evidence_index_sha256 || !run.mlflow_run_id) {
    throw new Error("Completed workload evidence identity is incomplete");
  }
}


async function waitForNewWorkload(previousIds, timeoutMs) {
  return waitForWorkload(
    "",
    (run) => !previousIds.has(run.run_id) && run.actor === requester && run.reason === reason,
    timeoutMs,
    "Fresh SmolVLM workload was not observed"
  );
}


async function waitForWorkload(runIdValue, predicate, timeoutMs, message) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const payload = runIdValue
      ? await fetchJson(`${apiUrl}/control-panel/v1/scenario-workloads/${encodeURIComponent(runIdValue)}`)
      : await fetchJson(`${apiUrl}/control-panel/v1/scenario-workloads?limit=500`);
    const candidates = runIdValue ? [payload] : payload.runs;
    const match = candidates.find(predicate);
    if (match) return match;
    await delay(750);
  }
  throw new Error(message);
}


async function waitForIntent(predicate, timeoutMs, message) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const payload = await fetchJson(`${apiUrl}/control-panel/v1/scenario-workloads/production-intents?limit=500`);
    const match = payload.intents.find(predicate);
    if (match) return match;
    await delay(750);
  }
  throw new Error(message);
}


async function refreshWorkloads(page) {
  if (!page.url().includes("view=workloads")) {
    await page.goto(targetUrl, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "AI Workloads", exact: true }).waitFor();
  }
  await page.getByRole("button", { name: "Refresh workloads" }).click();
  await page.waitForTimeout(500);
}


function stageLocator(page, stageId) {
  const label = {
    data_intake: "Data Intake",
    identity_quality_gate: "Identity And Quality Gate",
    gpu_lease: "Exclusive GPU Lease",
    adaptation: "Bounded Model Adaptation",
    experiment_tracking: "MLflow Tracking",
    isolated_evaluation: "Isolated Evaluation",
    artifact_seal: "Artifact Seal",
    approval: "Release Approval",
    staging_serving: "Staging Serving",
    observability: "Inference And Observability"
  }[stageId];
  return label ? page.locator(".scenario-stage", { hasText: label }).first() : page.locator(".scenario-stage-flow");
}


function stageCaption(stageId) {
  return ({
    data_intake: "Airflow가 ScienceQA intake를 실행하고 task와 DAG run 상태를 이 lifecycle run에 연결합니다.",
    identity_quality_gate: "manifest, split, 데이터 identity, 모델 revision과 source commit을 다시 해시해 입력 경계를 고정합니다.",
    gpu_lease: "승인된 exact B0 holder만 0으로 전환하고 fenced lease를 획득해 단일 GPU 소유권을 명확히 합니다.",
    adaptation: "SmolVLM-500M LoRA adaptation과 baseline/held-out 평가가 실제 RTX 4080 CUDA에서 실행됩니다.",
    experiment_tracking: "MLflow의 exact run과 adapter 및 evaluation artifact가 존재하는지 독립적으로 확인합니다.",
    isolated_evaluation: "분리된 test 레코드에서 VLM accuracy, parse rate와 latency를 계산해 품질 gate를 판정합니다.",
    artifact_seal: "배포 후보 adapter bytes와 실행 evidence index를 다시 해시해 이후 승인 대상의 변경을 차단합니다.",
    approval: "자동 gate 통과 후에도 staging 진입은 요청자와 다른 승인자의 결정을 기다립니다.",
    staging_serving: "승인된 adapter를 임시 staging endpoint에 로드하고 실제 ScienceQA 이미지 CUDA inference를 실행합니다.",
    observability: "Prometheus scrape가 exact staging run을 up으로 관측한 뒤 staging을 종료하고 B0 GPU holder를 복구합니다."
  })[stageId] || "Lifecycle state is updating from runtime evidence.";
}


function stageStep(stageId) {
  if (["data_intake", "identity_quality_gate"].includes(stageId)) return "04 / 09";
  if (["gpu_lease", "adaptation"].includes(stageId)) return "05 / 09";
  if (["experiment_tracking", "isolated_evaluation", "artifact_seal", "approval"].includes(stageId)) return "06 / 09";
  return "07 / 09";
}


function productionCaption(state) {
  return ({
    queued: "독립 승인된 production intent가 worker queue에 들어갔습니다. 아직 배포 적용 전입니다.",
    applying: "worker가 identity를 재검증하고 exact B0 holder를 해제한 뒤 SmolVLM CUDA serving을 시작합니다.",
    applied: "SmolVLM local-production 적용이 완료됐습니다. readiness, 실제 inference와 Prometheus 검증이 모두 통과했습니다.",
    failed: "배포 gate가 실패해 적용을 중단하고 known-good B0 복구 절차를 수행했습니다."
  })[state] || `Production intent state: ${state}`;
}


function recordWorkloadEvent(run, source) {
  const event = {
    observed_at: new Date().toISOString(),
    source,
    kind: "workload",
    run_id: run.run_id,
    state: run.state,
    current_stage: run.current_stage,
    progress: run.progress,
    version: run.version,
    training_step: run.training_progress?.current_step || null,
    stages: run.stages.map((stage) => ({
      stage_id: stage.stage_id,
      state: stage.state,
      progress: stage.progress
    }))
  };
  const previous = lifecycleEvents.at(-1);
  if (!previous || eventSignature(previous) !== eventSignature(event)) lifecycleEvents.push(event);
}


function recordIntentEvent(intent, source) {
  const event = {
    observed_at: new Date().toISOString(),
    source,
    kind: "production_intent",
    run_id: intent.run_id,
    intent_id: intent.intent_id,
    state: intent.state,
    version: intent.version,
    action_digest: intent.action_digest,
    blockers: intent.blockers
  };
  const previous = lifecycleEvents.at(-1);
  if (!previous || eventSignature(previous) !== eventSignature(event)) lifecycleEvents.push(event);
}


function eventSignature(event) {
  return JSON.stringify({
    kind: event.kind,
    run_id: event.run_id,
    intent_id: event.intent_id,
    state: event.state,
    current_stage: event.current_stage,
    progress: event.progress,
    training_step: event.training_step,
    version: event.version,
    stages: event.stages,
    blockers: event.blockers
  });
}


async function setOverlay(page, caption, locator, step) {
  if (forbiddenPresentationTerms.some((term) => caption.toLowerCase().includes(term.toLowerCase()))) {
    throw new Error(`Forbidden presentation label detected: ${caption}`);
  }
  captions.push({ observed_at: new Date().toISOString(), step, text: caption });
  await locator.scrollIntoViewIfNeeded().catch(() => {});
  const box = await locator.boundingBox().catch(() => null);
  const x = box ? Math.round(box.x + Math.min(box.width * 0.78, box.width - 20)) : 1080;
  const y = box ? Math.round(box.y + Math.min(box.height * 0.5, box.height - 18)) : 540;
  await page.evaluate(({ captionText, pointerX, pointerY, stepText }) => {
    let style = document.querySelector("#evm-live-demo-style");
    if (!style) {
      style = document.createElement("style");
      style.id = "evm-live-demo-style";
      style.textContent = `
        #evm-live-caption { position: fixed; z-index: 2147483646; left: 50%; bottom: 18px; transform: translateX(-50%); max-width: min(1000px, calc(100vw - 48px)); padding: 10px 16px; border: 1px solid rgba(182,255,59,.42); border-radius: 7px; color: #f7f8f4; background: rgba(2,3,2,.94); box-shadow: 0 10px 34px rgba(0,0,0,.55); font: 600 13px/1.48 system-ui, sans-serif; text-align: center; pointer-events: none; }
        #evm-live-pointer { position: fixed; z-index: 2147483647; width: 16px; height: 16px; border: 2px solid #fff; border-radius: 50%; background: #b6ff3b; box-shadow: 0 0 0 5px rgba(182,255,59,.20), 0 0 18px rgba(182,255,59,.86); pointer-events: none; }
        #evm-live-step { position: fixed; z-index: 2147483646; top: 18px; right: 18px; padding: 7px 10px; border: 1px solid rgba(182,255,59,.30); border-radius: 6px; color: #b6ff3b; background: rgba(2,3,2,.92); font: 700 11px/1 system-ui, sans-serif; pointer-events: none; }
      `;
      document.head.appendChild(style);
    }
    let captionElement = document.querySelector("#evm-live-caption");
    if (!captionElement) {
      captionElement = document.createElement("div");
      captionElement.id = "evm-live-caption";
      document.body.appendChild(captionElement);
    }
    let pointer = document.querySelector("#evm-live-pointer");
    if (!pointer) {
      pointer = document.createElement("div");
      pointer.id = "evm-live-pointer";
      document.body.appendChild(pointer);
    }
    let stepElement = document.querySelector("#evm-live-step");
    if (!stepElement) {
      stepElement = document.createElement("div");
      stepElement.id = "evm-live-step";
      document.body.appendChild(stepElement);
    }
    captionElement.textContent = captionText;
    stepElement.textContent = stepText;
    pointer.style.left = `${Math.max(6, Math.min(innerWidth - 24, pointerX - 8))}px`;
    pointer.style.top = `${Math.max(6, Math.min(innerHeight - 24, pointerY - 8))}px`;
  }, { captionText: caption, pointerX: x, pointerY: y, stepText: step });
  await page.mouse.move(x, y);
}


async function holdScene(sceneId, durationMs) {
  scenes.push({ scene_id: sceneId, started_ms: Date.now() - captureStartedMs, duration_ms: durationMs });
  reportProgress("scene", { scene_id: sceneId, duration_ms: durationMs });
  await delay(durationMs);
}


function startCapture() {
  const args = [
    "-y", "-thread_queue_size", "2048", "-f", "gdigrab", "-framerate", "60",
    "-draw_mouse", "0", "-offset_x", "0", "-offset_y", "0", "-video_size", "1440x900",
    "-i", "desktop", "-an", "-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq",
    "-rc", "vbr", "-cq", "20", "-b:v", "0", "-pix_fmt", "yuv420p", "-r", "60",
    "-fps_mode", "cfr", "-g", "120", "-sc_threshold", "0", "-f", "matroska", rawPath
  ];
  const processHandle = spawn(ffmpegPath, args, { stdio: ["pipe", "ignore", "pipe"], windowsHide: true });
  let stderr = "";
  let readyResolve;
  let readyReject;
  const ready = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
  const timeout = setTimeout(() => readyReject(new Error("FFmpeg capture did not become ready")), 15_000);
  processHandle.stderr.on("data", (chunk) => {
    const value = chunk.toString();
    stderr += value;
    if (value.includes("Press [q]")) { clearTimeout(timeout); readyResolve(); }
  });
  processHandle.on("error", (error) => { clearTimeout(timeout); readyReject(error); });
  const finished = new Promise((resolve) => processHandle.on("close", (code) => resolve({ code, stderr })));
  return { process: processHandle, ready, finished };
}


async function validateFinalMedia() {
  const probe = spawnSync(ffmpegPath, ["-hide_banner", "-i", finalPath], { encoding: "utf8", windowsHide: true });
  const mediaInfo = `${probe.stdout || ""}\n${probe.stderr || ""}`;
  const bytes = await readFile(finalPath);
  const headerWindow = bytes.subarray(0, Math.min(bytes.length, 4 * 1024 * 1024));
  const moovOffset = headerWindow.indexOf(Buffer.from("moov"));
  const mdatOffset = headerWindow.indexOf(Buffer.from("mdat"));
  const durationMatch = mediaInfo.match(/Duration: (\d{2}:\d{2}:\d{2}(?:\.\d+)?)/);
  const durationSeconds = durationMatch ? durationToSeconds(durationMatch[1]) : 0;
  const seekSeconds = Math.max(1, Math.min(120, Math.floor(durationSeconds / 2)));
  const seekResult = await runFfmpeg([
    "-y", "-ss", String(seekSeconds), "-i", finalPath, "-frames:v", "1", "-update", "1", seekProofPath
  ]);
  if (seekResult.code !== 0) throw new Error("Seek proof extraction failed");
  const validation = {
    duration_seconds: durationSeconds,
    declared_60_fps: /\b60 fps\b/.test(mediaInfo),
    h264: /Video: h264/.test(mediaInfo),
    aac: /Audio: aac/.test(mediaInfo),
    faststart: moovOffset >= 0 && mdatOffset >= 0 && moovOffset < mdatOffset,
    seek_proof_seconds: seekSeconds,
    seek_proof_path: seekProofPath,
    browser_errors: browserErrors
  };
  if (!validation.declared_60_fps || !validation.h264 || !validation.aac || !validation.faststart || browserErrors.length) {
    throw new Error(`Final media validation failed: ${JSON.stringify(validation)}`);
  }
  return validation;
}


async function validateDesktopCapture() {
  const probePath = path.join(rawRoot, "desktop-preflight.rgb");
  const result = await runFfmpeg([
    "-y", "-f", "gdigrab", "-framerate", "1", "-draw_mouse", "0", "-offset_x", "0",
    "-offset_y", "0", "-video_size", "1440x900", "-i", "desktop", "-frames:v", "1",
    "-pix_fmt", "rgb24", "-f", "rawvideo", probePath
  ]);
  if (result.code !== 0) throw new Error("Pinned desktop capture preflight failed");
  const bytes = await readFile(probePath);
  await rm(probePath, { force: true });
  let minimum = 255;
  let maximum = 0;
  let bright = 0;
  let count = 0;
  for (let index = 0; index < bytes.length; index += 97) {
    const value = bytes[index];
    minimum = Math.min(minimum, value);
    maximum = Math.max(maximum, value);
    if (value > 32) bright += 1;
    count += 1;
  }
  const stats = { minimum, maximum, bright_ratio: count ? bright / count : 0 };
  if (maximum < 48 || stats.bright_ratio < 0.002) throw new Error(`Desktop capture is blank: ${JSON.stringify(stats)}`);
  return stats;
}


async function validatePageLayout(page) {
  const layout = await page.evaluate(() => ({
    inner_width: innerWidth,
    inner_height: innerHeight,
    document_width: document.documentElement.scrollWidth,
    body_width: document.body.scrollWidth
  }));
  if (layout.inner_width !== 1440 || layout.inner_height !== 860 || layout.document_width > 1441 || layout.body_width > 1441) {
    throw new Error(`Control Panel layout is clipped: ${JSON.stringify(layout)}`);
  }
  return layout;
}


async function waitForEnabled(locator, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await locator.isEnabled().catch(() => false)) return;
    await delay(200);
  }
  throw new Error("Timed out waiting for an enabled UI control");
}


async function waitForRunControl(locator, runIdValue, timeoutMs, controlLabel) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const run = await fetchJson(
      `${apiUrl}/control-panel/v1/scenario-workloads/${encodeURIComponent(runIdValue)}`
    );
    if (["blocked", "failed", "cancelled"].includes(run.state)) {
      const blockers = (run.blockers || []).join(",") || run.current_stage || "unknown";
      throw new Error(`${controlLabel} unavailable because workload ${run.state}: ${blockers}`);
    }
    if (await locator.isEnabled().catch(() => false)) return;
    await delay(250);
  }
  throw new Error(`Timed out waiting for ${controlLabel} control`);
}


async function waitForWindowTitle(fragment) {
  const escaped = fragment.replaceAll("'", "''");
  const command = `$fragment='${escaped}'; $match=Get-Process | Where-Object { $_.MainWindowTitle -like \"*$fragment*\" } | Select-Object -First 1 -ExpandProperty MainWindowTitle; if($match){$match}`;
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    const result = spawnSync("powershell.exe", ["-NoProfile", "-Command", command], { encoding: "utf8", windowsHide: true });
    const title = String(result.stdout || "").trim();
    if (title) return title;
    await delay(250);
  }
  throw new Error(`Chromium window title not found for ${fragment}`);
}


function pinWindowTopmost(windowTitle) {
  const escaped = windowTitle.replaceAll("'", "''");
  const script = `
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class EvmLiveWindow {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
  [DllImport("user32.dll", SetLastError=true)] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
}
'@
$target = Get-Process | Where-Object { $_.MainWindowTitle -eq '${escaped}' } | Select-Object -First 1
if (-not $target) { throw 'target_window_not_found' }
$handle = $target.MainWindowHandle
[void][EvmLiveWindow]::ShowWindowAsync($handle, 9)
[void][EvmLiveWindow]::SetWindowPos($handle, [IntPtr](-1), 0, 0, 1440, 900, 0x0040)
Start-Sleep -Milliseconds 300
$rect = New-Object EvmLiveWindow+RECT
[void][EvmLiveWindow]::GetWindowRect($handle, [ref]$rect)
[PSCustomObject]@{ handle = $handle.ToInt64(); left = $rect.Left; top = $rect.Top; width = $rect.Right - $rect.Left; height = $rect.Bottom - $rect.Top } | ConvertTo-Json -Compress
`;
  const encoded = Buffer.from(script, "utf16le").toString("base64");
  const result = spawnSync("powershell.exe", ["-NoProfile", "-EncodedCommand", encoded], { encoding: "utf8", windowsHide: true });
  if (result.status !== 0) throw new Error(`Failed to pin Chromium window: ${result.stderr || result.stdout}`);
  const bounds = JSON.parse(String(result.stdout || "{}").trim());
  if (bounds.left !== 0 || bounds.top !== 0 || bounds.width < 1400 || bounds.height < 860) {
    throw new Error(`Pinned Chromium bounds are unsafe: ${JSON.stringify(bounds)}`);
  }
  return bounds;
}


async function firstTestRecord(manifestUri) {
  const payload = await readFile(manifestUri, "utf8");
  const records = payload.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
  const record = records.find((item) => item.split === "test");
  if (!record) throw new Error("ScienceQA manifest has no test record");
  return record;
}


function commandJson(command) {
  const result = spawnSync(command[0], command.slice(1), {
    encoding: "utf8",
    windowsHide: true,
    timeout: 60_000
  });
  if (result.status !== 0) throw new Error(`${command.join(" ")} failed: ${result.stderr || result.stdout}`);
  return JSON.parse(result.stdout);
}


async function runFfmpeg(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(ffmpegPath, args, { stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}


async function fetchJson(url, init = {}) {
  const response = await fetch(url, {
    ...init,
    headers: { Accept: "application/json", ...(init.headers || {}) }
  });
  const body = await response.text();
  if (!response.ok) throw new Error(`${url} returned ${response.status}: ${body.slice(0, 500)}`);
  try {
    return body ? JSON.parse(body) : null;
  } catch {
    throw new Error(`${url} did not return JSON: ${body.slice(0, 500)}`);
  }
}


async function fetchText(url) {
  const response = await fetch(url);
  const body = await response.text();
  if (!response.ok) throw new Error(`${url} returned ${response.status}: ${body.slice(0, 500)}`);
  return body;
}


async function writeJson(filePath, payload) {
  await writeFile(filePath, JSON.stringify(payload, null, 2), "utf8");
}


async function sha256File(filePath) {
  return createHash("sha256").update(await readFile(filePath)).digest("hex");
}


function gitRevision(cwd) {
  const result = spawnSync("git", ["rev-parse", "HEAD"], { cwd, encoding: "utf8", windowsHide: true });
  if (result.status !== 0) throw new Error(`Unable to resolve Git revision: ${result.stderr || result.stdout}`);
  return result.stdout.trim();
}


function gitBranch(cwd) {
  const result = spawnSync("git", ["branch", "--show-current"], { cwd, encoding: "utf8", windowsHide: true });
  if (result.status !== 0) throw new Error(`Unable to resolve Git branch: ${result.stderr || result.stdout}`);
  return result.stdout.trim() || "detached";
}


function reportProgress(event, details = {}) {
  console.log(JSON.stringify({ at: new Date().toISOString(), event, ...details }));
}


function shortId(value) {
  return value.length > 28 ? `${value.slice(0, 18)}...${value.slice(-8)}` : value;
}


function timestampToken() {
  return new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z").toLowerCase();
}


function durationToSeconds(value) {
  const [hours, minutes, seconds] = value.split(":");
  return Number(hours) * 3600 + Number(minutes) * 60 + Number(seconds);
}


function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
