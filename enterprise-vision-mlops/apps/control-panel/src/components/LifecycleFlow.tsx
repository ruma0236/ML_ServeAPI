import {
  Activity,
  BadgeCheck,
  BrainCircuit,
  Database,
  Rocket,
  ScanSearch,
  type LucideIcon
} from "lucide-react";

import type { PipelinePlanStage, PipelineStage } from "../api/types";


export type LifecycleFlowState = "pending" | "ready" | "running" | "completed" | "review" | "blocked";


export interface LifecycleFlowItem {
  id: "data" | "validate" | "train" | "evaluate" | "release" | "observe";
  label: string;
  state: LifecycleFlowState;
  progress: number;
  detail: string;
}


const phases: Array<{ id: LifecycleFlowItem["id"]; label: string; icon: LucideIcon }> = [
  { id: "data", label: "Data", icon: Database },
  { id: "validate", label: "Validate", icon: ScanSearch },
  { id: "train", label: "Train", icon: BrainCircuit },
  { id: "evaluate", label: "Evaluate", icon: BadgeCheck },
  { id: "release", label: "Release", icon: Rocket },
  { id: "observe", label: "Observe", icon: Activity }
];


export function LifecycleFlow({ items, label }: { items: LifecycleFlowItem[]; label: string }) {
  const byId = new Map(items.map((item) => [item.id, item]));
  return (
    <div className="lifecycle-flow" role="img" aria-label={label}>
      {phases.map((phase, index) => {
        const item = byId.get(phase.id) || {
          id: phase.id,
          label: phase.label,
          state: "pending" as const,
          progress: 0,
          detail: "No stage evidence"
        };
        const Icon = phase.icon;
        return (
          <div className={`lifecycle-flow-phase flow-${item.state}`} key={phase.id} title={item.detail}>
            <div className="lifecycle-flow-node">
              <Icon size={19} />
              {item.state === "running" ? <i aria-hidden="true" /> : null}
            </div>
            <div className="lifecycle-flow-copy">
              <strong>{phase.label}</strong>
              <span>{flowStateLabel(item.state)}</span>
            </div>
            <div className="lifecycle-flow-progress" aria-hidden="true">
              <b style={{ width: `${item.progress}%` }} />
            </div>
            {index < phases.length - 1 ? <div className="lifecycle-flow-connector" aria-hidden="true"><i /></div> : null}
          </div>
        );
      })}
    </div>
  );
}


export function cycleLifecycleItems(stages: PipelineStage[]): LifecycleFlowItem[] {
  return phases.map((phase) => {
    const matches = stages.filter((stage) => phaseFor(stage.stage_id, stage.name) === phase.id);
    const progress = average(matches.map((stage) => completedStatus(stage.status) ? 1 : stage.progress));
    return {
      id: phase.id,
      label: phase.label,
      state: cycleState(matches),
      progress: Math.round(progress * 100),
      detail: matches.length ? matches.map((stage) => `${stage.name}: ${stage.status}`).join(" | ") : "No stage evidence"
    };
  });
}


export function planLifecycleItems(stages: PipelinePlanStage[]): LifecycleFlowItem[] {
  return phases.map((phase) => {
    const matches = stages.filter((stage) => phaseFor(stage.stage_id, stage.label) === phase.id);
    const progress = average(matches.map((stage) => stage.progress));
    return {
      id: phase.id,
      label: phase.label,
      state: planState(matches),
      progress: Math.round(progress * 100),
      detail: matches.length ? matches.map((stage) => `${stage.label}: ${stage.state}`).join(" | ") : "Not part of this Blueprint"
    };
  });
}


function phaseFor(id: string, label: string): LifecycleFlowItem["id"] {
  const value = `${id} ${label}`.toLowerCase();
  if (/drift|monitor|observ/.test(value)) return "observe";
  if (/promotion|deployment|admission|approval|serving|release|cdct|ci \/ ct|ci\/cd/.test(value)) return "release";
  if (/train|efficientnet|fine[- ]?tun/.test(value)) return "train";
  if (/quality|validation|validate|split|contract/.test(value)) return "validate";
  if (/evaluation|evaluate|readiness|registry|lifecycle|experiment|a\/b/.test(value)) return "evaluate";
  return "data";
}


function cycleState(stages: PipelineStage[]): LifecycleFlowState {
  if (!stages.length) return "pending";
  if (stages.some((stage) => stage.status === "blocked" || stage.status === "fail")) return "blocked";
  if (stages.some((stage) => stage.status === "running")) return "running";
  if (stages.some((stage) => stage.status === "warn")) return "review";
  if (stages.every((stage) => completedStatus(stage.status))) return "completed";
  if (stages.some((stage) => stage.status === "queued")) return "ready";
  return "pending";
}


function planState(stages: PipelinePlanStage[]): LifecycleFlowState {
  if (!stages.length) return "pending";
  if (stages.some((stage) => stage.state === "blocked")) return "blocked";
  if (stages.every((stage) => stage.progress >= 1)) return "completed";
  if (stages.some((stage) => stage.state === "ready")) return "ready";
  return "pending";
}


function completedStatus(status: PipelineStage["status"]): boolean {
  return status === "pass" || status === "done";
}


function average(values: number[]): number {
  if (!values.length) return 0;
  return Math.max(0, Math.min(1, values.reduce((total, value) => total + value, 0) / values.length));
}


function flowStateLabel(state: LifecycleFlowState): string {
  if (state === "completed") return "Completed";
  if (state === "running") return "In progress";
  if (state === "blocked") return "Blocked";
  if (state === "review") return "Review";
  if (state === "ready") return "Ready";
  return "Not started";
}
