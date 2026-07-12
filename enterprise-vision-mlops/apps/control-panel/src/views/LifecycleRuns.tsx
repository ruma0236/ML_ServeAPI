import {
  Activity,
  CheckCircle2,
  CircleDashed,
  Clock3,
  FileWarning,
  Play,
  RefreshCcw,
  RotateCcw,
  ShieldCheck,
  Square,
  XCircle
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  approveLifecycleRun,
  fetchLifecycleRuns,
  fetchLifecycleWorker,
  transitionLifecycleRun
} from "../api/controlPanelClient";
import type {
  LifecycleRun,
  LifecycleRunState,
  LifecycleStage,
  LifecycleStageState,
  LifecycleWorkerState
} from "../api/types";


export function LifecycleRuns() {
  const [runs, setRuns] = useState<LifecycleRun[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const selectedRef = useRef("");
  const [worker, setWorker] = useState<LifecycleWorkerState>({ status: "offline" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [syncedAt, setSyncedAt] = useState("");
  const [approver, setApprover] = useState("ai-infra-sre");

  async function load() {
    const [runResult, workerResult] = await Promise.allSettled([
      fetchLifecycleRuns(),
      fetchLifecycleWorker()
    ]);
    if (runResult.status === "fulfilled") {
      setRuns(runResult.value.runs);
      const selectedExists = runResult.value.runs.some(
        (run) => run.run_id === selectedRef.current
      );
      if (!selectedExists) {
        const next = runResult.value.runs[0]?.run_id || "";
        selectedRef.current = next;
        setSelectedId(next);
      }
      setError("");
    } else {
      setError(message(runResult.reason));
    }
    if (workerResult.status === "fulfilled") setWorker(workerResult.value);
    setSyncedAt(new Date().toLocaleTimeString());
  }

  useEffect(() => {
    void load();
    const interval = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(interval);
  }, []);

  const selected = useMemo(
    () => runs.find((run) => run.run_id === selectedId) || runs[0] || null,
    [runs, selectedId]
  );
  const selectedBlockers = useMemo(() => {
    if (!selected) return [];
    const blockers = [...selected.blockers];
    if (!selected.source_commit) blockers.push("source_revision_missing");
    return [...new Set(blockers)];
  }, [selected]);

  function select(runId: string) {
    selectedRef.current = runId;
    setSelectedId(runId);
  }

  async function runAction(action: "queue" | "cancel" | "retry") {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const updated = await transitionLifecycleRun(selected.run_id, action, {
        actor: selected.actor,
        reason: `${action} requested from Control Panel`,
        expected_version: selected.version
      });
      replaceRun(updated);
      await load();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const updated = await approveLifecycleRun(selected.run_id, {
        actor: approver,
        approver,
        reason: "Independent lifecycle release approval",
        expected_version: selected.version
      });
      replaceRun(updated);
      await load();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  function replaceRun(updated: LifecycleRun) {
    setRuns((current) => current.map((run) => run.run_id === updated.run_id ? updated : run));
  }

  return (
    <section className="lifecycle-console" aria-label="Lifecycle runs">
      <header className="lifecycle-header panel wide">
        <div>
          <span className="eyebrow">Persistent Execution Ledger</span>
          <h2>Lifecycle Runs</h2>
          <p>{runs.length} runs / synced {syncedAt || "pending"}</p>
        </div>
        <div className={`worker-health worker-${worker.status}`}>
          <i />
          <div>
            <span>Host Worker</span>
            <strong>{workerLabel(worker)}</strong>
            <small>{worker.current_run_id || worker.worker_id || "No heartbeat"}</small>
          </div>
          <button type="button" className="icon-button" onClick={() => void load()} title="Refresh lifecycle ledger" aria-label="Refresh lifecycle ledger">
            <RefreshCcw size={16} />
          </button>
        </div>
      </header>

      {error ? <div className="policy-error" role="alert">{error}</div> : null}

      <div className="lifecycle-layout">
        <aside className="panel lifecycle-run-list" aria-label="Lifecycle run history">
          <div className="panel-heading">
            <div><h2>Run History</h2><p>immutable profile executions</p></div>
            <Activity />
          </div>
          <div className="lifecycle-run-rows">
            {runs.map((run) => (
              <button
                type="button"
                key={run.run_id}
                className={run.run_id === selected?.run_id ? "active" : ""}
                onClick={() => select(run.run_id)}
              >
                <span className={`lifecycle-state-dot state-${run.state}`} />
                <div>
                  <strong>{run.profile_id} / v{run.profile_version}</strong>
                  <small>{shortId(run.run_id)} / {stateLabel(run.state)}</small>
                </div>
                <em>{Math.round(run.progress * 100)}%</em>
              </button>
            ))}
            {!runs.length ? <div className="lifecycle-empty">No lifecycle runs</div> : null}
          </div>
        </aside>

        {selected ? (
          <div className="lifecycle-detail">
            <section className="panel lifecycle-summary">
              <div className="lifecycle-summary-main">
                <div>
                  <span>{selected.run_id}</span>
                  <h2>{stateLabel(selected.state)}</h2>
                  <p>{selected.reason}</p>
                </div>
                <strong>{Math.round(selected.progress * 100)}%</strong>
              </div>
              <div className={`lifecycle-progress state-${selected.state}`} aria-label={`Lifecycle progress ${Math.round(selected.progress * 100)}%`}>
                <b style={{ width: `${Math.round(selected.progress * 100)}%` }} />
              </div>
              <div className="lifecycle-metadata">
                <Metadata label="Current Stage" value={currentStageLabel(selected)} />
                <Metadata label="Requested By" value={selected.actor} />
                <Metadata label="Config Digest" value={selected.effective_config_digest.slice(0, 16)} />
                <Metadata label="Source Commit" value={selected.source_commit?.slice(0, 12) || "not captured"} />
              </div>
              <div className="lifecycle-actions">
                {selected.state === "dry_run" ? (
                  <button
                    type="button"
                    className="primary-action"
                    disabled={busy || worker.status !== "online" || !selected.source_commit}
                    onClick={() => void runAction("queue")}
                    title={selected.source_commit ? "Queue this immutable lifecycle snapshot" : "Source commit is required before queueing"}
                  >
                    <Play size={16} /> Queue Run
                  </button>
                ) : null}
                {selected.state === "waiting_approval" ? (
                  <>
                    <label><span>Independent Approver</span><input value={approver} onChange={(event) => setApprover(event.target.value)} /></label>
                    <button type="button" className="primary-action" disabled={busy || approver.length < 2} onClick={() => void approve()}>
                      <ShieldCheck size={16} /> Approve
                    </button>
                  </>
                ) : null}
                {selected.state === "blocked" || selected.state === "failed" ? (
                  <button type="button" className="secondary-action" disabled={busy} onClick={() => void runAction("retry")}>
                    <RotateCcw size={16} /> Retry Stage
                  </button>
                ) : null}
                {isCancellable(selected.state) ? (
                  <button type="button" className="danger-action" disabled={busy} onClick={() => void runAction("cancel")}>
                    <Square size={15} /> Cancel
                  </button>
                ) : null}
              </div>
            </section>

            <section className="panel lifecycle-stage-panel">
              <div className="panel-heading">
                <div><h2>Execution Stages</h2><p>dependency and runtime state</p></div>
                <Clock3 />
              </div>
              <div className="lifecycle-stage-list">
                {selected.stages.map((stage, index) => (
                  <LifecycleStageRow key={stage.stage_id} stage={stage} index={index} />
                ))}
              </div>
            </section>

            {(selectedBlockers.length || selected.failure_reason) ? (
              <details className="panel lifecycle-failure" open>
                <summary><FileWarning size={16} /> Blocked / Failure Evidence</summary>
                {selected.failure_reason ? <strong>{selected.failure_reason}</strong> : null}
                {selectedBlockers.map((blocker) => <code key={blocker}>{blocker}</code>)}
              </details>
            ) : null}

            <details className="panel lifecycle-evidence">
              <summary>Run Evidence And Audit</summary>
              <Evidence label="Profile" value={selected.profile_snapshot_uri} />
              <Evidence label="Airflow Config" value={selected.airflow_config_uri} />
              <Evidence label="Model Config" value={selected.model_config_uri} />
              <Evidence label="Model Matrix" value={selected.model_matrix_uri} />
              <Evidence label="Readiness" value={selected.readiness_uri} />
              <Evidence label="GPU Handoff" value={selected.resource_handoff_uri} />
              <Evidence label="Deployment Intent" value={selected.deployment_intent_id} />
              <div className="lifecycle-audit-list">
                {selected.audit.slice().reverse().map((event, index) => (
                  <div key={`${event.timestamp}-${event.event}-${index}`}>
                    <time>{formatTime(event.timestamp)}</time>
                    <strong>{event.event.replaceAll("_", " ")}</strong>
                    <span>{event.actor}</span>
                  </div>
                ))}
              </div>
            </details>
          </div>
        ) : null}
      </div>
    </section>
  );
}


function LifecycleStageRow({ stage, index }: { stage: LifecycleStage; index: number }) {
  const progress = stageProgressPercent(stage);
  const timing = stageTiming(stage);
  return (
    <article
      className={`lifecycle-stage-row stage-${stage.state}`}
      aria-label={`${stage.label}: ${stageStateLabel(stage.state)}`}
    >
      <div className="lifecycle-stage-index">{String(index + 1).padStart(2, "0")}</div>
      <div className="lifecycle-stage-icon">{stageIcon(stage.state)}</div>
      <div className="lifecycle-stage-copy">
        <header><strong>{stage.label}</strong><em>{stageStateLabel(stage.state)}</em></header>
        <span>{stage.runtime} / {stage.runtime_state || stage.detail || "Waiting for dependency"}</span>
        <div
          className={`lifecycle-stage-progress is-${stage.state}`}
          role="progressbar"
          aria-label={`${stage.label} progress`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress}
          aria-valuetext={`${stageStateLabel(stage.state)}, ${progress}%`}
        >
          <b style={{ width: `${progress}%` }} />
        </div>
        {stage.blockers.length ? (
          <details><summary>{stage.blockers.length} blocker{stage.blockers.length === 1 ? "" : "s"}</summary>{stage.blockers.map((item) => <code key={item}>{item}</code>)}</details>
        ) : null}
      </div>
      <div className="lifecycle-stage-status" title={stageAttemptTitle(stage)}>
        <strong>{stageStatusMetric(stage, progress)}</strong>
        <span>{timing || stageAttemptLabel(stage)}</span>
      </div>
    </article>
  );
}


function Metadata({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong title={value}>{value}</strong></div>;
}


function Evidence({ label, value }: { label: string; value?: string | null }) {
  return <div className="lifecycle-evidence-row"><span>{label}</span><code title={value || "Not available"}>{value || "Not available"}</code></div>;
}


function stageIcon(state: LifecycleStageState) {
  if (state === "completed") return <CheckCircle2 />;
  if (state === "running") return <Activity />;
  if (state === "blocked" || state === "failed") return <XCircle />;
  if (state === "waiting_approval") return <ShieldCheck />;
  return <CircleDashed />;
}


function stageStateLabel(state: LifecycleStageState): string {
  const labels: Record<LifecycleStageState, string> = {
    not_started: "Not Started",
    queued: "Queued",
    running: "In Progress",
    waiting_approval: "Approval Required",
    blocked: "Blocked",
    failed: "Failed",
    completed: "Completed",
    skipped: "Skipped",
    cancelled: "Cancelled"
  };
  return labels[state];
}


function stateLabel(state: LifecycleRunState): string {
  const labels: Record<LifecycleRunState, string> = {
    dry_run: "Dry Run Ready",
    queued: "Queued",
    running: "In Progress",
    waiting_approval: "Approval Required",
    blocked: "Blocked",
    failed: "Failed",
    completed: "Completed",
    cancelled: "Cancelled",
    rolling_back: "Rolling Back",
    rolled_back: "Rolled Back"
  };
  return labels[state];
}


function workerLabel(worker: LifecycleWorkerState): string {
  if (worker.status === "online") return worker.current_run_id ? "Processing" : "Online";
  if (worker.status === "stale") return "Heartbeat Stale";
  return "Offline";
}


function isCancellable(state: LifecycleRunState): boolean {
  return ["queued", "running", "waiting_approval", "blocked", "failed"].includes(state);
}


function shortId(value: string): string {
  return value.length > 30 ? `${value.slice(0, 18)}...${value.slice(-8)}` : value;
}


function currentStageLabel(run: LifecycleRun): string {
  if (run.current_stage) return run.current_stage.replaceAll("_", " ");
  if (run.state === "dry_run") return "Ready to Queue";
  if (["completed", "cancelled", "rolled_back"].includes(run.state)) return "Terminal";
  return "Waiting for Worker";
}


function stageProgressPercent(stage: LifecycleStage): number {
  if (stage.state === "completed") return 100;
  return Math.max(0, Math.min(100, Math.round(stage.progress * 100)));
}


function stageStatusMetric(stage: LifecycleStage, progress: number): string {
  if (stage.state === "completed") return "100%";
  if (stage.state === "running") return progress ? `${progress}%` : "Live";
  if (stage.state === "waiting_approval") return "Waiting";
  if (stage.state === "queued") return "Queued";
  if (stage.state === "blocked" || stage.state === "failed") return "Stopped";
  if (stage.state === "skipped") return "Skipped";
  if (stage.state === "cancelled") return "Cancelled";
  return "Pending";
}


function stageAttemptLabel(stage: LifecycleStage): string {
  if (!stage.attempt) return stage.state === "not_started" ? "Not started" : "No attempt";
  if (stage.state === "blocked" || stage.state === "failed") {
    const retriesLeft = Math.max(0, stage.max_attempts - stage.attempt);
    return retriesLeft ? `${retriesLeft} ${retriesLeft === 1 ? "retry" : "retries"} left` : "No retries left";
  }
  return `Attempt ${stage.attempt}`;
}


function stageAttemptTitle(stage: LifecycleStage): string {
  if (!stage.attempt) return `No attempt started; maximum ${stage.max_attempts}`;
  return `Attempt ${stage.attempt}; maximum ${stage.max_attempts}`;
}


function stageTiming(stage: LifecycleStage): string {
  if (!stage.started_at) return "";
  const startedAt = Date.parse(stage.started_at);
  const finishedAt = stage.finished_at ? Date.parse(stage.finished_at) : Date.now();
  if (!Number.isFinite(startedAt) || !Number.isFinite(finishedAt) || finishedAt < startedAt) return "";
  return formatDuration(Math.max(0, finishedAt - startedAt));
}


function formatDuration(milliseconds: number): string {
  const seconds = Math.round(milliseconds / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes < 60) return remainingSeconds ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}


function formatTime(value: string): string {
  return new Date(value).toLocaleString();
}


function message(error: unknown): string {
  return error instanceof Error ? error.message : "Lifecycle request failed";
}
