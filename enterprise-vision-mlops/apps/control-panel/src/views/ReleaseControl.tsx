import { Activity, CheckCircle2, ExternalLink, GitPullRequestArrow, RadioTower, ShieldAlert } from "lucide-react";

import type { CycleRun, State } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { DeploymentIntentPanel } from "./DeploymentIntentPanel";


interface ReleaseControlProps {
  cycle: CycleRun;
}


interface ReleaseStage {
  id: string;
  label: string;
  status: State;
  detail: string;
  evidence?: string | null;
}


export function ReleaseControl({ cycle }: ReleaseControlProps) {
  const intent = cycle.latest_deployment_intent;
  const stages: ReleaseStage[] = [
    {
      id: "ci",
      label: "Continuous Integration",
      status: cycle.ci_evidence?.valid ? "pass" : "blocked",
      detail: cycle.ci_evidence?.workflow_run_id || "No immutable CI evidence",
      evidence: cycle.ci_evidence?.report_uri
    },
    {
      id: "ct",
      label: "Continuous Test",
      status: cycle.cdct_gate?.ct_status || "unknown",
      detail: cycle.cdct_gate?.ct_trigger || "No CT trigger",
      evidence: cycle.cdct_gate?.gate_report_uri
    },
    {
      id: "readiness",
      label: "Artifact Readiness",
      status: cycle.readiness_evaluation?.status || "blocked",
      detail: cycle.readiness_evaluation?.decision || "No readiness evaluation",
      evidence: cycle.readiness_evaluation?.report_uri
    },
    {
      id: "approval",
      label: "Promotion Approval",
      status: approvalStatus(cycle),
      detail: intent?.approver || cycle.promotion_policy?.approver || "Approver required",
      evidence: cycle.promotion_policy?.audit_uri
    },
    {
      id: "deployment",
      label: "Continuous Deployment",
      status: deploymentStatus(intent?.state),
      detail: intent?.state || "No deployment intent",
      evidence: intent?.audit_uri
    },
    {
      id: "serving",
      label: "Model Serving",
      status: cycle.serving.status,
      detail: `${cycle.serving.model_version} / ${cycle.serving.healthy_targets ?? 0} targets`,
      evidence: cycle.serving.endpoint
    },
    {
      id: "monitoring",
      label: "Production Monitoring",
      status: monitoringStatus(cycle),
      detail: cycle.serving.p95_latency_ms == null
        ? "Latency evidence unavailable"
        : `p95 ${cycle.serving.p95_latency_ms.toFixed(1)} ms`,
      evidence: cycle.cdct_gate?.pipeline_run_uri
    }
  ];
  const blockers = releaseBlockers(cycle, stages);
  const productionReady = intent?.target_environment === "production"
    && stages.every((stage) => stage.status === "pass" || stage.status === "done")
    && intent?.state === "applied";

  return (
    <section className="release-layout" aria-label="Release control">
      <div className="release-header">
        <div>
          <span className="eyebrow">CI / CT / CD</span>
          <h2>Release Control</h2>
          <p>{cycle.cycle_id}</p>
        </div>
        <div className="release-outcome">
          {productionReady ? <CheckCircle2 /> : <ShieldAlert />}
          <div>
            <span>Production</span>
            <strong>{productionReady ? "eligible" : "blocked"}</strong>
          </div>
          <StatusBadge status={productionReady ? "pass" : "blocked"} />
        </div>
      </div>

      <div className="release-flow" aria-label="Release pipeline stages">
        {stages.map((stage, index) => (
          <article key={stage.id} className={`release-stage release-${stage.status}`}>
            <div className="release-stage-index">{String(index + 1).padStart(2, "0")}</div>
            <div>
              <span>{stage.label}</span>
              <strong>{stage.detail}</strong>
              {stage.evidence ? <small title={stage.evidence}>{compact(stage.evidence)}</small> : <small>No evidence linked</small>}
            </div>
            <StatusBadge status={stage.status} compact />
          </article>
        ))}
      </div>

      <div className="release-monitor-grid">
        <MonitorLink icon={<Activity />} label="Grafana" href={serviceUrl("grafana")} detail="dashboards and alerts" />
        <MonitorLink icon={<GitPullRequestArrow />} label="MLflow" href={serviceUrl("mlflow")} detail="runs and model registry" />
        <MonitorLink icon={<RadioTower />} label="Prometheus" href={serviceUrl("prometheus")} detail="targets and metrics" />
      </div>

      {blockers.length ? (
        <div className="release-blockers" role="alert">
          <strong>Release blockers</strong>
          <div>{blockers.map((blocker) => <span key={blocker}>{blocker}</span>)}</div>
        </div>
      ) : null}

      <DeploymentIntentPanel cycle={cycle} />
    </section>
  );
}


function approvalStatus(cycle: CycleRun): State {
  const intent = cycle.latest_deployment_intent;
  if (intent?.approver && ["queued", "applying", "applied", "rolled_back"].includes(intent.state)) return "pass";
  if (cycle.promotion_policy?.decision === "allow") return "pass";
  if (cycle.promotion_policy?.decision === "pending_approval") return "warn";
  return "blocked";
}


function deploymentStatus(state: string | undefined): State {
  if (state === "applied") return "pass";
  if (state === "failed") return "fail";
  if (state === "rolled_back") return "warn";
  if (state === "queued" || state === "applying") return "running";
  if (state === "dry_run" || state === "pending_approval") return "queued";
  return "blocked";
}


function monitoringStatus(cycle: CycleRun): State {
  if (cycle.serving.status === "fail" || cycle.serving.status === "blocked") return "blocked";
  if (!cycle.serving.healthy_targets) return "warn";
  return cycle.serving.p95_latency_ms == null ? "warn" : "pass";
}


function releaseBlockers(cycle: CycleRun, stages: ReleaseStage[]): string[] {
  const blockers = new Set<string>([
    ...(cycle.promotion_gate?.blockers || []),
    ...(cycle.cdct_gate?.promotion_blockers || []),
    ...(cycle.readiness_evaluation?.blockers || [])
  ]);
  for (const stage of stages) {
    if (["blocked", "fail", "unknown"].includes(stage.status)) blockers.add(`${stage.id}:${stage.detail}`);
  }
  if (cycle.latest_deployment_intent?.target_environment !== "production") {
    blockers.add("deployment_target_not_production");
  }
  if (cycle.latest_deployment_intent?.state === "queued") blockers.add("deployment_executor_pending");
  return [...blockers];
}


function serviceUrl(service: "grafana" | "mlflow" | "prometheus"): string {
  const remote = window.location.hostname.endsWith(".ts.net");
  const ports = remote
    ? { grafana: 3001, mlflow: 5001, prometheus: 9091 }
    : { grafana: 3000, mlflow: 5000, prometheus: 9090 };
  return `${window.location.protocol}//${window.location.hostname}:${ports[service]}/`;
}


function MonitorLink({ icon, label, href, detail }: { icon: React.ReactNode; label: string; href: string; detail: string }) {
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {icon}
      <div><strong>{label}</strong><span>{detail}</span></div>
      <ExternalLink />
    </a>
  );
}


function compact(value: string): string {
  return value.length > 74 ? `${value.slice(0, 38)}...${value.slice(-28)}` : value;
}
