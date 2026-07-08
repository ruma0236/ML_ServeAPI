# VLM Workload Pipeline

## Purpose

The W4 VLM workload pipeline turns the existing control-plane foundation into a
manufacturing visual inspection MLOps path. It validates image data, creates
repeatable shards, runs a mock VLM adapter, validates structured output, blocks
bad candidates, writes audit/RCA events, and exports observability evidence.

## Storage Policy

Large local data and artifacts are stored outside the Git repository.

| Runtime | Data Root |
|---|---|
| Windows local | `F:\EnterpriseMLOps_Data\enterprise-vision-mlops` |
| Docker/Airflow | `/mnt/evm-data` |

MinIO is also mounted to the F-drive backed root through
`EVM_HOST_MINIO_ROOT`, so object storage data does not grow inside the repo.

## Command Order

```powershell
python scripts\run_pipeline.py dataset-intake-audit --config configs\local.toml
python scripts\run_pipeline.py domain-pack-check --config configs\local.toml
python scripts\run_pipeline.py object-store-bootstrap --config configs\local.toml
python scripts\run_pipeline.py data-ingest --config configs\local.toml
python scripts\run_pipeline.py data-validate --config configs\local.toml
python scripts\run_pipeline.py image-quality --config configs\local.toml
python scripts\run_pipeline.py dataset-shards --config configs\local.toml
python scripts\run_pipeline.py vlm-contract --config configs\local.toml
python scripts\run_pipeline.py vlm-batch-eval --config configs\local.toml
python scripts\run_pipeline.py vlm-reliability --config configs\local.toml
python scripts\run_pipeline.py vlm-rca --config configs\local.toml
python scripts\run_pipeline.py vlm-observability --config configs\local.toml
```

For the real VisA open dataset cycle, use `configs\local_visa.toml`. This
configuration keeps all large raw, processed, validated, and VLM artifacts under
`F:\EnterpriseMLOps_Data\enterprise-vision-mlops` and scopes derived outputs to
the `visa` subdirectories.

The equivalent Makefile target is:

```powershell
make visa-open-data-cycle
```

If GNU Make is not available on Windows, run the commands listed in the
`visa-open-data-cycle` target in `Makefile`.

## Outputs

- `data/raw/industrial/source_registry.json`
- `data/raw/industrial/acquisition_plan.json`
- `data/raw/industrial/cleaning_benchmark.json`
- `data/raw/industrial/mvi_import_manifest.jsonl`
- `data/validated/mvi_quality_manifest.jsonl`
- `data/validated/mvi_quality_report.json`
- `data/validated/shards/shard_index.json`
- `artifacts/vlm/contracts/vlm_contract.json`
- `artifacts/vlm/latest_batch_summary.json`
- `artifacts/registry/vlm/prompt_registry.json`
- `artifacts/registry/vlm/model_registry.json`
- `artifacts/vlm/reliability/gate_report.json`
- `artifacts/vlm/audit/audit_events.jsonl`
- `artifacts/vlm/audit/failure_scenarios.json`
- `artifacts/vlm/observability/benchmark_report.json`
- `artifacts/vlm/observability/slo_report.md`
- `artifacts/vlm/observability/vlm_metrics.prom`

All paths above are under the configured F-drive backed root for local runs.

## Real Dataset Intake Status

`dataset-intake-audit` is the production-data entry point. It does not download
licensed datasets by itself. Instead, it scans the configured F-drive raw roots
from the manufacturing domain pack and writes:

- source/license/retention/access policy evidence,
- acquisition next actions and checkpoint locations,
- an import manifest when local images exist,
- exact-hash, dimension, brightness, blur, label, class, and split summaries.

When VisA or MVTec AD files are not present yet, the pipeline returns
`needs_data` and records `root_missing` for each dataset. That is a successful
control-plane audit state, not a claim that the real dataset has been validated.

## VisA Open Data Verification

On 2026-07-06, the real VisA open dataset was downloaded to the F-drive raw zone
and the complete W4 path was verified with `configs\local_visa.toml`.

- Records discovered and validated: `10821`.
- Label counts: `normal=9621`, `anomaly=1200`.
- Image quality: `status=pass`, `error_count=0`, `warning_count=0`.
- Dataset shards: `23`.
- VLM batch records: `10821`, `schema_valid_rate=1.0`.
- Reliability gate: `promotion_decision=promote_candidate`.
- Registry/serving: `vision-baseline` promoted to `version=5`,
  `ready_model_loaded=true`.
- Prometheus: `healthy_targets=2`.
- Storage: local and MinIO processed/validated datasets are real Parquet
  artifacts with `PAR1` magic bytes.

Detailed evidence is recorded in
`docs/status/2026-07-06-visa-open-data-cycle.md`.

## Data Quality And ETL Extension

The `image-quality` stage now uses a reusable data quality policy and ETL recipe
boundary instead of hard-coding all gate behavior inside the pipeline.

- Dataset contract:
  `domain_packs/manufacturing_visual_inspection/data_contract.toml`
- Quality policy:
  `domain_packs/manufacturing_visual_inspection/quality_policy.toml`
- ETL recipe:
  `domain_packs/manufacturing_visual_inspection/etl_recipe.toml`
- Runtime policy loader:
  `src/evm/data_quality/policy.py`
- ETL recipe loader:
  `src/evm/etl/recipe.py`

This means future datasets can add new checks and transforms without turning
`image-quality` into a single monolithic validation script. The current VisA
cycle still reports `10821` quality records, zero errors, zero warnings, and
`gate_decision.status=pass`.
