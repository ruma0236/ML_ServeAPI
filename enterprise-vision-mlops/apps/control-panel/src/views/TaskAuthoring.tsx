import { ClipboardList, Gauge, GitBranch, Play, Radio, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  compactUri,
  confirmTaskAssignment,
  createTaskAssignment,
  dispatchTaskAssignment,
  fetchCommandIntents,
  fetchDefaultTaskAssignment,
  fetchTaskAssignments
} from "../api/controlPanelClient";
import type {
  CommandIntent,
  CycleRun,
  OrchestratorConnection,
  RuntimeResource,
  TaskAssignment,
  TaskAssignmentRequest,
  TaskPriority,
  TaskType
} from "../api/types";
import { CommandDrawer } from "../components/CommandDrawer";
import { StatusBadge } from "../components/StatusBadge";

interface TaskAuthoringProps {
  cycle: CycleRun;
  resources: RuntimeResource[];
  orchestrators: OrchestratorConnection[];
}

const resourceProfiles = ["local-pipeline-workers", "windows-rtx-4080-super", "local-compose-platform", "mac-mini-m4-pro-evaluator"];
const taskTypes: TaskType[] = ["airflow_dag_run", "mlflow_run", "kubernetes_job"];
const priorities: TaskPriority[] = ["normal", "high", "urgent", "low"];
const approvalPolicies = ["auto", "manual", "two_person", "change_ticket"];

export function TaskAuthoring({ cycle, resources, orchestrators }: TaskAuthoringProps) {
  const [defaultTask, setDefaultTask] = useState<TaskAssignmentRequest | null>(null);
  const [tasks, setTasks] = useState<TaskAssignment[]>([]);
  const [commands, setCommands] = useState<CommandIntent[]>([]);
  const [taskType, setTaskType] = useState<TaskType>("airflow_dag_run");
  const [owner, setOwner] = useState(cycle.tenant?.ops_owner || "ai-infra-sre");
  const [priority, setPriority] = useState<TaskPriority>("normal");
  const [resourceProfile, setResourceProfile] = useState("local-pipeline-workers");
  const [approvalPolicy, setApprovalPolicy] = useState("manual");
  const [configText, setConfigText] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [taskScope, setTaskScope] = useState<"selected" | "all">("selected");
  const [syncedAt, setSyncedAt] = useState("");

  useEffect(() => {
    async function load(initial = false) {
      const [taskTemplate, taskLedger, commandLedger] = await Promise.all([
        fetchDefaultTaskAssignment(),
        fetchTaskAssignments(),
        fetchCommandIntents()
      ]);
      setTasks(taskLedger);
      setCommands(commandLedger);
      setSyncedAt(new Date().toLocaleTimeString());
      if (initial) {
        setDefaultTask(taskTemplate);
        setTaskType(taskTemplate.task_type);
        setOwner(taskTemplate.owner);
        setPriority(taskTemplate.priority);
        setResourceProfile(taskTemplate.resource_profile);
        setApprovalPolicy(taskTemplate.approval_policy || "manual");
        setConfigText(JSON.stringify(taskTemplate.config_payload, null, 2));
      }
    }

    void load(true).catch((err) => setError(err instanceof Error ? err.message : "operation ledger request failed"));
    const interval = window.setInterval(
      () => void load().catch((err) => setError(err instanceof Error ? err.message : "operation ledger request failed")),
      5000
    );
    return () => window.clearInterval(interval);
  }, []);

  const opsSummary = useMemo(() => {
    const runtime = new Map(orchestrators.map((item) => [item.orchestrator, item]));
    return [
      { label: "Airflow", value: runtime.get("airflow")?.mode || cycle.airflow?.mode || "unknown", status: runtime.get("airflow")?.status || "unknown" },
      { label: "MLflow", value: runtime.get("mlflow")?.mode || cycle.mlflow?.model_name || "unknown", status: runtime.get("mlflow")?.status || "unknown" },
      { label: "Kubernetes", value: runtime.get("kubernetes")?.mode || cycle.environment?.cluster || "unknown", status: runtime.get("kubernetes")?.status || "unknown" },
      { label: "CD/CT", value: cycle.cdct_gate?.ct_trigger || "manual", status: cycle.cdct_gate?.status || "unknown" },
      { label: "Environment", value: cycle.environment?.name || "local", status: cycle.environment?.promotion_state === "blocked" ? "blocked" : "running" }
    ];
  }, [cycle, orchestrators]);

  function buildTaskRequest(dryRun: boolean): TaskAssignmentRequest {
    const parsedConfig = parseConfig(configText);
    return {
      ...(defaultTask || {
        task_type: "airflow_dag_run",
        owner,
        priority,
        resource_profile: resourceProfile,
        config_payload: parsedConfig,
        dry_run: true
      }),
      cycle_id: cycle.cycle_id,
      task_type: taskType,
      owner,
      priority,
      resource_profile: resourceProfile,
      requester_team: cycle.tenant?.team_id || defaultTask?.requester_team || "mvi-platform",
      environment: cycle.environment || defaultTask?.environment || null,
      approval_policy: approvalPolicy,
      airflow: cycle.airflow || defaultTask?.airflow || null,
      mlflow: cycle.mlflow || defaultTask?.mlflow || null,
      cdct_gate: dryRun || approvalPolicy === "auto" || taskType === "airflow_dag_run"
        ? null
        : cycle.cdct_gate || defaultTask?.cdct_gate || null,
      config_payload: parsedConfig,
      dry_run: dryRun
    };
  }

  async function submitTask(dryRun: boolean) {
    setSubmitting(true);
    setError("");
    try {
      let task = await createTaskAssignment(buildTaskRequest(dryRun));
      if (!dryRun && task.status === "queued" && task.task_type === "airflow_dag_run") {
        task = await dispatchTaskAssignment(task.task_id);
      }
      setTasks((current) => [task, ...current.filter((item) => item.task_id !== task.task_id)]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "task assignment failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function advanceTask(task: TaskAssignment) {
    setSubmitting(true);
    setError("");
    try {
      const updated = task.status === "pending_confirmation"
        ? await confirmTaskAssignment(task.task_id, {
            actor: owner,
            reason: `Confirm ${task.task_type} for ${task.cycle_id || "unbound cycle"}`
          })
        : await dispatchTaskAssignment(task.task_id);
      setTasks((current) => [updated, ...current.filter((item) => item.task_id !== updated.task_id)]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "task transition failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="ops-layout" aria-label="Operations">
      <div className="ops-hero panel wide">
        <div className="ops-title">
          <span className="eyebrow">W7 Operations</span>
          <h2>Task Assignment And Command Control</h2>
          <div className="ops-live" aria-live="polite">
            <Radio />
            <span>Ledger Live</span>
            <time>{syncedAt || "connecting"}</time>
          </div>
        </div>
        <div className="ops-summary">
          {opsSummary.map((item) => (
            <div key={item.label}>
              <span>{item.label}</span>
              <strong>{item.value}</strong>
              <StatusBadge status={item.status} compact />
            </div>
          ))}
        </div>
      </div>

      {error ? <section className="error-state" role="alert">{error}</section> : null}

      <div className="panel ops-author">
        <div className="panel-heading">
          <div>
            <h2>Task Authoring</h2>
            <p>{cycle.airflow?.dag_id || "enterprise_vision_mlops_daily"}</p>
          </div>
          <ClipboardList />
        </div>
        <div className="ops-form">
          <label>
            <span>Task</span>
            <select value={taskType} onChange={(event) => setTaskType(event.target.value as TaskType)}>
              {taskTypes.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Owner</span>
            <input value={owner} onChange={(event) => setOwner(event.target.value)} />
          </label>
          <label>
            <span>Priority</span>
            <select value={priority} onChange={(event) => setPriority(event.target.value as TaskPriority)}>
              {priorities.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Resource</span>
            <select value={resourceProfile} onChange={(event) => setResourceProfile(event.target.value)}>
              {resourceProfiles.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Approval</span>
            <select value={approvalPolicy} onChange={(event) => setApprovalPolicy(event.target.value)}>
              {approvalPolicies.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="json-editor">
          <span>Config JSON</span>
          <textarea value={configText} onChange={(event) => setConfigText(event.target.value)} spellCheck={false} />
        </label>
        <div className="ops-button-row">
          <button type="button" className="primary-action" disabled={submitting} onClick={() => void submitTask(true)} title="Validate and record a dry-run assignment without dispatching it.">
            <ShieldCheck size={16} />
            Validate Task
          </button>
          <button type="button" className="secondary-action" disabled={submitting} onClick={() => void submitTask(false)}>
            <Play size={16} />
            {taskType === "airflow_dag_run" && approvalPolicy === "auto" ? "Queue & Dispatch" : "Queue Intent"}
          </button>
        </div>
      </div>

      <CommandDrawer resources={resources} commands={commands} onCommandsChange={setCommands} />

      <div className="panel wide">
        <div className="panel-heading">
          <div>
            <h2>Assignment Ledger</h2>
            <p>{tasks.length} tasks / {commands.length} commands</p>
          </div>
          <GitBranch />
        </div>
        <div className="ledger-scope" aria-label="Assignment ledger scope">
          <button type="button" className={taskScope === "selected" ? "active" : ""} onClick={() => setTaskScope("selected")}>Selected Cycle</button>
          <button type="button" className={taskScope === "all" ? "active" : ""} onClick={() => setTaskScope("all")}>All Cycles</button>
        </div>
        <div className="ops-ledger-grid">
          <LedgerColumn
            title="Tasks"
            items={taskScope === "all" ? tasks : tasks.filter((task) => task.cycle_id === cycle.cycle_id)}
            disabled={submitting}
            onAdvance={(task) => void advanceTask(task)}
          />
          <CommandColumn commands={commands} />
        </div>
      </div>
    </section>
  );
}

function parseConfig(value: string): TaskAssignmentRequest["config_payload"] {
  const parsed = JSON.parse(value || "{}") as TaskAssignmentRequest["config_payload"];
  return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
}

function LedgerColumn({
  title,
  items,
  disabled,
  onAdvance
}: {
  title: string;
  items: TaskAssignment[];
  disabled: boolean;
  onAdvance: (task: TaskAssignment) => void;
}) {
  return (
    <div className="ledger-column">
      <h3>{title}</h3>
      {items.length ? (
        items.slice(0, 6).map((task) => (
          <article key={task.task_id} className="ledger-item">
            <header>
              <div>
                <strong>{task.task_type}</strong>
              <span>{task.owner} / {task.resource_profile}</span>
              </div>
              <StatusBadge status={task.status} compact />
            </header>
            <p>{compactUri(task.cycle_id || "unbound cycle")}</p>
            <small>{task.runtime_state || task.audit.at(-1)?.event || "audit pending"}</small>
            {task.failure_reason ? <small className="ledger-failure">{task.failure_reason}</small> : null}
            {task.runtime_evidence_uri ? (
              <small title={task.runtime_evidence_uri}>{compactUri(task.runtime_evidence_uri)}</small>
            ) : null}
            {(
              task.status === "pending_confirmation" && task.approval_policy === "manual"
            ) || (task.status === "queued" && task.task_type === "airflow_dag_run") ? (
              <div className="ledger-actions">
                <button type="button" onClick={() => onAdvance(task)} disabled={disabled}>
                  {task.status === "pending_confirmation" ? "Confirm" : "Dispatch"}
                </button>
              </div>
            ) : null}
            {task.status === "pending_confirmation" && task.approval_policy !== "manual" ? (
              <small>External {task.approval_policy} approval required</small>
            ) : null}
          </article>
        ))
      ) : (
        <div className="empty-ledger">No task assignments</div>
      )}
    </div>
  );
}

function CommandColumn({ commands }: { commands: CommandIntent[] }) {
  return (
    <div className="ledger-column">
      <h3>Commands</h3>
      {commands.length ? (
        commands.slice(0, 6).map((command) => (
          <article key={command.command_id} className="ledger-item">
            <header>
              <div>
                <strong>{command.action}</strong>
                <span>{command.target.kind} / {command.target.name}</span>
              </div>
              <StatusBadge status={command.status} compact />
            </header>
            <p>{compactUri(command.command_id)}</p>
            <small>{command.audit.at(-1)?.event || "audit pending"}</small>
          </article>
        ))
      ) : (
        <div className="empty-ledger">No command intents</div>
      )}
    </div>
  );
}
