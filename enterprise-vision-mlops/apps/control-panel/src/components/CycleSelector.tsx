import { History, Radio } from "lucide-react";

import type { CycleRunList } from "../api/types";


interface CycleSelectorProps {
  catalog: CycleRunList | null;
  selectedCycleId: string;
  onSelect: (cycleId: string) => void;
}


export function CycleSelector({ catalog, selectedCycleId, onSelect }: CycleSelectorProps) {
  const selected = catalog?.cycles.find((cycle) => cycle.cycle_id === selectedCycleId);
  return (
    <div className="cycle-selector" aria-label="CycleRun selector">
      <div className="cycle-selector-icon">
        {selected?.live ? <Radio size={17} /> : <History size={17} />}
      </div>
      <label>
        <span>{selected?.live ? "Live Cycle" : "Cycle History"}</span>
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
      <small>{catalog?.total || 0} runs</small>
    </div>
  );
}
