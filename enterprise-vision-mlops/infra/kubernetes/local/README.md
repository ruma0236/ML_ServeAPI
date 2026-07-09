# Local Kubernetes Scaffold

Issue: `EVM-222` / `SCRUM-100`

This scaffold is the first local Kubernetes boundary for Enterprise Vision
MLOps. It is intended for Docker Desktop Kubernetes, k3s, or kind-style local
verification. It does not replace the current Docker Compose runtime yet.

## Included Workloads

- `evm-postgres`: MLflow backend database
- `evm-minio`: S3-compatible object store
- `evm-minio-create-buckets`: bucket bootstrap Job
- `evm-mlflow`: MLflow tracking and registry service
- `evm-api`: FastAPI model serving and metrics endpoint
- `evm-prometheus`: Prometheus metrics scraping
- `evm-grafana`: Grafana dashboard UI
- `evm-airflow-control-contract`: external Airflow control boundary for W7 UI
- `evm-domain-pack-check`: domain policy validation Job using VisA config
- `evm-curation-workflow`: VisA curation/HITL/eval manifest Job
- `evm-lakehouse-probe`: VisA Parquet/lakehouse probe Job

## Local Images

Build local images before applying the scaffold:

```powershell
docker build -t enterprise-vision-mlops-api:local -f apps/api/Dockerfile .
docker build -t enterprise-vision-mlops-mlflow:local -f infra/docker/mlflow/Dockerfile .
docker build -t enterprise-vision-mlops-pipeline:local -f infra/docker/pipeline/Dockerfile .
```

For kind, load the images into the cluster after building:

```powershell
kind load docker-image enterprise-vision-mlops-api:local
kind load docker-image enterprise-vision-mlops-mlflow:local
kind load docker-image enterprise-vision-mlops-pipeline:local
```

## F-Drive Storage Boundary

Large data remains outside the repo:

```text
F:/EnterpriseMLOps_Data/enterprise-vision-mlops
```

The scaffold maps this to Docker Desktop's Linux VM path:

```text
/run/desktop/mnt/host/f/EnterpriseMLOps_Data/enterprise-vision-mlops
```

If the local cluster is not Docker Desktop Kubernetes, adjust
`storage.yaml` before applying.

## Render And Apply

Render:

```powershell
kubectl kustomize infra/kubernetes/local
```

Apply:

```powershell
kubectl apply -k infra/kubernetes/local
```

Check:

```powershell
kubectl get pods -n evm-platform
kubectl get jobs -n evm-pipelines
kubectl logs -n evm-pipelines job/evm-domain-pack-check
kubectl logs -n evm-pipelines job/evm-curation-workflow
kubectl logs -n evm-pipelines job/evm-lakehouse-probe
```

Port-forward examples:

```powershell
kubectl port-forward -n evm-platform svc/evm-api 8000:8000
kubectl port-forward -n evm-platform svc/evm-mlflow 5000:5000
kubectl port-forward -n evm-platform svc/evm-minio 9000:9000
kubectl port-forward -n evm-platform svc/evm-prometheus 9090:9090
kubectl port-forward -n evm-platform svc/evm-grafana 3000:3000
```

## Guardrails

- `secrets.dev.yaml` is local-development only.
- Production credentials should move to a real secret manager or
  ExternalSecret-style flow.
- Airflow is currently represented as an external Compose contract, not as
  in-cluster webserver/scheduler resources.
- The Mac mini M4 Pro remains an external remote evaluator for this scaffold.
- GPU/VLM workloads are modeled by `runtime-resource-map.json` but are not yet
  deployed by this overlay.
