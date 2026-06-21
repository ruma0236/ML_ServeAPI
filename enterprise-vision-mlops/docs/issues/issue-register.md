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
| `EVM-EPIC-02` | Airflow + MLflow Orchestration | 2026-06 to 2026-07 | Next | Operating DAG with retry/log/schedule |
| `EVM-EPIC-03` | Object Storage Data Platform | 2026-07 | Planned | MinIO raw/processed/validated + Parquet |
| `EVM-EPIC-04` | Remote Training Infra | 2026-07-W3 | Planned | mac-mini/Linux worker job execution |
| `EVM-EPIC-05` | Registry-driven Serving | 2026-07-W3 | Planned | API loads promoted model version |
| `EVM-EPIC-06` | Observability / Drift / SLO | 2026-07-W4 | Planned | dashboards, drift reports, alert rules |
| `EVM-EPIC-07` | CI/CD / Governance | 2026-07-W4 to W5 | Planned | GitHub Actions, checks, release notes |
| `EVM-EPIC-08` | 2027 Enterprise Extension | 2026-Q4 to 2027-Q1 | Planned | k3s/GPU/Triton/KServe extension |

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
| `EVM-021` | Airflow Docker Compose service 추가 | Next | 2026-06-23 | Airflow webserver/scheduler/metadata DB 실행 |
| `EVM-022` | DAG directory와 `enterprise_vision_mlops_daily.py` 생성 | Next | 2026-06-24 | Airflow UI에서 DAG 표시 |
| `EVM-023` | 기존 pipeline command를 Airflow task로 연결 | Next | 2026-07-02 | ingest->validate->train->register->deploy->monitor task 연결 |
| `EVM-024` | retry/timeout/log policy 설정 | Planned | 2026-07-03 | task 실패/재시도 정책 문서화 |
| `EVM-025` | MLflow run id와 Airflow run id 연결 | Planned | 2026-07-04 | MLflow tag 또는 params에 DAG context 저장 |
| `EVM-026` | Airflow runbook/status 문서 작성 | Planned | 2026-07-05 | manual run 절차와 결과 기록 |
| `EVM-027` | Phase 2 smoke 검증 | Planned | 2026-07-05 | full DAG run 성공 |

### Phase 3. Object Storage Data Platform

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-031` | MinIO bucket bootstrap 고도화 | Planned | 2026-07-W2 | raw/processed/validated/mlflow-artifacts bucket 생성 |
| `EVM-032` | object storage client module 추가 | Planned | 2026-07-W2 | upload/download/list API |
| `EVM-033` | public vision dataset ingest | Planned | 2026-07-W2 | raw dataset manifest and object upload |
| `EVM-034` | validation report 고도화 | Planned | 2026-07-W2 | schema, dimensions, label distribution report |
| `EVM-035` | Parquet dataset generation | Planned | 2026-07-W2 | processed/validated parquet outputs |
| `EVM-036` | dataset version metadata | Planned | 2026-07-W2 | training input version fixed by metadata |

### Phase 4. Remote Training Infra

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-041` | remote job spec 정의 | Planned | 2026-07-W3 | command, env, input, output, timeout schema |
| `EVM-042` | mac-mini ARM64 evaluation job | Planned | 2026-07-W3 | remote job execution and report collection |
| `EVM-043` | Linux worker SSH 복구/등록 | Deferred | 2026-Q4 | `remote_exec_ready=true` for Linux candidate |
| `EVM-044` | worker resource report | Planned | 2026-07-W3 | CPU/memory/runtime/architecture report |
| `EVM-045` | remote artifact collection | Planned | 2026-07-W3 | control-plane collects remote outputs |

### Phase 5. Registry-driven Serving

| ID | Task | Status | Target | Acceptance Criteria |
|---|---|---|---:|---|
| `EVM-051` | model loading module 추가 | Planned | 2026-07-W3 | API loads local registry artifact |
| `EVM-052` | `/ready` model readiness 확장 | Planned | 2026-07-W3 | model version/load status returned |
| `EVM-053` | `/predict` placeholder 제거 | Planned | 2026-07-W3 | prediction uses promoted artifact |
| `EVM-054` | model version metric expose | Planned | 2026-07-W3 | Prometheus sees serving model version |
| `EVM-055` | rollback-ready registry contract | Planned | 2026-07-W3 | version selection and rollback documented |

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

## Bug Register

Bug는 발견 즉시 이 섹션에 추가하거나 GitHub Issue로 먼저 생성한 뒤 역으로 기록한다.

| ID | Title | Status | GitHub Issue | Root Cause |
|---|---|---|---|---|
| `EVM-BUG-001` | sample edit breaks data validation dimensions | Closed | https://github.com/ruma0236/ML_ServeAPI/issues/1 | Controlled workflow test; no production code change required |

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
