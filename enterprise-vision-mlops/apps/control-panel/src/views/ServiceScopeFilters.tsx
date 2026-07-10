import { Building2, CheckCircle2, RefreshCcw, ShieldAlert, UsersRound } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { evaluatePromotionPolicy } from "../api/controlPanelClient";
import type {
  CycleRun,
  EnvironmentTier,
  PromotionPolicyDecision
} from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

interface ServiceScopeFiltersProps {
  cycle: CycleRun;
}

const environmentTiers: EnvironmentTier[] = [
  "dev",
  "test",
  "staging",
  "pre-production",
  "production"
];
const namespaceDefaults: Record<EnvironmentTier, string> = {
  dev: "evm-dev",
  test: "evm-test",
  staging: "evm-staging",
  "pre-production": "evm-pre-production",
  production: "evm-production"
};

export function ServiceScopeFilters({ cycle }: ServiceScopeFiltersProps) {
  const initialTier = cycle.promotion_policy?.target_environment || cycle.environment?.tier || "staging";
  const [targetEnvironment, setTargetEnvironment] = useState<EnvironmentTier>(initialTier);
  const [targetNamespace, setTargetNamespace] = useState(
    cycle.promotion_policy?.target_namespace || cycle.environment?.namespace || namespaceDefaults[initialTier]
  );
  const [requester, setRequester] = useState(
    cycle.promotion_policy?.requester || cycle.tenant?.model_owner || "ml-platform"
  );
  const [approver, setApprover] = useState(cycle.promotion_policy?.approver || "");
  const [policy, setPolicy] = useState<PromotionPolicyDecision | null>(cycle.promotion_policy || null);
  const [evaluating, setEvaluating] = useState(false);
  const [policyError, setPolicyError] = useState("");
  const owners = useMemo(
    () => [
      { label: "Data", value: cycle.tenant?.data_owner, status: cycle.tenant?.data_owner ? "pass" : "blocked" },
      { label: "Model", value: cycle.tenant?.model_owner, status: cycle.tenant?.model_owner ? "pass" : "blocked" },
      { label: "Ops", value: cycle.tenant?.ops_owner, status: cycle.tenant?.ops_owner ? "pass" : "blocked" }
    ],
    [cycle]
  );

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (!targetNamespace.trim() || !requester.trim()) return;
      setEvaluating(true);
      setPolicyError("");
      void evaluatePromotionPolicy({
        target_environment: targetEnvironment,
        target_namespace: targetNamespace.trim(),
        requester: requester.trim(),
        approver: approver.trim() || null
      })
        .then((decision) => {
          if (!cancelled) setPolicy(decision);
        })
        .catch((error) => {
          if (!cancelled) {
            setPolicyError(error instanceof Error ? error.message : "promotion policy request failed");
          }
        })
        .finally(() => {
          if (!cancelled) setEvaluating(false);
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [approver, cycle.readiness_evaluation?.input_digest, requester, targetEnvironment, targetNamespace]);

  const blockers = policy?.reason_codes.length
    ? policy.reason_codes
    : [policy?.decision === "allow" ? "all required checks passed" : "no policy decision returned"];
  const requiredChecks = policy?.checks.filter((check) => check.required) || [];

  function selectEnvironment(tier: EnvironmentTier) {
    setTargetEnvironment(tier);
    setTargetNamespace(namespaceDefaults[tier]);
    if (tier !== "production" && tier !== "pre-production") setApprover("");
  }

  return (
    <section className="panel wide service-scope-panel" aria-label="Enterprise service scope">
      <div className="panel-heading">
        <div>
          <h2>Enterprise Scope</h2>
          <p>{cycle.tenant?.department || "unknown"} / {cycle.tenant?.service_scope || "unknown"}</p>
        </div>
        <div className="policy-heading-state">
          {evaluating ? <RefreshCcw className="spin" aria-label="Evaluating policy" /> : <Building2 />}
          <StatusBadge status={policy?.status || "unknown"} />
        </div>
      </div>

      <div className="scope-layout">
        <div className="scope-filter-card policy-target-card">
          <header>
            <ShieldAlert />
            <strong>Target Policy Input</strong>
          </header>
          <div className="service-scope-value">
            <span>Service scope</span>
            <strong>{cycle.tenant?.service_scope || "not-bound"}</strong>
          </div>
          <div className="policy-input-grid">
            <label className="scope-select">
              <span>Environment tier</span>
              <select
                aria-label="Target environment"
                value={targetEnvironment}
                onChange={(event) => selectEnvironment(event.target.value as EnvironmentTier)}
              >
                {environmentTiers.map((tier) => (
                  <option key={tier} value={tier}>{tier}</option>
                ))}
              </select>
            </label>
            <label className="scope-select">
              <span>Namespace</span>
              <input
                aria-label="Target namespace"
                value={targetNamespace}
                onChange={(event) => setTargetNamespace(event.target.value)}
              />
            </label>
            <label className="scope-select">
              <span>Requester</span>
              <input value={requester} onChange={(event) => setRequester(event.target.value)} />
            </label>
            <label className="scope-select">
              <span>Approver</span>
              <input
                value={approver}
                placeholder={targetEnvironment === "production" ? "required for production" : "not required"}
                onChange={(event) => setApprover(event.target.value)}
              />
            </label>
          </div>
          {policyError ? <p className="policy-error" role="alert">{policyError}</p> : null}
        </div>

        <div className="owner-grid" aria-label="Owner coverage">
          <header>
            <UsersRound />
            <strong>Owner Coverage</strong>
            <StatusBadge status={cycle.tenant?.ownership_status || "unknown"} compact />
          </header>
          {owners.map((owner) => (
            <div key={owner.label}>
              <span>{owner.label}</span>
              <strong>{owner.value || "unassigned"}</strong>
              <StatusBadge status={owner.status} compact />
            </div>
          ))}
        </div>

        <div className="environment-card" aria-label="Promotion policy decision">
          <header>
            <CheckCircle2 />
            <strong>Promotion Decision</strong>
            <StatusBadge status={policy?.status || "unknown"} compact />
          </header>
          <dl className="detail-list">
            <div><dt>Decision</dt><dd>{policy?.decision || "unknown"}</dd></div>
            <div><dt>Target</dt><dd>{policy?.target_environment || targetEnvironment}</dd></div>
            <div><dt>Namespace</dt><dd>{policy?.target_namespace || targetNamespace}</dd></div>
            <div><dt>Approval</dt><dd>{policy?.approval_policy || "not-evaluated"}</dd></div>
            <div><dt>Policy</dt><dd>{policy?.policy_version || "not-bound"}</dd></div>
            <div><dt>Audit</dt><dd>{policy?.decision_id || "not-written"}</dd></div>
          </dl>
          <div className="policy-check-grid" aria-label="Promotion policy checks">
            {requiredChecks.map((check) => (
              <div key={check.check_id}>
                <span>{check.check_id.replaceAll("_", " ")}</span>
                <StatusBadge status={check.status} compact />
              </div>
            ))}
          </div>
          <div className="blocker-pills">
            {blockers.map((blocker) => <span key={blocker}>{blocker}</span>)}
          </div>
        </div>
      </div>
    </section>
  );
}
