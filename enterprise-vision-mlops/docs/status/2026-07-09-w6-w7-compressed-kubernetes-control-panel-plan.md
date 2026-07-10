# 2026-07-09 W6/W7 Compressed Kubernetes And Control Panel Plan

## Schedule Decision

The W6 and W7 plan is compressed to finish by next Wednesday,
`2026-07-15`.

| Sprint | New Window | Focus |
|---|---|---|
| W6 | 2026-07-10 to 2026-07-12 | data curation/lakehouse work, Kubernetes resource map, manifest scaffold, metadata API contract |
| W7 | 2026-07-13 to 2026-07-15 | enterprise Control Panel v0, animated Kubernetes/pipeline/resource control UI, Kubernetes real execution proof, governance, serving-scale handoff, final integration review |

## 2026-07-09 Scope Control Update

Review feedback identified W7 scope risk across `EVM-224` through `EVM-238`.
The resolution is not to reduce implementation depth. W7 keeps production-grade
acceptance depth for every task and uses P0/P1/P2 only to define dependency
order, evidence gates, and blocker visibility. W7 now follows the acceptance
matrix in:

- `docs/status/2026-07-09-w7-implementation-acceptance-matrix.md`

Execution order:

1. `EVM-224` read-only aggregation API and `EVM-238-A` real-test policy guard
   are P0.
2. UI work must bind to live `CycleRun` fields, not static example JSON.
3. Kubernetes proof must use `kubectl apply`, pod/job status, logs, and
   artifact evidence.
4. EfficientNet evidence must include MLflow runs, model artifacts, metric
   matrix, split manifest, epoch/step counts, confusion matrices, GPU resource
   profile, Torch/TorchVision/CUDA metadata, and blocked/failure reasons.
5. Airflow/MLflow task authoring remains dry-run/queued/confirm/audit before
   mutation.
6. Mock adapters, placeholder predictions, and smoke-only checks are excluded
   from W7 completion evidence.
7. `EVM-238-B` can close only after actual `CycleRun.model_matrix` and
   EfficientNet evidence exist.
8. W7 empirical artifacts use
   `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/` as the
   source-of-truth evidence root; the repo keeps summaries and indexes.

## Scope Added

New Epic:

- `EVM-EPIC-18` / `SCRUM-98` - Kubernetes Runtime And MLOps Control Panel

New tasks:

- `EVM-221` - Kubernetes runtime resource map
- `EVM-222` - Local Kubernetes manifest scaffold
- `EVM-223` - Control Panel metadata and control API contract
- `EVM-224` - Cycle lineage aggregation API
- `EVM-225` - MLOps Control Panel v0
- `EVM-226` - Kubernetes local real execution proof
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
- `EVM-237` - Torch EfficientNet-B0/B7 real model matrix
- `EVM-238` - W7 real-test-only evidence policy umbrella
- `EVM-238-A` - W7 real-test policy guard
- `EVM-238-B` - W7 real-test evidence validation

## 2026-07-10 Portfolio Evidence Reprioritization

The Control Panel and real EfficientNet matrix are now implemented, but a
second review found that `EVM-233` to `EVM-236` were closed at presentation or
schema depth. They are reopened for operational evidence. The active order is:

1. `EVM-226`: standardize on Docker Desktop Kubernetes and execute the selected
   B7 training Job and serving Deployment. The active serving scope of
   `EVM-227` is absorbed here.
2. `EVM-236`: replace checklist inference with an artifact-content readiness
   evaluator.
3. `EVM-233`: make environment and namespace policy determine promotion
   eligibility.
4. `EVM-235`: gate deployment-intent creation on CI and readiness evidence and
   execute audited deployment states through apply/failure/rollback.
5. `EVM-234`: compare B7 baseline/current predictions and emit a
   review-first `review_required` event.
6. `EVM-228`: close only after one traceable cycle connects all evidence.

The objective review and portfolio claim boundary are in
`docs/reviews/2026-07-10-w7-portfolio-readiness-reprioritization.md`.

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
- `EVM-233` -> `SCRUM-111`
- `EVM-234` -> `SCRUM-112`
- `EVM-235` -> `SCRUM-113`
- `EVM-236` -> `SCRUM-114`
- `EVM-237` -> `SCRUM-115`
- `EVM-238` -> `SCRUM-116`
- `EVM-238-A` -> `SCRUM-117`
- `EVM-238-B` -> `SCRUM-118`

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
- Real model matrix: W7 model work should use Torch/TorchVision
  EfficientNet-B0 and EfficientNet-B7 candidate runs, with parallelizable
  conditions, full VisA split manifests, minimum epoch/step evidence, GPU
  resource profiles, MLflow run references, artifacts, confusion matrices,
  metrics, and promotion blockers surfaced through `CycleRun.model_matrix`.
- Real-test-only evidence: mock adapters, placeholder predictions, and
  smoke-only checks remain historical scaffolding evidence only. They must not
  be used as W7 completion evidence for model or production-readiness claims.

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
| Real model proof | W5 uses a lifecycle proof model and W4 VLM stages use mock adapter evidence | add Torch EfficientNet-B0/B7 candidate matrix and task `EVM-237` |
| Real-test evidence policy | W0-W6 used smoke/mock checks where appropriate for scaffolding | add no-mock/no-smoke W7 completion rule and task `EVM-238` |

## Interpretation

W7 now requires a real local Kubernetes runtime proof before closeout. This is
not a claim of organization-wide production cutover; it is one deeply verified
Docker Desktop Kubernetes path for the selected B7 model. The Control Panel
must show computed readiness, policy, deployment-state, and review-event
results instead of only displaying labels and checklists. W7 is not complete
until one traceable cycle proves whether deployment is allowed, who approved
it, what artifact was applied, what Kubernetes did, and why review or rollback
was required.

## Updated Source Files

- `docs/agenda/enterprise-mlops-accelerated-weekly-schedule.md`
- `docs/agenda/enterprise-mlops-implementation-agenda.md`
- `docs/issues/issue-register.md`
- `docs/status/2026-07-09-w7-implementation-acceptance-matrix.md`
