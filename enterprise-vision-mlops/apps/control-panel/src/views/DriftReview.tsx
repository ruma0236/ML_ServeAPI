import { BadgeCheck, FileSearch, LockKeyhole, Waves } from "lucide-react";
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
  const measured = drift?.measurement_status === "measured";
  return (
    <div className="panel drift-review-panel">
      <div className="panel-heading">
        <div>
          <h2>Drift Review</h2>
          <p>{drift?.review_event_type || actionLabels[drift?.action || "none"] || drift?.action || "none"}</p>
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
          <DriftStateTile label="Input Category JS" status={drift?.data_drift_status} value={formatMetric(drift?.input_category_js)} />
          <DriftStateTile label="Confidence PSI" status={drift?.prediction_drift_status} value={formatMetric(drift?.confidence_psi)} />
          <DriftStateTile label="Review Queue" status={drift?.review_queue_count ? "warn" : "pass"} value={String(drift?.review_queue_count || 0)} />
          <DriftStateTile label="Auto Retraining" status={drift?.automatic_retraining ? "fail" : "pass"} value={drift?.automatic_retraining ? "enabled" : "disabled"} />
        </div>

        <dl className="detail-list drift-detail-list">
          <Row label="Measurement" value={drift?.measurement_status} />
          <Row label="Candidate" value={drift?.model_candidate_id} />
          <Row label="Event" value={drift?.review_event_id} />
          <Row label="Event State" value={drift?.review_event_status} />
          <Row label="Baseline" value={windowLabel(drift?.reference_window_id, drift?.reference_record_count)} />
          <Row label="Current" value={windowLabel(drift?.current_window_id, drift?.current_record_count)} />
          <Row label="Predicted Class JS" value={formatMetric(drift?.predicted_class_js)} />
          <Row label="Mean Confidence Drop" value={formatMetric(drift?.mean_confidence_drop)} />
          <Row label="Low Confidence Increase" value={formatPercent(drift?.low_confidence_rate_increase)} />
          <Row label="Report" value={compactUri(drift?.report_uri)} />
          <Row label="Label Queue" value={compactUri(drift?.label_review_queue_uri)} />
        </dl>

        {measured ? (
          <div className="drift-confidence-comparison" aria-label="Confidence quantile comparison">
            <header>
              <span>Confidence</span>
              <strong>Baseline</strong>
              <strong>Current</strong>
            </header>
            {(["p10", "p50", "p90"] as const).map((quantile) => (
              <div key={quantile}>
                <span>{quantile.toUpperCase()}</span>
                <strong>{formatMetric(drift?.reference_confidence_quantiles?.[quantile])}</strong>
                <strong>{formatMetric(drift?.current_confidence_quantiles?.[quantile])}</strong>
              </div>
            ))}
            <div>
              <span>Low rate</span>
              <strong>{formatPercent(drift?.reference_low_confidence_rate)}</strong>
              <strong>{formatPercent(drift?.current_low_confidence_rate)}</strong>
            </div>
          </div>
        ) : null}

        <div className="drift-rule-list" aria-label="Triggered drift rules">
          {(drift?.triggered_rules?.length ? drift.triggered_rules : ["no policy threshold exceeded"]).map((rule) => (
            <span key={rule}>{rule}</span>
          ))}
        </div>

        <div className="drift-action-rail" aria-label="Drift action rail">
          <ActionPill icon={<FileSearch />} label="Label Review" active={drift?.action === "label_review"} />
          <ActionPill icon={<BadgeCheck />} label="Approval Pending" active={drift?.approval_required === true} />
          <ActionPill icon={<LockKeyhole />} label="No Auto Retrain" active={drift?.automatic_retraining === false} />
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

function Row({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value === null || value === undefined || value === "" ? "-" : value}</dd>
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

function formatMetric(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value.toFixed(4);
}

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return `${(value * 100).toFixed(2)}%`;
}

function windowLabel(windowId: string | null | undefined, count: number | undefined): string {
  if (!windowId) return "-";
  return `${windowId} · ${count || 0} records`;
}
