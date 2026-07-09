# Kubernetes Runtime Resource Map

Issue: `EVM-221` / `SCRUM-99`

This document maps the current Docker Compose runtime to Kubernetes resources.
It is the handoff between the W5 local lifecycle proof and the W6/W7
Kubernetes-aware Control Panel work.

The structured source for automation and future Control Panel metadata is:

- `infra/kubernetes/runtime-resource-map.json`

## Runtime Goal

The near-term W6 goal is not a full production Kubernetes cutover. The goal is
to make the runtime boundary explicit enough that local k3s/kind manifests,
resource observability, and Control Panel command intents can be built without
guessing from `docker-compose.yml`.

## Namespace Boundary

| Namespace | Purpose |
|---|---|
| `evm-platform` | metadata databases, Airflow, MLflow, MinIO, API, Prometheus, Grafana |
| `evm-pipelines` | batch pipeline Jobs and future GPU/VLM training or serving workloads |

This split keeps the control-plane services stable while pipeline workloads can
be created, retried, cancelled, or scheduled independently.

## Node Pool Interpretation

| Node pool | Role | Initial placement |
|---|---|---|
| `local-cpu` | default local control-plane and CPU pipeline execution | Postgres, Airflow, MinIO, MLflow, API, Prometheus, Grafana, CPU jobs |
| `windows-rtx` | primary local GPU trainer and VLM serving candidate | future deep image training, VLM endpoint, GPU batch inference |
| `mac-mini-remote` | ARM64 evaluator outside the first cluster boundary | remote eval smoke, cross-platform validation |

The Mac mini remains a remote evaluator for now. It should not be modeled as a
Kubernetes worker until networking, credentials, and ARM64 image compatibility
are intentionally handled.

## Persistent Storage

Large data and artifact storage stay on the F drive:

- Host data root: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops`
- Container data mount: `/mnt/evm-data`
- MinIO root: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/minio`
- Artifact root: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts`

The Kubernetes scaffold should use local-path style PVCs for the first local
proof. Any TB-scale dataset or model artifact path must bind back to the F-drive
root rather than the repo directory.

| Claim | Current source | Capacity hint | Notes |
|---|---|---:|---|
| `postgres-data` | Compose named volume | `20Gi` | MLflow backend DB |
| `airflow-postgres-data` | Compose named volume | `20Gi` | Airflow metadata DB |
| `minio-data` | `EVM_HOST_MINIO_ROOT` | `1Ti+` | object data must stay on F drive |
| `evm-large-data` | `EVM_HOST_DATA_ROOT` | `1Ti+` | raw, processed, validated, artifacts, lifecycle files |
| `prometheus-data` | Compose named volume | `50Gi` | local metrics retention |
| `grafana-data` | Compose named volume | `10Gi` | dashboard state |
| `airflow-logs` | Compose named volume | `50Gi` | shared scheduler/webserver logs |

## Compose To Kubernetes Mapping

| Compose service | Kubernetes resources | Namespace | Placement | Storage | Readiness |
|---|---|---|---|---|---|
| `postgres` | StatefulSet, Service, PVC, Secret | `evm-platform` | `local-cpu` | `postgres-data` | `pg_isready` |
| `airflow-postgres` | StatefulSet, Service, PVC, Secret | `evm-platform` | `local-cpu` | `airflow-postgres-data` | `pg_isready` |
| `airflow-init` | Job, ConfigMap, Secret | `evm-platform` | `local-cpu` | none | job succeeds before Airflow starts |
| `airflow-webserver` | Deployment, Service, ConfigMap, Secret, PVC | `evm-platform` | `local-cpu` | `evm-large-data`, `airflow-logs` | `GET /health` |
| `airflow-scheduler` | Deployment, ConfigMap, Secret, PVC | `evm-platform` | `local-cpu` | `evm-large-data`, `airflow-logs` | `airflow jobs check` |
| `minio` | StatefulSet, Service, PVC, Secret | `evm-platform` | `local-cpu`, F-drive storage | `minio-data` | `/minio/health/ready` |
| `minio-create-buckets` | Job, Secret | `evm-platform` | `local-cpu` | none | buckets exist |
| `mlflow` | Deployment, Service, Secret, ConfigMap | `evm-platform` | `local-cpu` | MinIO artifacts | `GET /health` |
| `api` | Deployment, Service, ConfigMap, PVC | `evm-platform` | `local-cpu`, future `windows-rtx` for VLM serving | `evm-large-data` read-only | `GET /health`, `GET /ready` |
| `prometheus` | Deployment, Service, ConfigMap, PVC | `evm-platform` | `local-cpu` | `prometheus-data` | `/-/ready` |
| `grafana` | Deployment, Service, Secret, ConfigMap, PVC | `evm-platform` | `local-cpu` | `grafana-data` | `/api/health` |

## Pipeline Workloads

Airflow still owns scheduling intent in the current platform. Kubernetes should
provide repeatable execution targets:

| Workload | Kubernetes resources | Namespace | Placement | Examples |
|---|---|---|---|---|
| `evm-pipeline-job` | Job, ConfigMap, Secret, PVC | `evm-pipelines` | `local-cpu` | `dataset-intake-audit`, `data-validate`, `image-quality`, `train`, `register-model` |
| `evm-gpu-training-job` | Job, Secret, PVC | `evm-pipelines` | `windows-rtx`, `nvidia.com/gpu: 1` | future deep image or VLM training |
| `evm-vlm-serving` | Deployment, Service, HPA, PVC | `evm-pipelines` | `windows-rtx`, `nvidia.com/gpu: 1` | future Qwen-VL, Triton, KServe, vLLM, or Ray Serve endpoint |

## Control Panel Resource Fields

The Control Panel should not scrape arbitrary YAML for its first version. It
should consume a normalized resource shape derived from this map:

- namespace
- resource kind
- resource name
- owner issue
- status
- readiness
- restarts
- CPU request
- memory request
- GPU request
- storage claim
- storage root
- node pool
- last transition time
- dependencies
- control actions

This gives the UI enough structure to render topology, capacity, health,
dependency order, and safe action buttons without hard-coding each service.

## Initial Control Actions

The first Control Panel actions should be explicit command intents, not direct
silent mutations:

| Action | Targets | Guardrail |
|---|---|---|
| `restart_deployment` | API, Airflow, MLflow, Prometheus, Grafana | requires confirmation |
| `run_pipeline_job` | `evm-pipeline-job` | requires owner, config, resource profile |
| `scale_deployment` | API, future VLM serving | requires dry-run and confirmation |

The command intent schema is part of `EVM-223`.

## EVM-221 Exit Criteria

- Compose services are mapped to Kubernetes resources.
- CPU/GPU/storage placement has an explicit initial policy.
- F-drive persistence boundaries are preserved.
- Future Control Panel resource fields are identified.
- The map exists in both human-readable and machine-readable forms.
