# EVM-260 Stage Workbench, Candidate Promotion, And Observability

Date: 2026-07-14  
Jira: `SCRUM-166` under `SCRUM-156`  
Implementation commit: `17486a57ead3d9d62de065fbebca8e185a0fd769`

## Outcome

The Control Panel can now run a full lifecycle automatically or stop after each
completed dependency boundary. The new `Stages` workbench lists immutable stage
inputs, outputs, blockers, and allowed actions. It also lists historical model
candidates and permits promotion only after readiness, isolated CT, artifact
URI, and artifact digest agree on the same candidate.

Prometheus remains the metric collection and PromQL layer rather than the main
operator UI. `/targets` answers whether a scrape target is reachable. The
Control Panel and Grafana now consume dedicated lifecycle metrics for operator
use.

## Implemented Boundaries

- `LifecycleRun.execution_mode`: `automatic` or `stepwise`.
- Stepwise runs persist `paused` and require an optimistic-lock `Continue` call.
- `GET /control-panel/v1/stage-handoffs` returns ready, active, blocked,
  completed, consumed, cancelled, and waiting work with lineage refs.
- Cancelled stages are archived separately and no longer inflate blocked counts.
- `GET /control-panel/v1/model-candidates` indexes live and historical CycleRun
  matrices; blocked candidates retain explicit reason codes.
- Candidate selection writes an immutable F-drive audit record and the resulting
  `model_selection_id` is validated again when a deployment intent is created.
- Historical cycle selection is honored server-side rather than silently being
  replaced with the latest cycle.
- The `Stages` UI defaults to actionable work and promotion-ready candidates,
  progressively disclosing archived, blocked, and full catalogs.
- Candidate catalog reads are cached for 30 seconds for polling and Prometheus;
  selection mutations bypass the cache and revalidate source evidence.

## Prometheus And Grafana

Live scrape targets on completion:

- `evm-api`: up
- `evm-b0-production`: up
- `prometheus`: up

Added metrics:

- `evm_control_panel_lifecycle_run_count{state,execution_mode}`
- `evm_control_panel_stage_count{stage,state,runtime}`
- `evm_control_panel_stage_progress_ratio{run_id,stage}`
- `evm_control_panel_stage_handoff_count{bucket}`
- `evm_control_panel_model_candidate_count{architecture,status,selectable}`
- `evm_control_panel_lifecycle_worker_online`
- `evm_control_panel_metric_refresh_success`

Observed values included worker online `1`, metric refresh success `1`, two
promotion-ready EfficientNet-B0 candidates, and six blocked B7 candidates. Warm
candidate API latency was reduced from about 6.9 seconds to 0.006 seconds;
Prometheus API scrape duration was observed below 0.5 seconds after warm-up.

Grafana provisions `Enterprise Vision MLOps - Control Plane Operations` with
eight panels under UID `evm-control-plane`. The Control Panel opens that
dashboard directly. Its Prometheus link opens a pre-populated stage-handoff
PromQL query rather than the target-only page.

Node CPU, node filesystem, and Kubernetes object metrics are not claimed here:
node-exporter and kube-state-metrics are not installed. Kubernetes topology
continues to come from the Control Panel observer contract.

## Real Stepwise Data Proof

Profile:

- `w8-stepwise-handoff-real-20260714/v1`
- replay status: ready and executable
- source manifest SHA: `75b8d81be7ea972b68b0acf5a69540a8c73716a40100ff74f65a55cc0d21f3fd`
- split manifest SHA: `10977332e8c32992c82eedfa2051bc69fd46d1b1d970fea6a05b405c4b8b0184`

Run:

- `lifecycle-20260714T035305-1fae6f0f`
- source commit: `17486a57ead3d9d62de065fbebca8e185a0fd769`
- Airflow run: `cp__20260714T035308-0f4fffba`
- Airflow terminal tasks: 8 success, 10 branch-policy skipped
- data provenance: pass, five observations, zero blockers
- terminal lifecycle state for this proof: `paused`
- current stage: `model_training`
- model training state: `not_started`
- eligible actions: `continue`, `inspect`

The data evidence is:

`F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/lifecycle_runs/lifecycle-20260714T035305-1fae6f0f/data/provenance-validation.json`

The Continue action was intentionally not executed. This proves the operator
boundary without starting another GPU training run.

## Verification

- Python: `284 passed`.
- Frontend unit contracts: `44 passed`.
- Full Playwright suite: `22 passed` across desktop and MobileChrome.
- Final all-tab visual pass after real handoff: `2 passed`.
- API and Control Panel containers: healthy.
- Lifecycle worker: online.
- Desktop and mobile Stage Workbench screenshots:
  - `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/all_tabs_visual/latest/chromium-stages.png`
  - `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/all_tabs_visual/latest/MobileChrome-stages.png`

Operator entry points:

- Control Panel: `http://127.0.0.1:4173/?view=stages`
- Grafana: `http://127.0.0.1:3000/d/evm-control-plane/enterprise-vision-mlops-control-plane-operations`
- Prometheus: `http://127.0.0.1:9090/graph?g0.expr=sum%20by%20(bucket)%20(evm_control_panel_stage_handoff_count)&g0.tab=1`
