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
