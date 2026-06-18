# 2026-06-18 Local Infra Status

## Completed

- Docker Compose local stack created.
- PostgreSQL, MinIO, MLflow, FastAPI, Prometheus, and Grafana services are wired.
- API exposes `/health`, `/ready`, `/predict`, and `/metrics`.
- Prometheus scrapes API metrics.
- Grafana datasource and dashboard are provisioned.
- Modular pipeline code layout was added under `src/evm`.
- Full local MVP pipeline sequence was executed successfully:
  - `data-ingest`
  - `data-validate`
  - `train`
  - `register-model`
  - `deploy-check`
  - `monitor-check`
- Tailscale remote worker candidates were identified:
  - `ruma-macmini`: online, low-latency Tailscale path, SSH port open
  - `ruma-ubuntu`: online over Tailscale, SSH refused during initial probe
  - `k3s-master`: known tailnet node, currently offline
- mac-mini remote development bootstrap completed:
  - `uv` installed under user home
  - Python 3.11.15 installed
  - GitHub branch cloned
  - `compileall`, `data-ingest`, and `data-validate` executed successfully

## Verification Snapshot

| Check | Result |
|---|---|
| Docker Compose services | API, MLflow, Postgres healthy; MinIO, Prometheus, Grafana running |
| API health | `200 OK` |
| API readiness | `MLflow ready: true` |
| Training | Baseline model artifact generated |
| MLflow | Training metric logged |
| Registry | Local model version record generated |
| Deployment | `/health`, `/ready`, `/predict` passed |
| Monitoring | Prometheus targets healthy: `2/2` |
| mac-mini network | Tailscale reachable, SSH port open |
| mac-mini execution | Python 3.11 remote pipeline smoke checks passed |
| Static check | `python -m compileall src scripts` passed |
| Compose config | `docker compose config --quiet` passed |

## Current System Boundary

This is still a local MVP. The serving API uses placeholder inference until the training and registry pipelines are connected to a real model artifact loaded by the API.

## Next Immediate Work

1. Replace synthetic manifest with a real public dataset ingest step.
2. Add object storage writes for raw and validated data.
3. Add baseline image model training.
4. Promote the MLflow model registry as the source of truth.
5. Add Kafka/Redpanda and Spark/Parquet data pipeline.
6. Enable Docker/Colima on mac-mini and add ARM64 image build validation.
