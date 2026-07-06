# W3 Registry-Driven Serving Status

Date: 2026-07-05
Branch: `codex/mac-mini-worker`
Scope: W3 first implementation tranche, `EVM-051` through `EVM-055`.

## Summary

The serving API now loads the promoted local registry metadata at runtime and
uses it for readiness, prediction response metadata, and Prometheus metrics.
The previous placeholder serving gap is closed for the current local baseline
model.

This completes the W3 registry-driven serving track. The W3 remote execution
track was completed later on 2026-07-05 through `remote-job`.

## Completed Tasks

| ID | Result |
|---|---|
| `EVM-051` | API reads `/app/artifacts/registry/vision-baseline/latest.json` in Docker Compose |
| `EVM-052` | `/ready` reports `model_loaded`, model version, stage, dataset version, registry path |
| `EVM-053` | `/predict` returns `placeholder=false` and uses the loaded artifact prediction |
| `EVM-054` | `/metrics` exposes `evm_serving_model_loaded`, `evm_serving_model_version`, `evm_serving_model_info` |
| `EVM-055` | Rollback contract documented in `docs/runbooks/registry-driven-serving.md` |

## Runtime Evidence

API service was rebuilt and recreated:

```powershell
docker compose build api
docker compose up -d api
```

`/ready` returned:

```text
status=ok
mlflow_ready=true
model_loaded=true
model_name=vision-baseline
model_stage=Production
model_version=17
dataset_version=public-vision-local-3cafd20ac032
validated_parquet_uri=s3://validated/public-vision-local/public-vision-local-3cafd20ac032/validated/validated_dataset.parquet
registry_path=/app/artifacts/registry/vision-baseline/latest.json
```

`/predict` returned:

```text
model_name=vision-baseline
model_stage=Production
model_version=17
dataset_version=public-vision-local-3cafd20ac032
prediction=normal
confidence=0.5
placeholder=false
```

`/metrics` exposed:

```text
evm_serving_model_loaded{model_name="vision-baseline",model_stage="Production"} 1.0
evm_serving_model_version{model_name="vision-baseline",model_stage="Production"} 17.0
evm_serving_model_info{dataset_version="public-vision-local-3cafd20ac032",model_name="vision-baseline",model_stage="Production",model_version="17"} 1.0
```

## Pipeline Smoke Evidence

W3 preflight after implementation:

```text
ready_for_w3_start=True
status_counts={'pass': 7, 'warn': 0, 'fail': 0}
serving gap: predict endpoint no longer has placeholder=True
```

Deployment smoke:

```text
health_status=200
ready_status=200
predict_status=200
ready_model_loaded=True
predict_placeholder=False
contract_ok=True
```

Monitoring smoke:

```text
targets_status=200
active_targets=2
healthy_targets=2
api:8000/metrics health=up
```

## Jira Sync State

Repository source state is updated in `docs/issues/issue-register.md`.

Dry-run command:

```powershell
python scripts\dev\jira_sync.py --project-root . --project-key SCRUM --source-id EVM-EPIC-05,EVM-051,EVM-052,EVM-053,EVM-054,EVM-055 --include-done --dry-run
```

Dry-run result:

```text
project_key=SCRUM
total=6
EVM-EPIC-05 status=Done
EVM-051 status=Done
EVM-052 status=Done
EVM-053 status=Done
EVM-054 status=Done
EVM-055 status=Done
```

Live Jira update completed after credentials were supplied for the active
command. The script updated and transitioned the following Jira issues to
`완료`:

```text
SCRUM-9
SCRUM-35
SCRUM-36
SCRUM-37
SCRUM-38
SCRUM-39
```

## Remote Execution Follow-Up Completion

The remote execution track was completed after the serving tranche:

- `EVM-041`: structured remote job spec.
- `EVM-042`: mac-mini ARM64 evaluation job.
- `EVM-044`: worker resource report.
- `EVM-045`: remote artifact collection.

Latest successful run:

```text
pipeline=remote-job
pipeline_run_id=remote-job-20260705T100117Z
status=success
worker_id=ruma_macmini
architecture=arm64
cpu_count=12
memory_bytes=25769803776
artifacts_collected=true
```

## Known Limitations

- The current model is still a local majority-class baseline, not a real VLM or
  multimodal model.
- The serving source of truth is local registry metadata, not MLflow Model
  Registry stage promotion.
- The API reloads the registry metadata through the local mounted artifact path;
  production-grade registry events or watch/reload controls remain future work.
