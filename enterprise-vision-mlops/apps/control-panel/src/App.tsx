import {
  Activity,
  AlertCircle,
  BookOpenCheck,
  Moon,
  RefreshCcw,
  ShieldCheck,
  SlidersHorizontal,
  Sun,
  type LucideIcon
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  fetchControlPanelDiagnostics,
  fetchCycle,
  fetchCycles,
  fetchDecisionRecords,
  fetchLatestCycle,
  fetchLatestDriftReview,
  fetchLifecycleRun,
  fetchOrchestrators,
  fetchRuntimeResources
} from "./api/controlPanelClient";
import type {
  ControlPanelDiagnostics,
  CycleRun,
  CycleRunList,
  DecisionRecordList,
  DriftReviewWorkflow,
  LifecycleRun,
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
type WorkspaceKey = "observe" | "design" | "validate" | "govern";

const workspaces: Array<{
  key: WorkspaceKey;
  label: string;
  icon: LucideIcon;
  views: Array<{ key: TabKey; label: string }>;
}> = [
  {
    key: "observe",
    label: "Observe",
    icon: Activity,
    views: [
      { key: "overview", label: "Overview" },
      { key: "runs", label: "Runs" },
      { key: "timeline", label: "Timeline" }
    ]
  },
  {
    key: "design",
    label: "Design",
    icon: SlidersHorizontal,
    views: [
      { key: "configure", label: "Configure" },
      { key: "operate", label: "Operate" }
    ]
  },
  {
    key: "validate",
    label: "Validate",
    icon: ShieldCheck,
    views: [
      { key: "readiness", label: "Readiness" },
      { key: "gates", label: "Gates" },
      { key: "release", label: "Release" }
    ]
  },
  {
    key: "govern",
    label: "Govern",
    icon: BookOpenCheck,
    views: [{ key: "governance", label: "Governance" }]
  }
];

const tabs = workspaces.flatMap((workspace) => workspace.views);

const sourceDefinitions = [
  { source_id: "catalog", label: "Cycle Catalog" },
  { source_id: "cycle", label: "Selected Cycle" },
  { source_id: "resources", label: "Kubernetes" },
  { source_id: "orchestrators", label: "Orchestrators" },
  { source_id: "diagnostics", label: "Diagnostics" },
  { source_id: "drift", label: "Drift Review" },
  { source_id: "decisions", label: "Decisions" }
];

const SELECTED_CYCLE_KEY = "evm.control-panel.selected-cycle";
const SELECTED_RUN_KEY = "evm.control-panel.selected-run";
const SELECTED_TAB_KEY = "evm.control-panel.selected-tab";

export function App() {
  const restoredCycleId = readLocalValue(SELECTED_CYCLE_KEY);
  const [cycle, setCycle] = useState<CycleRun | null>(null);
  const [catalog, setCatalog] = useState<CycleRunList | null>(null);
  const [selectedCycleId, setSelectedCycleId] = useState(restoredCycleId);
  const selectedCycleRef = useRef(restoredCycleId);
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
  const [tab, setTab] = useState<TabKey>(() => restoredTab());
  const [lifecycleContext, setLifecycleContext] = useState<LifecycleRun | null>(null);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshedAt, setRefreshedAt] = useState("");
  const activeRefresh = useRef<Promise<void> | null>(null);

  async function loadCycle(background = false, queueAfterActive = false): Promise<void> {
    if (activeRefresh.current) {
      if (queueAfterActive) {
        await activeRefresh.current;
        return loadCycle(background, false);
      }
      return activeRefresh.current;
    }
    const operation = (async () => {
      if (!background) setLoading(true);
      try {
        const results = await Promise.allSettled([
          fetchCycles(),
          selectedCycleRef.current ? fetchCycle(selectedCycleRef.current) : fetchLatestCycle(),
          fetchRuntimeResources(),
          fetchOrchestrators(),
          fetchControlPanelDiagnostics(selectedCycleRef.current || undefined),
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
      }
    })();
    activeRefresh.current = operation;
    try {
      await operation;
    } finally {
      if (activeRefresh.current === operation) activeRefresh.current = null;
    }
  }

  useEffect(() => {
    void loadCycle();
    const interval = window.setInterval(() => void loadCycle(true), 5000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    const runId = readLocalValue(SELECTED_RUN_KEY);
    if (!runId) return;
    void fetchLifecycleRun(runId)
      .then(async (run) => {
        setLifecycleContext(run);
        if (run.cycle_id && run.cycle_id !== selectedCycleRef.current) {
          await selectCycle(run.cycle_id, true);
        }
      })
      .catch(() => removeLocalValue(SELECTED_RUN_KEY));
  }, []);

  async function selectCycle(cycleId: string, preserveLifecycleContext = false) {
    if (!preserveLifecycleContext) {
      setLifecycleContext(null);
      removeLocalValue(SELECTED_RUN_KEY);
    }
    selectedCycleRef.current = cycleId;
    setSelectedCycleId(cycleId);
    writeLocalValue(SELECTED_CYCLE_KEY, cycleId);
    setLoading(true);
    try {
      const [selected, selectedDiagnostics] = await Promise.all([
        fetchCycle(cycleId),
        fetchControlPanelDiagnostics(cycleId)
      ]);
      setCycle(selected);
      setDiagnostics(selectedDiagnostics);
      setError("");
    } catch (selectionError) {
      setError(errorMessage(selectionError));
    } finally {
      setLoading(false);
    }
  }

  async function selectLifecycleContext(run: LifecycleRun) {
    setLifecycleContext(run);
    writeLocalValue(SELECTED_RUN_KEY, run.run_id);
    if (run.cycle_id) await selectCycle(run.cycle_id, true);
  }

  const activeView = useMemo(() => {
    if (tab === "runs") return <LifecycleRuns onCycleContext={(run) => void selectLifecycleContext(run)} />;
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
    if (tab === "gates") return <GateAndRiskPanel cycle={cycle} workflow={driftWorkflow} onRefresh={() => loadCycle(true, true)} />;
    if (tab === "release") return <ReleaseControl cycle={cycle} lifecycleRun={lifecycleContext} />;
    if (tab === "governance") return <GovernancePanel cycle={cycle} lifecycleRun={lifecycleContext} registry={decisionRegistry} onRefresh={() => loadCycle(true, true)} />;
    return <CycleOverview cycle={cycle} />;
  }, [cycle, decisionRegistry, driftWorkflow, lifecycleContext, orchestratorConnections, resourceSnapshot, tab]);

  const syncStatus: State = syncSources.some((source) => source.status === "error")
    ? "blocked"
    : syncSources.some((source) => source.status === "stale")
      ? "warn"
      : "pass";
  const activeWorkspace = workspaceForTab(tab);

  function selectTab(nextTab: TabKey) {
    setTab(nextTab);
    writeLocalValue(SELECTED_TAB_KEY, nextTab);
  }

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
          {lifecycleContext ? (
            <div className={`execution-context run-context-${lifecycleContext.state}`} title={lifecycleContext.run_id}>
              <span>Run</span>
              <strong>{formatLifecycleState(lifecycleContext.state)}</strong>
              <em>{Math.round(lifecycleContext.progress * 100)}%</em>
            </div>
          ) : null}
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
          <button type="button" className="icon-button" onClick={() => void loadCycle(false, true)} aria-label="Refresh cycle">
            <RefreshCcw size={18} />
          </button>
        </div>
      </header>

      <section className="navigation-shell">
        <nav className="workspace-nav" aria-label="Control Panel workspaces">
          {workspaces.map((workspace) => {
            const Icon = workspace.icon;
            return (
              <button
                key={workspace.key}
                type="button"
                className={workspace.key === activeWorkspace.key ? "active" : ""}
                onClick={() => selectTab(workspace.views[0].key)}
                aria-pressed={workspace.key === activeWorkspace.key}
                aria-label={workspace.label}
              >
                <Icon size={17} />
                <span>{workspace.label}</span>
              </button>
            );
          })}
        </nav>
        <nav className="view-nav" aria-label="Control Panel views">
          {activeWorkspace.views.map((item) => (
            <button
              key={item.key}
              type="button"
              className={item.key === tab ? "active" : ""}
              onClick={() => selectTab(item.key)}
              aria-label={item.label}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </section>

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

function readLocalValue(key: string): string {
  try {
    return window.localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function writeLocalValue(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Browser storage is an ergonomic enhancement, not a runtime dependency.
  }
}

function removeLocalValue(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Browser storage is an ergonomic enhancement, not a runtime dependency.
  }
}

function restoredTab(): TabKey {
  const value = readLocalValue(SELECTED_TAB_KEY);
  return tabs.some((item) => item.key === value) ? value as TabKey : "overview";
}


function workspaceForTab(tab: TabKey) {
  return workspaces.find((workspace) => workspace.views.some((view) => view.key === tab)) || workspaces[0];
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Control Panel source request failed";
}


function formatLifecycleState(value: LifecycleRun["state"]): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}
