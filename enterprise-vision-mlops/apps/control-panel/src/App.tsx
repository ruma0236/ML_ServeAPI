import { AlertCircle, RefreshCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { fetchLatestCycle } from "./api/controlPanelClient";
import type { CycleRun } from "./api/types";
import { StatusBadge } from "./components/StatusBadge";
import { CycleOverview } from "./views/CycleOverview";
import { DataModelReadiness } from "./views/DataModelReadiness";
import { GateAndRiskPanel } from "./views/GateAndRiskPanel";
import { PipelineTimeline } from "./views/PipelineTimeline";

type TabKey = "overview" | "readiness" | "timeline" | "gates";

const tabs: Array<{ key: TabKey; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "readiness", label: "Readiness" },
  { key: "timeline", label: "Timeline" },
  { key: "gates", label: "Gates" }
];

export function App() {
  const [cycle, setCycle] = useState<CycleRun | null>(null);
  const [tab, setTab] = useState<TabKey>("overview");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshedAt, setRefreshedAt] = useState("");

  async function loadCycle() {
    setLoading(true);
    setError("");
    try {
      const payload = await fetchLatestCycle();
      setCycle(payload);
      setRefreshedAt(new Date().toLocaleTimeString());
    } catch (err) {
      setError(err instanceof Error ? err.message : "CycleRun request failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadCycle();
  }, []);

  const activeView = useMemo(() => {
    if (!cycle) return null;
    if (tab === "readiness") return <DataModelReadiness cycle={cycle} />;
    if (tab === "timeline") return <PipelineTimeline cycle={cycle} />;
    if (tab === "gates") return <GateAndRiskPanel cycle={cycle} />;
    return <CycleOverview cycle={cycle} />;
  }, [cycle, tab]);

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <span className="eyebrow">Enterprise Vision MLOps</span>
          <h1>Control Panel</h1>
        </div>
        <div className="topbar-actions">
          {cycle ? <StatusBadge status={cycle.status} /> : null}
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
