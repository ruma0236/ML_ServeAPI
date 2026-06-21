# Enterprise MLOps Roadmap

작성일: 2026-06-21

이 문서는 일정이 눈에 보이도록 관리하기 위한 roadmap이다. 상세 issue 목록은
`docs/issues/issue-register.md`를 source of truth로 둔다.

7월 말까지 enterprise MLOps pipeline MVP를 완성하는 압축 일정은
`docs/agenda/enterprise-mlops-accelerated-weekly-schedule.md`에서 관리한다.

## Accelerated Portfolio Roadmap

```mermaid
gantt
    title Enterprise MLOps Accelerated Roadmap
    dateFormat  YYYY-MM-DD
    axisFormat  %m/%d

    section Phase 1 MVP
    Local control-plane MVP                 :done, p1, 2026-06-18, 2026-06-21
    mac-mini worker and issue workflow      :done, p1b, 2026-06-21, 2026-06-21

    section W0 Airflow Foundation
    Airflow compose service                 :active, evm021, 2026-06-22, 2026-06-23
    DAG skeleton and first tasks            :evm022, 2026-06-24, 2026-06-26
    Airflow foundation review               :milestone, w0, 2026-06-28, 0d

    section W1 Full Orchestration
    Full DAG task wiring                    :evm023, 2026-06-29, 2026-07-02
    Retry timeout and MLflow linkage        :evm024, 2026-07-03, 2026-07-04
    Full DAG smoke                          :milestone, w1, 2026-07-05, 0d

    section W2 Data Platform
    MinIO buckets and object client         :evm031, 2026-07-06, 2026-07-08
    Public dataset ingest and validation    :evm033, 2026-07-09, 2026-07-10
    Parquet and dataset version metadata    :evm035, 2026-07-11, 2026-07-12

    section W3 Serving and Remote Jobs
    Remote job spec and mac-mini execution  :evm041, 2026-07-13, 2026-07-15
    Registry-driven model loading           :evm051, 2026-07-16, 2026-07-18
    Serving and remote worker review        :milestone, w3, 2026-07-19, 0d

    section W4 Observability and CI
    Grafana and pipeline metrics            :evm061, 2026-07-20, 2026-07-22
    GitHub Actions CI/CD skeleton           :evm071, 2026-07-23, 2026-07-24
    CT trigger and SLO documentation        :evm073, 2026-07-25, 2026-07-26

    section W5 Final Cut
    Final demo and clean clone verification :evm075, 2026-07-27, 2026-07-29
    Release note and final status           :evm074, 2026-07-30, 2026-07-31
    Enterprise MVP final cut                :milestone, cut2026july, 2026-07-31, 0d
```

## Flow View

```mermaid
flowchart LR
    P1["Phase 1<br/>Local MVP"]
    P2["Phase 2<br/>Airflow DAG"]
    P3["Phase 3<br/>MinIO + Parquet Data"]
    P4["Phase 4<br/>Remote Worker Jobs"]
    P5["Phase 5<br/>Registry-driven Serving"]
    P6["Phase 6<br/>Observability / Drift / SLO"]
    P7["Phase 7<br/>CI/CD / Governance"]
    CUT["2026-07-31<br/>Enterprise Pipeline MVP"]

    P1 --> P2 --> P3 --> P4 --> P5 --> P6 --> P7 --> CUT
```

## Weekly Focus

| Week | Date | Focus | Must-have Evidence |
|---|---|---|---|
| W0 | 2026-06-22 ~ 2026-06-28 | Airflow foundation | Airflow UI and first DAG tasks |
| W1 | 2026-06-29 ~ 2026-07-05 | Full orchestration | full DAG run and MLflow linkage |
| W2 | 2026-07-06 ~ 2026-07-12 | Data platform | MinIO objects, Parquet output, dataset version |
| W3 | 2026-07-13 ~ 2026-07-19 | Serving and remote jobs | registry-driven API and mac-mini job result |
| W4 | 2026-07-20 ~ 2026-07-26 | Observability and CI/CD/CT | dashboard, CI workflow, CT skeleton |
| W5 | 2026-07-27 ~ 2026-07-31 | Final integration | final demo script and release note |

## Weekly Review Rule

매주 다음을 갱신한다.

- `docs/issues/issue-register.md`
- `docs/status/YYYY-MM-DD-*.md`
- completed issue commit links
- blocked issue list
- next 7-day task list
