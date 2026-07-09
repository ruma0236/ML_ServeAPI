# Enterprise MLOps Roadmap

작성일: 2026-06-21

이 문서는 일정이 눈에 보이도록 관리하기 위한 roadmap이다. 상세 issue 목록은
`docs/issues/issue-register.md`를 source of truth로 둔다.

7월 말까지 enterprise MLOps pipeline MVP를 완성하는 압축 일정은
`docs/agenda/enterprise-mlops-accelerated-weekly-schedule.md`에서 관리한다.

완전한 enterprise vision/multimodal MLOps target architecture와 장기 확장
방향은 `docs/agenda/enterprise-multimodal-mlops-target-roadmap.md`에서
관리한다. 7월 MVP는 이 장기 target의 local control-plane foundation으로 본다.

## 2026-07-05 VLM-First Direction Reset

The July 31 cut is now VLM-first Manufacturing Visual Inspection AI Infra /
MLOps / AIOps. W0-W3 remain the completed control-plane foundation. W4 should
focus on real industrial image dataset manifesting, validation, sharding, VLM
adapter, router, and batch inference. W5 should focus on prompt/model
regression gates, failure scenarios, benchmark, RCA/audit, rollback simulation,
and portfolio evidence.

## 2026-07-06 Current-week Sprint Compression

The active plan is compressed again on 2026-07-06. The newly defined
enterprise VLM-first MLOps work is planned for completion during the current
week, 2026-07-06 to 2026-07-12. The former W5 reliability tasks are pulled into
the same completion sprint. W5 and later now track post-completion operating
research: real model lifecycle management, drift/special-case tracking,
draft/decision governance, large-scale data acquisition/cleaning, AgentOps
reliability, and serving-scale research.

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
    Remote job spec and mac-mini execution  :done, evm041, 2026-07-13, 2026-07-15
    Registry-driven model loading           :done, evm051, 2026-07-16, 2026-07-18
    Serving and remote worker review        :milestone, w3, 2026-07-19, 0d

    section W4 Current-week VLM Completion
    Domain pack foundation                  :done, evm130, 2026-07-06, 2026-07-06
    Quality validation and sharding         :evm134, 2026-07-07, 2026-07-07
    VLM adapter and router                  :evm141, 2026-07-08, 2026-07-08
    Batch inference and schema validation   :evm143, 2026-07-09, 2026-07-09
    Registry gates audit RCA failures       :evm151, 2026-07-10, 2026-07-10
    Metrics CI release demo evidence        :evm171, 2026-07-11, 2026-07-12

    section W5+ Post-completion Research
    Model lifecycle drift draft governance  :evm191, 2026-07-13, 2026-07-19
    Data acquisition cleaning research      :evm201, 2026-07-20, 2026-07-26
    AgentOps and serving scale hardening    :evm211, 2026-07-27, 2026-07-31
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
| W3 | 2026-07-13 ~ 2026-07-19 | Serving and remote jobs | registry-driven API and mac-mini remote job evidence complete |
| W4 | 2026-07-06 ~ 2026-07-12 | Current-week enterprise VLM MLOps completion | industrial dataset quality/sharding, VLM adapter/router, batch inference, reliability gates, metrics, CI, final demo evidence |
| W5 | 2026-07-13 ~ 2026-07-19 | Real model lifecycle, serving, drift, and remote validation | trainable feature model, registry v9, lifecycle/lineage/special-case/RCA artifacts, API inference, visual evidence, Mac mini remote proof |
| W6 | 2026-07-20 ~ 2026-07-26 | Large-scale data acquisition and cleaning research | source policy, batch collection planner, dedup/quality benchmark, lakehouse ingestion research |
| W7 | 2026-07-27 ~ 2026-07-31 | AgentOps and portfolio hardening | AgentOps reliability design, scale serving research, final stabilization |

## Weekly Review Rule

매주 다음을 갱신한다.

- `docs/issues/issue-register.md`
- `docs/status/YYYY-MM-DD-*.md`
- completed issue commit links
- blocked issue list
- next 7-day task list
