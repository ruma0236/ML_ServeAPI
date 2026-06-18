# ADR 0001: Start With a Local MLOps Stack

## Status

Accepted

## Context

The project must demonstrate enterprise MLOps capabilities under a compressed timeline. Starting with Kubernetes or cloud infrastructure first would slow feedback loops and make it harder to prove the full ML lifecycle.

## Decision

Build a local Docker Compose stack first:

- MinIO
- PostgreSQL
- MLflow
- FastAPI
- Prometheus
- Grafana

## Consequences

This gives a reproducible local baseline for demos and later migration to Kubernetes. The same service boundaries can map to Helm charts, KServe/Triton serving, and managed cloud services in later milestones.
