import { Gauge, ShieldAlert } from "lucide-react";

import { compactUri, resourcePressure } from "../api/controlPanelClient";
import type { RuntimeResource } from "../api/types";
import { StatusBadge } from "./StatusBadge";

interface ResourceDetailDrawerProps {
  resource: RuntimeResource | null;
}

export function ResourceDetailDrawer({ resource }: ResourceDetailDrawerProps) {
  if (!resource) {
    return (
      <aside className="detail-drawer empty">
        <ShieldAlert />
        <strong>Select a resource</strong>
        <span>Topology details will appear here.</span>
      </aside>
    );
  }

  return (
    <aside className="detail-drawer" aria-label="Resource detail">
      <header>
        <div>
          <span>{resource.kind}</span>
          <h3>{resource.name}</h3>
        </div>
        <StatusBadge status={resource.status} />
      </header>
      <dl className="detail-list">
        <Row label="Namespace" value={resource.namespace} />
        <Row label="Readiness" value={resource.readiness} />
        <Row label="Pressure" value={resourcePressure(resource)} />
        <Row label="Node pool" value={resource.node_pool} />
        <Row label="CPU" value={resource.cpu_request} />
        <Row label="Memory" value={resource.memory_request} />
        <Row label="GPU" value={resource.gpu_request || "none"} />
        <Row label="GPU Capacity" value={resource.gpu_capacity || "none"} />
        <Row label="Storage" value={resource.storage_claim || compactUri(resource.storage_root)} />
        <Row label="Restarts" value={String(resource.restarts)} />
        <Row label="Source" value={resource.observation_source || "cycle_projection"} />
        <Row label="Observed" value={resource.observed_at} />
        <Row label="Reason" value={resource.reason} />
        <Row
          label="Replicas"
          value={
            resource.desired_replicas == null
              ? null
              : `${resource.ready_replicas || 0} / ${resource.desired_replicas}`
          }
        />
      </dl>
      {resource.observation_message ? (
        <div className="resource-observation-message">{resource.observation_message}</div>
      ) : null}
      <div className="detail-section">
        <h4>
          <Gauge />
          Related Stages
        </h4>
        {(resource.related_stages?.length ? resource.related_stages : ["cycle-level resource"]).map((stage) => (
          <span key={stage} className="detail-chip">
            {stage}
          </span>
        ))}
      </div>
      <div className="detail-section">
        <h4>Dry-run Actions</h4>
        {resource.control_actions.map((action) => (
          <span key={action} className="detail-chip">
            {action}
          </span>
        ))}
      </div>
    </aside>
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
