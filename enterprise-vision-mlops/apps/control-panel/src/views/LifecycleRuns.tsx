import {
  Activity,
  BarChart3,
  CheckCircle2,
  CircleDashed,
  Clock3,
  FileWarning,
  Gauge,
  GitBranch,
  Play,
  RefreshCcw,
  RotateCcw,
  ShieldCheck,
  Square,
  Wrench,
  XCircle
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  approveLifecycleRun,
  fetchExperimentRun,
  fetchLifecycleRuns,
  fetchLifecycleWorker,
  transitionLifecycleRun
} from "../api/controlPanelClient";
import type {
  LifecycleRun,
  LifecycleRunState,
  LifecycleStage,
  LifecycleStageState,
  LifecycleWorkerState,
  ExperimentRun,
  ExperimentTrainingTelemetry
} from "../api/types";


interface LifecycleRunsProps {
  onCycleContext?: (run: LifecycleRun) => void;
  onOpenBlueprint?: (run: LifecycleRun) => void;
}


export function LifecycleRuns({ onCycleContext, onOpenBlueprint }: LifecycleRunsProps = {}) {
  const [runs, setRuns] = useState<LifecycleRun[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const selectedRef = useRef("");
  const [worker, setWorker] = useState<LifecycleWorkerState>({ status: "offline" });
  const [experiment, setExperiment] = useState<ExperimentRun | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [syncedAt, setSyncedAt] = useState("");
  const [approver, setApprover] = useState("ai-infra-sre");

  async function load() {
    const [runResult, workerResult] = await Promise.allSettled([
      fetchLifecycleRuns(),
      fetchLifecycleWorker()
    ]);
    let experimentId = "";
    if (runResult.status === "fulfilled") {
      setRuns(runResult.value.runs);
      const selectedExists = runResult.value.runs.some(
        (run) => run.run_id === selectedRef.current
      );
      let experimentRun: LifecycleRun | undefined;
      if (!selectedExists) {
        const nextRun = runResult.value.runs[0];
        const next = nextRun?.run_id || "";
        experimentRun = nextRun;
        selectedRef.current = next;
        setSelectedId(next);
        if (nextRun?.cycle_id) onCycleContext?.(nextRun);
      } else {
        experimentRun = runResult.value.runs.find(
          (run) => run.run_id === selectedRef.current
        );
      }
      experimentId = experimentRun?.experiment_id || "";
      setError("");
    } else {
      setError(message(runResult.reason));
    }
    if (workerResult.status === "fulfilled") setWorker(workerResult.value);
    if (experimentId) {
      try {
        setExperiment(await fetchExperimentRun(experimentId));
      } catch (reason) {
        setError(message(reason));
      }
    } else {
      setExperiment(null);
    }
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
  const retryStage = useMemo(() => {
    if (!selected || !["blocked", "failed"].includes(selected.state)) return null;
    return selected.stages.find((stage) => stage.stage_id === selected.current_stage)
      || selected.stages.find((stage) => stage.state === "blocked" || stage.state === "failed")
      || null;
  }, [selected]);
  const qualityRevisionRequired = experiment?.quality_review?.state === "review_required";
  const canRetry = Boolean(
    retryStage
    && retryStage.attempt < retryStage.max_attempts
    && !qualityRevisionRequired
  );

  function select(runId: string) {
    selectedRef.current = runId;
    setSelectedId(runId);
    const run = runs.find((item) => item.run_id === runId);
    if (run?.cycle_id) onCycleContext?.(run);
    if (run?.experiment_id) {
      void fetchExperimentRun(run.experiment_id)
        .then(setExperiment)
        .catch((reason) => setError(message(reason)));
    } else {
      setExperiment(null);
    }
  }

  async function runAction(action: "queue" | "continue" | "cancel" | "retry") {
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
                <Metadata label="Execution Mode" value={selected.execution_mode} />
                <Metadata label="Requested By" value={selected.actor} />
                <Metadata label="Config Digest" value={selected.effective_config_digest.slice(0, 16)} />
                <Metadata label="Source Commit" value={selected.source_commit?.slice(0, 12) || "not captured"} />
                <Metadata label="Cycle Context" value={selected.cycle_id || "snapshot pending"} />
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
                {selected.state === "paused" ? (
                  <button
                    type="button"
                    className="primary-action"
                    disabled={busy || worker.status !== "online"}
                    onClick={() => void runAction("continue")}
                  >
                    <Play size={16} /> Run Next Stage
                  </button>
                ) : null}
                {selected.state === "blocked" || selected.state === "failed" ? (
                  <button
                    type="button"
                    className="secondary-action"
                    disabled={busy || !canRetry}
                    onClick={() => void runAction("retry")}
                    title={
                      qualityRevisionRequired
                        ? "This quality regression requires a revised Blueprint"
                        : canRetry
                          ? "Retry the failed stage"
                          : "This stage exhausted its retry policy"
                    }
                  >
                    <RotateCcw size={16} /> {
                      qualityRevisionRequired
                        ? "Blueprint Revision Required"
                        : canRetry
                          ? "Retry Stage"
                          : "Retry Exhausted"
                    }
                  </button>
                ) : null}
                {isCancellable(selected.state) ? (
                  <button type="button" className="danger-action" disabled={busy} onClick={() => void runAction("cancel")}>
                    <Square size={15} /> Cancel
                  </button>
                ) : null}
              </div>
            </section>

            {experiment?.lifecycle_run_id === selected.run_id ? (
              <ExperimentProgress
                run={experiment}
                onOpenBlueprint={
                  onOpenBlueprint ? () => onOpenBlueprint(selected) : undefined
                }
              />
            ) : null}

            <section className="panel lifecycle-stage-panel">
              <div className="panel-heading">
                <div><h2>Execution Stages</h2><p>dependency and runtime state</p></div>
                <Clock3 />
              </div>
              <div className="lifecycle-stage-list">
                {selected.stages.map((stage, index) => (
                  <LifecycleStageRow
                    key={stage.stage_id}
                    stage={stage}
                    index={index}
                    progressOverride={
                      stage.stage_id === "model_training"
                      && experiment?.lifecycle_run_id === selected.run_id
                        ? experiment.progress
                        : undefined
                    }
                  />
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


function ExperimentProgress({
  run,
  onOpenBlueprint
}: {
  run: ExperimentRun;
  onOpenBlueprint?: () => void;
}) {
  const metric = `${run.primary_metric}_mean`;
  const ranked = run.trials
    .filter((trial) => trial.state === "completed")
    .slice()
    .sort((left, right) => (right.aggregate_metrics[metric] || 0) - (left.aggregate_metrics[metric] || 0));
  return (
    <section className={`panel experiment-progress experiment-${run.state}`} aria-label="Cross-validation and hyperparameter search progress">
      <div className="panel-heading">
        <div><h2>Experiment Search</h2><p>{run.mode} / {run.folds}-fold / seed {run.seed}</p></div>
        <BarChart3 />
      </div>
      <div className="experiment-progress-summary">
        <div><span>State</span><strong>{run.state.replaceAll("_", " ")}</strong></div>
        <div><span>Units</span><strong>{run.completed_units}/{run.total_units}</strong></div>
        <div><span>MLflow Parent</span><strong title={run.parent_mlflow_run_id || "pending"}>{run.parent_mlflow_run_id?.slice(0, 12) || "pending"}</strong></div>
        <div><span>Selected</span><strong>{run.selected_trial_id || "pending"}</strong></div>
      </div>
      <div className={`experiment-progress-bar state-${run.state}`} role="progressbar" aria-label="Experiment search progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(run.progress * 100)}>
        <b style={{ width: `${Math.round(run.progress * 100)}%` }} />
      </div>
      {run.training_telemetry ? <LiveTrainingTelemetry telemetry={run.training_telemetry} /> : null}
      {ranked.length ? (
        <div className="experiment-trial-matrix">
          {ranked.slice(0, 4).map((trial, index) => (
            <details key={trial.trial_id} className={trial.trial_id === run.selected_trial_id ? "selected" : ""}>
              <summary>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div><strong>{trial.trial_id}</strong><small>{trial.folds.length} fold results</small></div>
                <em>{((trial.aggregate_metrics[metric] || 0) * 100).toFixed(2)}%</em>
              </summary>
              <div className="experiment-trial-detail">
                <dl>
                  {Object.entries(trial.parameters).map(([name, value]) => (
                    <div key={name}><dt>{name.replaceAll("_", " ")}</dt><dd>{formatExperimentValue(value)}</dd></div>
                  ))}
                </dl>
                <div className="experiment-fold-list">
                  {trial.folds.map((fold) => (
                    <div key={`${fold.repeat}-${fold.fold}`} aria-label={`${trial.trial_id} fold ${fold.fold + 1} metrics`}>
                      <strong>F{fold.fold + 1}</strong>
                      <span>{fold.train_records.toLocaleString()} / {fold.validation_records.toLocaleString()} rows</span>
                      <span>ACC {formatExperimentMetric(fold.metrics.accuracy)}</span>
                      <span>F1 {formatExperimentMetric(fold.metrics.f1)}</span>
                      <span>AUROC {formatExperimentMetric(fold.metrics.auroc)}</span>
                      <code title={fold.mlflow_run_id || "MLflow run pending"}>{fold.mlflow_run_id?.slice(0, 8) || "pending"}</code>
                    </div>
                  ))}
                </div>
              </div>
            </details>
          ))}
        </div>
      ) : (
        <div className="experiment-awaiting"><GitBranch size={17} /><span>Fold evidence will appear after the first completed trial.</span></div>
      )}
      {run.quality_review ? (
        <section className="model-quality-review" aria-label="Model quality regression review">
          <header>
            <div>
              <span>Promotion Review</span>
              <strong>{run.quality_review.event_type.replaceAll("_", " ")}</strong>
            </div>
            <em>{run.quality_review.state.replaceAll("_", " ")}</em>
          </header>
          <div className="quality-gate-matrix">
            {run.quality_review.failed_gates.map((gate) => {
              const metric = gate.split(/[<>]/)[0];
              return (
                <div key={gate}>
                  <span>{metric.toUpperCase()}</span>
                  <strong>{formatExperimentMetric(run.quality_review?.observed_metrics[metric])}</strong>
                  <small>policy {formatExperimentMetric(run.quality_review?.policy_thresholds[metric])}</small>
                </div>
              );
            })}
          </div>
          <div className="quality-remediation">
            <div>
              <span>Required revision</span>
              {run.quality_review.recommendations.map((item) => (
                <code key={item}>{humanizeRecommendation(item)}</code>
              ))}
            </div>
            {onOpenBlueprint ? (
              <button type="button" className="secondary-action" onClick={onOpenBlueprint}>
                <Wrench size={15} /> Tune Blueprint
              </button>
            ) : null}
          </div>
          <footer>
            <span>Same-profile retry locked</span>
            <code title={run.quality_review.fingerprint}>{run.quality_review.fingerprint.slice(0, 16)}</code>
          </footer>
        </section>
      ) : null}
      {run.blockers.length ? <details className="experiment-blockers"><summary>{run.blockers.length} experiment blocker{run.blockers.length === 1 ? "" : "s"}</summary>{run.blockers.map((item) => <code key={item}>{item}</code>)}</details> : null}
    </section>
  );
}


function LiveTrainingTelemetry({ telemetry }: { telemetry: ExperimentTrainingTelemetry }) {
  const percent = Math.round(telemetry.unit_progress * 100);
  const phase = telemetry.phase.replaceAll("_", " ");
  const unit = telemetry.unit_role === "final_refit"
    ? "Final refit"
    : `${telemetry.trial_id || "trial"} / fold ${(telemetry.fold || 0) + 1}`;
  return (
    <section className="training-telemetry" aria-label="Live training step telemetry">
      <header>
        <div><Gauge size={16} /><strong>{unit}</strong></div>
        <span className={`telemetry-phase phase-${telemetry.phase}`}>{phase}</span>
        <em>{percent}%</em>
      </header>
      <div className="telemetry-track" role="progressbar" aria-label="Current training unit progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
        <b style={{ width: `${percent}%` }} />
      </div>
      <div className="telemetry-stats">
        <div><span>Epoch</span><strong>{telemetry.epoch}/{telemetry.epochs || "-"}</strong></div>
        <div><span>Step</span><strong>{telemetry.step}/{telemetry.steps || "-"}</strong></div>
        <div><span>Optimizer</span><strong>{telemetry.optimizer_steps.toLocaleString()}</strong></div>
        <div><span>Loss</span><strong>{telemetry.train_loss == null ? "-" : telemetry.train_loss.toFixed(4)}</strong></div>
        <div><span>Val F1</span><strong>{formatExperimentMetric(telemetry.validation_metrics.f1)}</strong></div>
      </div>
    </section>
  );
}


function LifecycleStageRow({
  stage,
  index,
  progressOverride
}: {
  stage: LifecycleStage;
  index: number;
  progressOverride?: number;
}) {
  const progress = stageProgressPercent(stage, progressOverride);
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
        <span>{stage.runtime} / {stageRuntimeSummary(stage)}</span>
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
        <span>{stageStatusDetail(stage, timing)}</span>
        {timing && (stage.state === "blocked" || stage.state === "failed") ? <small>{timing}</small> : null}
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


function stageRuntimeSummary(stage: LifecycleStage): string {
  if (stage.stage_id === "approval" && stage.state === "completed") return "approved";
  return stage.runtime_state || stage.detail || "Waiting for dependency";
}


function stateLabel(state: LifecycleRunState): string {
  const labels: Record<LifecycleRunState, string> = {
    dry_run: "Dry Run Ready",
    queued: "Queued",
    running: "In Progress",
    paused: "Ready for Next Stage",
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
  return ["queued", "running", "paused", "waiting_approval"].includes(state);
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


function stageProgressPercent(stage: LifecycleStage, progressOverride?: number): number {
  if (stage.state === "completed") return 100;
  const progress = progressOverride ?? stage.progress;
  return Math.max(0, Math.min(100, Math.round(progress * 100)));
}


function formatExperimentMetric(value: number | undefined): string {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "n/a";
}


function formatExperimentValue(value: string | number | boolean): string {
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number" && value > 0 && value < 0.01) return value.toExponential(1);
  return String(value);
}


function humanizeRecommendation(value: string): string {
  return value.replaceAll("_", " ");
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
  if (stage.state === "completed" && !stage.attempt) return "Completed";
  if (!stage.attempt) return stage.state === "not_started" ? "Not started" : "No attempt";
  if (stage.state === "blocked" || stage.state === "failed") {
    const retriesLeft = Math.max(0, stage.max_attempts - stage.attempt);
    return retriesLeft ? `${retriesLeft} ${retriesLeft === 1 ? "retry" : "retries"} left` : "No retries left";
  }
  return `Attempt ${stage.attempt}`;
}


function stageStatusDetail(stage: LifecycleStage, timing: string): string {
  if (stage.state === "blocked" || stage.state === "failed") return stageAttemptLabel(stage);
  return timing || stageAttemptLabel(stage);
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
