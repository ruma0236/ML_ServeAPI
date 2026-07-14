import { CheckCheck, ChevronRight, FileClock, GitPullRequestArrow, Plus, RotateCcw, X } from "lucide-react";
import { useState } from "react";

import { compactUri, createDecisionRecord, transitionDecisionRecord } from "../api/controlPanelClient";
import type {
  CycleRun,
  DecisionRecord,
  DecisionRecordList,
  DecisionState,
  DecisionSubjectType,
  LifecycleRun
} from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

interface GovernancePanelProps {
  cycle: CycleRun;
  lifecycleRun?: LifecycleRun | null;
  registry: DecisionRecordList;
  onRefresh: () => Promise<void>;
}

const subjectTypes: DecisionSubjectType[] = [
  "experiment",
  "prompt_change",
  "model_candidate",
  "evaluation_policy",
  "drift_review",
  "serving_change"
];

export function GovernancePanel({ cycle, lifecycleRun, registry, onRefresh }: GovernancePanelProps) {
  const [subjectType, setSubjectType] = useState<DecisionSubjectType>("model_candidate");
  const [title, setTitle] = useState(`${cycle.model.model_name} lifecycle decision`);
  const [summary, setSummary] = useState(`Review ${cycle.dataset.version} and ${cycle.model.version} lifecycle evidence.`);
  const [owner, setOwner] = useState(cycle.tenant?.model_owner || "ml-platform");
  const [actor, setActor] = useState("ai-infra-sre");
  const [reason, setReason] = useState("Review current evidence and advance the governance state.");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function createDraft() {
    setBusy(true);
    setError("");
    try {
      const evidenceUris = [
        lifecycleRun?.model_matrix_uri,
        lifecycleRun?.readiness_uri,
        lifecycleRun?.real_test_validation_uri,
        lifecycleRun?.cycle_snapshot_uri,
        cycle.readiness_evaluation?.report_uri,
        cycle.cdct_gate?.gate_report_uri
      ].filter((value): value is string => Boolean(value));
      await createDecisionRecord({
        subject_type: subjectType,
        title,
        summary,
        owner,
        evidence_uris: [...new Set(evidenceUris)],
        metadata: {
          cycle_id: cycle.cycle_id,
          lifecycle_run_id: lifecycleRun?.run_id || null,
          dataset_version: cycle.dataset.version,
          model_version: cycle.model.version,
          source_commit: lifecycleRun?.source_commit || null
        }
      });
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "decision draft failed");
    } finally {
      setBusy(false);
    }
  }

  async function transition(record: DecisionRecord, target: DecisionState) {
    setBusy(true);
    setError("");
    try {
      await transitionDecisionRecord(record.decision_id, {
        target_state: target,
        actor,
        reason,
        expected_version: record.version
      });
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "decision transition failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="governance-layout" aria-label="Decision governance">
      <div className="panel governance-registry">
        <div className="panel-heading">
          <div><h2>Decision Queue</h2><p>{registry.decisions.length} governed changes</p></div>
          <StatusBadge status={registry.status} />
        </div>
        <div className="governance-actor-row">
          <label><span>Actor</span><input value={actor} onChange={(event) => setActor(event.target.value)} /></label>
          <label><span>Reason</span><input value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        </div>
        <div className="decision-list">
          {registry.decisions.map((record) => (
            <article key={record.decision_id} className="decision-record">
              <header>
                <div><small>{record.subject_type}</small><strong>{record.title}</strong><span>{record.owner} / v{record.version}</span></div>
                <StatusBadge status={decisionTone(record.state)} compact />
              </header>
              <p>{record.summary}</p>
              <dl className="decision-summary-grid">
                <div><dt>Outcome</dt><dd>{decisionOutcome(record)}</dd></div>
                <div><dt>Impact</dt><dd>{decisionImpact(record)}</dd></div>
                <div><dt>Next action</dt><dd>{decisionNextAction(record)}</dd></div>
              </dl>
              <details className="decision-evidence">
                <summary><span>{record.evidence_uris.length} linked evidence</span><ChevronRight size={14} /></summary>
                <small title={String(record.metadata.cycle_id || "")}>Cycle {compactUri(String(record.metadata.cycle_id || "unbound cycle"))}</small>
                {record.evidence_uris.map((uri) => <code key={uri} title={uri}>{compactUri(uri)}</code>)}
              </details>
              <div className="decision-actions">{decisionActions(record).map((action) => (
                <button key={action.state} type="button" className="secondary-action" disabled={busy} onClick={() => void transition(record, action.state)}>{action.icon}{action.label}</button>
              ))}</div>
            </article>
          ))}
          {!registry.decisions.length ? <div className="empty-ledger">No decision records</div> : null}
        </div>
        {[...registry.blockers, ...(error ? [error] : [])].length ? <div className="policy-error" role="alert">{[...registry.blockers, error].filter(Boolean).join(", ")}</div> : null}
      </div>

      <details className="panel governance-author">
        <summary className="governance-author-summary">
          <span><FileClock size={18} /><strong>New Decision</strong><small>Record a reviewed model, data, policy, or serving change</small></span>
          <Plus size={18} />
        </summary>
        <div className="governance-author-body">
          <div className="governance-context" aria-label="Decision evidence context">
            <span>Cycle</span><strong title={cycle.cycle_id}>{compactUri(cycle.cycle_id)}</strong>
            <span>Run</span><strong title={lifecycleRun?.run_id || ""}>{lifecycleRun?.run_id || "No run selected"}</strong>
          </div>
          <div className="governance-form">
            <label><span>Subject</span><select value={subjectType} onChange={(event) => setSubjectType(event.target.value as DecisionSubjectType)}>{subjectTypes.map((item) => <option key={item}>{item}</option>)}</select></label>
            <label><span>Owner</span><input value={owner} onChange={(event) => setOwner(event.target.value)} /></label>
            <label className="governance-wide"><span>Title</span><input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
            <label className="governance-wide"><span>Summary</span><textarea value={summary} onChange={(event) => setSummary(event.target.value)} /></label>
            <button type="button" className="primary-action" onClick={() => void createDraft()} disabled={busy}><Plus size={16} /> Create Draft</button>
          </div>
        </div>
      </details>
    </section>
  );
}

function decisionOutcome(record: DecisionRecord): string {
  if (record.state === "approved") return "Approved and immutable";
  if (record.state === "rejected") return "Rejected with audit history";
  if (record.state === "review") return "Awaiting reviewer decision";
  return "Draft, no runtime impact";
}

function decisionImpact(record: DecisionRecord): string {
  const labels: Record<DecisionSubjectType, string> = {
    experiment: "Experiment configuration",
    prompt_change: "Prompt behavior",
    model_candidate: "Model promotion",
    evaluation_policy: "Quality and gate policy",
    drift_review: "Drift response",
    serving_change: "Serving runtime"
  };
  return labels[record.subject_type];
}

function decisionNextAction(record: DecisionRecord): string {
  if (record.state === "draft") return "Submit for review";
  if (record.state === "review") return "Approve or reject";
  if (record.state === "rejected") return "Revise and reopen";
  return "Monitor linked runtime evidence";
}

function decisionTone(state: DecisionState): string {
  if (state === "approved") return "pass";
  if (state === "rejected") return "blocked";
  if (state === "review") return "warn";
  return "queued";
}

function decisionActions(record: DecisionRecord): Array<{ state: DecisionState; label: string; icon: React.ReactNode }> {
  if (record.state === "draft") return [{ state: "review", label: "Submit Review", icon: <GitPullRequestArrow size={15} /> }];
  if (record.state === "review") return [
    { state: "approved", label: "Approve", icon: <CheckCheck size={15} /> },
    { state: "rejected", label: "Reject", icon: <X size={15} /> }
  ];
  if (record.state === "rejected") return [{ state: "draft", label: "Reopen", icon: <RotateCcw size={15} /> }];
  return [];
}
