import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchDefaultPipelineProfile,
  launchPipelineProfile,
  savePipelineProfile,
  validatePipelineProfile
} from "../../apps/control-panel/src/api/controlPanelClient";
import type {
  PipelineProfileRecord,
  PipelineProfileValidation,
  PipelineRunProfile
} from "../../apps/control-panel/src/api/types";


const profile = {
  schema_version: "evm.pipeline_profile.v1",
  profile_name: "contract-profile",
  description: "contract",
  owner: "ml-platform",
  execution_scope: "data_cycle",
  base_airflow_config: "configs/airflow.toml",
  base_model_config: "configs/w7_efficientnet_real_test.toml",
  data: {
    dataset_name: "visa",
    dataset_version: "visa-v1",
    source_manifest_uri: "F:/data/manifest.jsonl",
    split_manifest_uri: "F:/data/shards.json",
    split_manifest_sha256: "a".repeat(64),
    fail_on_empty: true,
    fail_on_quality_error: true,
    duplicate_severity: "warn",
    dimension_severity: "warn",
    max_review_samples: 128,
    records_per_shard: 512,
    split: {
      seed: 42,
      train: 0.6,
      validation: 0.2,
      test: 0.2,
      stratified: true,
      cross_validation_enabled: false,
      cross_validation_folds: 5,
      holdout_split: "test",
      immutable_holdout: true,
      allow_holdout_in_training: false
    }
  },
  model: {
    framework: "torch",
    architecture: "efficientnet-b0",
    pretrained: true,
    freeze_backbone: false,
    input_size: 224,
    batch_size: 64,
    epochs: 20,
    optimizer: "adamw",
    learning_rate: 0.0001,
    weight_decay: 0.0001,
    mixed_precision: true,
    class_weighted_loss: true,
    early_stop_metric: "accuracy",
    early_stop_threshold: 0.93,
    early_stop_min_epochs: 2,
    early_stop_patience: 3,
    tuning_mode: "manual",
    max_trials: 1
  },
  experiment: {
    mlflow_experiment_name: "contract",
    primary_metric: "f1",
    repeats: 1,
    ab_test_enabled: false,
    control_candidate_id: null,
    challenger_candidate_id: null,
    challenger_traffic_percent: 10
  },
  gates: {
    promotion_min_accuracy: 0.8,
    promotion_min_f1: 0.75,
    promotion_min_auroc: 0.8,
    isolated_ct_dataset_required: true,
    ct_dataset_split: "test",
    require_ci: true,
    require_cd: true,
    require_ct: true,
    require_drift_review: true,
    approval_policy: "two_person",
    target_environment: "staging",
    target_namespace: "evm-staging"
  },
  resources: {
    compute_target: "windows-rtx-4080-super",
    gpu_count: 1,
    cpu_request: 6,
    memory_gb: 16,
    max_parallel_trials: 1
  }
} satisfies PipelineRunProfile;


const validation: PipelineProfileValidation = {
  status: "ready",
  valid: true,
  executable: true,
  checked_at: "2026-07-12T00:00:00Z",
  blockers: [],
  warnings: [],
  capabilities: [],
  stages: []
};


describe("pipeline profile API bindings", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("loads, validates, versions, and launches one profile", async () => {
    const record: PipelineProfileRecord = {
      profile_id: "contract-profile",
      version: 1,
      digest: "b".repeat(64),
      created_at: "2026-07-12T00:00:00Z",
      profile,
      validation,
      profile_uri: "F:/profiles/v1/profile.json",
      airflow_config_uri: "F:/profiles/v1/airflow.runtime.json",
      airflow_runtime_uri: "/mnt/evm-data/profiles/v1/airflow.runtime.json",
      model_config_uri: "F:/profiles/v1/model.runtime.json",
      model_runtime_uri: "/mnt/evm-data/profiles/v1/model.runtime.json"
    };
    const launch = { profile_id: record.profile_id, version: 1, validation, task: null };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(profile), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(validation), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(record), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(launch), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);

    expect((await fetchDefaultPipelineProfile("http://control-panel.test")).profile_name).toBe("contract-profile");
    expect((await validatePipelineProfile(profile, "http://control-panel.test")).executable).toBe(true);
    expect((await savePipelineProfile(profile, "http://control-panel.test")).version).toBe(1);
    expect((await launchPipelineProfile(record.profile_id, 1, { actor: "ml-platform", reason: "test", dry_run: true }, "http://control-panel.test")).profile_id).toBe(record.profile_id);
    expect(fetchMock.mock.calls[3][0]).toBe(
      "http://control-panel.test/control-panel/v1/pipeline-profiles/contract-profile/launch?version=1"
    );
  });
});
