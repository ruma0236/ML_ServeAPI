# Enterprise MLOps Implementation Agenda

작성일: 2026-06-21

## 1. Objective

이 프로젝트의 목표는 단순한 MLOps 도구 설치 데모가 아니라, 대기업 MLOps
엔지니어 직무에서 요구하는 운영형 시스템 설계/구축 경험을 포트폴리오로 증명하는
것이다.

최종적으로 보여줘야 하는 메시지는 다음과 같다.

> "데이터 수집부터 검증, 학습, 실험 추적, 모델 버전 관리, 배포, 모니터링, 원격
> 실행 인프라까지 하나의 재현 가능한 MLOps control-plane으로 설계하고 운영했다."

## 2. Target Portfolio Narrative

면접과 이력서에서 이 프로젝트는 다음 문장으로 설명할 수 있어야 한다.

- 로컬 Docker 기반 MLOps control-plane을 직접 설계하고 구성했다.
- Airflow로 데이터/학습/배포 검증 파이프라인을 orchestration했다.
- MLflow로 실험, metric, artifact, model registry를 추적했다.
- MinIO/S3-compatible object storage를 데이터와 모델 artifact 저장소로 사용했다.
- FastAPI serving layer를 registry-driven 구조로 전환했다.
- Prometheus/Grafana로 API, model, pipeline 상태를 관측했다.
- mac-mini, Linux worker, 향후 GPU/k8s node를 remote execution layer로 확장했다.
- runbook, architecture decision, connectivity review, failure handling 문서를 남겼다.

## 3. Current Position

현재 상태는 `Phase 1: Local Control-plane MVP` 완료 상태로 본다.

| Phase | Name | Status | 판단 |
|---|---|---|---|
| Phase 1 | Local Control-plane MVP | Done | Docker stack, modular pipeline, mac-mini worker, 문서화 완료 |
| Phase 2 | Airflow + MLflow Orchestration | Not started | 다음 즉시 착수 대상 |
| Phase 3 | Data Platform | Not started | MinIO bucket, Parquet, validation, dataset versioning 필요 |
| Phase 4 | Training Infra Expansion | Partial | mac-mini worker만 부분 구성 |
| Phase 5 | Registry-driven Serving | Not started | API contract만 있고 실제 model loading 미구현 |
| Phase 6 | Observability / Drift / SLO | Partial | Prometheus/Grafana 기본만 있음 |
| Phase 7 | CI/CD / Governance | Partial | 문서는 있으나 GitHub Actions, policy gate 미구현 |

## 4. Current Implemented Baseline

현재 구현된 baseline은 다음과 같다.

- Docker Compose local stack
- PostgreSQL backend store
- MinIO S3-compatible artifact store
- MLflow tracking endpoint
- FastAPI serving API
- Prometheus metrics scraping
- Grafana dashboard foundation
- Modular pipeline package under `src/evm`
- Pipeline command runner: `scripts/run_pipeline.py`
- Pipeline sequence:
  - `data-ingest`
  - `data-validate`
  - `train`
  - `register-model`
  - `deploy-check`
  - `monitor-check`
  - `remote-inventory`
  - `remote-job`
- mac-mini worker branch: `codex/mac-mini-worker`
- mac-mini PyCharm Remote Development preflight
- Connectivity and runbook documentation

## 5. Final Target Architecture

최종 목표 구조는 다음 흐름으로 고정한다.

```text
Data Engineering
-> Workflow Orchestration
-> Experiment Tracking
-> Model Registry
-> Scalable Training Infra
-> Registry-driven Serving
-> Observability
-> CI/CD and Governance
```

역할 분리는 다음과 같다.

| Layer | Tool / Component | Responsibility |
|---|---|---|
| Workflow Orchestration | Airflow | DAG, schedule, dependency, retry, backfill |
| Experiment Tracking | MLflow | run, params, metrics, artifacts |
| Model Registry | MLflow Registry + local metadata bridge | model version, stage, promotion |
| Object Storage | MinIO | raw, processed, validated, model artifacts |
| Batch Data | Spark or DuckDB/Polars first, Spark later | large-scale transform, Parquet generation |
| Serving | FastAPI first, Triton/KServe later | prediction API, model loading, canary-ready contract |
| Observability | Prometheus / Grafana | latency, errors, target health, model version metrics |
| Remote Compute | mac-mini, Linux worker, future GPU/k8s | heterogeneous validation and distributed execution |
| CI/CD | GitHub Actions | lint, test, build, smoke, release gate |
| Governance | docs/runbooks/decisions/status | reproducibility, operating evidence, troubleshooting |

## 6. Phase Plan

### Phase 1. Local Control-plane MVP

목표:

- MLOps 전체 흐름을 작은 규모로 한 번 끝까지 실행한다.
- 수동 실행이라도 각 pipeline role이 모듈로 분리되어 있어야 한다.
- Docker 기반 local infra가 재현 가능해야 한다.

구현 범위:

- Docker Compose stack
- MLflow, Postgres, MinIO
- FastAPI serving contract
- Prometheus/Grafana foundation
- modular pipeline code
- mac-mini remote worker inventory
- architecture/runbook/status docs

완료 기준:

- `docker compose ps`에서 핵심 서비스 정상
- `data-ingest -> data-validate -> train -> register-model -> deploy-check -> monitor-check` 실행
- MLflow run logging 확인
- Prometheus target healthy
- mac-mini `remote_exec_ready=true`

현재 판단:

- Phase 1은 완료로 본다.
- 단, serving은 placeholder이고, 데이터는 synthetic/local manifest이므로 enterprise-ready는 아니다.

### Phase 2. Airflow + MLflow Orchestration

목표:

- `scripts/run_pipeline.py`가 임시로 하던 orchestration을 Airflow DAG로 전환한다.
- pipeline 실행 이력, retry, timeout, dependency를 운영형으로 만든다.

구현 범위:

- Airflow Docker service 추가
- DAG folder 구성
- `enterprise_vision_mlops_daily` DAG 작성
- 각 task에서 기존 pipeline module 호출
- task timeout, retry, dependency 설정
- Airflow variables/connections 정리
- MLflow run id와 Airflow dag/task/run id 연결

필수 DAG:

```text
data_ingest
-> data_validate
-> train
-> evaluate
-> register_model
-> deploy_check
-> monitor_check
```

완료 기준:

- Airflow UI에서 DAG run 성공
- task별 log 확인 가능
- 실패 task 재실행 가능
- MLflow run과 Airflow run 관계를 문서화
- manual trigger와 scheduled run 모두 가능

포트폴리오 메시지:

- "수동 스크립트가 아니라 운영형 workflow scheduler로 MLOps pipeline을 구성했다."

### Phase 3. Data Platform

목표:

- local JSONL manifest 수준을 넘어 object storage 중심의 데이터 파이프라인으로 확장한다.
- 대용량 데이터 처리 경험을 설명할 수 있게 만든다.

구현 범위:

- MinIO bucket 실제 사용:
  - `raw`
  - `processed`
  - `validated`
  - `mlflow-artifacts`
- public vision dataset ingest
- object storage upload/download client
- Parquet dataset generation
- schema validation
- data quality report
- dataset version metadata
- DuckDB/Polars 기반 local batch 처리
- 이후 Spark local cluster로 확장

완료 기준:

- raw image/metadata가 MinIO에 저장됨
- processed/validated dataset이 Parquet으로 저장됨
- validation report가 Airflow/MLflow artifact로 연결됨
- dataset version이 training input으로 명시됨

포트폴리오 메시지:

- "모델 학습 이전에 데이터 품질, 데이터 버전, object storage 기반 데이터 계약을 설계했다."

### Phase 4. Training Infra Expansion

목표:

- 학습 실행 위치를 control-plane에서 분리한다.
- mac-mini, Linux worker, 향후 GPU/k8s node를 실행 자원으로 관리한다.

구현 범위:

- remote job spec 정의
- mac-mini ARM64 smoke/evaluation job
- Linux worker SSH 재구성
- worker inventory 고도화
- remote artifact collection
- resource report:
  - CPU
  - memory
  - architecture
  - Python runtime
  - elapsed time
- 향후 GPU worker:
  - CUDA availability
  - GPU memory
  - training throughput

완료 기준:

- control-plane에서 remote job을 제출하고 결과를 회수
- worker별 실행 결과가 `artifacts/reports`에 저장
- mac-mini는 ARM64/MPS/CoreML validation 역할로 명확히 분리
- Linux/GPU worker는 training worker 후보로 분리

포트폴리오 메시지:

- "단일 머신 학습이 아니라 control-plane과 worker를 분리한 실행 구조를 설계했다."

### Phase 5. Registry-driven Serving

목표:

- FastAPI가 placeholder response가 아니라 registry에서 승격된 모델을 로드하도록 만든다.

구현 범위:

- MLflow Model Registry 또는 registry bridge를 source-of-truth로 사용
- model loading module 추가
- startup load와 reload path 설계
- `/ready`에서 model load 상태 반환
- `/predict`에서 실제 model artifact 기반 추론
- model version metric expose
- Docker image rebuild
- canary/shadow deployment 설계 문서화
- 이후 Triton/KServe 확장 가능성 정리

완료 기준:

- `register-model` 후 API가 해당 model version을 로드
- `/predict`가 실제 model metadata/result를 사용
- Prometheus에서 model version, latency, request count 확인
- rollback 가능한 registry version 구조

포트폴리오 메시지:

- "모델 파일을 직접 박아 넣은 API가 아니라 registry promotion을 기준으로 serving하는 구조를 만들었다."

### Phase 6. Observability / Drift / SLO

목표:

- 배포 후 운영 상태를 관측하고, 품질 저하와 장애를 탐지할 수 있게 만든다.

구현 범위:

- API latency histogram
- request count/error count
- model version gauge
- prediction distribution metric
- data drift check
- model drift check
- pipeline success/failure metric
- Grafana dashboard
- alert rule
- SLO document

완료 기준:

- Grafana에서 API, pipeline, model 상태를 확인
- drift report가 주기적으로 생성
- SLO/alert 기준 문서화
- 장애 상황을 local runbook으로 복구 가능

포트폴리오 메시지:

- "배포 자체보다 운영 관측성과 장애 대응을 기준으로 MLOps 시스템을 설계했다."

### Phase 7. CI/CD / Governance

목표:

- 개인 프로젝트가 아니라 팀 단위 운영 프로젝트처럼 보이게 만든다.

구현 범위:

- GitHub Actions
- lint
- unit test
- pipeline smoke test
- Docker build check
- docs check
- branch strategy
- release notes
- ADR 추가
- runbook 추가
- incident-style troubleshooting 문서

완료 기준:

- PR 또는 branch push 시 CI 실행
- 실패 시 merge/promotion 불가 구조 문서화
- release tag 또는 milestone note 생성
- 포트폴리오용 architecture summary와 demo script 준비

포트폴리오 메시지:

- "코드만 만든 것이 아니라 변경 관리, 검증, 운영 문서까지 포함한 engineering system을 만들었다."

## 7. Schedule

현재 날짜 기준으로 2026년 7월 31일을 enterprise pipeline MVP 최종 구현 목표로 둔다.
기존 2026년 9월 하반기 지원 컷은 polish, 고도화, 발표 자료 정리 기간으로 전환한다.
2027년 상반기 지원 컷은 k8s/GPU/Triton/KServe 확장 목표로 둔다.

세부 주간 일정은 `docs/agenda/enterprise-mlops-accelerated-weekly-schedule.md`에서
관리한다.

| Week | Date | Goal | Required Output |
|---|---|---|---|
| W0 | 2026-06-22 ~ 2026-06-28 | Airflow foundation | Airflow UI, DAG skeleton, ingest/validate task |
| W1 | 2026-06-29 ~ 2026-07-05 | Full DAG + MLflow linkage | full DAG run, retry/timeout, MLflow context tag |
| W2 | 2026-07-06 ~ 2026-07-12 | MinIO/Parquet data platform | raw/validated buckets, parquet dataset, dataset version |
| W3 | 2026-07-13 ~ 2026-07-19 | Registry-driven serving + remote job | API model loader, `/predict`, mac-mini job result |
| W4 | 2026-07-20 ~ 2026-07-26 | Observability + CI/CD/CT | Grafana panels, CI workflow, CT trigger skeleton |
| W5 | 2026-07-27 ~ 2026-07-31 | Final integration cut | clean clone verification, demo script, release note |

### W0. 2026-06-22 to 2026-06-28

- Airflow Compose service 추가
- DAG directory와 `enterprise_vision_mlops_daily.py` 생성
- `data-ingest`, `data-validate` task 연결
- Airflow setup runbook 작성
- `docs/status/2026-06-28-airflow-foundation.md` 작성

### W1. 2026-06-29 to 2026-07-05

- `train`, `register-model`, `deploy-check`, `monitor-check` task 연결
- retry, timeout, dependency policy 추가
- Airflow run id를 MLflow tag/param으로 기록
- full DAG manual smoke 검증
- `docs/status/2026-07-05-airflow-full-dag.md` 작성

### W2. 2026-07-06 to 2026-07-12

- MinIO bucket bootstrap 고도화
- object storage client module 추가
- public vision dataset ingest
- validation report 고도화
- Parquet dataset generation과 dataset version metadata 추가
- `docs/status/2026-07-12-data-platform.md` 작성

### W3. 2026-07-13 to 2026-07-19

- remote job spec 정의
- mac-mini ARM64 evaluation job 실행
- remote artifact collection 구현
- registry-driven model loading module 추가
- `/ready`, `/predict`, model version metric 고도화
- `docs/status/2026-07-19-serving-remote-worker.md` 작성

### W4. 2026-07-20 to 2026-07-26

- Grafana dashboard 고도화
- pipeline success/failure metric 추가
- data drift report 추가
- SLO/alert rule 문서화
- GitHub Actions lint/test, Docker build, CT trigger skeleton 추가
- `docs/status/2026-07-26-observability-cicd.md` 작성

### W5. 2026-07-27 to 2026-07-31

- final demo script 작성
- release note 작성
- architecture diagram, troubleshooting runbook 정리
- clean clone verification 수행
- end-to-end integration smoke 수행
- `docs/status/2026-07-31-enterprise-mlops-final-cut.md` 작성
- `docs/releases/2026-07-enterprise-mlops-mvp.md` 작성

### 2026-Q4 to 2027-Q1

목표:

- 2027 상반기 지원용 enterprise extension

작업:

- Linux worker 추가
- k3s/Kubernetes control-plane 구성
- GPU node 후보 연결
- Triton/KServe 검토
- CI/CD promotion gate
- drift/SLO/alert 고도화
- 최종 포트폴리오 문서/발표 자료 정리

## 8. Immediate Next Agenda

다음 작업은 Phase 2로 고정한다.

세부 task와 일정은 `docs/issues/issue-register.md`와
`docs/agenda/enterprise-mlops-roadmap.md`에서 관리한다.

우선순위:

1. Airflow Docker Compose service 추가
2. DAG directory와 `enterprise_vision_mlops_daily.py` 생성
3. 기존 `scripts/run_pipeline.py` command를 BashOperator 또는 PythonOperator로 연결
4. task dependency 정의
5. retry/timeout 설정
6. Airflow UI 접속 확인
7. DAG manual run 성공
8. Airflow run 결과를 `docs/status`에 기록

이 순서가 중요한 이유:

- 현재 pipeline module은 이미 분리되어 있다.
- Airflow를 붙이면 즉시 "운영형 MLOps pipeline" 형태가 된다.
- 이후 MinIO/Spark, registry-driven serving, remote training은 Airflow task로 자연스럽게 확장된다.

## 9. Definition of Done for Portfolio Cut

2026년 7월 31일 enterprise pipeline MVP cut의 완료 기준은 다음과 같다.

- clone 후 local infra 실행 가능
- Airflow DAG로 full pipeline run 가능
- MLflow UI에서 run/metric/artifact 확인 가능
- MinIO에서 raw/validated/model artifact 확인 가능
- FastAPI가 registry metadata 기반으로 model load
- Prometheus/Grafana에서 serving metric 확인 가능
- mac-mini remote worker smoke result 확인 가능
- README만 보고 demo 재현 가능
- architecture, runbook, status, agenda 문서가 정리되어 있음

## 10. Non-goals for the July Cut

2026년 7월 31일 1차 cut에서는 다음은 필수 목표가 아니다.

- 완전한 production Kubernetes 운영
- 대규모 CUDA multi-node training
- 실제 대기업 수준의 데이터 양
- Triton/KServe production hardening
- 완전 자동 canary rollout

단, 위 항목들은 2027년 상반기 지원용 고도화 목표로 남긴다.

## 11. Key Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Airflow 도입이 늦어짐 | enterprise orchestration 근거 약화 | 6월 말까지 DAG skeleton 우선 완성 |
| 데이터가 synthetic에 머무름 | 대용량 데이터 처리 경험 부족 | 7월에 public dataset + MinIO + Parquet 연결 |
| serving이 placeholder에 머무름 | MLOps end-to-end 설득력 약화 | 7월 셋째 주에 registry-driven model loading 구현 |
| mac-mini 역할이 과장됨 | GPU cluster 경험처럼 보이면 역효과 | ARM64/edge validation worker로 명확히 표현 |
| 문서가 구현보다 앞섬 | 포트폴리오 신뢰도 하락 | 각 문서는 status evidence와 command output 기반으로 갱신 |

## 12. 2026-07-05 Plan Realignment After W2

This agenda has been realigned after the W2 object-storage data-platform
completion and W3 prework review.

Current position:

| Phase | Name | Updated status | Notes |
|---|---|---|---|
| Phase 1 | Local Control-plane MVP | Done | Docker Compose stack, modular pipeline, MLflow, MinIO, FastAPI, Prometheus/Grafana foundation |
| Phase 2 | Airflow + MLflow Orchestration | Done | Full Airflow DAG is running with MLflow trace linkage and scheduled run evidence |
| Phase 3 | Object Storage Data Platform | Done | MinIO raw/processed/validated zones, Parquet outputs, validation report, dataset version metadata |
| Phase 4 | Remote Training Infra | Done | mac-mini structured remote job spec, ARM64 evaluation, worker resource report, and artifact collection are implemented |
| Phase 5 | Registry-driven Serving | Done | API loads promoted local registry metadata, removes placeholder `/predict`, and exposes model/dataset metadata |
| Phase 6 | Observability / Drift / SLO | Partial / planned | Prometheus/Grafana are live; model/pipeline/drift/SLO hardening remains W4 work |
| Phase 7 | CI/CD / Governance | Partial / planned | Docs and issue governance exist; CI, release gates, and demo cut remain W4-W5 work |
| Phase 8 | Enterprise Multimodal MLOps Expansion | Planned | Long-range lakehouse, lineage, multimodal embeddings, VLM evaluation/serving, enterprise governance |

Strategic direction:

- The July W0-W5 plan remains the local enterprise control-plane MVP.
- W3 completed registry-driven serving first, then remote execution, before
  multimodal expansion.
- The long-range target is now explicitly an enterprise vision/multimodal MLOps
  platform, progressing from image baseline to real image models, then VLM and
  multimodal workloads.
- Detailed target architecture and backlog extensions are tracked in
  `docs/agenda/enterprise-multimodal-mlops-target-roadmap.md`.
- Jira/Git remain the operational source of truth; Notion captures polished
  review state, and Obsidian captures deep work history for future Codex
  recovery.

Immediate update required after this realignment:

1. W3 serving and remote execution tasks are now complete; keep W4/W5 tasks
   planned until implementation starts.
2. Sync the new Phase 8 enterprise multimodal backlog to Jira when Jira API
   credentials are available in the active shell.
3. Add Notion review/evidence entries for this plan realignment.
4. Add Obsidian work-log and context-pack entries so future sessions recover
   the expanded target without relying on chat history.

## 13. Operating Rule

앞으로 모든 Phase 작업은 다음 구조를 따른다.

1. 코드/infra 구현
2. local 또는 remote 검증
3. `docs/pipelines` 업데이트
4. `docs/status`에 날짜별 검증 기록 추가
5. 필요 시 `docs/runbooks` 업데이트
6. Git branch commit/push

## 14. 2026-07-05 VLM-First Manufacturing Reset

The shared direction document resets the July cut around a more specific
portfolio target: Manufacturing Visual Inspection VLM-first AI Infra / MLOps /
AIOps. W0-W3 remain the control-plane foundation. W4 and W5 should now turn
that foundation toward a real industrial image dataset, VLM adapter contracts,
batch evaluation, regression gates, observability, rollback, RCA, and final
demo evidence.

Updated planning rule:

- Do not make LLM Agent, LangGraph, HITL, Kueue, Ray Serve, KServe, or
  production vLLM the July P0 target.
- Treat LLM Agent and AgentOps as P2 after August.
- Treat VisA as the recommended primary P0 dataset candidate, with MVTec AD as
  fallback or secondary comparison.
- Build the mock VLM adapter first so dataset, manifest, batch inference,
  schema validation, tracing, and regression gates can land before the real
  endpoint is ready.
- Target Qwen2.5-VL 3B/7B quantized on the Windows RTX 4080 SUPER node for the
  real VLM path.

Revised W4/W5 execution framing:

| Window | Focus | Issue IDs |
|---|---|---|
| W4 | Manufacturing dataset foundation and VLM adapter skeleton | `EVM-130` to `EVM-144` |
| W5 | VLM reliability gates, failure scenarios, benchmark, RCA, portfolio cut | `EVM-151` to `EVM-181` |

The previous W4/W5 observability and CI/CD work remains important, but it should
now be tied to VLM workload evidence: schema validity, latency, error rate,
dataset quality, prompt/model version, rollback, and RCA events.

## 15. 2026-07-06 Current-week Completion Reset And W5+ Plan

The active sprint plan is compressed on 2026-07-06 KST. The newly defined
enterprise-grade VLM-first MLOps plan is scheduled for completion during the
current week, 2026-07-06 to 2026-07-12. This pulls the former W5 VLM reliability
work into the same execution sprint so the system can show not only feature
presence, but also reviewable reliability evidence.

Current-week completion scope:

| Date | Scope | Issue IDs |
|---|---|---|
| 2026-07-06 | Domain pack foundation completed and synchronized | `EVM-130` to `EVM-133` |
| 2026-07-07 | Image quality validation and shard/split builder | `EVM-134`, `EVM-135` |
| 2026-07-08 | VLM adapter contract and multimodal router | `EVM-141`, `EVM-142` |
| 2026-07-09 | Manifest-based batch inference and VLM output validation | `EVM-143`, `EVM-144` |
| 2026-07-10 | Prompt/model registry, regression gate, audit/RCA, failure suite | `EVM-151`, `EVM-152`, `EVM-161`, `EVM-162` |
| 2026-07-11 | VLM metrics, benchmark, observability, CI, demo evidence | `EVM-171`, `EVM-181`, `EVM-061` to `EVM-075` |
| 2026-07-12 | Integration buffer, release note, final review and handoff | `EVM-074`, `EVM-075` |

Post-completion W5+ plan:

| Sprint | Date | Focus | Issue IDs |
|---|---|---|---|
| W5 | 2026-07-13 to 2026-07-19 | Real model lifecycle, serving contract, drift/special-case tracking, RCA feedback, Mac mini remote validation | `EVM-191` to `EVM-199` |
| W6 | 2026-07-20 to 2026-07-26 | Large-scale data acquisition and cleaning research | `EVM-201` to `EVM-205` |
| W7 | 2026-07-27 to 2026-07-31 | Draft/decision governance, AgentOps reliability design, scale serving research, portfolio stabilization | `EVM-211` to `EVM-214` |

Strategic interpretation:

- The platform remains domain-general if dataset/model/prompt/eval policies are
  isolated in domain packs and registries instead of hard-coded into platform
  services.
- Manufacturing visual inspection is the first concrete policy pack, not the
  only future target domain.
- After the current-week build, the next problems are operating problems:
  lifecycle state, drift/special-case tracking, draft management, data scale,
  and reliable AgentOps/serving expansion.

문서와 코드가 같이 움직여야 이 프로젝트가 단순 toy project가 아니라 운영형
MLOps engineering portfolio로 보인다.
