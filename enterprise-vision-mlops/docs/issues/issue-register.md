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
| `EVM-EPIC-17` | Draft Decision And AgentOps Governance | 2026-07-W7 | Planned | draft/proposal registry, decision trail, AgentOps reliability design, and final portfolio hardening |
| `EVM-EPIC-18` | Kubernetes Runtime And MLOps Control Panel | 2026-07-W7 | In Progress | Kubernetes runtime foundation, metadata/control API, animated visual cycle panel, resource control, task assignment, and serving-scale handoff |

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
| `EVM-211` | Draft and decision registry | Planned | 2026-07-W7 | experiments, prompt changes, model candidates, and eval policies have draft/review/approved/rejected states |
| `EVM-212` | AgentOps reliability follow-up design | Planned | 2026-07-W7 | LLM agent, LangGraph, HITL, tool-call audit, and recovery scenarios are scoped for post-MVP work |
| `EVM-213` | Scale serving research plan | Planned | 2026-07-W7 | vLLM, Triton, KServe, Ray Serve, and Kueue are compared for the next production-serving path |
| `EVM-214` | July portfolio stabilization review | Planned | 2026-07-W7 | final demo evidence, known gaps, post-MVP backlog, and handoff risks are consolidated |

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
| `EVM-224` | Cycle lineage aggregation API | Planned | 2026-07-W7 | backend endpoint aggregates registry, lifecycle, dataset, Airflow/MLflow references, Prometheus serving status, artifact links, tenant/environment scope, drift state, and CD/CT gate state for one cycle |
| `EVM-225` | MLOps Control Panel v0 | Planned | 2026-07-W7 | UI provides tabbed/depth navigation for Kubernetes control, pipeline control, resource management, lineage, artifacts, cycle detail, dataset/model cards, promotion blockers, drift/CDCT gates, tenant/environment scope, and serving state |
| `EVM-226` | Kubernetes local real execution proof | Planned | 2026-07-W7 | local Kubernetes/k3s/kind run proves at least one API or pipeline job path with real configured inputs, documented commands, screenshots/logs, and no smoke-only completion claim |
| `EVM-227` | GPU/VLM serving deployment design | Planned | 2026-07-W7 | Windows RTX, Mac mini evaluator, KServe/Triton/vLLM/Ray Serve options, and GPU scheduling constraints are compared for next implementation |
| `EVM-228` | Compressed W6/W7 integration review | Planned | 2026-07-W7 | Git/Jira/Notion/Obsidian evidence confirms W6/W7 scope, enterprise-readiness checklist, remaining risks, and next handoff by the 2026-07-15 target |
| `EVM-229` | Kubernetes resource topology and animation UI | Planned | 2026-07-W7 | UI visualizes namespace/node/pod/job/service/PVC/GPU state with readable animated transitions and drilldowns for allocation, pressure, restarts, and readiness |
| `EVM-230` | Airflow and MLflow task authoring and assignment UI | Planned | 2026-07-W7 | UI can draft, edit, assign, validate, and queue pipeline tasks with owner, priority, resource profile, config payload, environment/approval policy, Airflow DAG/run reference, MLflow experiment/run reference, and CD/CT gate preview |
| `EVM-231` | Live pipeline timeline and intermediate result drilldown | Planned | 2026-07-W7 | data intake, validation, quality gate, training, registry, inference, serving, monitoring, drift review, and CD/CT stages show animated current state plus stage-level artifacts, metrics, logs, sample outputs, and failure reasons |
| `EVM-232` | Resource control protocol and audit guardrails | Planned | 2026-07-W7 | Kubernetes, Airflow, and MLflow actions are represented as explicit command intents with dry-run/confirm/apply states, audit trail, RBAC-ready actor fields, and rollback or cancel semantics |
| `EVM-233` | Enterprise service tenancy and environment scope | Planned | 2026-07-W7 | W7 API/UI exposes team, department, internal/external service scope, data/model/ops owners, environment tier, namespace/cluster, approval policy, and promotion state |
| `EVM-234` | Drift detection and retraining trigger surface | Planned | 2026-07-W7 | data drift, prediction drift, reference/current dataset versions, drift report URI, label-review/retrain/block actions, and UI drilldown are represented in cycle detail |
| `EVM-235` | CD/CT push verification and promotion gate | Planned | 2026-07-W7 | push/PR checks, image build, kustomize render, data quality, model eval, drift review, CT trigger, and promotion blockers are represented as a pass/fail gate before deploy/promote |
| `EVM-236` | Enterprise data/model pipeline readiness checklist | Planned | 2026-07-W7 | data pipeline readiness covers source policy, schema, quality, lineage, replay/backfill; model pipeline readiness covers MLflow tracking, eval reports, registry, model card, rollback, and owner approval |
| `EVM-237` | Torch EfficientNet-B0/B7 real model matrix | Planned | 2026-07-W7 | W7 model plan defines real Torch/TorchVision EfficientNet-B0 and EfficientNet-B7 candidates, condition matrix, GPU resource profiles, MLflow tracking requirements, and Control Panel `model_matrix` output |
| `EVM-238` | W7 real-test-only evidence policy umbrella | Planned | 2026-07-W7 | W7 acceptance explicitly rejects mock adapters, placeholder predictions, and smoke-only runs as completion evidence; closure requires both `EVM-238-A` and `EVM-238-B` |
| `EVM-238-A` | W7 real-test policy guard | Planned | 2026-07-W7 | guard implementation blocks mock adapters, placeholder predictions, synthetic-only fixtures, and smoke-only evidence before W7 closure |
| `EVM-238-B` | W7 real-test evidence validation | Planned | 2026-07-W7 | validates actual `CycleRun.model_matrix`, EfficientNet MLflow runs, F-drive artifacts, metrics, split manifest, GPU profile, environment report, and confusion matrices after `EVM-237` evidence exists |

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
