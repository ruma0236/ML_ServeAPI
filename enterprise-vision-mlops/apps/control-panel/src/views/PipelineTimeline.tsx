import { ChevronDown, Workflow } from "lucide-react";
import { useMemo, useState } from "react";

import { summarizeStages } from "../api/controlPanelClient";
import type { CycleRun, PipelineStage, RuntimeResourceList } from "../api/types";
import { StageDetail } from "../components/StageDetail";
import { StatusBadge } from "../components/StatusBadge";
import { KubernetesTopology } from "./KubernetesTopology";

interface PipelineTimelineProps {
  cycle: CycleRun;
  resourceSnapshot: RuntimeResourceList;
}

export function PipelineTimeline({ cycle, resourceSnapshot }: PipelineTimelineProps) {
  const resources = resourceSnapshot.resources;
  const [selectedStageId, setSelectedStageId] = useState(cycle.stages[0]?.stage_id || "");
  const [selectedResourceId, setSelectedResourceId] = useState(resources[0]?.resource_id || "");
  const selectedStage = useMemo(
    () => cycle.stages.find((stage) => stage.stage_id === selectedStageId) || cycle.stages[0] || null,
    [cycle.stages, selectedStageId]
  );
  const selectedResource = useMemo(
    () => resources.find((resource) => resource.resource_id === selectedResourceId) || resources[0] || null,
    [resources, selectedResourceId]
  );
  const summaries = summarizeStages(cycle);
  const progress = timelineProgress(cycle.stages);
  return (
    <section className="timeline-grid" aria-label="Pipeline timeline and resources">
      <div className="panel wide">
        <div className="panel-heading">
          <div>
            <h2>Pipeline Timeline</h2>
            <p>{cycle.airflow?.dag_id || "external-compose"}</p>
          </div>
          <Workflow />
        </div>
        <div className="timeline-progress-summary" aria-label={`Pipeline progress ${progress.percent}%`}>
          <div>
            <span>Pipeline Progress</span>
            <strong>{progress.percent}%</strong>
          </div>
          <div className="timeline-progress-counts">
            <span><i className="count-completed" /> Completed {progress.completed}</span>
            <span><i className="count-running" /> In Progress {progress.running}</span>
            <span><i className="count-pending" /> Not Started {progress.pending}</span>
            <span><i className="count-blocked" /> Blocked {progress.blocked}</span>
          </div>
          <div className={`timeline-overall-bar ${progress.running ? "is-running" : ""}`}>
            <b style={{ width: `${progress.percent}%` }} />
          </div>
        </div>
        <div className="timeline-layout">
          <div className="timeline">
            {cycle.stages.map((stage) => (
              <StageItem
                key={stage.stage_id}
                stage={stage}
                selected={stage.stage_id === selectedStage?.stage_id}
                onSelect={() => setSelectedStageId(stage.stage_id)}
              />
            ))}
          </div>
          {selectedStage ? (
            <StageDetail stage={selectedStage} />
          ) : (
            <aside className="stage-detail empty">No pipeline stages returned by CycleRun.</aside>
          )}
        </div>
        <details className="stage-summary-disclosure">
          <summary>
            <span>Artifact And Resource Index</span>
            <strong>{summaries.length} stages</strong>
            <ChevronDown />
          </summary>
          <div className="stage-summary-strip">
            {summaries.map((summary) => (
              <button key={summary.stageId} type="button" onClick={() => setSelectedStageId(summary.stageId)}>
                <strong>{summary.name}</strong>
                <span>{summary.artifactCount} artifacts / {summary.metricCount} metrics / {summary.resourceCount} resources</span>
                <em>{summary.blocker}</em>
              </button>
            ))}
          </div>
        </details>
      </div>

      <KubernetesTopology
        snapshot={resourceSnapshot}
        selectedResource={selectedResource}
        onSelectResource={(resource) => setSelectedResourceId(resource.resource_id)}
      />
    </section>
  );
}

function StageItem({ stage, selected, onSelect }: { stage: PipelineStage; selected: boolean; onSelect: () => void }) {
  const percent = stagePercent(stage);
  const stateLabel = stageStateLabel(stage.status);
  return (
    <button type="button" className={`stage-item stage-${stage.status} ${selected ? "selected" : ""}`} onClick={onSelect}>
      <div className="stage-rail">
        <i />
      </div>
      <div className="stage-body">
        <header>
          <div>
            <h3>{stage.name}</h3>
            <p>{stage.stage_id}</p>
          </div>
          <div className="stage-state-stack">
            <strong>{stateLabel}</strong>
            <StatusBadge status={stage.status} compact />
          </div>
        </header>
        <div className={`progress-line ${stage.status === "running" ? "is-running" : ""}`} aria-label={`${stateLabel} ${percent}%`}>
          <b style={{ width: `${percent}%` }} />
          <span>{percent}%</span>
        </div>
        <div className="stage-meta">
          <span>{stage.current_step || stage.failure_reason || "closed"}</span>
          <span>{stage.artifacts.length} artifacts</span>
          <span>{stage.resources.length} resources</span>
        </div>
      </div>
    </button>
  );
}


function stagePercent(stage: PipelineStage): number {
  if (stage.status === "pass" || stage.status === "done") return 100;
  return Math.max(0, Math.min(100, Math.round(stage.progress * 100)));
}


function stageStateLabel(status: PipelineStage["status"]): string {
  if (status === "pass" || status === "done") return "Completed";
  if (status === "running") return "In Progress";
  if (status === "blocked" || status === "fail") return "Blocked";
  if (status === "warn") return "Needs Review";
  return "Not Started";
}


function timelineProgress(stages: PipelineStage[]) {
  const completed = stages.filter((stage) => stage.status === "pass" || stage.status === "done").length;
  const running = stages.filter((stage) => stage.status === "running").length;
  const blocked = stages.filter((stage) => stage.status === "blocked" || stage.status === "fail").length;
  const pending = Math.max(stages.length - completed - running - blocked, 0);
  const percent = stages.length
    ? Math.round(stages.reduce((total, stage) => total + stagePercent(stage), 0) / stages.length)
    : 0;
  return { completed, running, blocked, pending, percent };
}
