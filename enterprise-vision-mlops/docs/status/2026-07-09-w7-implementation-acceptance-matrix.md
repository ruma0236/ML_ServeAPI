# 2026-07-09 W7 Implementation Acceptance Matrix

## Purpose

This matrix turns the W7 review findings into execution rules. W7 is broad, but
the implementation depth must not be reduced. The P0/P1/P2 tiers define
dependency order and evidence gates only; every issue still needs production-
grade implementation files, real inputs, output artifacts, verification
commands, success criteria, and blocker rules before it can close.

This is the live implementation-control document. Completed UI/schema
foundations remain valid, but an issue is reopened when later review shows that
its operational behavior was represented rather than executed.

## Scope Control

W7 completion claims are ordered by dependency, not by reduced depth:

| Tier | Scope | Rule |
|---|---|---|
| Foundation complete | `EVM-224`, `EVM-225`, `EVM-229`, `EVM-230`, `EVM-231`, `EVM-232`, `EVM-237`, `EVM-238` | Preserve the live API, Control Panel, real EfficientNet matrix, and evidence validators as dependencies. |
| P0 runtime proof | `EVM-226` with `EVM-227` execution scope absorbed | Fix Docker Desktop Kubernetes as the W7 cluster and execute the selected B7 training Job and serving Deployment with GPU and artifact evidence. |
| P1 policy path | `EVM-236` -> `EVM-233` -> `EVM-235` | Evaluate evidence, apply environment/namespace promotion policy, then create and execute an audited deployment intent. |
| P2 review path | `EVM-234` | Emit a measured B7 `review_required` event from real current inputs without automatic retraining. |
| Closeout | `EVM-228` | Replay one traceable cycle only after P0/P1/P2 execution evidence is complete. |

Hard rules:

- `EVM-224` is the implementation dependency for all UI work.
- Tiers are sequencing gates only. P0/P1/P2 work must be implemented to the
  same acceptance depth before being marked Done.
- UI completion cannot be claimed from `contracts/control-panel/examples/cycle-run.json` alone.
- Airflow remains `external-compose`; W7 must not claim in-cluster Airflow
  migration unless new resources are actually added and verified.
- Kubernetes proof means Docker Desktop Kubernetes, `nvidia.com/gpu`
  scheduling, `kubectl apply`, pod/job status, probes, logs, serving requests,
  failure evidence, and artifacts, not manifest existence.
- EfficientNet proof means real Torch/TorchVision training/evaluation,
  MLflow run ids, model artifacts, metrics, resource profiles, and blocker
  reasons.
- Mock adapters, placeholder predictions, synthetic-only fixtures, and
  smoke-only runs are not W7 completion evidence.
- UI fields and schema objects are not sufficient completion evidence for
  tenancy policy, CD/CT, readiness, or drift behavior.
- The detailed reprioritization and portfolio claim boundary are recorded in
  `docs/reviews/2026-07-10-w7-portfolio-readiness-reprioritization.md`.

## Evidence Root Rule

W7 empirical evidence uses the F-drive data root as the source of truth:

- Source-of-truth evidence root:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/`
- Control Panel API captures:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel/<run_id>/`
- Control Panel UI test evidence:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel_ui/<run_id>/`
- EfficientNet model evidence:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/efficientnet/<matrix_id>/<candidate_id>/`
- Kubernetes B7 runtime evidence:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/kubernetes_b7/<run_id>/`
- Readiness, deployment-intent, and drift evidence:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/{readiness,deployment_intents,drift_review}/<run_id>/`

The repository should keep only source code, contracts, docs, summaries, and
small evidence indexes. Large model/data artifacts must not be treated as
repo-relative completion evidence.

## UI Field Binding

The Control Panel UI must consume these `CycleRun` fields:

| UI Area | Required API Fields |
|---|---|
| Cycle overview | `cycle_id`, `status`, `started_at`, `finished_at`, `owner_issue`, `tenant`, `environment` |
| Data readiness | `dataset`, `data_pipeline`, `stages[*].metrics`, `stages[*].artifacts` |
| Model card | `model`, `mlflow`, `experiment_pipeline`, `promotion_gate`, `metrics` |
| Model matrix | `model_matrix.real_test_policy`, `model_matrix.candidates[*]` |
| Pipeline timeline | `stages[*].stage_id`, `status`, `progress`, `current_step`, `failure_reason`, `metrics`, `artifacts`, `sample_outputs`, `resources` |
| Kubernetes topology | `resources[*]`, `stages[*].resources`, `airflow.mode`, `airflow.control_mode` |
| Drift review | `drift.status`, `data_drift_status`, `prediction_drift_status`, `reference_dataset_version`, `current_dataset_version`, `drifting_columns`, `report_uri`, `action` |
| CD/CT gate | `cdct_gate.status`, `ci_status`, `cd_status`, `ct_status`, `required_checks`, `passed_checks`, `failed_checks`, `promotion_blockers` |
| Task authoring | `TaskAssignment`, `AirflowRef`, `MLflowRef`, `CDCTGate`, `EnvironmentRef` |
| Command audit | `CommandIntent.status`, `actor`, `reason`, `dry_run`, `audit`, `rollback_command_id` |

## Issue-Level Acceptance Matrix

### EVM-224 - Cycle Lineage Aggregation API

- Implementation files:
  - `apps/api/main.py`
  - `apps/api/control_panel.py` or equivalent router module
  - `src/evm/control_panel/aggregation.py`
  - `src/evm/control_panel/schemas.py`
  - `src/evm/control_panel/validate_cycle_run.py`
  - `tests/test_control_panel_contract.py`
  - `tests/test_control_panel_aggregation.py`
- Input data:
  - `contracts/control-panel/control-panel.openapi.json`
  - `contracts/control-panel/examples/cycle-run.json`
  - `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/registry/vision-baseline/latest.json`
  - MLflow tracking URI
  - Airflow DAG/run metadata from external Compose Airflow
  - Prometheus/API serving state
  - lifecycle, curation, lakehouse, quality, drift, and model-matrix artifacts
- Output artifact:
  - live `GET /control-panel/v1/cycles/latest` response
  - live `GET /control-panel/v1/cycles/{cycle_id}` response
  - captured JSON under `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel/<run_id>/cycle_run.json`
  - schema validation report under `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel/<run_id>/cycle_run_schema_validation.json`
  - repo-side summary/index under `docs/status/`
- Verification command:
  - `python -m json.tool contracts\control-panel\examples\cycle-run.json`
  - `curl.exe -fsS http://localhost:8000/control-panel/v1/cycles/latest -o $env:TEMP\evm-cycle-run-latest.json`
  - `python -m evm.control_panel.validate_cycle_run --openapi contracts\control-panel\control-panel.openapi.json --component CycleRun --input $env:TEMP\evm-cycle-run-latest.json`
  - `python -m pytest tests\test_control_panel_contract.py tests\test_control_panel_aggregation.py -q`
- Success criteria:
  - response conforms to the OpenAPI `CycleRun` component and the Pydantic
    `CycleRun` model;
  - response uses real local artifacts, not only the example JSON;
  - missing upstream evidence is marked `unknown`, `blocked`, or `not_available`
    explicitly;
  - includes tenant/environment, data readiness, model readiness, drift,
    CD/CT, and model matrix fields.
- Failure blocker:
  - UI work is blocked if this endpoint is missing or returns a static fixture.

### EVM-225 - MLOps Control Panel v0

- Implementation files:
  - `apps/control-panel/package.json` with `lint`, `test`, `build`, and
    `test:e2e` scripts
  - `apps/control-panel/package-lock.json`
  - `apps/control-panel/playwright.config.ts` with `chromium` and
    `MobileChrome` projects
  - `apps/control-panel/vite.config.ts`
  - `apps/control-panel/src/main.tsx`
  - `apps/control-panel/src/App.tsx`
  - `apps/control-panel/src/api/controlPanelClient.*`
  - `apps/control-panel/src/views/CycleOverview.*`
  - `apps/control-panel/src/views/DataModelReadiness.*`
  - `apps/control-panel/src/views/GateAndRiskPanel.*`
  - `tests/control-panel/cycle-overview.spec.ts`
  - `tests/control-panel/cycle-overview.contract.test.ts`
- Input data:
  - live `CycleRun` from `EVM-224`
  - `RuntimeResource[]`
  - `OrchestratorConnection[]`
  - `TaskAssignment` and `CommandIntent` schemas
- Output artifact:
  - screenshots or video under `docs/assets/w7-control-panel/`
  - UI test report under `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel_ui/<run_id>/`
- Verification command:
  - `npm --prefix apps/control-panel install`
  - `npm --prefix apps/control-panel run lint`
  - `npm --prefix apps/control-panel run test`
  - `npm --prefix apps/control-panel run build`
  - `npm --prefix apps/control-panel run test:e2e -- --project=chromium`
  - `python -m pytest tests/test_control_panel_contract.py tests/test_control_panel_aggregation.py -q`
- Success criteria:
  - UI binds to live API responses;
  - dashboard shows cycle, data, model, drift, CD/CT, and model matrix state;
  - blocked states and missing evidence are visible;
  - no completion claim from static screenshots alone.
- Failure blocker:
  - blocked if the UI reads only `cycle-run.json` or hides blockers.

### EVM-226 - Docker Desktop Kubernetes B7 Training And Serving Proof

This issue absorbs the active execution scope of `EVM-227`. Docker Desktop
Kubernetes is the only W7 local-cluster target. The existing `EVM-227` design
and inventory remain historical inputs, not independent production-serving
evidence.

- Implementation files:
  - `infra/kubernetes/model-runtime/namespaces.yaml`
  - `infra/kubernetes/model-runtime/b7-training-job.yaml`
  - `infra/kubernetes/model-runtime/b7-serving-deployment.yaml`
  - `infra/kubernetes/model-runtime/kustomization.yaml`
  - `infra/docker/efficientnet-training/Dockerfile`
  - `apps/api` B7 inference loader and probe contract
  - `scripts/dev/w7_kubernetes_b7_execution_proof.ps1`
  - `tests/test_kubernetes_b7_manifests.py`
  - `docs/status/YYYY-MM-DD-w7-kubernetes-b7-execution-proof.md`
- Input data:
  - Docker Desktop Kubernetes context `docker-desktop`
  - node allocatable resource `nvidia.com/gpu`
  - selected candidate `effnet-b7-img600-finetune-adamw`
  - VisA dataset version and immutable split-manifest digest
  - source MLflow run `a4e2763b28ae494ea67944084edd4b3f`
  - F-drive data, evidence, and model artifact roots
  - immutable training and serving image digests
- Output artifact:
  - cluster/GPU preflight JSON
  - manifest render and `kubectl apply` logs
  - `evm-training` Job status, events, logs, resource usage, MLflow run, and
    model artifact checksum
  - `evm-staging` Deployment/ReplicaSet/Pod status, rollout history,
    readiness/liveness results, and inference request/response
  - one controlled failed Job or rollout with events and failure logs
  - rollback target and rollback result
  - evidence index under
    `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/kubernetes_b7/<run_id>/`
- Verification command:
  - `docker desktop kubernetes status`
  - `kubectl config use-context docker-desktop`
  - `kubectl get node -o jsonpath="{.items[0].status.allocatable.nvidia\.com/gpu}"`
  - `kubectl kustomize infra/kubernetes/model-runtime`
  - `kubectl apply -k infra/kubernetes/model-runtime`
  - `kubectl wait --for=condition=complete job/evm-b7-training -n evm-training --timeout=7200s`
  - `kubectl logs -n evm-training job/evm-b7-training --all-containers=true`
  - `kubectl rollout status deployment/evm-b7-serving -n evm-staging --timeout=600s`
  - `kubectl get pods,jobs,deploy,rs,svc -n evm-training -o wide`
  - `kubectl get pods,jobs,deploy,rs,svc -n evm-staging -o wide`
  - `kubectl describe job/evm-b7-training -n evm-training`
  - `kubectl describe deployment/evm-b7-serving -n evm-staging`
  - `scripts\dev\w7_kubernetes_b7_execution_proof.ps1`
- Success criteria:
  - the node advertises a schedulable GPU and both workloads request and limit
    `nvidia.com/gpu: 1`;
  - the B7 Job uses the declared real VisA split and writes a traceable model
    artifact plus MLflow run;
  - the serving Deployment loads the selected artifact by digest, becomes
    ready, survives liveness checks, and returns a real inference result;
  - logs, events, resource use, artifact checksums, failure evidence, and
    rollback evidence are captured;
  - no CPU fallback or manifest-only result is accepted.
- Failure blocker:
  - blocked if Docker Desktop Kubernetes is disabled, the node lacks
    `nvidia.com/gpu`, storage cannot be mounted, Job/Deployment probes fail,
    model identity is mutable, or expected artifacts are not produced.

### EVM-227 - Serving Design Record, Execution Absorbed By EVM-226

- Historical result:
  - GPU inventory, runtime comparison, and worker placement design are complete
    in `docs/status/2026-07-10-w7-gpu-vlm-serving-deployment-design.md`.
- Active rule:
  - no separate production-serving completion claim is permitted from this
    issue;
  - B7 packaging, Kubernetes serving, probes, GPU scheduling, inference, and
    rollback evidence are accepted only under `EVM-226`;
  - Triton, KServe, Ray Serve, and vLLM remain future alternatives until a
    measured need justifies them.

### EVM-228 - Compressed W6/W7 Integration Review

- Implementation files:
  - `docs/status/YYYY-MM-DD-w7-integration-review.md`
  - `docs/reviews/YYYY-MM-DD-w7-final-review.md`
- Input data:
  - all W7 issue evidence
  - Git commit hashes
  - Jira issue states
  - Notion page URLs
  - Obsidian work logs
- Output artifact:
  - final W7 integration review
  - evidence index
  - known-risk and blocker register
- Verification command:
  - `git status --short --branch`
  - Jira query for `SCRUM-102` to `SCRUM-116`
  - Notion and Obsidian lookup checks
- Success criteria:
  - no W7 issue is marked Done without implementation files, inputs, outputs,
    verification, success criteria, and blocker evidence;
  - remaining gaps are explicitly labeled.
- Failure blocker:
  - blocked if any closure relies on mock, placeholder, or smoke-only evidence.

### EVM-229 - Kubernetes Resource Topology And Animation UI

- Implementation files:
  - `apps/control-panel/src/views/KubernetesTopology.*`
  - `apps/control-panel/src/components/ResourceNode.*`
  - `apps/control-panel/src/components/ResourceDetailDrawer.*`
  - `tests/control-panel/kubernetes-topology.spec.ts`
  - `tests/control-panel/kubernetes-topology.contract.test.ts`
- Input data:
  - `GET /control-panel/v1/resources`
  - `RuntimeResource[]`
  - Kubernetes status from `EVM-226`
- Output artifact:
  - topology screenshot/video
  - UI test report
- Verification command:
  - `npm --prefix apps/control-panel run test:e2e -- --project=chromium --grep "@w7-kubernetes-topology"`
  - `npm --prefix apps/control-panel run test:e2e -- --project=MobileChrome --grep "@w7-kubernetes-topology"`
  - `npm --prefix apps/control-panel run test -- --run tests/control-panel/kubernetes-topology.contract.test.ts`
- Success criteria:
  - namespace, pod/job/service/PVC/GPU/resource pressure states are visible;
  - animation reflects actual status transitions;
  - failed/crashloop/unknown states are visible.
- Failure blocker:
  - blocked if topology is hand-drawn static art or not backed by resource API.

### EVM-230 - Airflow And MLflow Task Authoring And Assignment UI

- Implementation files:
  - `apps/control-panel/src/views/TaskAuthoring.*`
  - `apps/control-panel/src/api/taskAssignments.*`
  - `apps/api/control_panel_tasks.py`
  - `tests/test_control_panel_tasks.py`
- Input data:
  - `TaskAssignmentRequest`
  - `AirflowRef`
  - `MLflowRef`
  - `EnvironmentRef`
  - `CDCTGate`
  - external Airflow contract from `infra/kubernetes/local/airflow-external.yaml`
- Output artifact:
  - dry-run task assignment object
  - queued task preview
  - audit entry
- Verification command:
  - `pytest tests/test_control_panel_tasks.py -q`
  - UI test creating dry-run and queued assignments
- Success criteria:
  - supports `dry_run`, `queued`, `pending_confirmation`, and `blocked` states;
  - shows Airflow mode as `external-compose`;
  - does not trigger mutation before confirmation and audit state exist.
- Failure blocker:
  - blocked if UI directly mutates Airflow/MLflow or hides audit state.

### EVM-231 - Live Pipeline Timeline And Intermediate Result Drilldown

- Implementation files:
  - `apps/control-panel/src/views/PipelineTimeline.*`
  - `apps/control-panel/src/components/StageDetail.*`
  - `tests/control-panel/pipeline-timeline.spec.ts`
  - `tests/control-panel/pipeline-timeline.contract.test.ts`
- Input data:
  - `CycleRun.stages[*]`
  - `PipelineStage.metrics`
  - `PipelineStage.artifacts`
  - `PipelineStage.sample_outputs`
  - `PipelineStage.failure_reason`
- Output artifact:
  - timeline screenshot/video
  - stage drilldown evidence capture
- Verification command:
  - `npm --prefix apps/control-panel run test:e2e -- --project=chromium --grep "@w7-pipeline-timeline"`
  - `npm --prefix apps/control-panel run test:e2e -- --project=MobileChrome --grep "@w7-pipeline-timeline"`
  - `npm --prefix apps/control-panel run test -- --run tests/control-panel/pipeline-timeline.contract.test.ts`
- Success criteria:
  - current stage, completed stages, blocked stages, artifacts, metrics, logs,
    sample outputs, and failure reasons are readable;
  - no stage is shown as pass without artifact/metric evidence.
- Failure blocker:
  - blocked if the timeline is decorative or lacks stage evidence drilldown.

### EVM-232 - Resource Control Protocol And Audit Guardrails

- Implementation files:
  - `apps/api/control_panel_commands.py`
  - `src/evm/control_panel/commands.py`
  - `apps/control-panel/src/views/CommandDrawer.*`
  - `tests/test_control_panel_commands.py`
- Input data:
  - `CommandIntentRequest`
  - `ResourceRef`
  - actor, reason, dry-run flag, parameters
- Output artifact:
  - command intent JSON
  - audit event list
  - rollback/cancel references where applicable
- Verification command:
  - `pytest tests/test_control_panel_commands.py -q`
  - UI dry-run/confirm/cancel interaction test
- Success criteria:
  - command lifecycle supports `draft`, `dry_run`, `pending_confirmation`,
    `applying`, `applied`, `cancelled`, `failed`, and `rolled_back`;
  - mutation is impossible before confirmation;
  - audit trail records actor, reason, target, and result.
- Failure blocker:
  - blocked if commands directly mutate resources without intent/audit state.

### EVM-233 - Enterprise Service Tenancy And Environment Scope

- Implementation files:
  - `src/evm/control_panel/org_context.py`
  - `src/evm/control_panel/environment.py`
  - `src/evm/control_panel/promotion_policy.py`
  - `configs/promotion_policy.toml`
  - `apps/control-panel/src/views/ServiceScopeFilters.*`
  - `tests/test_promotion_policy.py`
- Input data:
  - `OrgContext`
  - `EnvironmentRef`
  - target environment and namespace
  - requester and approver identities
  - immutable image/model artifact digests
  - `EVM-236` readiness decision
  - `EVM-235` CI/CD/CT gate decision and rollback reference
- Output artifact:
  - computed `allow`, `pending_approval`, or `blocked` policy decision
  - explicit policy reason codes
  - tenant/environment fields and decision in `CycleRun`
  - audit record containing policy version and evaluated inputs
- Verification command:
  - `python -m pytest tests/test_promotion_policy.py -q`
  - `python -m evm.control_panel.promotion_policy --policy configs/promotion_policy.toml --fixture tests/fixtures/promotion/staging-pass.json`
  - `python -m evm.control_panel.promotion_policy --policy configs/promotion_policy.toml --fixture tests/fixtures/promotion/production-missing-approval.json`
- Success criteria:
  - staging can queue only when readiness, CI, namespace, and ownership policy
    pass;
  - production additionally requires an allow-listed production namespace,
    all gates, rollback reference, immutable digests, and an approver different
    from the requester;
  - unknown ownership, namespace mismatch, or missing approval blocks intent
    creation with deterministic reasons.
- Failure blocker:
  - blocked if environment/namespace values are display-only or policy outcome
    is supplied by the caller.

### EVM-234 - Drift Detection And Retraining Trigger Surface

- Implementation files:
  - `src/evm/control_panel/drift.py`
  - `src/evm/pipelines/drift_review/run.py`
  - `configs/b7_drift_policy.toml`
  - `apps/control-panel/src/views/DriftReview.*`
  - `tests/test_control_panel_drift.py`
- Input data:
  - selected B7 validation baseline predictions and confidence values
  - current real-input window predictions and confidence values
  - baseline/current dataset versions and window timestamps
  - reviewed drift thresholds
- Output artifact:
  - `DriftState` in `CycleRun`
  - measured drift report with distribution and confidence deltas
  - `review_required` event with evidence URI, threshold, and reason
  - label-review queue reference
- Verification command:
  - `python -m pytest tests/test_control_panel_drift.py -q`
  - `python scripts/run_pipeline.py drift-review --config configs/local_visa.toml`
  - `curl.exe -fsS http://localhost:8000/control-panel/v1/cycles/latest -o $env:TEMP/evm-cycle-run-latest.json`
  - `python -m evm.control_panel.validate_cycle_run --openapi contracts/control-panel/control-panel.openapi.json --component CycleRun --input $env:TEMP/evm-cycle-run-latest.json`
- Success criteria:
  - the report compares a real B7 baseline with a distinct real current window;
  - documented distribution and confidence statistics drive the decision;
  - exceeded policy emits `review_required` and routes to label review and
    approval;
  - no automatic retraining, deployment, or promotion is triggered.
- Failure blocker:
  - blocked if drift is inferred from queue length, synthetic data, or a
    heuristic score without measured baseline/current predictions.

### EVM-235 - CD/CT Push Verification And Promotion Gate

- Implementation files:
  - `.github/workflows/ci.yml`
  - `.github/workflows/deployment-intent.yml`
  - `src/evm/control_panel/cdct.py`
  - `src/evm/control_panel/deployment_intents.py`
  - `src/evm/control_panel/deployment_executor.py`
  - `apps/control-panel/src/views/CDCTGatePanel.*`
  - `tests/test_control_panel_cdct.py`
  - `tests/test_deployment_intents.py`
- Input data:
  - immutable CI evidence bundle containing commit SHA, workflow run id, test
    result, evidence-validator result, image digest, and config-render digest
  - `EVM-236` readiness decision
  - `EVM-233` environment policy decision
  - actor, approver, target namespace, rollback reference, and CT trigger
- Output artifact:
  - validated CI/CD/CT gate report
  - deployment intent ledger
  - state-transition audit events
  - Kubernetes apply/failed/rollback result linked to `EVM-226`
- Verification command:
  - `python -m pytest tests/test_control_panel_cdct.py tests/test_deployment_intents.py -q`
  - `python -m evm.control_panel.deployment_intents validate-ci --input $env:TEMP/ci-evidence.json`
  - `$intent = python -m evm.control_panel.deployment_intents create --input $env:TEMP/staging-intent.json --dry-run | ConvertFrom-Json`
  - `python -m evm.control_panel.deployment_intents request-approval --intent-id $intent.intent_id`
  - `python -m evm.control_panel.deployment_intents approve --intent-id $intent.intent_id --actor portfolio-approver`
  - `python -m evm.control_panel.deployment_intents queue --intent-id $intent.intent_id`
  - `python -m evm.control_panel.deployment_executor apply --intent-id $intent.intent_id`
- Success criteria:
  - deployment intent creation is impossible when CI or `EVM-236` fails;
  - lifecycle follows `dry_run -> pending_approval -> queued -> applying ->
    applied`, with `failed` and `rolled_back` branches;
  - production cannot queue without the `EVM-233` approver and namespace
    policy;
  - every transition records actor, timestamp, environment, namespace,
    artifact digest, reason, and result;
  - only the executor can mutate Kubernetes and only from `queued`.
- Failure blocker:
  - blocked if CI/CD/CT fields are hard-coded, deployment intent can be created
    before validators pass, or UI/API calls Kubernetes directly.

### EVM-236 - Enterprise Data/Model Evidence Readiness Evaluator

- Implementation files:
  - `src/evm/control_panel/readiness.py`
  - `src/evm/control_panel/readiness_evaluator.py`
  - `src/evm/control_panel/validate_readiness.py`
  - `apps/control-panel/src/views/ReadinessChecklist.*`
  - `tests/test_readiness_evaluator.py`
- Input data:
  - parsed data contract and schema version
  - split manifest, record counts, dataset version, and digest
  - lineage graph and source identity
  - quality-gate report and threshold values
  - MLflow run status, parameters, metrics, and artifact URI
  - evaluation report, model card identity, model artifact checksum, and
    rollback reference
- Output artifact:
  - `DataPipelineReadiness`
  - `ExperimentPipelineReadiness`
  - machine-readable readiness evaluation report with `ready` or `blocked`,
    blocker codes, evidence URIs, digests, and evaluation timestamp
- Verification command:
  - `python -m pytest tests/test_readiness_evaluator.py -q`
  - `python -m evm.control_panel.validate_readiness --cycle http://localhost:8000/control-panel/v1/cycles/latest --output $env:TEMP/readiness-evaluation.json`
  - `python -m json.tool $env:TEMP/readiness-evaluation.json`
- Success criteria:
  - evaluator parses artifact content instead of checking only file existence;
  - dataset identity agrees across contract, split, lineage, MLflow run, model
    card, and model artifact;
  - missing, stale, malformed, mismatched, or failing evidence returns
    `blocked` with deterministic blocker codes;
  - the same inputs reproduce the same decision and evidence digests.
- Failure blocker:
  - blocked if readiness is inferred from a single status, caller-supplied
    booleans, or path existence alone.

### EVM-237 - Torch EfficientNet-B0/B7 Real Model Matrix

- Implementation files:
  - `configs/w7_efficientnet_real_test.toml`
  - `src/evm/pipelines/efficientnet_training/run.py`
  - `src/evm/core/torch_efficientnet.py`
  - `tests/test_efficientnet_real_test_matrix.py`
  - `docs/models/w7-efficientnet-real-test-matrix.md`
- Input data:
  - real VisA dataset, `visa-open-data-f1f1c9ee9922`
  - full split manifest under F-drive dataset root
  - `configs/w7_efficientnet_real_test.toml`
  - Windows RTX 4080 SUPER GPU resource
  - MLflow tracking URI
  - Torch/TorchVision/CUDA runtime metadata
- Output artifact:
  - one MLflow run per candidate
  - model artifact per candidate under `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/efficientnet/<matrix_id>/<candidate_id>/`
  - metric matrix JSON
  - split manifest snapshot
  - training history with epoch and optimizer-step counts
  - confusion matrix JSON and PNG per candidate
  - GPU profile JSON with CUDA device, peak memory, and utilization samples
  - environment JSON with `torch`, `torchvision`, CUDA, Python, and driver data
  - model card or lifecycle dashboard
  - `CycleRun.model_matrix`
- Verification command:
  - `python scripts/run_pipeline.py efficientnet-training --config configs/w7_efficientnet_real_test.toml`
  - `pytest tests/test_efficientnet_real_test_matrix.py tests/test_w7_real_test_policy.py -q`
- Success criteria:
  - EfficientNet-B0 and EfficientNet-B7 candidates have run ids, artifacts,
    metrics, resource profiles, and blocker reasons;
  - split manifest proves at least 10,821 VisA records with at least 6,504
    train images, 2,136 validation images, and 2,181 test images;
  - fixed seed `20260709` is logged for split, model initialization, and data
    loader workers;
  - EfficientNet-B0 candidates run at least 5 epochs and EfficientNet-B7
    candidates run at least 3 epochs, with optimizer-step counts recorded
    against `min_epochs_b0` and `min_epochs_b7` config thresholds;
  - `torch` and `torchvision` versions plus CUDA availability, device name,
    total memory, and peak memory are captured;
  - confusion matrix and per-class metrics exist for every candidate;
  - GPU profile is recorded;
  - failed candidates are recorded as blocked, not hidden.
- Failure blocker:
  - blocked if only the config exists, if there is no MLflow run, or if no
    model artifact/metrics are produced;
  - blocked if the run uses fewer records, epochs, or runtime evidence than
    the acceptance thresholds without an explicit blocker reason.

### EVM-238 - W7 Real-Test-Only Evidence Policy Umbrella

`EVM-238` remains the umbrella Jira issue for W7 real-test-only evidence. Its
closure is split to avoid marking the policy complete before the real
EfficientNet evidence exists.

- `EVM-238-A`: implement the no-mock/no-smoke policy guard.
- `EVM-238-B`: validate actual `CycleRun.model_matrix` and EfficientNet
  evidence after `EVM-237` produces runs, metrics, and artifacts.

`EVM-238` cannot be marked Done until both `EVM-238-A` and `EVM-238-B` are
closed.

### EVM-238-A - W7 Real-Test Policy Guard

- Implementation files:
  - `src/evm/control_panel/real_test_policy.py`
  - `tests/test_w7_real_test_policy.py`
  - `configs/w7_efficientnet_real_test.toml`
  - `docs/status/YYYY-MM-DD-w7-real-test-policy.md`
- Input data:
  - `RealTestPolicy`
  - task closure records
  - model and pipeline evidence metadata
- Output artifact:
  - real-test policy validation report
  - blocked evidence report for mock, placeholder, or smoke-only claims
- Verification command:
  - `pytest tests/test_w7_real_test_policy.py -q`
  - policy check against latest `CycleRun`
- Success criteria:
  - `mock_allowed=false`;
  - `smoke_allowed=false`;
  - placeholder predictions block model readiness;
  - smoke-only checks cannot mark W7 model work Done.
- Failure blocker:
  - blocked if any W7 closure record uses mock/smoke-only evidence as the
    completion proof.

### EVM-238-B - W7 Real-Test Evidence Validation

- Implementation files:
  - `src/evm/control_panel/real_test_policy.py`
  - `src/evm/control_panel/aggregation.py`
  - `tests/test_w7_real_test_evidence_validation.py`
  - `docs/status/YYYY-MM-DD-w7-real-test-evidence-validation.md`
- Input data:
  - live `CycleRun.model_matrix`
  - EfficientNet MLflow run ids from `EVM-237`
  - candidate artifact directories under `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/efficientnet/`
  - split manifest, metric matrix, confusion matrices, GPU profiles, and
    environment reports
- Output artifact:
  - real-test evidence validation report
  - blocked candidate report for incomplete or non-real evidence
- Verification command:
  - `python scripts/run_pipeline.py efficientnet-training --config configs/w7_efficientnet_real_test.toml`
  - `pytest tests/test_efficientnet_real_test_matrix.py tests/test_w7_real_test_evidence_validation.py -q`
- Success criteria:
  - `CycleRun.model_matrix` references actual EfficientNet candidate runs and
    F-drive artifacts;
  - every candidate has MLflow run id, model artifact, metric matrix,
    confusion matrix, GPU profile, environment report, and blocker reason when
    not promotable;
  - policy validation fails if `EVM-237` has not produced real evidence.
- Failure blocker:
  - blocked until `EVM-237` emits actual model matrix evidence; blocked if
    `CycleRun.model_matrix` is static, mock-only, or missing candidate
    artifacts.
