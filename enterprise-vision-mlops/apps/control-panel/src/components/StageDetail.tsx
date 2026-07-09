import { Boxes, FileJson, Gauge, ListChecks } from "lucide-react";

import { compactUri, formatNumber } from "../api/controlPanelClient";
import type { PipelineStage } from "../api/types";
import { StatusBadge } from "./StatusBadge";

interface StageDetailProps {
  stage: PipelineStage;
}

export function StageDetail({ stage }: StageDetailProps) {
  return (
    <aside className="stage-detail" aria-label="Stage detail">
      <header>
        <div>
          <span>{stage.stage_id}</span>
          <h3>{stage.name}</h3>
        </div>
        <StatusBadge status={stage.status} />
      </header>
      <div className="detail-progress">
        <b style={{ width: `${Math.round(stage.progress * 100)}%` }} />
        <span>{Math.round(stage.progress * 100)}%</span>
      </div>

      <section className="detail-section">
        <h4>
          <Gauge />
          Metrics
        </h4>
        <div className="metric-grid">
          {stage.metrics.length ? (
            stage.metrics.map((metric) => (
              <div key={metric.name}>
                <span>{metric.name}</span>
                <strong>{formatNumber(metric.value)}</strong>
                <small>{metric.unit || metric.status || ""}</small>
              </div>
            ))
          ) : (
            <span className="detail-muted">No metrics captured</span>
          )}
        </div>
      </section>

      <section className="detail-section">
        <h4>
          <FileJson />
          Artifacts
        </h4>
        <div className="artifact-list">
          {stage.artifacts.length ? (
            stage.artifacts.map((artifact) => (
              <span key={`${artifact.name}-${artifact.uri}`} title={artifact.uri}>
                {artifact.name}
                <small>{compactUri(artifact.uri)}</small>
              </span>
            ))
          ) : (
            <span className="detail-muted">No artifact linked yet</span>
          )}
        </div>
      </section>

      <section className="detail-section">
        <h4>
          <FileJson />
          Sample Outputs
        </h4>
        <div className="artifact-list">
          {stage.sample_outputs.length ? (
            stage.sample_outputs.map((artifact) => (
              <span key={`${artifact.name}-${artifact.uri}`} title={artifact.uri}>
                {artifact.name}
                <small>{compactUri(artifact.preview_uri || artifact.uri)}</small>
              </span>
            ))
          ) : (
            <span className="detail-muted">No sample output linked yet</span>
          )}
        </div>
      </section>

      <section className="detail-section">
        <h4>
          <Boxes />
          Resources
        </h4>
        {stage.resources.length ? (
          stage.resources.map((resource) => (
            <span key={`${resource.namespace}-${resource.kind}-${resource.name}`} className="detail-chip">
              {resource.kind} / {resource.name}
            </span>
          ))
        ) : (
          <span className="detail-muted">No resource reference</span>
        )}
      </section>

      <section className="detail-section">
        <h4>
          <ListChecks />
          Result
        </h4>
        <p className="result-copy">{stage.failure_reason || stage.current_step || "closed"}</p>
      </section>
    </aside>
  );
}
