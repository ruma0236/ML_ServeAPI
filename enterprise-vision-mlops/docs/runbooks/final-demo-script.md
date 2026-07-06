# Final Demo Script

## Preconditions

- Docker Desktop is running.
- F-drive storage root exists: `F:\EnterpriseMLOps_Data\enterprise-vision-mlops`.
- `.env` uses the F-drive storage variables from `.env.example`.

## Start Stack

```powershell
docker compose up -d --build
docker compose ps
```

## Run W4 VLM Path

```powershell
make w4-vlm
```

Equivalent explicit commands:

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
```

## Serving Smoke

```powershell
python scripts\run_pipeline.py train --config configs\local.toml
python scripts\run_pipeline.py register-model --config configs\local.toml
python scripts\run_pipeline.py deploy-check --config configs\local.toml
python scripts\run_pipeline.py monitor-check --config configs\local.toml
```

## Review Evidence

- Airflow: `http://localhost:8080`
- MLflow: `http://localhost:5000`
- MinIO: `http://localhost:9001`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000`
- API readiness: `http://localhost:8000/ready`
- API metrics: `http://localhost:8000/metrics`

Expected W4 evidence:

- image quality fatal errors: `0`
- VLM schema valid rate: `1.0`
- bad prompt candidate: blocked by regression gate
- audit events: request, response, promotion gate, failure scenario events
- VLM metrics visible from API `/metrics` after `vlm-observability`
