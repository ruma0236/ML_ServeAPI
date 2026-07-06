# Architecture Connectivity

This document defines how the current enterprise-vision MLOps system is wired
across machines, Docker services, and pipeline modules.

## Node-Level Topology

```mermaid
flowchart LR
    DEV["Windows control-plane\nCodex workspace + Git + Docker Desktop"]
    DOCKER["Local Docker network\nevm-local"]
    MAC["ruma-macmini\nmacOS ARM64 worker"]
    GH["GitHub\nruma0236/ML_ServeAPI"]
    FUTURE_LINUX["ruma-ubuntu\nfuture Linux worker"]
    FUTURE_K3S["k3s-master\nfuture Kubernetes control-plane"]

    DEV -->|"docker compose"| DOCKER
    DEV <-->|"git push / pull"| GH
    DEV -->|"Tailscale SSH\nkey-based remote exec"| MAC
    MAC <-->|"git pull"| GH
    DEV -. "future SSH / worker probe" .-> FUTURE_LINUX
    DEV -. "future k3s API / kubectl" .-> FUTURE_K3S
```

## Local Docker Service Topology

```mermaid
flowchart LR
    API["FastAPI API\n:8000"]
    MLFLOW["MLflow Tracking + Registry\n:5000"]
    POSTGRES["PostgreSQL\ncontainer:5432 host:5433"]
    MINIO["MinIO S3 API\n:9000 console:9001"]
    PROM["Prometheus\n:9090"]
    GRAFANA["Grafana\n:3000"]

    MLFLOW -->|"backend store\nSQL metadata"| POSTGRES
    MLFLOW -->|"artifact store\ns3://mlflow-artifacts"| MINIO
    API -->|"readiness check\nGET /health"| MLFLOW
    PROM -->|"scrape\nGET /metrics"| API
    GRAFANA -->|"datasource query"| PROM
```

## Current Connectivity Status

| Link | Protocol | Current Status | Notes |
|---|---|---|---|
| Windows -> Docker Compose | Docker Engine API | healthy | `docker compose ps` shows API, MLflow, Postgres healthy and other services running. |
| API -> MLflow | HTTP inside `evm-local` | healthy | `/ready` returns `mlflow_ready=true` through `http://mlflow:5000`. |
| MLflow -> Postgres | PostgreSQL inside `evm-local` | healthy | Postgres healthcheck and `pg_isready` pass. |
| MLflow -> MinIO | S3-compatible HTTP inside `evm-local` | healthy | MinIO live/ready endpoint passes; MLflow server is healthy. |
| Prometheus -> API | HTTP inside `evm-local` | healthy | Prometheus target `api:8000/metrics` is `up`. |
| Grafana -> Prometheus | HTTP inside `evm-local` | healthy | Grafana `/api/health` returns database `ok`. |
| Windows -> mac-mini | Tailscale SSH | healthy | SSH remote exec returns `ruma`, `rumaui-Macmini.local`, `arm64`. |
| Windows -> Tailscale local status | Local Tailscale API | healthy | `remote-inventory` reads `tailscale status --json` in the current shell. |
| mac-mini -> GitHub | HTTPS Git | healthy | mac-mini clone tracks `codex/mac-mini-worker`. |
| Windows -> ruma-ubuntu | Tailscale SSH | unavailable | Candidate node exists but SSH port is not reachable in current probe. |
| Windows -> k3s-master | Tailscale/Kubernetes | unavailable | Candidate control-plane is not active in current probe. |

## Pipeline Module Topology

```mermaid
flowchart LR
    INGEST["data_ingestion"]
    VALIDATE["data_validation"]
    TRAIN["training"]
    REGISTRY["model_registry"]
    DEPLOY["deployment"]
    MONITOR["monitoring"]
    WORKERS["remote_workers"]
    REMOTE_JOB["remote_job"]

    INGEST -->|"raw_manifest.jsonl"| VALIDATE
    VALIDATE -->|"validated_manifest.jsonl\nvalidation_report.json"| TRAIN
    TRAIN -->|"model.json\nMLflow run metrics"| REGISTRY
    REGISTRY -->|"latest.json\nversioned registry metadata"| DEPLOY
    DEPLOY -->|"API health/ready/predict result"| MONITOR
    WORKERS -->|"remote inventory report"| MONITOR
    WORKERS -->|"reachable worker metadata"| REMOTE_JOB
    REMOTE_JOB -->|"job spec, resource report,\ncollected remote artifacts"| MONITOR
```

## Data Exchange Contract

| Producer | Consumer | Medium | Payload | Current Scope |
|---|---|---|---|---|
| `data_ingestion` | `data_validation` | Local manifest file | `data/raw/raw_manifest.jsonl` with `id`, `image_uri`, `label`, `width`, `height`, `source`, `ingested_at` | Synthetic seed manifest. |
| `data_validation` | `training` | Local manifest/report files | `data/validated/validated_manifest.jsonl`, `data/validated/validation_report.json` | Schema, extension, label, and dimension checks. |
| `training` | MLflow | HTTP REST | experiment, run, params, metrics | Logs baseline run when MLflow is healthy. |
| `training` | `model_registry` | Local artifact | `artifacts/models/vision-baseline/model.json` | Majority-class baseline model metadata. |
| `model_registry` | API / `deployment` | Local registry metadata | `artifacts/registry/vision-baseline/vN.json`, `latest.json` | API loads promoted local registry metadata at runtime. |
| `deployment` | API | HTTP | `GET /health`, `GET /ready`, `POST /predict` | Smoke validates `model_loaded=true` and `placeholder=false`. |
| API | Prometheus | HTTP metrics scrape | `/metrics` Prometheus exposition format | Request counters, latency histogram, loaded model/version metadata. |
| Prometheus | Grafana | HTTP datasource | target health and metric series | Dashboard-ready metrics source. |
| Windows control-plane | mac-mini | SSH command/result | branch sync, smoke commands, stdout/stderr, exit code | ARM64 worker validation and future edge/MPS experiments. |
| `remote_job` | mac-mini | SSH + SCP | structured job spec, uploaded eval script, resource report, remote report/log/summary | W3 remote execution and artifact reconciliation. |
| mac-mini | GitHub | Git HTTPS | branch checkout/pull for `codex/mac-mini-worker` | Keeps worker-specific code isolated. |

## Current Gaps

- MLflow is used for run tracking, but the local file registry is still the
  serving metadata source; MLflow Model Registry promotion remains future work.
- Lineage is still local metadata plus MLflow params; OpenLineage/catalog
  emission remains future work.
- Linux and Kubernetes candidate workers are still future targets; W3 completed
  the mac-mini remote execution path only.
