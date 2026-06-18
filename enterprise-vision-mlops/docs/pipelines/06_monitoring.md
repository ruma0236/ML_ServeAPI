# Monitoring Pipeline

## Role

Verifies that observability is attached to the serving layer.

## Current Local MVP Scope

- Queries Prometheus active targets.
- Confirms the API metrics target is healthy.
- Writes monitoring summary.

## Inputs

- Prometheus at `http://localhost:9090`
- API `/metrics` endpoint

## Outputs

- `artifacts/reports/monitoring.md`
- `artifacts/runs/monitoring/*/summary.json`

## Command

```bash
python scripts/run_pipeline.py monitor-check --config configs/local.toml
```

## Extension Plan

- Add alert rules.
- Add SLO definitions for latency and error rate.
- Add drift monitoring.
- Add NVIDIA DCGM exporter for GPU metrics.

## Update Log

- 2026-06-18: Added Prometheus target health validation.
- 2026-06-18: Verified Prometheus target health with 2 healthy targets.
