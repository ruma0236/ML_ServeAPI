import { CheckCircle2, DatabaseZap, FileCheck2, GitPullRequestArrow, ShieldAlert } from "lucide-react";

import { compactUri, formatNumber } from "../api/controlPanelClient";
import type { CDCTGate, CycleRun, State } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

interface CDCTGatePanelProps {
  cycle: CycleRun;
}

export function CDCTGatePanel({ cycle }: CDCTGatePanelProps) {
  const gate = cycle.cdct_gate;
  return (
    <div className="panel wide cdct-gate-panel">
      <div className="panel-heading">
        <div>
          <h2>Release Admission</h2>
          <p>{gate?.promotion_decision || "manual_review"}</p>
        </div>
        <CheckCircle2 />
      </div>

      <div className="cdct-layout">
        <div className="cdct-status-strip">
          <GateStatus label="CI" status={gate?.ci_status} />
          <GateStatus label="CD" status={gate?.cd_status} />
          <GateStatus label="CT" status={gate?.ct_status} />
          <GateStatus label="Promote" status={gate?.status} />
        </div>

        <div className="ct-evidence-strip" aria-label="Isolated continuous test evidence">
          <DatabaseZap />
          <EvidenceValue label="Snapshot" value={gate?.ct_snapshot_id || "missing"} />
          <EvidenceValue label="Evaluation" value={gate?.ct_evaluation_id || "missing"} />
          <EvidenceValue label="CT Records" value={formatNumber(cycle.ct_evaluation?.ct_record_count)} />
          <EvidenceValue label="Overlap" value={formatNumber(cycle.ct_evaluation?.overlap_count)} />
          <EvidenceValue label="Device" value={cycle.ct_evaluation?.device || "pending"} />
        </div>

        <div className="cdct-decision-card">
          <ShieldAlert />
          <div>
            <span>Block Reason</span>
            <strong>{gate?.block_reason || "promotion gate clear"}</strong>
          </div>
        </div>

        <div className="cdct-check-grid" aria-label="CD/CT check matrix">
          {(gate?.required_checks || []).map((check) => (
            <CheckRow key={check} check={check} gate={gate} />
          ))}
        </div>

        <dl className="detail-list cdct-detail-list">
          <Row label="Trigger" value={gate?.ct_trigger || "manual"} />
          <Row label="Pipeline" value={compactUri(gate?.pipeline_run_uri)} />
          <Row label="CI Report" value={compactUri(gate?.gate_report_uri)} />
          <Row label="CT Report" value={compactUri(gate?.ct_evidence_uri)} />
          <Row label="CT Digest" value={compactUri(gate?.ct_snapshot_digest)} />
          <Row label="Approved By" value={gate?.approved_by || "pending"} />
        </dl>

        <div className="blocker-pills cdct-blockers">
          {(gate?.promotion_blockers?.length ? gate.promotion_blockers : ["no promotion blockers"]).map((blocker) => (
            <span key={blocker}>{blocker}</span>
          ))}
        </div>
      </div>
    </div>
  );
}

function EvidenceValue({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

function GateStatus({ label, status }: { label: string; status: State | string | null | undefined }) {
  return (
    <div>
      <span>{label}</span>
      <StatusBadge status={status} compact />
    </div>
  );
}

function CheckRow({ check, gate }: { check: string; gate: CDCTGate | null | undefined }) {
  const status = checkStatus(check, gate);
  return (
    <div className={`cdct-check-row cdct-check-${statusTone(status)}`}>
      {status === "pass" ? <FileCheck2 /> : <GitPullRequestArrow />}
      <span>{check}</span>
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

function checkStatus(check: string, gate: CDCTGate | null | undefined): State {
  if (!gate) return "unknown";
  if (gate.failed_checks.includes(check)) return "blocked";
  if (gate.passed_checks.includes(check)) return "pass";
  return gate.verification_summary?.[check] || "unknown";
}

function statusTone(status: State): "pass" | "blocked" | "unknown" {
  if (status === "pass" || status === "done") return "pass";
  if (status === "blocked" || status === "fail") return "blocked";
  return "unknown";
}
