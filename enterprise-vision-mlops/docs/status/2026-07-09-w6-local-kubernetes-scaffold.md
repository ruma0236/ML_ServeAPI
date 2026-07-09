# 2026-07-09 W6 Local Kubernetes Scaffold

Issue: `EVM-222` / Jira `SCRUM-100`

## Result

`EVM-222` is complete as a local Kubernetes scaffold. The overlay is intended
for Docker Desktop Kubernetes, k3s, or kind-style local verification and does
not replace the current Docker Compose runtime yet.

## Files Added

- `infra/docker/pipeline/Dockerfile`
- `infra/kubernetes/local/README.md`
- `infra/kubernetes/local/kustomization.yaml`
- `infra/kubernetes/local/namespace.yaml`
- `infra/kubernetes/local/configmaps.yaml`
- `infra/kubernetes/local/secrets.dev.yaml`
- `infra/kubernetes/local/storage.yaml`
- `infra/kubernetes/local/airflow-external.yaml`
- `infra/kubernetes/local/postgres.yaml`
- `infra/kubernetes/local/minio.yaml`
- `infra/kubernetes/local/mlflow.yaml`
- `infra/kubernetes/local/api.yaml`
- `infra/kubernetes/local/prometheus-grafana.yaml`
- `infra/kubernetes/local/pipeline-job.yaml`

## Workloads Covered

The scaffold includes local manifests for:

- API: `evm-api`
- MLflow: `evm-mlflow`
- MinIO: `evm-minio`
- MinIO bucket bootstrap: `evm-minio-create-buckets`
- Prometheus: `evm-prometheus`
- Grafana: `evm-grafana`
- MLflow Postgres backend: `evm-postgres`
- External Airflow control contract: `evm-airflow-control-contract`
- Domain policy validation Job: `evm-domain-pack-check`
- VisA curation workflow Job: `evm-curation-workflow`
- VisA lakehouse probe Job: `evm-lakehouse-probe`

## Storage Boundary

The overlay keeps the large-data path on the F drive by modeling the local host
path:

```text
/run/desktop/mnt/host/f/EnterpriseMLOps_Data/enterprise-vision-mlops
```

This maps to:

```text
F:/EnterpriseMLOps_Data/enterprise-vision-mlops
```

If the local cluster is not Docker Desktop Kubernetes, `storage.yaml` must be
adjusted before applying.

## Verification

Render command:

```powershell
kubectl kustomize infra/kubernetes/local
```

Observed offline render result:

- rendered resources: `34`
- missing required resources: `[]`
- required resources checked:
  - `evm-api`
  - `evm-mlflow`
  - `evm-minio`
  - `evm-prometheus`
  - `evm-grafana`
  - `evm-domain-pack-check`
  - `evm-curation-workflow`
  - `evm-lakehouse-probe`
  - `evm-airflow-control-contract`

Command used for the structured check:

```powershell
kubectl kustomize infra/kubernetes/local |
  Set-Content -Encoding utf8 $env:TEMP\evm-k8s-local-rendered.yaml
```

Then the rendered output was checked for required resource names and resource
count.

## Current Local Cluster Caveat

`kubectl apply --dry-run=client` could not complete because no current
Kubernetes context is configured in this Windows environment:

```text
error: current-context is not set
```

The scaffold is therefore verified as an offline rendered overlay. Actual
cluster apply and pod readiness remain part of the next Kubernetes real
execution proof
step.

## Handoff

`EVM-223` can now define the Control Panel metadata/control API around the
resource map and local Kubernetes resource names introduced here.
