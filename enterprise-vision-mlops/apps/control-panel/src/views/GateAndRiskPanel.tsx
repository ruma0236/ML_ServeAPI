import { AlertTriangle, CheckCircle2, GitPullRequestArrow, Waves } from "lucide-react";

import type { CycleRun } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

interface GateAndRiskPanelProps {
  cycle: CycleRun;
}

export function GateAndRiskPanel({ cycle }: GateAndRiskPanelProps) {
  return (
    <section className="two-column" aria-label="Gate and risk state">
      <div className="panel">
        <div className="panel-heading">
          <div>
            <h2>Promotion Gate</h2>
            <p>{cycle.promotion_gate?.decision || "unknown"}</p>
          </div>
          <GitPullRequestArrow />
        </div>
        <StatusBadge status={cycle.promotion_gate?.status} />
        <div className="blocker-list">
          {(cycle.promotion_gate?.blockers.length ? cycle.promotion_gate.blockers : ["no blockers recorded"]).map((blocker) => (
            <div key={blocker} className="blocker-item">
              <AlertTriangle />
              <span>{blocker}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="panel">
        <div className="panel-heading">
          <div>
            <h2>Drift</h2>
            <p>{cycle.drift?.action || "none"}</p>
          </div>
          <Waves />
        </div>
        <div className="readiness-grid">
          <Readiness label="State" status={cycle.drift?.status} />
          <Readiness label="Data" status={cycle.drift?.data_drift_status} />
          <Readiness label="Prediction" status={cycle.drift?.prediction_drift_status} />
          <Readiness label="Columns" status={cycle.drift?.drifting_columns?.length ? "warn" : "pass"} />
        </div>
      </div>

      <div className="panel wide">
        <div className="panel-heading">
          <div>
            <h2>CD/CT Gate</h2>
            <p>{cycle.cdct_gate?.ct_trigger || "manual"}</p>
          </div>
          <CheckCircle2 />
        </div>
        <div className="gate-columns">
          <CheckList title="Required" items={cycle.cdct_gate?.required_checks || []} />
          <CheckList title="Passed" items={cycle.cdct_gate?.passed_checks || []} tone="good" />
          <CheckList title="Failed" items={cycle.cdct_gate?.failed_checks || []} tone="bad" />
        </div>
      </div>
    </section>
  );
}

function Readiness({ label, status }: { label: string; status: string | null | undefined }) {
  return (
    <div className="readiness-item">
      <span>{label}</span>
      <StatusBadge status={status} compact />
    </div>
  );
}

function CheckList({ title, items, tone = "idle" }: { title: string; items: string[]; tone?: "good" | "bad" | "idle" }) {
  return (
    <div className={`check-list check-${tone}`}>
      <h3>{title}</h3>
      {items.length ? items.map((item) => <span key={item}>{item}</span>) : <span>-</span>}
    </div>
  );
}
