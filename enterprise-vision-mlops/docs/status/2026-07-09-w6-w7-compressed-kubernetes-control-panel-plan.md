# 2026-07-09 W6/W7 Compressed Kubernetes And Control Panel Plan

## Schedule Decision

The W6 and W7 plan is compressed to finish by next Wednesday,
`2026-07-15`.

| Sprint | New Window | Focus |
|---|---|---|
| W6 | 2026-07-10 to 2026-07-12 | data curation/lakehouse work, Kubernetes resource map, manifest scaffold, metadata API contract |
| W7 | 2026-07-13 to 2026-07-15 | enterprise Control Panel v0, animated Kubernetes/pipeline/resource control UI, Kubernetes smoke proof, governance, serving-scale handoff, final integration review |

## Scope Added

New Epic:

- `EVM-EPIC-18` / `SCRUM-98` - Kubernetes Runtime And MLOps Control Panel

New tasks:

- `EVM-221` - Kubernetes runtime resource map
- `EVM-222` - Local Kubernetes manifest scaffold
- `EVM-223` - Control Panel metadata and control API contract
- `EVM-224` - Cycle lineage aggregation API
- `EVM-225` - MLOps Control Panel v0
- `EVM-226` - Kubernetes local smoke proof
- `EVM-227` - GPU/VLM serving deployment design
- `EVM-228` - Compressed W6/W7 integration review
- `EVM-229` - Kubernetes resource topology and animation UI
- `EVM-230` - Airflow and MLflow task authoring and assignment UI
- `EVM-231` - Live pipeline timeline and intermediate result drilldown
- `EVM-232` - Resource control protocol and audit guardrails
- `EVM-233` - Enterprise service tenancy and environment scope
- `EVM-234` - Drift detection and retraining trigger surface
- `EVM-235` - CD/CT push verification and promotion gate
- `EVM-236` - Enterprise data/model pipeline readiness checklist

## Jira Live Sync

Live Jira sync was applied on `2026-07-09`.

| Sprint | Jira Sprint ID | Issues |
|---|---:|---|
| W6 | 110 | `SCRUM-99` to `SCRUM-101` plus existing W6 data tasks |
| W7 | 111 | `SCRUM-102` to `SCRUM-110` plus existing W7 governance tasks |

New Control Panel detail mapping:

- `EVM-229` -> `SCRUM-107`
- `EVM-230` -> `SCRUM-108`
- `EVM-231` -> `SCRUM-109`
- `EVM-232` -> `SCRUM-110`

## Enterprise Control Panel Addendum

The Control Panel target is expanded from a passive cycle viewer into an
enterprise MLOps operations surface. It should include multiple UI depths or
tabs for:

- Kubernetes control: namespace, node, pod, job, service, PVC, GPU allocation,
  readiness, restart, pressure, and resource placement views with readable
  animated state transitions.
- Pipeline control: data intake, validation, image-quality gate, training,
  registry, inference, serving, and monitoring stages shown as an animated live
  timeline.
- Task assignment: Airflow DAG/run and MLflow experiment/run work can be
  drafted, edited, validated, assigned to an owner/resource profile, and queued
  through a controlled command protocol.
- Resource management: CPU, GPU, memory, storage, PVC, object-store, and remote
  worker capacity are surfaced as operational constraints instead of hidden
  script parameters.
- Result drilldown: each pipeline stage exposes artifacts, metrics, logs,
  sample outputs, promotion blockers, and failure reasons from the UI.
- Control protocol: Kubernetes, Airflow, and MLflow mutations are represented as
  explicit command intents with dry-run, confirm, apply, cancel, rollback, actor,
  and audit fields.
- Service tenancy: team, department, service scope, data/model/ops owners,
  namespace, environment tier, and approval policy are first-class fields, so
  the same platform can support team-level, department-level, and external
  production operations.
- Drift and CT control: data drift, prediction drift, drift report links,
  label-review/retraining actions, and CT trigger reason are visible before
  model promotion.
- CD verification: push/PR checks, image build, kustomize render, data quality,
  model evaluation, and drift review are represented as one deploy/promotion
  gate instead of scattered logs.

## 2026-07-09 Enterprise Readiness Re-Audit

The W7 plan was re-audited against an enterprise MLOps operating premise:

- Internal platform service: multiple teams or departments can request,
  inspect, validate, and operate data/model cycles through a shared UI.
- External production service: deploy/promote actions require environment,
  owner, approval, rollback, and CD/CT evidence.
- Data pipeline standard: source policy, schema/quality gate, lineage,
  replay/backfill, storage location, and drift baseline are required.
- Model pipeline standard: MLflow run, model version, evaluation report,
  registry/promotion state, model card/dashboard, rollback route, and owner
  approval are required.
- DevOps/AIOps standard: code push and config changes must pass CI/CD checks,
  and new data or drift must trigger CT evaluation before serving changes.

Audit result:

| Area | W7 Before Re-Audit | Required Supplement |
|---|---|---|
| Team/department service use | implicit in Control Panel wording | add tenant/environment/owner fields and task `EVM-233` |
| External production use | partial via command guardrails | add environment promotion state, approval policy, CD/CT gate |
| Data pipeline readiness | partially covered by data quality/lineage artifacts | expose source policy, quality, lineage, replay/backfill and drift baseline |
| Model pipeline readiness | partially covered by MLflow/model registry references | expose eval report, model card/dashboard, registry, rollback, approval |
| Drift detection | earlier W5 artifacts exist, but W7 UI contract did not require it | add `DriftState` and task `EVM-234` |
| CD/CT push verification | CI exists, but W7 did not treat CI/CD/CT as a promotion gate | add `CDCTGate` and task `EVM-235` |
| UI usability | timeline/resource/task views planned | add explicit drift/CDCT/readiness drilldowns and service-scope filters |

## Interpretation

This update does not require an immediate production-grade Kubernetes cutover.
The near-term goal is to keep the W5 lifecycle visible and repeatable while
preparing the runtime boundary for Kubernetes. The Control Panel should let the
user inspect and operate a full cycle visually instead of opening each
lifecycle, registry, dataset, metrics, Airflow, MLflow, and Kubernetes artifact
by hand. W7 is therefore not complete unless it can show whether a cycle is
safe to promote across environments and why a drift/CD/CT gate blocked or
allowed the next action.

## Updated Source Files

- `docs/agenda/enterprise-mlops-accelerated-weekly-schedule.md`
- `docs/agenda/enterprise-mlops-implementation-agenda.md`
- `docs/issues/issue-register.md`
