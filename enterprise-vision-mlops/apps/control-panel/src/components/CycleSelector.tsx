import { History, LoaderCircle, Radio } from "lucide-react";

import type { CycleRunList } from "../api/types";


interface CycleSelectorProps {
  catalog: CycleRunList | null;
  selectedCycleId: string;
  loading: boolean;
  onSelect: (cycleId: string) => void;
  onReturnLive: () => void;
}


export function CycleSelector({
  catalog,
  selectedCycleId,
  loading,
  onSelect,
  onReturnLive
}: CycleSelectorProps) {
  const selected = catalog?.cycles.find((cycle) => cycle.cycle_id === selectedCycleId);
  const isSynchronizing = !catalog;
  const isSwitching = loading && Boolean(catalog);
  const isLive = Boolean(selected?.live || selectedCycleId === catalog?.latest_cycle_id);
  const selectorLabel = isSynchronizing
    ? "Synchronizing Cycle"
    : isSwitching
      ? "Switching Cycle"
      : isLive ? "Live Cycle" : "Cycle History";
  const viewModeLabel = isSynchronizing
    ? "CONNECTING"
    : isSwitching
      ? isLive ? "LOADING LIVE" : "LOADING SNAPSHOT"
      : isLive ? "LIVE DATA" : "HISTORICAL SNAPSHOT";
  return (
    <div
      className={`cycle-selector ${isSynchronizing ? "cycle-selector-syncing" : isLive ? "cycle-selector-live" : "cycle-selector-history"}`}
      aria-label="CycleRun selector"
    >
      <div className="cycle-selector-icon">
        {isSynchronizing || isSwitching ? <LoaderCircle className="cycle-selector-spinner" size={17} /> : isLive ? <Radio size={17} /> : <History size={17} />}
      </div>
      <label>
        <span>{selectorLabel}</span>
        <select
          value={selectedCycleId}
          onChange={(event) => onSelect(event.target.value)}
          disabled={!catalog?.cycles.length}
        >
          {(catalog?.cycles || []).map((cycle) => (
            <option key={cycle.cycle_id} value={cycle.cycle_id}>
              {cycle.live ? "LIVE" : new Date(cycle.started_at).toLocaleDateString()} | {cycle.dataset_version} | {cycle.model_version}
            </option>
          ))}
        </select>
      </label>
      <div className="cycle-selector-meta">
        <strong className={`cycle-view-mode ${isSynchronizing || isSwitching ? "syncing" : isLive ? "live" : "history"}`}>
          {viewModeLabel}
        </strong>
        {!isSynchronizing && !isLive && catalog?.latest_cycle_id ? (
          <button type="button" onClick={onReturnLive} title="Return to the current live CycleRun">
            <Radio size={14} />
            Return to Live
          </button>
        ) : null}
        <small>{catalog?.total || 0} cycles</small>
      </div>
    </div>
  );
}
