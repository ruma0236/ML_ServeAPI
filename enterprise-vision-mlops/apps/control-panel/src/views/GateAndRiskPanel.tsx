import { AlertTriangle, GitPullRequestArrow } from "lucide-react";

import type { CycleRun, DriftReviewWorkflow } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { CDCTGatePanel } from "./CDCTGatePanel";
import { DriftReview } from "./DriftReview";
import { DeploymentIntentPanel } from "./DeploymentIntentPanel";

interface GateAndRiskPanelProps {
  cycle: CycleRun;
  workflow: DriftReviewWorkflow | null;
  onRefresh: () => Promise<void>;
}

export function GateAndRiskPanel({ cycle, workflow, onRefresh }: GateAndRiskPanelProps) {
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

      <DriftReview cycle={cycle} workflow={workflow} onRefresh={onRefresh} />
      <CDCTGatePanel cycle={cycle} />
      <DeploymentIntentPanel cycle={cycle} />
    </section>
  );
}
