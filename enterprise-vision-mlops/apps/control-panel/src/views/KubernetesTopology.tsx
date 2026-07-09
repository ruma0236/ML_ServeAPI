import { Boxes } from "lucide-react";

import type { RuntimeResource } from "../api/types";
import { ResourceDetailDrawer } from "../components/ResourceDetailDrawer";
import { ResourceNode } from "../components/ResourceNode";

interface KubernetesTopologyProps {
  resources: RuntimeResource[];
  selectedResource: RuntimeResource | null;
  onSelectResource: (resource: RuntimeResource) => void;
}

export function KubernetesTopology({ resources, selectedResource, onSelectResource }: KubernetesTopologyProps) {
  const namespaces = Array.from(new Set(resources.map((resource) => resource.namespace))).sort();
  return (
    <div className="panel wide" aria-label="Kubernetes topology">
      <div className="panel-heading">
        <div>
          <h2>Kubernetes Resource Topology</h2>
          <p>{resources.length} resources / {namespaces.length} namespaces</p>
        </div>
        <Boxes />
      </div>
      <div className="topology-layout">
        <div className="topology-lanes">
          {namespaces.map((namespace) => (
            <section key={namespace} className="namespace-lane">
              <header>
                <strong>{namespace}</strong>
                <span>{resources.filter((resource) => resource.namespace === namespace).length}</span>
              </header>
              <div className="topology-map">
                {resources
                  .filter((resource) => resource.namespace === namespace)
                  .map((resource) => (
                    <ResourceNode
                      key={resource.resource_id}
                      resource={resource}
                      selected={selectedResource?.resource_id === resource.resource_id}
                      onSelect={onSelectResource}
                    />
                  ))}
              </div>
            </section>
          ))}
        </div>
        <ResourceDetailDrawer resource={selectedResource} />
      </div>
    </div>
  );
}
