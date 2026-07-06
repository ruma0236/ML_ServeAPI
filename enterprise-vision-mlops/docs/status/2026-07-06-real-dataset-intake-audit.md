# 2026-07-06 Real Dataset Intake Audit

## Summary

Implemented the first production-data intake layer for the manufacturing VLM
path. The new `dataset-intake-audit` pipeline scans F-drive raw dataset roots
defined in the manufacturing domain pack and writes source registry,
acquisition plan, cleaning benchmark, and import manifest artifacts.

This closes the control-plane portion of `EVM-201`, `EVM-202`, and `EVM-203`.
It does not mean VisA or MVTec AD has been downloaded or fully validated yet.
The current runtime state is `needs_data` because the configured raw roots are
not populated.

## Scope

- Data source registry and collection policy.
- Large-scale acquisition planner with checkpoint locations.
- Exact-hash and byte-level cleaning benchmark.
- Import manifest generation for VisA/MVTec-style local directory layouts.
- F-drive storage preservation for raw data and generated intake artifacts.

## Artifacts

- `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\data\raw\industrial\source_registry.json`
- `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\data\raw\industrial\acquisition_plan.json`
- `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\data\raw\industrial\cleaning_benchmark.json`
- `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\data\raw\industrial\mvi_import_manifest.jsonl`

## Verification

Executed:

```powershell
python -m py_compile src\evm\core\data_intake.py src\evm\pipelines\dataset_intake_audit\run.py scripts\run_pipeline.py orchestration\airflow\dags\enterprise_vision_mlops_daily.py
python scripts\run_pipeline.py dataset-intake-audit --config configs\local.toml
```

Observed:

- `status=needs_data`
- `datasets_checked=2`
- `datasets_ready=0`
- `records_discovered=0`
- VisA root state: `root_missing`
- MVTec AD root state: `root_missing`
- registry/plan/benchmark/manifest files were written under the F-drive root

Additional fixture smoke verified that a VisA-style local PNG file is converted
into a manifest record with positive width/height, normal label type, and a
cleaning benchmark with zero unreadable images.

## Source Policy Notes

- VisA source: `https://registry.opendata.aws/visa/`
- VisA license recorded in the domain pack: `CC-BY-4.0`
- MVTec AD source: `https://www.mvtec.com/research-teaching/datasets/mvtec-ad`
- MVTec AD license recorded in the domain pack: `CC-BY-NC-SA-4.0`

Both sources are configured with `manual_or_scripted_download_after_license_review`.

## Next Handoff

Populate one of these raw roots after license review:

- `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\data\raw\industrial\visa`
- `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\data\raw\industrial\mvtec_ad`

Then rerun `dataset-intake-audit`. The expected next state should move from
`needs_data` to `ready_for_import`, and `records_discovered` should become
greater than zero.
