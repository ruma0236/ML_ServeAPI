import {
  ArrowRight,
  CheckCircle2,
  GitBranch,
  ListRestart,
  Play,
  RefreshCcw,
  Rocket,
  Rows3,
  TriangleAlert
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  fetchModelCandidates,
  fetchStageHandoffs,
  selectModelCandidate,
  transitionLifecycleRun
} from "../api/controlPanelClient";
import type {
  ModelCandidateCatalog,
  ModelCandidateSelection,
  StageHandoff,
  StageHandoffBucket,
  StageHandoffCatalog
} from "../api/types";
import { StatusBadge } from "../components/StatusBadge";


interface StageWorkbenchProps {
  onPromote: (selection: ModelCandidateSelection) => void;
}


const buckets: Array<{ key: StageHandoffBucket | "all"; label: string }> = [
  { key: "ready", label: "Ready" },
  { key: "active", label: "Active" },
  { key: "blocked", label: "Blocked" },
  { key: "completed", label: "Outputs" },
  { key: "cancelled", label: "Archived" },
  { key: "all", label: "All" }
];

type CandidateFilter = "ready" | "blocked" | "all";


export function StageWorkbench({ onPromote }: StageWorkbenchProps) {
  const [handoffs, setHandoffs] = useState<StageHandoffCatalog | null>(null);
  const [candidates, setCandidates] = useState<ModelCandidateCatalog | null>(null);
  const [bucket, setBucket] = useState<StageHandoffBucket | "all">("ready");
  const [candidateFilter, setCandidateFilter] = useState<CandidateFilter>("ready");
  const [handoffLimit, setHandoffLimit] = useState(12);
  const [candidateLimit, setCandidateLimit] = useState(12);
  const [operator, setOperator] = useState("ml-platform-operator");
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const initializedQueue = useRef(false);
  const loadingHandoffs = handoffs === null;
  const loadingCandidates = candidates === null;

  const load = useCallback(async () => {
    try {
      const [nextHandoffs, nextCandidates] = await Promise.all([
        fetchStageHandoffs(),
        fetchModelCandidates()
      ]);
      setHandoffs(nextHandoffs);
      setCandidates(nextCandidates);
      if (!initializedQueue.current) {
        if (nextHandoffs.ready > 0) setBucket("ready");
        else if (nextHandoffs.active > 0) setBucket("active");
        else if (nextHandoffs.blocked > 0) setBucket("blocked");
        else setBucket("completed");
        initializedQueue.current = true;
      }
      setError("");
    } catch (reason) {
      setError(message(reason));
    }
  }, []);

  useEffect(() => {
    void load();
    const interval = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(interval);
  }, [load]);

  const filteredHandoffs = useMemo(() => {
    const all = handoffs?.handoffs || [];
    if (bucket === "all") return all;
    if (bucket === "completed") {
      return all.filter((item) => item.bucket === "completed" || item.bucket === "consumed");
    }
    return all.filter((item) => item.bucket === bucket);
  }, [bucket, handoffs]);
  const visibleHandoffs = filteredHandoffs.slice(0, handoffLimit);
  const filteredCandidates = useMemo(() => {
    const all = candidates?.candidates || [];
    if (candidateFilter === "ready") return all.filter((item) => item.selectable);
    if (candidateFilter === "blocked") return all.filter((item) => !item.selectable);
    return all;
  }, [candidateFilter, candidates]);
  const visibleCandidates = filteredCandidates.slice(0, candidateLimit);

  async function actOnHandoff(item: StageHandoff, action: "continue" | "retry") {
    setBusyId(item.handoff_id);
    setError("");
    try {
      await transitionLifecycleRun(item.run_id, action, {
        actor: operator,
        reason: `${action} ${item.stage_id} from Stage Workbench`,
        expected_version: item.run_version
      });
      await load();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusyId("");
    }
  }

  async function promote(candidateKey: string) {
    setBusyId(candidateKey);
    setError("");
    try {
      const selection = await selectModelCandidate(candidateKey, {
        actor: operator,
        reason: "Select verified model candidate for governed promotion"
      });
      onPromote(selection);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusyId("");
    }
  }

  return (
    <section className="stage-workbench" aria-label="Stage Workbench">
      <header className="workbench-header">
        <div><span>Execution Control</span><h2>Stage Workbench</h2></div>
        <label><span>Operator</span><input value={operator} onChange={(event) => setOperator(event.target.value)} /></label>
        <button type="button" className="icon-button" onClick={() => void load()} aria-label="Refresh stage workbench" title="Refresh stage workbench"><RefreshCcw size={17} /></button>
      </header>

      <div className="workbench-kpis" aria-label="Stage work queue summary">
        <Metric icon={<Play />} label="Ready" value={handoffs?.ready ?? null} tone="run" />
        <Metric icon={<Rows3 />} label="Active" value={handoffs?.active ?? null} tone="run" />
        <Metric icon={<TriangleAlert />} label="Blocked" value={handoffs?.blocked ?? null} tone="bad" />
        <Metric icon={<Rocket />} label="Candidates" value={candidates?.selectable ?? null} tone="good" />
      </div>

      {error ? <div className="workbench-error" role="alert"><TriangleAlert size={16} /><span>{error}</span></div> : null}

      <section className="panel wide handoff-panel">
        <div className="panel-heading">
          <div><h2>Stage Handoffs</h2><p>{loadingHandoffs ? "Loading stage evidence..." : `${handoffs.total} immutable stage records`}</p></div>
          <GitBranch />
        </div>
        <div className="handoff-filters" aria-label="Stage handoff filters">
          {buckets.map((item) => (
            <button key={item.key} type="button" className={bucket === item.key ? "active" : ""} onClick={() => { setBucket(item.key); setHandoffLimit(12); }}>{item.label}</button>
          ))}
        </div>
        <div className="handoff-list">
          {loadingHandoffs ? <LoadingLedger label="Loading stage handoffs" /> : visibleHandoffs.length ? visibleHandoffs.map((item) => (
            <article key={item.handoff_id} className={`handoff-row bucket-${item.bucket}`}>
              <div className="handoff-state"><i /><StatusBadge status={item.stage_state} compact /></div>
              <div className="handoff-main">
                <header><strong>{item.stage_label}</strong><span>{item.execution_mode}</span></header>
                <p>{item.run_id}</p>
                <div className="handoff-flow">
                  <span>{item.previous_stage_id || "Start"}</span><ArrowRight size={13} /><b>{item.stage_id}</b><ArrowRight size={13} /><span>{item.next_stage_id || "Complete"}</span>
                </div>
                <div className="handoff-progress"><b style={{ width: `${Math.round(item.progress * 100)}%` }} /></div>
                {item.blockers.length ? <details><summary>{item.blockers.length} blockers</summary>{item.blockers.map((blocker) => <code key={blocker}>{blocker}</code>)}</details> : null}
                {(Object.keys(item.input_refs).length || Object.keys(item.output_refs).length) ? (
                  <details className="handoff-evidence">
                    <summary>Input and output evidence</summary>
                    {Object.entries(item.input_refs).map(([key, value]) => <code key={`input-${key}`} title={value}>IN {key}: {compactRef(value)}</code>)}
                    {Object.entries(item.output_refs).map(([key, value]) => <code key={`output-${key}`} title={value}>OUT {key}: {compactRef(value)}</code>)}
                  </details>
                ) : null}
              </div>
              <div className="handoff-output">
                <span>{Object.keys(item.input_refs).length} inputs / {Object.keys(item.output_refs).length} outputs</span>
                <strong>{item.runtime}</strong>
                <small>{new Date(item.updated_at).toLocaleString()}</small>
              </div>
              <div className="handoff-actions">
                {item.eligible_actions.includes("continue") ? <button type="button" className="primary-action" disabled={busyId === item.handoff_id || operator.length < 2} onClick={() => void actOnHandoff(item, "continue")}><Play size={15} /> Continue</button> : null}
                {item.eligible_actions.includes("retry") ? <button type="button" className="secondary-action" disabled={busyId === item.handoff_id || operator.length < 2} onClick={() => void actOnHandoff(item, "retry")}><ListRestart size={15} /> Retry</button> : null}
              </div>
            </article>
          )) : <div className="empty-ledger">No stage records in this queue</div>}
          {!loadingHandoffs && filteredHandoffs.length > handoffLimit ? <button type="button" className="ledger-more" onClick={() => setHandoffLimit((current) => current + 12)}>Show {Math.min(12, filteredHandoffs.length - handoffLimit)} more stages</button> : null}
        </div>
      </section>

      <section className="panel wide candidate-catalog-panel">
        <div className="panel-heading">
          <div><h2>Model Candidate Matrix</h2><p>{loadingCandidates ? "Loading model evidence..." : `${candidates.selectable} promotion-ready / ${candidates.total} total`}</p></div>
          <CheckCircle2 />
        </div>
        <div className="candidate-filters" aria-label="Model candidate filters">
          {(["ready", "blocked", "all"] as CandidateFilter[]).map((item) => (
            <button key={item} type="button" className={candidateFilter === item ? "active" : ""} onClick={() => { setCandidateFilter(item); setCandidateLimit(12); }}>
              {item === "ready" ? "Promotion Ready" : item === "blocked" ? "Blocked" : "All"}
            </button>
          ))}
        </div>
        <div className="candidate-matrix" role="table" aria-label="Model candidate promotion matrix">
          <div className="candidate-matrix-head" role="row">
            <span>Candidate</span><span>Dataset</span><span>Accuracy</span><span>F1</span><span>AUROC</span><span>Latency</span><span>GPU</span><span>Evidence</span><span>Action</span>
          </div>
          {loadingCandidates ? <LoadingLedger label="Loading model candidates" /> : visibleCandidates.length ? visibleCandidates.map((candidate) => (
            <div className={`candidate-matrix-row ${candidate.selectable ? "is-selectable" : "is-blocked"}`} role="row" key={candidate.candidate_key}>
              <div><strong>{candidate.candidate_id}</strong><small>{candidate.architecture} / {candidate.resource_profile}</small></div>
              <div><strong>{candidate.dataset_version}</strong><small>{candidate.matrix_id}</small></div>
              <MetricValue label="Accuracy" value={candidate.metrics.accuracy} />
              <MetricValue label="F1" value={candidate.metrics.f1} />
              <MetricValue label="AUROC" value={candidate.metrics.auroc} />
              <MetricValue label="Latency" value={candidate.metrics.latency_p95_ms} suffix=" ms" />
              <MetricValue label="GPU" value={candidate.metrics.gpu_memory_peak_mb} suffix=" MB" digits={0} />
              <div className="candidate-evidence"><StatusBadge status={candidate.selectable ? "pass" : "blocked"} compact /><small>Candidate {candidate.status} / CT {candidate.ct_decision}</small><small>{candidate.artifact_digest?.slice(0, 10) || "no digest"}</small></div>
              <div>
                <button type="button" className="primary-action" disabled={!candidate.selectable || busyId === candidate.candidate_key || operator.length < 2} onClick={() => void promote(candidate.candidate_key)}><Rocket size={15} /> Promote</button>
                {!candidate.selectable && candidate.blockers.length ? <details><summary>{candidate.blockers.length} blockers</summary>{candidate.blockers.map((blocker) => <code key={blocker}>{blocker}</code>)}</details> : null}
              </div>
            </div>
          )) : <div className="empty-ledger">No model candidates in this filter</div>}
          {!loadingCandidates && filteredCandidates.length > candidateLimit ? <button type="button" className="ledger-more" onClick={() => setCandidateLimit((current) => current + 12)}>Show {Math.min(12, filteredCandidates.length - candidateLimit)} more candidates</button> : null}
        </div>
      </section>
    </section>
  );
}


function Metric({ icon, label, value, tone }: { icon: React.ReactNode; label: string; value: number | null; tone: string }) {
  return <div className={`workbench-kpi tone-${tone} ${value === null ? "is-loading" : ""}`}>{icon}<span>{label}</span><strong>{value ?? "--"}</strong></div>;
}


function LoadingLedger({ label }: { label: string }) {
  return <div className="workbench-loading" role="status"><RefreshCcw size={17} /><span>{label}</span></div>;
}


function MetricValue({ label, value, suffix = "", digits = 3 }: { label: string; value?: number; suffix?: string; digits?: number }) {
  return <div className="candidate-metric"><span>{label}</span><strong>{value === undefined ? "-" : `${value.toFixed(digits)}${suffix}`}</strong></div>;
}


function message(error: unknown): string {
  return error instanceof Error ? error.message : "Stage Workbench request failed";
}


function compactRef(value: string): string {
  return value.length > 88 ? `${value.slice(0, 44)}...${value.slice(-32)}` : value;
}
