import { Building2, Filter, ShieldAlert, UsersRound } from "lucide-react";
import { useMemo, useState } from "react";

import type { CycleRun } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

interface ServiceScopeFiltersProps {
  cycle: CycleRun;
}

const serviceScopes = ["internal-team", "internal-department", "external-production"];

export function ServiceScopeFilters({ cycle }: ServiceScopeFiltersProps) {
  const [scopeFilter, setScopeFilter] = useState(cycle.tenant?.service_scope || "internal-department");
  const [environmentFilter, setEnvironmentFilter] = useState(cycle.environment?.tier || "staging");
  const owners = useMemo(
    () => [
      { label: "Data", value: cycle.tenant?.data_owner, status: cycle.tenant?.data_owner ? "pass" : "blocked" },
      { label: "Model", value: cycle.tenant?.model_owner, status: cycle.tenant?.model_owner ? "pass" : "blocked" },
      { label: "Ops", value: cycle.tenant?.ops_owner, status: cycle.tenant?.ops_owner ? "pass" : "blocked" }
    ],
    [cycle]
  );
  const blockers = cycle.environment?.promotion_blockers?.length
    ? cycle.environment.promotion_blockers
    : cycle.tenant?.missing_owners?.length
      ? cycle.tenant.missing_owners
      : ["no environment blockers recorded"];

  return (
    <section className="panel wide service-scope-panel" aria-label="Enterprise service scope">
      <div className="panel-heading">
        <div>
          <h2>Enterprise Scope</h2>
          <p>{cycle.tenant?.department || "unknown"} / {cycle.environment?.name || "unknown"}</p>
        </div>
        <Building2 />
      </div>

      <div className="scope-layout">
        <div className="scope-filter-card">
          <header>
            <Filter />
            <strong>Service Filter State</strong>
          </header>
          <div className="segmented-control" role="group" aria-label="Service scope filter">
            {serviceScopes.map((scope) => (
              <button
                key={scope}
                type="button"
                className={scope === scopeFilter ? "active" : ""}
                onClick={() => setScopeFilter(scope)}
              >
                {scope}
              </button>
            ))}
          </div>
          <label className="scope-select">
            <span>Environment tier</span>
            <select value={environmentFilter} onChange={(event) => setEnvironmentFilter(event.target.value)}>
              {["dev", "test", "staging", "pre-production", "production"].map((tier) => (
                <option key={tier} value={tier}>
                  {tier}
                </option>
              ))}
            </select>
          </label>
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

        <div className="environment-card">
          <header>
            <ShieldAlert />
            <strong>Environment Gate</strong>
            <StatusBadge status={cycle.environment?.promotion_state === "blocked" ? "blocked" : "pass"} compact />
          </header>
          <dl className="detail-list">
            <div>
              <dt>Cluster</dt>
              <dd>{cycle.environment?.cluster || "not-bound"}</dd>
            </div>
            <div>
              <dt>Namespace</dt>
              <dd>{cycle.environment?.namespace || "not-bound"}</dd>
            </div>
            <div>
              <dt>Approval</dt>
              <dd>{cycle.environment?.approval_policy || "manual-owner-approval"}</dd>
            </div>
            <div>
              <dt>Promotion</dt>
              <dd>{cycle.environment?.promotion_state || "unknown"}</dd>
            </div>
          </dl>
          <div className="blocker-pills">
            {blockers.map((blocker) => (
              <span key={blocker}>{blocker}</span>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
