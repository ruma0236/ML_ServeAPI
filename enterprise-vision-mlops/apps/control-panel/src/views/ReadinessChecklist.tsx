import { CheckSquare, Copy, DatabaseZap, Fingerprint, GitPullRequestArrow, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";

import type { ArtifactReadinessEvaluation, CycleRun, ReadinessEvidenceCheck, State } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

interface ReadinessChecklistProps {
  cycle: CycleRun;
}

interface ChecklistItem {
  label: string;
  status: State | string | null | undefined;
  evidence: string | null | undefined;
}

export function ReadinessChecklist({ cycle }: ReadinessChecklistProps) {
  const dataItems: ChecklistItem[] = [
    { label: "Source contract", status: cycle.data_pipeline?.contract_status, evidence: cycle.data_pipeline?.source_policy_uri },
    { label: "Quality report", status: cycle.data_pipeline?.quality_status, evidence: cycle.data_pipeline?.quality_report_uri },
    { label: "Lineage", status: cycle.data_pipeline?.lineage_status, evidence: cycle.data_pipeline?.lineage_uri },
    { label: "Replay/backfill", status: cycle.data_pipeline?.replay_ready ? "pass" : "blocked", evidence: cycle.data_pipeline?.backfill_window }
  ];
  const modelItems: ChecklistItem[] = [
    { label: "MLflow tracking", status: cycle.experiment_pipeline?.tracking_status, evidence: cycle.experiment_pipeline?.experiment_uri },
    { label: "Evaluation", status: cycle.experiment_pipeline?.evaluation_status, evidence: cycle.experiment_pipeline?.evaluation_report_uri },
    { label: "Registry", status: cycle.experiment_pipeline?.registry_status, evidence: cycle.model.registry_uri },
    { label: "Rollback", status: cycle.experiment_pipeline?.rollback_ready ? "pass" : "blocked", evidence: cycle.experiment_pipeline?.model_card_uri }
  ];

  return (
    <section className="readiness-checklist" aria-label="Enterprise readiness checklist">
      <ChecklistPanel
        title="Data Pipeline Checklist"
        subtitle={cycle.dataset.version}
        icon={<DatabaseZap />}
        items={dataItems}
        owner={cycle.data_pipeline?.owner_approval_actor}
        ownerStatus={cycle.data_pipeline?.owner_approval_status}
        blockers={cycle.data_pipeline?.blockers || []}
      />
      <ChecklistPanel
        title="Model Pipeline Checklist"
        subtitle={`${cycle.model.model_name} v${cycle.model.version}`}
        icon={<GitPullRequestArrow />}
        items={modelItems}
        owner={cycle.experiment_pipeline?.owner_approval_actor}
        ownerStatus={cycle.experiment_pipeline?.owner_approval_status}
        blockers={cycle.experiment_pipeline?.blockers || []}
      />
      <EvidenceEvaluationPanel evaluation={cycle.readiness_evaluation} />
    </section>
  );
}

function EvidenceEvaluationPanel({ evaluation }: { evaluation: ArtifactReadinessEvaluation | null | undefined }) {
  if (!evaluation) {
    return (
      <div className="panel readiness-panel evidence-readiness-panel">
        <div className="panel-heading">
          <div>
            <h2>Artifact Evidence Decision</h2>
            <p>not evaluated</p>
          </div>
          <Fingerprint />
        </div>
        <StatusBadge status="unknown" compact />
      </div>
    );
  }

  return (
    <div className="panel readiness-panel evidence-readiness-panel" aria-label="Artifact evidence evaluation">
      <div className="panel-heading">
        <div>
          <h2>Artifact Evidence Decision</h2>
          <p>{evaluation.evaluation_id}</p>
        </div>
        <Fingerprint />
      </div>
      <div className="readiness-decision-strip">
        <DecisionState label="Overall" status={evaluation.status} value={evaluation.decision} />
        <DecisionState label="Data" status={evaluation.data_status} value={evaluation.dataset_version} />
        <DecisionState label="Model" status={evaluation.model_status} value={evaluation.candidate_id} />
        <DecisionState label="Runtime" status={evaluation.runtime_status} value={`${evaluation.checks.length} checks`} />
      </div>
      <div className="evidence-check-rows">
        {evaluation.checks.map((check) => (
          <EvidenceCheckRow key={check.check_id} check={check} />
        ))}
      </div>
      <div className="blocker-pills evidence-blockers">
        {(evaluation.blockers.length ? evaluation.blockers : ["no evidence blockers"]).map((blocker) => (
          <span key={blocker}>{blocker}</span>
        ))}
      </div>
    </div>
  );
}

function DecisionState({ label, status, value }: { label: string; status: State; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong title={value}>{value}</strong>
      <StatusBadge status={status} compact />
    </div>
  );
}

function EvidenceCheckRow({ check }: { check: ReadinessEvidenceCheck }) {
  const evidence = evidenceLabel(check.evidence_uri);
  const observed = Object.entries(check.observed)
    .filter(([, value]) => value !== "" && value !== null)
    .slice(0, 2)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" | ");
  const copyEvidence = () => {
    if (check.evidence_uri && navigator.clipboard) {
      void navigator.clipboard.writeText(check.evidence_uri);
    }
  };

  return (
    <div className="evidence-check-row">
      <span className="evidence-category">{check.category}</span>
      <div>
        <strong>{formatCheckId(check.check_id)}</strong>
        <small title={observed}>{observed || "no observed values"}</small>
      </div>
      <div className="evidence-source">
        <span title={check.evidence_uri || ""}>{evidence}</span>
        <code title={check.evidence_digest || ""}>{check.evidence_digest?.slice(0, 12) || "no digest"}</code>
      </div>
      {check.evidence_uri ? (
        <button type="button" className="icon-button compact" aria-label={`Copy ${check.check_id} evidence path`} onClick={copyEvidence}>
          <Copy />
        </button>
      ) : (
        <span className="evidence-copy-spacer" />
      )}
      <StatusBadge status={check.status} compact />
    </div>
  );
}

function formatCheckId(value: string) {
  return value.replaceAll("_", " ");
}

function evidenceLabel(uri: string | null | undefined) {
  if (!uri) return "runtime response";
  const segments = uri.replaceAll("\\", "/").split("/").filter(Boolean);
  return segments.slice(-2).join("/");
}

function ChecklistPanel({
  title,
  subtitle,
  icon,
  items,
  owner,
  ownerStatus,
  blockers
}: {
  title: string;
  subtitle: string;
  icon: ReactNode;
  items: ChecklistItem[];
  owner: string | null | undefined;
  ownerStatus: State | string | null | undefined;
  blockers: string[];
}) {
  return (
    <div className="panel readiness-panel">
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
        {icon}
      </div>
      <div className="checklist-rows">
        {items.map((item) => (
          <div key={item.label}>
            <CheckSquare />
            <span>{item.label}</span>
            <small title={item.evidence || "missing evidence"}>
              {item.evidence || "missing evidence"}
            </small>
            <StatusBadge status={item.status} compact />
          </div>
        ))}
      </div>
      <div className="owner-approval">
        <div>
          <ShieldCheck />
          <span>Owner approval</span>
          <strong>{owner || "unassigned"}</strong>
        </div>
        <StatusBadge status={ownerStatus} compact />
      </div>
      <div className="blocker-pills">
        {(blockers.length ? blockers : ["no readiness blockers"]).map((blocker) => (
          <span key={blocker}>{blocker}</span>
        ))}
      </div>
    </div>
  );
}
