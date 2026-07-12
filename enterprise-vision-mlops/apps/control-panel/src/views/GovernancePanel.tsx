import { CheckCheck, FileClock, GitPullRequestArrow, Plus, RotateCcw, X } from "lucide-react";
import { useState } from "react";

import { createDecisionRecord, transitionDecisionRecord } from "../api/controlPanelClient";
import type {
  DecisionRecord,
  DecisionRecordList,
  DecisionState,
  DecisionSubjectType
} from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

interface GovernancePanelProps {
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

export function GovernancePanel({ registry, onRefresh }: GovernancePanelProps) {
  const [subjectType, setSubjectType] = useState<DecisionSubjectType>("model_candidate");
  const [title, setTitle] = useState("EfficientNet B7 lifecycle decision");
  const [summary, setSummary] = useState("Record evidence, review ownership, and the final lifecycle decision.");
  const [owner, setOwner] = useState("ml-platform");
  const [actor, setActor] = useState("ai-infra-sre");
  const [reason, setReason] = useState("Review current evidence and advance the governance state.");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function createDraft() {
    setBusy(true);
    setError("");
    try {
      await createDecisionRecord({
        subject_type: subjectType,
        title,
        summary,
        owner,
        evidence_uris: [],
        metadata: {}
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
      <div className="panel governance-author">
        <div className="panel-heading">
          <div><h2>Decision Draft</h2><p>EVM-211</p></div>
          <FileClock />
        </div>
        <div className="governance-form">
          <label><span>Subject</span><select value={subjectType} onChange={(event) => setSubjectType(event.target.value as DecisionSubjectType)}>{subjectTypes.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label><span>Owner</span><input value={owner} onChange={(event) => setOwner(event.target.value)} /></label>
          <label className="governance-wide"><span>Title</span><input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
          <label className="governance-wide"><span>Summary</span><textarea value={summary} onChange={(event) => setSummary(event.target.value)} /></label>
          <button type="button" className="primary-action" onClick={() => void createDraft()} disabled={busy}><Plus size={16} /> Create Draft</button>
        </div>
      </div>

      <div className="panel governance-registry">
        <div className="panel-heading">
          <div><h2>Decision Registry</h2><p>{registry.decisions.length} records</p></div>
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
              <div className="decision-actions">{decisionActions(record).map((action) => (
                <button key={action.state} type="button" className="secondary-action" disabled={busy} onClick={() => void transition(record, action.state)}>{action.icon}{action.label}</button>
              ))}</div>
            </article>
          ))}
          {!registry.decisions.length ? <div className="empty-ledger">No decision records</div> : null}
        </div>
        {[...registry.blockers, ...(error ? [error] : [])].length ? <div className="policy-error" role="alert">{[...registry.blockers, error].filter(Boolean).join(", ")}</div> : null}
      </div>
    </section>
  );
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
