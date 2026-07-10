import { Activity, Boxes } from "lucide-react";
import { useState } from "react";

import type { RuntimeResource, RuntimeResourceList } from "../api/types";
import { ResourceDetailDrawer } from "../components/ResourceDetailDrawer";
import { ResourceNode } from "../components/ResourceNode";

interface KubernetesTopologyProps {
  snapshot: RuntimeResourceList;
  selectedResource: RuntimeResource | null;
  onSelectResource: (resource: RuntimeResource) => void;
}

export function KubernetesTopology({ snapshot, selectedResource, onSelectResource }: KubernetesTopologyProps) {
  const resources = snapshot.resources;
  const liveResources = resources.filter((resource) => resource.observation_source === "kubernetes_snapshot");
  const liveCount = liveResources.length;
  const projectedCount = resources.length - liveCount;
  const [sourceMode, setSourceMode] = useState<"live" | "all">("live");
  const displayedResources = sourceMode === "live" && liveCount ? liveResources : resources;
  const namespaces = Array.from(new Set(displayedResources.map((resource) => resource.namespace))).sort();
  const effectiveSelected =
    displayedResources.find((resource) => resource.resource_id === selectedResource?.resource_id) ||
    displayedResources[0] ||
    null;
  const age = snapshot.snapshot_age_seconds == null ? null : `${Math.round(snapshot.snapshot_age_seconds)}s`;
  return (
    <div className="panel wide" aria-label="Kubernetes topology">
      <div className="panel-heading">
        <div>
          <h2>Kubernetes Resource Topology</h2>
          <p>{liveCount} live / {projectedCount} projected / {namespaces.length} namespaces</p>
        </div>
        <div className="observation-state">
          <div className="segmented-control resource-source-control" aria-label="Resource source">
            <button
              type="button"
              className={sourceMode === "live" ? "active" : ""}
              onClick={() => setSourceMode("live")}
            >
              Live
            </button>
            <button
              type="button"
              className={sourceMode === "all" ? "active" : ""}
              onClick={() => setSourceMode("all")}
            >
              All
            </button>
          </div>
          <span className={`observation-${snapshot.observation_status}`}>
            <Activity />
            {snapshot.observation_status}{age ? ` ${age}` : ""}
          </span>
          <Boxes />
        </div>
      </div>
      <div className="topology-layout">
        <div className="topology-lanes">
          {namespaces.map((namespace) => (
            <section key={namespace} className="namespace-lane">
              <header>
                <strong>{namespace}</strong>
                <span>{displayedResources.filter((resource) => resource.namespace === namespace).length}</span>
              </header>
              <div className="topology-map">
                {displayedResources
                  .filter((resource) => resource.namespace === namespace)
                  .map((resource) => (
                    <ResourceNode
                      key={resource.resource_id}
                      resource={resource}
                      selected={effectiveSelected?.resource_id === resource.resource_id}
                      onSelect={onSelectResource}
                    />
                  ))}
              </div>
            </section>
          ))}
        </div>
        <ResourceDetailDrawer resource={effectiveSelected} />
      </div>
    </div>
  );
}
