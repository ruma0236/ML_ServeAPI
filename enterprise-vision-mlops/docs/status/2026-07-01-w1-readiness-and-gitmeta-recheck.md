# 2026-07-01 W1 Readiness And Git Metadata Recheck

## Scope

W1 had already been implemented early, but the current July 1 execution window
required a fresh readiness check before moving on. This pass verified that the
local infrastructure, Airflow full DAG, trace propagation, and MLflow linkage
still work from a cold-ish local desktop state.

## Readiness Findings

| Check | Result | Note |
|---|---|---|
| Docker engine | Recovered | Docker Desktop Linux engine was initially unavailable, then started successfully. |
| Compose stack | Pass | Airflow, MLflow, MinIO, API, Prometheus, Grafana started. |
| DAG import | Pass | `enterprise_vision_mlops_daily` listed in Airflow. |
| API health | Pass | `http://localhost:8000/health` returned `ok`. |
| MLflow health | Pass | `http://localhost:5000/health` returned `OK`. |
| Full DAG execution | Pass | Six W1 tasks completed successfully. |

## Issue Found

`EVM-BUG-004` was found during the W1 recheck.

Plain `docker compose up -d` can create Airflow containers without
`EVM_GIT_COMMIT` and `EVM_GIT_BRANCH`. The DAG still succeeds, but generated
`trace.json` files and MLflow params lose the code version. For enterprise-grade
lineage, this is a real reproducibility gap.

Tracking:

- GitHub: https://github.com/ruma0236/ML_ServeAPI/issues/4
- Jira: https://opop0236.atlassian.net/browse/SCRUM-55

## Fix

Added:

```text
scripts/dev/start_local_stack.ps1
```

The script:

1. resolves the repository root,
2. reads the current Git commit and branch,
3. injects them as `EVM_GIT_COMMIT` and `EVM_GIT_BRANCH`,
4. starts the compose stack,
5. recreates Airflow runtime containers so the metadata is present in task
   execution environments.

Updated:

```text
docs/runbooks/airflow-local.md
```

The runbook now recommends the script as the default local start path.

## Final Validation

Final Airflow run:

```text
w1_full_dag_final_gitmeta_20260701T134038
```

Final trace id:

```text
enterprise_vision_mlops_daily__w1_full_dag_final_gitmeta_20260701T134038
```

Task states:

```text
data_ingest     success
data_validate   success
train           success
register_model  success
deploy_check    success
monitor_check   success
```

Trace metadata verified across all six pipeline stages:

```text
git_commit: 809f83cf
git_branch: codex/mac-mini-worker
```

Downstream evidence:

| Stage | Evidence |
|---|---|
| training | MLflow run `b7d068e2f72148a1ae82b265b711a247` |
| model registry | registry version `12` |
| deployment | `/health`, `/ready`, `/predict` returned HTTP `200` |
| monitoring | healthy Prometheus targets `2` |

MLflow params verified on run `b7d068e2f72148a1ae82b265b711a247`:

```text
trace_id
airflow_dag_run_id
airflow_task_id
git_commit
git_branch
```

## Jira Sync Note

GitHub Issue creation succeeded first. Jira synchronization initially failed
because the previous Jira API token was no longer valid. After the token was
refreshed, `EVM-BUG-004` was backfilled to Jira as `SCRUM-55`, linked to
`SCRUM-6`, and transitioned to `완료`.

No Jira token value is stored in the repository.

## W1 Status

W1 is implementation-complete and revalidated as of 2026-07-01.

The remaining next step is W2 object storage and dataset platform work:

- `EVM-031`: MinIO bucket bootstrap hardening.
- `EVM-032`: object storage client module.
- `EVM-033`: public vision dataset ingest.
- `EVM-034`: validation report hardening.
- `EVM-035`: Parquet dataset generation.
- `EVM-036`: dataset version metadata.
