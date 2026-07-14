import { Boxes, ChevronRight, Cpu, GitBranch, RefreshCcw, Server, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchDeploymentIntents, fetchLifecycleRuns } from "../api/controlPanelClient";
import type {
  CycleRun,
  DeploymentIntentList,
  LifecycleRun,
  LifecycleRunList,
  LifecycleRunState,
  RuntimeResource,
  RuntimeResourceList,
  State
} from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { buildDeploymentInventory } from "./deploymentInventoryModel";

interface CycleOverviewProps {
  cycle: CycleRun;
  resourceSnapshot: RuntimeResourceList;
  onOpenRuns: () => void;
  onOpenDeployments: () => void;
}

const activeRunStates: LifecycleRunState[] = ["queued", "running", "paused", "waiting_approval", "rolling_back"];

export function CycleOverview({ cycle, resourceSnapshot, onOpenRuns, onOpenDeployments }: CycleOverviewProps) {
  const [runs, setRuns] = useState<LifecycleRunList>({ runs: [], total: 0 });
  const [ledger, setLedger] = useState<DeploymentIntentList>({ intents: [], status: "unknown", blockers: [] });
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [nextRuns, nextLedger] = await Promise.all([fetchLifecycleRuns(), fetchDeploymentIntents()]);
      setRuns(nextRuns);
      setLedger(nextLedger);
      setError("");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Operations overview unavailable");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  const activeRuns = useMemo(
    () => runs.runs.filter((run) => activeRunStates.includes(run.state)),
    [runs.runs]
  );
  const attentionRuns = useMemo(
    () => runs.runs.filter((run) => ["blocked", "failed"].includes(run.state) && isRecent(run.updated_at)),
    [runs.runs]
  );
  const visibleRuns = useMemo(() => {
    const prioritized = [...activeRuns, ...attentionRuns.filter((run) => !activeRuns.some((active) => active.run_id === run.run_id))];
    if (prioritized.length) return prioritized.slice(0, 5);
    return runs.runs.filter((run) => run.state === "completed").slice(0, 3);
  }, [activeRuns, attentionRuns, runs.runs]);
  const deployments = useMemo(
    () => buildDeploymentInventory(ledger.intents, resourceSnapshot.resources),
    [ledger.intents, resourceSnapshot.resources]
  );
  const compute = useMemo(() => summarizeComputeResources(resourceSnapshot.resources), [resourceSnapshot.resources]);
  const attention = attentionRuns.length + deployments.attention;

  return (
    <section className="operations-overview" aria-label="Live operations overview">
      <header className="operations-header">
        <div><span className="eyebrow">Fleet-wide / 5 second refresh</span><h2>Live Operations</h2><p>{cycle.tenant?.department || "Enterprise MLOps"}</p></div>
        <div className="operations-live"><i className={resourceSnapshot.observation_status === "live" ? "is-live" : ""} /><span>{resourceSnapshot.observation_status}</span><button type="button" className="icon-button compact" onClick={() => void refresh()} aria-label="Refresh operations overview" title="Refresh operations overview"><RefreshCcw size={16} /></button></div>
      </header>

      <div className="operations-kpis" aria-label="Live operations summary">
        <OperationMetric icon={<GitBranch />} label="Pipelines in flight" value={activeRuns.length} tone={activeRuns.length ? "run" : "idle"} />
        <OperationMetric icon={<Server />} label="Serving models" value={deployments.active} tone="good" />
        <OperationMetric icon={<Cpu />} label="GPU allocated" value={`${compute.gpuAllocated} / ${compute.gpuCapacity || "-"}`} tone={compute.gpuAllocated > compute.gpuCapacity ? "bad" : "good"} />
        <OperationMetric icon={<TriangleAlert />} label="Needs attention" value={attention} tone={attention ? "bad" : "good"} />
      </div>

      {error ? <div className="workbench-error" role="alert"><TriangleAlert size={16} /><span>{error}</span></div> : null}

      <div className="operations-grid">
        <section className="operations-panel pipeline-activity-panel" aria-label="Pipeline activity">
          <PanelHeading icon={<GitBranch />} title="Pipeline Activity" meta={`${activeRuns.length} in flight`} action="View runs" onAction={onOpenRuns} />
          <div className="operations-pipeline-list">
            {visibleRuns.length ? visibleRuns.map((run) => <PipelineRow key={run.run_id} run={run} />) : <EmptyState text="No lifecycle runs recorded" />}
          </div>
        </section>

        <section className="operations-panel compute-panel" aria-label="Compute capacity">
          <PanelHeading icon={<Cpu />} title="Compute" meta={resourceSnapshot.cluster_context || "cluster unavailable"} />
          <div className="compute-visual">
            <CapacityRing label="GPU" value={compute.gpuAllocated} total={compute.gpuCapacity} />
            <CapacityRing label="Ready workloads" value={compute.readyWorkloads} total={compute.totalWorkloads} />
          </div>
          <div className="compute-status-list">
            <span><i className="status-good" />Nodes ready<strong>{compute.readyNodes} / {compute.totalNodes}</strong></span>
            <span><i className={compute.failedWorkloads ? "status-bad" : "status-good"} />Failed workloads<strong>{compute.failedWorkloads}</strong></span>
            <span><i className="status-idle" />CPU / memory usage<strong>Exporter required</strong></span>
          </div>
        </section>

        <section className="operations-panel deployment-fleet-panel" aria-label="Deployment fleet">
          <PanelHeading icon={<Boxes />} title="Deployment Fleet" meta={`${deployments.total} targets`} action="Manage models" onAction={onOpenDeployments} />
          <div className="operations-deployment-list">
            {deployments.items.slice(0, 5).map((item) => (
              <div className={`operations-deployment deployment-${item.runtimeState}`} key={item.id}>
                <i />
                <div><strong>{item.targetName}</strong><span>{item.candidateId}</span></div>
                <div><b>{item.environment}</b><span>{item.readyReplicas ?? "-"} / {item.desiredReplicas ?? "-"} replicas</span></div>
                <StatusBadge status={deploymentBadge(item.runtimeState)} compact />
              </div>
            ))}
            {!deployments.items.length ? <EmptyState text="No deployment targets recorded" /> : null}
          </div>
        </section>
      </div>
    </section>
  );
}

function PanelHeading({ icon, title, meta, action, onAction }: { icon: React.ReactNode; title: string; meta: string; action?: string; onAction?: () => void }) {
  return <header className="operations-panel-heading"><div>{icon}<span><strong>{title}</strong><small>{meta}</small></span></div>{action && onAction ? <button type="button" onClick={onAction}>{action}<ChevronRight size={14} /></button> : null}</header>;
}

function OperationMetric({ icon, label, value, tone }: { icon: React.ReactNode; label: string; value: string | number; tone: string }) {
  return <div className={`operation-metric tone-${tone}`}>{icon}<span>{label}</span><strong>{value}</strong></div>;
}

function PipelineRow({ run }: { run: LifecycleRun }) {
  const progress = Math.max(0, Math.min(100, Math.round(run.progress * 100)));
  const stage = run.stages.find((item) => item.stage_id === run.current_stage);
  return (
    <article className={`operations-pipeline pipeline-${run.state}`}>
      <span className="operations-pipeline-state"><i /></span>
      <div className="operations-pipeline-copy"><strong>{run.profile_id} / v{run.profile_version}</strong><span>{stage?.label || run.current_stage || run.state.replaceAll("_", " ")}</span></div>
      <div className="operations-pipeline-progress"><span><b>{progress}%</b><em>{run.execution_mode}</em></span><div><i style={{ width: `${progress}%` }} /></div></div>
      <StatusBadge status={runBadge(run.state)} compact />
    </article>
  );
}

function CapacityRing({ label, value, total }: { label: string; value: number; total: number }) {
  const ratio = total > 0 ? Math.min(100, Math.round(value / total * 100)) : 0;
  return <div className="capacity-ring" style={{ "--capacity": `${ratio}%` } as React.CSSProperties}><div><strong>{value}<small>/{total || "-"}</small></strong><span>{label}</span></div></div>;
}

function EmptyState({ text }: { text: string }) {
  return <div className="operations-empty">{text}</div>;
}

export function summarizeComputeResources(resources: RuntimeResource[]) {
  const live = resources.filter((resource) => resource.observation_source === "kubernetes_snapshot" && resource.observation_status === "live");
  const nodes = live.filter((resource) => resource.kind === "Node");
  const workloads = live.filter((resource) => (
    resource.kind === "Deployment"
      ? (resource.desired_replicas || 0) > 0
      : resource.kind === "Job" && ["queued", "running"].includes(resource.status)
  ));
  const allocatedPods = live.filter((resource) => (
    resource.kind === "Pod"
    && ["pass", "running"].includes(resource.status)
    && numericResource(resource.gpu_request) > 0
  ));
  const gpuCapacity = nodes.reduce((total, node) => total + numericResource(node.gpu_capacity), 0);
  const gpuAllocated = allocatedPods.reduce(
    (total, pod) => total + numericResource(pod.gpu_request),
    0
  );
  return {
    totalNodes: nodes.length,
    readyNodes: nodes.filter((node) => node.status === "pass" || node.status === "done").length,
    totalWorkloads: workloads.length,
    readyWorkloads: workloads.filter((workload) => workload.status === "pass" || workload.status === "done" || workload.status === "running").length,
    failedWorkloads: workloads.filter((workload) => workload.status === "fail" || workload.status === "blocked").length,
    gpuCapacity,
    gpuAllocated
  };
}

function numericResource(value: string | null | undefined): number {
  const match = value?.match(/\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : 0;
}

function runBadge(state: LifecycleRunState): State {
  if (state === "completed") return "pass";
  if (["running", "rolling_back"].includes(state)) return "running";
  if (["queued", "dry_run"].includes(state)) return "queued";
  if (["paused", "waiting_approval", "rolled_back"].includes(state)) return "warn";
  if (state === "cancelled") return "cancelled";
  return "blocked";
}

function deploymentBadge(state: string): State {
  if (state === "active") return "pass";
  if (state === "pending") return "running";
  if (["scaled_down", "rolled_back"].includes(state)) return "warn";
  return "blocked";
}

function isRecent(value: string): boolean {
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) && Date.now() - timestamp <= 24 * 60 * 60 * 1000;
}
