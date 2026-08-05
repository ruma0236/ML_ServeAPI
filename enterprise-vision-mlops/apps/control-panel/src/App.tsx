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
  ModelCandidateSelection,
  OrchestratorConnectionList,
  RuntimeResourceList
} from "./api/types";
import { DiagnosticsDrawer, type ClientSyncSource } from "./components/DiagnosticsDrawer";
import { CycleSelector } from "./components/CycleSelector";
import { StatusBadge } from "./components/StatusBadge";
import { CycleOverview } from "./views/CycleOverview";
import { DataModelReadiness } from "./views/DataModelReadiness";
import { GateAndRiskPanel } from "./views/GateAndRiskPanel";
import { GovernancePanel } from "./views/GovernancePanel";
import { GuardIncidentTimeline } from "./views/GuardIncidentTimeline";
import { LifecycleRuns } from "./views/LifecycleRuns";
import { PipelineTimeline } from "./views/PipelineTimeline";
import { PipelineProfileStudio } from "./views/PipelineProfileStudio";
import { ReleaseControl } from "./views/ReleaseControl";
import { ScenarioWorkloads } from "./views/ScenarioWorkloads";
import { StageWorkbench } from "./views/StageWorkbench";
import { TaskAuthoring } from "./views/TaskAuthoring";

type TabKey = "overview" | "configure" | "workloads" | "stages" | "runs" | "readiness" | "timeline" | "operate" | "gates" | "release" | "incidents" | "governance";
type WorkspaceKey = "overview" | "build" | "deploy" | "govern";

const workspaces: Array<{
  key: WorkspaceKey;
  label: string;
  icon: LucideIcon;
  views: Array<{ key: TabKey; label: string }>;
}> = [
  {
    key: "overview",
    label: "Overview",
    icon: Activity,
    views: [
      { key: "overview", label: "Operations" },
      { key: "runs", label: "Runs" },
      { key: "timeline", label: "Resources" }
    ]
  },
  {
    key: "build",
    label: "Build",
    icon: SlidersHorizontal,
    views: [
      { key: "configure", label: "Pipeline Studio" },
      { key: "workloads", label: "AI Workloads" },
      { key: "stages", label: "Handoffs" },
      { key: "operate", label: "Runtime Tasks" }
    ]
  },
  {
    key: "deploy",
    label: "Deploy",
    icon: ShieldCheck,
    views: [
      { key: "release", label: "Models" },
      { key: "readiness", label: "Readiness" },
      { key: "gates", label: "Quality & Drift" },
    ]
  },
  {
    key: "govern",
    label: "Govern",
    icon: BookOpenCheck,
    views: [
      { key: "incidents", label: "Incidents" },
      { key: "governance", label: "Decisions" }
    ]
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

const LEGACY_VIEW_KEYS = [
  "evm.control-panel.selected-cycle",
  "evm.control-panel.selected-run",
  "evm.control-panel.selected-tab"
];

interface ViewLocation {
  cycleId: string;
  runId: string;
  modelSelectionId: string;
  tab: TabKey;
}

type SyncMode = "connecting" | "live" | "partial" | "unavailable";

export function App() {
  const [initialLocation] = useState<ViewLocation>(readViewLocation);
  const [cycle, setCycle] = useState<CycleRun | null>(null);
  const [catalog, setCatalog] = useState<CycleRunList | null>(null);
  const [selectedCycleId, setSelectedCycleId] = useState(initialLocation.cycleId);
  const selectedCycleRef = useRef(initialLocation.cycleId);
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
  const [tab, setTab] = useState<TabKey>(initialLocation.tab);
  const [lifecycleContext, setLifecycleContext] = useState<LifecycleRun | null>(null);
  const [modelSelectionId, setModelSelectionId] = useState(initialLocation.modelSelectionId);
  const [blueprintTarget, setBlueprintTarget] = useState<{
    profileId: string;
    version: number;
  } | null>(null);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [initialSyncComplete, setInitialSyncComplete] = useState(false);
  const [refreshedAt, setRefreshedAt] = useState("");
  const activeRefresh = useRef<Promise<void> | null>(null);
  const selectionVersion = useRef(0);

  function applyResourceSnapshot(next: RuntimeResourceList) {
    setResourceSnapshot((current) => {
      const currentObserved = Date.parse(current.observed_at || "");
      const nextObserved = Date.parse(next.observed_at || "");
      if (Number.isFinite(currentObserved) && Number.isFinite(nextObserved) && nextObserved < currentObserved) {
        return current;
      }
      return next;
    });
  }

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
      const requestSelectionVersion = selectionVersion.current;
      const requestedCycleId = selectedCycleRef.current;
      try {
        const results = await Promise.allSettled([
          fetchCycles(),
          requestedCycleId ? fetchCycle(requestedCycleId) : fetchLatestCycle(),
          fetchRuntimeResources(),
          fetchOrchestrators(),
          fetchControlPanelDiagnostics(requestedCycleId || undefined),
          fetchLatestDriftReview(),
          fetchDecisionRecords()
        ]);
        const synchronizedAt = new Date().toISOString();
        const selectionRequestIsCurrent = requestSelectionVersion === selectionVersion.current;
        setSyncSources((current) => sourceDefinitions.map((source, index) => {
          const previous = current.find((item) => item.source_id === source.source_id);
          if (["cycle", "diagnostics"].includes(source.source_id) && !selectionRequestIsCurrent) {
            return previous || { ...source, status: "stale" };
          }
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
        if (cycleResult.status === "fulfilled" && selectionRequestIsCurrent) {
          setCycle(cycleResult.value);
          if (!selectedCycleRef.current) {
            selectedCycleRef.current = cycleResult.value.cycle_id;
            setSelectedCycleId(cycleResult.value.cycle_id);
          }
          setError("");
        } else if (cycleResult.status === "rejected" && selectionRequestIsCurrent) {
          setError(errorMessage(cycleResult.reason));
        }
        if (resourceResult.status === "fulfilled") applyResourceSnapshot(resourceResult.value);
        if (orchestratorResult.status === "fulfilled") setOrchestratorConnections(orchestratorResult.value);
        if (diagnosticsResult.status === "fulfilled" && selectionRequestIsCurrent) {
          setDiagnostics(diagnosticsResult.value);
        }
        if (driftResult.status === "fulfilled") setDriftWorkflow(driftResult.value);
        if (decisionsResult.status === "fulfilled") setDecisionRegistry(decisionsResult.value);
        if (results.some((result) => result.status === "fulfilled")) {
          setRefreshedAt(new Date().toLocaleTimeString());
        }
      } finally {
        if (!background && requestSelectionVersion === selectionVersion.current) {
          setLoading(false);
        }
        setInitialSyncComplete(true);
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
    LEGACY_VIEW_KEYS.forEach(removeLocalValue);
    void loadCycle();
    const interval = window.setInterval(() => void loadCycle(true), 5000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function refreshResourcesOnly() {
      try {
        const next = await fetchRuntimeResources();
        if (cancelled) return;
        applyResourceSnapshot(next);
        const synchronizedAt = new Date().toISOString();
        setSyncSources((current) => current.map((source) => source.source_id === "resources"
          ? { ...source, status: "live", last_success_at: synchronizedAt, error: undefined }
          : source));
      } catch (resourceError) {
        if (cancelled) return;
        setSyncSources((current) => current.map((source) => source.source_id === "resources"
          ? {
              ...source,
              status: source.last_success_at ? "stale" : "error",
              error: errorMessage(resourceError)
            }
          : source));
      }
    }

    void refreshResourcesOnly();
    const interval = window.setInterval(() => void refreshResourcesOnly(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    const runId = initialLocation.runId;
    if (!runId) return;
    void fetchLifecycleRun(runId)
      .then(async (run) => {
        setLifecycleContext(run);
        if (run.cycle_id && run.cycle_id !== selectedCycleRef.current) {
          await selectCycle(run.cycle_id, true, run.run_id);
        }
      })
      .catch(() => writeViewLocation({ runId: "" }));
  }, []);

  async function selectCycle(
    cycleId: string,
    preserveLifecycleContext = false,
    runId = "",
    preserveModelSelection = false
  ) {
    if (!preserveLifecycleContext) {
      setLifecycleContext(null);
    }
    if (!preserveModelSelection) setModelSelectionId("");
    selectedCycleRef.current = cycleId;
    setSelectedCycleId(cycleId);
    const requestSelectionVersion = ++selectionVersion.current;
    const selectedSummary = catalog?.cycles.find((item) => item.cycle_id === cycleId);
    const liveSelection = Boolean(selectedSummary?.live || cycleId === catalog?.latest_cycle_id);
    writeViewLocation({
      cycleId: liveSelection && !runId ? "" : cycleId,
      runId: preserveLifecycleContext ? runId : "",
      modelSelectionId: preserveModelSelection ? modelSelectionId : ""
    });
    setLoading(true);
    try {
      const [selected, selectedDiagnostics] = await Promise.all([
        fetchCycle(cycleId),
        fetchControlPanelDiagnostics(cycleId)
      ]);
      if (requestSelectionVersion !== selectionVersion.current) return;
      setCycle(selected);
      setDiagnostics(selectedDiagnostics);
      setError("");
    } catch (selectionError) {
      if (requestSelectionVersion === selectionVersion.current) {
        setError(errorMessage(selectionError));
      }
    } finally {
      if (requestSelectionVersion === selectionVersion.current) setLoading(false);
    }
  }

  async function selectLifecycleContext(run: LifecycleRun) {
    setLifecycleContext(run);
    if (run.cycle_id) await selectCycle(run.cycle_id, true, run.run_id);
  }

  async function returnToLive() {
    const liveCycleId = catalog?.cycles.find((item) => item.live)?.cycle_id
      || catalog?.latest_cycle_id
      || "";
    if (liveCycleId) {
      await selectCycle(liveCycleId);
      return;
    }
    setLifecycleContext(null);
    setModelSelectionId("");
    selectionVersion.current += 1;
    selectedCycleRef.current = "";
    setSelectedCycleId("");
    writeViewLocation({ cycleId: "", runId: "", modelSelectionId: "" });
    await loadCycle(false, true);
  }

  const activeView = useMemo(() => {
    if (tab === "runs") return (
      <LifecycleRuns
        onCycleContext={(run) => void selectLifecycleContext(run)}
        onOpenBlueprint={(run) => {
          setBlueprintTarget({ profileId: run.profile_id, version: run.profile_version });
          selectTab("configure");
        }}
      />
    );
    if (tab === "workloads") return <ScenarioWorkloads />;
    if (!cycle) return null;
    if (tab === "stages") return <StageWorkbench onPromote={(selection) => void openCandidatePromotion(selection)} />;
    if (tab === "configure") return (
      <PipelineProfileStudio cycle={cycle} profileTarget={blueprintTarget} />
    );
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
    if (tab === "release") return (
      <ReleaseControl
        cycle={cycle}
        lifecycleRun={lifecycleContext}
        modelSelectionId={modelSelectionId}
        resourceSnapshot={resourceSnapshot}
        onPromote={(selection) => void openCandidatePromotion(selection)}
      />
    );
    if (tab === "incidents") return <GuardIncidentTimeline />;
    if (tab === "governance") return <GovernancePanel cycle={cycle} lifecycleRun={lifecycleContext} registry={decisionRegistry} onRefresh={() => loadCycle(true, true)} />;
    return (
      <CycleOverview
        cycle={cycle}
        resourceSnapshot={resourceSnapshot}
        onOpenRuns={() => selectTab("runs")}
        onOpenDeployments={() => selectTab("release")}
      />
    );
  }, [blueprintTarget, cycle, decisionRegistry, driftWorkflow, lifecycleContext, modelSelectionId, orchestratorConnections, resourceSnapshot, tab]);

  async function openCandidatePromotion(selection: ModelCandidateSelection) {
    setModelSelectionId(selection.selection_id);
    await selectCycle(selection.cycle_id, false, "", true);
    setTab("release");
    writeViewLocation({
      cycleId: selection.cycle_id,
      runId: "",
      modelSelectionId: selection.selection_id,
      tab: "release"
    });
  }

  const criticalSyncError = syncSources.some(
    (source) => ["catalog", "cycle"].includes(source.source_id) && source.status === "error"
  );
  const syncMode: SyncMode = loading
    ? "connecting"
    : criticalSyncError
      ? "unavailable"
      : syncSources.some((source) => source.status === "error" || source.status === "stale")
        ? "partial"
        : "live";
  const syncLabel = syncMode === "connecting"
    ? initialSyncComplete ? "Refreshing" : "Connecting"
    : syncMode === "live"
      ? "Live 5s"
      : syncMode === "partial"
        ? "Partial"
        : "Unavailable";
  const activeWorkspace = workspaceForTab(tab);

  function selectTab(nextTab: TabKey) {
    setTab(nextTab);
    writeViewLocation({ tab: nextTab === "overview" ? "" : nextTab });
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
        <CycleSelector
          catalog={catalog}
          selectedCycleId={selectedCycleId}
          loading={loading}
          onSelect={(cycleId) => void selectCycle(cycleId)}
          onReturnLive={() => void returnToLive()}
        />
        <div className="topbar-actions">
          {lifecycleContext ? (
            <div className={`execution-context run-context-${lifecycleContext.state}`} title={lifecycleContext.run_id}>
              <span>Run</span>
              <strong>{formatLifecycleState(lifecycleContext.state)}</strong>
              <em>{Math.round(lifecycleContext.progress * 100)}%</em>
            </div>
          ) : null}
          <div className="sync-indicator" title="Control Panel source synchronization">
            <i className={syncMode} />
            <span>{syncLabel}</span>
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
          <button
            type="button"
            className="retry-action"
            onClick={() => void loadCycle(false, true)}
            disabled={loading}
          >
            <RefreshCcw size={15} />
            Retry
          </button>
        </section>
      ) : null}

      {loading && !cycle ? (
        <section className="loading-state" aria-live="polite">
          <i aria-hidden="true" />
          <span>Synchronizing CycleRun</span>
        </section>
      ) : activeView}

      <DiagnosticsDrawer diagnostics={diagnostics} clientSources={syncSources} />

      <footer className="footer-line">
        <span>{cycle?.cycle_id || "cycle not loaded"}</span>
        <span>{refreshedAt ? `refreshed ${refreshedAt}` : "waiting"}</span>
      </footer>
    </main>
  );
}

function removeLocalValue(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Browser storage is an ergonomic enhancement, not a runtime dependency.
  }
}

function readViewLocation(): ViewLocation {
  const params = new URLSearchParams(window.location.search);
  const view = params.get("view") || "";
  return {
    cycleId: params.get("cycle") || "",
    runId: params.get("run") || "",
    modelSelectionId: params.get("candidate") || "",
    tab: tabs.some((item) => item.key === view) ? view as TabKey : "overview"
  };
}

function writeViewLocation(update: {
  cycleId?: string;
  runId?: string;
  modelSelectionId?: string;
  tab?: string;
}): void {
  const params = new URLSearchParams(window.location.search);
  updateLocationParameter(params, "cycle", update.cycleId);
  updateLocationParameter(params, "run", update.runId);
  updateLocationParameter(params, "candidate", update.modelSelectionId);
  updateLocationParameter(params, "view", update.tab);
  const query = params.toString();
  window.history.replaceState(
    window.history.state,
    "",
    `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`
  );
}

function updateLocationParameter(
  params: URLSearchParams,
  key: string,
  value: string | undefined
): void {
  if (value === undefined) return;
  if (value) params.set(key, value);
  else params.delete(key);
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
