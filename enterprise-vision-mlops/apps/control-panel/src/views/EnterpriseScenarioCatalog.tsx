import {
  CheckCircle2,
  Database,
  Download,
  FileText,
  Image,
  Images,
  Play,
  ShieldCheck,
  TriangleAlert,
  type LucideIcon
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  fetchEnterpriseScenarios,
  fetchTaskAssignments,
  launchScenarioIntake
} from "../api/controlPanelClient";
import type {
  EnterpriseScenario,
  EnterpriseScenarioCatalog as ScenarioCatalogResponse,
  PipelineRunProfile,
  ScenarioModality,
  TaskAssignment
} from "../api/types";
import { StatusBadge } from "../components/StatusBadge";


interface EnterpriseScenarioCatalogProps {
  owner: string;
  onApplyProfile: (profile: PipelineRunProfile) => void;
}


const modalityIcon: Record<ScenarioModality, LucideIcon> = {
  image: Image,
  text: FileText,
  image_text: Images
};


export function EnterpriseScenarioCatalog({
  owner,
  onApplyProfile
}: EnterpriseScenarioCatalogProps) {
  const [catalog, setCatalog] = useState<ScenarioCatalogResponse | null>(null);
  const [busyScenario, setBusyScenario] = useState("");
  const [task, setTask] = useState<TaskAssignment | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    async function refresh() {
      const next = await fetchEnterpriseScenarios();
      if (active) setCatalog(next);
    }
    void refresh().catch((reason) => active && setError(message(reason)));
    const timer = window.setInterval(
      () => void refresh().catch((reason) => active && setError(message(reason))),
      3000
    );
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!task || ["dry_run", "done", "failed", "cancelled", "blocked"].includes(task.status)) {
      return;
    }
    let active = true;
    async function refreshTask() {
      const tasks = await fetchTaskAssignments();
      const current = tasks.find((item) => item.task_id === task?.task_id);
      if (active && current) setTask(current);
    }
    void refreshTask().catch((reason) => active && setError(message(reason)));
    const timer = window.setInterval(
      () => void refreshTask().catch((reason) => active && setError(message(reason))),
      2000
    );
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [task?.task_id, task?.status]);

  const ordered = useMemo(
    () => [...(catalog?.scenarios || [])].sort((left, right) => {
      if (left.readiness === "verified_full_lifecycle") return -1;
      if (right.readiness === "verified_full_lifecycle") return 1;
      return left.display_name.localeCompare(right.display_name);
    }),
    [catalog]
  );

  async function startIntake(scenario: EnterpriseScenario, dryRun: boolean) {
    setBusyScenario(scenario.scenario_id);
    setError("");
    try {
      const next = await launchScenarioIntake(scenario.scenario_id, {
        actor: owner,
        reason: `${dryRun ? "Validate" : "Run"} ${scenario.display_name} governed intake`,
        dry_run: dryRun
      });
      setTask(next);
      setCatalog(await fetchEnterpriseScenarios());
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusyScenario("");
    }
  }

  return (
    <div className="scenario-catalog" aria-label="Enterprise scenario catalog">
      <div className="scenario-catalog-heading">
        <div>
          <strong>Approved Use Cases</strong>
          <span>{catalog ? `${catalog.scenarios.length} scenarios / ${catalog.catalog_digest.slice(0, 10)}` : "loading registry"}</span>
        </div>
        <Database size={18} />
      </div>
      {error ? <div className="policy-error" role="alert">{error}</div> : null}
      <div className="scenario-grid">
        {ordered.map((scenario) => (
          <ScenarioCard
            key={scenario.scenario_id}
            scenario={scenario}
            busy={busyScenario === scenario.scenario_id}
            onApply={() => scenario.profile_template && onApplyProfile(scenario.profile_template)}
            onIntake={(dryRun) => void startIntake(scenario, dryRun)}
          />
        ))}
      </div>
      {task ? (
        <div className="scenario-task" aria-live="polite">
          <StatusBadge status={task.status} compact />
          <strong>{String(task.config_payload.scenario_id || task.task_id)}</strong>
          <span>{task.runtime_state || task.audit.at(-1)?.event || "recorded"}</span>
        </div>
      ) : null}
    </div>
  );
}


function ScenarioCard({
  scenario,
  busy,
  onApply,
  onIntake
}: {
  scenario: EnterpriseScenario;
  busy: boolean;
  onApply: () => void;
  onIntake: (dryRun: boolean) => void;
}) {
  const Icon = modalityIcon[scenario.modality];
  const progress = scenario.intake_state?.progress ?? (
    scenario.data_readiness === "verified" || scenario.data_readiness === "ready" ? 1 : 0
  );
  const fullLifecycle = scenario.readiness === "verified_full_lifecycle";
  return (
    <article className={`scenario-card scenario-${scenario.readiness}`}>
      <header>
        <span className="scenario-icon"><Icon size={18} /></span>
        <div>
          <strong>{scenario.display_name}</strong>
          <span>{scenario.department}</span>
        </div>
        <StatusBadge status={scenario.readiness} compact />
      </header>
      <p>{scenario.business_outcome}</p>
      <div className="scenario-state-row">
        <span><small>Data</small><strong>{scenario.data_readiness.replaceAll("_", " ")}</strong></span>
        <span><small>Model</small><strong>{scenario.model_readiness.replaceAll("_", " ")}</strong></span>
        <span><small>Deploy</small><strong>{scenario.deployment_readiness.replaceAll("_", " ")}</strong></span>
      </div>
      <div className={`scenario-progress ${scenario.data_readiness === "running" ? "is-running" : ""}`}>
        <b style={{ width: `${Math.round(progress * 100)}%` }} />
      </div>
      <div className="scenario-meta">
        <span>{scenario.dataset.dataset_name}</span>
        <span>{formatBytes(scenario.dataset.source_size_bytes)}</span>
        <span>{scenario.dataset.license_id}</span>
        {scenario.intake_state?.records_output ? <span>{scenario.intake_state.records_output.toLocaleString()} records</span> : null}
      </div>
      {scenario.blockers.length ? (
        <details className="scenario-blockers">
          <summary><TriangleAlert size={14} /> {scenario.blockers.length} lifecycle blockers</summary>
          <ul>{scenario.blockers.map((blocker) => <li key={blocker}>{blocker.replaceAll("_", " ")}</li>)}</ul>
        </details>
      ) : (
        <div className="scenario-verified"><CheckCircle2 size={14} /> Real data, GPU training, CT and serving evidence verified</div>
      )}
      <div className="scenario-actions">
        {scenario.profile_template ? (
          <button type="button" className="primary-action" onClick={onApply} disabled={busy}>
            <Play size={15} /> Use Blueprint
          </button>
        ) : null}
        {scenario.intake_supported ? (
          <>
            <button type="button" className="secondary-action" onClick={() => onIntake(true)} disabled={busy}>
              <ShieldCheck size={15} /> Validate
            </button>
            <button type="button" className="primary-action" onClick={() => onIntake(false)} disabled={busy || scenario.data_readiness === "running"}>
              <Download size={15} /> {scenario.data_readiness === "running" ? "Running" : scenario.data_readiness === "ready" ? "Re-run Intake" : "Run Intake"}
            </button>
          </>
        ) : null}
        {fullLifecycle ? <span className="scenario-proof"><CheckCircle2 size={14} /> Staging release proven</span> : null}
      </div>
    </article>
  );
}


function formatBytes(value: number): string {
  if (!value) return "local evidence";
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GiB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${Math.ceil(value / 1024)} KiB`;
}


function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : "Scenario catalog operation failed.";
}
