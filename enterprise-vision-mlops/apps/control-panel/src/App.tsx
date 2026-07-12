import { AlertCircle, Moon, RefreshCcw, Sun } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  fetchControlPanelDiagnostics,
  fetchCycle,
  fetchCycles,
  fetchDecisionRecords,
  fetchLatestCycle,
  fetchLatestDriftReview,
  fetchOrchestrators,
  fetchRuntimeResources
} from "./api/controlPanelClient";
import type {
  ControlPanelDiagnostics,
  CycleRun,
  CycleRunList,
  DecisionRecordList,
  DriftReviewWorkflow,
  OrchestratorConnectionList,
  RuntimeResourceList,
  State
} from "./api/types";
import { DiagnosticsDrawer, type ClientSyncSource } from "./components/DiagnosticsDrawer";
import { CycleSelector } from "./components/CycleSelector";
import { StatusBadge } from "./components/StatusBadge";
import { CycleOverview } from "./views/CycleOverview";
import { DataModelReadiness } from "./views/DataModelReadiness";
import { GateAndRiskPanel } from "./views/GateAndRiskPanel";
import { GovernancePanel } from "./views/GovernancePanel";
import { LifecycleRuns } from "./views/LifecycleRuns";
import { PipelineTimeline } from "./views/PipelineTimeline";
import { PipelineProfileStudio } from "./views/PipelineProfileStudio";
import { ReleaseControl } from "./views/ReleaseControl";
import { TaskAuthoring } from "./views/TaskAuthoring";

type TabKey = "overview" | "configure" | "runs" | "readiness" | "timeline" | "operate" | "gates" | "release" | "governance";

const tabs: Array<{ key: TabKey; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "configure", label: "Configure" },
  { key: "runs", label: "Runs" },
  { key: "readiness", label: "Readiness" },
  { key: "timeline", label: "Timeline" },
  { key: "operate", label: "Operate" },
  { key: "gates", label: "Gates" },
  { key: "release", label: "Release" },
  { key: "governance", label: "Governance" }
];

const sourceDefinitions = [
  { source_id: "catalog", label: "Cycle Catalog" },
  { source_id: "cycle", label: "Selected Cycle" },
  { source_id: "resources", label: "Kubernetes" },
  { source_id: "orchestrators", label: "Orchestrators" },
  { source_id: "diagnostics", label: "Diagnostics" },
  { source_id: "drift", label: "Drift Review" },
  { source_id: "decisions", label: "Decisions" }
];

export function App() {
  const [cycle, setCycle] = useState<CycleRun | null>(null);
  const [catalog, setCatalog] = useState<CycleRunList | null>(null);
  const [selectedCycleId, setSelectedCycleId] = useState("");
  const selectedCycleRef = useRef("");
  const [resourceSnapshot, setResourceSnapshot] = useState<RuntimeResourceList>({
    resources: [],
    observation_status: "unavailable"
  });
  const [orchestratorConnections, setOrchestratorConnections] = useState<OrchestratorConnectionList>({
    orchestrators: [],
    checked_at: "",
    status: "unknown"
  });
  const [diagnostics, setDiagnostics] = useState<ControlPanelDiagnostics | null>(null);
  const [driftWorkflow, setDriftWorkflow] = useState<DriftReviewWorkflow | null>(null);
  const [decisionRegistry, setDecisionRegistry] = useState<DecisionRecordList>({ decisions: [], status: "pass", blockers: [] });
  const [syncSources, setSyncSources] = useState<ClientSyncSource[]>(
    sourceDefinitions.map((source) => ({ ...source, status: "stale" }))
  );
  const [tab, setTab] = useState<TabKey>("overview");
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshedAt, setRefreshedAt] = useState("");
  const refreshInFlight = useRef(false);

  async function loadCycle(background = false) {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    if (!background) setLoading(true);
    try {
      const results = await Promise.allSettled([
        fetchCycles(),
        selectedCycleRef.current ? fetchCycle(selectedCycleRef.current) : fetchLatestCycle(),
        fetchRuntimeResources(),
        fetchOrchestrators(),
        fetchControlPanelDiagnostics(),
        fetchLatestDriftReview(),
        fetchDecisionRecords()
      ]);
      const synchronizedAt = new Date().toISOString();
      setSyncSources((current) => sourceDefinitions.map((source, index) => {
        const previous = current.find((item) => item.source_id === source.source_id);
        const result = results[index];
        if (result.status === "fulfilled") {
          return { ...source, status: "live", last_success_at: synchronizedAt };
        }
        return {
          ...source,
          status: previous?.last_success_at ? "stale" : "error",
          last_success_at: previous?.last_success_at,
          error: errorMessage(result.reason)
        };
      }));

      const [
        catalogResult,
        cycleResult,
        resourceResult,
        orchestratorResult,
        diagnosticsResult,
        driftResult,
        decisionsResult
      ] = results;
      if (catalogResult.status === "fulfilled") {
        setCatalog(catalogResult.value);
        if (!selectedCycleRef.current) {
          selectedCycleRef.current = catalogResult.value.latest_cycle_id;
          setSelectedCycleId(catalogResult.value.latest_cycle_id);
        }
      }
      if (cycleResult.status === "fulfilled") {
        setCycle(cycleResult.value);
        setError("");
      } else {
        setError(errorMessage(cycleResult.reason));
      }
      if (resourceResult.status === "fulfilled") setResourceSnapshot(resourceResult.value);
      if (orchestratorResult.status === "fulfilled") setOrchestratorConnections(orchestratorResult.value);
      if (diagnosticsResult.status === "fulfilled") setDiagnostics(diagnosticsResult.value);
      if (driftResult.status === "fulfilled") setDriftWorkflow(driftResult.value);
      if (decisionsResult.status === "fulfilled") setDecisionRegistry(decisionsResult.value);
      if (results.some((result) => result.status === "fulfilled")) {
        setRefreshedAt(new Date().toLocaleTimeString());
      }
    } finally {
      if (!background) setLoading(false);
      refreshInFlight.current = false;
    }
  }

  useEffect(() => {
    void loadCycle();
    const interval = window.setInterval(() => void loadCycle(true), 5000);
    return () => window.clearInterval(interval);
  }, []);

  async function selectCycle(cycleId: string) {
    selectedCycleRef.current = cycleId;
    setSelectedCycleId(cycleId);
    setLoading(true);
    try {
      const selected = await fetchCycle(cycleId);
      setCycle(selected);
      setError("");
    } catch (selectionError) {
      setError(errorMessage(selectionError));
    } finally {
      setLoading(false);
    }
  }

  const activeView = useMemo(() => {
    if (tab === "runs") return <LifecycleRuns />;
    if (!cycle) return null;
    if (tab === "configure") return <PipelineProfileStudio cycle={cycle} />;
    if (tab === "readiness") return <DataModelReadiness cycle={cycle} />;
    if (tab === "timeline") return <PipelineTimeline cycle={cycle} resourceSnapshot={resourceSnapshot} />;
    if (tab === "operate") return (
      <TaskAuthoring
        cycle={cycle}
        resources={resourceSnapshot.resources}
        orchestrators={orchestratorConnections.orchestrators}
      />
    );
    if (tab === "gates") return <GateAndRiskPanel cycle={cycle} workflow={driftWorkflow} onRefresh={() => loadCycle(true)} />;
    if (tab === "release") return <ReleaseControl cycle={cycle} />;
    if (tab === "governance") return <GovernancePanel registry={decisionRegistry} onRefresh={() => loadCycle(true)} />;
    return <CycleOverview cycle={cycle} />;
  }, [cycle, decisionRegistry, driftWorkflow, orchestratorConnections, resourceSnapshot, tab]);

  const syncStatus: State = syncSources.some((source) => source.status === "error")
    ? "blocked"
    : syncSources.some((source) => source.status === "stale")
      ? "warn"
      : "pass";

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  return (
    <main className="shell" data-theme={theme}>
      <header className="topbar">
        <div>
          <span className="eyebrow">Enterprise Vision MLOps</span>
          <h1>Control Panel</h1>
        </div>
        <CycleSelector catalog={catalog} selectedCycleId={selectedCycleId} onSelect={(cycleId) => void selectCycle(cycleId)} />
        <div className="topbar-actions">
          <div className="sync-indicator" title="Control Panel source synchronization">
            <i className={syncStatus === "pass" ? "live" : "degraded"} />
            <span>{syncStatus === "pass" ? "Live 5s" : "Degraded"}</span>
          </div>
          {cycle ? <StatusBadge status={cycle.status} /> : null}
          <button
            type="button"
            className="icon-button"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            aria-label="Toggle theme"
            title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          >
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <button type="button" className="icon-button" onClick={() => void loadCycle()} aria-label="Refresh cycle">
            <RefreshCcw size={18} />
          </button>
        </div>
      </header>

      <nav className="tabbar" aria-label="Control Panel views">
        {tabs.map((item) => (
          <button
            key={item.key}
            type="button"
            className={item.key === tab ? "active" : ""}
            onClick={() => setTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      {error ? (
        <section className="error-state" role="alert">
          <AlertCircle />
          <div>
            <strong>API unavailable</strong>
            <span>{error}</span>
          </div>
        </section>
      ) : null}

      {loading && !cycle ? <section className="loading-state">Loading CycleRun</section> : activeView}

      <DiagnosticsDrawer diagnostics={diagnostics} clientSources={syncSources} />

      <footer className="footer-line">
        <span>{cycle?.cycle_id || "cycle not loaded"}</span>
        <span>{refreshedAt ? `refreshed ${refreshedAt}` : "waiting"}</span>
      </footer>
    </main>
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Control Panel source request failed";
}
