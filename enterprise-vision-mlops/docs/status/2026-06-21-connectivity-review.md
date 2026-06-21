# 2026-06-21 Connectivity Review

## Summary

The current system is connected well enough for a local control-plane MVP plus a
mac-mini heterogeneous worker branch.

The critical paths are healthy:

- Local Docker services are running.
- API readiness confirms internal API-to-MLflow communication.
- MLflow is healthy with Postgres and MinIO dependencies available.
- Prometheus scrapes API metrics successfully.
- Grafana health endpoint is healthy.
- Windows control-plane can execute commands on `ruma-macmini` over SSH.
- mac-mini tracks `codex/mac-mini-worker`.

## Verification Commands

```bash
docker compose ps
python -m compileall src scripts
python scripts/run_pipeline.py data-ingest --config configs/local.toml
python scripts/run_pipeline.py data-validate --config configs/local.toml
python scripts/run_pipeline.py train --config configs/local.toml
python scripts/run_pipeline.py register-model --config configs/local.toml
python scripts/run_pipeline.py deploy-check --config configs/local.toml
python scripts/run_pipeline.py monitor-check --config configs/local.toml
python scripts/run_pipeline.py remote-inventory --config configs/local.toml
```

## Results

| Check | Result |
|---|---|
| Docker Compose services | API, MLflow, Postgres healthy; MinIO, Prometheus, Grafana running |
| API `/health` | `200`, status `ok` |
| API `/ready` | `200`, `mlflow_ready=true` |
| API `/predict` | `200`, placeholder response returned |
| API `/metrics` | Prometheus format exposed through `curl.exe` |
| MLflow `/health` | `OK` |
| Postgres `pg_isready` | accepting connections |
| MinIO readiness | `200` |
| Prometheus health | healthy |
| Prometheus targets | 2 active, 2 healthy |
| Grafana `/api/health` | database `ok` |
| Pipeline `data-ingest` | 8 records |
| Pipeline `data-validate` | 8 valid, 0 invalid |
| Pipeline `train` | MLflow run logged |
| Pipeline `register-model` | local registry version created |
| Pipeline `deploy-check` | health, ready, predict passed |
| Pipeline `monitor-check` | API and Prometheus targets up |
| Pipeline `remote-inventory` | 1 reachable worker, mac-mini remote exec ready |

## Remote Worker Finding

Tailscale CLI is installed on Windows, but the current shell cannot access the
local tailscaled pipe. The inventory now records this separately:

- `tailnet_status_available=false`
- `tailnet_status_error=tailscale_status_failed`
- `ruma-macmini.connectivity_status=remote_exec_ready`
- `reachable_workers=1`

This means the mac-mini connection is operational for SSH-based development, but
the Tailscale status API is not available from the current control-plane shell.

## Data Exchange Reviewed

| Path | Data |
|---|---|
| Ingest -> Validate | raw image manifest records |
| Validate -> Train | validated manifest and validation report |
| Train -> MLflow | params, metrics, run status |
| Train -> Registry | local `model.json` artifact |
| Registry -> Deployment | versioned `latest.json` model metadata |
| Deployment -> API | health, readiness, prediction request/response |
| API -> Prometheus | metrics exposition |
| Prometheus -> Grafana | metric series and target health |
| Windows -> mac-mini | Git branch state, smoke commands, stdout/stderr, exit code |

## Decision

Keep `codex/mac-mini-worker` as the branch for mac-mini-only automation. Merge
only generic remote-worker abstractions back into `codex/local-infra-mvp` after a
Linux worker is available for comparison.
