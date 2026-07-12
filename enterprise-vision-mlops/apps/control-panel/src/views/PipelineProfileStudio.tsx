import {
  Braces,
  CheckCircle2,
  Database,
  FlaskConical,
  Gauge,
  LockKeyhole,
  Play,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  TriangleAlert,
  Wrench
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  createLifecycleRun,
  fetchDefaultPipelineProfile,
  fetchPipelineProfiles,
  launchPipelineProfile,
  savePipelineProfile,
  validatePipelineProfile
} from "../api/controlPanelClient";
import type {
  CycleRun,
  EnvironmentTier,
  LifecycleRun,
  PipelineCapability,
  PipelinePlanStage,
  PipelineProfileRecord,
  PipelineProfileValidation,
  PipelineRunProfile,
  TaskAssignment
} from "../api/types";
import { StatusBadge } from "../components/StatusBadge";


interface PipelineProfileStudioProps {
  cycle: CycleRun;
}


const namespaces: Record<EnvironmentTier, string> = {
  dev: "evm-dev",
  test: "evm-test",
  staging: "evm-staging",
  "pre-production": "evm-pre-production",
  production: "evm-production"
};


export function PipelineProfileStudio({ cycle }: PipelineProfileStudioProps) {
  const [profile, setProfile] = useState<PipelineRunProfile | null>(null);
  const [validation, setValidation] = useState<PipelineProfileValidation | null>(null);
  const [saved, setSaved] = useState<PipelineProfileRecord | null>(null);
  const [task, setTask] = useState<TaskAssignment | null>(null);
  const [lifecycleRun, setLifecycleRun] = useState<LifecycleRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [rawOpen, setRawOpen] = useState(false);
  const [rawText, setRawText] = useState("");

  useEffect(() => {
    async function load() {
      const [defaultProfile, history] = await Promise.all([
        fetchDefaultPipelineProfile(),
        fetchPipelineProfiles()
      ]);
      defaultProfile.owner = cycle.tenant?.model_owner || defaultProfile.owner;
      setProfile(defaultProfile);
      setRawText(JSON.stringify(defaultProfile, null, 2));
      setSaved(
        history.find((item) => JSON.stringify(item.profile) === JSON.stringify(defaultProfile)) || null
      );
      setValidation(await validatePipelineProfile(defaultProfile));
    }
    void load().catch((reason) => setError(message(reason)));
  }, [cycle.tenant?.model_owner]);

  useEffect(() => {
    if (!profile) return;
    const timer = window.setTimeout(() => {
      void validatePipelineProfile(profile)
        .then(setValidation)
        .catch((reason) => setError(message(reason)));
    }, 350);
    return () => window.clearTimeout(timer);
  }, [profile]);

  const savedCurrent = saved;
  const validationCoverage = useMemo(() => {
    if (!validation) return 0;
    if (validation.executable && validation.status === "ready") return 100;
    const checks = validation.capabilities.length + validation.stages.length;
    if (!checks) return 0;
    const passingCapabilities = validation.capabilities.filter(
      (item) => item.active && item.status !== "not_wired"
    ).length;
    const unblockedStages = validation.stages.filter((stage) => stage.state !== "blocked").length;
    return Math.round((passingCapabilities + unblockedStages) / checks * 100);
  }, [validation]);

  if (!profile) return <section className="loading-state">Loading pipeline profile</section>;
  const activeProfile = profile;

  function updateProfile(next: PipelineRunProfile) {
    setSaved(null);
    setTask(null);
    setLifecycleRun(null);
    setError("");
    setProfile(next);
  }

  async function validateNow() {
    setBusy(true);
    setError("");
    try {
      setValidation(await validatePipelineProfile(activeProfile));
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function saveVersion() {
    setBusy(true);
    setError("");
    try {
      const record = await savePipelineProfile(activeProfile);
      setSaved(record);
      setValidation(record.validation);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function launch(dryRun: boolean) {
    if (!savedCurrent) return;
    setBusy(true);
    setError("");
    try {
      if (activeProfile.execution_scope === "full_lifecycle") {
        const result = await createLifecycleRun({
          profile_id: savedCurrent.profile_id,
          profile_version: savedCurrent.version,
          actor: activeProfile.owner,
          reason: `${dryRun ? "Preview" : "Execute"} ${activeProfile.profile_name} full lifecycle`,
          dry_run: dryRun
        });
        setLifecycleRun(result);
        setTask(null);
        return;
      }
      const result = await launchPipelineProfile(savedCurrent.profile_id, savedCurrent.version, {
        actor: activeProfile.owner,
        reason: `${dryRun ? "Preview" : "Queue"} ${activeProfile.profile_name}`,
        dry_run: dryRun
      });
      setValidation(result.validation);
      setTask(result.task || null);
      setLifecycleRun(null);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  function openRaw() {
    setRawText(JSON.stringify(activeProfile, null, 2));
    setRawOpen(true);
  }

  function applyRaw() {
    try {
      const next = JSON.parse(rawText) as PipelineRunProfile;
      updateProfile(next);
      setRawOpen(false);
    } catch {
      setError("Profile document is not valid JSON.");
    }
  }

  return (
    <section className="profile-studio" aria-label="Pipeline profile studio">
      <div className="profile-hero panel wide">
        <div>
          <span className="eyebrow">Versioned Run Configuration</span>
          <h2>Pipeline Profile Studio</h2>
          <p>{savedCurrent ? `${savedCurrent.profile_id} / v${savedCurrent.version}` : "unsaved working copy"}</p>
        </div>
        <div className="profile-readiness">
          <div>
            <span>Configuration Validity</span>
            <strong>{validationCoverage}%</strong>
          </div>
          <StatusBadge status={validation?.status || "unknown"} />
          <div className="profile-overall-progress" aria-label={`Configuration validity ${validationCoverage}%`}>
            <b style={{ width: `${validationCoverage}%` }} />
          </div>
        </div>
      </div>

      {error ? <div className="policy-error" role="alert">{error}</div> : null}

      <div className="profile-workspace">
        <div className="panel profile-editor">
          <SectionHeading icon={<SlidersHorizontal />} title="Run Scope" />
          <div className="profile-form-grid">
            <TextField label="Profile" value={profile.profile_name} onChange={(value) => updateProfile({ ...profile, profile_name: value })} />
            <TextField label="Owner" value={profile.owner} onChange={(value) => updateProfile({ ...profile, owner: value })} />
          </div>
          <div className="segmented-control" aria-label="Execution scope">
            <button type="button" className={profile.execution_scope === "data_cycle" ? "active" : ""} onClick={() => updateProfile({ ...profile, execution_scope: "data_cycle" })}>Data Cycle</button>
            <button type="button" className={profile.execution_scope === "full_lifecycle" ? "active" : ""} onClick={() => updateProfile({ ...profile, execution_scope: "full_lifecycle" })}>Full Lifecycle</button>
          </div>

          <SectionHeading icon={<Database />} title="Data And Validation" />
          <div className="profile-form-grid">
            <TextField label="Dataset Version" value={profile.data.dataset_version} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, dataset_version: value } })} />
            <NumberField label="Split Seed" value={profile.data.split.seed} step={1} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, seed: value } } })} />
            <NumberField label="Train Ratio" value={profile.data.split.train} step={0.05} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, train: value } } })} />
            <NumberField label="Validation Ratio" value={profile.data.split.validation} step={0.05} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, validation: value } } })} />
            <NumberField label="Test Ratio" value={profile.data.split.test} step={0.05} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, test: value } } })} />
            <SelectField label="Holdout" value={profile.data.split.holdout_split} options={["validation", "test"]} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, holdout_split: value as "validation" | "test" } }, gates: { ...profile.gates, ct_dataset_split: value as "validation" | "test" } })} />
            <NumberField label="Records / Shard" value={profile.data.records_per_shard} step={64} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, records_per_shard: value } })} />
            <NumberField label="Review Samples" value={profile.data.max_review_samples} step={16} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, max_review_samples: value } })} />
          </div>
          <div className="toggle-row">
            <Toggle label="Fail On Empty" checked={profile.data.fail_on_empty} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, fail_on_empty: value } })} />
            <Toggle label="Fail Quality Gate" checked={profile.data.fail_on_quality_error} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, fail_on_quality_error: value } })} />
            <Toggle label="Immutable Holdout" checked={profile.data.split.immutable_holdout} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, immutable_holdout: value } } })} />
            <Toggle label="Cross-validation" checked={profile.data.split.cross_validation_enabled} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, cross_validation_enabled: value } } })} />
          </div>
          {profile.data.split.cross_validation_enabled ? (
            <NumberField label="CV Folds" value={profile.data.split.cross_validation_folds} step={1} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, cross_validation_folds: value } } })} />
          ) : null}

          <SectionHeading icon={<FlaskConical />} title="Model And Experiments" />
          <div className="segmented-control" aria-label="Model architecture">
            <button type="button" className={profile.model.architecture === "efficientnet-b0" ? "active" : ""} onClick={() => updateProfile({ ...profile, model: { ...profile.model, architecture: "efficientnet-b0", input_size: 224 } })}>EfficientNet-B0</button>
            <button type="button" className={profile.model.architecture === "efficientnet-b7" ? "active" : ""} onClick={() => updateProfile({ ...profile, model: { ...profile.model, architecture: "efficientnet-b7", input_size: 600 } })}>EfficientNet-B7</button>
          </div>
          <div className="profile-form-grid">
            <NumberField label="Input Size" value={profile.model.input_size} step={8} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, input_size: value } })} />
            <NumberField label="Batch Size" value={profile.model.batch_size} step={8} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, batch_size: value } })} />
            <NumberField label="Epochs" value={profile.model.epochs} step={1} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, epochs: value } })} />
            <NumberField label="Learning Rate" value={profile.model.learning_rate} step={0.0001} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, learning_rate: value } })} />
            <NumberField label="Early Stop" value={profile.model.early_stop_threshold} step={0.01} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, early_stop_threshold: value } })} />
            <NumberField label="Patience" value={profile.model.early_stop_patience} step={1} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, early_stop_patience: value } })} />
            <SelectField label="Optimizer" value={profile.model.optimizer} options={["adamw", "sgd"]} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, optimizer: value as "adamw" | "sgd" } })} />
            <SelectField label="Tuning" value={profile.model.tuning_mode} options={["manual", "grid", "bayesian"]} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, tuning_mode: value as "manual" | "grid" | "bayesian" } })} />
          </div>
          <div className="toggle-row">
            <Toggle label="Mixed Precision" checked={profile.model.mixed_precision} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, mixed_precision: value } })} />
            <Toggle label="Class Weighted Loss" checked={profile.model.class_weighted_loss} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, class_weighted_loss: value } })} />
            <Toggle label="A/B Test" checked={profile.experiment.ab_test_enabled} onChange={(value) => updateProfile({ ...profile, experiment: { ...profile.experiment, ab_test_enabled: value } })} />
          </div>

          <SectionHeading icon={<ShieldCheck />} title="Gates And Resources" />
          <div className="profile-form-grid">
            <NumberField label="Min Accuracy" value={profile.gates.promotion_min_accuracy} step={0.01} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, promotion_min_accuracy: value } })} />
            <NumberField label="Min F1" value={profile.gates.promotion_min_f1} step={0.01} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, promotion_min_f1: value } })} />
            <NumberField label="Min AUROC" value={profile.gates.promotion_min_auroc} step={0.01} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, promotion_min_auroc: value } })} />
            <SelectField label="Environment" value={profile.gates.target_environment} options={Object.keys(namespaces)} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, target_environment: value as EnvironmentTier, target_namespace: namespaces[value as EnvironmentTier] } })} />
            <TextField label="Namespace" value={profile.gates.target_namespace} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, target_namespace: value } })} />
            <SelectField label="Approval" value={profile.gates.approval_policy} options={["manual", "two_person", "change_ticket"]} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, approval_policy: value as "manual" | "two_person" | "change_ticket" } })} />
            <NumberField label="GPU" value={profile.resources.gpu_count} step={1} onChange={(value) => updateProfile({ ...profile, resources: { ...profile.resources, gpu_count: value } })} />
            <NumberField label="CPU" value={profile.resources.cpu_request} step={1} onChange={(value) => updateProfile({ ...profile, resources: { ...profile.resources, cpu_request: value } })} />
            <NumberField label="Memory GiB" value={profile.resources.memory_gb} step={2} onChange={(value) => updateProfile({ ...profile, resources: { ...profile.resources, memory_gb: value } })} />
          </div>

          <div className="profile-actions">
            <button type="button" className="secondary-action" onClick={() => void validateNow()} disabled={busy}>
              <ShieldCheck size={16} /> Validate Profile
            </button>
            <button type="button" className="secondary-action" onClick={openRaw} disabled={busy}>
              <Braces size={16} /> Config Document
            </button>
            <button type="button" className="primary-action" onClick={() => void saveVersion()} disabled={busy}>
              <Save size={16} /> Save Version
            </button>
          </div>
        </div>

        <div className="profile-inspector">
          <div className="panel profile-plan">
            <div className="panel-heading">
              <div><h2>Execution Plan</h2><p>{validation?.checked_at || "pending validation"}</p></div>
              <Gauge />
            </div>
            <div className="profile-stage-list">
              {(validation?.stages || []).map((stage, index) => <PlanStage key={stage.stage_id} stage={stage} index={index} />)}
            </div>
          </div>

          <div className="panel capability-panel">
            <div className="panel-heading">
              <div><h2>Runtime Coverage</h2><p>effective parameter bindings</p></div>
              <Wrench />
            </div>
            <div className="capability-list">
              {(validation?.capabilities || []).map((item) => <Capability key={item.capability_id} item={item} />)}
            </div>
            {validation?.blockers.length ? (
              <div className="profile-blockers" role="alert">
                <strong><TriangleAlert size={15} /> Blockers</strong>
                {validation.blockers.map((item) => <span key={item}>{item}</span>)}
              </div>
            ) : null}
          </div>

          <div className="panel profile-launch">
            <div>
              <span>Saved Profile</span>
              <strong>{savedCurrent ? `v${savedCurrent.version}` : "required"}</strong>
              <small>{savedCurrent?.digest.slice(0, 16) || "No immutable digest"}</small>
            </div>
            <button type="button" className="secondary-action" disabled={busy || !savedCurrent || !validation?.executable} onClick={() => void launch(true)}>
              <ShieldCheck size={16} /> Preview Run
            </button>
            <button type="button" className="primary-action" disabled={busy || !savedCurrent || !validation?.executable} onClick={() => void launch(false)}>
              <Play size={16} /> {profile.execution_scope === "full_lifecycle" ? "Queue Full Lifecycle" : "Queue Data Cycle"}
            </button>
            {task ? <div className="profile-task"><StatusBadge status={task.status} compact /><span>{task.task_id}</span></div> : null}
            {lifecycleRun ? <div className="profile-task"><LifecycleState state={lifecycleRun.state} /><span>{lifecycleRun.run_id}</span></div> : null}
          </div>
        </div>
      </div>

      {rawOpen ? (
        <div className="profile-document-backdrop" role="presentation">
          <section className="profile-document" role="dialog" aria-modal="true" aria-label="Pipeline profile JSON document">
            <header><div><span>evm.pipeline_profile.v1</span><h2>Config Document</h2></div><LockKeyhole /></header>
            <textarea value={rawText} onChange={(event) => setRawText(event.target.value)} spellCheck={false} />
            <div><button type="button" className="secondary-action" onClick={() => setRawOpen(false)}>Cancel</button><button type="button" className="primary-action" onClick={applyRaw}><CheckCircle2 size={16} /> Apply Document</button></div>
          </section>
        </div>
      ) : null}
    </section>
  );
}


function SectionHeading({ icon, title }: { icon: React.ReactNode; title: string }) {
  return <div className="profile-section-heading">{icon}<h3>{title}</h3></div>;
}


function TextField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <label><span>{label}</span><input value={value} onChange={(event) => onChange(event.target.value)} /></label>;
}


function NumberField({ label, value, step, onChange }: { label: string; value: number; step: number; onChange: (value: number) => void }) {
  return <label><span>{label}</span><input type="number" value={value} step={step} onChange={(event) => onChange(Number(event.target.value))} /></label>;
}


function SelectField({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  return <label><span>{label}</span><select value={value} onChange={(event) => onChange(event.target.value)}>{options.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>;
}


function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="profile-toggle"><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i /><span>{label}</span></label>;
}


function Capability({ item }: { item: PipelineCapability }) {
  const icon = item.status === "wired" ? <CheckCircle2 /> : item.status === "partial" ? <Wrench /> : <LockKeyhole />;
  return <article className={`capability-${item.status} ${item.active ? "active" : "inactive"}`}>{icon}<div><strong>{item.label}</strong><span>{item.detail}</span></div><em>{item.active ? item.status.replace("_", " ") : "inactive"}</em></article>;
}


function PlanStage({ stage, index }: { stage: PipelinePlanStage; index: number }) {
  const state = planStateLabel(stage.state);
  return <article className={`profile-plan-stage plan-${stage.state}`}><div className="profile-plan-index">{String(index + 1).padStart(2, "0")}</div><div><header><strong>{stage.label}</strong><span>{state}</span></header><small>{stage.runtime} / {stage.detail}</small><div className="profile-stage-progress"><b style={{ width: `${Math.round(stage.progress * 100)}%` }} /></div></div></article>;
}


function planStateLabel(state: PipelinePlanStage["state"]): string {
  if (state === "ready") return "Ready To Start";
  if (state === "blocked") return "Blocked";
  return "Not Started";
}


function LifecycleState({ state }: { state: LifecycleRun["state"] }) {
  const label = state === "dry_run" ? "Dry Run Ready" : state === "queued" ? "Queued" : state.replaceAll("_", " ");
  return <strong className={`profile-lifecycle-state state-${state}`}>{label}</strong>;
}


function message(error: unknown): string {
  return error instanceof Error ? error.message : "Pipeline profile request failed";
}
