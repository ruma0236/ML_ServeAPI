import type { CSSProperties, ReactNode } from "react";

import { Activity, Boxes, Database, GitBranch, RadioTower, ServerCog } from "lucide-react";

import { formatNumber, summarizeCycle } from "../api/controlPanelClient";
import type { CycleRun } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

interface CycleOverviewProps {
  cycle: CycleRun;
}

export function CycleOverview({ cycle }: CycleOverviewProps) {
  const summary = summarizeCycle(cycle);
  const visibleStages = cycle.stages.slice(0, 9);
  const nodeCount = Math.max(visibleStages.length, 1);

  return (
    <section className="overview-grid" aria-label="Cycle overview">
      <div className="summary-strip">
        <MetricTile icon={<Activity />} label="Cycle" value={summary.cycleId} status={summary.status} />
        <MetricTile icon={<Database />} label="Dataset" value={summary.datasetVersion} status={cycle.dataset.quality_status} />
        <MetricTile icon={<GitBranch />} label="Model" value={summary.modelVersion} status={cycle.promotion_gate?.status} />
        <MetricTile icon={<Boxes />} label="Artifacts" value={String(summary.artifactCount)} status={summary.blockedStages ? "blocked" : "pass"} />
      </div>

      <div className="panel cycle-card">
        <div className="panel-heading">
          <div>
            <h2>Cycle State</h2>
            <p>{cycle.owner_issue}</p>
          </div>
          <StatusBadge status={cycle.status} />
        </div>
        <div className="cycle-ring" aria-label={`Current cycle status ${cycle.status}`}>
          <div className="ring-sweep" aria-hidden="true" />
          <div className="ring-core">
            <span>{summary.stageCount}</span>
            <small>stages</small>
          </div>
          {visibleStages.map((stage, index) => (
            <span
              key={stage.stage_id}
              className="ring-node-anchor"
              style={{ "--angle": `${index * (360 / nodeCount)}deg` } as CSSProperties}
              title={`${stage.name}: ${stage.status}`}
            >
              <span className={`ring-node node-${stage.status}`} />
            </span>
          ))}
        </div>
      </div>

      <div className="panel">
        <div className="panel-heading">
          <div>
            <h2>Service Scope</h2>
            <p>{cycle.tenant?.department || "unknown"}</p>
          </div>
          <ServerCog />
        </div>
        <dl className="dense-list">
          <Row label="Team" value={cycle.tenant?.team_id} />
          <Row label="Scope" value={cycle.tenant?.service_scope} />
          <Row label="Environment" value={`${cycle.environment?.name || "unknown"} / ${cycle.environment?.tier || "unknown"}`} />
          <Row label="Cluster" value={cycle.environment?.cluster || "not-bound"} />
        </dl>
      </div>

      <div className="panel">
        <div className="panel-heading">
          <div>
            <h2>Serving</h2>
            <p>{cycle.serving.endpoint}</p>
          </div>
          <RadioTower />
        </div>
        <dl className="dense-list">
          <Row label="Loaded" value={cycle.serving.model_loaded ? "true" : "false"} />
          <Row label="Version" value={cycle.serving.model_version} />
          <Row label="Targets" value={formatNumber(cycle.serving.healthy_targets)} />
          <Row label="Placeholder" value={cycle.serving.placeholder === false ? "false" : String(cycle.serving.placeholder ?? "unknown")} />
        </dl>
      </div>
    </section>
  );
}

function MetricTile({ icon, label, value, status }: { icon: ReactNode; label: string; value: string; status: string | undefined }) {
  return (
    <div className="metric-tile">
      <div className="metric-icon">{icon}</div>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
      <StatusBadge status={status} compact />
    </div>
  );
}

function Row({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value || "-"}</dd>
    </div>
  );
}
