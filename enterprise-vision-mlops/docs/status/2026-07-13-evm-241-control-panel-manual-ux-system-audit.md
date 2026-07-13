# EVM-241 Control Panel Manual UX and System Audit

Date: 2026-07-13

## Verdict

The Control Panel is operationally valid for a single-node enterprise MLOps lab workflow. A real VisA data cycle, CUDA EfficientNet-B0 training, MLflow evaluation, readiness admission, CI/CT, independent approval, Kubernetes staging deployment, serving validation, and Prometheus monitoring completed through one versioned profile and one persistent lifecycle run.

This result does not claim a new production promotion. The verified candidate targeted `evm-staging`; the existing CUDA Product deployment was restored to `evm-production/evm-b0-production` after the single-GPU validation handoff. Multi-cluster high availability and organization-scale tenant isolation remain later-stage requirements.

## Execution Identity

| Field | Evidence |
| --- | --- |
| Lifecycle run | `lifecycle-20260712T164133-1e8bf477` |
| CycleRun | `cycle-w7-visa-open-data-e35d93d5561f-efficientnet-b0-visa-anomaly-vca742ba3784d4861a58b8a92f30eb2ab` |
| Profile | `standard-b0-operator-validation / v1` |
| Dataset | `visa-open-data-e35d93d5561f` |
| MLflow run | `ca742ba3784d4861a58b8a92f30eb2ab` |
| Airflow DAG run | `cp__20260712T164135-ab06c1f7` |
| Kubernetes training Job | `evm-lifecycle-train-406a000e7d20` |
| Governance decision | `decision-20260712-bdc0686103`, approved, v3 |
| Source commit captured by run | `01c64c6cf17b836ede28b490500c06208a09dee6` |

## Manual Control Panel Checklist

| View | Manual action and acceptance | Result |
| --- | --- | --- |
| Overview | Switch live and historical CycleRuns; verify ring nodes remain inside the ring; confirm selected-cycle diagnostics | Pass. Ring geometry, worst-stage status, and cycle-scoped diagnostics are coherent. |
| Configure | Edit owner, B0, epochs, early-stop threshold, patience, execution scope, environment, and namespace; validate, inspect JSON, save, preview, and queue | Pass. Versioned immutable profile and full lifecycle launch were created from the UI. |
| Runs | Select completed, failed, blocked, cancelled, and dry-run records; approve a waiting run; inspect stage progress and worker state | Pass. Terminal stages render `100%` plus duration, not attempt fractions. Running stages use live stripes and scan animation. Worker is `Online`. |
| Readiness | Open Evidence Checks; inspect 13 checks; copy an evidence path; evaluate production policy with an independent approver | Pass. Hidden evidence becomes visible through the disclosure and policy changes affect decision state. |
| Timeline | Select stages; toggle Live and All topology; inspect artifact and resource drilldowns | Pass. Timeline, intermediate evidence, Kubernetes topology, and selected-resource details stay synchronized. |
| Operate | Edit task parameters; validate a dry-run task; edit command actor, reason, and replicas; preview guarded command | Pass. Dry-run records are auditable without mutating the runtime. |
| Gates | Inspect model metrics, drift comparison, CD/CT checks, approval state, and diagnostics | Pass. Drift remains review-oriented and no automatic retraining is triggered. Current approval may validly be `pending`. |
| Release | Inspect seven release stages, target-specific labels, deployment ledger, Grafana, MLflow, and Prometheus links | Pass. Staging is shown as target verification, and `deployment_target_not_production` remains explicit. |
| Governance | Create a cycle-linked draft; transition draft to review and approved with a separate actor | Pass. Decision evidence, cycle, run, dataset, model, and source commit are persisted on F drive. |

## Cross-Cutting UX Checklist

| Check | Result |
| --- | --- |
| Dark and light theme toggle | Pass; final default remains black-base dark theme. |
| Context persistence after reload | Pass for selected tab, CycleRun, and lifecycle run. |
| Five-second live refresh | Pass; mutations schedule a follow-up refresh instead of being dropped behind an active poll. |
| Blocked and warning cause visibility | Pass through the cycle-scoped Runtime Diagnostics disclosure. |
| Desktop 1440 x 1000 | Pass across all nine tabs. |
| Mobile Pixel 5 | Pass across all nine tabs. |
| Horizontal document overflow | Pass; automated threshold is at most one pixel for every tab and viewport. |
| Browser warnings and errors | Pass; zero warning/error entries after the rebuilt deployment loaded. |
| Loading state after container restart | Pass; disabled selector and explicit `Loading CycleRun` resolve to synchronized state. |

## Real Lifecycle Proof

| Stage | Real evidence | Result |
| --- | --- | --- |
| Data pipeline | Airflow success; VisA open dataset; 10,821 records and 23 shards; Parquet and lineage artifacts on F drive | Pass, 9m 39s |
| GPU training | Kubernetes Job on CUDA; EfficientNet-B0; 4 of 6 epochs; early stop threshold 0.93 reached | Pass, 5m 15s |
| Model evaluation | Accuracy 0.963778; precision 0.843137; recall 0.785388; F1 0.813239; AUROC 0.982582 | Pass |
| GPU profile | Peak 2,846.96 MiB; 408 optimizer steps; training duration 141.73 seconds | Pass |
| MLflow | Run `ca742ba3784d4861a58b8a92f30eb2ab`; model, metrics, environment, model card, and confusion matrix linked | Pass |
| Readiness | Model matrix, split manifest, digests, lineage, quality report, runtime, and rollback inputs | Pass, no blockers |
| CI/CT | Immutable CI evidence admitted before deployment intent | Pass |
| Approval | Independent actor `ai-infra-sre` approved after automated gates | Pass |
| Deployment | Staging deployment applied, readiness checked, then scaled down after validation | Pass |
| Product restoration | `evm-production/evm-b0-production` is 1/1 Ready on CUDA at port 30800 | Pass |
| Monitoring | Product Prometheus target `host.docker.internal:30800/metrics` is `up`; ephemeral staging target removed | Pass |

## Defects Closed During Audit

| Defect | Closure |
| --- | --- |
| Completed stages showed `1/2`, `1/3`, or `1/20` as progress | Terminal rows now show 100% and duration; attempt metadata remains accessible. |
| Running stages lacked clear motion | Striped progress and scan animation added with accessible progressbar semantics. |
| Diagnostics belonged to a different CycleRun | Diagnostics API and UI are bound to the selected cycle ID. |
| Lifecycle run and CycleRun selection diverged | Selecting a run now synchronizes the cycle context and persists it. |
| One GPU caused training or staging pods to remain Pending | Product GPU lease handoff now scales down, validates, and restores deterministically. |
| Worker heartbeat stopped after a Windows sharing violation | Atomic JSON replacement retries transient locks and the heartbeat loop recovers from `OSError`. |
| Governance and drift ledgers mapped to read-only storage | Writable F-drive roots and explicit 503 persistence failures were added. |
| Stale E2E assertions assumed seed approvals and unwired orchestration | Tests now read live CycleRun semantics and validate actual operator policy transitions. |
| FastAPI startup hook emitted deprecation warnings | Model initialization moved to the lifespan contract. |
| Training used deprecated `torch.cuda.amp` | Source moved to `torch.amp`; a regression assertion prevents reintroduction. |

## Automated Verification

| Command | Result |
| --- | --- |
| `python -m pytest -q` | 219 passed |
| `npm --prefix apps/control-panel run test -- --run` | 33 passed |
| `npm --prefix apps/control-panel run build` | Pass |
| `npm --prefix apps/control-panel run test:e2e` | 20 passed across desktop and mobile |
| API and Control Panel container rebuild | Healthy |
| Product `/ready` | Model loaded, CUDA available, EfficientNet-B0 |
| Worker heartbeat double-read | Advanced from one heartbeat interval to the next |

## Evidence Locations

- Runtime evidence root: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/lifecycle_runs/lifecycle-20260712T164133-1e8bf477`
- Desktop and mobile screenshots: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/all_tabs_visual/latest`
- Playwright traces and failure history: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel_ui/playwright`
- Governance record: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/governance/decisions/decision-20260712-bdc0686103/decision.json`
- Machine-readable audit index: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel_ui/evm-241-final-audit.json`

## Residual Boundaries

1. This run proves staging deployment and Product restoration, not promotion of the newly trained candidate into production.
2. GPU handoff is intentionally serialized because the current lab has one schedulable GPU. Production scale needs a GPU pool and scheduler policy rather than deployment scale-down.
3. The immutable lifecycle artifact created before the approval-state fix retains `runtime_state=two_person_approval_required`; its completed state and approval detail are preserved, while API/UI normalization and all new records use `approved`.
4. The completed model artifact was produced by the previously pinned training image. The `torch.amp` source update must be included in the next rebuilt training image and proven by the next real training cycle.
5. Organization-wide tenancy, HA control-plane components, disaster recovery, SSO/RBAC integration, and multi-cluster policy enforcement are outside this single-node validation.
