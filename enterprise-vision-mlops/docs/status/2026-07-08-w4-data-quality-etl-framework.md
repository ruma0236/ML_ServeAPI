# 2026-07-08 W4 Data Quality And ETL Framework Supplement

## Summary

W4 `image-quality` was refactored from a VisA-oriented validation step into the
first reusable data quality and ETL policy boundary. The existing VisA cycle
still passes, but quality severity, gate behavior, dataset contract, and ETL
recipe metadata are now declared in the manufacturing domain pack instead of
being only embedded in the pipeline code.

## Scope

- Added reusable data quality interfaces and policy objects.
- Added reusable ETL recipe and transform contracts.
- Added manufacturing visual inspection data contract, quality policy, and ETL
  recipe files.
- Updated `image-quality` to load policy and recipe metadata.
- Updated `domain-pack-check` to validate the referenced quality framework
  files and registries.
- Added regression tests for policy override behavior and image-quality
  pipeline enrichment.

## Architecture

New reusable modules:

- `src/evm/data_quality/policy.py`
- `src/evm/data_quality/runner.py`
- `src/evm/etl/recipe.py`
- `src/evm/etl/runner.py`

New domain-pack files:

- `domain_packs/manufacturing_visual_inspection/data_contract.toml`
- `domain_packs/manufacturing_visual_inspection/quality_policy.toml`
- `domain_packs/manufacturing_visual_inspection/etl_recipe.toml`

The domain pack now exposes:

- `quality_framework.dataset_contract`
- `quality_framework.quality_policy`
- `quality_framework.etl_recipe`
- `quality_framework.check_registry`
- `quality_framework.transform_registry`

## Gate Definition

The quality gate now separates:

- check result generation,
- severity assignment,
- gate decision,
- ETL recipe declaration,
- enriched quality manifest output.

Current manufacturing policy:

- blocking level: `error`
- blocking examples: missing image URI, unreadable image, invalid dimensions,
  unknown license
- warning examples: duplicate hash, dimension mismatch, missing local image,
  missing label, missing anomaly mask, drift proxy warning

## Verification

Executed:

```powershell
python -m compileall -q src tests scripts
C:\Users\opop0\miniconda3\python.exe -m compileall -q src tests scripts
C:\Users\opop0\miniconda3\python.exe -m pytest -q
python scripts\run_pipeline.py domain-pack-check --config configs\local_visa.toml
C:\Users\opop0\miniconda3\python.exe scripts\run_pipeline.py image-quality --config configs\local_visa.toml
C:\Users\opop0\miniconda3\python.exe scripts\run_pipeline.py dataset-shards --config configs\local_visa.toml
C:\Users\opop0\miniconda3\python.exe scripts\run_pipeline.py vlm-observability --config configs\local_visa.toml
```

Observed:

- Tests: `3 passed`.
- Domain pack: `status=pass`, `check_registry_count=8`,
  `transform_registry_count=4`.
- Image quality: `status=pass`, `gate_decision.status=pass`,
  `policy_id=mvi_quality_policy_v1`, `recipe_id=mvi_open_dataset_etl_recipe_v1`.
- VisA image quality records: `10821`.
- Image quality errors/warnings: `0/0`.
- Dataset shards after refactor: `23`.
- VLM observability after refactor: `status=pass`,
  `records=10821`, `schema_valid_rate=1.0`, `healthy downstream reports`.

## Remaining Boundary

This establishes the extension point, not every future transform. The next
data-platform evolution should add executable transform plugins for
deduplication, image normalization, annotation conversion, HITL curation queues,
drift baselines, and lakehouse-scale validation.
