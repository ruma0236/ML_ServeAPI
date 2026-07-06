# Enterprise Multimodal MLOps Target Roadmap

Created: 2026-07-03

## Purpose

The project target is not only a local vision pipeline. The target is an
enterprise-grade MLOps platform that can run image, vision-language, and
multimodal model workflows with production-style data, training, registry,
serving, observability, CI/CD, and governance controls.

The current W0-W5 plan remains the foundation. This document defines the
larger target architecture and the backlog direction after the local platform
is stable.

## 2026-07-05 VLM-First July MVP Reset

The July 31 target is now a VLM-first manufacturing visual inspection AI
infrastructure MVP. The platform should be described as an enterprise-style AI
infrastructure reliability lab for manufacturing inspection workloads, not as a
generic VLM demo application.

P0 for July:

- use a real open industrial anomaly dataset, with VisA as the recommended
  primary candidate and MVTec AD as fallback or secondary comparison data;
- build dataset import, manifest, quality validation, sharding, sampling,
  retry/resume, and audit/lineage hooks before real VLM serving;
- implement a VLM adapter contract and mock adapter first, then swap in a real
  Qwen2.5-VL 3B/7B quantized endpoint on the Windows RTX node when ready;
- support manifest-based batch VLM inference and structured output validation;
- add prompt/model version tracking, regression gates, rollback simulation,
  failure scenarios, benchmark reports, and RCA/audit evidence.

P1 for July if time allows:

- Grafana panels, MLflow tracking artifacts, prompt/model registry hardening,
  traffic spike benchmark, GPU/resource metrics, Docker Compose deployment
  polish, drift metrics, audit log hardening, Mac mini/Windows distributed
  operating documentation, MinIO/object storage refinements.

P2 after August:

- LLM Agent, LangGraph AgentOps, human approval workflow, tool-call safety,
  Kueue/GPU scheduling, OpenLineage/Marquez, Ray Serve, KServe, production
  vLLM, multi-GPU simulation, autoscaling, synthetic defect generation, RAG
  over defect history, and full MLflow Registry governance.

Home Lab role split:

| Node | Role |
|---|---|
| Windows 11 desktop / RTX 4080 SUPER / 64GB RAM | primary VLM inference, serving, benchmark, batch inference, failure simulation, GPU pressure |
| Mac mini M4 Pro / 24GB RAM | control-plane, API gateway candidate, metadata/audit, dataset manifest/indexing, evaluator, monitoring, Docker Compose orchestration |
| MacBook Air M4 / 16GB RAM | development client, API/load-test client, demo operator, smoke-test client |

## Target Model Direction

The model path should mature in four steps:

1. Vision baseline model.
   - Purpose: prove dataset versioning, registry, API contract, monitoring.
   - Current baseline: majority-class vision placeholder and local metadata.
2. Real image model.
   - Purpose: replace synthetic behavior with a real image classifier or
     embedding model.
   - Candidate model family: PyTorch or Hugging Face vision models.
3. Vision-language model.
   - Purpose: accept image plus text prompt, produce text or structured JSON.
   - Candidate serving direction: vLLM where the selected VLM is supported,
     or Triton/custom runtime when model preprocessing is more specialized.
4. Multimodal platform workload.
   - Purpose: support image-text retrieval, multimodal RAG, evaluation,
     safety checks, latency SLOs, canary release, rollback, and audit trails.

## Target Architecture Layers

| Layer | Current Foundation | Enterprise Target |
|---|---|---|
| Object storage | MinIO buckets | MinIO/S3-compatible lake layout with raw, bronze, silver, gold, artifacts |
| Dataset format | JSONL, single Parquet files | Partitioned Parquet plus Iceberg or Delta table metadata |
| Batch compute | Python pipeline modules | DuckDB/Polars for local checks, Spark for distributed transforms |
| Streaming ingest | None | Kafka or Redpanda for event/image metadata streams |
| Data quality | Custom validation report | Great Expectations or equivalent expectation suites and data docs |
| Lineage | Local trace JSON and MLflow params | OpenLineage events from Airflow, Spark, and validation jobs |
| Feature layer | Dataset metadata only | Feast for structured features; vector DB for image/text embeddings |
| Experiment tracking | MLflow | MLflow with model cards, dataset lineage, prompt/eval artifacts |
| Training infra | Local Python and mac-mini worker | Ray or Kubernetes Jobs for distributed training and evaluation |
| Model registry | Local registry bridge | MLflow Registry plus promotion policy, rollback metadata, model cards |
| Serving | FastAPI placeholder | Registry-driven API, then KServe/Triton/vLLM production runtime |
| Model gateway | None | Versioned endpoint, canary, shadow traffic, A/B testing, rate limits |
| Observability | Prometheus/Grafana basics | API, model, data, drift, GPU, queue, and pipeline SLO dashboards |
| CI/CD/CT | Planned GitHub Actions | Image build, tests, policy gates, scheduled retraining/evaluation |
| Security | Local credentials | Secrets management, RBAC, audit logs, data retention, access policy |

## Platform Roadmap

### Stage 0. Current Local Foundation

Status: in progress, mostly proven through W0-W2 and W3 preflight.

Required evidence:

- Airflow DAG can run the full local pipeline.
- MinIO stores raw, processed, validated, and artifact objects.
- MLflow stores run metadata.
- FastAPI exposes health, readiness, predict, and metrics endpoints.
- Prometheus/Grafana can observe serving metrics.

### Stage 1. Registry-Driven Serving

Goal: remove placeholder serving and make the API load promoted model metadata.

Key tasks:

- Load `artifacts/registry/vision-baseline/latest.json` at API startup.
- Expose model name, version, stage, dataset version, and readiness.
- Make `/predict` use promoted artifact metadata instead of placeholder logic.
- Export model version and dataset version metrics.
- Add rollback-ready registry selection.

Evidence:

- `/ready` returns loaded model and dataset version.
- `/predict` returns `placeholder=false`.
- Grafana shows model version and prediction request metrics.

### Stage 2. Real Image Dataset And Lakehouse Layout

Goal: move from deterministic synthetic samples to a realistic image dataset
and enterprise data layout.

Key tasks:

- Ingest a public image dataset with image files and metadata.
- Split data into raw, bronze, silver, and gold zones.
- Write partitioned Parquet by dataset, version, split, and label.
- Add DuckDB/Polars smoke queries over Parquet in MinIO.
- Add Spark local job for larger transforms.
- Evaluate Iceberg or Delta metadata for table-level versioning.

Evidence:

- Dataset count, label distribution, dimensions, and split statistics are
  queryable from Parquet.
- Dataset version is content-derived and immutable.
- Validation failures are stored and reviewable.

### Stage 3. Data Quality, Lineage, And Catalog

Goal: make every dataset and pipeline run traceable across orchestration,
storage, training, registry, and serving.

Key tasks:

- Add Great Expectations or equivalent validation suites.
- Emit OpenLineage metadata from Airflow pipeline steps.
- Track dataset input/output URIs, schema, quality result, and model lineage.
- Add a lightweight catalog page or metadata index for datasets and models.
- Preserve lineage from raw image to served model version.

Evidence:

- A reviewer can answer which raw objects produced a model version.
- Failed validation and accepted validation are both recorded.
- Airflow run, MLflow run, dataset version, and registry version are linked.

### Stage 4. Multimodal Dataset And Embedding Pipeline

Goal: prepare the platform for VLM and multimodal retrieval workloads.

Key tasks:

- Add image-caption or image-question-answer dataset schema.
- Generate image and text embeddings with a vision-language encoder.
- Store embedding artifacts in MinIO.
- Add vector index storage with Qdrant, Milvus, or another vector DB.
- Version prompt templates, preprocessing, and embedding model metadata.

Evidence:

- Given an image or text query, the system can retrieve relevant image records.
- Embedding model version and dataset version are traceable.
- Retrieval quality metrics are logged as evaluation artifacts.

### Stage 5. VLM Training, Fine-Tuning, And Evaluation

Goal: move target serving from simple image prediction to VLM or multimodal
inference.

Key tasks:

- Select an open VLM suitable for available hardware and license constraints.
- Start with inference-only evaluation before fine-tuning.
- Add LoRA or QLoRA fine-tuning path only after evaluation contracts are clear.
- Track prompt, image, output, latency, token count, and eval score.
- Add golden test sets for image QA, captioning, classification, and refusal or
  safety behavior where applicable.

Evidence:

- MLflow stores model, dataset, prompt, metric, and evaluation artifacts.
- A repeatable evaluation job compares VLM versions.
- Promotion requires quality, latency, and safety gates.

### Stage 6. Production Serving Runtime

Goal: evolve from FastAPI-only serving to a production-grade serving platform.

Key tasks:

- Keep FastAPI as model gateway and business contract layer.
- Add vLLM for OpenAI-compatible LLM/VLM serving when model support fits.
- Add Triton for optimized multimodel GPU/CPU inference workloads.
- Add KServe on Kubernetes for autoscaling, rollout, and standardized
  inference resources.
- Support canary release, shadow traffic, rollback, timeout, batch, and queue
  metrics.

Evidence:

- A model version can be promoted, served, rolled back, and observed.
- Serving exposes model, dataset, prompt template, and runtime metadata.
- Load test results are captured with latency and error SLOs.

### Stage 7. Enterprise Governance And Operations

Goal: make the platform reviewable by infra, security, data, and ML engineers.

Key tasks:

- Add policy gates for data quality, model quality, security, and release.
- Add secrets management and credential rotation plan.
- Add RBAC and audit log plan for Airflow, MLflow, MinIO, Grafana, and API.
- Add cost and resource reporting for CPU, memory, GPU, and storage.
- Add incident runbooks for data quality failure, model rollback, serving
  latency, and storage outage.

Evidence:

- Every production-like action has an owner, run id, artifact, and rollback path.
- Release notes include data, model, serving, monitoring, and risk sections.
- Notion, Obsidian, Jira, and repo docs remain synchronized after milestones.

## Recommended Backlog Additions

| ID Range | Theme | Initial Tasks |
|---|---|---|
| `EVM-08x` | Lakehouse query layer | DuckDB query smoke, Spark transform, Iceberg/Delta evaluation |
| `EVM-09x` | Data quality and lineage | Great Expectations suite, OpenLineage emission, dataset catalog |
| `EVM-10x` | Multimodal dataset | image-caption schema, embedding generation, vector index |
| `EVM-11x` | VLM evaluation | model selection, inference eval, prompt/version tracking |
| `EVM-12x` | Production serving | vLLM/Triton/KServe runtime comparison and rollout plan |
| `EVM-13x` | Enterprise operations | RBAC, secrets, audit, SLO, incident runbooks |

## Technology Anchors

- KServe: Kubernetes-native inference platform for predictive and generative AI.
- Triton Inference Server: multi-framework inference runtime across GPU, CPU,
  edge, and data center environments.
- vLLM: OpenAI-compatible serving path for supported LLM and VLM workloads.
- Apache Iceberg: table format for large analytic datasets and engines such as
  Spark and Trino.
- OpenLineage: standard lineage model for jobs, runs, and datasets.
- Feast: feature store for production ML feature definition and serving.
- Ray Serve: scalable Python model serving and multi-model composition.
- Great Expectations: data quality validation and documentation layer.

## Planning Rule

Do not treat the July local MVP as the final architecture. Treat it as the
control-plane proof. Future work should keep each milestone tied to enterprise
evidence:

- reproducible run,
- versioned data,
- versioned model,
- governed promotion,
- observable serving,
- rollback path,
- documented operational decision.
