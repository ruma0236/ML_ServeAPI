# Enterprise MLOps Accelerated Weekly Schedule

## 2026-07-06 Current-week Reset

The active execution plan is reset on 2026-07-06 KST. W0 to W3 are treated as
completed foundation work. W4 is now the current-week completion sprint for the
new enterprise VLM-first MLOps plan, scheduled for 2026-07-06 to 2026-07-12.

The previously planned W5 VLM reliability work is pulled into the same
current-week completion sprint. W5 and later now represent post-completion
research and operating-system work: real model lifecycle management,
drift/special-case tracking, draft/decision management, and large-scale data
collection/cleaning research.

작성일: 2026-06-21

## 1. 목표

기존 2026년 9월 portfolio cut 일정을 압축하여, 2026년 7월 중순부터 7월 말까지
최종 enterprise MLOps pipeline MVP를 모두 구현하는 것을 목표로 한다.

여기서 "최종 enterprise pipeline MVP"는 production-grade 운영 환경 전체가 아니라,
아래 기능이 하나의 end-to-end 시스템으로 실제 동작하고 검증되는 상태를 의미한다.

- Airflow 기반 workflow orchestration
- MLflow experiment tracking / model registry 연계
- MinIO object storage 기반 데이터/아티팩트 흐름
- Parquet 기반 dataset output
- registry-driven FastAPI serving
- Prometheus/Grafana observability
- mac-mini remote worker execution
- GitHub Issue / CI workflow / 운영 문서화

## 2. 일정 원칙

- 일정 단위는 1주 단위로 관리한다.
- 매주 마지막 날에는 반드시 integration smoke와 status 문서를 남긴다.
- 구현과 문서는 같은 commit에 묶는다.
- 완료 기준은 "코드 작성"이 아니라 "검증 명령 통과 + 문서 기록 + commit/push"이다.
- 일정이 밀리면 scope를 줄이지 말고 production-hardening 항목을 2027 extension으로 넘긴다.

## 3. 압축 일정 요약

| Week | Date | Main Goal | Exit Criteria |
|---|---|---|---|
| W0 | 2026-06-22 ~ 2026-06-28 | Airflow 기반 orchestration 착수 | Airflow UI와 DAG skeleton 동작 |
| W1 | 2026-06-29 ~ 2026-07-05 | Full DAG + MLflow linkage | ingest~monitor full DAG run 성공 |
| W2 | 2026-07-06 ~ 2026-07-12 | MinIO/Parquet data platform | object storage와 parquet dataset 생성 |
| W3 | 2026-07-13 ~ 2026-07-19 | Registry-driven serving + remote job | API가 registry artifact를 로드하고 mac-mini job 실행 |
| W4 | 2026-07-20 ~ 2026-07-26 | Observability + CI/CD/CT | dashboard, CI, CT trigger skeleton 동작 |
| W5 | 2026-07-27 ~ 2026-07-31 | Final integration and portfolio cut | 전체 demo script와 release note 완성 |

Current active sprint rows below supersede the earlier W4/W5 rows in this
summary table and are intentionally repeated so automation reads the latest
week range for each week label.

| Week | Date | Main Goal | Exit Criteria |
|---|---|---|---|
| W4 | 2026-07-06 ~ 2026-07-12 | Current-week enterprise VLM MLOps completion | EVM-134~181 plus VLM observability, CI, release, and demo evidence |
| W5 | 2026-07-13 ~ 2026-07-19 | Model lifecycle, drift, and draft governance | lifecycle state machine, drift/special-case tracking, draft registry |
| W6 | 2026-07-10 ~ 2026-07-12 | Accelerated data platform + Kubernetes foundation | curation/lakehouse plan, Kubernetes resource map, metadata API contract |
| W7 | 2026-07-13 ~ 2026-07-15 | Accelerated Control Panel + runtime hardening | animated Kubernetes/pipeline/resource control UI, Kubernetes real execution proof, serving-scale handoff |
| W8 | 2026-07-13 ~ 2026-07-15 | Operator-centered reproducible Control Plane | purpose-based workspace navigation, guided immutable Run Blueprint, focused animated lifecycle views, deterministic replay evidence, and guarded CV/HPO, CT, and A/B execution |

## 4. W0: 2026-06-22 ~ 2026-06-28

목표:

- 현재 `scripts/run_pipeline.py` 중심 수동 실행 구조를 Airflow DAG로 옮기기 시작한다.
- Airflow가 local control-plane의 orchestration layer가 되도록 구성한다.

작업:

| ID | Task | Output |
|---|---|---|
| `EVM-021` | Airflow Docker Compose service 추가 | webserver, scheduler, metadata DB |
| `EVM-022` | DAG directory 구성 | `airflow/dags` 또는 `orchestration/airflow/dags` |
| `EVM-023-A` | DAG skeleton 생성 | `enterprise_vision_mlops_daily.py` |
| `EVM-023-B` | `data-ingest`, `data-validate` task 연결 | Airflow UI task graph |
| `EVM-DOC-021` | Airflow setup runbook 작성 | `docs/runbooks/airflow-local.md` |

검증:

```powershell
docker compose ps
docker compose logs airflow-webserver
docker compose logs airflow-scheduler
```

완료 기준:

- Airflow UI 접속 가능
- DAG가 UI에 표시됨
- `data-ingest -> data-validate` task 수동 실행 성공
- `docs/status/2026-06-28-airflow-foundation.md` 작성

## 5. W1: 2026-06-29 ~ 2026-07-05

목표:

- Phase 2를 완료한다.
- 기존 local pipeline 전체를 Airflow DAG로 실행한다.
- Airflow run context와 MLflow run context를 연결한다.

작업:

| ID | Task | Output |
|---|---|---|
| `EVM-023-C` | `train` task 연결 | Airflow task logs |
| `EVM-023-D` | `register-model` task 연결 | registry metadata output |
| `EVM-023-E` | `deploy-check`, `monitor-check` task 연결 | deployment/monitoring report |
| `EVM-024` | retry, timeout, dependency policy 추가 | DAG default args |
| `EVM-025` | Airflow run id를 MLflow tag/param으로 기록 | traceability |
| `EVM-027` | Full DAG smoke | 전체 DAG 성공 |

검증:

```powershell
docker compose ps
python scripts/run_pipeline.py train --config configs/local.toml
```

Airflow UI에서 확인:

```text
data_ingest -> data_validate -> train -> register_model -> deploy_check -> monitor_check
```

완료 기준:

- Airflow full DAG manual run 성공
- MLflow에 Airflow context 기록
- 실패 task 재실행 가능
- `docs/status/2026-07-05-airflow-full-dag.md` 작성

## 6. W2: 2026-07-06 ~ 2026-07-12

목표:

- 데이터 파이프라인을 local JSONL 중심에서 MinIO + Parquet 중심으로 전환한다.
- "대용량 데이터 처리 경험"을 설명할 수 있는 형태를 만든다.

작업:

| ID | Task | Output |
|---|---|---|
| `EVM-031` | MinIO bucket bootstrap 고도화 | `raw`, `processed`, `validated`, `mlflow-artifacts` |
| `EVM-032` | object storage client module 추가 | upload/download/list API |
| `EVM-033` | public vision dataset ingest | raw object + manifest |
| `EVM-034` | validation report 고도화 | schema, label, dimension report |
| `EVM-035` | Parquet dataset generation | processed/validated parquet |
| `EVM-036` | dataset version metadata | training input version |

검증:

```powershell
python scripts/run_pipeline.py data-ingest --config configs/local.toml
python scripts/run_pipeline.py data-validate --config configs/local.toml
```

추가 검증:

```text
MinIO raw bucket object exists
MinIO validated bucket object exists
Parquet dataset exists
dataset version metadata exists
```

완료 기준:

- Airflow DAG에서 MinIO 기반 ingest/validate 실행
- Parquet output 생성
- training task가 dataset version을 input으로 사용
- `docs/status/2026-07-12-data-platform.md` 작성

## 7. W3: 2026-07-13 ~ 2026-07-19

목표:

- registry-driven serving을 완성한다.
- remote worker execution을 inventory 수준에서 실제 job execution 수준으로 올린다.

작업:

| ID | Task | Output |
|---|---|---|
| `EVM-041` | remote job spec 정의 | command/env/input/output schema |
| `EVM-042` | mac-mini ARM64 evaluation job | remote run report |
| `EVM-045` | remote artifact collection | control-plane artifact collection |
| `EVM-051` | model loading module 추가 | API startup model loader |
| `EVM-052` | `/ready` model readiness 확장 | model version/load status |
| `EVM-053` | `/predict` placeholder 제거 | artifact 기반 prediction |
| `EVM-054` | model version metric expose | Prometheus model version metric |

검증:

```powershell
python scripts/run_pipeline.py register-model --config configs/local.toml
python scripts/run_pipeline.py deploy-check --config configs/local.toml
python scripts/run_pipeline.py remote-inventory --config configs/local.toml
ssh ruma-macmini-mlops 'bash ~/mlops-lab/ML_ServeAPI/enterprise-vision-mlops/infra/remote-workers/mac-mini/run_mac_worker_smoke.sh'
```

완료 기준:

- API가 registry metadata 또는 MLflow artifact를 기준으로 모델 로드
- `/predict`가 placeholder flag 없이 응답
- mac-mini remote job이 실행되고 결과가 회수됨
- `docs/status/2026-07-19-serving-remote-worker.md` 작성

## 8. W4: 2026-07-06 ~ 2026-07-12

2026-07-06 current-week override:

W4 is now the current-week enterprise VLM MLOps completion sprint. It includes
the remaining Manufacturing VLM P0 Foundation work, the VLM Reliability
Evaluation work previously scheduled for W5, and the observability/CI/demo
items required to make the result reviewable as an enterprise MLOps system.

| ID | Task | Output |
|---|---|---|
| `EVM-130` | Reposition project narrative and README for manufacturing VLM infra | README and agenda positioning |
| `EVM-131` | Home Lab deployment topology update | Windows RTX / Mac mini / MacBook role map |
| `EVM-132` | Industrial anomaly dataset decision and import contract | VisA primary and MVTec AD fallback decision |
| `EVM-133` | Manufacturing image manifest schema | manifest schema |
| `EVM-134` | Image quality validation pipeline | quality report artifacts |
| `EVM-135` | Dataset shard/split builder | repeatable shards and splits |
| `EVM-141` | VLM adapter interface and mock adapter | adapter contract and mock backend |
| `EVM-142` | Multimodal router request classification | request type routing |
| `EVM-143` | Manifest-based batch inference runner | JSONL batch outputs with retry/resume |
| `EVM-144` | VLM output schema validator | structured output validation |
| `EVM-151` | Prompt and model version registry | prompt/model/eval config registry |
| `EVM-152` | Regression gate with intentionally bad candidate | blocked bad candidate evidence |
| `EVM-161` | Audit event schema and RCA join path | trace_id-based RCA join |
| `EVM-162` | Failure scenario suite | bad prompt, corrupt/drift, schema, endpoint failures |
| `EVM-171` | VLM metrics and benchmark reports | latency, error, schema validity, resource reports |
| `EVM-181` | Portfolio README and final demo script | final demo evidence |
| `EVM-061` | Grafana dashboard hardening | VLM workload dashboard panels |
| `EVM-062` | pipeline success/failure metric | pipeline status metric |
| `EVM-063` | data drift report | manifest/image distribution comparison |
| `EVM-065` | SLO/alert rule documentation | SLO and alert rules |
| `EVM-071` | GitHub Actions lint/test workflow | CI workflow |
| `EVM-072` | Docker build check | image build validation |
| `EVM-073` | pipeline smoke check in CI | CI smoke validation |
| `EVM-074` | release note template | July enterprise MVP release note |
| `EVM-075` | final portfolio demo script | repeatable demo steps |

목표:

- 운영 관측성과 CI/CD/CT skeleton을 추가한다.
- enterprise pipeline을 "돌아가는 시스템"에서 "운영 가능한 시스템"으로 끌어올린다.

작업:

| ID | Task | Output |
|---|---|---|
| `EVM-061` | Grafana dashboard 고도화 | latency/error/model version panels |
| `EVM-062` | pipeline success/failure metric | pipeline status metric |
| `EVM-063` | data drift report | distribution comparison report |
| `EVM-065` | SLO/alert rule 문서화 | SLO and alert rules |
| `EVM-071` | GitHub Actions lint/test workflow | CI workflow |
| `EVM-072` | Docker build check | image build validation |
| `EVM-073` | CT trigger skeleton | scheduled or manual training trigger |

검증:

```powershell
docker compose ps
python scripts/run_pipeline.py monitor-check --config configs/local.toml
```

GitHub 확인:

```text
Actions workflow file exists
Issue template exists
CI validates compile/test/smoke path
```

완료 기준:

- Grafana dashboard에서 핵심 metric 확인 가능
- CI workflow가 push/PR 기준으로 실행 가능
- CT skeleton이 Airflow DAG 또는 GitHub Actions schedule로 정의됨
- `docs/status/2026-07-26-observability-cicd.md` 작성

## 9. W5: 2026-07-13 ~ 2026-07-19

2026-07-06 post-completion reset:

W5 now starts after the current-week completion sprint. Its scope is the real
model lifecycle and operating feedback loop: real trainable model artifact,
candidate state, promotion evidence, lineage, drift/special-case tracking, RCA
feedback into eval sets, serving contract validation, visual evidence, and
remote Mac mini resource validation.

| ID | Task | Output |
|---|---|---|
| `EVM-191` | Real trainable model lifecycle state machine | `image_feature_centroid` training artifact and lifecycle transition evidence |
| `EVM-192` | Model/data/resource lineage matrix | dataset version, Parquet URI, model digest, metrics, and resource profile joined to registry v9 |
| `EVM-193` | Drift and special-case tracking queue | misclassification and label-distribution watch cases with owner/severity/status |
| `EVM-194` | RCA-to-regression feedback loop | false prediction cases promoted into reviewable regression candidates |
| `EVM-195` | Lifecycle dashboard and review checkpoint | dashboard JSON plus W5 visual verification package |
| `EVM-196` | Real model serving contract hardening | FastAPI `/predict` performs registry-loaded feature-model inference |
| `EVM-197` | Airflow W4/W5 real-cycle alignment | DAG/config point to mounted VisA/F-drive data and include `model_lifecycle` |
| `EVM-198` | Mac mini M4 Pro resource integration proof | SSH, resource inventory, and remote job artifact collection verified |
| `EVM-199` | W5 visual verification evidence package | Markdown plus SVG lifecycle, metric, confusion matrix, and topology evidence |
| `EVM-200` | Production promotion gate hardening | weak proof models remain Shadow when production thresholds are not met |

목표:

- 전체 enterprise pipeline MVP를 통합 검증한다.
- 지원/면접에서 보여줄 수 있는 portfolio cut을 만든다.

작업:

| ID | Task | Output |
|---|---|---|
| `EVM-074` | release note 작성 | July enterprise MVP release note |
| `EVM-075` | final demo script 작성 | repeatable demo steps |
| `EVM-DOC-031` | architecture diagram 정리 | final architecture doc |
| `EVM-DOC-032` | troubleshooting runbook 정리 | known failure and recovery |
| `EVM-QA-001` | clean clone verification | clean setup report |
| `EVM-QA-002` | final integration smoke | end-to-end evidence |

최종 검증 명령:

```powershell
docker compose up -d --build
docker compose ps
python scripts/run_pipeline.py data-ingest --config configs/local.toml
python scripts/run_pipeline.py data-validate --config configs/local.toml
python scripts/run_pipeline.py train --config configs/local.toml
python scripts/run_pipeline.py register-model --config configs/local.toml
python scripts/run_pipeline.py deploy-check --config configs/local.toml
python scripts/run_pipeline.py monitor-check --config configs/local.toml
python scripts/run_pipeline.py remote-inventory --config configs/local.toml
```

Airflow 검증:

```text
Full DAG manual run success
Latest DAG run logs available
MLflow run linked
Model registry updated
Serving API ready
Prometheus targets healthy
```

완료 기준:

- README 기준 demo 재현 가능
- Airflow full DAG 성공
- MLflow run/metric/artifact 확인
- MinIO raw/validated/parquet artifact 확인
- registry-driven API serving 확인
- Prometheus/Grafana dashboard 확인
- mac-mini remote worker job 확인
- GitHub Issue/CI workflow 확인
- release note와 final status 문서 작성

최종 산출물:

```text
docs/status/2026-07-31-enterprise-mlops-final-cut.md
docs/releases/2026-07-enterprise-mlops-mvp.md
docs/runbooks/final-demo-script.md
```

## 10. W6: 2026-07-10 ~ 2026-07-12

W6 is compressed into the 2026-07-10 to 2026-07-12 window. It keeps the
large-scale data acquisition and cleaning research scope, but now also prepares
the runtime for Kubernetes and Control Panel work. The exit criterion is not a
production Kubernetes migration; it is a clear resource map, local manifest
scaffold, and metadata API contract that can expose the W5 lifecycle without
manual artifact chasing.

| ID | Task | Output |
|---|---|---|
| `EVM-201` | Data source registry and collection policy | source/license/retention/access/volume policy |
| `EVM-202` | Large-scale batch acquisition planner | resumable checkpoints, shard targets, retry policy |
| `EVM-203` | Deduplication and cleaning benchmark | exact/perceptual hash and quality benchmark |
| `EVM-204` | Labeling and curation workflow | review queue, label states, curated eval promotion |
| `EVM-205` | Lakehouse-scale ingestion research spike | DuckDB/Polars/Spark/Iceberg prototype recommendation |
| `EVM-221` | Kubernetes runtime resource map | Compose services mapped to Deployment/Service/PVC/Secret/Job/CronJob |
| `EVM-222` | Local Kubernetes manifest scaffold | k8s overlays for API, MLflow, MinIO, Prometheus/Grafana, and batch Job |
| `EVM-223` | Control Panel metadata and control API contract | cycle-run, dataset, model, lineage, task assignment, resource action, promotion, and serving status schema |

## 11. W7: 2026-07-13 ~ 2026-07-15

W7 is compressed into the 2026-07-13 to 2026-07-15 window. It keeps the
governance, AgentOps, and serving-scale research scope, and adds the first
usable enterprise MLOps Control Panel v0. The Control Panel should read
lifecycle, registry, dataset, Kubernetes, Airflow/MLflow, and Prometheus
evidence through metadata and control APIs so the user can inspect a full cycle
visually instead of opening each artifact file by hand. It must also define the
first UI/control boundary for task authoring, task assignment, resource-control
intent, and stage-level intermediate result review.

| ID | Task | Output |
|---|---|---|
| `EVM-211` | Draft and decision registry | draft/review/approved/rejected states |
| `EVM-212` | AgentOps reliability follow-up design | LLM agent, LangGraph, HITL, tool-call audit scope |
| `EVM-213` | Scale serving research plan | vLLM/Triton/KServe/Ray Serve/Kueue comparison |
| `EVM-214` | July portfolio stabilization review | demo evidence, known gaps, backlog, handoff risks |
| `EVM-224` | Cycle lineage aggregation API | endpoint returns run, dataset, model, metrics, gate, artifacts, serving state, and orchestration references |
| `EVM-225` | MLOps Control Panel v0 | tabbed/depth UI for Kubernetes control, pipeline control, resource management, cycle detail, dataset/model cards, gate, lineage, and serving status |
| `EVM-226` | Kubernetes local real execution proof | at least one API or pipeline job path runs under local Kubernetes/k3s/kind with real configured inputs |
| `EVM-227` | GPU/VLM serving deployment design | Windows RTX, KServe/Triton/vLLM/Ray Serve roles and constraints documented |
| `EVM-228` | Compressed W6/W7 integration review | 2026-07-15 handoff evidence across Git/Jira/Notion/Obsidian |
| `EVM-229` | Kubernetes resource topology and animation UI | animated namespace/node/pod/job/service/PVC/GPU state with allocation and readiness drilldowns |
| `EVM-230` | Airflow and MLflow task authoring and assignment UI | draft/edit/assign/validate/queue tasks with owner, priority, resource profile, config, DAG/run, and experiment/run references |
| `EVM-231` | Live pipeline timeline and intermediate result drilldown | animated data intake, validation, quality, training, registry, inference, serving, and monitoring stages with artifacts, metrics, logs, and sample outputs |
| `EVM-232` | Resource control protocol and audit guardrails | dry-run/confirm/apply command intents for Kubernetes, Airflow, and MLflow actions with actor, audit, cancel, and rollback semantics |
| `EVM-237` | Torch EfficientNet-B0/B7 real model matrix | parallel Torch/TorchVision EfficientNet candidates, GPU resource profiles, MLflow runs, and Control Panel `model_matrix` output |
| `EVM-238` | W7 real-test-only evidence policy | mock adapters, placeholder predictions, and smoke-only runs are rejected as W7 completion evidence |

## 12. 압축 일정 기준의 Scope Control

7월 말까지 반드시 구현할 것:

- Airflow orchestration
- MLflow tracking / registry linkage
- MinIO object storage data path
- Parquet dataset output
- registry-driven API loading
- Prometheus/Grafana monitoring
- mac-mini remote execution
- GitHub issue and CI workflow
- final demo/runbook/status docs

7월 말 이후로 넘길 수 있는 것:

- production-grade multi-node Kubernetes operation
- multi-node GPU training
- Triton/KServe production deployment
- full canary rollout
- advanced drift detection
- Jira full automation

## 13. Weekly Review Template

매주 금요일 또는 일요일에 아래 형식으로 상태 문서를 추가한다.

````markdown
# YYYY-MM-DD Weekly Review

## Completed

- ...

## Verification

```bash
...
```

## Open Issues

- ...

## Next Week

- ...
````

## 14. 최종 판단 기준

7월 31일 기준으로 다음 질문에 모두 "yes"라고 답할 수 있으면 목표 달성으로 본다.

- 데이터가 object storage로 들어가는가?
- 검증된 dataset version이 학습 입력으로 고정되는가?
- Airflow가 end-to-end pipeline을 실행하는가?
- MLflow가 실험과 모델 artifact를 추적하는가?
- registry에서 승격된 모델을 API가 로드하는가?
- API metric이 Prometheus/Grafana에서 보이는가?
- mac-mini가 remote worker로 실제 job을 수행하는가?
- GitHub Issue/CI/status 문서가 운영 근거를 남기는가?
- clean clone 후 demo script로 재현 가능한가?
