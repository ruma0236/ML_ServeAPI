# Runbook: Local Stack Failure

## Symptoms

- API is not reachable on `http://localhost:8000`.
- MLflow is not reachable on `http://localhost:5000`.
- Prometheus has no `evm-api` target.
- Grafana dashboard is empty.

## Triage

```bash
docker compose ps
docker compose logs api
docker compose logs mlflow
docker compose logs postgres
docker compose logs minio
```

## Common Fixes

1. If MLflow fails to connect to PostgreSQL, restart the stack after PostgreSQL is healthy.
2. If MLflow cannot write artifacts, confirm the `mlflow-artifacts` bucket exists in MinIO.
3. If Prometheus target is down, confirm the API container is healthy and `/metrics` responds.
4. If Grafana is empty, generate traffic through `/predict` and wait for the next Prometheus scrape.

## Reset

Use this only when local volumes can be discarded:

```bash
docker compose down -v
docker compose up -d --build
```
