import { AlertTriangle, CheckCircle2, GitPullRequestArrow } from "lucide-react";

import type { CycleRun, DriftReviewWorkflow } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { CDCTGatePanel } from "./CDCTGatePanel";
import { DriftReview } from "./DriftReview";

interface GateAndRiskPanelProps {
  cycle: CycleRun;
  workflow: DriftReviewWorkflow | null;
  onRefresh: () => Promise<void>;
}

export function GateAndRiskPanel({ cycle, workflow, onRefresh }: GateAndRiskPanelProps) {
  const blockers = cycle.promotion_gate?.blockers || [];
  return (
    <section className="two-column gate-risk-layout" aria-label="Gate and risk state">
      <div className="panel">
        <div className="panel-heading">
          <div>
            <h2>Model Metric Gate</h2>
            <p>{cycle.promotion_gate?.decision || "unknown"}</p>
          </div>
          <GitPullRequestArrow />
        </div>
        <StatusBadge status={cycle.promotion_gate?.status} />
        <div className="blocker-list">
          {blockers.map((blocker) => (
            <div key={blocker} className="blocker-item">
              <AlertTriangle />
              <span>{blocker}</span>
            </div>
          ))}
          {!blockers.length ? (
            <div className="blocker-item blocker-clear">
              <CheckCircle2 />
              <span>no blockers recorded</span>
            </div>
          ) : null}
        </div>
      </div>

      <DriftReview cycle={cycle} workflow={workflow} onRefresh={onRefresh} />
      <CDCTGatePanel cycle={cycle} />
    </section>
  );
}
