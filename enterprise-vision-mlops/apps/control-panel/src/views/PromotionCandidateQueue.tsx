import { CheckCircle2, RefreshCcw, Rocket } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchModelCandidates, selectModelCandidate } from "../api/controlPanelClient";
import type { ModelCandidateCatalog, ModelCandidateSelection } from "../api/types";

interface PromotionCandidateQueueProps {
  actor: string;
  onPromote: (selection: ModelCandidateSelection) => void;
}

export function PromotionCandidateQueue({ actor, onPromote }: PromotionCandidateQueueProps) {
  const [catalog, setCatalog] = useState<ModelCandidateCatalog | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      setCatalog(await fetchModelCandidates());
      setError("");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Model candidate catalog unavailable");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  const ready = useMemo(() => catalog?.candidates.filter((candidate) => candidate.selectable) || [], [catalog]);
  const blocked = (catalog?.total || 0) - ready.length;

  async function select(candidateKey: string) {
    setBusy(candidateKey);
    setError("");
    try {
      const selection = await selectModelCandidate(candidateKey, {
        actor: actor || "ml-platform-operator",
        reason: "Select promotion-ready model from deployment manager"
      });
      onPromote(selection);
    } catch (selectionError) {
      setError(selectionError instanceof Error ? selectionError.message : "Model selection failed");
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="promotion-candidate-queue" aria-label="Promotion-ready model candidates">
      <header>
        <div><span className="eyebrow">Governed candidates</span><h2>Ready to Deploy</h2><p>{catalog ? `${ready.length} ready · ${blocked} blocked by policy` : "Loading model evidence..."}</p></div>
        <button type="button" className="icon-button compact" onClick={() => void refresh()} aria-label="Refresh promotion candidates" title="Refresh promotion candidates"><RefreshCcw size={16} /></button>
      </header>
      <div className="promotion-candidate-list">
        {ready.slice(0, 5).map((candidate) => (
          <article key={candidate.candidate_key}>
            <CheckCircle2 />
            <div><strong>{candidate.candidate_id}</strong><span>{candidate.architecture} · {candidate.dataset_version}</span></div>
            <Metric label="Accuracy" value={candidate.metrics.accuracy} />
            <Metric label="F1" value={candidate.metrics.f1} />
            <Metric label="AUROC" value={candidate.metrics.auroc} />
            <button type="button" className="primary-action" disabled={busy === candidate.candidate_key} onClick={() => void select(candidate.candidate_key)}><Rocket size={14} /> Select</button>
          </article>
        ))}
        {catalog && !ready.length ? <div className="deployment-inventory-empty">No candidate currently satisfies promotion policy.</div> : null}
      </div>
      {error ? <div className="policy-error" role="alert">{error}</div> : null}
    </section>
  );
}

function Metric({ label, value }: { label: string; value?: number }) {
  return <div className="promotion-candidate-metric"><span>{label}</span><strong>{value === undefined ? "-" : value.toFixed(3)}</strong></div>;
}
