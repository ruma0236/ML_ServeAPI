# 2026-07-09 W6 Lakehouse Probe

Issue: `EVM-205` / Jira `SCRUM-86`

## Result

`EVM-205` is complete. A runnable lakehouse-scale ingestion research probe now
reads the validated Parquet dataset, writes a dataset summary, records
DuckDB/Polars/Spark/Iceberg tradeoffs, and emits a recommendation document.

## Files Changed

- `src/evm/pipelines/lakehouse_probe/run.py`
- `src/evm/pipelines/lakehouse_probe/__init__.py`
- `scripts/run_pipeline.py`
- `configs/local.toml`
- `configs/local_visa.toml`
- `configs/airflow.toml`
- `orchestration/airflow/dags/enterprise_vision_mlops_daily.py`
- `tests/test_lakehouse_probe.py`

## Pipeline Outputs

For the VisA open dataset config, the pipeline writes:

- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/lakehouse/visa/lakehouse_probe.json`
- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/lakehouse/visa/engine_tradeoff_matrix.json`
- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/lakehouse/visa/lakehouse_recommendation.md`

## VisA Verification

Command:

```powershell
& 'C:\Users\opop0\miniconda3\python.exe' .\scripts\run_pipeline.py lakehouse-probe --config configs\local_visa.toml
```

Observed:

- status: `pass`
- input parquet: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/validated/visa/validated_dataset.parquet`
- file size bytes: `1517264`
- row count: `10821`
- row group count: `1`
- column count: `20`
- label counts:
  - `normal`: `9621`
  - `anomaly`: `1200`
- split counts:
  - `train`: `6504`
  - `validation`: `2136`
  - `test`: `2181`
- engine availability:
  - `pyarrow`: `true`
  - `duckdb`: `false`
  - `polars`: `false`
  - `pyspark`: `false`
  - `pyiceberg`: `false`
- recommendation: `PyArrow now, DuckDB next`

## Test Verification

Command:

```powershell
pytest tests\test_lakehouse_probe.py
```

Observed:

- `1 passed`

## Runtime Note

The default `C:\Python314\python.exe` runtime does not currently have
`pyarrow`, so the real Parquet probe was executed with:

```text
C:\Users\opop0\miniconda3\python.exe
```

This matches the prior VisA Parquet verification path.

## Airflow Integration

The Airflow DAG now inserts `lakehouse_probe` after `data_validate` and before
`image_quality`, so scheduled cycles can record Parquet dataset readiness and
lakehouse engine recommendations before downstream quality/curation work.

## Handoff

For W7 Control Panel:

- show lakehouse row count, label/split/class distribution, and engine
  availability in dataset detail;
- treat DuckDB as the next local SQL dependency;
- defer Spark/Iceberg until dataset size or governance requirements justify a
  heavier lakehouse/table-format stack.
