# 2026-07-09 W7 Enterprise MLOps Readiness Audit

## Trigger

The W7 plan was re-audited against the updated system direction:

- The platform must support team-level, department-level, and business-unit
  operation as an internal enterprise MLOps platform.
- The same lifecycle must also be usable for externally exposed production
  services.
- Data pipelines and model training, experiment, validation, promotion, and
  serving pipelines must be explicit enough for large-company MLOps practice.
- The Control Panel must make the lifecycle understandable and operable through
  UI, not only through files and scripts.
- Drift detection and DevOps/MLOps CD/CT verification must be first-class W7
  requirements before code, data, pipeline, or model updates are promoted.
- W7 model proof should move to real Torch/TorchVision EfficientNet-B0 and
  EfficientNet-B7 candidate runs; mock adapters and smoke-only checks must not
  be accepted as W7 model completion evidence.

## Baseline References

The audit used current industry-facing documentation as a baseline:

- Google Cloud MLOps architecture describes CI, CD, and CT as separate but
  connected production ML concerns. It also calls out triggers such as new
  data, scheduled execution, model performance degradation, and statistical
  data changes.
- Kubeflow Pipelines documents ML workflows as portable, scalable,
  containerized workflows on Kubernetes, with tracking and visualization of
  pipeline definitions, runs, experiments, and ML artifacts.
- Evidently documents drift checks as current-vs-reference dataset comparison,
  with column drift, target/prediction drift, overall dataset drift, and
  retraining or labeling review as possible responses.

References:

- https://docs.cloud.google.com/architecture/architecture-for-mlops-using-tfx-kubeflow-pipelines-and-cloud-build
- https://www.kubeflow.org/docs/components/pipelines/overview/
- https://docs.evidentlyai.com/metrics/preset_data_drift

## Verdict

Before this audit, W7 was directionally correct but not explicit enough for the
new enterprise requirement. Kubernetes, Control Panel, task assignment, and
pipeline visualization were present, but W7 did not force the UI/API to carry:

- team, department, service scope, and owner context;
- environment tier and promotion state;
- data contract, quality, lineage, replay, and backfill readiness;
- model experiment, evaluation, registry, and model-card readiness;
- drift status and action recommendations;
- CD/CT gate status before promotion or deployment.
- real model candidate matrices for parallel Torch EfficientNet-B0/B7
  experimentation;
- an explicit no-mock/no-smoke evidence rule for W7 completion.

After this audit, W7 is now explicit enough as a specification baseline. It is
not yet an implementation-complete enterprise control plane. The W7
implementation is complete only when the API, UI, command workflow, and
verification evidence can show whether a cycle is promotable, blocked, or
requires review across development, staging, pre-production, and production
contexts.

## Audit Matrix

| Requirement | Pre-audit state | W7 supplement added |
|---|---|---|
| Team, department, and production service premise | Mostly implied by Control Panel wording | Added `OrgContext`, `EnvironmentRef`, and `EVM-233` |
| Enterprise data pipeline standard | Data quality and lakehouse work existed, but W7 UI contract did not require readiness aggregation | Added `DataPipelineReadiness` and `EVM-236` |
| Enterprise model pipeline standard | MLflow/model promotion existed, but W7 did not require experiment/evaluation/model-card readiness in cycle view | Added `ExperimentPipelineReadiness` and `EVM-236` |
| UI support for easy operation and analysis | Runtime/resource/task UI was planned, but lifecycle risk states were not mandatory | Extended `CycleRun`, task assignment, and W7 UI acceptance for tenant, environment, drift, and CD/CT drilldown |
| Drift detection | Earlier drift work existed, but W7 did not require drift as a control-plane field | Added `DriftState` and `EVM-234` |
| CD/CT verification for code and pipeline updates | CI existed, but CD/CT was not modeled as a promotion gate | Added `CDCTGate` and `EVM-235` |
| Promotion safety | Promotion gate existed, but did not unify data, model, drift, and delivery checks | `CycleRun` now aggregates readiness, drift, and CD/CT gate state |
| Real model matrix | W5 lifecycle proof was real but still a lightweight feature model; W4 VLM stages used mock adapter evidence | Added Torch EfficientNet-B0/B7 matrix contract, config, and `EVM-237` |
| Real-test evidence policy | Smoke/mock checks were still valid historical scaffolding, but not enough for W7 production-readiness claims | Added no-mock/no-smoke W7 completion policy and `EVM-238` |

## Contract Changes

The Control Panel API contract now uses version
`2026-07-09.w7.realtest.v1`.

New or expanded contract concepts:

- `OrgContext`: team, department, product area, service scope, and
  data/model/ops owners.
- `EnvironmentRef`: environment tier, cluster, namespace, release reference,
  and promotion state.
- `DataPipelineReadiness`: data contract, quality, lineage, replay, source
  policy, and backfill readiness.
- `ExperimentPipelineReadiness`: tracking, evaluation, registry, model-card,
  and promotion readiness.
- `DriftState`: data drift, prediction drift, reference/current dataset
  versions, drift score, drifting columns, report URI, and recommended action.
- `CDCTGate`: CI, CD, CT, required/passed/failed checks, pipeline run URI,
  CT trigger, approver, and blockers.
- `ModelExperimentMatrix`: parallel Torch candidate matrix for real
  EfficientNet-B0/B7 model tests.
- `RealTestPolicy`: explicit W7 evidence policy with mock and smoke completion
  proof disabled.

Affected files:

- `contracts/control-panel/control-panel.openapi.json`
- `contracts/control-panel/examples/cycle-run.json`
- `docs/contracts/control-panel-api.md`
- `configs/w7_efficientnet_real_test.toml`
- `docs/models/w7-efficientnet-real-test-matrix.md`

## Issue Plan Changes

W7 now includes these explicit enterprise-readiness tasks:

| Issue | Purpose |
|---|---|
| `EVM-233` | Enterprise service tenancy and environment scope |
| `EVM-234` | Drift detection and retraining trigger surface |
| `EVM-235` | CD/CT push verification and promotion gate |
| `EVM-236` | Enterprise data/model pipeline readiness checklist |
| `EVM-237` | Torch EfficientNet-B0/B7 real model matrix |
| `EVM-238` | W7 real-test-only evidence policy |

Existing W7 task acceptance was also tightened:

- `EVM-224`: cycle API must aggregate tenant, environment, drift, and CD/CT
  gate state.
- `EVM-225`: dashboard must expose pipeline stage, resource, drift, CD/CT, and
  tenant/environment scope.
- `EVM-228`: final proof must include enterprise-readiness checklist results.
- `EVM-230`: task assignment must show environment, approval policy, and CD/CT
  gate preview.
- `EVM-231`: visualization must show data intake, training, inference,
  serving, drift review, and CD/CT stages.

## W7 Implementation Acceptance

W7 should not be considered enterprise-ready unless all of the following can be
shown in the Control Panel or machine-readable API output:

- which team, department, product area, environment, cluster, and namespace own
  the current cycle;
- which dataset version, source policy, data contract, data quality report,
  lineage record, replay/backfill window, and storage location were used;
- which MLflow experiment/run/model version/model card/evaluation report were
  used for the cycle;
- which real Torch EfficientNet-B0/B7 candidates were evaluated, under which
  conditions, resource profiles, and dataset versions;
- whether current data and prediction behavior drifted from the reference
  dataset/model behavior;
- whether the system recommends no action, label review, retraining,
  promotion block, or rollback review;
- whether CI, CD, and CT checks passed before a code, pipeline, or model update
  can be promoted;
- why a promotion was allowed or blocked.
- that mock adapters, placeholder predictions, and smoke-only runs were not
  used as W7 model completion evidence.

## Verification

Contract syntax checks:

```powershell
python -m json.tool contracts\control-panel\control-panel.openapi.json
python -m json.tool contracts\control-panel\examples\cycle-run.json
python -m json.tool contracts\control-panel\examples\command-intent.json
```

Structured contract check:

```text
paths: 8
schemas: 29
version: 2026-07-09.w7.realtest.v1
```

## Handoff

W7 implementation should start by wiring the read-only aggregation layer first:

1. Build `EVM-224` around the expanded `CycleRun` contract.
2. Render the enterprise-readiness, drift, and CD/CT states in `EVM-225`,
   `EVM-229`, `EVM-230`, and `EVM-231`.
3. Keep all mutation actions behind `EVM-232` command intent and audit
   guardrails.
4. Treat `EVM-233` to `EVM-236` as required W7 acceptance criteria, not
   optional future polish.
5. Treat `EVM-237` and `EVM-238` as the W7 model-evidence gate: real
   Torch/TorchVision EfficientNet-B0/B7 candidates, no mock adapters, and no
   smoke-only completion claims.

The detailed closure rule for each W7 issue is now fixed in
`docs/status/2026-07-09-w7-implementation-acceptance-matrix.md`. No issue from
`EVM-224` through `EVM-238` should be marked Done unless that matrix row has
real implementation files, input data, output artifacts, verification commands,
success criteria, and blocker evidence.
