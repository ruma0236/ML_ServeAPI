import type { State } from "../api/types";
import { statusTone } from "../api/controlPanelClient";

interface StatusBadgeProps {
  status: State | string | null | undefined;
  compact?: boolean;
}

export function StatusBadge({ status, compact = false }: StatusBadgeProps) {
  const label = typeof status === "string" ? status.replaceAll("_", " ") : status;
  return (
    <span className={`status-badge status-${statusTone(status)} ${compact ? "status-compact" : ""}`}>
      {label || "unknown"}
    </span>
  );
}
