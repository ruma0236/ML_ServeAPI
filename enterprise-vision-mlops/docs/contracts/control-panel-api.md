# Control Panel Metadata And Control API Contract

Issue: `EVM-223` / Jira `SCRUM-101`

This contract defines the backend/UI boundary for the enterprise MLOps Control
Panel. It is intentionally defined before the W7 UI implementation so the UI can
be built against stable cycle, resource, task, model matrix, drift, CD/CT, and
command shapes.

Machine-readable contract:

- `contracts/control-panel/control-panel.openapi.json`

Examples:

- `contracts/control-panel/examples/cycle-run.json`
- `contracts/control-panel/examples/command-intent.json`

## API Areas

| Area | Endpoint | Purpose |
|---|---|---|
| Cycle detail | `GET /control-panel/v1/cycles/{cycle_id}` | return lifecycle, dataset, model, metrics, gate, serving, resources, artifacts |
| Latest cycle | `GET /control-panel/v1/cycles/latest` | dashboard landing state |
| Resource state | `GET /control-panel/v1/resources` | normalized Kubernetes and external worker resource state |
| Orchestrator contracts | `GET /control-panel/v1/orchestrators` | Airflow, MLflow, Kubernetes, and remote worker control connection status |
| Task assignment | `POST /control-panel/v1/tasks` | draft or queue Airflow, MLflow, or Kubernetes work |
| Command intent | `POST /control-panel/v1/commands` | create dry-run or pending command intent |
| Command confirm | `POST /control-panel/v1/commands/{command_id}/confirm` | confirm an auditable action |
| Command cancel | `POST /control-panel/v1/commands/{command_id}/cancel` | cancel a pending action |

## Core Models

### CycleRun

`CycleRun` is the main UI document. It combines:

- Airflow DAG/run reference
- MLflow experiment/run/model reference
- tenant, department, service scope, and environment state
- data pipeline readiness
- experiment/training/evaluation readiness
- dataset version and quality state
- model version and registry state
- model experiment matrix and candidate state
- drift state and drift action
- CD/CT gate state
- metrics and thresholds
- promotion gate decision
- serving readiness
- pipeline stage timeline
- runtime resources
- artifact links

The UI should treat this as the primary detail view for one MLOps cycle.

### OrgContext And EnvironmentRef

`OrgContext` and `EnvironmentRef` make the Control Panel usable as a platform
service rather than a single-user artifact viewer. They capture:

- team and department
- internal-team, internal-department, or external-production scope
- data/model/ops owners
- dev/test/staging/pre-production/production tier
- promotion state
- cluster and namespace

The UI should use these fields for service-scope filters, approval routing, and
environment promotion views.

### DataPipelineReadiness

`DataPipelineReadiness` is the enterprise data-pipeline checklist. It captures:

- source or data contract status
- quality gate status
- lineage status
- replay/backfill readiness
- source policy, quality report, and lineage artifact links

The UI should render this as a data readiness panel before any training or
promotion action.

### ExperimentPipelineReadiness

`ExperimentPipelineReadiness` is the model training/experiment/evaluation
checklist. It captures:

- MLflow tracking status
- evaluation status
- registry status
- promotion readiness
- experiment, model card, and evaluation report links

The UI should render this next to dataset/model cards so reviewers can see why a
candidate can or cannot advance.

### ModelExperimentMatrix

`ModelExperimentMatrix` is the W7 real-model comparison surface. It is designed
for parallel Torch EfficientNet candidates, starting with:

- `efficientnet-b0` for fast real-data iteration and parallel condition search
- `efficientnet-b7` for higher-capacity GPU-bound comparison

Each `ModelCandidate` carries the architecture, Torch/TorchVision backbone,
dataset version, resource profile, run/artifact URI, condition map, metrics, and
promotion blockers. `RealTestPolicy` explicitly marks whether mock or smoke-only
runs are allowed.

For W7 real model work, `mock_allowed=false` and `smoke_allowed=false`. The
Control Panel should show this matrix as a candidate comparison view rather than
as a single opaque model card.

### DriftState

`DriftState` represents data drift, prediction drift, reference/current dataset
versions, drifting columns, report URI, and recommended action. W7 should expose
this as both a dashboard summary and a drilldown before CT or promotion.

### CDCTGate

`CDCTGate` represents the DevOps/MLOps verification boundary. It combines CI,
CD, and CT state into one deploy/promotion gate with required checks, passed
checks, failed checks, CT trigger reason, approver, and promotion blockers.

This should be the UI source for "can this pushed change or retrained model move
forward?".

### PipelineStage

`PipelineStage` powers the live animated timeline. Each stage includes:

- state
- start/end timestamps
- progress
- current step
- failure reason
- metrics
- artifacts
- sample outputs
- resource references

The first W7 UI can render these stages as a status rail with expandable detail
panels.

### RuntimeResource

`RuntimeResource` normalizes Kubernetes and external worker state. It includes:

- namespace, kind, name
- readiness and restarts
- CPU, memory, GPU, and storage requests
- storage claim/root
- node pool
- owner issue
- available control actions
- observation source: live Kubernetes snapshot or CycleRun projection
- observation status and timestamp: live, stale, projected, or unavailable
- Kubernetes reason/message, desired/ready replicas, and GPU capacity

The UI should use this model for the Kubernetes resource topology and resource
management tab.

`RuntimeResourceList` also carries snapshot age, cluster context, source URI,
and collection status. The local W7 bridge writes a sanitized F-drive snapshot
from host `kubectl`; the API does not receive the local kubeconfig. Snapshots
older than the configured threshold must render as stale rather than live.

### OrchestratorConnection

`OrchestratorConnection` prevents the UI from guessing where orchestration is
running. The first W6/W7 boundary declares Airflow as `external-compose` through
`infra/kubernetes/local/airflow-external.yaml`; future Kubernetes-native Airflow
can replace that contract with `in-cluster` resources without changing the UI
control model.

The UI should render:

- orchestrator type
- mode and control mode
- base URL or namespace
- connection status
- supported actions
- config reference

### TaskAssignment

`TaskAssignment` is the control boundary for Airflow, MLflow, or Kubernetes
work. It carries:

- task type
- owner
- priority
- resource profile
- requester team
- environment
- approval policy
- Airflow reference
- MLflow reference
- CD/CT gate preview
- config payload
- dry-run flag

The first implementation should create tasks in dry-run or queued state before
allowing mutation.

### CommandIntent

`CommandIntent` is the safety boundary for operational actions. It represents
Kubernetes, Airflow, and MLflow mutations as explicit, auditable requests.

Allowed initial actions:

- `restart_deployment`
- `scale_deployment`
- `run_pipeline_job`
- `cancel_job`
- `trigger_airflow_dag`
- `pause_airflow_dag`
- `resume_airflow_dag`
- `run_cd_verification`
- `run_ct_evaluation`
- `trigger_drift_review`
- `approve_environment_promotion`
- `promote_model`
- `rollback_model`

Required guardrails:

- actor
- reason
- dry-run state
- confirmation state
- audit trail
- rollback reference when applicable

## UI Contract Notes

The W7 Control Panel should be able to build these views from the contract:

- cycle overview
- data/model cards
- model candidate matrix
- live pipeline timeline
- Kubernetes topology
- resource pressure/detail panel
- task authoring and assignment form
- command confirmation and audit drawer
- stage-level artifact/metric/log/sample-output drilldown

The binding rules are fixed in
`docs/status/2026-07-09-w7-implementation-acceptance-matrix.md`. UI work should
not be closed unless each view reads live `CycleRun` or related API responses.
Static example JSON is allowed for development fixtures only, not as completion
evidence.

## Implementation Notes

- `EVM-224` should implement a read-only aggregator that emits `CycleRun`.
- `EVM-225` should consume `CycleRun`, `RuntimeResource`, `TaskAssignment`, and
  `CommandIntent` shapes, including tenant, drift, CD/CT, and readiness fields.
- `EVM-232` should enforce command states before real mutation is enabled.
- `EVM-233` to `EVM-236` should close the W7 enterprise-readiness gap: service
  tenancy, drift/retraining trigger visibility, CD/CT gates, and data/model
  readiness checklists.
- `EVM-237`, `EVM-238-A`, and `EVM-238-B` should move W7 model proof from
  lifecycle/mock/smoke evidence to Torch EfficientNet-B0/B7 real-test evidence
  with explicit candidate matrices, real-execution policy guards, and evidence
  validation against actual `CycleRun.model_matrix` output.
- Airflow task assignment must use the orchestrator contract first. In W6 local
  Kubernetes this means external Airflow REST API control; in W7+ this can move
  to in-cluster Airflow resources or an operator-backed control mode.
- Actual Airflow/MLflow/Kubernetes write operations should stay behind
  dry-run and confirmation until audit and rollback paths exist.
- Each W7 issue from `EVM-224` to `EVM-238`, including `EVM-238-A` and
  `EVM-238-B`, must close against the acceptance matrix: implementation files,
  input data, output artifacts, verification command, success criteria, and
  failure blocker.
