import { Activity, ChevronDown, FileWarning, Radio } from "lucide-react";

import { compactUri } from "../api/controlPanelClient";
import type { ControlPanelDiagnostics, State } from "../api/types";
import { StatusBadge } from "./StatusBadge";

export interface ClientSyncSource {
  source_id: string;
  label: string;
  status: "live" | "stale" | "error";
  last_success_at?: string;
  error?: string;
}

interface DiagnosticsDrawerProps {
  diagnostics: ControlPanelDiagnostics | null;
  clientSources: ClientSyncSource[];
}

export function DiagnosticsDrawer({ diagnostics, clientSources }: DiagnosticsDrawerProps) {
  const degradedClient = clientSources.some((source) => source.status !== "live");
  const status: State = degradedClient
    ? clientSources.some((source) => source.status === "error") ? "blocked" : "warn"
    : diagnostics?.status || "unknown";
  const count = diagnostics
    ? diagnostics.blocked_count + diagnostics.warn_count + diagnostics.fail_count
    : 0;

  return (
    <details className="diagnostics-drawer" aria-label="Runtime diagnostics">
      <summary>
        <span><Activity /> Runtime Diagnostics</span>
        <span className="diagnostic-summary-state">
          <strong>{count} signals</strong>
          <StatusBadge status={status} compact />
          <ChevronDown />
        </span>
      </summary>

      <div className="sync-source-grid" aria-label="Control Panel synchronization sources">
        {clientSources.map((source) => (
          <div key={source.source_id}>
            <Radio />
            <span>{source.label}</span>
            <strong>{source.status}</strong>
            <small>{source.error || formatSyncTime(source.last_success_at)}</small>
          </div>
        ))}
        {diagnostics?.sources.map((source) => (
          <div key={source.source_id}>
            <Radio />
            <span>{source.source_id}</span>
            <strong>{source.status}</strong>
            <small>{source.age_seconds === null || source.age_seconds === undefined ? "age n/a" : `${source.age_seconds.toFixed(1)}s old`}</small>
          </div>
        ))}
      </div>

      <div className="diagnostic-list" aria-label="Blocked and warning reasons">
        {diagnostics?.diagnostics.length ? diagnostics.diagnostics.map((item) => (
          <details key={item.diagnostic_id} className="diagnostic-entry">
            <summary>
              <FileWarning />
              <span>
                <strong>{item.component}</strong>
                <small>{item.summary}</small>
              </span>
              <StatusBadge status={item.status} compact />
            </summary>
            <dl>
              <div><dt>Code</dt><dd>{item.code}</dd></div>
              <div><dt>Source</dt><dd>{item.source}</dd></div>
              <div><dt>Action</dt><dd>{item.remediation}</dd></div>
              <div><dt>Evidence</dt><dd title={item.evidence_uri || ""}>{compactUri(item.evidence_uri)}</dd></div>
            </dl>
          </details>
        )) : <div className="empty-ledger">No blocked or warning diagnostics</div>}
      </div>

      {diagnostics ? (
        <footer>
          <span>{diagnostics.state_digest.slice(0, 16)}</span>
          <span title={diagnostics.audit_uri || ""}>{compactUri(diagnostics.audit_uri)}</span>
        </footer>
      ) : null}
    </details>
  );
}

function formatSyncTime(value?: string): string {
  if (!value) return "not synchronized";
  return `last ${new Date(value).toLocaleTimeString()}`;
}
