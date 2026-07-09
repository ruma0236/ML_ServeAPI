import { CheckSquare, DatabaseZap, GitPullRequestArrow, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";

import type { CycleRun, State } from "../api/types";
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
    </section>
  );
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
            <small>{item.evidence || "missing evidence"}</small>
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
