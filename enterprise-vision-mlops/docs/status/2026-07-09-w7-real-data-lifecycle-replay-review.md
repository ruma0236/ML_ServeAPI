# 2026-07-09 W7 Real Data Lifecycle Replay Review

## Summary

The implemented W7 lifecycle surface was replayed against the local VisA real
dataset evidence on the F drive. The replay confirms that the current system can
run the data validation, image quality, curation, lakehouse, training, registry,
and lifecycle stages over real data and expose the resulting state through the
Control Panel `CycleRun` contract.

This is not yet a full enterprise production closure. It is a reproducible
local-lab proof for the currently implemented lifecycle. The remaining hard
gaps are real Kubernetes job execution, Torch EfficientNet-B0/B7 training
evidence, and non-dry-run orchestration mutation.

## Replay Evidence

- Evidence root:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/lifecycle_replay/evm-lifecycle-replay-20260709T231259`
- Config:
  `configs/local_visa.toml`
- Git HEAD at replay:
  `29f52d1`
- Dataset:
  `visa-open-data-f1f1c9ee9922`
- Records:
  `10821`
- Label counts:
  `normal=9621`, `anomaly=1200`
- Shards:
  `23`
- Split:
  `train=6504`, `validation=2136`, `test=2181`
- Curation:
  `128` HITL review items and `4317` curated eval records
- Lakehouse:
  Parquet probe passed with `10821` rows
- Model:
  `vision-baseline` v11, `image_feature_centroid`, `Shadow`
- Training:
  `10821` records used, `0` skipped, CPU execution, GPU detected
- MLflow:
  run `f243e6aea04f4bffa5b84d5f064814e4` logged
- Promotion:
  blocked by `accuracy`, `precision`, `recall`, `f1`, and `auroc` thresholds

## CycleRun Contract Result

- Captured payload:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/lifecycle_replay/evm-lifecycle-replay-20260709T231259/cycle_run_latest.json`
- Schema report:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/lifecycle_replay/evm-lifecycle-replay-20260709T231259/cycle_run_schema_validation.json`
- Result:
  valid `CycleRun`
- Latest cycle:
  `cycle-w7-visa-open-data-f1f1c9ee9922-vision-baseline-v11`
- Stage statuses:
  data validation pass, image quality pass, curation workflow pass,
  lakehouse probe pass, model registry pass, model lifecycle blocked,
  EfficientNet real test queued

## Remediation Added

- `scripts/dev/w7_lifecycle_replay.ps1` now provides a reproducible local
  lifecycle replay command that records per-stage stdout/stderr, Git metadata,
  `CycleRun`, and schema validation under the F-drive evidence root.
- `apps/api/Dockerfile` now packages the Control Panel API router dependencies,
  configs, and domain packs needed by the container API runtime.
- `docker-compose.yml` now sets `EVM_CONTROL_PANEL_CONFIG=configs/airflow.toml`
  so the API container reads `/mnt/evm-data` paths rather than Windows-only
  `F:/...` paths.
- `infra/kubernetes/local` now points container jobs and runtime config at
  `configs/airflow.toml`, aligning K8s execution with the mounted data path.

## Post-Remediation Verification

- Rebuilt and recreated the Compose API container with
  `docker compose up -d --build api`.
- Verified serving readiness at `http://127.0.0.1:8000/ready`:
  `vision-baseline` v11, `Shadow`, `image_feature_centroid`,
  `placeholder=false`.
- Verified Control Panel API at
  `http://127.0.0.1:8000/control-panel/v1/cycles/latest`.
- Captured and validated the 8000 HTTP `CycleRun` payload:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/lifecycle_replay/evm-lifecycle-replay-20260709T231259/cycle_run_http_8000.json`
  and `cycle_run_http_8000_schema_validation.json`.
- Ran deployment check:
  `/health=200`, `/ready=200`, `/predict=200`, `contract_ok=true`.
- Ran monitoring check:
  Prometheus `active_targets=2`, `healthy_targets=2`.
- Ran real-test policy validation:
  `valid=true`, `mock_allowed=false`, `smoke_allowed=false`,
  `minimum_records=10821`.
- Ran Control Panel lint/build and full Playwright desktop/mobile e2e:
  `14 passed`.
- Captured final all-tab screenshots under:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/lifecycle_replay/evm-lifecycle-replay-20260709T231259/ui_all_tabs_8000_final`.

## UI Review

The dark theme is now black/neutral based and the current tab captures are
readable across desktop and mobile widths. A visual defect in the overview
lifecycle ring was found during screenshot review: stage nodes could render
outside the ring on full-page captures. The ring node positioning was corrected
so all lifecycle dots remain anchored inside the cycle card.

## Production Readiness Judgment

Current W7 is valid for a local enterprise-style lifecycle observability proof:

- real dataset validation and Parquet/lakehouse artifact creation
- image quality gate and HITL curation state
- model training/registry/lifecycle state over real VisA records
- MLflow metric logging
- API `CycleRun` aggregation and UI visualization
- dry-run task and command control state

Current W7 is not yet valid as a full enterprise production MLOps platform:

- Kubernetes manifests still need real `kubectl apply`, pod/job status, logs,
  and generated artifact proof.
- EfficientNet-B0/B7 Torch candidates remain queued and require real MLflow
  runs, model artifacts, metrics, GPU profile, and confusion-matrix evidence.
- Airflow/MLflow/Kubernetes UI operations are still dry-run/queued/confirm
  contracts, not production mutation executors.
- VLM/multimodal serving remains a target direction, not a real serving
  endpoint with production-grade evaluation.

## Recommended Next Steps

1. Execute EVM-226 Kubernetes proof with real pod/job logs and artifacts.
2. Execute EVM-237 EfficientNet-B0/B7 real model matrix and close EVM-238-B
   only after the candidate evidence validates.
3. Replace dry-run-only task and command control with guarded production
   mutation executors after Kubernetes and Airflow/MLflow control contracts are
   validated.
