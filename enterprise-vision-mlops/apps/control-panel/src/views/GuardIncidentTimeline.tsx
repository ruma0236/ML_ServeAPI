import { AlertTriangle, Clock3, Fingerprint, KeyRound, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { fetchGuardIncidents } from "../api/controlPanelClient";
import type { GuardIncident, GuardIncidentPlane } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

interface GuardIncidentTimelineProps {
  client?: () => Promise<GuardIncidentPlane>;
  pollMs?: number;
}

export function GuardIncidentTimeline({
  client = fetchGuardIncidents,
  pollMs = 5000
}: GuardIncidentTimelineProps) {
  const [snapshot, setSnapshot] = useState<GuardIncidentPlane | null>(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const next = await client();
        if (!cancelled) {
          setSnapshot(next);
          setError("");
        }
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Incident sync failed");
      } finally {
        if (!cancelled) setRefreshing(false);
      }
    }
    void refresh();
    const interval = window.setInterval(() => void refresh(), pollMs);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [client, pollMs]);

  const activeOwners = useMemo(
    () => snapshot?.leases.filter((lease) => lease.state === "active").length || 0,
    [snapshot]
  );
  const planeTone = snapshot?.status === "live"
    ? "pass"
    : snapshot?.status === "stale"
      ? "warn"
      : "blocked";

  return (
    <section className="incident-workspace" aria-label="Guard incident timeline">
      <header className="incident-hero">
        <div className="incident-title">
          <span className={`incident-pulse ${snapshot?.status || "unavailable"}`}><ShieldCheck size={20} /></span>
          <div>
            <small>Read-only recovery coordination</small>
            <h2>Guard Incidents</h2>
            <p>Exact identity, owner fence, evidence, and recovery timing</p>
          </div>
        </div>
        <div className="incident-sync">
          <StatusBadge status={refreshing ? "running" : planeTone} />
          <span>{snapshot ? formatTime(snapshot.generated_at_utc) : "waiting for snapshot"}</span>
        </div>
      </header>

      <div className="incident-kpis" aria-label="Incident coordination summary">
        <MetricTile icon={<AlertTriangle size={17} />} label="Incidents" value={snapshot?.incidents.length || 0} />
        <MetricTile icon={<KeyRound size={17} />} label="Active owners" value={activeOwners} />
        <MetricTile icon={<ShieldCheck size={17} />} label="Recommendations" value={snapshot?.actions.length || 0} />
        <MetricTile icon={<Clock3 size={17} />} label="Blocked decisions" value={snapshot?.blocked_decision_count || 0} />
      </div>

      {error ? <div className="incident-alert" role="alert"><AlertTriangle size={16} />{error}</div> : null}
      {snapshot?.active_blockers.length ? (
        <div className="incident-alert warning" role="status">
          <AlertTriangle size={16} />
          <span>{snapshot.active_blockers.map(humanize).join(" / ")}</span>
        </div>
      ) : null}

      <div className="incident-timeline">
        {(snapshot?.incidents || []).map((incident) => (
          <IncidentRow key={incident.incident_id} incident={incident} />
        ))}
        {!refreshing && !snapshot?.incidents.length ? (
          <div className="incident-empty">
            <ShieldCheck size={22} />
            <strong>No incident evidence</strong>
            <span>{snapshot?.status === "unavailable" ? "Snapshot is unavailable" : "No guard incident is active"}</span>
          </div>
        ) : null}
      </div>

      <footer className="incident-boundary">
        <Fingerprint size={15} />
        <span>Mutation endpoint</span>
        <strong>{snapshot?.mutation_endpoint_available ? "Enabled" : "Not exposed"}</strong>
        <code title={snapshot?.source_revision}>{shortId(snapshot?.source_revision || "unknown")}</code>
      </footer>
    </section>
  );
}

function IncidentRow({ incident }: { incident: GuardIncident }) {
  const timing = [
    { label: "Collect", value: milliseconds(incident.timing.collection_delay_ms) },
    { label: "Correlate", value: milliseconds(incident.timing.correlation_overhead_ms) },
    { label: "Contain", value: seconds(incident.timing.containment_seconds) },
    { label: "Recover", value: seconds(incident.timing.recovery_seconds) }
  ];
  return (
    <article className={`incident-row state-${incidentTone(incident)}`}>
      <div className="incident-rail" aria-hidden="true"><i /></div>
      <div className="incident-main">
        <header>
          <div>
            <small>{incident.target_class || "unbound target"}</small>
            <strong>{humanize(incident.state)}</strong>
            <span>{incident.owner_id || "No recovery owner"}{incident.fencing_token ? ` / fence ${incident.fencing_token}` : ""}</span>
          </div>
          <StatusBadge status={incidentTone(incident)} compact />
        </header>
        <div className="incident-timing" aria-label="Incident timing breakdown">
          {timing.map((item, index) => (
            <div key={item.label} className={item.value === "-" ? "empty" : "measured"}>
              <span>{item.label}</span><strong>{item.value}</strong>{index < timing.length - 1 ? <i /> : null}
            </div>
          ))}
        </div>
        <div className="incident-facts">
          <span>{incident.event_count} events</span>
          <span>{incident.causal_edge_count} causal edges</span>
          <span>{incident.authorized_recommendation_count} recommendations</span>
          <span>{incident.blockers.length ? incident.blockers.map(humanize).join(", ") : "No active blocker"}</span>
        </div>
        <details className="incident-evidence">
          <summary>Identity and evidence</summary>
          <dl>
            <div><dt>Incident</dt><dd title={incident.incident_id}>{shortId(incident.incident_id)}</dd></div>
            <div><dt>Correlation</dt><dd title={incident.correlation_id}>{shortId(incident.correlation_id)}</dd></div>
            <div><dt>Target digest</dt><dd title={incident.target_identity_digest || ""}>{shortId(incident.target_identity_digest || "unbound")}</dd></div>
            <div><dt>Updated</dt><dd>{formatTime(incident.updated_at_utc)}</dd></div>
          </dl>
          {incident.child_evidence_uris.map((uri) => <code key={uri} title={uri}>{compactUri(uri)}</code>)}
        </details>
      </div>
    </article>
  );
}

function MetricTile({ icon, label, value }: { icon: React.ReactNode; label: string; value: number }) {
  return <div className="incident-kpi"><span>{icon}</span><div><small>{label}</small><strong>{value}</strong></div></div>;
}

function incidentTone(incident: GuardIncident): string {
  if (incident.blockers.length || ["blocked", "held", "rollback_pending"].includes(incident.state)) return "blocked";
  if (["recovered", "validated", "closed", "rolled_back"].includes(incident.state)) return "pass";
  if (["recovery_owned", "recovering", "contained"].includes(incident.state)) return "running";
  return "warn";
}

function milliseconds(value?: number | null): string {
  return value == null ? "-" : value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${value.toFixed(1)} ms`;
}

function seconds(value?: number | null): string {
  return value == null ? "-" : `${value.toFixed(value >= 10 ? 1 : 2)} s`;
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function shortId(value: string): string {
  if (value.length <= 18) return value;
  return `${value.slice(0, 9)}...${value.slice(-6)}`;
}

function compactUri(value: string): string {
  const normalized = value.replaceAll("\\", "/");
  const parts = normalized.split("/").filter(Boolean);
  return parts.slice(-3).join("/") || value;
}

function formatTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "unknown" : parsed.toLocaleTimeString();
}
