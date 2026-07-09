import { Cpu, Database, HardDrive, Server, Workflow } from "lucide-react";

import { resourcePressure } from "../api/controlPanelClient";
import type { RuntimeResource } from "../api/types";
import { StatusBadge } from "./StatusBadge";

interface ResourceNodeProps {
  resource: RuntimeResource;
  selected: boolean;
  onSelect: (resource: RuntimeResource) => void;
}

export function ResourceNode({ resource, selected, onSelect }: ResourceNodeProps) {
  const Icon = iconFor(resource);
  return (
    <button
      type="button"
      className={`topology-node topology-${resource.status} ${selected ? "selected" : ""}`}
      onClick={() => onSelect(resource)}
      aria-label={`${resource.kind} ${resource.name} ${resource.status}`}
    >
      <span className="topology-icon">
        <Icon />
      </span>
      <span className="topology-copy">
        <small>{resource.kind}</small>
        <strong>{resource.name}</strong>
        <em>{resource.namespace}</em>
      </span>
      <span className="topology-meta">
        <StatusBadge status={resource.status} compact />
        <small>pressure {resourcePressure(resource)}</small>
      </span>
    </button>
  );
}

function iconFor(resource: RuntimeResource) {
  const kind = resource.kind.toLowerCase();
  if (resource.gpu_request) return Cpu;
  if (kind === "job") return Workflow;
  if (kind === "pod") return Cpu;
  if (kind === "persistentvolumeclaim" || resource.storage_claim) return Database;
  if (resource.name.includes("minio")) return HardDrive;
  return Server;
}
