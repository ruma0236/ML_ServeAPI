import { Boxes, ChevronRight, CircleDot, RefreshCcw, Server, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { fetchDeploymentIntents } from "../api/controlPanelClient";
import type { DeploymentIntentList, RuntimeResourceList } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { buildDeploymentInventory, type DeploymentRuntimeState } from "./deploymentInventoryModel";

interface DeploymentInventoryProps {
  resourceSnapshot: RuntimeResourceList;
}

export function DeploymentInventory({ resourceSnapshot }: DeploymentInventoryProps) {
  const [ledger, setLedger] = useState<DeploymentIntentList>({ intents: [], status: "unknown", blockers: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      setLedger(await fetchDeploymentIntents());
      setError("");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Deployment ledger unavailable");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(interval);
  }, []);

  const inventory = useMemo(
    () => buildDeploymentInventory(ledger.intents, resourceSnapshot.resources),
    [ledger.intents, resourceSnapshot.resources]
  );

  return (
    <section className="deployment-inventory" aria-label="Deployed model inventory">
      <header className="deployment-inventory-header">
        <div>
          <span className="eyebrow">Runtime inventory</span>
          <h2>Deployed Models</h2>
          <p>One row per deployment target, reconciled from Kubernetes and the release ledger.</p>
        </div>
        <div className="deployment-live-state">
          <i className={resourceSnapshot.observation_status === "live" ? "is-live" : ""} />
          <span>{resourceSnapshot.observation_status === "live" ? "Live · 5s" : resourceSnapshot.observation_status}</span>
          <button type="button" className="icon-button compact" onClick={() => void refresh()} aria-label="Refresh deployed models" title="Refresh deployed models">
            <RefreshCcw size={16} />
          </button>
        </div>
      </header>

      <div className="deployment-inventory-summary" aria-label="Deployment inventory summary">
        <InventoryMetric icon={<CircleDot />} label="Active" value={inventory.active} tone="active" />
        <InventoryMetric icon={<Server />} label="Scaled down" value={inventory.scaledDown} tone="idle" />
        <InventoryMetric icon={<TriangleAlert />} label="Attention" value={inventory.attention} tone="attention" />
        <InventoryMetric icon={<Boxes />} label="Targets" value={inventory.total} tone="total" />
      </div>

      {loading && inventory.total === 0 ? (
        <div className="deployment-inventory-empty is-loading">Reconciling deployment targets...</div>
      ) : inventory.items.length ? (
        <div className="deployment-inventory-list">
          {inventory.items.map((item) => (
            <details className={`deployment-target deployment-target-${item.runtimeState}`} key={item.id}>
              <summary>
                <span className="deployment-runtime-indicator"><i /></span>
                <span className="deployment-target-identity">
                  <strong>{item.targetName}</strong>
                  <small>{item.candidateId}</small>
                </span>
                <span className="deployment-target-location">
                  <b>{item.environment}</b>
                  <small>{item.namespace}</small>
                </span>
                <span className="deployment-target-replicas">
                  <b>{replicas(item.readyReplicas, item.desiredReplicas)}</b>
                  <small>ready / desired</small>
                </span>
                <span className="deployment-target-state">
                  <StatusBadge status={stateBadge(item.runtimeState)} compact />
                  <small>{runtimeLabel(item.runtimeState)}</small>
                </span>
                <ChevronRight className="deployment-target-chevron" size={17} />
              </summary>
              <div className="deployment-target-detail">
                <Evidence label="Intent" value={item.intentId || "No deployment intent"} />
                <Evidence label="Cycle" value={item.cycleId || "No bound cycle"} />
                <Evidence label="Model digest" value={compactDigest(item.modelDigest)} title={item.modelDigest} />
                <Evidence label="Image digest" value={compactDigest(item.imageDigest)} title={item.imageDigest} />
                <Evidence label="Runtime" value={`${item.observationSource} / ${item.observationStatus}`} />
                <Evidence label="Reason" value={item.reason || item.readiness} />
                <Evidence label="Traffic" value="Not configured" />
                <Evidence label="Observed" value={formatTime(item.updatedAt)} />
              </div>
            </details>
          ))}
        </div>
      ) : (
        <div className="deployment-inventory-empty">No model deployment targets were found.</div>
      )}

      {error ? <div className="policy-error" role="alert">{error}</div> : null}
    </section>
  );
}

function InventoryMetric({ icon, label, value, tone }: { icon: React.ReactNode; label: string; value: number; tone: string }) {
  return <div className={`deployment-inventory-metric metric-${tone}`}>{icon}<span>{label}</span><strong>{value}</strong></div>;
}

function Evidence({ label, value, title }: { label: string; value: string; title?: string }) {
  return <div><span>{label}</span><strong title={title}>{value}</strong></div>;
}

function replicas(ready: number | null, desired: number | null): string {
  return `${ready ?? "-"} / ${desired ?? "-"}`;
}

function runtimeLabel(state: DeploymentRuntimeState): string {
  const labels: Record<DeploymentRuntimeState, string> = {
    active: "Serving",
    scaled_down: "Scaled down",
    degraded: "Degraded",
    pending: "Deploying",
    failed: "Failed",
    rolled_back: "Rolled back",
    unverified: "Runtime unverified"
  };
  return labels[state];
}

function stateBadge(state: DeploymentRuntimeState) {
  if (state === "active") return "pass";
  if (state === "pending") return "running";
  if (state === "scaled_down" || state === "rolled_back") return "warn";
  return "blocked";
}

function compactDigest(value: string): string {
  if (!value) return "Not recorded";
  return value.length > 22 ? `${value.slice(0, 12)}...${value.slice(-8)}` : value;
}

function formatTime(value: string): string {
  if (!value) return "Not observed";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}
