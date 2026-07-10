import {
  CheckCheck,
  CircleDotDashed,
  GitCommitHorizontal,
  ListChecks,
  Play,
  RefreshCcw,
  ShieldCheck
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  ControlPanelApiError,
  createDeploymentIntent,
  fetchDeploymentIntents,
  transitionDeploymentIntent
} from "../api/controlPanelClient";
import type {
  CycleRun,
  DeploymentIntent,
  DeploymentIntentList,
  DeploymentTransitionRequest,
  EnvironmentTier
} from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

interface DeploymentIntentPanelProps {
  cycle: CycleRun;
}

const namespaces: Record<EnvironmentTier, string> = {
  dev: "evm-dev",
  test: "evm-test",
  staging: "evm-staging",
  "pre-production": "evm-pre-production",
  production: "evm-production"
};

export function DeploymentIntentPanel({ cycle }: DeploymentIntentPanelProps) {
  const initialEnvironment = cycle.environment?.tier || "staging";
  const [ledger, setLedger] = useState<DeploymentIntentList>({
    intents: cycle.latest_deployment_intent ? [cycle.latest_deployment_intent] : [],
    status: "pass",
    blockers: []
  });
  const [environment, setEnvironment] = useState<EnvironmentTier>(initialEnvironment);
  const [requester, setRequester] = useState(cycle.tenant?.model_owner || "ml-platform");
  const [approver, setApprover] = useState(cycle.tenant?.ops_owner || "ai-infra-sre");
  const [reason, setReason] = useState("Promote verified EfficientNet B7 candidate");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [actionBlockers, setActionBlockers] = useState<string[]>([]);
  const latest = ledger.intents[0] || cycle.latest_deployment_intent || null;
  const namespace = namespaces[environment];
  const admissionStatus = latest?.state || (
    cycle.ci_evidence?.valid
    && cycle.readiness_evaluation?.decision === "ready"
    && cycle.promotion_policy?.decision === "allow"
      ? "queued"
      : "blocked"
  );

  async function loadLedger() {
    try {
      setLedger(await fetchDeploymentIntents());
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "deployment ledger unavailable");
    }
  }

  useEffect(() => {
    void loadLedger();
    const interval = window.setInterval(() => void loadLedger(), 5000);
    return () => window.clearInterval(interval);
  }, []);

  const nextAction = useMemo(() => {
    if (!latest) return null;
    if (latest.state === "dry_run") return "request-approval" as const;
    if (latest.state === "pending_approval" && !latest.approver) return "approve" as const;
    if (latest.state === "pending_approval" && latest.approver) return "queue" as const;
    return null;
  }, [latest]);

  async function createIntent() {
    setBusy(true);
    clearActionState();
    try {
      const created = await createDeploymentIntent({
        target_environment: environment,
        target_namespace: namespace,
        target: { namespace, kind: "Deployment", name: "evm-b7-serving" },
        actor: requester,
        reason,
        dry_run: true
      });
      mergeIntent(created);
    } catch (error) {
      captureActionError(error);
    } finally {
      setBusy(false);
    }
  }

  async function advanceIntent() {
    if (!latest || !nextAction) return;
    setBusy(true);
    clearActionState();
    const actor = nextAction === "request-approval" ? requester : approver;
    const request: DeploymentTransitionRequest = {
      actor,
      reason: `${nextAction} ${latest.target_environment} deployment`,
      expected_version: latest.version
    };
    try {
      const updated = await transitionDeploymentIntent(latest.intent_id, nextAction, request);
      mergeIntent(updated);
    } catch (error) {
      captureActionError(error);
    } finally {
      setBusy(false);
    }
  }

  function mergeIntent(intent: DeploymentIntent) {
    setLedger((current) => ({
      status: "pass",
      blockers: [],
      intents: [intent, ...current.intents.filter((item) => item.intent_id !== intent.intent_id)]
    }));
  }

  function clearActionState() {
    setActionError("");
    setActionBlockers([]);
  }

  function captureActionError(error: unknown) {
    setActionError(error instanceof Error ? error.message : "deployment action failed");
    setActionBlockers(error instanceof ControlPanelApiError ? error.blockers : []);
  }

  return (
    <div className="panel wide deployment-intent-panel" aria-label="Deployment intent control">
      <div className="panel-heading">
        <div>
          <h2>Deployment Intent</h2>
          <p>{latest?.intent_id || "no admitted intent"}</p>
        </div>
        <div className="deployment-heading-state">
          <StatusBadge status={admissionStatus} />
          <button
            type="button"
            className="icon-button compact"
            onClick={() => void loadLedger()}
            aria-label="Refresh deployment intents"
            title="Refresh deployment intents"
          >
            <RefreshCcw size={16} />
          </button>
        </div>
      </div>

      <div className="deployment-signal-grid" aria-label="Deployment admission signals">
        <Signal
          icon={<GitCommitHorizontal />}
          label="CI Evidence"
          value={cycle.ci_evidence?.workflow_run_id || "not admitted"}
          status={cycle.ci_evidence?.status || "blocked"}
        />
        <Signal
          icon={<ListChecks />}
          label="Readiness"
          value={cycle.readiness_evaluation?.decision || "blocked"}
          status={cycle.readiness_evaluation?.status || "blocked"}
        />
        <Signal
          icon={<ShieldCheck />}
          label="Environment Policy"
          value={latest?.promotion_policy.decision || cycle.promotion_policy?.decision || "blocked"}
          status={latest?.promotion_policy.status || cycle.promotion_policy?.status || "blocked"}
        />
        <Signal
          icon={<CircleDotDashed />}
          label="Executor"
          value={latest?.execution_result?.status || "awaiting queue"}
          status={latest?.state === "applied" ? "pass" : latest?.state || "unknown"}
        />
      </div>

      <div className="deployment-workspace">
        <div className="deployment-form">
          <label>
            <span>Target Environment</span>
            <select value={environment} onChange={(event) => setEnvironment(event.target.value as EnvironmentTier)}>
              {Object.keys(namespaces).map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Namespace</span>
            <input value={namespace} readOnly />
          </label>
          <label>
            <span>Requester</span>
            <input value={requester} onChange={(event) => setRequester(event.target.value)} />
          </label>
          <label>
            <span>Approver</span>
            <input value={approver} onChange={(event) => setApprover(event.target.value)} />
          </label>
          <label className="deployment-reason">
            <span>Reason</span>
            <input value={reason} onChange={(event) => setReason(event.target.value)} />
          </label>
          <div className="deployment-actions">
            <button type="button" className="secondary-action" onClick={() => void createIntent()} disabled={busy}>
              <Play size={16} /> Dry Run
            </button>
            <button type="button" className="primary-action" onClick={() => void advanceIntent()} disabled={busy || !nextAction}>
              <CheckCheck size={16} /> {actionLabel(nextAction)}
            </button>
          </div>
        </div>

        <div className="deployment-ledger" aria-label="Deployment transition audit">
          {latest?.transitions.length ? latest.transitions.map((item, index) => (
            <div key={`${item.timestamp}-${index}`} className="deployment-transition">
              <i />
              <div>
                <strong>{item.from_state} → {item.to_state}</strong>
                <span>{item.actor} · {item.result}</span>
                <small>{new Date(item.timestamp).toLocaleString()}</small>
              </div>
            </div>
          )) : <div className="empty-ledger">No deployment transitions</div>}
        </div>
      </div>

      {[...ledger.blockers, ...actionBlockers].length ? (
        <div className="blocker-pills deployment-blockers">
          {[...new Set([...ledger.blockers, ...actionBlockers])].map((blocker) => <span key={blocker}>{blocker}</span>)}
        </div>
      ) : null}
      {actionError ? <div className="policy-error" role="alert">{actionError}</div> : null}
    </div>
  );
}

function Signal({ icon, label, value, status }: { icon: React.ReactNode; label: string; value: string; status: string }) {
  return (
    <div>
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
      <StatusBadge status={status} compact />
    </div>
  );
}

function actionLabel(action: "request-approval" | "approve" | "queue" | null): string {
  if (action === "request-approval") return "Request Approval";
  if (action === "approve") return "Approve";
  if (action === "queue") return "Queue";
  return "Awaiting State";
}
