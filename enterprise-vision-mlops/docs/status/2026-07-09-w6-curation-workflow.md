# 2026-07-09 W6 Curation Workflow

Issue: `EVM-204` / Jira `SCRUM-85`

## Result

`EVM-204` is complete. A reusable curation workflow pipeline now defines sample
review, label state, HITL queue, and curated eval-set promotion states from the
image-quality manifest.

## Files Changed

- `src/evm/pipelines/curation_workflow/run.py`
- `src/evm/pipelines/curation_workflow/__init__.py`
- `scripts/run_pipeline.py`
- `configs/local.toml`
- `configs/local_visa.toml`
- `configs/airflow.toml`
- `orchestration/airflow/dags/enterprise_vision_mlops_daily.py`
- `tests/test_curation_workflow.py`

## Pipeline Outputs

For the VisA open dataset config, the pipeline writes:

- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/validated/visa/curation/curation_state.json`
- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/validated/visa/curation/curation_manifest.jsonl`
- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/validated/visa/curation/hitl_queue.jsonl`
- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/validated/visa/curation/sample_review.jsonl`
- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/validated/visa/curation/curated_eval_manifest.jsonl`

## VisA Verification

Command:

```powershell
python .\scripts\run_pipeline.py curation-workflow --config configs\local_visa.toml
```

Observed:

- record count: `10821`
- HITL queue count: `128`
- sample review count: `128`
- curated eval count: `4317`
- label state counts:
  - `auto_accepted`: `10693`
  - `sample_review`: `128`
- review state counts:
  - `not_required`: `10693`
  - `review_requested`: `128`
- eval promotion state counts:
  - `not_candidate`: `6504`
  - `candidate`: `4317`

## Test Verification

Command:

```powershell
pytest tests\test_curation_workflow.py
```

Observed:

- `1 passed`

## Airflow Integration

The Airflow DAG now inserts `curation_workflow` between `image_quality` and
`dataset_shards`, so future scheduled cycles create curation/HITL/eval-set
artifacts before sharding and VLM evaluation.

## Handoff

The W7 Control Panel should use:

- `label_state`
- `review_state`
- `eval_promotion_state`
- `review_reasons`
- `hitl_queue.jsonl`
- `curated_eval_manifest.jsonl`

These fields can directly power the curation tab and stage-level intermediate
result drilldown.
