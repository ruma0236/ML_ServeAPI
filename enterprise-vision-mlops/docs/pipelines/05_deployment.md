# Deployment Pipeline

## Role

Verifies that the serving API is reachable, ready, and able to process a sample inference request.

## Current Local MVP Scope

- Calls `/health`.
- Calls `/ready`.
- Calls `/predict` with a sample image URI.
- Verifies the W3 registry-driven serving contract:
  - `/ready` returns `model_loaded=true`,
  - `/predict` returns `placeholder=false`.
- Writes deployment smoke-test summary.

## Inputs

- Running FastAPI service at `http://localhost:8000`
- Promoted model metadata from model registry

## Outputs

- `artifacts/reports/deployment.md`
- `artifacts/runs/deployment/*/summary.json`

## Command

```bash
python scripts/run_pipeline.py deploy-check --config configs/local.toml
```

## Extension Plan

- Add container image tag promotion.
- Add Helm/Kustomize manifest rendering.
- Add KServe/Triton deployment checks.
- Add canary and rollback validation.

## Update Log

- 2026-06-18: Added local API smoke-test pipeline.
- 2026-06-18: Verified `/health`, `/ready`, and `/predict` smoke checks.
- 2026-07-05: Strengthened deployment smoke to fail when registry-driven serving is not loaded.
