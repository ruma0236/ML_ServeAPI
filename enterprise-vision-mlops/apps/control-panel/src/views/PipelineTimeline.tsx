import { Workflow } from "lucide-react";
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
        <div className="stage-summary-strip">
          {summaries.map((summary) => (
            <button key={summary.stageId} type="button" onClick={() => setSelectedStageId(summary.stageId)}>
              <strong>{summary.name}</strong>
              <span>{summary.artifactCount} artifacts / {summary.metricCount} metrics / {summary.resourceCount} resources</span>
              <em>{summary.blocker}</em>
            </button>
          ))}
        </div>
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
          <StatusBadge status={stage.status} />
        </header>
        <div className="progress-line">
          <b style={{ width: `${Math.round(stage.progress * 100)}%` }} />
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
