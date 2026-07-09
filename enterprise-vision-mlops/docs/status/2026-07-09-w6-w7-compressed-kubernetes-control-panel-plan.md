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

## Interpretation

This update does not require an immediate production-grade Kubernetes cutover.
The near-term goal is to keep the W5 lifecycle visible and repeatable while
preparing the runtime boundary for Kubernetes. The Control Panel should let the
user inspect and operate a full cycle visually instead of opening each
lifecycle, registry, dataset, metrics, Airflow, MLflow, and Kubernetes artifact
by hand.

## Updated Source Files

- `docs/agenda/enterprise-mlops-accelerated-weekly-schedule.md`
- `docs/agenda/enterprise-mlops-implementation-agenda.md`
- `docs/issues/issue-register.md`
