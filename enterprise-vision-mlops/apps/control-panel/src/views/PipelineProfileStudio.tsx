import {
  ArrowLeft,
  ArrowRight,
  Braces,
  CheckCircle2,
  Cpu,
  Database,
  FileInput,
  FlaskConical,
  LockKeyhole,
  Play,
  Rocket,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  TriangleAlert,
  Wrench,
  type LucideIcon
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  createLifecycleRun,
  fetchDefaultPipelineProfile,
  fetchModelComponents,
  fetchPipelineProfileReplayValidation,
  fetchPipelineProfiles,
  launchPipelineProfile,
  savePipelineProfile,
  validatePipelineProfile
} from "../api/controlPanelClient";
import type {
  CycleRun,
  EnvironmentTier,
  LifecycleRun,
  ModelComponent,
  PipelineCapability,
  PipelineProfileRecord,
  PipelineProfileReplayValidation,
  PipelineProfileValidation,
  PipelineRunProfile,
  TaskAssignment
} from "../api/types";
import { LifecycleFlow, planLifecycleItems } from "../components/LifecycleFlow";
import { StatusBadge } from "../components/StatusBadge";


interface PipelineProfileStudioProps {
  cycle: CycleRun;
}


type BlueprintStep = "intent" | "data" | "training" | "release" | "review";


const blueprintSteps: Array<{ key: BlueprintStep; label: string; icon: LucideIcon }> = [
  { key: "intent", label: "Intent", icon: SlidersHorizontal },
  { key: "data", label: "Data", icon: Database },
  { key: "training", label: "Training", icon: FlaskConical },
  { key: "release", label: "Release", icon: Rocket },
  { key: "review", label: "Review", icon: ShieldCheck }
];


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
  const [replayValidation, setReplayValidation] = useState<PipelineProfileReplayValidation | null>(null);
  const [task, setTask] = useState<TaskAssignment | null>(null);
  const [lifecycleRun, setLifecycleRun] = useState<LifecycleRun | null>(null);
  const [modelComponents, setModelComponents] = useState<ModelComponent[]>([]);
  const [activeStep, setActiveStep] = useState<BlueprintStep>("intent");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [rawOpen, setRawOpen] = useState(false);
  const [rawText, setRawText] = useState("");
  const validationSequence = useRef(0);

  useEffect(() => {
    async function load() {
      const [defaultProfile, history, componentCatalog] = await Promise.all([
        fetchDefaultPipelineProfile(),
        fetchPipelineProfiles(),
        fetchModelComponents()
      ]);
      defaultProfile.owner = cycle.tenant?.model_owner || defaultProfile.owner;
      setProfile(defaultProfile);
      setRawText(JSON.stringify(defaultProfile, null, 2));
      const existing = history.find(
        (item) => JSON.stringify(item.profile) === JSON.stringify(defaultProfile)
      ) || null;
      setSaved(existing);
      setReplayValidation(
        existing
          ? await fetchPipelineProfileReplayValidation(existing.profile_id, existing.version)
          : null
      );
      setModelComponents(componentCatalog.components.filter((component) => component.status === "approved"));
      const sequence = ++validationSequence.current;
      const result = await validatePipelineProfile(defaultProfile);
      if (sequence === validationSequence.current) setValidation(result);
    }
    void load().catch((reason) => setError(message(reason)));
  }, [cycle.tenant?.model_owner]);

  useEffect(() => {
    if (!profile) return;
    const sequence = ++validationSequence.current;
    const timer = window.setTimeout(() => {
      void validatePipelineProfile(profile)
        .then((result) => {
          if (sequence === validationSequence.current) setValidation(result);
        })
        .catch((reason) => {
          if (sequence === validationSequence.current) setError(message(reason));
        });
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
  const stepIndex = blueprintSteps.findIndex((step) => step.key === activeStep);
  const flowItems = useMemo(() => planLifecycleItems(validation?.stages || []), [validation]);

  if (!profile) return <section className="loading-state">Loading pipeline profile</section>;
  const activeProfile = profile;

  function updateProfile(next: PipelineRunProfile) {
    setSaved(null);
    setReplayValidation(null);
    setTask(null);
    setLifecycleRun(null);
    setError("");
    setValidation(null);
    setProfile(next);
  }

  async function validateNow() {
    setBusy(true);
    setError("");
    const sequence = ++validationSequence.current;
    try {
      const result = await validatePipelineProfile(activeProfile);
      if (sequence === validationSequence.current) setValidation(result);
    } catch (reason) {
      if (sequence === validationSequence.current) setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function saveVersion() {
    setBusy(true);
    setError("");
    const sequence = ++validationSequence.current;
    try {
      const record = await savePipelineProfile(activeProfile);
      setSaved(record);
      if (sequence === validationSequence.current) setValidation(record.validation);
      setReplayValidation(
        await fetchPipelineProfileReplayValidation(record.profile_id, record.version)
      );
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
          reason: `${dryRun ? "Validate" : "Execute"} ${activeProfile.profile_name} full lifecycle`,
          dry_run: dryRun
        });
        setLifecycleRun(result);
        setTask(null);
        return;
      }
      const result = await launchPipelineProfile(savedCurrent.profile_id, savedCurrent.version, {
        actor: activeProfile.owner,
        reason: `${dryRun ? "Validate" : "Queue"} ${activeProfile.profile_name}`,
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

  function moveStep(offset: number) {
    const next = Math.max(0, Math.min(blueprintSteps.length - 1, stepIndex + offset));
    setActiveStep(blueprintSteps[next].key);
  }

  return (
    <section className="profile-studio" aria-label="Pipeline profile studio">
      <div className="profile-hero panel wide">
        <div>
          <span className="eyebrow">Immutable Run Configuration</span>
          <h2>Run Blueprint Studio</h2>
          <p>{savedCurrent ? `${savedCurrent.profile_id} / v${savedCurrent.version}` : "unsaved working copy"}</p>
        </div>
        <div className="profile-readiness">
          <div>
            <span>Blueprint Validity</span>
            <strong>{validationCoverage}%</strong>
          </div>
          <StatusBadge status={validation?.status || "unknown"} />
          <div className="profile-overall-progress" aria-label={`Blueprint validity ${validationCoverage}%`}>
            <b style={{ width: `${validationCoverage}%` }} />
          </div>
        </div>
      </div>

      {error ? <div className="policy-error" role="alert">{error}</div> : null}

      <nav className="blueprint-step-nav" aria-label="Blueprint steps">
        {blueprintSteps.map((step, index) => {
          const Icon = step.icon;
          return (
            <button
              key={step.key}
              type="button"
              className={`${step.key === activeStep ? "active" : ""} ${index < stepIndex ? "visited" : ""}`}
              onClick={() => setActiveStep(step.key)}
              aria-current={step.key === activeStep ? "step" : undefined}
              aria-label={step.label}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>
              <Icon size={17} />
              <strong>{step.label}</strong>
            </button>
          );
        })}
      </nav>

      <div className="profile-workspace">
        <div className="panel profile-editor">
          <header className="blueprint-step-heading">
            <div>
              <span>Step {stepIndex + 1} of {blueprintSteps.length}</span>
              <h3>{blueprintSteps[stepIndex].label}</h3>
            </div>
            <StatusBadge status={validation?.status || "unknown"} compact />
          </header>

          {activeStep === "intent" ? (
            <div className="blueprint-step-content" data-step="intent">
              <SectionHeading icon={<SlidersHorizontal />} title="Run Identity" />
              <div className="profile-form-grid">
                <TextField label="Profile" value={profile.profile_name} onChange={(value) => updateProfile({ ...profile, profile_name: value })} />
                <TextField label="Owner" value={profile.owner} onChange={(value) => updateProfile({ ...profile, owner: value })} />
                <TextField className="field-wide" label="Description" value={profile.description} onChange={(value) => updateProfile({ ...profile, description: value })} />
              </div>
              <SectionHeading icon={<FileInput />} title="Execution Scope" />
              <div className="segmented-control" aria-label="Execution scope">
                <button type="button" className={profile.execution_scope === "data_cycle" ? "active" : ""} onClick={() => updateProfile({ ...profile, execution_scope: "data_cycle" })}>Data Cycle</button>
                <button type="button" className={profile.execution_scope === "full_lifecycle" ? "active" : ""} onClick={() => updateProfile({ ...profile, execution_scope: "full_lifecycle" })}>Full Lifecycle</button>
              </div>
            </div>
          ) : null}

          {activeStep === "data" ? (
            <div className="blueprint-step-content" data-step="data">
              <SectionHeading icon={<Database />} title="Dataset Identity" />
              <div className="profile-form-grid">
                <TextField label="Dataset" value={profile.data.dataset_name} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, dataset_name: value } })} />
                <TextField label="Dataset Version" value={profile.data.dataset_version} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, dataset_version: value } })} />
                <TextField className="field-wide" label="Source Manifest URI" value={profile.data.source_manifest_uri} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, source_manifest_uri: value } })} />
                <TextField className="field-wide" label="Split Manifest URI" value={profile.data.split_manifest_uri} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split_manifest_uri: value } })} />
                <TextField className="field-wide" label="Split Manifest SHA-256" value={profile.data.split_manifest_sha256} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split_manifest_sha256: value } })} />
              </div>
              <SectionHeading icon={<ShieldCheck />} title="Deterministic Split" />
              <div className="profile-form-grid">
                <NumberField label="Split Seed" value={profile.data.split.seed} step={1} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, seed: value } } })} />
                <NumberField label="Train Ratio" value={profile.data.split.train} step={0.05} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, train: value } } })} />
                <NumberField label="Validation Ratio" value={profile.data.split.validation} step={0.05} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, validation: value } } })} />
                <NumberField label="Test Ratio" value={profile.data.split.test} step={0.05} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, test: value } } })} />
                <SelectField label="Holdout" value={profile.data.split.holdout_split} options={["validation", "test"]} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, holdout_split: value as "validation" | "test" } }, gates: { ...profile.gates, ct_dataset_split: value as "validation" | "test" } })} />
                <NumberField label="Records / Shard" value={profile.data.records_per_shard} step={64} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, records_per_shard: value } })} />
                <NumberField label="Review Samples" value={profile.data.max_review_samples} step={16} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, max_review_samples: value } })} />
                <SelectField label="Duplicate Severity" value={profile.data.duplicate_severity} options={["info", "warn", "error"]} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, duplicate_severity: value as "info" | "warn" | "error" } })} />
                <SelectField label="Dimension Severity" value={profile.data.dimension_severity} options={["info", "warn", "error"]} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, dimension_severity: value as "info" | "warn" | "error" } })} />
              </div>
              <div className="toggle-row">
                <Toggle label="Fail On Empty" checked={profile.data.fail_on_empty} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, fail_on_empty: value } })} />
                <Toggle label="Fail Quality Gate" checked={profile.data.fail_on_quality_error} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, fail_on_quality_error: value } })} />
                <Toggle label="Stratified" checked={profile.data.split.stratified} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, stratified: value } } })} />
                <Toggle label="Immutable Holdout" checked={profile.data.split.immutable_holdout} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, immutable_holdout: value } } })} />
                <Toggle label="Cross-validation" checked={profile.data.split.cross_validation_enabled} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, cross_validation_enabled: value } } })} />
              </div>
              {profile.data.split.cross_validation_enabled ? (
                <NumberField label="CV Folds" value={profile.data.split.cross_validation_folds} step={1} onChange={(value) => updateProfile({ ...profile, data: { ...profile.data, split: { ...profile.data.split, cross_validation_folds: value } } })} />
              ) : null}
            </div>
          ) : null}

          {activeStep === "training" ? (
            <div className="blueprint-step-content" data-step="training">
              <SectionHeading icon={<FlaskConical />} title="Approved Model Component" />
              <div className="segmented-control model-component-control" aria-label="Model component">
                {modelComponents.map((component) => (
                  <button
                    type="button"
                    key={`${component.component_id}:${component.version}`}
                    className={profile.model.component_id === component.component_id && profile.model.component_version === component.version ? "active" : ""}
                    onClick={() => updateProfile({
                      ...profile,
                      model: {
                        ...profile.model,
                        component_id: component.component_id,
                        component_version: component.version,
                        architecture: component.architecture,
                        input_size: component.default_input_size,
                        batch_size: component.architecture === "efficientnet-b7" ? 4 : 64,
                        learning_rate: component.architecture === "efficientnet-b7" ? 0.0001 : 0.0003,
                        search_space: {
                          ...profile.model.search_space,
                          learning_rates: component.architecture === "efficientnet-b7" ? [0.00005, 0.0001] : [0.0001, 0.0003],
                          batch_sizes: component.architecture === "efficientnet-b7" ? [4, 8] : [32, 64]
                        }
                      }
                    })}
                  >
                    {component.display_name}
                  </button>
                ))}
              </div>
              <div className="profile-form-grid">
                <NumberField label="Input Size" value={profile.model.input_size} step={8} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, input_size: value } })} />
                <NumberField label="Batch Size" value={profile.model.batch_size} step={8} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, batch_size: value } })} />
                <NumberField label="Epochs" value={profile.model.epochs} step={1} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, epochs: value } })} />
                <SelectField label="Optimizer" value={profile.model.optimizer} options={["adamw", "sgd"]} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, optimizer: value as "adamw" | "sgd" } })} />
                <NumberField label="Learning Rate" value={profile.model.learning_rate} step={0.0001} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, learning_rate: value } })} />
                <NumberField label="Weight Decay" value={profile.model.weight_decay} step={0.0001} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, weight_decay: value } })} />
                <SelectField label="Early Stop Metric" value={profile.model.early_stop_metric} options={["accuracy", "f1", "auroc"]} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, early_stop_metric: value as "accuracy" | "f1" | "auroc" } })} />
                <NumberField label="Early Stop" value={profile.model.early_stop_threshold} step={0.01} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, early_stop_threshold: value } })} />
                <NumberField label="Min Epochs" value={profile.model.early_stop_min_epochs} step={1} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, early_stop_min_epochs: value } })} />
                <NumberField label="Patience" value={profile.model.early_stop_patience} step={1} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, early_stop_patience: value } })} />
                <SelectField label="Tuning" value={profile.model.tuning_mode} options={["manual", "grid", "bayesian"]} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, tuning_mode: value as "manual" | "grid" | "bayesian" } })} />
                <NumberField label="Max Trials" value={profile.model.max_trials} step={1} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, max_trials: value } })} />
              </div>
              <div className="toggle-row">
                <Toggle label="Pretrained" checked={profile.model.pretrained} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, pretrained: value } })} />
                <Toggle label="Freeze Backbone" checked={profile.model.freeze_backbone} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, freeze_backbone: value } })} />
                <Toggle label="Mixed Precision" checked={profile.model.mixed_precision} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, mixed_precision: value } })} />
                <Toggle label="Class Weighted Loss" checked={profile.model.class_weighted_loss} onChange={(value) => updateProfile({ ...profile, model: { ...profile.model, class_weighted_loss: value } })} />
              </div>
              {profile.model.tuning_mode !== "manual" || profile.data.split.cross_validation_enabled ? (
                <>
                  <SectionHeading icon={<SlidersHorizontal />} title="Bounded Search Space" />
                  <div className="profile-form-grid">
                    <NumberListField label="Learning Rates" values={profile.model.search_space.learning_rates} onChange={(values) => updateProfile({ ...profile, model: { ...profile.model, search_space: { ...profile.model.search_space, learning_rates: values } } })} />
                    <NumberListField label="Weight Decays" values={profile.model.search_space.weight_decays} onChange={(values) => updateProfile({ ...profile, model: { ...profile.model, search_space: { ...profile.model.search_space, weight_decays: values } } })} />
                    <IntegerListField label="Batch Sizes" values={profile.model.search_space.batch_sizes} onChange={(values) => updateProfile({ ...profile, model: { ...profile.model, search_space: { ...profile.model.search_space, batch_sizes: values } } })} />
                  </div>
                  <div className="toggle-row">
                    <Toggle label="Search AdamW" checked={profile.model.search_space.optimizers.includes("adamw")} onChange={(checked) => updateProfile({ ...profile, model: { ...profile.model, search_space: { ...profile.model.search_space, optimizers: toggleOption(profile.model.search_space.optimizers, "adamw", checked) } } })} />
                    <Toggle label="Search SGD" checked={profile.model.search_space.optimizers.includes("sgd")} onChange={(checked) => updateProfile({ ...profile, model: { ...profile.model, search_space: { ...profile.model.search_space, optimizers: toggleOption(profile.model.search_space.optimizers, "sgd", checked) } } })} />
                    <Toggle label="Try Fine-tune" checked={profile.model.search_space.freeze_backbone_options.includes(false)} onChange={(checked) => updateProfile({ ...profile, model: { ...profile.model, search_space: { ...profile.model.search_space, freeze_backbone_options: toggleOption(profile.model.search_space.freeze_backbone_options, false, checked) } } })} />
                    <Toggle label="Try Frozen" checked={profile.model.search_space.freeze_backbone_options.includes(true)} onChange={(checked) => updateProfile({ ...profile, model: { ...profile.model, search_space: { ...profile.model.search_space, freeze_backbone_options: toggleOption(profile.model.search_space.freeze_backbone_options, true, checked) } } })} />
                  </div>
                </>
              ) : null}
              <SectionHeading icon={<Wrench />} title="Experiment And Compute" />
              <div className="profile-form-grid">
                <TextField className="field-wide" label="MLflow Experiment" value={profile.experiment.mlflow_experiment_name} onChange={(value) => updateProfile({ ...profile, experiment: { ...profile.experiment, mlflow_experiment_name: value } })} />
                <SelectField label="Primary Metric" value={profile.experiment.primary_metric} options={["accuracy", "f1", "auroc"]} onChange={(value) => updateProfile({ ...profile, experiment: { ...profile.experiment, primary_metric: value as "accuracy" | "f1" | "auroc" } })} />
                <NumberField label="Repeats" value={profile.experiment.repeats} step={1} onChange={(value) => updateProfile({ ...profile, experiment: { ...profile.experiment, repeats: value } })} />
                <SelectField label="Compute Target" value={profile.resources.compute_target} options={["windows-rtx-4080-super", "mac-mini-m4-pro", "cpu-local"]} onChange={(value) => updateProfile({ ...profile, resources: { ...profile.resources, compute_target: value as PipelineRunProfile["resources"]["compute_target"] } })} />
                <NumberField label="GPU" value={profile.resources.gpu_count} step={1} onChange={(value) => updateProfile({ ...profile, resources: { ...profile.resources, gpu_count: value } })} />
                <NumberField label="CPU" value={profile.resources.cpu_request} step={1} onChange={(value) => updateProfile({ ...profile, resources: { ...profile.resources, cpu_request: value } })} />
                <NumberField label="Memory GiB" value={profile.resources.memory_gb} step={2} onChange={(value) => updateProfile({ ...profile, resources: { ...profile.resources, memory_gb: value } })} />
                <NumberField label="Parallel Trials" value={profile.resources.max_parallel_trials} step={1} onChange={(value) => updateProfile({ ...profile, resources: { ...profile.resources, max_parallel_trials: value } })} />
              </div>
            </div>
          ) : null}

          {activeStep === "release" ? (
            <div className="blueprint-step-content" data-step="release">
              <SectionHeading icon={<ShieldCheck />} title="Promotion Gates" />
              <div className="profile-form-grid">
                <NumberField label="Min Accuracy" value={profile.gates.promotion_min_accuracy} step={0.01} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, promotion_min_accuracy: value } })} />
                <NumberField label="Min F1" value={profile.gates.promotion_min_f1} step={0.01} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, promotion_min_f1: value } })} />
                <NumberField label="Min AUROC" value={profile.gates.promotion_min_auroc} step={0.01} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, promotion_min_auroc: value } })} />
                <SelectField label="Environment" value={profile.gates.target_environment} options={Object.keys(namespaces)} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, target_environment: value as EnvironmentTier, target_namespace: namespaces[value as EnvironmentTier] } })} />
                <TextField label="Namespace" value={profile.gates.target_namespace} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, target_namespace: value } })} />
                <SelectField label="Approval" value={profile.gates.approval_policy} options={["manual", "two_person", "change_ticket"]} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, approval_policy: value as "manual" | "two_person" | "change_ticket" } })} />
                <SelectField label="CT Dataset Split" value={profile.gates.ct_dataset_split} options={["validation", "test"]} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, ct_dataset_split: value as "validation" | "test" }, data: { ...profile.data, split: { ...profile.data.split, holdout_split: value as "validation" | "test" } } })} />
              </div>
              <div className="toggle-row">
                <Toggle label="Require CI" checked={profile.gates.require_ci} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, require_ci: value } })} />
                <Toggle label="Require CD" checked={profile.gates.require_cd} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, require_cd: value } })} />
                <Toggle label="Require CT" checked={profile.gates.require_ct} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, require_ct: value } })} />
                <Toggle label="Isolated CT Dataset" checked={profile.gates.isolated_ct_dataset_required} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, isolated_ct_dataset_required: value } })} />
                <Toggle label="Drift Review" checked={profile.gates.require_drift_review} onChange={(value) => updateProfile({ ...profile, gates: { ...profile.gates, require_drift_review: value } })} />
                <Toggle label="A/B Test" checked={profile.experiment.ab_test_enabled} onChange={(value) => updateProfile({ ...profile, experiment: { ...profile.experiment, ab_test_enabled: value } })} />
              </div>
              {profile.experiment.ab_test_enabled ? (
                <div className="profile-form-grid">
                  <TextField label="Control Candidate" value={profile.experiment.control_candidate_id || ""} onChange={(value) => updateProfile({ ...profile, experiment: { ...profile.experiment, control_candidate_id: value } })} />
                  <TextField label="Challenger Candidate" value={profile.experiment.challenger_candidate_id || ""} onChange={(value) => updateProfile({ ...profile, experiment: { ...profile.experiment, challenger_candidate_id: value } })} />
                  <NumberField label="Challenger Traffic %" value={profile.experiment.challenger_traffic_percent} step={1} onChange={(value) => updateProfile({ ...profile, experiment: { ...profile.experiment, challenger_traffic_percent: value } })} />
                </div>
              ) : null}
            </div>
          ) : null}

          {activeStep === "review" ? (
            <div className="blueprint-step-content" data-step="review">
              <SectionHeading icon={<ShieldCheck />} title="Reproducibility Snapshot" />
              <dl className="blueprint-review-grid">
                <SummaryRow label="Dataset" value={`${profile.data.dataset_name} / ${profile.data.dataset_version}`} />
                <SummaryRow label="Split" value={`${profile.data.split.seed} / ${profile.data.split.train}:${profile.data.split.validation}:${profile.data.split.test}`} />
                <SummaryRow label="Model" value={`${profile.model.component_id} / ${profile.model.component_version}`} />
                <SummaryRow label="Training" value={`${profile.model.epochs} epochs / batch ${profile.model.batch_size} / ${profile.model.optimizer}`} />
                <SummaryRow label="Search" value={`${profile.model.tuning_mode} / ${profile.model.max_trials} trials / ${profile.data.split.cross_validation_enabled ? `${profile.data.split.cross_validation_folds}-fold` : "single split"}`} />
                <SummaryRow label="Target" value={`${profile.gates.target_environment} / ${profile.gates.target_namespace}`} />
                <SummaryRow label="Compute" value={`${profile.resources.compute_target} / GPU ${profile.resources.gpu_count}`} />
              </dl>
              <SectionHeading icon={<Cpu />} title="Pinned Runtime Sources" />
              <div className="profile-form-grid">
                <TextField className="field-wide" label="Airflow Base Config" value={profile.base_airflow_config} onChange={(value) => updateProfile({ ...profile, base_airflow_config: value })} />
                <TextField className="field-wide" label="Model Base Config" value={profile.base_model_config} onChange={(value) => updateProfile({ ...profile, base_model_config: value })} />
              </div>
              <SectionHeading icon={<LockKeyhole />} title="Replay Integrity" />
              <div className="replay-verification" data-status={replayValidation?.status || "unsaved"}>
                <div>
                  <StatusBadge status={replayValidation?.status || "unknown"} compact />
                  <strong>{replayValidation ? `${replayValidation.checks.filter((check) => check.status === "pass").length}/${replayValidation.checks.length} identities sealed` : "Save a version to seal runtime identities"}</strong>
                  <span>{replayValidation?.reproducibility_digest.slice(0, 20) || "Profile, data, split, configs, catalog and container images"}</span>
                </div>
                {replayValidation ? (
                  <details>
                    <summary>Integrity checks</summary>
                    <ul>
                      {replayValidation.checks.map((check) => (
                        <li key={check.check_id} data-status={check.status}>
                          <span>{check.check_id.replaceAll("_", " ")}</span>
                          <strong>{check.status}</strong>
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
              </div>
            </div>
          ) : null}

          <div className="profile-actions">
            <button type="button" className="secondary-action" onClick={openRaw} disabled={busy}>
              <Braces size={16} /> Config Document
            </button>
            {stepIndex > 0 ? (
              <button type="button" className="secondary-action" onClick={() => moveStep(-1)} disabled={busy}>
                <ArrowLeft size={16} /> Back
              </button>
            ) : null}
            {stepIndex < blueprintSteps.length - 1 ? (
              <button type="button" className="primary-action" onClick={() => moveStep(1)} disabled={busy}>
                Next <ArrowRight size={16} />
              </button>
            ) : (
              <>
                <button type="button" className="secondary-action" onClick={() => void validateNow()} disabled={busy}>
                  <ShieldCheck size={16} /> Validate Blueprint
                </button>
                <button type="button" className="primary-action" onClick={() => void saveVersion()} disabled={busy || !validation?.executable}>
                  <Save size={16} /> Save Version
                </button>
              </>
            )}
          </div>
        </div>

        <div className="profile-inspector">
          <div className="panel blueprint-summary">
            <div className="panel-heading">
              <div><h2>Blueprint</h2><p>{profile.schema_version}</p></div>
              <FileInput />
            </div>
            <dl className="dense-list">
              <SummaryRow label="Data" value={profile.data.dataset_version} />
              <SummaryRow label="Model" value={`${profile.model.architecture} / ${profile.model.component_version}`} />
              <SummaryRow label="Seed" value={String(profile.data.split.seed)} />
              <SummaryRow label="Target" value={`${profile.gates.target_environment} / ${profile.gates.target_namespace}`} />
              <SummaryRow label="Digest" value={savedCurrent?.digest.slice(0, 16) || "unsaved"} />
            </dl>
          </div>

          <div className="panel blueprint-flow-panel">
            <div className="panel-heading">
              <div><h2>Execution Path</h2><p>{validation?.checked_at || "pending validation"}</p></div>
              <Play />
            </div>
            <LifecycleFlow items={flowItems} label="Blueprint execution path" />
          </div>

          {validation?.blockers.length ? (
            <div className="profile-blockers" role="alert">
              <strong><TriangleAlert size={15} /> Blockers</strong>
              {validation.blockers.map((item) => <span key={item}>{item}</span>)}
            </div>
          ) : null}

          <details className="panel capability-disclosure">
            <summary><span><Wrench size={16} /> Runtime Coverage</span><strong>{validation?.capabilities.filter((item) => item.active && item.status === "wired").length || 0}/{validation?.capabilities.filter((item) => item.active).length || 0}</strong></summary>
            <div className="capability-list">
              {(validation?.capabilities || []).map((item) => <Capability key={item.capability_id} item={item} />)}
            </div>
          </details>

          <div className="panel profile-launch">
            <div>
              <span>Saved Blueprint</span>
              <strong>{savedCurrent ? `v${savedCurrent.version}` : "required"}</strong>
              <small>{savedCurrent?.digest.slice(0, 16) || "No immutable digest"}</small>
            </div>
            <button type="button" className="secondary-action" disabled={busy || !savedCurrent || !validation?.executable || replayValidation?.status !== "ready"} onClick={() => void launch(true)}>
              <ShieldCheck size={16} /> Create Dry Run
            </button>
            <button type="button" className="primary-action" disabled={busy || !savedCurrent || !validation?.executable || replayValidation?.status !== "ready"} onClick={() => void launch(false)}>
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


function TextField({ label, value, onChange, className = "" }: { label: string; value: string; onChange: (value: string) => void; className?: string }) {
  return <label className={className}><span>{label}</span><input aria-label={label} value={value} onChange={(event) => onChange(event.target.value)} /></label>;
}


function NumberField({ label, value, step, onChange }: { label: string; value: number; step: number; onChange: (value: number) => void }) {
  return <label><span>{label}</span><input aria-label={label} type="number" value={value} step={step} onChange={(event) => onChange(Number(event.target.value))} /></label>;
}


function NumberListField({ label, values, onChange }: { label: string; values: number[]; onChange: (values: number[]) => void }) {
  return <label><span>{label}</span><input aria-label={label} value={values.join(", ")} onChange={(event) => onChange(parseNumberList(event.target.value, false))} /></label>;
}


function IntegerListField({ label, values, onChange }: { label: string; values: number[]; onChange: (values: number[]) => void }) {
  return <label><span>{label}</span><input aria-label={label} value={values.join(", ")} onChange={(event) => onChange(parseNumberList(event.target.value, true))} /></label>;
}


function SelectField({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  return <label><span>{label}</span><select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>{options.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>;
}


function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="profile-toggle"><input aria-label={label} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i /><span>{label}</span></label>;
}


function parseNumberList(value: string, integers: boolean): number[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
    .map(Number)
    .filter((item) => Number.isFinite(item))
    .map((item) => integers ? Math.round(item) : item);
}


function toggleOption<T>(values: T[], option: T, checked: boolean): T[] {
  if (checked) return values.includes(option) ? values : [...values, option];
  return values.filter((value) => value !== option);
}


function Capability({ item }: { item: PipelineCapability }) {
  const icon = item.status === "wired" ? <CheckCircle2 /> : item.status === "partial" ? <Wrench /> : <LockKeyhole />;
  return <article className={`capability-${item.status} ${item.active ? "active" : "inactive"}`}>{icon}<div><strong>{item.label}</strong><span>{item.detail}</span></div><em>{item.active ? item.status.replace("_", " ") : "inactive"}</em></article>;
}


function SummaryRow({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value || "-"}</dd></div>;
}


function LifecycleState({ state }: { state: LifecycleRun["state"] }) {
  const label = state === "dry_run" ? "Dry Run Ready" : state === "queued" ? "Queued" : state.replaceAll("_", " ");
  return <strong className={`profile-lifecycle-state state-${state}`}>{label}</strong>;
}


function message(error: unknown): string {
  return error instanceof Error ? error.message : "Pipeline profile request failed";
}
