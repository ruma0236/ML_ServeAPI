# Pipeline Overview

## Purpose

This project is organized as one modular MLOps platform. Each pipeline owns a clear lifecycle responsibility, while shared concerns live in `src/evm/core`.

## Pipeline Map

```mermaid
flowchart LR
    A["data_ingestion"] --> B["data_validation"]
    B --> C["training"]
    C --> D["model_registry"]
    D --> E["deployment"]
    E --> F["monitoring"]
    F --> G["remote_workers"]
```

## Code Ownership

| Pipeline | Code | Document |
|---|---|---|
| Data ingestion | `src/evm/pipelines/data_ingestion` | `docs/pipelines/01_data_ingestion.md` |
| Data validation | `src/evm/pipelines/data_validation` | `docs/pipelines/02_data_validation.md` |
| Training | `src/evm/pipelines/training` | `docs/pipelines/03_training.md` |
| Model registry | `src/evm/pipelines/model_registry` | `docs/pipelines/04_model_registry.md` |
| Deployment | `src/evm/pipelines/deployment` | `docs/pipelines/05_deployment.md` |
| Monitoring | `src/evm/pipelines/monitoring` | `docs/pipelines/06_monitoring.md` |
| Remote workers | `src/evm/pipelines/remote_workers` | `docs/pipelines/07_remote_workers.md` |

## Shared Modules

- `src/evm/core/config.py`: TOML config loading and project path resolution.
- `src/evm/core/pipeline.py`: run context, JSON/JSONL helpers, markdown report writing.
- `src/evm/core/http.py`: dependency-free HTTP JSON client.
- `src/evm/core/mlflow_client.py`: minimal MLflow REST integration.

## Update Log

- 2026-06-18: Added modular pipeline layout and shared core package.
- 2026-06-18: Verified full local MVP sequence from ingestion through monitoring.
- 2026-06-18: Added remote worker inventory layer for Tailscale-connected machines.
