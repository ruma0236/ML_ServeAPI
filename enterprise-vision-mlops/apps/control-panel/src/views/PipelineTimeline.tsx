import { Boxes, Workflow } from "lucide-react";

import { toResourceNodes } from "../api/controlPanelClient";
import type { CycleRun, PipelineStage } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

interface PipelineTimelineProps {
  cycle: CycleRun;
}

export function PipelineTimeline({ cycle }: PipelineTimelineProps) {
  const resources = toResourceNodes(cycle);
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
        <div className="timeline">
          {cycle.stages.map((stage) => (
            <StageItem key={stage.stage_id} stage={stage} />
          ))}
        </div>
      </div>

      <div className="panel wide">
        <div className="panel-heading">
          <div>
            <h2>Resource Topology</h2>
            <p>{cycle.environment?.namespace || "evm-platform"}</p>
          </div>
          <Boxes />
        </div>
        <div className="resource-map">
          {resources.map((resource, index) => (
            <div key={resource.id} className={`resource-node resource-${resource.status}`} style={{ animationDelay: `${index * 90}ms` }}>
              <span>{resource.kind}</span>
              <strong>{resource.name}</strong>
              <small>{resource.namespace}</small>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function StageItem({ stage }: { stage: PipelineStage }) {
  return (
    <article className={`stage-item stage-${stage.status}`}>
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
    </article>
  );
}
