# Enterprise MLOps Roadmap

작성일: 2026-06-21

이 문서는 일정이 눈에 보이도록 관리하기 위한 roadmap이다. 상세 issue 목록은
`docs/issues/issue-register.md`를 source of truth로 둔다.

## Portfolio Roadmap

```mermaid
gantt
    title Enterprise MLOps Portfolio Roadmap
    dateFormat  YYYY-MM-DD
    axisFormat  %m/%d

    section Phase 1 MVP
    Local control-plane MVP               :done, p1, 2026-06-18, 2026-06-21
    Connectivity and mac-mini setup        :done, p1b, 2026-06-21, 2026-06-21

    section Phase 2 Airflow
    Airflow compose service                :active, evm021, 2026-06-22, 2026-06-23
    DAG skeleton                           :evm022, 2026-06-24, 2026-06-24
    Pipeline task wiring                   :evm023, 2026-06-25, 2026-06-25
    Retry timeout and MLflow linkage       :evm024, 2026-06-26, 2026-06-27
    Full DAG smoke                         :milestone, evm027, 2026-06-30, 0d

    section Phase 3 Data Platform
    MinIO bucket and object client         :evm031, 2026-07-01, 2026-07-05
    Public dataset ingest                  :evm033, 2026-07-06, 2026-07-12
    Validation and Parquet dataset         :evm034, 2026-07-13, 2026-07-24
    Dataset version metadata               :evm036, 2026-07-25, 2026-07-31

    section Phase 4 Training Infra
    Remote job spec and mac-mini eval      :evm041, 2026-08-01, 2026-08-10
    Linux worker and resource report       :evm043, 2026-08-11, 2026-08-20
    Remote artifact collection             :evm045, 2026-08-21, 2026-08-31

    section Phase 5 Serving
    Registry-driven model loading          :evm051, 2026-08-11, 2026-08-24
    Model version metrics and rollback     :evm054, 2026-08-25, 2026-08-31

    section Phase 6 Observability
    Grafana dashboard hardening            :evm061, 2026-08-25, 2026-09-05
    Drift reports and SLO                  :evm063, 2026-09-06, 2026-09-14

    section Phase 7 Governance
    GitHub Actions checks                  :evm071, 2026-09-01, 2026-09-10
    Release note and demo script           :evm074, 2026-09-11, 2026-09-20
    2026 portfolio cut                     :milestone, cut2026, 2026-09-21, 0d
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
    CUT["2026 Portfolio Cut"]

    P1 --> P2 --> P3 --> P4 --> P5 --> P6 --> P7 --> CUT
```

## Monthly Focus

| Month | Focus | Must-have Evidence |
|---|---|---|
| 2026-06 | Phase 1 hardening and Phase 2 start | Airflow service and first DAG run |
| 2026-07 | Data platform | MinIO object data, Parquet output, validation report |
| 2026-08 | Remote infra and serving | remote job result, registry-driven API |
| 2026-09 | Observability and portfolio cut | dashboard, CI, release note, demo script |
| 2026-Q4 | Cluster extension | Linux worker/k3s/GPU plan and implementation |
| 2027-Q1 | Enterprise hardening | KServe/Triton, drift, SLO, promotion gates |

## Weekly Review Rule

매주 다음을 갱신한다.

- `docs/issues/issue-register.md`
- `docs/status/YYYY-MM-DD-*.md`
- completed issue commit links
- blocked issue list
- next 7-day task list
