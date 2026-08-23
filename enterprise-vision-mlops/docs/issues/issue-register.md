# Issue Register

작성일: 2026-06-21

## Purpose

이 문서는 Jira가 연결되기 전까지 Git repository 안에서 agenda, backlog, issue,
진행 상태, commit 근거를 추적하기 위한 lightweight issue register이다.

원칙:

- 모든 작업은 issue id를 가진다.
- branch, commit, status 문서가 issue id와 연결된다.
- Jira가 연결되면 이 register를 Jira Epic/Story/Task로 migration한다.
- 현재 GitHub Issue 도구가 없어도 Markdown과 commit history만으로 진행 근거를 남긴다.

## Issue ID Convention

| Prefix | Meaning | Example |
|---|---|---|
| `EVM-EPIC` | 큰 phase 또는 portfolio epic | `EVM-EPIC-02` |
| `EVM` | 일반 구현 task | `EVM-021` |
| `EVM-DOC` | 문서/운영 기록 | `EVM-DOC-004` |
| `EVM-BUG` | 결함/운영 이슈 | `EVM-BUG-001` |

Branch convention:

```text
codex/evm-021-airflow-dag
codex/evm-035-minio-parquet
codex/evm-bug-001-remote-inventory-timeout
```

Commit convention:

```text
EVM-021 Add Airflow DAG skeleton
EVM-DOC-004 Document June connectivity review
EVM-BUG-001 Fix remote worker status fallback
```

Jira 연결 후에는 같은 issue key를 Jira key로 사용하거나, Jira key mapping table을
이 문서 하단에 추가한다.

## Epic Register

| ID | Epic | Target Window | Status | Outcome |
|---|---|---:|---|---|
| `EVM-EPIC-01` | Local Control-plane MVP | 2026-06 | Done | Local infra and modular MVP pipeline |
| `EVM-EPIC-02` | Airflow + MLflow Orchestration | 2026-06 to 2026-07 | Done | Full DAG with Airflow, MLflow linkage, retry/log/schedule |
| `EVM-EPIC-03` | Object Storage Data Platform | 2026-07 | Done | MinIO raw/processed/validated + Parquet + dataset version metadata |
| `EVM-EPIC-04` | Remote Training Infra | 2026-07-W3 | Done | mac-mini structured remote job execution, resource report, and artifact collection |
| `EVM-EPIC-05` | Registry-driven Serving | 2026-07-W3 | Done | API loads promoted model version and exposes model/dataset serving metadata |
| `EVM-EPIC-06` | Observability / Drift / SLO | 2026-07-W4 | Planned | dashboards, drift reports, alert rules |
| `EVM-EPIC-07` | CI/CD / Governance | 2026-07-W4 to W5 | Planned | GitHub Actions, checks, release notes |
| `EVM-EPIC-08` | 2027 Enterprise Extension | 2026-Q4 to 2027-Q1 | Planned | k3s/GPU/Triton/KServe extension |
| `EVM-EPIC-09` | Lakehouse Query / Data Quality / Lineage | 2026-Q4 | Planned | DuckDB/Spark/Iceberg evaluation, data quality suites, lineage emission |
| `EVM-EPIC-10` | Multimodal Dataset / Embedding Platform | 2026-Q4 | Planned | image-text schema, embedding artifacts, vector index |
| `EVM-EPIC-11` | VLM Evaluation And Serving | 2026-Q4 to 2027-Q1 | Planned | VLM inference evaluation, vLLM/Triton/KServe serving path |
| `EVM-EPIC-12` | Enterprise Operations And Governance | 2027-Q1 | Planned | RBAC, secrets, audit, SLO, incident runbooks |
| `EVM-EPIC-13` | Manufacturing VLM P0 Foundation | 2026-07-W4 | Next | VLM-first manufacturing visual inspection foundation: dataset, manifest, adapter, router, batch inference |
| `EVM-EPIC-14` | VLM Reliability Evaluation And Portfolio Cut | 2026-07-W5 | Planned | regression gate, rollback simulation, failure scenarios, benchmark, RCA, final portfolio evidence |
| `EVM-EPIC-06` | Observability / Drift / SLO | 2026-07-11 | Done | current-week VLM workload dashboards, drift reports, alert rules |
| `EVM-EPIC-07` | CI/CD / Governance | 2026-07-12 | Done | current-week GitHub Actions, checks, release notes, demo evidence |
| `EVM-EPIC-13` | Manufacturing VLM P0 Foundation | 2026-07-12 | Done | current-week completion sprint: dataset, manifest, adapter, router, batch inference |
| `EVM-EPIC-14` | VLM Reliability Evaluation And Portfolio Cut | 2026-07-12 | Done | current-week regression gate, rollback simulation, failure scenarios, benchmark, RCA, final portfolio evidence |
| `EVM-EPIC-15` | Model Lifecycle And Drift Operations | 2026-07-W5 | Planned | real model lifecycle state machine, candidate promotion, drift/special-case tracking, and alert review |
| `EVM-EPIC-16` | Large-scale Data Acquisition And Cleaning Research | 2026-07-W6 | Done | source registry, scalable acquisition, dedup/quality benchmark, curation, and lakehouse ingestion research |
| `EVM-EPIC-17` | Draft Decision And AgentOps Governance | 2026-07-W7 | Done | draft/decision registry, AgentOps reliability contract, scale-serving decision, portfolio stabilization, live diagnostics, and audited drift workflow are complete |
| `EVM-EPIC-18` | Kubernetes Runtime And MLOps Control Panel | 2026-07-W7 | Done | Kubernetes runtime, real B7 GPU lifecycle, metadata/control API, Control Panel, resource controls, and 13/13 executable closeout completed |
| `EVM-EPIC-19` | Human-Tunable Pipeline Control | Post-W7 | In Progress | Versioned profile control, dependency-aware lifecycle orchestration, CV/search, and isolated CT promotion enforcement are operational; A/B canary routing remains the explicit follow-up boundary |
| `EVM-EPIC-20` | Operator-Centered Reproducible Control Plane | 2026-07-W8 | Done | purpose-based operator workspaces, focused animated lifecycle views, immutable Run Blueprints, approved model-component onboarding, and deterministic user-scenario replay completed with desktop/mobile and F-drive evidence |
| `EVM-EPIC-21` | Multi-Domain Governed MLOps Evidence | Post-W8 | In Progress | governed scenario catalog, real multi-domain intake, operator-launched Airflow execution, and portfolio evidence are complete; text/VLM runtime, tenancy, and production resilience remain explicit follow-up work |
| `EVM-EPIC-22` | Cross-Scenario Correlation And Recovery Validation | Post-W8 | In Progress | plan/design, normalized correlation, and single-owner recovery/read-only incident plane complete; pairwise proof and maintenance-gated closure remain EVM-274..275 |
| `EVM-EPIC-23` | Full Lifecycle Guard Integration And VisA Operations Drill | Post-W8 | In Progress | execution ledger active; integrate A-E guards with actual data, training, MLflow, CT, replacement, CUDA serving and monitoring without hidden repair |
| `EVM-EPIC-24` | Real VLM And LLM Scenario Lifecycle Expansion | Post-W8 | Done | model-family-neutral lifecycle contract and sequential RTX 4080 proofs for real ScienceQA/SmolVLM and governed Dolly/Qwen workloads completed; no mock-only or automatic production promotion credit |
| `EVM-EPIC-25` | Distributed Scale And Operational Load Validation | Post-W8 | Planned | capacity envelope, durable concurrency, bounded queue/backpressure, GPU batching/VRAM, API availability, distributed data, VLM/LLM fairness and soak evidence; single-node limits remain explicit |

## Backlog

### Phase 1. Local Control-plane MVP

| ID | Task | Status | Evidence |
|---|---|---|---|
| `EVM-001` | Docker Compose stack 구성 | Done | `docker-compose.yml`, `docs/status/2026-06-18-local-infra.md` |
| `EVM-002` | Modular pipeline runner 구성 | Done | `scripts/run_pipeline.py`, `src/evm/pipelines` |
| `EVM-003` | Data ingest/validate/train/register/deploy/monitor command 구성 | Done | `docs/pipelines/*.md` |
| `EVM-004` | mac-mini remote worker branch 구성 | Done | `codex/mac-mini-worker`, `docs/status/2026-06-21-mac-mini-worker-branch.md` |
| `EVM-005` | Infra connectivity review 문서화 | Done | `docs/architecture-connectivity.md`, `docs/status/2026-06-21-connectivity-review.md` |
| `EVM-006` | PyCharm remote development preflight 구성 | Done | `docs/runbooks/pycharm-remote-dev-mac-mini.md` |
| `EVM-DOC-001` | Enterprise implementation agenda 작성 | Done | `docs/agenda/enterprise-mlops-implementation-agenda.md` |

### Phase 2. Airflow + MLflow Orchestration

| ID | Task | Status | Due | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-021` | Airflow Docker Compose service 추가 | Done | 2026-06-23 | Airflow webserver/scheduler/metadata DB 실행 |
| `EVM-022` | DAG directory와 `enterprise_vision_mlops_daily.py` 생성 | Done | 2026-06-24 | Airflow UI에서 DAG 표시 |
| `EVM-023-A` | DAG skeleton 생성 | Done | 2026-06-28 | `enterprise_vision_mlops_daily` DAG import 성공 |
| `EVM-023-B` | `data-ingest`, `data-validate` task 연결 | Done | 2026-06-28 | Airflow task graph에서 ingest->validate 확인 |
| `EVM-DOC-021` | Airflow setup runbook 작성 | Done | 2026-06-28 | `docs/runbooks/airflow-local.md` 작성 |
| `EVM-023` | 기존 pipeline command를 Airflow task로 연결 | Done | 2026-07-02 | ingest->validate->train->register->deploy->monitor task 연결 |
| `EVM-023-C` | `train` task 연결 | Done | 2026-07-05 | Airflow task logs and MLflow run |
| `EVM-023-D` | `register-model` task 연결 | Done | 2026-07-05 | registry metadata output |
| `EVM-023-E` | `deploy-check`, `monitor-check` task 연결 | Done | 2026-07-05 | deployment and monitoring reports |
| `EVM-024` | retry/timeout/log policy 설정 | Done | 2026-07-03 | task 실패/재시도 정책 문서화 |
| `EVM-025` | MLflow run id와 Airflow run id 연결 | Done | 2026-07-04 | MLflow tag 또는 params에 DAG context 저장 |
| `EVM-026` | Airflow runbook/status 문서 작성 | Done | 2026-07-05 | manual run 절차와 결과 기록 |
| `EVM-027` | Phase 2 smoke 검증 | Done | 2026-07-05 | full DAG run 성공 |

### Phase 3. Object Storage Data Platform

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-031` | MinIO bucket bootstrap 고도화 | Done | 2026-07-W2 | raw/processed/validated/mlflow-artifacts bucket 생성 |
| `EVM-032` | object storage client module 추가 | Done | 2026-07-W2 | upload/list/object-exists API |
| `EVM-033` | public vision dataset ingest | Done | 2026-07-W2 | raw dataset manifest and object upload |
| `EVM-034` | validation report 고도화 | Done | 2026-07-W2 | schema, dimensions, label distribution report |
| `EVM-035` | Parquet dataset generation | Done | 2026-07-W2 | processed/validated parquet outputs |
| `EVM-036` | dataset version metadata | Done | 2026-07-W2 | training input version fixed by metadata |

### Phase 4. Remote Training Infra

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-041` | remote job spec 정의 | Done | 2026-07-W3 | command, env, input, output, timeout schema |
| `EVM-042` | mac-mini ARM64 evaluation job | Done | 2026-07-W3 | remote job execution and report collection |
| `EVM-043` | Linux worker SSH 복구/등록 | Deferred | 2026-Q4 | `remote_exec_ready=true` for Linux candidate |
| `EVM-044` | worker resource report | Done | 2026-07-W3 | CPU/memory/runtime/architecture report |
| `EVM-045` | remote artifact collection | Done | 2026-07-W3 | control-plane collects remote outputs |

### Phase 5. Registry-driven Serving

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-051` | model loading module 추가 | Done | 2026-07-W3 | API loads local registry artifact |
| `EVM-052` | `/ready` model readiness 확장 | Done | 2026-07-W3 | model version/load status returned |
| `EVM-053` | `/predict` placeholder 제거 | Done | 2026-07-W3 | prediction uses promoted artifact |
| `EVM-054` | model version metric expose | Done | 2026-07-W3 | Prometheus sees serving model version |
| `EVM-055` | rollback-ready registry contract | Done | 2026-07-W3 | version selection and rollback documented |

### Phase 6. Observability / Drift / SLO

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-061` | Grafana dashboard 고도화 | Planned | 2026-07-W4 | latency/error/model version panels |
| `EVM-062` | pipeline success/failure metric | Planned | 2026-07-W4 | task run status visible |
| `EVM-063` | data drift report | Planned | 2026-07-W4 | baseline vs current distribution report |
| `EVM-064` | model drift placeholder/check | Deferred | 2026-Q4 | drift check output and documentation |
| `EVM-065` | SLO/alert rule 문서화 | Planned | 2026-07-W4 | latency/error/data quality SLO |

### Phase 7. CI/CD / Governance

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-071` | GitHub Actions lint/test workflow | Planned | 2026-07-W4 | push/PR check executes |
| `EVM-072` | Docker build check | Planned | 2026-07-W4 | API/MLflow images build in CI |
| `EVM-073` | pipeline smoke check in CI | Planned | 2026-07-W4 | minimal local pipeline command passes |
| `EVM-074` | release note template | Planned | 2026-07-W5 | portfolio cut release note |
| `EVM-075` | final portfolio demo script | Planned | 2026-07-W5 | repeatable demo steps |

### Phase 8. Enterprise Multimodal MLOps Expansion

Long-range planning details are maintained in
`docs/agenda/enterprise-multimodal-mlops-target-roadmap.md`.

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-081` | DuckDB/Polars query smoke over MinIO Parquet | Planned | 2026-Q4 | validated dataset can be queried from object storage |
| `EVM-082` | Spark transform job over object storage | Planned | 2026-Q4 | Spark writes partitioned Parquet with dataset version metadata |
| `EVM-083` | Iceberg/Delta table format evaluation | Planned | 2026-Q4 | recommendation doc with catalog, schema evolution, rollback tradeoffs |
| `EVM-091` | Great Expectations validation suite | Planned | 2026-Q4 | expectation suite and data docs generated for image metadata |
| `EVM-092` | OpenLineage metadata emission | Planned | 2026-Q4 | Airflow dataset/job/run lineage exported for pipeline tasks |
| `EVM-101` | multimodal dataset schema | Planned | 2026-Q4 | image, caption, prompt, answer, split, license, and source metadata schema |
| `EVM-102` | embedding generation pipeline | Planned | 2026-Q4 | image/text embeddings versioned and stored as artifacts |
| `EVM-103` | vector index integration | Planned | 2026-Q4 | retrieval smoke test by image or text query |
| `EVM-111` | VLM model selection and license review | Planned | 2026-Q4 | selected model fits hardware, license, and serving constraints |
| `EVM-112` | VLM inference evaluation job | Planned | 2026-Q4 | repeatable eval set with quality, latency, and safety metrics |
| `EVM-113` | vLLM/Triton/KServe serving comparison | Planned | 2027-Q1 | serving recommendation with runtime, rollout, and monitoring tradeoffs |
| `EVM-121` | enterprise RBAC/secrets/audit plan | Planned | 2027-Q1 | operational policy document covering Airflow, MLflow, MinIO, Grafana, API |
| `EVM-122` | incident and rollback runbooks | Planned | 2027-Q1 | runbooks for data failure, model rollback, latency incident, storage outage |
| `EVM-123` | secure Tailnet remote operator access | Done | 2026-07-12 | Docker ports are localhost-only; eight Tailnet routes and nine remote endpoint checks pass from the Mac mini |

### Phase 13. Manufacturing VLM P0 Foundation

This phase supersedes the generic W4 hardening order for the July MVP. The
project target is now a manufacturing visual inspection AI infrastructure lab:
real industrial image dataset handling first, VLM adapter contracts second, and
operational reliability evidence around the VLM workload third.

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-130` | Reposition project narrative and README for manufacturing VLM infra | Done | 2026-07-W4 | README and agenda describe Manufacturing Visual Inspection VLM-first AI Infra / MLOps / AIOps, not a generic demo app |
| `EVM-131` | Home Lab deployment topology update | Done | 2026-07-W4 | Windows RTX inference, Mac mini control-plane/evaluator, and MacBook client roles documented |
| `EVM-132` | Industrial anomaly dataset decision and import contract | Done | 2026-07-W4 | VisA primary and MVTec AD fallback/secondary decision recorded with license/access notes |
| `EVM-133` | Manufacturing image manifest schema | Done | 2026-07-W4 | manifest captures dataset_id, sample_id, split, label, image_uri/path, dimensions, hash, source, license, version |
| `EVM-134` | Image quality validation pipeline | Planned | 2026-07-W4 | corrupt image, duplicate/hash, dimension, brightness/blur, split/label, and drift proxy checks produce report artifacts |
| `EVM-135` | Dataset shard/split builder | Planned | 2026-07-W4 | manifest can be sharded and sampled for repeatable batch VLM evaluation and resume/retry |
| `EVM-136` | Data quality and ETL framework boundary | Done | 2026-07-W4 | image-quality gate is backed by reusable data quality policy, dataset contract, ETL recipe, and registry interfaces |
| `EVM-141` | VLM adapter interface and mock adapter | Planned | 2026-07-W4 | adapter accepts request_id, trace_id, image, prompt, prompt_version, model_version and returns structured JSON |
| `EVM-142` | Multimodal router request classification | Planned | 2026-07-W4 | API/router can classify visual inspection, caption/description, QA, and unsupported request types |
| `EVM-143` | Manifest-based batch inference runner | Planned | 2026-07-W4 | runner reads manifest shards, calls VLM adapter, writes JSONL outputs, supports retry/resume |
| `EVM-144` | VLM output schema validator | Planned | 2026-07-W4 | defect_detected, defect_type, severity, evidence, confidence_proxy, action, raw output, latency, and error_type are validated |

### Phase 14. VLM Reliability Evaluation And Portfolio Cut

This phase keeps the previous W4/W5 observability and CI/CD goals, but ties
them to the VLM workload that must be demo-ready by 2026-07-31. P2 items such as
LLM Agent, LangGraph, Kueue, Ray Serve, KServe, and full vLLM production serving
remain deferred until after the July cut.

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-151` | Prompt and model version registry | Planned | 2026-07-W5 | prompt templates, model candidates, adapter backend, dataset version, and eval config are versioned |
| `EVM-152` | Regression gate with intentionally bad candidate | Planned | 2026-07-W5 | bad prompt/model candidate fails quality/schema/latency gate and blocks promotion |
| `EVM-161` | Audit event schema and RCA join path | Planned | 2026-07-W5 | request, dataset sample, batch, prompt, model, output, metric, and failure events can be joined by trace_id |
| `EVM-162` | Failure scenario suite | Planned | 2026-07-W5 | at least bad prompt, corrupt/drifted image, schema failure, and model endpoint failure scenarios are reproducible |
| `EVM-171` | VLM metrics and benchmark reports | Planned | 2026-07-W5 | latency, error, schema validity, dataset quality, and resource pressure reports are generated |
| `EVM-181` | Portfolio README and final demo script | Planned | 2026-07-W5 | final README/demo show architecture, hardware-aware home lab, dataset, VLM eval, observability, RCA, rollback, lessons learned |

### Phase 6. 2026-07-06 Current-week Observability Override

These rows supersede the earlier W4 target-window rows for the current-week
enterprise VLM completion sprint. The intent is to make observability evidence
support the VLM workload instead of remaining a generic platform task.

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-061` | Grafana dashboard hardening | Done | 2026-07-11 | latency, error, model version, VLM schema validity, and dataset quality panels are visible |
| `EVM-062` | pipeline success/failure metric | Done | 2026-07-11 | task run status and latest run result are visible |
| `EVM-063` | data drift report | Done | 2026-07-11 | baseline vs current distribution report is generated for manifest/image metadata |
| `EVM-065` | SLO/alert rule documentation | Done | 2026-07-11 | latency, error, data quality, and schema validity SLOs are documented |

### Phase 7. 2026-07-06 Current-week CI/Governance Override

These rows supersede the earlier W4/W5 target-window rows. CI and governance
must now support the current-week VLM completion sprint and final demo evidence.

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-071` | GitHub Actions lint/test workflow | Done | 2026-07-11 | push/PR check executes |
| `EVM-072` | Docker build check | Done | 2026-07-11 | API and MLflow-related images build in CI |
| `EVM-073` | pipeline smoke check in CI | Done | 2026-07-11 | minimal local pipeline command passes in CI |
| `EVM-074` | release note template | Done | 2026-07-12 | portfolio cut release note includes VLM workload evidence |
| `EVM-075` | final portfolio demo script | Done | 2026-07-12 | repeatable demo steps cover dataset, VLM eval, observability, RCA, and rollback |

### Phase 13. 2026-07-06 Current-week Manufacturing Sprint Override

The new enterprise MLOps plan is now scheduled for completion during
2026-07-06 to 2026-07-12. The already completed domain-pack foundation remains
Done; the rest is sequenced as a day-level implementation plan.

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-130` | Reposition project narrative and README for manufacturing VLM infra | Done | 2026-07-06 | README and agenda describe Manufacturing Visual Inspection VLM-first AI Infra / MLOps / AIOps, not a generic demo app |
| `EVM-131` | Home Lab deployment topology update | Done | 2026-07-06 | Windows RTX inference, Mac mini control-plane/evaluator, and MacBook client roles documented |
| `EVM-132` | Industrial anomaly dataset decision and import contract | Done | 2026-07-06 | VisA primary and MVTec AD fallback/secondary decision recorded with license/access notes |
| `EVM-133` | Manufacturing image manifest schema | Done | 2026-07-06 | manifest captures dataset_id, sample_id, split, label, image_uri/path, dimensions, hash, source, license, version |
| `EVM-134` | Image quality validation pipeline | Done | 2026-07-07 | corrupt image, duplicate/hash, dimension, brightness/blur, split/label, and drift proxy checks produce report artifacts |
| `EVM-135` | Dataset shard/split builder | Done | 2026-07-07 | manifest can be sharded and sampled for repeatable batch VLM evaluation and resume/retry |
| `EVM-136` | Data quality and ETL framework boundary | Done | 2026-07-08 | image-quality gate now loads reusable quality policy and ETL recipe metadata from the manufacturing domain pack |
| `EVM-141` | VLM adapter interface and mock adapter | Done | 2026-07-08 | adapter accepts request_id, trace_id, image, prompt, prompt_version, model_version and returns structured JSON |
| `EVM-142` | Multimodal router request classification | Done | 2026-07-08 | API/router can classify visual inspection, caption/description, QA, and unsupported request types |
| `EVM-143` | Manifest-based batch inference runner | Done | 2026-07-09 | runner reads manifest shards, calls VLM adapter, writes JSONL outputs, supports retry/resume |
| `EVM-144` | VLM output schema validator | Done | 2026-07-09 | defect_detected, defect_type, severity, evidence, confidence_proxy, action, raw output, latency, and error_type are validated |

### Phase 14. 2026-07-06 Current-week Reliability Sprint Override

The former W5 VLM reliability work is now part of the same current-week
completion sprint. This keeps the enterprise claim focused on lifecycle
evidence, not only feature presence.

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-151` | Prompt and model version registry | Done | 2026-07-10 | prompt templates, model candidates, adapter backend, dataset version, and eval config are versioned |
| `EVM-152` | Regression gate with intentionally bad candidate | Done | 2026-07-10 | bad prompt/model candidate fails quality/schema/latency gate and blocks promotion |
| `EVM-161` | Audit event schema and RCA join path | Done | 2026-07-10 | request, dataset sample, batch, prompt, model, output, metric, and failure events can be joined by trace_id |
| `EVM-162` | Failure scenario suite | Done | 2026-07-10 | at least bad prompt, corrupt/drifted image, schema failure, and model endpoint failure scenarios are reproducible |
| `EVM-171` | VLM metrics and benchmark reports | Done | 2026-07-11 | latency, error, schema validity, dataset quality, and resource pressure reports are generated |
| `EVM-181` | Portfolio README and final demo script | Done | 2026-07-11 | final README/demo show architecture, hardware-aware home lab, dataset, VLM eval, observability, RCA, rollback, lessons learned |

### Phase 15. Model Lifecycle And Drift Operations

After the current-week enterprise VLM completion sprint, the next sprint moves
from "can the platform run the VLM workload?" to "can the platform manage the
real model lifecycle over time?" This includes candidate state, promotion
evidence, drift/special-case tracking, RCA feedback loops, and lifecycle review
surfaces.

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-191` | Real trainable model lifecycle state machine | Done | 2026-07-09 | `train` now produces `image_feature_centroid` artifacts with lifecycle gate state; `model-lifecycle` emits Draft/Registered/Validated/Promoted transition evidence |
| `EVM-192` | Model/data/resource lineage matrix | Done | 2026-07-09 | registry v9 joins dataset version, validated parquet URI, model digest, feature config, metrics, and local resource profile |
| `EVM-193` | Drift and special-case tracking queue | Done | 2026-07-09 | `drift_special_case_queue.json` records misclassification and label-distribution watch cases with owner, severity, and status |
| `EVM-194` | RCA-to-regression feedback loop | Done | 2026-07-09 | `rca_regression_candidates.json` promotes false prediction cases into reviewable regression candidates |
| `EVM-195` | Lifecycle dashboard and visual review checkpoint | Done | 2026-07-09 | `lifecycle_dashboard.json` and W5 verification SVG/Markdown package show candidate state, blockers, metrics, deployment, monitoring, and Mac mini evidence |
| `EVM-196` | Real model serving contract hardening | Done | 2026-07-09 | FastAPI `/predict` now runs registry-loaded feature model inference and deployment check verifies `feature_source` and class scores |
| `EVM-197` | Airflow W4/W5 real-cycle alignment | Done | 2026-07-09 | Airflow config points to mounted VisA/F-drive data and DAG includes `model_lifecycle` after `register_model` |
| `EVM-198` | Mac mini M4 Pro resource integration proof | Done | 2026-07-09 | `remote-inventory` and `remote-job` verify arm64 SSH execution, 12 CPU, 24GB memory, and remote artifact collection |
| `EVM-199` | W5 visual verification evidence package | Done | 2026-07-09 | `docs/reviews/2026-07-09-w5-real-model-lifecycle-verification.md` and SVG evidence assets generated |
| `EVM-200` | Production promotion gate hardening | Done | 2026-07-09 | weak proof models are registered as Shadow when accuracy/precision/recall/F1/AUROC thresholds are not met, while serving/deployment proof remains verifiable |

### Phase 16. Large-scale Data Acquisition And Cleaning Research

This sprint researches how the platform should ingest and clean larger image
collections without locking the architecture to one domain. Manufacturing
inspection remains the first policy pack, but source policy, schema, quality,
deduplication, and curation flows must be replaceable.

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-201` | Data source registry and collection policy | Done | 2026-07-06 | `dataset-intake-audit` records source URL, license, retention, access policy, F-drive raw root, object prefix, and readiness state before acquisition |
| `EVM-202` | Large-scale batch acquisition planner | Done | 2026-07-06 | acquisition plan records download/mount next action, checkpoint path, target manifest, object layout, and retry-ready handoff steps |
| `EVM-203` | Deduplication and cleaning benchmark | Done | 2026-07-06 | cleaning benchmark reports exact hashes, duplicate groups, unreadable images, dimensions, brightness/blur proxies, labels, classes, and splits |
| `EVM-204` | Labeling and curation workflow | Done | 2026-07-W6 | sample review, label state, HITL queue, and curated eval-set promotion states are defined |
| `EVM-205` | Lakehouse-scale ingestion research spike | Done | 2026-07-W6 | DuckDB/Polars/Spark/Iceberg tradeoffs are documented with at least one runnable prototype path |

Real VisA cycle evidence: `docs/status/2026-07-06-visa-open-data-cycle.md`
verifies 10,821 open-dataset records through intake, validation, image quality,
sharding, mock VLM batch evaluation, reliability gate, training, registry,
serving, monitoring, and MinIO Parquet storage.

### Phase 17. Draft Decision And AgentOps Governance

This sprint handles the research and management problems that appear after the
first VLM platform cut: proposal/draft management, decision traceability,
AgentOps reliability, and production-scale serving options.

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-211` | Draft and decision registry | Done | 2026-07-W7 | API/F-drive registry and Control Panel support draft/review/approved/rejected states, optimistic versions, evidence URIs, and independent approval; a real staging-retention decision closed as approved v3 |
| `EVM-212` | AgentOps reliability follow-up design | Done | 2026-07-W7 | executable AgentRun contract, persistent-checkpoint/HITL/tool-audit policy, four recovery scenarios, and validator pass; runtime execution is explicitly not claimed |
| `EVM-213` | Scale serving research plan | Done | 2026-07-W7 | role-specific vLLM/Triton/KServe/Ray Serve/Kueue decision and six-gate KServe+Triton pilot validator pass; runtime installation is explicitly not claimed |
| `EVM-214` | July portfolio stabilization review | Done | 2026-07-W7 | post-W7 full VisA host/container replay preserves the selected B7 readiness through immutable snapshots; runtime-path-independent identity, responsive UI evidence, known gaps, and handoff risks are consolidated |
| `EVM-215` | Control Panel diagnostics and drift review workflow | Done | 2026-07-W7 | five-source 5-second synchronization, structured blocked/warn evidence, change-only audit log, measured drift preview/acknowledgement, independent decision record, and real Kubernetes CUDA B7 inference are verified |

### Phase 18. Kubernetes Runtime And MLOps Control Panel

This compressed sprint supplements W6/W7 with the next system architecture
upgrade: Kubernetes-ready runtime boundaries plus an enterprise MLOps Control
Panel. The goal is to stop relying on manual artifact-file inspection for every
cycle and prepare the Docker Compose foundation for local Kubernetes/k3s/kind
execution. The Control Panel must make Kubernetes resource state, Airflow/MLflow
task assignment, data intake, training, inference, intermediate artifacts, and
resource-control actions visible through real-time or near-real-time UI states.
W7 must explicitly assume both internal platform use by teams/departments and
external production-service operation. Data pipelines, model experiment/training
pipelines, drift review, and CD/CT verification gates must be visible and
actionable from the same control surface. For W7 model evidence, mock adapters
and smoke-only checks are no longer acceptable completion proof; model work must
use real Torch training/evaluation over real dataset records, starting with a
parallel EfficientNet-B0/B7 candidate matrix.

Implementation closure for `EVM-224` through `EVM-238` must follow
`docs/status/2026-07-09-w7-implementation-acceptance-matrix.md`. Each issue
must have implementation files, input data, output artifacts, verification
commands, success criteria, and blocker rules before it can be marked Done.
`EVM-224` is the hard dependency for UI work; Control Panel views must bind to
live `CycleRun` API fields, not static examples.
P0/P1/P2 tiers define dependency order only; they must not be interpreted as a
reduced-depth implementation plan. `EVM-238` is an umbrella policy task whose
closure is split into `EVM-238-A` policy guard implementation and `EVM-238-B`
validation against actual `CycleRun.model_matrix` and EfficientNet evidence.

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-221` | Kubernetes runtime resource map | Done | 2026-07-W6 | Docker Compose services are mapped to Kubernetes Deployment/Service/PVC/Secret/ConfigMap/Job/CronJob resources with CPU/GPU/storage placement notes |
| `EVM-222` | Local Kubernetes manifest scaffold | Done | 2026-07-W6 | initial k8s manifests or overlays exist for API, MLflow, MinIO, Prometheus/Grafana, and at least one pipeline Job |
| `EVM-223` | Control Panel metadata and control API contract | Done | 2026-07-W6 | API contract defines cycle run, dataset version, model version, lineage, metrics, promotion gate, artifacts, serving state, command intent, task assignment, and resource action schemas |
| `EVM-224` | Cycle lineage aggregation API | Done | 2026-07-W7 | backend endpoint aggregates registry, lifecycle, dataset, Airflow/MLflow references, Prometheus serving status, artifact links, tenant/environment scope, drift state, and CD/CT gate state for one cycle; verified by `docs/status/2026-07-09-w7-evm-224-cycle-lineage-aggregation-api.md` |
| `EVM-225` | MLOps Control Panel v0 | Done | 2026-07-W7 | React/Vite UI binds to live CycleRun API and renders cycle, readiness, model matrix, gates, timeline, and resource topology; verified by `docs/status/2026-07-09-w7-control-panel-v0.md`; Overview cycle-ring remediation verified by `docs/status/2026-07-09-w7-control-panel-cycle-ring-remediation.md` |
| `EVM-226` | Docker Desktop Kubernetes B7 training and serving proof | Done | 2026-07-W7 | Docker Desktop advertises `nvidia.com/gpu=1`; real VisA run `w7-k8s-b7-20260711T010003` completed the B7 GPU Job, MLflow run `445be011d88a40ada5e70ab86de4f750`, model SHA `1d1df27...8337d`, 1/1 Ready serving, CUDA inference, controlled invalid-digest failure, and recovery. Final proof is in `docs/status/2026-07-11-w7-kubernetes-b7-closeout.md`. |
| `EVM-227` | GPU/VLM serving deployment design | Done | 2026-07-W7 | historical design and resource inventory are complete; active B7 packaging, GPU scheduling, Kubernetes serving, probes, inference, and rollback acceptance is superseded by and absorbed into EVM-226, so this issue is not an independent production-serving claim |
| `EVM-228` | Compressed W6/W7 integration review | Done | 2026-07-W7 | Live closeout matrix returned 13 passing claims, zero blockers, and `closeout_allowed=true` after real GPU training, serving, readiness, policy, deployment apply, and exact rollback. Final evidence is indexed by `docs/status/2026-07-11-w7-kubernetes-b7-closeout.md`. |
| `EVM-229` | Kubernetes resource topology and animation UI | Done | 2026-07-W7 | UI visualizes namespace/node/pod/job/service/PVC/GPU state with live/stale/projected source metadata and drilldowns. Historical `DeadlineExceeded` evidence remains preserved; the final observer reports `evm-b7-training = done / CompletionsReached` and serving 1/1 Ready. Desktop/mobile topology tests bind to the live status and pass. |
| `EVM-230` | Airflow and MLflow task authoring and assignment UI | Done | 2026-07-W7 | UI can create dry-run, queued, pending-confirmation, and blocked task assignments with owner, priority, resource profile, config payload, environment/approval policy, Airflow DAG/run reference, MLflow experiment/run reference, and CD/CT gate preview; verified by `docs/status/2026-07-09-w7-evm-230-232-operations-control.md` |
| `EVM-231` | Live pipeline timeline and intermediate result drilldown | Done | 2026-07-W7 | data validation, image quality, curation, lakehouse, registry, lifecycle, and EfficientNet real-test stages show live status plus stage-level artifacts, metrics, sample outputs, resources, and failure reasons; verified by `docs/status/2026-07-09-w7-evm-229-231-topology-timeline.md` |
| `EVM-232` | Resource control protocol and audit guardrails | Done | 2026-07-W7 | Kubernetes, Airflow, and MLflow actions are represented as explicit command intents with dry-run, pending-confirmation, cancel, actor, reason, target, parameters, and audit trail; mutation remains blocked before explicit future apply support; verified by `docs/status/2026-07-09-w7-evm-230-232-operations-control.md` |
| `EVM-233` | Enterprise environment promotion policy | Done | 2026-07-W7 | server-side policy computes deterministic `allow`, `pending_approval`, or `blocked` from ownership, target namespace, EVM-236 readiness, CI/CD/CT, immutable model/image digests, rollback evidence, and separation of duties; CycleRun/API/UI and promotion command guards use the same decision, caller-supplied namespace/identity values cannot override the command target, and F-drive audit evidence plus desktop/mobile regression proof are recorded under `artifacts/w7/promotion_policy/evm-233-verification-20260710T184311` |
| `EVM-234` | B7 drift review event | Done | 2026-07-W7 | pinned B7 CUDA inference compares 2,136 real validation baseline records with a disjoint 205-record `pcb3` test intake window. Input category JS `0.742829` exceeds policy `0.10` while confidence PSI `0.016077` remains stable; event `drift-cf8be9047505ec32` routes 128 real records to label review/approval with automatic retraining, deployment, and promotion disabled. Verified by `docs/status/2026-07-10-w7-evm-234-measured-b7-drift-review.md`. |
| `EVM-235` | CI-gated deployment intent and state machine | Done | 2026-07-W7 | Intent `deploy-3bfdb1f5a81ba507` completed CI-bound dry-run, approval, queue, Kubernetes apply, real CUDA inference, and exact approved rollback. Model/image identities are immutable, rollback recomputes artifact SHA instead of using revision undo, and the intent stores an immutable CI bundle plus transition audit. Deployment CI `29108295585`, closeout control-plane CI `29108780028`, 120 Python tests, 19 frontend contracts, and 14/14 desktop/mobile E2E scenarios pass. |
| `EVM-236` | Enterprise evidence readiness evaluator | Done | 2026-07-W7 | Artifact-content evaluator returns `ready` with 13/13 checks passing and zero blockers. It validates the data contract, 10,821-record metadata/shards/split, quality, lineage, MLflow run, model card/artifact digest, real-test report, approved rollback reference, and Kubernetes runtime proof. |
| `EVM-237` | Torch EfficientNet-B0/B7 real model matrix | Done | 2026-07-W7 | All four real VisA candidates retain MLflow and F-drive evidence. The selected run-scoped B7 candidate is MLflow run `445be011d88a40ada5e70ab86de4f750`, seed `20260710`, accuracy `0.972948`, F1 `0.864989`, AUROC `0.987418`, with model SHA `1d1df27...8337d`. |
| `EVM-238` | W7 real-test-only evidence policy umbrella | Done | 2026-07-W7 | W7 real-test-only closure is complete because `EVM-238-A` policy guard is Done and `EVM-238-B` evidence validation passed with `valid=true`, `checked_candidate_count=4`, and `violations=[]` in `docs/status/2026-07-10-w7-real-test-evidence-validation.md` |
| `EVM-238-A` | W7 real-test policy guard | Done | 2026-07-W7 | policy guard validates CycleRun real-test policy and W7 closure records; verified by `docs/status/2026-07-09-w7-real-test-policy.md` |
| `EVM-238-B` | W7 real-test evidence validation | Done | 2026-07-W7 | validator checks actual `CycleRun.model_matrix`, four EfficientNet MLflow runs, F-drive artifacts, metrics, split manifest, GPU profile, environment report, confusion matrices, training history, model cards, and blocker reasons; report `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/real_test_evidence/evm-238-b-real-test-evidence-report.json` returned `valid=true` and `violations=[]` |

### Phase 19. Human-Tunable Pipeline Control

This phase turns the Control Panel from a monitoring and guarded-intent surface
into an operator-authored pipeline control plane. Configuration is stored as an
immutable typed profile, while runtime capabilities remain fail closed until a
real executor is connected. Completion evidence is indexed by
`docs/status/2026-07-12-human-tunable-pipeline-profile-control.md`.

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-239` | Versioned Pipeline Run Profile and Profile Studio | Done | Post-W7 | typed data/model/experiment/gate/resource profile, immutable F-drive versions and digests, API/OpenAPI contract, rendered Airflow/model configs, data-cycle preview/queue, responsive Configure UI, and fail-closed capability matrix pass 173 Python, 26 frontend, and 20 browser tests |
| `EVM-240` | Explicit real-time lifecycle progress semantics | Done | Post-W7 | Timeline, stage detail, and Release views display Not Started, In Progress, Completed, and Blocked labels with calculated percentages, animated running bars, reduced-motion support, and desktop/mobile regression proof |
| `EVM-241` | Dependency-aware full lifecycle profile orchestrator | Done | Post-W7 | run `lifecycle-20260712T164133-1e8bf477` completed 10/10 real stages with VisA, CUDA EfficientNet-B0, MLflow `ca742ba3784d4861a58b8a92f30eb2ab`, readiness, CI/CT, independent approval, Kubernetes staging validation, and monitoring; all nine Control Panel tabs passed manual desktop/mobile audit, Product is restored 1/1, CI `29219088931` and Deployment Admission `29219114314` passed, and the final review is `docs/status/2026-07-13-evm-241-control-panel-manual-ux-system-audit.md` |
| `EVM-242` | Cross-validation and bounded hyperparameter search executor | Done | 2026-07-W8 | real run `lifecycle-20260713T074247-429569a4` processed all 10,821 VisA records, passed five-file source provenance, executed two deterministic 2-fold GPU trials plus final refit on RTX 4080 SUPER, persisted MLflow parent `3046f9900e20472cb5c4425ca11c268f`, four fold children, comparison/fold/model matrices and model digest `34666024...`; trial-001 was selected without holdout leakage and final promotion correctly remained blocked only by `f1<0.75` (observed 0.4622), while the Control Panel exposed live units, parameters, fold metrics, and explicit blockers |
| `EVM-243` | Isolated CT dataset snapshot and promotion enforcement | Done | 2026-07-W8 | final run `lifecycle-20260713T164053-c701bd39` completed 10/10 stages without retry or manual approval: real VisA Airflow 18/18, CUDA B0 training with epoch-4 early stop, MLflow run `b35b5cc3d0704464abe2288e6e3548be`, immutable CT snapshot `ct-visa-open-data-e35d93d5561f-test-c6b466afb907`, 2,181 isolated holdout records against 8,640 training records with zero overlap and no mutation, CUDA CT accuracy 0.9624/F1 0.8075/AUROC 0.9827, automated staging admission, deployment, serving inference, and Prometheus validation; source commit `23fb2a6`, CI `29266039045`, deployment admission `29266106499` |
| `EVM-244` | A/B canary traffic router and evaluator | Done | 2026-07-W8 | Jira `SCRUM-144` closure comment `10461`; non-disruptive scope closed by `scenario-b-quality-closure-20260802T032348Z-3058c67e` and `scenario-b-runtime-closure-20260802T032542Z-1d1df27f`: real VisA/CUDA, 500 paired shadow, exact 100/1,000 routing, 1,000/1,000 identity, quality admission block, 2% controlled runtime breach, zero allocation, exact B0 route/post-inference, 16/16 hashes per run and common live-proof PASS. This is isolated controlled replay, not production traffic or business A/B; live Kubernetes canary remains a separate blocked boundary |

### Phase 20. Operator-Centered Reproducible Control Plane

Goal:

- Reduce the Control Panel to purpose-based operator workspaces and progressively disclosed evidence.
- Let an operator define data identity, split policy, model, training parameters, gates, and resources as one immutable Run Blueprint.
- Prove that a saved Blueprint digest can reproduce the same rendered configs, execution plan, and evidence references.

| ID | Task | Status | Due | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-245` | Purpose-based Control Panel information architecture | Done | 2026-07-W8 | nine flat tabs are grouped under Monitor, Build, Release, and Govern with task-specific subordinate views; Readiness and Pipeline use focused summary/evidence and pipeline/infrastructure modes; persisted navigation and every existing view pass desktop/mobile browser regression |
| `EVM-246` | Guided immutable Run Blueprint studio | Done | 2026-07-W8 | five guided steps configure dataset identity and manifests, deterministic split, approved Torch model, hyperparameters, experiment policy, resources, gates, and environment; saving writes a versioned profile and exact Airflow/model runtime configs |
| `EVM-247` | Focused animated lifecycle visualization | Done | 2026-07-W8 | Overview and Blueprint review use an accessible animated Data-to-Observe process map driven by real CycleRun/profile state with pending, ready, running, completed, review, and blocked semantics plus reduced-motion support |
| `EVM-248` | Approved model-component registry and onboarding contract | Done | 2026-07-W8 | versioned approved B0/B7 components expose pinned source and training/serving images; UI selection drives rendered config and actual K8s Job/Deployment images; unregistered, mismatched, mutable, or unpinned components fail closed |
| `EVM-249` | Deterministic operator-scenario replay and evidence validator | Done | 2026-07-W8 | desktop/mobile automation and an in-app browser scenario saved `w8-operator-replay-20260713` v1, sealed 11/11 profile/data/config/catalog/image identities, created dry run `lifecycle-20260713T063058-7d46b7bb`, and persisted all evidence under the F-drive |
| `EVM-250` | Model-quality regression remediation and live training telemetry | Done | 2026-07-W8 | under-threshold candidates now emit a typed `review_required` quality event with observed/policy metrics, deterministic failure fingerprint, remediation actions, and a same-profile repeat guard; real B0 evidence was backfilled as `model-quality-d47f18f1175d15c7`, the UI routes directly to the failed Blueprint, and operator edits produced replay-sealed v4 plus dry run `lifecycle-20260713T083700-d1555735`; batch/epoch/validation telemetry is persisted by immutable trainer image `sha256:1114d63f...` and rendered every 3 seconds in Runs |

### Phase 21. Multi-Domain Governed MLOps Evidence

Goal:

- Let an operator discover, validate, and launch approved department scenarios without shell access.
- Prove immutable real-data intake and policy-driven preprocessing for image, text, instruction, and image-text datasets.
- Preserve an honest boundary between data readiness and model/deployment readiness.

| ID | Task | Status | Due | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-251` | Governed enterprise scenario catalog and intake registry | Done | 2026-07-W8 | Control Panel exposes four approved scenarios with separate data/model/deploy readiness; immutable source identity and an allowlisted preprocessing registry reject arbitrary transform execution |
| `EVM-252` | Real multi-domain intake and quality evidence | Done | 2026-07-W8 | F-drive evidence contains VisA 10,821, BANKING77 13,071, Dolly 14,942, and ScienceQA image subset 512 records with source revisions/hashes, split manifests, deduplication, leakage/PII policy, and image extraction/header validation |
| `EVM-253` | Operator-only Airflow intake launch and live reconciliation | Done | 2026-07-W8 | Control Panel launch reaches a real Airflow task instance, reconciles queued/running to terminal state, persists artifacts, and no future-dated DAG start date can create an empty successful run |
| `EVM-254` | Evidence validator and first enterprise portfolio draft | Done | 2026-07-W8 | fail-closed validator writes a hash-addressed F-drive record and the Korean portfolio draft distinguishes the proven CV staging lifecycle from data-only NLP/LLM/VLM adapters and material enterprise gaps |
| `EVM-255` | BANKING77 text training/serving lifecycle | Planned | Post-W8 | real text training, MLflow evaluation, isolated CT, promotion, Kubernetes serving, monitored rollback, and operator-only inference evidence |
| `EVM-256` | VLM evaluation and governed serving adapter | Planned | Post-W8 | pinned VLM runtime, ScienceQA-style evaluation, safety/quality gates, GPU profile, model card, serving probe, and controlled promotion evidence |
| `EVM-257` | Multi-tenant SSO, RBAC, quotas, and secrets | Planned | Post-W8 | team tenancy, least-privilege authorization, namespace/resource quotas, secret rotation, audited approval, and negative isolation tests |
| `EVM-258` | HA/DR, load, chaos, SLO, and GitOps proof | Planned | Post-W8 | backup/restore, fault injection, load evidence, error-budget alerts, GitOps reconciliation, rollback, and disaster-recovery proof |
| `EVM-259` | Deterministic cross-origin Control Panel state and live-runtime preflight | Done | 2026-07-W8 | root entry on ports 4173 and 4174 resolves to the same latest LIVE CycleRun; explicit `cycle`, `run`, and `view` URL state replaces origin-scoped persistence; stale requests cannot overwrite a newer selection; Connecting, Partial, and Unavailable are distinct; host worker and Kubernetes observer preflight fail fast; 43 frontend contracts and 20 desktop/mobile browser scenarios pass |
| `EVM-260` | Stage-level lifecycle workbench, evidence-bound model promotion, and control-plane observability | Done | 2026-07-W8 | automatic and stepwise lifecycle modes, explicit Continue/Retry handoffs, immutable stage input/output lineage, historical model-candidate selection bound to readiness/CT/artifact digest, selection-bound deployment intent, Prometheus lifecycle/stage/candidate/worker metrics, and an eight-panel Grafana operations dashboard pass 284 Python, 44 frontend, and 22 desktop/mobile browser scenarios; real run `lifecycle-20260714T035305-1fae6f0f` completed the VisA Airflow data stage and paused before untouched model training with a ready Continue handoff |
| `EVM-261` | Fleet operations overview, multi-model deployment control, and governed custom authoring | Done | 2026-07-W8 | four purpose-based workspaces start from fleet-wide live pipelines, compute, and deployment state; Models reconciles production and scaled-down Kubernetes targets with promotion-ready candidates; custom manifests and immutable digest-pinned EfficientNet components are operator-authored in Pipeline Studio; `portfolio-custom-efficientnet-b0@0.1.0` was persisted on the F drive; 78 Python, 47 frontend, and 14 desktop/mobile core UX scenarios pass |
| `EVM-262` | Real-time host compute telemetry and independent Control Panel synchronization | Done | 2026-07-W8 | the host observer samples CPU, RAM, NVIDIA GPU load, VRAM, temperature, and power every five seconds; typed telemetry is sanitized through the F-drive snapshot and API without granting the API container host GPU access; Overview replaces GPU capacity counts with live utilization gauges, rejects stale values, and polls resources independently from heavy CycleRun sources; 30 backend, 49 frontend, two focused desktop/mobile, and two all-tab visual scenarios pass |
| `EVM-263` | Task Manager-aligned GPU engine utilization semantics | Done | 2026-07-W8 | Windows PDH aggregates per-process counters by physical adapter and engine, then reports the busiest physical GPU engine as the primary load; NVIDIA NVML activity remains a separately labeled secondary metric; live proof showed Windows 3D engine 9.1% while NVML activity was 21%, and 31 backend, 49 frontend, production build, plus desktop/mobile E2E pass |

### Phase 22. Operational Failure Validation And Recovery Evidence

Goal:

- prove detection, containment, recovery, rollback, and prevention for bounded
  operational failures before adding more model domains;
- retain immutable, machine-valid evidence with an honest single-node local
  boundary and no business A/B or production HA claim;
- make each scenario defensible through hiring competencies, interview
  questions, design trade-offs, and factual evidence.

The source plan is
`docs/status/2026-08-01-operational-failure-validation-master-plan.md`.
The Stage 2 development plan is
`docs/status/2026-08-01-stage-2-operational-reliability-development-plan.md`.

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-264` | P0 local runtime recovery | Done | 2026-08-01 | automatic WSL NVIDIA driver discovery, corrected GPU readiness, supervised worker/observer startup and revision injection restored GPU 1/1, device-plugin 1/1, production serving 1/1, real CUDA VisA inference, Prometheus up, live worker/observer, and 57 focused tests; evidence is `docs/status/2026-08-01-p0-local-runtime-recovery.md` |
| `EVM-265` | Operational failure validation master contract | In Progress | Post-W8 | shared evidence/safety/claim contract and five runbooks remain authoritative. Scenarios A-E now have independent local closure evidence with explicit claim boundaries. Cross-scenario correlation and the final VisA operations drill remain open, so the master must not close yet. |
| `EVM-266` | Scenario A GPU and serving failure recovery | Done | Sprint 178 | Jira `SCRUM-172` completion comment `10444`; A0-A8 complete: 49 focused tests and Ruff pass; real production-candidate CUDA CT has 2,181 holdout records and zero overlap; three independent UID-preconditioned B0 Pod recovery runs detected in 0.170-0.200 s, interrupted the endpoint for 9.872-9.904 s, recovered in 10.082-25.087 s, restored exact identity and CUDA inference, and returned the exact Prometheus target to up; the earlier malformed-path run remains immutable RCA evidence |
| `EVM-267` | Scenario B invalid model canary and rollback | Done | Sprint 178 | Jira `SCRUM-173` closure comment `10460`; B0-B7 non-disruptive closure is recorded in `docs/status/2026-08-02-scenario-b-controlled-replay-closure.md`: real under-threshold B7 rejected, strong B7 runtime breach contained, stable and raw CUDA observations each 1,000 with zero raw errors, exact 100/1,000 routing and identity, detection 0.0048-0.0091 s, verified recovery 0.0392-0.0498 s, exact post-replay B0 inference, 16/16 hashes and common live-proof PASS per run. Git/Jira/Notion/Obsidian claim audit is synchronized. Failed attempts remain immutable RCA evidence. Production Kubernetes canary remains separately blocked; no business A/B or HA claim. |
| `EVM-268` | Scenario C quality degradation and retraining gate | Done | Sprint 178 | Non-disruptive closure is recorded in `docs/status/2026-08-02-scenario-c-quality-degradation-closure.md`: real CUDA VisA windows `2,136 / 2,181 / 205`, known-good `within_policy`, deterministic `pcb3` shift `review_required`, decision `37.891 s`, one event/candidate after three duplicate/stale attempts, zero deployment intents, manual hold, `17 / 17` hashes, common live-proof PASS, `62 / 62` tests, and unchanged exact production B0. Scenario E is now independently closed; real candidate training, MLflow, isolated CT and limited release remain cross-scenario/final-drill work and are not claimed complete. |
| `EVM-269` | Scenario D lifecycle supervision recovery | Done | Sprint 178 | Closure is `docs/status/2026-08-02-scenario-d-lifecycle-supervision-closure.md`: 13/13 deterministic fixtures, fenced claims, exact identity and restart controls, live API/Prometheus/Grafana observability, and source-`37ec89d` worker/observer/worker proof. Detection max `5.870 s`, recovery max `9.049 s`, healthy heartbeat p95 max `5.0 s`; every run passes 10/10 postconditions, 9/9 hashes, exact CUDA/production invariants, and common closure. The earlier heartbeat-closure omission remains immutable RCA evidence; local single-node scope is explicit. |
| `EVM-270` | Scenario E data and artifact integrity gate | Done | Sprint 178 | Closure is `docs/status/2026-08-02-scenario-e-data-artifact-integrity-closure.md`: source `89c93a5`, real VisA 10,821 records/23 shards/CT 2,181, canonical 3/3, 14 fixtures and 42/42 deterministic replays, corrupted 39/39 blocked, signed one-hour admission TTL, 36/36 hashes, zero intent delta, exact CUDA/production invariants, live API/Prometheus proof and 395/395 tests. Timestamp-fingerprint failure and the rejected 30-day pre-closure run remain immutable RCA history. Local Ed25519/single-node claim boundaries are explicit. |

### Phase 23. Cross-Scenario Correlation And Recovery Validation

Goal:

- correlate bounded A-E signals without losing exact identity or merging events
  from timing alone;
- enforce E integrity and D control-plane freshness before any A/B/C recovery,
  release, or candidate action;
- prove one fenced recovery owner, deterministic causality and dedupe, complete
  parent/child evidence, and honest local claim boundaries;
- keep implementation, fault injection, and runtime mutation outside the
  planning/design-validation checkpoint.

The source plan is
`docs/status/2026-08-02-cross-scenario-correlation-recovery-validation-plan.md`.
The workstream is separate from Sprint 178 and does not change A-E Done states.

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-271` | Cross-scenario master contract and independent design validation | Done | Post-Sprint 178 | Jira `SCRUM-178` under Epic `SCRUM-177`; versioned contract, exact event/identity/causality/dedupe/ownership rules, five ordered pairs and read-only design remediation produce a planning-only PASS; no implementation or runtime mutation |
| `EVM-272` | Normalized event, identity, correlation, causality, and dedupe engine | Done | Post-Sprint 178 | Jira `SCRUM-179`; source `627209a`, 12 focused/98 A-E/407 full tests, and three 1,000-event replay series passed with 300 unrelated events, zero false merge/duplicate parent/action, p95 `30.183-32.374 ms`, and F-drive hash closure `625/625`; direct-F timeout and SSD-spool/hash-publish trade-off are retained in `docs/status/2026-08-02-cross-scenario-correlation-implementation-closure.md` |
| `EVM-273` | Fail-closed recovery ownership and read-only incident plane | Done | Post-Sprint 178 | Jira `SCRUM-180`; source `c905d7d`, UI truthfulness correction `3467315`, three independent recommendation-only series, zero mutation, 41/41 artifact hash closure, GET-only API, low-cardinality metrics, browser-verified stale state, 423 Python tests and 16 files/51 Control Panel tests; single-host/read-only claim boundary retained in `docs/status/2026-08-02-recovery-coordination-incident-plane-closure.md` |
| `EVM-274` | Non-disruptive pairwise correlation proof | Planned / To Do | Post-Sprint 178 | Jira `SCRUM-181`; D+E, C+E, and B+C each pass three independent isolated/replay runs plus negative anti-merge fixtures, full hash closure, zero production/process/data mutation, and immutable RCA history |
| `EVM-275` | Maintenance-gated live pair and cross-scenario closure | Planned / To Do / approval gated | Post-Sprint 178 | Jira `SCRUM-182`; only after EVM-274 PASS: exact-target A+D then A+B proof under a new single-use maintenance approval, one owner/action, known-good rollback, child SLO plus coordinator-overhead reporting, and explicit single-node limitations |

### Phase 24. Full Lifecycle Guard Integration And VisA Operations Drill

Goal:

- run A-E guards through the actual VisA intake, Torch training, MLflow,
  isolated CT, approval, controlled release, model replacement, CUDA serving
  and monitoring graph;
- prove guard stage placement, fail-closed action, idempotent side-effect outcomes,
  governed resume/rollback, exact identity and no hidden repair;
- keep planning separate from later lifecycle execution and maintenance-gated
  development-production replacement.

The source plan is
`docs/status/2026-08-02-full-lifecycle-guard-integration-validation-plan.md`.

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-276` | Full lifecycle guard master contract and design validation | Done | Post-Sprint 178 | Jira `SCRUM-184` under Epic `SCRUM-183`; golden path, stage/authority matrix, A-E injections, immutable retry, real model policy, SLI/SLO, evidence and UI operator contract passed a planning-only design validation; no implementation or runtime mutation |
| `EVM-277` | Common lifecycle identity envelope, guard dispatcher and golden path | Done | Post-Sprint 178 | Jira `SCRUM-185`; accepted run `lifecycle-20260802T165525-279cf1dc` at `85867e1` passed 11/11 preflight, Airflow 18/18, real CUDA training, MLflow, readiness 13/13, isolated CT 18/18, local-staging approval/deploy, exact CUDA inference and Prometheus; 10/10 stages, 23/23 guard decisions and 8/8 side effects completed; exact production B0 returned 1/1 and staging 0/0; post-run training/CT handoff evidence overwrite was separated and all 437 tests pass |
| `EVM-278` | Scenario E lifecycle data and artifact guards | Done | Post-Sprint 178 | Jira `SCRUM-186`; closure `docs/status/2026-08-02-lifecycle-guard-scenario-e-closure.md`. Source `bc726d9`; real VisA 10,821 records/23 shards; six canonical/corrupt/corrected branches and 18/18 deterministic replays; maximum decision 0.361160 s; zero Kubernetes Job/MLflow/candidate/intent delta; production B0/CUDA/GPU/plugin/supervisor/Prometheus and golden hashes unchanged; evidence 133/133. Controlled local replay only, not production traffic or HA. |
| `EVM-279` | Scenario D idempotent lifecycle continuity and side-effect reconciliation | Done | Post-Sprint 178 | Jira `SCRUM-187`; accepted source `7f253ac` series `scenario-d-training-20260802T202554Z-7f253ace` completed 11/11 checks and 10/10 stages. Exact-worker detection/recovery was `6.9141254 s / 10.0661301 s`; the same Job continued without redispatch. Eight effects were unique/committed; exact delta was Jobs +2, MLflow +1, candidate +1, intent +1. Exact B0 UID/1/1/CUDA/plugin/revision and two distinct Prometheus scrapes restored in `28.9677976 s`; 16/16 hashes and 468/468 tests pass. Controlled local single-node closure only; no HA or distributed exactly-once claim. |
| `EVM-280` | Scenario C lifecycle quality/drift hold and governed resume | Done | Post-Sprint 178 | Jira `SCRUM-188`; source `39d4cd2`, series `scenario-c-lifecycle-20260802T213154Z-39d4cd2e` and run `lifecycle-20260802T213202-1c0776fc` passed 18/18 checks. Hold kept training at attempt 0 with zero downstream effect; single-use independent approval resumed real CUDA training, MLflow and isolated CT, then stopped at release approval with intent 0. Exact delta Jobs +2/MLflow +1/candidate +1; source 17/17 and integrated 21/21 hashes; exact B0 CUDA/plugin/Prometheus restored in 29.0953264 s. Controlled local batch proof only. |
| `EVM-281` | Scenario B lifecycle candidate release and stable rollback | Done | Post-Sprint 178 | Jira `SCRUM-189`; evidence source `1e541de`. Two fresh real lifecycles completed Airflow/CUDA/MLflow/readiness/isolated CT. Quality F1 `0.823529` versus fixed `0.90` produced `rejected_release`; runtime 100/1,000 routing with two errors produced `rolled_back`, 1,000/1,000 identity and zero final allocation. Both approvals returned HTTP 422, intent 0, exact B0 unchanged. Five indexes re-hashed 95/95; 492 tests pass. Three failed attempts remain immutable RCA. No real-user A/B, HA or production claim. |
| `EVM-282` | Scenario A post-promotion GPU serving recovery | Done / fresh live proof PASS | Post-Sprint 178 | Jira `SCRUM-190`; accepted source `d121c9c`, run `scenario-a-lifecycle-20260803T003224Z-d121c9c5-351ae3c7`. M1 apply 18.173018 s, exact Pod detection/recovery 0.2045879/10.0966768 s, interruption 9.8788259 s, M0 rollback 57.634592 s, hashes 38/38. Exact M0/CUDA/Prometheus/plugin restored; prior path failure remains immutable RCA. |
| `EVM-283` | Integrated single-scenario lifecycle evidence closure | Done | Post-Sprint 178 | Jira `SCRUM-191`; accepted E series `scenario-e-integrated-20260803T030435Z-55e9f243` passes actual L2 Airflow-to-integrity block, corrected L4 CUDA/MLflow/readiness/CT and actual L6 HTTP 422 before intent. Delta Jobs +2, MLflow +1, candidate +1, intent 0; 20/20 checks, 32 integrated artifacts, canonical data/B0 unchanged. A-E audit `closure-20260803T032754Z-55e9f243` passes all scenarios, E replay+integrated 165 artifacts and exact runtime restoration. Attempt 1 path-mapping failure remains immutable no-credit RCA. |
| `EVM-284` | Final VisA lifecycle operations drill | Planned / To Do / final gate | Post-Sprint 178 | Jira `SCRUM-192`; after EVM-283 and EVM-274/275: operator-only success, failure, containment, replacement, recovery and rollback series with exact identity and honest local limits |
| `EVM-285` | Full lifecycle guard validation execution ledger | Done | Post-Sprint 178 | Jira `SCRUM-193` under Epic `SCRUM-183`; replacement suite `full-lifecycle-actual-injection-20260803T062614Z-d83a51f0` completed `E -> C -> B -> D -> A` with one fresh run/injection at a time. Fresh A candidate `lifecycle-20260805T104249-7d184e13` completed 10/10 real Airflow/CUDA/MLflow/readiness/CT/approval/staging/Prometheus stages. Exact committed-M1 Pod restart then passed at source `30c68f5`: detection `0.219 s`, interruption `9.860 s`, recovery `10.079 s`, separate M0 rollback `32.774 s`; 38/38 artifacts matched. Final M0 is 1/1 with CUDA, GPU/plugin and singular Prometheus target healthy; active runs 0; device-plugin/data/registry/cluster-wide mutation 0. Suite manifest/index are `84a28605...c61e` / `dff5294c...e705`; all 11 references re-hash. Controlled local single-node evidence only, not HA, zero-downtime production traffic or SLA. |

### Real VLM And LLM Scenario Lifecycle Expansion

| ID | Task | Status | Window | Evidence / Exit Criteria |
|---|---|---|---|---|
| `EVM-286` | Generic scenario workload lifecycle contract and RTX 4080 executor | Done | Post-Sprint 178 | Jira `SCRUM-195` under Epic `SCRUM-194`; exact identity/state, fail-closed quality views, fenced cross-process GPU lease, real runner, MLflow/artifact seal, staging server and Prometheus closure are implemented. Accepted VLM/LLM runs each completed 10/10 stages and released the lease. |
| `EVM-287` | Real ScienceQA SmolVLM lifecycle validation | Done | Post-Sprint 178 | Jira `SCRUM-196`; accepted run `scenario-workload-20260805T121300-966b6bc1` used pinned real SmolVLM weights, 48 real image-text records, BF16 LoRA, MLflow, held-out evaluation, real CUDA staging inference and Prometheus. Evidence index `624fbbad...2d0393`; test-derived split is not a benchmark. |
| `EVM-288` | Governed Dolly Qwen LLM lifecycle validation | Done | Post-Sprint 178 | Jira `SCRUM-197`; approved 320-record filtered view, pinned Qwen, real NF4 int4 QLoRA, MLflow, held-out generation evaluation, real CUDA staging inference and Prometheus passed in `scenario-workload-20260805T121811-dcee8c89`. Evidence `488e958c...0f0653`; automated PII filtering is not a privacy audit. |
| `EVM-289` | Cross-family deployment, observability and evidence closure | Done | Post-Sprint 178 | Jira `SCRUM-198`; read-only API and `Build -> AI Workloads` expose two completed and two immutable failed runs with truthful stage progress, RCA, exact model/data/source/artifact identity, quantization, GPU profile and MLflow lineage. API/workload tests 17/17, API contract 13/13, UI 54/54, lint/build and desktop/mobile browser checks pass. |

The versioned execution contract is
`docs/status/2026-08-05-real-vlm-llm-scenario-lifecycle-expansion.md`.
The workloads are serialized `VLM -> teardown -> LLM`; the existing production
B0, device plugin and canonical source data are outside the mutation scope.

The original independent-scenario dependency order was
`A/D -> B -> E -> C -> cross-scenario`; EVM-278..283 retain that historical
evidence. EVM-285 is a separate fresh integration suite ordered
`E -> C -> B -> D -> A`, with one intentional injection per LifecycleRun. Its
first post-migration E re-seal remains an immutable failed attempt; the second
fresh E re-seal and fresh C/B/D/A runs are accepted in the completed suite.
Independent scenario closure does not imply a
combined release drill, automatic promotion, customer production, or HA.

### Distributed Scale And Operational Load Validation

| Scenario | ID | Task | Status | Window | Evidence / Exit Criteria |
|---|---|---|---|---|---|
| `S0` | `EVM-290` | Runtime Baseline & Evidence Contract | Verified | Post-W8 | three fresh 60-second fixed-rate controls at 0.05 RPS, deterministic seed, complete cross-runtime trace, bounded telemetry and canonical Git-byte evidence passed; local single-node baseline only |
| `S1` | `EVM-292` | Transactional Job State & Idempotency | Verified | Post-W8 | Jira `SCRUM-202`, strict closure comment `10607`; Notion canonical page `3bc10ad2-dcad-81ec-9174-d9686d5d1957`; Obsidian canonical work log and Current Context updated. Implementation revision `8a8f54c`. External TCP/HTTP create/approve/cancel/retry sweeps reached measured client/server peak in-flight `100/100`, `250/250`, and `500/500`; all requests completed with legal conflict outcomes and complete trace identity. A one-connection API pool returned `12/12` bounded `503` responses within `0.328 s`. The exact epoch-1 worker PID was terminated; a different supervised process claimed epoch 2, fenced the stale owner, committed lifecycle/deployment/artifact effects exactly once each with duplicates `0`, and reconciled an injected PostgreSQL-to-JSON mirror gap to payload/version parity within `9.875 s`. `622` general, `7` real-PostgreSQL, `57` lifecycle, and `20` S0 regression tests passed. The prior concurrency-64 proof is historical only. Local single-node proof; not database HA, production traffic, DR, or SLA. |
| `S2` | `EVM-293` | Bounded Queue & Backpressure | Verified | Post-W8 | Jira `SCRUM-203` Done, corrected closure comment `10613`; exercised revision `e5b399a`, closure revision `c0483e7`. The frozen A-J external matrix passed `30/30` profile repetitions through Uvicorn TCP/HTTP, isolated PostgreSQL 16 schemas, the real queue-worker process, deterministic Airflow-compatible HTTP, Prometheus file_sd, per-task W3C OTLP, exact worker PID recovery, and trusted CUDA. `S2-AC-01..04` and readiness gates `11/11` passed; `61` focused real-PostgreSQL/S2, `692` full Python, `50` lifecycle/host, `28` S0/S1 evidence, and `59` Control Panel tests plus the production frontend build passed. Runtime and closure public hashes are `3ad2c517...a7cd` and `dc9da8e7...00d8`; all `20` public and `4` embedded artifacts rehashed from canonical Git bytes. Five retained RCA categories cover short-lived executor RSS sampling, a Windows heartbeat replacement lock, ambiguous downstream ownership with queue-wait projection loss, expiry-fixture clock skew, and timeout-evidence misclassification. All accepted scopes drained and cleaned up. Local single-node controlled proof only; not customer traffic, production SLA, physical-node HA, DR, or multi-GPU evidence. |
| `S3` | `EVM-291` | HIGGS Lightweight Capacity Envelope | Verified | Post-W8 | Jira `SCRUM-201`. Strict reclosure preserved `111` accepted point repetitions, `19` guardrail skips, and four failed-attempt RCA. Python 3.11/3.12/3.13 produced the same `77aba704...350` analysis hash; runtime/config/analysis paths are bound to resolvable Git revisions, blob OIDs, ancestry, and canonical Git-byte SHA-256. Raw-derived `S3-AC-01..04` passed. A three-repetition current-revision external smoke completed `12,853` requests with `130/130` sampled trace chains, p99 `9.90 ms`, healthy Prometheus targets, terminal drain, and exact cleanup. All `592` private artifacts (`364,787,238` bytes) rehashed with zero mismatch; a trusted single-GPU CUDA regression recorded nonzero activity. `741` Python tests passed with real PostgreSQL and one environment-specific CUDA test skipped in favor of the separate mandatory CUDA run; `50` lifecycle/host and `59` Control Panel tests plus the production frontend build passed. Local single-node controlled evidence only; not production/customer traffic, SLA, physical-node HA, DR, or multi-GPU evidence. |
| `S4` | `EVM-294` | HIGGS Tiny MLP GPU Batching | Implementing | Post-W8 | no-catch-up pacing now measures delivery fidelity; an integrated post-saturation 80 RPS confirmation failed despite an earlier standalone calibration, while three 60 RPS calibration repeats passed fixed p99/queue SLO, trace, OOM, drain and cleanup gates. A 60-second quiet GPU recovery gate and 60 RPS ceiling are frozen; fresh full closure remains pending |
| `S5` | `EVM-296` | Criteo Spark Memory-bounded Data Scale | Planned | Post-W8 | staged click-log subsets compare single-process, local Spark and Kubernetes executors with bounded memory, shuffle/spill/skew, retry and digest closure |
| `S6` | `EVM-295` | API Rolling Continuity & GPU Controlled Handoff | Planned | Post-W8 | multi-replica API rolling has accepted loss/duplicate 0; single-GPU switch reports measured interruption and exact rollback identity without HA overclaim |
| `S7` | `EVM-297` | Image/VLM/LLM Auxiliary Admission | Planned | Post-W8 | sequential image/generative probes validate family-specific p95/p99, quality, decode/pixel/token admission, fairness, OOM 0 and starvation 0 |
| `S8` | `EVM-298` | Dependency Soak & Resource-efficiency Closure | Planned | Post-W8 | bounded dependency faults and 30-60 minute soak quantify retry amplification, MTTR, resource slopes, efficiency, residual risk and final hashes |

The canonical planning contract is
`docs/agenda/2026-08-15-distributed-scale-operational-validation-plan-v3.md`.
EVM-290, EVM-291, EVM-292, and EVM-293 are verified from canonical evidence.
EVM-294 is implementing from hash-linked preparation, seven retained RCA checkpoints,
and a non-acceptance three-repeat pacing calibration. A fresh full rerun is still required
before acceptance credit. EVM-295..298
remain planning-only.
No benchmark, load, scale, HA, shared-GPU, or production-readiness acceptance is
credited until fresh runtime evidence satisfies the corresponding exit criteria.

### Current Jira Timebox And Hierarchy

- Sprint `144` was closed on `2026-08-01 23:06:25 KST` after unresolved
  `SCRUM-49..52` and `SCRUM-144` were returned to the backlog with status,
  parent, and closed-sprint history preserved.
- Sprint `178`, `EVM S2 A-E 2026-08-01~08-02`, is active from
  `2026-08-01 23:07:32 KST` to `2026-08-02 23:59 KST`.
- Jira requires scenario subtasks `SCRUM-172..176` to inherit sprint membership
  from parent `SCRUM-171`; the hierarchy and work statuses remain unchanged.
- The sprint was defined as a readiness, implementation-start, and
  non-disruptive validation timebox. Scenario A received separate maintenance
  approval; A-E are now independently complete from their own evidence. This
  does not complete cross-scenario or final end-to-end work.
- Roadmap hierarchy is now explicit: `SCRUM-123..127 -> SCRUM-119`,
  `SCRUM-128..130 -> SCRUM-120`, `SCRUM-131..133 -> SCRUM-121`, and
  `SCRUM-134..135 -> SCRUM-122`.
- Full audit evidence is in
  `docs/status/2026-08-01-stage-2-jira-sprint-realignment.md`.

## Jira Mapping

Jira live sync 기준 mapping이다.

| Git Issue ID | Jira Key | Jira Type | Link |
|---|---|---|---|
| `EVM-EPIC-02` | `SCRUM-6` | Epic | https://opop0236.atlassian.net/browse/SCRUM-6 |
| `EVM-EPIC-03` | `SCRUM-7` | Epic | https://opop0236.atlassian.net/browse/SCRUM-7 |
| `EVM-EPIC-04` | `SCRUM-8` | Epic | https://opop0236.atlassian.net/browse/SCRUM-8 |
| `EVM-EPIC-05` | `SCRUM-9` | Epic | https://opop0236.atlassian.net/browse/SCRUM-9 |
| `EVM-EPIC-06` | `SCRUM-10` | Epic | https://opop0236.atlassian.net/browse/SCRUM-10 |
| `EVM-EPIC-07` | `SCRUM-11` | Epic | https://opop0236.atlassian.net/browse/SCRUM-11 |
| `EVM-EPIC-08` | `SCRUM-12` | Epic | https://opop0236.atlassian.net/browse/SCRUM-12 |
| `EVM-021` | `SCRUM-5` | Task | https://opop0236.atlassian.net/browse/SCRUM-5 |
| `EVM-022` | `SCRUM-13` | Task | https://opop0236.atlassian.net/browse/SCRUM-13 |
| `EVM-023-A` | `SCRUM-14` | Task | https://opop0236.atlassian.net/browse/SCRUM-14 |
| `EVM-023-B` | `SCRUM-15` | Task | https://opop0236.atlassian.net/browse/SCRUM-15 |
| `EVM-DOC-021` | `SCRUM-16` | Task | https://opop0236.atlassian.net/browse/SCRUM-16 |
| `EVM-023` | `SCRUM-17` | Task | https://opop0236.atlassian.net/browse/SCRUM-17 |
| `EVM-024` | `SCRUM-18` | Task | https://opop0236.atlassian.net/browse/SCRUM-18 |
| `EVM-025` | `SCRUM-19` | Task | https://opop0236.atlassian.net/browse/SCRUM-19 |
| `EVM-023-C` | `SCRUM-20` | Task | https://opop0236.atlassian.net/browse/SCRUM-20 |
| `EVM-023-D` | `SCRUM-21` | Task | https://opop0236.atlassian.net/browse/SCRUM-21 |
| `EVM-023-E` | `SCRUM-22` | Task | https://opop0236.atlassian.net/browse/SCRUM-22 |
| `EVM-026` | `SCRUM-23` | Task | https://opop0236.atlassian.net/browse/SCRUM-23 |
| `EVM-027` | `SCRUM-24` | Task | https://opop0236.atlassian.net/browse/SCRUM-24 |
| `EVM-031` | `SCRUM-25` | Task | https://opop0236.atlassian.net/browse/SCRUM-25 |
| `EVM-032` | `SCRUM-26` | Task | https://opop0236.atlassian.net/browse/SCRUM-26 |
| `EVM-033` | `SCRUM-27` | Task | https://opop0236.atlassian.net/browse/SCRUM-27 |
| `EVM-034` | `SCRUM-28` | Task | https://opop0236.atlassian.net/browse/SCRUM-28 |
| `EVM-035` | `SCRUM-29` | Task | https://opop0236.atlassian.net/browse/SCRUM-29 |
| `EVM-036` | `SCRUM-30` | Task | https://opop0236.atlassian.net/browse/SCRUM-30 |
| `EVM-041` | `SCRUM-31` | Task | https://opop0236.atlassian.net/browse/SCRUM-31 |
| `EVM-042` | `SCRUM-32` | Task | https://opop0236.atlassian.net/browse/SCRUM-32 |
| `EVM-044` | `SCRUM-33` | Task | https://opop0236.atlassian.net/browse/SCRUM-33 |
| `EVM-045` | `SCRUM-34` | Task | https://opop0236.atlassian.net/browse/SCRUM-34 |
| `EVM-051` | `SCRUM-35` | Task | https://opop0236.atlassian.net/browse/SCRUM-35 |
| `EVM-052` | `SCRUM-36` | Task | https://opop0236.atlassian.net/browse/SCRUM-36 |
| `EVM-053` | `SCRUM-37` | Task | https://opop0236.atlassian.net/browse/SCRUM-37 |
| `EVM-054` | `SCRUM-38` | Task | https://opop0236.atlassian.net/browse/SCRUM-38 |
| `EVM-055` | `SCRUM-39` | Task | https://opop0236.atlassian.net/browse/SCRUM-39 |
| `EVM-061` | `SCRUM-40` | Task | https://opop0236.atlassian.net/browse/SCRUM-40 |
| `EVM-062` | `SCRUM-41` | Task | https://opop0236.atlassian.net/browse/SCRUM-41 |
| `EVM-063` | `SCRUM-42` | Task | https://opop0236.atlassian.net/browse/SCRUM-42 |
| `EVM-065` | `SCRUM-43` | Task | https://opop0236.atlassian.net/browse/SCRUM-43 |
| `EVM-071` | `SCRUM-44` | Task | https://opop0236.atlassian.net/browse/SCRUM-44 |
| `EVM-072` | `SCRUM-45` | Task | https://opop0236.atlassian.net/browse/SCRUM-45 |
| `EVM-073` | `SCRUM-46` | Task | https://opop0236.atlassian.net/browse/SCRUM-46 |
| `EVM-074` | `SCRUM-47` | Task | https://opop0236.atlassian.net/browse/SCRUM-47 |
| `EVM-075` | `SCRUM-48` | Task | https://opop0236.atlassian.net/browse/SCRUM-48 |
| `EVM-DOC-031` | `SCRUM-49` | Task | https://opop0236.atlassian.net/browse/SCRUM-49 |
| `EVM-DOC-032` | `SCRUM-50` | Task | https://opop0236.atlassian.net/browse/SCRUM-50 |
| `EVM-QA-001` | `SCRUM-51` | Task | https://opop0236.atlassian.net/browse/SCRUM-51 |
| `EVM-QA-002` | `SCRUM-52` | Task | https://opop0236.atlassian.net/browse/SCRUM-52 |
| `EVM-130` | `SCRUM-56` | Task | https://opop0236.atlassian.net/browse/SCRUM-56 |
| `EVM-131` | `SCRUM-57` | Task | https://opop0236.atlassian.net/browse/SCRUM-57 |
| `EVM-132` | `SCRUM-58` | Task | https://opop0236.atlassian.net/browse/SCRUM-58 |
| `EVM-133` | `SCRUM-59` | Task | https://opop0236.atlassian.net/browse/SCRUM-59 |
| `EVM-EPIC-13` | `SCRUM-60` | Epic | https://opop0236.atlassian.net/browse/SCRUM-60 |
| `EVM-EPIC-14` | `SCRUM-61` | Epic | https://opop0236.atlassian.net/browse/SCRUM-61 |
| `EVM-EPIC-15` | `SCRUM-62` | Epic | https://opop0236.atlassian.net/browse/SCRUM-62 |
| `EVM-EPIC-16` | `SCRUM-63` | Epic | https://opop0236.atlassian.net/browse/SCRUM-63 |
| `EVM-EPIC-17` | `SCRUM-64` | Epic | https://opop0236.atlassian.net/browse/SCRUM-64 |
| `EVM-EPIC-18` | `SCRUM-98` | Epic | https://opop0236.atlassian.net/browse/SCRUM-98 |
| `EVM-134` | `SCRUM-65` | Task | https://opop0236.atlassian.net/browse/SCRUM-65 |
| `EVM-135` | `SCRUM-66` | Task | https://opop0236.atlassian.net/browse/SCRUM-66 |
| `EVM-136` | `SCRUM-91` | Task | https://opop0236.atlassian.net/browse/SCRUM-91 |
| `EVM-141` | `SCRUM-67` | Task | https://opop0236.atlassian.net/browse/SCRUM-67 |
| `EVM-142` | `SCRUM-68` | Task | https://opop0236.atlassian.net/browse/SCRUM-68 |
| `EVM-143` | `SCRUM-69` | Task | https://opop0236.atlassian.net/browse/SCRUM-69 |
| `EVM-144` | `SCRUM-70` | Task | https://opop0236.atlassian.net/browse/SCRUM-70 |
| `EVM-151` | `SCRUM-71` | Task | https://opop0236.atlassian.net/browse/SCRUM-71 |
| `EVM-152` | `SCRUM-72` | Task | https://opop0236.atlassian.net/browse/SCRUM-72 |
| `EVM-161` | `SCRUM-73` | Task | https://opop0236.atlassian.net/browse/SCRUM-73 |
| `EVM-162` | `SCRUM-74` | Task | https://opop0236.atlassian.net/browse/SCRUM-74 |
| `EVM-171` | `SCRUM-75` | Task | https://opop0236.atlassian.net/browse/SCRUM-75 |
| `EVM-181` | `SCRUM-76` | Task | https://opop0236.atlassian.net/browse/SCRUM-76 |
| `EVM-191` | `SCRUM-77` | Task | https://opop0236.atlassian.net/browse/SCRUM-77 |
| `EVM-192` | `SCRUM-78` | Task | https://opop0236.atlassian.net/browse/SCRUM-78 |
| `EVM-193` | `SCRUM-79` | Task | https://opop0236.atlassian.net/browse/SCRUM-79 |
| `EVM-194` | `SCRUM-80` | Task | https://opop0236.atlassian.net/browse/SCRUM-80 |
| `EVM-195` | `SCRUM-81` | Task | https://opop0236.atlassian.net/browse/SCRUM-81 |
| `EVM-196` | `SCRUM-92` | Task | https://opop0236.atlassian.net/browse/SCRUM-92 |
| `EVM-197` | `SCRUM-93` | Task | https://opop0236.atlassian.net/browse/SCRUM-93 |
| `EVM-198` | `SCRUM-94` | Task | https://opop0236.atlassian.net/browse/SCRUM-94 |
| `EVM-199` | `SCRUM-95` | Task | https://opop0236.atlassian.net/browse/SCRUM-95 |
| `EVM-200` | `SCRUM-96` | Task | https://opop0236.atlassian.net/browse/SCRUM-96 |
| `EVM-201` | `SCRUM-82` | Task | https://opop0236.atlassian.net/browse/SCRUM-82 |
| `EVM-202` | `SCRUM-83` | Task | https://opop0236.atlassian.net/browse/SCRUM-83 |
| `EVM-203` | `SCRUM-84` | Task | https://opop0236.atlassian.net/browse/SCRUM-84 |
| `EVM-204` | `SCRUM-85` | Task | https://opop0236.atlassian.net/browse/SCRUM-85 |
| `EVM-205` | `SCRUM-86` | Task | https://opop0236.atlassian.net/browse/SCRUM-86 |
| `EVM-211` | `SCRUM-87` | Task | https://opop0236.atlassian.net/browse/SCRUM-87 |
| `EVM-212` | `SCRUM-88` | Task | https://opop0236.atlassian.net/browse/SCRUM-88 |
| `EVM-213` | `SCRUM-89` | Task | https://opop0236.atlassian.net/browse/SCRUM-89 |
| `EVM-214` | `SCRUM-90` | Task | https://opop0236.atlassian.net/browse/SCRUM-90 |
| `EVM-215` | `SCRUM-136` | Task | https://opop0236.atlassian.net/browse/SCRUM-136 |
| `EVM-221` | `SCRUM-99` | Task | https://opop0236.atlassian.net/browse/SCRUM-99 |
| `EVM-222` | `SCRUM-100` | Task | https://opop0236.atlassian.net/browse/SCRUM-100 |
| `EVM-223` | `SCRUM-101` | Task | https://opop0236.atlassian.net/browse/SCRUM-101 |
| `EVM-224` | `SCRUM-102` | Task | https://opop0236.atlassian.net/browse/SCRUM-102 |
| `EVM-225` | `SCRUM-103` | Task | https://opop0236.atlassian.net/browse/SCRUM-103 |
| `EVM-226` | `SCRUM-104` | Task | https://opop0236.atlassian.net/browse/SCRUM-104 |
| `EVM-227` | `SCRUM-105` | Task | https://opop0236.atlassian.net/browse/SCRUM-105 |
| `EVM-228` | `SCRUM-106` | Task | https://opop0236.atlassian.net/browse/SCRUM-106 |
| `EVM-229` | `SCRUM-107` | Task | https://opop0236.atlassian.net/browse/SCRUM-107 |
| `EVM-230` | `SCRUM-108` | Task | https://opop0236.atlassian.net/browse/SCRUM-108 |
| `EVM-231` | `SCRUM-109` | Task | https://opop0236.atlassian.net/browse/SCRUM-109 |
| `EVM-232` | `SCRUM-110` | Task | https://opop0236.atlassian.net/browse/SCRUM-110 |
| `EVM-233` | `SCRUM-111` | Task | https://opop0236.atlassian.net/browse/SCRUM-111 |
| `EVM-234` | `SCRUM-112` | Task | https://opop0236.atlassian.net/browse/SCRUM-112 |
| `EVM-235` | `SCRUM-113` | Task | https://opop0236.atlassian.net/browse/SCRUM-113 |
| `EVM-236` | `SCRUM-114` | Task | https://opop0236.atlassian.net/browse/SCRUM-114 |
| `EVM-237` | `SCRUM-115` | Task | https://opop0236.atlassian.net/browse/SCRUM-115 |
| `EVM-238` | `SCRUM-116` | Task | https://opop0236.atlassian.net/browse/SCRUM-116 |
| `EVM-238-A` | `SCRUM-117` | Task | https://opop0236.atlassian.net/browse/SCRUM-117 |
| `EVM-238-B` | `SCRUM-118` | Task | https://opop0236.atlassian.net/browse/SCRUM-118 |
| `EVM-EPIC-19` | `SCRUM-138` | Epic | https://opop0236.atlassian.net/browse/SCRUM-138 |
| `EVM-239` | `SCRUM-139` | Task | https://opop0236.atlassian.net/browse/SCRUM-139 |
| `EVM-240` | `SCRUM-140` | Task | https://opop0236.atlassian.net/browse/SCRUM-140 |
| `EVM-241` | `SCRUM-141` | Task | https://opop0236.atlassian.net/browse/SCRUM-141 |
| `EVM-242` | `SCRUM-142` | Task | https://opop0236.atlassian.net/browse/SCRUM-142 |
| `EVM-243` | `SCRUM-143` | Task | https://opop0236.atlassian.net/browse/SCRUM-143 |
| `EVM-244` | `SCRUM-144` | Task | https://opop0236.atlassian.net/browse/SCRUM-144 |
| `EVM-EPIC-20` | `SCRUM-146` | Epic | https://opop0236.atlassian.net/browse/SCRUM-146 |
| `EVM-245` | `SCRUM-150` | Task | https://opop0236.atlassian.net/browse/SCRUM-150 |
| `EVM-246` | `SCRUM-151` | Task | https://opop0236.atlassian.net/browse/SCRUM-151 |
| `EVM-247` | `SCRUM-152` | Task | https://opop0236.atlassian.net/browse/SCRUM-152 |
| `EVM-248` | `SCRUM-153` | Task | https://opop0236.atlassian.net/browse/SCRUM-153 |
| `EVM-249` | `SCRUM-154` | Task | https://opop0236.atlassian.net/browse/SCRUM-154 |
| `EVM-250` | `SCRUM-155` | Task | https://opop0236.atlassian.net/browse/SCRUM-155 |
| `EVM-EPIC-21` | `SCRUM-156` | Epic | https://opop0236.atlassian.net/browse/SCRUM-156 |
| `EVM-251` | `SCRUM-157` | Task | https://opop0236.atlassian.net/browse/SCRUM-157 |
| `EVM-252` | `SCRUM-158` | Task | https://opop0236.atlassian.net/browse/SCRUM-158 |
| `EVM-253` | `SCRUM-159` | Task | https://opop0236.atlassian.net/browse/SCRUM-159 |
| `EVM-254` | `SCRUM-160` | Task | https://opop0236.atlassian.net/browse/SCRUM-160 |
| `EVM-255` | `SCRUM-161` | Task | https://opop0236.atlassian.net/browse/SCRUM-161 |
| `EVM-256` | `SCRUM-162` | Task | https://opop0236.atlassian.net/browse/SCRUM-162 |
| `EVM-257` | `SCRUM-163` | Task | https://opop0236.atlassian.net/browse/SCRUM-163 |
| `EVM-258` | `SCRUM-164` | Task | https://opop0236.atlassian.net/browse/SCRUM-164 |
| `EVM-259` | `SCRUM-165` | Task | https://opop0236.atlassian.net/browse/SCRUM-165 |
| `EVM-260` | `SCRUM-166` | Task | https://opop0236.atlassian.net/browse/SCRUM-166 |
| `EVM-261` | `SCRUM-167` | Task | https://opop0236.atlassian.net/browse/SCRUM-167 |
| `EVM-262` | `SCRUM-168` | Task | https://opop0236.atlassian.net/browse/SCRUM-168 |
| `EVM-263` | `SCRUM-169` | Task | https://opop0236.atlassian.net/browse/SCRUM-169 |
| `EVM-264` | `SCRUM-170` | Task | https://opop0236.atlassian.net/browse/SCRUM-170 |
| `EVM-265` | `SCRUM-171` | Task | https://opop0236.atlassian.net/browse/SCRUM-171 |
| `EVM-266` | `SCRUM-172` | Subtask | https://opop0236.atlassian.net/browse/SCRUM-172 |
| `EVM-267` | `SCRUM-173` | Subtask | https://opop0236.atlassian.net/browse/SCRUM-173 |
| `EVM-268` | `SCRUM-174` | Subtask | https://opop0236.atlassian.net/browse/SCRUM-174 |
| `EVM-269` | `SCRUM-175` | Subtask | https://opop0236.atlassian.net/browse/SCRUM-175 |
| `EVM-270` | `SCRUM-176` | Subtask | https://opop0236.atlassian.net/browse/SCRUM-176 |
| `EVM-EPIC-22` | `SCRUM-177` | Epic | https://opop0236.atlassian.net/browse/SCRUM-177 |
| `EVM-271` | `SCRUM-178` | Task | https://opop0236.atlassian.net/browse/SCRUM-178 |
| `EVM-272` | `SCRUM-179` | Task | https://opop0236.atlassian.net/browse/SCRUM-179 |
| `EVM-273` | `SCRUM-180` | Task | https://opop0236.atlassian.net/browse/SCRUM-180 |
| `EVM-274` | `SCRUM-181` | Task | https://opop0236.atlassian.net/browse/SCRUM-181 |
| `EVM-275` | `SCRUM-182` | Task | https://opop0236.atlassian.net/browse/SCRUM-182 |
| `EVM-EPIC-23` | `SCRUM-183` | Epic | https://opop0236.atlassian.net/browse/SCRUM-183 |
| `EVM-276` | `SCRUM-184` | Task | https://opop0236.atlassian.net/browse/SCRUM-184 |
| `EVM-277` | `SCRUM-185` | Task | https://opop0236.atlassian.net/browse/SCRUM-185 |
| `EVM-278` | `SCRUM-186` | Task | https://opop0236.atlassian.net/browse/SCRUM-186 |
| `EVM-279` | `SCRUM-187` | Task | https://opop0236.atlassian.net/browse/SCRUM-187 |
| `EVM-280` | `SCRUM-188` | Task | https://opop0236.atlassian.net/browse/SCRUM-188 |
| `EVM-281` | `SCRUM-189` | Task | https://opop0236.atlassian.net/browse/SCRUM-189 |
| `EVM-282` | `SCRUM-190` | Task | https://opop0236.atlassian.net/browse/SCRUM-190 |
| `EVM-283` | `SCRUM-191` | Task | https://opop0236.atlassian.net/browse/SCRUM-191 |
| `EVM-284` | `SCRUM-192` | Task | https://opop0236.atlassian.net/browse/SCRUM-192 |
| `EVM-285` | `SCRUM-193` | Task | https://opop0236.atlassian.net/browse/SCRUM-193 |
| `EVM-EPIC-24` | `SCRUM-194` | Epic | https://opop0236.atlassian.net/browse/SCRUM-194 |
| `EVM-286` | `SCRUM-195` | Task | https://opop0236.atlassian.net/browse/SCRUM-195 |
| `EVM-287` | `SCRUM-196` | Task | https://opop0236.atlassian.net/browse/SCRUM-196 |
| `EVM-288` | `SCRUM-197` | Task | https://opop0236.atlassian.net/browse/SCRUM-197 |
| `EVM-289` | `SCRUM-198` | Task | https://opop0236.atlassian.net/browse/SCRUM-198 |
| `EVM-EPIC-25` | `SCRUM-199` | Epic | https://opop0236.atlassian.net/browse/SCRUM-199 |
| `EVM-290` | `SCRUM-200` | Task | https://opop0236.atlassian.net/browse/SCRUM-200 |
| `EVM-291` | `SCRUM-201` | Task | https://opop0236.atlassian.net/browse/SCRUM-201 |
| `EVM-292` | `SCRUM-202` | Task | https://opop0236.atlassian.net/browse/SCRUM-202 |
| `EVM-293` | `SCRUM-203` | Task | https://opop0236.atlassian.net/browse/SCRUM-203 |
| `EVM-294` | `SCRUM-204` | Task | https://opop0236.atlassian.net/browse/SCRUM-204 |
| `EVM-295` | `SCRUM-205` | Task | https://opop0236.atlassian.net/browse/SCRUM-205 |
| `EVM-296` | `SCRUM-206` | Task | https://opop0236.atlassian.net/browse/SCRUM-206 |
| `EVM-297` | `SCRUM-207` | Task | https://opop0236.atlassian.net/browse/SCRUM-207 |
| `EVM-298` | `SCRUM-208` | Task | https://opop0236.atlassian.net/browse/SCRUM-208 |
| `EVM-123` | `SCRUM-137` | Task | https://opop0236.atlassian.net/browse/SCRUM-137 |
| `EVM-BUG-002` | `SCRUM-53` | Bug | https://opop0236.atlassian.net/browse/SCRUM-53 |
| `EVM-BUG-003` | `SCRUM-54` | Bug | https://opop0236.atlassian.net/browse/SCRUM-54 |
| `EVM-BUG-004` | `SCRUM-55` | Bug | Jira: https://opop0236.atlassian.net/browse/SCRUM-55<br>GitHub: https://github.com/ruma0236/ML_ServeAPI/issues/4 |

## Bug Register

Bug는 발견 즉시 이 섹션에 추가하거나 Jira/GitHub Issue로 먼저 생성한 뒤 역으로 기록한다.
GitHub Issue가 없는 경우에도 Jira issue, branch, commit, status 문서로 추적성을 유지한다.

| ID | Title | Status | Tracking Link | Root Cause |
|---|---|---|---|---|
| `EVM-BUG-001` | sample edit breaks data validation dimensions | Closed | https://github.com/ruma0236/ML_ServeAPI/issues/1 | Controlled workflow test; no production code change required |
| `EVM-BUG-002` | Airflow metadata DB conflicts with MLflow Postgres alembic revision | Closed | Jira: https://opop0236.atlassian.net/browse/SCRUM-53<br>GitHub: https://github.com/ruma0236/ML_ServeAPI/issues/2 | Airflow and MLflow shared the same metadata DB; split Airflow into a dedicated Postgres service |
| `EVM-BUG-003` | Airflow manual smoke run created DagRun without task instances | Closed | Jira: https://opop0236.atlassian.net/browse/SCRUM-54<br>GitHub: https://github.com/ruma0236/ML_ServeAPI/issues/3 | DAG `start_date` was later than the manual smoke logical date; moved it to `2026-06-01` |
| `EVM-BUG-004` | Airflow trace metadata misses Git commit when stack starts without injected env | Closed | Jira: https://opop0236.atlassian.net/browse/SCRUM-55<br>GitHub: https://github.com/ruma0236/ML_ServeAPI/issues/4 | Local start path relied on manual `EVM_GIT_COMMIT`/`EVM_GIT_BRANCH`; added `scripts/dev/start_local_stack.ps1` |

Automation status:

- `scripts/dev/github_issue.py` supports create, comment, and resolve/close.
- `docs/status/2026-06-21-github-issue-automation.md` records dry-run validation.

## Status Values

| Status | Meaning |
|---|---|
| `Next` | 바로 다음 작업 |
| `Planned` | 계획됨 |
| `Deferred` | 7월 cut 이후 고도화 대상으로 연기 |
| `In Progress` | 작업 중 |
| `Blocked` | 외부 조건 또는 결정 필요 |
| `Done` | 구현, 검증, 문서화, commit 완료 |

## Operating Rule

각 issue는 완료 시 다음 evidence 중 최소 2개를 남긴다.

- code/config commit
- pipeline output or command result
- `docs/status/YYYY-MM-DD-*.md`
- updated runbook
- updated architecture or pipeline document
