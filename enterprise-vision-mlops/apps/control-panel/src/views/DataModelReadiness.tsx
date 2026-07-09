import { Cpu, DatabaseZap, GitCommitVertical, ShieldCheck } from "lucide-react";

import { formatNumber } from "../api/controlPanelClient";
import type { CycleRun } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

interface DataModelReadinessProps {
  cycle: CycleRun;
}

export function DataModelReadiness({ cycle }: DataModelReadinessProps) {
  const splitEntries = Object.entries(cycle.dataset.split || {});
  return (
    <section className="two-column" aria-label="Data and model readiness">
      <div className="panel">
        <div className="panel-heading">
          <div>
            <h2>Data Readiness</h2>
            <p>{cycle.dataset.dataset_id}</p>
          </div>
          <DatabaseZap />
        </div>
        <div className="readiness-grid">
          <ReadinessItem label="Contract" status={cycle.data_pipeline?.contract_status} />
          <ReadinessItem label="Quality" status={cycle.data_pipeline?.quality_status} />
          <ReadinessItem label="Lineage" status={cycle.data_pipeline?.lineage_status} />
          <ReadinessItem label="Replay" status={cycle.data_pipeline?.replay_ready ? "pass" : "blocked"} />
        </div>
        <dl className="dense-list">
          <Row label="Records" value={formatNumber(cycle.dataset.record_count)} />
          <Row label="Schema valid" value={cycle.dataset.schema_valid_rate?.toFixed(3)} />
          <Row label="Domain" value={cycle.dataset.domain_pack} />
        </dl>
        <div className="split-bars">
          {splitEntries.map(([key, value]) => (
            <div key={key}>
              <span>{key}</span>
              <b style={{ width: `${Math.max(8, (value / Math.max(cycle.dataset.record_count, 1)) * 100)}%` }} />
              <small>{formatNumber(value)}</small>
            </div>
          ))}
        </div>
      </div>

      <div className="panel">
        <div className="panel-heading">
          <div>
            <h2>Model Readiness</h2>
            <p>{cycle.model.model_name}</p>
          </div>
          <Cpu />
        </div>
        <div className="readiness-grid">
          <ReadinessItem label="Tracking" status={cycle.experiment_pipeline?.tracking_status} />
          <ReadinessItem label="Evaluation" status={cycle.experiment_pipeline?.evaluation_status} />
          <ReadinessItem label="Registry" status={cycle.experiment_pipeline?.registry_status} />
          <ReadinessItem label="Promote" status={cycle.experiment_pipeline?.promotion_ready ? "pass" : "blocked"} />
        </div>
        <dl className="dense-list">
          <Row label="Stage" value={cycle.model.stage} />
          <Row label="Type" value={cycle.model.model_type} />
          <Row label="Run" value={cycle.mlflow?.run_id || "not-linked"} />
        </dl>
        <div className="matrix-list">
          {cycle.model_matrix?.candidates.map((candidate) => (
            <div key={candidate.candidate_id} className="candidate-row">
              <GitCommitVertical />
              <div>
                <strong>{candidate.candidate_id}</strong>
                <span>{candidate.architecture} / {candidate.resource_profile}</span>
              </div>
              <StatusBadge status={candidate.status} />
            </div>
          ))}
        </div>
      </div>

      <div className="panel wide">
        <div className="panel-heading">
          <div>
            <h2>Real-Test Policy</h2>
            <p>{cycle.model_matrix?.matrix_id || "not-bound"}</p>
          </div>
          <ShieldCheck />
        </div>
        <div className="policy-grid">
          <Policy label="mock" value={cycle.model_matrix?.real_test_policy.mock_allowed === false ? "denied" : "allowed"} ok={cycle.model_matrix?.real_test_policy.mock_allowed === false} />
          <Policy label="smoke" value={cycle.model_matrix?.real_test_policy.smoke_allowed === false ? "denied" : "allowed"} ok={cycle.model_matrix?.real_test_policy.smoke_allowed === false} />
          <Policy label="dataset" value={cycle.model_matrix?.real_test_policy.requires_real_dataset ? "required" : "optional"} ok={cycle.model_matrix?.real_test_policy.requires_real_dataset === true} />
          <Policy label="training" value={cycle.model_matrix?.real_test_policy.requires_real_training ? "required" : "optional"} ok={cycle.model_matrix?.real_test_policy.requires_real_training === true} />
        </div>
      </div>
    </section>
  );
}

function ReadinessItem({ label, status }: { label: string; status: string | null | undefined }) {
  return (
    <div className="readiness-item">
      <span>{label}</span>
      <StatusBadge status={status} compact />
    </div>
  );
}

function Policy({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return (
    <div className={ok ? "policy policy-ok" : "policy policy-bad"}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value || "-"}</dd>
    </div>
  );
}
