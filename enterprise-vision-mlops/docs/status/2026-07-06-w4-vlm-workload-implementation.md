# 2026-07-06 W4 VLM Workload Implementation

## Summary

W4 implementation was advanced from planning into an executable VLM-first
manufacturing visual inspection workflow. The implementation keeps large local
data and artifacts on the F drive and exposes the workflow through the same
pipeline command pattern used by the earlier MLOps phases.

## Completed Scope

| Area | Issue IDs | Result |
|---|---|---|
| F-drive storage policy | `EVM-134` to `EVM-181` support | local data/artifacts now resolve to `F:\EnterpriseMLOps_Data\enterprise-vision-mlops`; Docker/Airflow sees `/mnt/evm-data` |
| Image quality validation | `EVM-134` | `image-quality` validates readability, hash duplication, dimensions, brightness/blur proxy, labels, splits |
| Dataset sharding | `EVM-135` | `dataset-shards` creates deterministic JSONL shards and shard index |
| VLM adapter/router | `EVM-141`, `EVM-142` | `vlm-contract` validates request/response schema and router behavior |
| Batch VLM evaluation | `EVM-143`, `EVM-144` | `vlm-batch-eval` writes request/response JSONL and schema validation report |
| Registry/gate | `EVM-151`, `EVM-152` | `vlm-reliability` writes prompt/model registries and blocks a bad prompt candidate |
| Audit/RCA/failure scenarios | `EVM-161`, `EVM-162` | `vlm-rca` writes audit events and four failure scenarios |
| Benchmark/SLO/metrics | `EVM-171`, `EVM-061` to `EVM-065` | `vlm-observability` writes benchmark, SLO, and Prometheus-style metric artifacts |
| CI/demo evidence | `EVM-071` to `EVM-075`, `EVM-181` | GitHub Actions CI workflow, release note, final demo script |

## Verification

Executed pipeline sequence:

```powershell
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
python scripts\run_pipeline.py train --config configs\local.toml
python scripts\run_pipeline.py register-model --config configs\local.toml
python scripts\run_pipeline.py deploy-check --config configs\local.toml
python scripts\run_pipeline.py monitor-check --config configs\local.toml
```

Observed result:

- Domain pack: `pass`.
- Data validation: `valid_records=8`, `invalid_records=0`.
- Image quality: `status=pass`, `error_count=0`, `warning_count=16`.
- Dataset shards: `record_count=8`, `shard_count=3`.
- VLM contract: `status=pass`.
- VLM batch: `records=8`, `schema_valid_rate=1.0`, `p95_latency_ms=8.903`.
- VLM reliability: `status=pass`, `promotion_decision=promote_candidate`,
  bad prompt candidate blocked.
- VLM RCA: `event_count=23`, `scenario_count=4`.
- VLM observability: `status=pass`.
- Training: `mlflow_status=logged`,
  `mlflow_run_id=a87db900acf1481ebf1e1ae45b19be5a`.
- Model registry: local CLI run advanced to `version=2`; Airflow verification
  run advanced to `version=3`, `stage=Production`.
- Deployment check: `contract_ok=true`, `ready_model_loaded=true`,
  `predict_placeholder=false`.
- Monitoring check: `healthy_targets=2`.
- API `/ready`: `model_loaded=true`, `model_version=3`.
- API `/metrics`: `evm_vlm_schema_valid_rate=1.0`,
  `evm_vlm_p95_latency_ms=8.903`, `evm_vlm_quality_error_count=0`,
  `evm_vlm_audit_event_count=23`.
- Airflow manual scheduler run:
  `codex_w4_verify_20260706T080000Z`, `state=success`, completed at
  `2026-07-06T08:00:10Z`.
- Static checks: `python -m compileall src scripts apps/api orchestration/airflow/dags`,
  `python -m json.tool monitoring/grafana/dashboards/local-mvp.json`,
  `docker compose config --quiet`, and `git diff --check` passed. The only
  `git diff --check` output was Git line-ending conversion warnings.
- Secret scan: no live Jira token was found in repo or Obsidian memory paths;
  only `.env.example` placeholder text matched.

## Notes

- Local Python 3.14 could not install `pyarrow==18.1.0` from a wheel, so
  `write_parquet` now falls back to JSONL at the configured Parquet path when
  `pyarrow` is unavailable. Docker/CI Python 3.11 keeps the normal Parquet
  dependency path.
- The placeholder seed images produce duplicate hash warnings and header
  dimension fallback warnings. These are expected until real VisA/MVTec images
  are imported.
