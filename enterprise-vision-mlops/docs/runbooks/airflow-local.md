# Airflow Local Runbook

작성일: 2026-06-21

## Purpose

`EVM-021`, `EVM-022`, `EVM-023-A`, `EVM-023-B`의 Airflow foundation 실행
절차를 정의한다.

## Services

Airflow local control-plane은 Docker Compose에 다음 service로 구성된다.

| Service | Role |
|---|---|
| `airflow-postgres` | Airflow dedicated metadata DB |
| `airflow-init` | metadata DB migration and admin user bootstrap |
| `airflow-webserver` | Airflow UI on `http://localhost:8080` |
| `airflow-scheduler` | DAG scheduling and task execution |

Metadata DB는 MLflow backend DB와 분리된 `airflow-postgres`를 사용한다. MLflow와
Airflow는 둘 다 Alembic migration을 사용하므로 같은 database를 공유하지 않는다.

## Credentials

기본 local 계정:

```text
username: admin
password: admin
```

환경 변수로 변경할 수 있다.

```powershell
$env:AIRFLOW_ADMIN_USERNAME="admin"
$env:AIRFLOW_ADMIN_PASSWORD="<password>"
```

## Start

Recommended local stack start:

```powershell
.\scripts\dev\start_local_stack.ps1
```

The script injects the current Git commit and branch into Airflow containers via
`EVM_GIT_COMMIT` and `EVM_GIT_BRANCH`, then recreates Airflow runtime containers
so W1 `trace.json` and MLflow params can record the executed code version.

W1 traceability 검증부터는 Airflow task가 생성하는 `trace.json`에 code version도
남기기 위해 compose 실행 전에 다음 값을 설정한다.

```powershell
$env:EVM_GIT_COMMIT = git rev-parse --short HEAD
$env:EVM_GIT_BRANCH = git branch --show-current
```

```powershell
docker compose up -d airflow-postgres airflow-init airflow-webserver airflow-scheduler
```

전체 local stack과 함께 실행:

```powershell
docker compose up -d --build
```

## Verify

```powershell
docker compose ps airflow-postgres airflow-webserver airflow-scheduler
docker compose exec airflow-scheduler airflow dags list
docker compose exec airflow-scheduler airflow tasks list enterprise_vision_mlops_daily --tree
docker compose exec airflow-scheduler airflow dags list-import-errors
docker compose logs airflow-webserver
docker compose logs airflow-scheduler
```

Airflow UI:

```text
http://localhost:8080
```

Expected DAG:

```text
enterprise_vision_mlops_daily
```

Current W2 task graph:

```text
object_store_bootstrap -> data_ingest -> data_validate -> train -> register_model -> deploy_check -> monitor_check
```

## Manual DAG Trigger

Airflow UI에서 `enterprise_vision_mlops_daily`를 선택한 뒤 manual run을 실행한다.

CLI:

```powershell
$runId = "evm_w0_smoke_" + (Get-Date -Format "yyyyMMddTHHmmss")
docker compose exec airflow-scheduler airflow dags trigger -r $runId enterprise_vision_mlops_daily
docker compose exec airflow-scheduler airflow dags list-runs -d enterprise_vision_mlops_daily
docker compose exec airflow-scheduler airflow tasks states-for-dag-run enterprise_vision_mlops_daily $runId
```

Expected task states:

```text
object_store_bootstrap success
data_ingest    success
data_validate  success
train          success
register_model success
deploy_check   success
monitor_check  success
```

Trace output:

```text
artifacts/runs/data_ingestion/<run_id>/trace.json
artifacts/runs/data_validation/<run_id>/trace.json
```

Both W0 tasks in a single DAG run should share the same `trace_id` and
`airflow_dag_run_id`.

For W2 full DAG runs, all seven tasks should share the same `trace_id`:

```text
object_storage_bootstrap.trace_id
data_ingestion.trace_id
data_validation.trace_id
training.trace_id
model_registry.trace_id
deployment.trace_id
monitoring.trace_id
```

## Retry, Timeout, And Log Policy

The DAG uses explicit task-level defaults in
`orchestration/airflow/dags/enterprise_vision_mlops_daily.py`.

| Policy | Default | Override |
|---|---:|---|
| task retries | `1` | `EVM_AIRFLOW_TASK_RETRIES` |
| retry delay | `2` minutes | `EVM_AIRFLOW_RETRY_DELAY_MINUTES` |
| task execution timeout | `10` minutes | `EVM_AIRFLOW_TASK_TIMEOUT_MINUTES` |
| DAG run timeout | `45` minutes | `EVM_AIRFLOW_DAG_TIMEOUT_MINUTES` |
| max active DAG runs | `1` | code-level DAG setting |

Task command runs with:

```bash
set -euo pipefail
```

This makes shell failures, unset variables, and failed command pipelines fail the
Airflow task instead of being silently ignored.

Logs are available in Airflow's log volume:

```powershell
docker compose exec airflow-scheduler sh -lc "find /opt/airflow/logs -type f | sort | tail -20"
```

## Expected Outputs

`data_ingest`:

```text
data/raw/raw_manifest.jsonl
artifacts/reports/data_ingestion.md
artifacts/runs/data_ingestion/<run_id>/summary.json
```

`data_validate`:

```text
data/validated/validated_manifest.jsonl
data/validated/validation_report.json
artifacts/reports/data_validation.md
artifacts/runs/data_validation/<run_id>/summary.json
```

Task logs are written inside the Airflow logs volume:

```powershell
docker compose exec airflow-scheduler sh -lc "find /opt/airflow/logs -type f | sort | tail -20"
```

## Troubleshooting

Airflow webserver가 뜨지 않는 경우:

```powershell
docker compose logs airflow-init
docker compose logs airflow-postgres
docker compose logs airflow-webserver
```

Scheduler health가 실패하는 경우:

```powershell
docker compose logs airflow-scheduler
docker compose exec airflow-scheduler airflow dags list
```

DAG import 오류가 나는 경우:

```powershell
docker compose exec airflow-scheduler python -m py_compile /opt/airflow/dags/enterprise_vision_mlops_daily.py
```

DagRun이 `success`인데 task instance가 비어 있는 경우:

```powershell
docker compose exec airflow-scheduler airflow tasks states-for-dag-run enterprise_vision_mlops_daily <run_id>
```

원인은 보통 DAG 또는 task `start_date`가 manual run logical date보다 미래인 경우다.
현재 W0 DAG는 local smoke 검증을 위해 `2026-06-01`부터 실행 가능하게 둔다.

Airflow DB migration에서 `No such revision or branch` 오류가 나는 경우:

- Airflow와 MLflow가 같은 Postgres database를 공유하지 않는지 확인한다.
- Airflow는 dedicated `airflow-postgres` service와 `AIRFLOW_POSTGRES_DB=airflow`를 사용한다.

## Extension Plan

W1에서는 동일 DAG에 다음 task를 추가한다.

```text
train -> register_model -> deploy_check -> monitor_check
```
