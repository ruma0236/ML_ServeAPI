import { AlertCircle, Moon, RefreshCcw, Sun } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { fetchLatestCycle, fetchRuntimeResources } from "./api/controlPanelClient";
import type { CycleRun, RuntimeResource } from "./api/types";
import { StatusBadge } from "./components/StatusBadge";
import { CycleOverview } from "./views/CycleOverview";
import { DataModelReadiness } from "./views/DataModelReadiness";
import { GateAndRiskPanel } from "./views/GateAndRiskPanel";
import { PipelineTimeline } from "./views/PipelineTimeline";
import { TaskAuthoring } from "./views/TaskAuthoring";

type TabKey = "overview" | "readiness" | "timeline" | "operate" | "gates";

const tabs: Array<{ key: TabKey; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "readiness", label: "Readiness" },
  { key: "timeline", label: "Timeline" },
  { key: "operate", label: "Operate" },
  { key: "gates", label: "Gates" }
];

export function App() {
  const [cycle, setCycle] = useState<CycleRun | null>(null);
  const [resources, setResources] = useState<RuntimeResource[]>([]);
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
      const [payload, runtimeResources] = await Promise.all([fetchLatestCycle(), fetchRuntimeResources()]);
      setCycle(payload);
      setResources(runtimeResources);
      setError("");
      setRefreshedAt(new Date().toLocaleTimeString());
    } catch (err) {
      setError(err instanceof Error ? err.message : "CycleRun request failed");
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

  const activeView = useMemo(() => {
    if (!cycle) return null;
    if (tab === "readiness") return <DataModelReadiness cycle={cycle} />;
    if (tab === "timeline") return <PipelineTimeline cycle={cycle} resources={resources} />;
    if (tab === "operate") return <TaskAuthoring cycle={cycle} resources={resources} />;
    if (tab === "gates") return <GateAndRiskPanel cycle={cycle} />;
    return <CycleOverview cycle={cycle} />;
  }, [cycle, resources, tab]);

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
        <div className="topbar-actions">
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

      <footer className="footer-line">
        <span>{cycle?.cycle_id || "cycle not loaded"}</span>
        <span>{refreshedAt ? `refreshed ${refreshedAt}` : "waiting"}</span>
      </footer>
    </main>
  );
}
