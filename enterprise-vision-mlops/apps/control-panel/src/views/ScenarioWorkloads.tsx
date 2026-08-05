import {
  Activity,
  Box,
  Cpu,
  Database,
  ExternalLink,
  FileCheck2,
  Gauge,
  RefreshCcw,
  ShieldCheck,
  TriangleAlert
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { fetchScenarioWorkloads } from "../api/controlPanelClient";
import type {
  ScenarioWorkloadRun,
  ScenarioWorkloadRunState,
  ScenarioWorkloadStage
} from "../api/types";
import { StatusBadge } from "../components/StatusBadge";


const activeStates: ScenarioWorkloadRunState[] = ["queued", "running", "waiting_approval"];
type WorkloadFilter = "all" | "active" | "completed" | "attention";


export function ScenarioWorkloads() {
  const [runs, setRuns] = useState<ScenarioWorkloadRun[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const selectedRef = useRef("");
  const [error, setError] = useState("");
  const [syncedAt, setSyncedAt] = useState("");
  const [filter, setFilter] = useState<WorkloadFilter>("all");

  async function load() {
    try {
      const result = await fetchScenarioWorkloads();
      setRuns(result.runs);
      if (!result.runs.some((run) => run.run_id === selectedRef.current)) {
        const next = result.runs[0]?.run_id || "";
        selectedRef.current = next;
        setSelectedId(next);
      }
      setError("");
      setSyncedAt(new Date().toLocaleTimeString());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Scenario workload ledger unavailable");
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
  }), [filter, runs]);
  const selected = useMemo(
    () => visibleRuns.find((run) => run.run_id === selectedId) || visibleRuns[0] || null,
    [selectedId, visibleRuns]
  );
  const completed = runs.filter((run) => run.state === "completed").length;
  const active = runs.filter((run) => activeStates.includes(run.state)).length;
  const attention = runs.filter((run) => ["failed", "blocked"].includes(run.state)).length;

  function select(runId: string) {
    selectedRef.current = runId;
    setSelectedId(runId);
  }

  return (
    <section className="scenario-workloads" aria-label="AI scenario workloads">
      <header className="scenario-workload-header">
        <div>
          <span className="eyebrow">Real Transformer Execution Ledger</span>
          <h2>AI Workloads</h2>
          <p>VLM and LLM runs across intake, adaptation, staging inference, and observability.</p>
        </div>
        <div className="scenario-sync">
          <span className={error ? "sync-dot error" : "sync-dot live"} />
          <small>{error || `Live / ${syncedAt || "connecting"}`}</small>
          <button type="button" className="icon-button" onClick={() => void load()} title="Refresh workloads" aria-label="Refresh workloads">
            <RefreshCcw size={16} />
          </button>
        </div>
      </header>

      <div className="scenario-kpis" role="group" aria-label="Workload status filter">
        <Kpi icon={Box} label="Runs" value={runs.length} active={filter === "all"} onClick={() => setFilter("all")} />
        <Kpi icon={Activity} label="Active" value={active} tone="run" active={filter === "active"} onClick={() => setFilter("active")} />
        <Kpi icon={FileCheck2} label="Completed" value={completed} tone="good" active={filter === "completed"} onClick={() => setFilter("completed")} />
        <Kpi icon={TriangleAlert} label="Attention" value={attention} tone={attention ? "bad" : "idle"} active={filter === "attention"} onClick={() => setFilter("attention")} />
      </div>

      <div className="scenario-workload-layout">
        <aside className="scenario-run-list" aria-label="AI workload history">
          {visibleRuns.map((run) => (
            <button
              type="button"
              key={run.run_id}
              className={run.run_id === selected?.run_id ? "active" : ""}
              onClick={() => select(run.run_id)}
            >
              <span className={`family-mark ${run.identity.model_family}`}>{run.identity.model_family.toUpperCase()}</span>
              <span>
                <strong>{run.identity.model_repository.split("/").at(-1)}</strong>
                <small>{formatTime(run.created_at)} / {Math.round(run.progress * 100)}%</small>
              </span>
              <StatusBadge status={run.state} />
            </button>
          ))}
          {!visibleRuns.length ? <div className="scenario-empty">No {filter === "all" ? "governed" : filter} transformer run is available.</div> : null}
        </aside>

        {selected ? <WorkloadDetail run={selected} /> : null}
      </div>
    </section>
  );
}


function WorkloadDetail({ run }: { run: ScenarioWorkloadRun }) {
  const activeStage = run.stages.find((stage) => stage.stage_id === run.current_stage);
  return (
    <main className="scenario-workload-detail">
      <header>
        <div>
          <span className="eyebrow">{run.identity.scenario_id}</span>
          <h2>{run.identity.model_repository.split("/").at(-1)}</h2>
          <p>{run.reason}</p>
        </div>
        <div className="scenario-run-state">
          <StatusBadge status={run.state} />
          <strong>{Math.round(run.progress * 100)}%</strong>
        </div>
      </header>

      <div className={`scenario-progress state-${run.state}`} aria-label={`Workload progress ${Math.round(run.progress * 100)}%`}>
        <i style={{ width: `${Math.round(run.progress * 100)}%` }} />
      </div>

      <section className="scenario-stage-flow" aria-label="Workload stage flow">
        {run.stages.map((stage) => <StageNode key={stage.stage_id} stage={stage} />)}
      </section>

      {run.blockers.length ? (
        <section className="scenario-blockers" role="alert">
          <TriangleAlert size={18} />
          <div><strong>{activeStage?.label || "Workload blocked"}</strong>{run.blockers.map((item) => <code key={item}>{item}</code>)}</div>
        </section>
      ) : null}

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
        {run.mlflow_run_id ? (
          <a href="http://127.0.0.1:5000" target="_blank" rel="noreferrer">
            MLflow <ExternalLink size={13} />
          </a>
        ) : null}
      </section>
    </main>
  );
}


function EvaluationPanel({ run }: { run: ScenarioWorkloadRun }) {
  const summary = run.evaluation_summary;
  if (!summary) {
    return (
      <section className="scenario-evaluation scenario-evaluation-empty" aria-label="Evaluation metrics unavailable">
        <Gauge size={18} />
        <div>
          <strong>Evaluation metrics unavailable</strong>
          <span>{run.evaluation_uri ? "Evidence could not be resolved" : "Evaluation has not completed"}</span>
        </div>
      </section>
    );
  }
  return (
    <section className="scenario-evaluation" aria-label={`${summary.model_family.toUpperCase()} evaluation metrics`}>
      <header>
        <div>
          <span className="eyebrow">{summary.model_family.toUpperCase()} metric schema</span>
          <h3>Evaluation & release evidence</h3>
        </div>
        <div className="scenario-gate-state" title={summary.release_gate.policy_source}>
          <ShieldCheck size={16} />
          <span>Release gate</span>
          <StatusBadge status={summary.release_gate.status} compact />
        </div>
      </header>
      <div className="scenario-metric-groups">
        <MetricGroup
          label="Model quality"
          metrics={summary.quality_metrics}
          emptyLabel="No supported quality metric"
        />
        <MetricGroup
          label="Runtime & evaluation"
          metrics={summary.operational_metrics}
          emptyLabel="No runtime metric"
        />
      </div>
      <footer>
        <code>{summary.schema_version}</code>
        <span>{summary.evaluated_at ? `evaluated ${formatTime(summary.evaluated_at)}` : "evaluation timestamp unavailable"}</span>
        {summary.release_gate.blockers.length ? <strong>{summary.release_gate.blockers.join(", ")}</strong> : <strong>policy evidence passed</strong>}
      </footer>
    </section>
  );
}


function MetricGroup({ label, metrics, emptyLabel }: { label: string; metrics: Record<string, number>; emptyLabel: string }) {
  const entries = Object.entries(metrics);
  return (
    <section className="scenario-metric-group" aria-label={label}>
      <span>{label}</span>
      <div>
        {entries.map(([name, value]) => (
          <div className="scenario-metric" key={name} title={metricDefinition(name)}>
            <small>{metricLabel(name)}</small>
            <strong>{formatMetric(name, value)}</strong>
          </div>
        ))}
        {!entries.length ? <em>{emptyLabel}</em> : null}
      </div>
    </section>
  );
}


function StageNode({ stage }: { stage: ScenarioWorkloadStage }) {
  return (
    <div className={`scenario-stage state-${stage.state}`} title={stage.detail || stage.label}>
      <i />
      <span>{stage.label}</span>
      <small>{stage.state.replaceAll("_", " ")}</small>
    </div>
  );
}


function Kpi({ icon: Icon, label, value, tone = "idle", active, onClick }: { icon: typeof Activity; label: string; value: number; tone?: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      className={`scenario-kpi tone-${tone} ${active ? "active" : ""}`}
      aria-label={`Show ${label.toLowerCase()} workloads`}
      aria-pressed={active}
      onClick={onClick}
    >
      <Icon size={18} /><span><small>{label}</small><strong>{value}</strong></span>
    </button>
  );
}


function Fact({ icon: Icon, label, value, detail }: { icon: typeof Activity; label: string; value: string; detail: string }) {
  return <div className="scenario-fact"><Icon size={18} /><span><small>{label}</small><strong>{value}</strong><code>{detail}</code></span></div>;
}


function shortHash(value: string): string {
  return value.length > 16 ? `${value.slice(0, 8)}...${value.slice(-6)}` : value;
}


function memoryLabel(run: ScenarioWorkloadRun): string {
  if (run.peak_gpu_allocated_mib === null || run.peak_gpu_allocated_mib === undefined) return "GPU profile pending";
  return `${(run.peak_gpu_allocated_mib / 1024).toFixed(2)} GiB peak allocated`;
}


function metricLabel(name: string): string {
  return ({
    accuracy: "Accuracy",
    parse_rate: "Parse rate",
    validation_loss: "Validation loss",
    mean_token_f1: "Token F1",
    nonempty_rate: "Non-empty rate",
    p95_latency_seconds: "P95 latency",
    evaluated_records: "Evaluated",
    peak_gpu_allocated_mib: "Peak VRAM",
    training_seconds: "Training time"
  } as Record<string, string>)[name] || name.replaceAll("_", " ");
}


function metricDefinition(name: string): string {
  return ({
    accuracy: "Correct VLM choices divided by held-out records.",
    parse_rate: "Parseable VLM answers divided by generated held-out answers.",
    validation_loss: "Cross-entropy loss measured on held-out validation records.",
    mean_token_f1: "Mean token-overlap F1 across generated LLM answers.",
    nonempty_rate: "Non-empty LLM generations divided by evaluated prompts.",
    p95_latency_seconds: "95th percentile generation latency on held-out evaluation.",
    evaluated_records: "Held-out records used by the recorded evaluation.",
    peak_gpu_allocated_mib: "Peak CUDA memory allocated during adaptation.",
    training_seconds: "Measured bounded adaptation wall time."
  } as Record<string, string>)[name] || "Metric recorded by the workload evaluation artifact.";
}


function formatMetric(name: string, value: number): string {
  if (["accuracy", "parse_rate", "mean_token_f1", "nonempty_rate"].includes(name)) {
    return `${(value * 100).toFixed(1)}%`;
  }
  if (name === "p95_latency_seconds") return `${value.toFixed(2)} s`;
  if (name === "peak_gpu_allocated_mib") return `${(value / 1024).toFixed(2)} GiB`;
  if (name === "training_seconds") return `${value.toFixed(1)} s`;
  if (name === "evaluated_records") return String(Math.round(value));
  return value.toFixed(3);
}


function formatTime(value: string): string {
  return new Date(value).toLocaleString([], { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
