import {
  Activity,
  Box,
  CheckCircle2,
  Cpu,
  Database,
  ExternalLink,
  FileCheck2,
  Gauge,
  KeyRound,
  LoaderCircle,
  Play,
  RefreshCcw,
  Rocket,
  RotateCcw,
  Server,
  ShieldCheck,
  TriangleAlert
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  approveScenarioGpuHandoff,
  approveScenarioProductionIntent,
  approveScenarioStaging,
  createScenarioProductionIntent,
  fetchScenarioProductionIntents,
  fetchScenarioWorkloadPresets,
  fetchScenarioWorkloadWorker,
  fetchScenarioWorkloads,
  launchScenarioWorkload,
  rollbackScenarioProductionIntent
} from "../api/controlPanelClient";
import type {
  ScenarioProductionIntent,
  ScenarioWorkloadPreset,
  ScenarioWorkloadRun,
  ScenarioWorkloadRunState,
  ScenarioWorkloadStage,
  ScenarioWorkloadWorkerState
} from "../api/types";
import { StatusBadge } from "../components/StatusBadge";


const activeStates: ScenarioWorkloadRunState[] = ["queued", "running", "waiting_approval"];
const activeProductionStates = [
  "pending_approval", "queued", "applying", "applied", "rollback_requested", "rolling_back"
];
type WorkloadFilter = "latest" | "all" | "active" | "completed" | "attention";


export function ScenarioWorkloads() {
  const [runs, setRuns] = useState<ScenarioWorkloadRun[]>([]);
  const [presets, setPresets] = useState<ScenarioWorkloadPreset[]>([]);
  const [worker, setWorker] = useState<ScenarioWorkloadWorkerState | null>(null);
  const [intents, setIntents] = useState<ScenarioProductionIntent[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const selectedRef = useRef("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [syncedAt, setSyncedAt] = useState("");
  const [filter, setFilter] = useState<WorkloadFilter>("latest");
  const [busyAction, setBusyAction] = useState("");
  const [presetId, setPresetId] = useState("");
  const [requester, setRequester] = useState("ml-engineer");
  const [runReason, setRunReason] = useState(
    "Execute an identity-bound SmolVLM lifecycle for local production validation"
  );
  const [approver, setApprover] = useState("ai-platform-sre");
  const [approvalReason, setApprovalReason] = useState(
    "Approve the exact run identity after reviewing quality and runtime evidence"
  );

  async function load() {
    try {
      const runResult = await fetchScenarioWorkloads();
      const [presetResult, workerResult, intentResult] = await Promise.allSettled([
        fetchScenarioWorkloadPresets(),
        fetchScenarioWorkloadWorker(),
        fetchScenarioProductionIntents()
      ]);
      setRuns(runResult.runs);
      if (presetResult.status === "fulfilled") {
        setPresets(presetResult.value.presets);
        setPresetId((current) => current || presetResult.value.presets[0]?.preset_id || "");
      }
      if (workerResult.status === "fulfilled") setWorker(workerResult.value);
      if (intentResult.status === "fulfilled") setIntents(intentResult.value.intents);
      if (!runResult.runs.some((run) => run.run_id === selectedRef.current)) {
        const next = runResult.runs[0]?.run_id || "";
        selectedRef.current = next;
        setSelectedId(next);
      }
      const degraded = [presetResult, workerResult, intentResult].filter(
        (result) => result.status === "rejected"
      ).length;
      setError(degraded ? `Execution controls degraded / ${degraded} auxiliary source unavailable` : "");
      setSyncedAt(new Date().toLocaleTimeString());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Scenario workload control plane unavailable");
    }
  }

  useEffect(() => {
    void load();
    const interval = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(interval);
  }, []);

  const visibleRuns = useMemo(() => runs.filter((run) => {
    if (filter === "active") return activeStates.includes(run.state);
    if (filter === "completed") return run.state === "completed";
    if (filter === "attention") return ["failed", "blocked"].includes(run.state);
    return true;
  }).slice(0, filter === "latest" ? 1 : undefined), [filter, runs]);
  const selected = useMemo(
    () => visibleRuns.find((run) => run.run_id === selectedId) || visibleRuns[0] || null,
    [selectedId, visibleRuns]
  );
  const selectedIntent = selected
    ? intents.find((intent) => intent.run_id === selected.run_id) || null
    : null;
  const production = intents.find((intent) => intent.state === "applied") || null;
  const activeProduction = intents.find((intent) => activeProductionStates.includes(intent.state)) || null;
  const active = runs.filter((run) => activeStates.includes(run.state)).length;
  const completed = runs.filter((run) => run.state === "completed").length;
  const attention = runs.filter((run) => ["failed", "blocked"].includes(run.state)).length;
  const canLaunch = Boolean(
    presetId && requester.trim().length >= 2 && runReason.trim().length >= 12
    && !active && !activeProduction && worker && ["online", "busy"].includes(worker.status)
  );

  function select(runId: string) {
    selectedRef.current = runId;
    setSelectedId(runId);
  }

  async function mutate(label: string, action: () => Promise<unknown>, success: string) {
    setBusyAction(label);
    setNotice("");
    setError("");
    try {
      await action();
      setNotice(success);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `${label} failed`);
    } finally {
      setBusyAction("");
    }
  }

  async function launch() {
    await mutate("launch", async () => {
      const run = await launchScenarioWorkload({
        preset_id: presetId,
        actor: requester.trim(),
        reason: runReason.trim()
      });
      select(run.run_id);
    }, "Workload queued with an immutable run identity.");
  }

  const approval = { actor: approver.trim(), reason: approvalReason.trim() };
  const approvalReady = approval.actor.length >= 2 && approval.reason.length >= 12;

  return (
    <section className="scenario-workloads" aria-label="AI scenario workloads">
      <header className="scenario-workload-header">
        <div>
          <span className="eyebrow">Real Transformer Control Plane</span>
          <h2>AI Workloads</h2>
          <p>Execute, validate, and release one identity-bound VLM or LLM workload on the local GPU platform.</p>
        </div>
        <div className="scenario-sync">
          <span className={error ? "sync-dot error" : "sync-dot live"} />
          <small>{error || `Live / ${syncedAt || "connecting"}`}</small>
          <button type="button" className="icon-button" onClick={() => void load()} title="Refresh workloads" aria-label="Refresh workloads">
            <RefreshCcw size={16} />
          </button>
        </div>
      </header>

      <ProductionBanner intent={production} />

      <section className="scenario-command-center" aria-label="Workload execution controls">
        <header>
          <div><span className="command-index">01</span><div><strong>Execute</strong><small>Choose a governed preset and seal a fresh run identity.</small></div></div>
          <WorkerBadge worker={worker} />
        </header>
        <div className="scenario-command-grid">
          <label>
            <span>Workload preset</span>
            <select aria-label="Workload Preset" value={presetId} onChange={(event) => setPresetId(event.target.value)}>
              {presets.map((preset) => <option key={preset.preset_id} value={preset.preset_id}>{preset.label}</option>)}
            </select>
          </label>
          <label>
            <span>Run requester</span>
            <input aria-label="Run Requester" value={requester} onChange={(event) => setRequester(event.target.value)} />
          </label>
          <label className="command-reason">
            <span>Execution reason</span>
            <input aria-label="Run Reason" value={runReason} onChange={(event) => setRunReason(event.target.value)} />
          </label>
          <button type="button" className="primary-action" disabled={!canLaunch || Boolean(busyAction)} onClick={() => void launch()}>
            {busyAction === "launch" ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}
            Launch real workload
          </button>
        </div>
        <PresetSummary preset={presets.find((preset) => preset.preset_id === presetId) || null} />
        {activeProduction ? <p className="command-boundary"><ShieldCheck size={14} />GPU execution is reserved by production intent <code>{shortHash(activeProduction.intent_id)}</code>.</p> : null}
        {notice ? <p className="scenario-notice"><CheckCircle2 size={15} />{notice}</p> : null}
      </section>

      <div className="scenario-kpis" role="group" aria-label="Workload status filter">
        <Kpi icon={Gauge} label="Latest" value={runs.length ? 1 : 0} active={filter === "latest"} onClick={() => setFilter("latest")} />
        <Kpi icon={Box} label="Runs" value={runs.length} active={filter === "all"} onClick={() => setFilter("all")} />
        <Kpi icon={Activity} label="Active" value={active} tone="run" active={filter === "active"} onClick={() => setFilter("active")} />
        <Kpi icon={FileCheck2} label="Completed" value={completed} tone="good" active={filter === "completed"} onClick={() => setFilter("completed")} />
        <Kpi icon={TriangleAlert} label="Attention" value={attention} tone={attention ? "bad" : "idle"} active={filter === "attention"} onClick={() => setFilter("attention")} />
      </div>

      <div className="scenario-workload-layout">
        <aside className="scenario-run-list" aria-label="AI workload history">
          {visibleRuns.map((run) => (
            <button type="button" key={run.run_id} className={run.run_id === selected?.run_id ? "active" : ""} onClick={() => select(run.run_id)}>
              <span className={`family-mark ${run.identity.model_family}`}>{run.identity.model_family.toUpperCase()}</span>
              <span><strong>{run.identity.model_repository.split("/").at(-1)}</strong><small>{formatTime(run.created_at)} / {Math.round(run.progress * 100)}%</small></span>
              <StatusBadge status={run.state} />
            </button>
          ))}
          {!visibleRuns.length ? <div className="scenario-empty">No {filter === "all" ? "governed" : filter} transformer run is available.</div> : null}
        </aside>

        {selected ? (
          <WorkloadDetail
            run={selected}
            intent={selectedIntent}
            approver={approver}
            approvalReason={approvalReason}
            setApprover={setApprover}
            setApprovalReason={setApprovalReason}
            approvalReady={approvalReady}
            busyAction={busyAction}
            mutate={mutate}
          />
        ) : <div className="scenario-empty">Launch or select a workload to inspect its lifecycle.</div>}
      </div>
    </section>
  );
}


function WorkloadDetail({
  run,
  intent,
  approver,
  approvalReason,
  setApprover,
  setApprovalReason,
  approvalReady,
  busyAction,
  mutate
}: {
  run: ScenarioWorkloadRun;
  intent: ScenarioProductionIntent | null;
  approver: string;
  approvalReason: string;
  setApprover: (value: string) => void;
  setApprovalReason: (value: string) => void;
  approvalReady: boolean;
  busyAction: string;
  mutate: (label: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const activeStage = run.stages.find((stage) => stage.stage_id === run.current_stage);
  const gpuCanApprove = ["queued", "running"].includes(run.state)
    && (run.control_state?.gpu_handoff_state || "missing") === "missing";
  const stagingCanApprove = run.state === "waiting_approval" && run.current_stage === "approval"
    && (run.control_state?.staging_approval_state || "missing") === "missing";
  const canCreateIntent = run.state === "completed" && !intent
    && run.evaluation_summary?.release_gate.status === "pass";

  return (
    <main className="scenario-workload-detail">
      <header>
        <div><span className="eyebrow">{run.identity.scenario_id}</span><h2>{run.identity.model_repository.split("/").at(-1)}</h2><p>{run.reason}</p></div>
        <div className="scenario-run-state"><StatusBadge status={run.state} /><strong>{Math.round(run.progress * 100)}%</strong></div>
      </header>

      <div className={`scenario-progress state-${run.state}`} aria-label={`Workload progress ${Math.round(run.progress * 100)}%`}><i style={{ width: `${Math.round(run.progress * 100)}%` }} /></div>
      <section className="scenario-stage-flow" aria-label="Workload stage flow">
        {run.stages.map((stage) => <StageNode key={stage.stage_id} stage={stage} />)}
      </section>
      <TrainingProgress run={run} />

      {run.blockers.length ? <section className="scenario-blockers" role="alert"><TriangleAlert size={18} /><div><strong>{activeStage?.label || "Workload blocked"}</strong>{run.blockers.map((item) => <code key={item}>{item}</code>)}</div></section> : null}

      <section className="scenario-action-rail" aria-label="Workload approvals and release actions">
        <header><div><span className="command-index">02</span><div><strong>Validate and release</strong><small>Every GPU or environment transition is bound to this run identity.</small></div></div><code>{shortHash(run.identity.identity_sha256)}</code></header>
        <div className="scenario-approval-fields">
          <label><span>Independent approver</span><input aria-label="Independent Approver" value={approver} onChange={(event) => setApprover(event.target.value)} /></label>
          <label><span>Decision reason</span><input aria-label="Approval Reason" value={approvalReason} onChange={(event) => setApprovalReason(event.target.value)} /></label>
        </div>
        <div className="scenario-action-buttons">
          <ActionButton
            icon={KeyRound}
            label="Authorize GPU handoff"
            detail={run.control_state?.gpu_handoff_state === "consumed" ? "Consumed once" : run.control_state?.gpu_handoff_state || "Required before CUDA work"}
            enabled={gpuCanApprove && approvalReady}
            busy={busyAction === "gpu"}
            done={["approved", "consumed"].includes(run.control_state?.gpu_handoff_state || "")}
            onClick={() => mutate("gpu", () => approveScenarioGpuHandoff(run.run_id, { actor: approver.trim(), reason: approvalReason.trim() }), "Exact GPU handoff approved.")}
          />
          <ActionButton
            icon={ShieldCheck}
            label="Approve staging"
            detail={run.control_state?.staging_approval_state === "approved" ? "Identity approved" : "Available after evaluation"}
            enabled={stagingCanApprove && approvalReady}
            busy={busyAction === "staging"}
            done={run.control_state?.staging_approval_state === "approved"}
            onClick={() => mutate("staging", () => approveScenarioStaging(run.run_id, { actor: approver.trim(), reason: approvalReason.trim() }), "Staging validation approved.")}
          />
          <ActionButton
            icon={FileCheck2}
            label="Create production intent"
            detail={intent ? `Intent ${intent.state.replaceAll("_", " ")}` : "Requires a sealed pass"}
            enabled={canCreateIntent && approvalReady}
            busy={busyAction === "intent"}
            done={Boolean(intent)}
            onClick={() => mutate("intent", () => createScenarioProductionIntent(run.run_id, { actor: run.actor, reason: "Promote the exact validated transformer adapter to local production" }), "Production intent created for independent approval.")}
          />
          <ActionButton
            icon={Rocket}
            label="Approve local production"
            detail={intent?.state === "applied" ? "CUDA service applied" : intent?.state.replaceAll("_", " ") || "Intent required"}
            enabled={Boolean(intent?.state === "pending_approval" && approvalReady)}
            busy={busyAction === "production" || intent?.state === "applying"}
            done={intent?.state === "applied"}
            onClick={() => intent && mutate("production", () => approveScenarioProductionIntent(intent.intent_id, { actor: approver.trim(), reason: approvalReason.trim() }), "Local production deployment queued.")}
          />
        </div>
      </section>

      {intent ? <ProductionIntentPanel intent={intent} approver={approver} reason={approvalReason} busyAction={busyAction} mutate={mutate} /> : null}
      <EvaluationPanel run={run} />
      <section className="scenario-facts">
        <Fact icon={Database} label="Data" value={run.identity.dataset_version} detail={shortHash(run.identity.data_identity_sha256)} />
        <Fact icon={Cpu} label="Compute" value={run.runtime_versions.gpu_name || run.identity.compute_backend} detail={memoryLabel(run)} />
        <Fact icon={Box} label="Adaptation" value={`${run.adaptation_method.toUpperCase()} / ${run.quantization_observed || run.quantization_requested}`} detail={run.mlflow_run_id ? `MLflow ${shortHash(run.mlflow_run_id)}` : "Tracking pending"} />
        <Fact icon={FileCheck2} label="Artifact" value={run.model_artifact_sha256 ? shortHash(run.model_artifact_sha256) : "Pending"} detail={run.gpu_lease_state === "released" ? "GPU lease released" : `GPU lease ${run.gpu_lease_state}`} />
      </section>
      <section className="scenario-evidence-strip">
        <div><span>Source</span><code>{shortHash(run.identity.source_commit || "missing")}</code></div>
        <div><span>Model revision</span><code>{shortHash(run.identity.model_revision)}</code></div>
        <div><span>Evidence</span><code>{run.evidence_index_sha256 ? shortHash(run.evidence_index_sha256) : "not sealed"}</code></div>
        <div><span>Staging</span><strong>{run.runtime_versions.staging_runtime_state || "not started"}</strong></div>
        {run.mlflow_run_id ? <a href="http://127.0.0.1:5000" target="_blank" rel="noreferrer">MLflow <ExternalLink size={13} /></a> : null}
      </section>
    </main>
  );
}


function ProductionBanner({ intent }: { intent: ScenarioProductionIntent | null }) {
  return (
    <section className={`scenario-production-banner ${intent ? "is-live" : "is-idle"}`} aria-label="Local production status">
      <div><Server size={18} /><span><small>Local production</small><strong>{intent ? intent.model_repository.split("/").at(-1) : "B0 GPU holder active"}</strong></span></div>
      <div><span>Runtime</span><strong>{intent ? "Windows host CUDA" : "Kubernetes / CUDA"}</strong></div>
      <div><span>Endpoint</span><code>{intent?.target.endpoint || "http://127.0.0.1:30800"}</code></div>
      <StatusBadge status={intent?.state || "ready"} />
    </section>
  );
}


function WorkerBadge({ worker }: { worker: ScenarioWorkloadWorkerState | null }) {
  const status = worker?.status || "offline";
  return <div className={`scenario-worker worker-${status}`}><i /><span><strong>{status}</strong><small>{worker?.current_run_id ? `run ${shortHash(worker.current_run_id)}` : worker?.current_intent_id ? `intent ${shortHash(worker.current_intent_id)}` : `${worker?.heartbeat_age_seconds?.toFixed(1) || "-"}s heartbeat`}</small></span></div>;
}


function PresetSummary({ preset }: { preset: ScenarioWorkloadPreset | null }) {
  if (!preset) return null;
  return <div className="scenario-preset-summary"><span className={`family-mark ${preset.model_family}`}>{preset.model_family.toUpperCase()}</span><div><strong>{preset.model_repository}</strong><small>{preset.adaptation_method.toUpperCase()} / {preset.max_steps} steps / {preset.quantization_requested}</small></div><div className="preset-splits">{Object.entries(preset.record_counts).map(([name, count]) => <span key={name}><small>{name}</small><strong>{count}</strong></span>)}</div><code>{shortHash(preset.model_revision)}</code></div>;
}


function TrainingProgress({ run }: { run: ScenarioWorkloadRun }) {
  const progress = run.training_progress;
  if (!progress) return null;
  return <section className="scenario-training-progress" aria-label="Live training progress"><header><div><Activity size={16} /><span><small>Live CUDA adaptation</small><strong>Step {progress.current_step} of {progress.max_steps}</strong></span></div><strong>{Math.round(progress.progress * 100)}%</strong></header><div><i style={{ width: `${Math.round(progress.progress * 100)}%` }} /></div><footer><span>Latest loss <strong>{progress.latest_loss?.toFixed(4) || "-"}</strong></span><span>Updated <strong>{formatTime(progress.observed_at)}</strong></span><span>Family <strong>{progress.model_family.toUpperCase()}</strong></span></footer></section>;
}


function ActionButton({ icon: Icon, label, detail, enabled, busy, done, onClick }: { icon: typeof Activity; label: string; detail: string; enabled: boolean; busy: boolean; done: boolean; onClick: () => void }) {
  return <button type="button" className={`scenario-action ${done ? "is-done" : ""}`} disabled={!enabled || busy} onClick={onClick}>{busy ? <LoaderCircle className="spin" size={17} /> : done ? <CheckCircle2 size={17} /> : <Icon size={17} />}<span><strong>{label}</strong><small>{detail}</small></span></button>;
}


function ProductionIntentPanel({ intent, approver, reason, busyAction, mutate }: { intent: ScenarioProductionIntent; approver: string; reason: string; busyAction: string; mutate: (label: string, action: () => Promise<unknown>, success: string) => Promise<void> }) {
  return <section className={`scenario-production-intent state-${intent.state}`} aria-label="Production deployment intent"><header><div><Rocket size={17} /><span><small>Deployment intent</small><strong>{intent.state.replaceAll("_", " ")}</strong></span></div><code>{shortHash(intent.intent_id)}</code></header><div className="intent-flow"><span className={intentStateReached(intent.state, "pending_approval") ? "done" : ""}>requested</span><i /><span className={intentStateReached(intent.state, "queued") ? "done" : ""}>approved</span><i /><span className={intentStateReached(intent.state, "applying") ? "done" : ""}>applying</span><i /><span className={intent.state === "applied" ? "done" : ""}>applied</span></div><footer><span><small>Target</small><code>{intent.target.runtime} / {intent.target.port}</code></span><span><small>Artifact</small><code>{shortHash(intent.model_artifact_sha256)}</code></span><span><small>CI evidence</small><code>{shortHash(intent.ci_evidence_sha256)}</code></span>{intent.state === "applied" ? <button type="button" className="secondary-action" disabled={Boolean(busyAction)} onClick={() => mutate("rollback", () => rollbackScenarioProductionIntent(intent.intent_id, { actor: approver.trim(), reason: reason.trim() }), "Rollback to the known-good B0 holder queued.")}><RotateCcw size={15} />Rollback to B0</button> : null}</footer>{intent.blockers.length ? <div className="intent-blockers">{intent.blockers.map((blocker) => <code key={blocker}>{blocker}</code>)}</div> : null}</section>;
}


function intentStateReached(current: ScenarioProductionIntent["state"], target: "pending_approval" | "queued" | "applying") {
  const order = ["pending_approval", "queued", "applying", "applied"];
  return order.indexOf(current) >= order.indexOf(target);
}


function EvaluationPanel({ run }: { run: ScenarioWorkloadRun }) {
  const summary = run.evaluation_summary;
  if (!summary) return <section className="scenario-evaluation scenario-evaluation-empty" aria-label="Evaluation metrics unavailable"><Gauge size={18} /><div><strong>Evaluation metrics unavailable</strong><span>{run.evaluation_uri ? "Evidence could not be resolved" : "Evaluation has not completed"}</span></div></section>;
  return <section className="scenario-evaluation" aria-label={`${summary.model_family.toUpperCase()} evaluation metrics`}><header><div><span className="eyebrow">{summary.model_family.toUpperCase()} metric schema</span><h3>Evaluation and release evidence</h3></div><div className="scenario-gate-state" title={summary.release_gate.policy_source}><ShieldCheck size={16} /><span>Release gate</span><StatusBadge status={summary.release_gate.status} compact /></div></header><div className="scenario-metric-groups"><MetricGroup label="Model quality" metrics={summary.quality_metrics} emptyLabel="No supported quality metric" /><MetricGroup label="Runtime and evaluation" metrics={summary.operational_metrics} emptyLabel="No runtime metric" /></div><footer><code>{summary.schema_version}</code><span>{summary.evaluated_at ? `evaluated ${formatTime(summary.evaluated_at)}` : "evaluation timestamp unavailable"}</span>{summary.release_gate.blockers.length ? <strong>{summary.release_gate.blockers.join(", ")}</strong> : <strong>policy evidence passed</strong>}</footer></section>;
}


function MetricGroup({ label, metrics, emptyLabel }: { label: string; metrics: Record<string, number>; emptyLabel: string }) {
  const entries = Object.entries(metrics);
  return <section className="scenario-metric-group" aria-label={label}><span>{label}</span><div>{entries.map(([name, value]) => <div className="scenario-metric" key={name} title={metricDefinition(name)}><small>{metricLabel(name)}</small><strong>{formatMetric(name, value)}</strong></div>)}{!entries.length ? <em>{emptyLabel}</em> : null}</div></section>;
}


function StageNode({ stage }: { stage: ScenarioWorkloadStage }) {
  return <div className={`scenario-stage state-${stage.state}`} title={stage.detail || stage.label}><i /><span>{stage.label}</span><small>{stage.state.replaceAll("_", " ")}</small></div>;
}


function Kpi({ icon: Icon, label, value, tone = "idle", active, onClick }: { icon: typeof Activity; label: string; value: number; tone?: string; active: boolean; onClick: () => void }) {
  return <button type="button" className={`scenario-kpi tone-${tone} ${active ? "active" : ""}`} aria-label={`Show ${label.toLowerCase()} workloads`} aria-pressed={active} onClick={onClick}><Icon size={18} /><span><small>{label}</small><strong>{value}</strong></span></button>;
}


function Fact({ icon: Icon, label, value, detail }: { icon: typeof Activity; label: string; value: string; detail: string }) {
  return <div className="scenario-fact"><Icon size={18} /><span><small>{label}</small><strong>{value}</strong><code>{detail}</code></span></div>;
}


function shortHash(value: string): string { return value.length > 16 ? `${value.slice(0, 8)}...${value.slice(-6)}` : value; }
function memoryLabel(run: ScenarioWorkloadRun): string { return run.peak_gpu_allocated_mib === null || run.peak_gpu_allocated_mib === undefined ? "GPU profile pending" : `${(run.peak_gpu_allocated_mib / 1024).toFixed(2)} GiB peak allocated`; }
function metricLabel(name: string): string { return ({ accuracy: "Accuracy", parse_rate: "Parse rate", validation_loss: "Validation loss", mean_token_f1: "Token F1", nonempty_rate: "Non-empty rate", p95_latency_seconds: "P95 latency", evaluated_records: "Evaluated", peak_gpu_allocated_mib: "Peak VRAM", training_seconds: "Training time" } as Record<string, string>)[name] || name.replaceAll("_", " "); }
function metricDefinition(name: string): string { return ({ accuracy: "Correct VLM choices divided by held-out records.", parse_rate: "Parseable VLM answers divided by generated held-out answers.", validation_loss: "Cross-entropy loss measured on held-out validation records.", mean_token_f1: "Mean token-overlap F1 across generated LLM answers.", nonempty_rate: "Non-empty LLM generations divided by evaluated prompts.", p95_latency_seconds: "95th percentile generation latency on held-out evaluation.", evaluated_records: "Held-out records used by the recorded evaluation.", peak_gpu_allocated_mib: "Peak CUDA memory allocated during adaptation.", training_seconds: "Measured bounded adaptation wall time." } as Record<string, string>)[name] || "Metric recorded by the workload evaluation artifact."; }
function formatMetric(name: string, value: number): string { if (["accuracy", "parse_rate", "mean_token_f1", "nonempty_rate"].includes(name)) return `${(value * 100).toFixed(1)}%`; if (name === "p95_latency_seconds") return `${value.toFixed(2)} s`; if (name === "peak_gpu_allocated_mib") return `${(value / 1024).toFixed(2)} GiB`; if (name === "training_seconds") return `${value.toFixed(1)} s`; if (name === "evaluated_records") return String(Math.round(value)); return value.toFixed(3); }
function formatTime(value: string): string { return new Date(value).toLocaleString([], { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
