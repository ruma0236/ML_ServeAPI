# Control Panel Metadata And Control API Contract

Issue: `EVM-223` / Jira `SCRUM-101`

This contract defines the first backend/UI boundary for the enterprise MLOps
Control Panel. It is intentionally defined before the W7 UI implementation so
the UI can be built against stable cycle, resource, task, and command shapes.

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
| Task assignment | `POST /control-panel/v1/tasks` | draft or queue Airflow, MLflow, or Kubernetes work |
| Command intent | `POST /control-panel/v1/commands` | create dry-run or pending command intent |
| Command confirm | `POST /control-panel/v1/commands/{command_id}/confirm` | confirm an auditable action |
| Command cancel | `POST /control-panel/v1/commands/{command_id}/cancel` | cancel a pending action |

## Core Models

### CycleRun

`CycleRun` is the main UI document. It combines:

- Airflow DAG/run reference
- MLflow experiment/run/model reference
- dataset version and quality state
- model version and registry state
- metrics and thresholds
- promotion gate decision
- serving readiness
- pipeline stage timeline
- runtime resources
- artifact links

The UI should treat this as the primary detail view for one MLOps cycle.

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

The UI should use this model for the Kubernetes resource topology and resource
management tab.

### TaskAssignment

`TaskAssignment` is the control boundary for Airflow, MLflow, or Kubernetes
work. It carries:

- task type
- owner
- priority
- resource profile
- Airflow reference
- MLflow reference
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
- live pipeline timeline
- Kubernetes topology
- resource pressure/detail panel
- task authoring and assignment form
- command confirmation and audit drawer
- stage-level artifact/metric/log/sample-output drilldown

## Implementation Notes

- `EVM-224` should implement a read-only aggregator that emits `CycleRun`.
- `EVM-225` should consume `CycleRun`, `RuntimeResource`, `TaskAssignment`, and
  `CommandIntent` shapes.
- `EVM-232` should enforce command states before real mutation is enabled.
- Actual Airflow/MLflow/Kubernetes write operations should stay behind
  dry-run and confirmation until audit and rollback paths exist.
