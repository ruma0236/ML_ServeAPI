import { AlertTriangle, GitBranch, ListChecks, Waves } from "lucide-react";
import type { ReactNode } from "react";

import { compactUri } from "../api/controlPanelClient";
import type { CycleRun, DriftState, State } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

interface DriftReviewProps {
  cycle: CycleRun;
}

const actionLabels: Record<string, string> = {
  none: "None",
  label_review: "Label Review",
  retrain_candidate: "Retrain Candidate",
  block_promotion: "Block Promotion",
  rollback_review: "Rollback Review"
};

export function DriftReview({ cycle }: DriftReviewProps) {
  const drift = cycle.drift;
  return (
    <div className="panel drift-review-panel">
      <div className="panel-heading">
        <div>
          <h2>Drift Review</h2>
          <p>{actionLabels[drift?.action || "none"] || drift?.action || "none"}</p>
        </div>
        <Waves />
      </div>

      <div className="drift-layout">
        <div className="drift-action-card">
          <div>
            <span>Recommended Action</span>
            <strong>{drift?.recommended_action || "no drift action required"}</strong>
          </div>
          <StatusBadge status={drift?.status} />
        </div>

        <div className="drift-state-grid">
          <DriftStateTile label="Data Drift" status={drift?.data_drift_status} value={drift?.severity || "none"} />
          <DriftStateTile label="Prediction Drift" status={drift?.prediction_drift_status} value={drift?.prediction_drift_status || "unknown"} />
          <DriftStateTile label="Review Queue" status={drift?.review_queue_count ? "warn" : "pass"} value={String(drift?.review_queue_count || 0)} />
          <DriftStateTile label="Retraining" status={drift?.retraining_candidate_required ? "warn" : "pass"} value={drift?.retraining_candidate_required ? "required" : "not required"} />
        </div>

        <dl className="detail-list drift-detail-list">
          <Row label="Reference" value={drift?.reference_dataset_version} />
          <Row label="Current" value={drift?.current_dataset_version} />
          <Row label="Score" value={formatScore(drift)} />
          <Row label="Report" value={compactUri(drift?.report_uri)} />
        </dl>

        <div className="drift-action-rail" aria-label="Drift action rail">
          <ActionPill icon={<ListChecks />} label="Label Review" active={drift?.action === "label_review"} />
          <ActionPill icon={<GitBranch />} label="Retrain" active={drift?.action === "retrain_candidate"} />
          <ActionPill icon={<AlertTriangle />} label="Block" active={drift?.action === "block_promotion"} />
        </div>
      </div>
    </div>
  );
}

function DriftStateTile({ label, value, status }: { label: string; value: string; status: State | string | null | undefined }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
      <StatusBadge status={status} compact />
    </div>
  );
}

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value || "-"}</dd>
    </div>
  );
}

function ActionPill({ icon, label, active }: { icon: ReactNode; label: string; active: boolean }) {
  return (
    <span className={active ? "active" : ""}>
      {icon}
      {label}
    </span>
  );
}

function formatScore(drift: DriftState | null | undefined): string {
  if (drift?.drift_score === null || drift?.drift_score === undefined) return "-";
  return drift.drift_score.toFixed(4);
}
