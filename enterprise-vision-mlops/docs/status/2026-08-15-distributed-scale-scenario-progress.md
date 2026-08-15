# Distributed Scale Scenario Progress

- Schema: `evm.scale_validation.progress.v2`
- Generated: `2026-08-15T09:42:47Z`
- Authoritative plan: `docs/agenda/2026-08-15-distributed-scale-operational-validation-plan-v3.md`
- Claim boundary: This ledger reports local development evidence only. Planned or implementing work is not benchmark, availability, scale, or production proof.

Only a scenario with passed acceptance criteria and hashed evidence may be `verified`.

## S0: Runtime Baseline & Evidence Contract

- Status: `implementing`
- Engineering question: Can every load result start from a healthy, reproducible runtime whose identity, metrics, and trace propagation are machine-verifiable?
- Why now: No scale result is credible without a stable and attributable control case.
- Observed gap: Degraded readiness could return HTTP 200, and no strict benchmark closure contract requires identity, cross-layer traces, repeated metrics, and hashed evidence.
- Existing-system baseline: The existing control plane uses a FastAPI API, file-ledger task assignments, a supervised lifecycle worker, Compose Airflow and MLflow services, Kubernetes model serving, and Prometheus. Health and metrics exist, but one exported cross-runtime trace and machine-enforced evidence closure do not yet cover that real path.
- Architecture before: Health, metrics, and logs exist without one machine-enforced evidence closure.
- Architecture after: Readiness, bounded telemetry, distributed trace identity, and benchmark closure gate every later scenario.
- Verdict: `not_run`
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario. A scenario pass does not replace final cross-scenario system validation.
- Next action: Commit and revision-align the corrected runner, then execute three fresh fixed-window controls before S1 begins.

### Affected Existing Components

- Existing API and serving request boundaries: `apps/api/main.py`, `apps/api/efficientnet_serving.py`, `src/evm/model_runtime/serving.py`
- Existing queue and lifecycle execution boundaries: `src/evm/control_panel/operations.py`, `src/evm/control_panel/lifecycle_runs.py`, `src/evm/control_panel/lifecycle_orchestrator.py`, `src/evm/control_panel/lifecycle_worker.py`
- Existing pipeline and tracking clients: `src/evm/core/config.py`, `src/evm/core/pipeline.py`, `src/evm/core/http.py`, `src/evm/core/mlflow_client.py`, `orchestration/airflow/dags/enterprise_vision_mlops_daily.py`, `src/evm/pipelines/spark_runtime_probe/run.py`
- Existing local runtime and monitoring stack: `docker-compose.yml`, `monitoring/prometheus/prometheus.yml`, `src/evm/control_panel/kubernetes_observer.py`, `scripts/dev/start_kubernetes_observer.ps1`
- Scale-validation public contracts: `src/evm/scale_validation/contracts.py`, `contracts/distributed-scale/scenario-progress.schema.json`, `contracts/distributed-scale/benchmark-evidence.schema.json`

### Architecture Delta

- Before: Health, metrics, and logs exist without one machine-enforced evidence closure.
- After: Readiness, bounded telemetry, distributed trace identity, and benchmark closure gate every later scenario.
- Selection reason: Instrument the existing runtime boundaries so later load results are attributable to the system under test.
- Selection reason: Keep high-cardinality run identity in traces and logs while Prometheus labels remain bounded.
- Alternative/trade-off: Full automatic instrumentation would reduce manual span code but obscures domain handoff boundaries and adds a larger dependency surface.
- Alternative/trade-off: A hosted trace backend would improve exploration but adds external state; a local Collector and immutable file evidence fit the current single-node scope.

### Proposed Design

- Return HTTP 503 whenever serving dependencies or the promoted model are not ready.
- Propagate W3C trace context through API, queue, worker, data, tracking, and serving.
- Keep metric labels bounded while exact identities remain in traces and structured logs.
- Capture three fixed-window low-load controls with latency, throughput, resource, load-generator permit-wait, and retry data.

### Implementation Delta

- Serving readiness contract: `apps/api/main.py`, `tests/test_api_metrics.py`
- In-place scale-validation evidence contracts: `src/evm/scale_validation/contracts.py`, `src/evm/scale_validation/evidence.py`, `src/evm/scale_validation/catalog.py`, `scripts/dev/initialize_scale_scenario_progress.py`, `scripts/dev/validate_scale_scenario_progress.py`, `tests/test_scale_scenario_progress.py`, `tests/test_scale_validation_evidence.py`, `.gitattributes`
- W3C trace propagation across existing runtime boundaries: `src/evm/observability/trace_context.py`, `src/evm/observability/otel.py`, `apps/api/main.py`, `apps/api/efficientnet_serving.py`, `src/evm/control_panel/lifecycle_runs.py`, `src/evm/control_panel/lifecycle_orchestrator.py`, `src/evm/control_panel/lifecycle_worker.py`, `src/evm/control_panel/operations.py`, `src/evm/core/pipeline.py`, `src/evm/core/http.py`, `src/evm/core/mlflow_client.py`
- Existing local telemetry runtime: `docker-compose.yml`, `monitoring/opentelemetry/collector.yaml`, `monitoring/prometheus/prometheus.yml`, `scripts/dev/start_lifecycle_worker.ps1`, `scripts/dev/start_kubernetes_observer.ps1`, `scripts/dev/start_local_stack.ps1`, `src/evm/control_panel/kubernetes_observer.py`
- Bounded serving telemetry and exact endpoint verification: `src/evm/model_runtime/serving.py`, `src/evm/model_runtime/workload_runner.py`, `src/evm/model_runtime/scenario_workload_production.py`, `src/evm/control_panel/lifecycle_orchestrator.py`, `tests/test_scenario_model_serving.py`, `tests/test_scenario_workload_production.py`
- Existing Airflow data path Spark boundary: `infra/docker/airflow/Dockerfile`, `orchestration/airflow/dags/enterprise_vision_mlops_daily.py`, `scripts/run_pipeline.py`, `scripts/run_profile_pipeline.py`, `src/evm/pipelines/spark_runtime_probe/run.py`
- Cross-runtime data-root resolution: `src/evm/core/config.py`, `tests/test_data_pipeline_empty_guards.py`
- Existing-runtime S0 low-load control runner: `src/evm/scale_validation/s0_runtime.py`, `scripts/dev/run_s0_low_load_control.py`, `src/evm/model_runtime/scenario_workload_production.py`, `tests/test_s0_runtime.py`
- Runtime revision and serving OTLP closure: `apps/api/Dockerfile`, `apps/api/main.py`, `docker-compose.yml`, `scripts/dev/start_local_stack.ps1`, `scripts/dev/start_scenario_workload_worker.ps1`, `src/evm/model_runtime/scenario_workload_production.py`, `src/evm/observability/otel.py`
- Host CUDA telemetry dependency contract: `infra/runtime/scenario-transformers/requirements.txt`, `scripts/dev/start_scenario_workload_worker.ps1`, `tests/test_scenario_runtime_dependencies.py`
- Compatibility: Tracing is environment-gated and additive; existing API payloads and legacy trace identifiers remain readable.
- Compatibility: No trace or run identifier is introduced as a Prometheus label.
- Compatibility: An intentionally scaled-to-zero B0 deployment is absent from active scrape discovery rather than reported as a false outage.
- Migration: Rebuild the existing API, Airflow, and serving images and restart the supervised worker after the source revision is committed.
- Migration: Historical artifacts remain historical evidence and cannot satisfy fresh S0 acceptance.

### Experiment Contract

- Workload/input: Three independent controls, each pacing three real CUDA requests across a declared 60-second fixed-rate window through the existing lifecycle and serving path.
- Precondition: Existing control services, one promoted serving target, monitoring, worker, and observer are healthy and revision-aligned.
- Precondition: Source, data, model, runtime, and load-profile identities are immutable and public-safe evidence roots are writable.
- Controlled variable: Source, data, model, runtime, seed-applied test-sample sequence, concurrency, warmup, and fixed measurement window.
- Controlled variable: No concurrent training, deployment, or unrelated background load during each control repetition.
- Signal: Readiness status, W3C trace stages, latency quantiles, fixed-window throughput, queue age/depth, worker activity, load-generator permit wait, retry count, CPU/RAM/GPU/VRAM.
- Signal: Focused regression tests and the existing end-to-end lifecycle regression result.
- Stop condition: Any active dependency is unhealthy, identity is ambiguous, trace linkage is missing, or metric labels become unbounded.
- Stop condition: Unexpected production-like mutation, accelerator OOM, or dirty cleanup state is observed.
- Recovery condition: All existing services return to the pre-run identity and health state and temporary work is removed.
- Recovery condition: A failed repetition is retained with RCA and is never replaced by an unlinked rerun.

### Acceptance

- `S0-AC-01` [pending]: Only healthy active targets are included in the baseline.
- `S0-AC-02` [pending]: Exact source, data, model, and runtime identity is complete.
- `S0-AC-03` [pending]: p50, p95, p99, fixed-window throughput, queue, load-generator permit wait, retry, CPU, RAM, GPU, and VRAM are queryable.
- `S0-AC-04` [pending]: Trace propagation spans every declared lifecycle stage with zero missing links.
- `S0-AC-05` [pending]: Three independent controls are comparable and variance is reported.

### Current Evidence

- `docs/status/evidence/s0-otel-implementation-checkpoint.json` (`fbef5bc797a91097e136a2510bb4740fd85c9efc5255e9cd5501e68c1fd046d7`): Collector configuration, one OTLP probe, and 583-test regression passed; runtime-wide S0 acceptance remains pending.
- `docs/status/evidence/s0-in-place-telemetry-boundary-checkpoint.json` (`65e86d95d63869f6c47e80f35c14b713d66c7ee56bbe81ce95d1bcabe44ef55e`): Bounded telemetry, desired-state target discovery, local Spark stage, and 588-test regression passed at contract level; runtime S0 acceptance remains pending.
- `docs/status/evidence/s0-spark-runtime-path-remediation-checkpoint.json` (`a30afb6ac712b82e6d2e6e768e36f2fce4ce1af738a4b3b90e273fd65e51a9e1`): A real local Spark computation exposed JVM and cross-runtime path gaps; both were remediated with 590-test regression, but a fresh accepted Spark evidence run remains pending.
- `docs/status/evidence/s0-spark-runtime-component-checkpoint.json` (`11a31ffe92c5b1b5bd6c4fa1e6ae2e4230ad6497b91b0bbc79c9da766270b62f`): One real bounded local Spark component run persisted through the existing data mount and exported linked OTLP spans; full S0 lifecycle acceptance remains pending.
- `docs/status/evidence/s0-serving-runtime-identity-contract-checkpoint.json` (`d47a2b0d7a8d5c47cc3ee7948b85aada58cd40f54f6dea65a131054b9af7dd2a`): Model-source and serving-runtime revisions are now separate in the existing serving contract; 593 tests passed, while live serving revision alignment and S0 runtime acceptance remain pending.
- `docs/status/evidence/s0-low-load-control-runner-checkpoint.json` (`f0bd6847bfec46694b4917c463a0774b121b4ba704d5288a1031f35ef4f9b89e`): An in-place low-load control runner and runtime-wide revision labels passed 600 tests; live controls and S0 acceptance remain pending.
- `docs/status/evidence/s0-first-control-rca-checkpoint.json` (`c3f80a305f377e301a591f9758f05032b4a20056acb6da68ca574543d48d7b3e`): One fresh control traversed Airflow, Spark, MLflow, and real CUDA inference but failed closed because the serving OTLP stage was absent; the RCA remediation passed 603 tests and acceptance remains pending.
- `docs/status/evidence/s0-serving-runtime-dependency-rca-checkpoint.json` (`1f9046690fb1fff2e0fd05b886c2684cfddb578ca1e0564920421b4725106729`): An exact serving replacement failed closed on a missing OTLP exporter, the B0 fallback recovered at 1/1, and compatible host-runtime pins plus 604-test regression passed; accepted controls remain at zero.
- `docs/status/evidence/s0-evidence-contract-rca-checkpoint.json` (`be8832236ccae55e6b889046fa633a4b3b2aa321863db167b16ecc706a2f7475`): An independent audit invalidated the first S0 closure because public hashes used CRLF worktree bytes, fixed-window pacing and seed application were absent, and permit wait was mislabeled as pool wait.
- `docs/status/evidence/s0-low-load-control-1.json` (`552af2a85fdb2d3bfcdf0407abb19b1aa3ff6c12e97f47db63bb9444f8d30a24`): Historical S0 control 1 is retained but is not accepted as fixed-window load evidence.
- `docs/status/evidence/s0-low-load-control-2.json` (`a3ae4a4939042355ae80567cc0de2fcd5453a91d9f23d35a6e009856c9a4525b`): Historical S0 control 2 is retained but is not accepted as fixed-window load evidence.
- `docs/status/evidence/s0-low-load-control-3.json` (`3b713445967002e60d8a6f5a2c2d3fe635c6530e0a9c9a309cf8b3bce24535f0`): Historical S0 control 3 is retained but is not accepted as fixed-window load evidence.
- `docs/status/evidence/s0-cross-runtime-trace-summary.json` (`0fafe770b87713d33fa370f75b03c26d7ff762d6ff31a6fff37f8e2160cb1e6c`): Historical trace linkage is retained but does not close the fixed-window benchmark contract.
- `docs/status/evidence/s0-low-load-benchmark-evidence.json` (`41aee97a7a2ba1d88e4618ece7eca229c0c94329e70b21ef3774c4a3b3123b9b`): The superseded burst suite is explicitly failed pending three fresh fixed-window controls.

### Chronological Updates

- `2026-08-14T19:34:00Z` `design` / `planned`: The authoritative in-place scenario contract was reviewed against the existing ML Serve API system.
- `2026-08-14T20:10:00Z` `implementation` / `implementing`: Readiness semantics and strict evidence-contract scaffolding were applied to the existing API and repository.
- `2026-08-14T20:34:00Z` `implementation` / `implementing`: W3C trace identity was propagated through the existing API, lifecycle, queue, and Airflow configuration boundaries; focused regression passed, but runtime trace acceptance remains unexecuted.
- `2026-08-14T21:40:31Z` `implementation` / `implementing`: Bounded telemetry, active-target reconciliation, and the local Spark boundary were implemented in the existing runtime path; 588 tests passed, but no live cross-runtime trace or control run is claimed.
- `2026-08-14T22:06:35Z` `implementation` / `implementing`: A real local Spark attempt exposed a missing JVM and then an invalid cross-runtime evidence path. Java 17 and shared data-root resolution were added; 590 tests and the path contract passed, while a fresh accepted Spark run remains pending.
- `2026-08-14T22:14:32Z` `experiment` / `implementing`: One real bounded Spark component run completed in the existing Airflow runtime, persisted its report through the shared mount, and exported linked parent-child spans. Full lifecycle trace and three-control acceptance remain unexecuted.
- `2026-08-14T22:32:37Z` `implementation` / `implementing`: The existing scenario serving contract now separates immutable model source from executing runtime source. Focused tests, static analysis, and 593-test regression passed; the active service has not yet been restarted or accepted as S0 runtime evidence.
- `2026-08-14T22:59:31Z` `implementation` / `implementing`: The existing runtime now has a fail-closed low-load control runner and revision-aware OTLP resources. Focused checks and 600-test regression passed; no live control or S0 acceptance is claimed.
- `2026-08-14T23:25:46Z` `experiment` / `implementing`: The first fresh control completed the bounded functional path but failed closed with the serving trace stage absent. RCA found an unconfigured serving child and stale API image identity; the in-place remediation passed 603 tests, while accepted controls remain at zero.
- `2026-08-14T23:34:43Z` `implementation` / `implementing`: The exact serving replacement failed closed because the dedicated CUDA runtime lacked the OTLP HTTP exporter. The B0 fallback returned 1/1, compatible telemetry dependencies were pinned and preflighted, pip check passed, and 604 tests passed.
- `2026-08-15T00:10:13Z` `experiment` / `implementing`: Three functional controls traversed the existing runtime, but an independent audit later invalidated benchmark acceptance: hashes described CRLF worktree bytes, requests were burst rather than fixed-window paced, seed was metadata-only, and permit wait was misnamed as connection-pool wait.

## S1: Transactional Job State & Idempotency

- Status: `planned`
- Engineering question: Can 100 to 500 concurrent mutations commit one legal and idempotent outcome?
- Why now: Durable state correctness must precede retries and queued high-volume mutation.
- Observed gap: Transactional ownership, atomic claims, fencing, and bounded connection-pool wait are not yet proven under concurrency.
- Existing-system baseline: The existing control plane serializes file-ledger writes and uses run claims and side-effect ledgers, but concurrent durable state transitions are not owned by one transactional database contract.
- Architecture before: Control-plane state still relies partly on process-local or file-ledger ownership.
- Architecture after: Transactions, idempotency, leases, and fencing own every concurrent transition.
- Verdict: `not_run`
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario. A scenario pass does not replace final cross-scenario system validation.
- Next action: Begin after S0 evidence contracts pass their focused tests.

### Affected Existing Components

- Lifecycle and task state: `src/evm/control_panel/lifecycle_runs.py`, `src/evm/control_panel/operations.py`
- Worker ownership: `src/evm/control_panel/lifecycle_worker.py`, `src/evm/operations/scenario_d_supervision.py`
- Control API: `apps/api/main.py`, `apps/api/control_panel_lifecycle.py`, `apps/api/control_panel_tasks.py`, `apps/api/control_panel_deployments.py`

### Architecture Delta

- Before: Control-plane state still relies partly on process-local or file-ledger ownership.
- After: Transactions, idempotency, leases, and fencing own every concurrent transition.
- Selection reason: Durable atomic ownership must precede retry and overload experiments.
- Alternative/trade-off: Extending file locks is simpler but cannot safely coordinate multiple replicas; SQLite improves transactions but not the intended multi-process pool behavior.

### Proposed Design

- Use database transactions and unique idempotency keys for every state mutation.
- Enforce legal transitions, atomic lease claims, fencing, and stale-owner reconciliation.
- Bound connection-pool size and wait time and expose conflict outcomes as telemetry.

### Implementation Delta

- No existing-system code change has started.
- Compatibility: Existing read contracts and identifiers must remain stable while writes migrate behind a repository boundary.
- Migration: Schema migration and dual-read verification must complete before file-ledger writes are retired.

### Experiment Contract

- Workload/input: Concurrent create, approve, cancel, retry, and stale-owner mutation requests against existing lifecycle jobs.
- Precondition: S0 identity, trace, health, and evidence contracts pass.
- Controlled variable: Concurrency level, idempotency-key reuse, pool size, lock wait, owner loss point, and retry count.
- Signal: Committed state, transition conflicts, duplicate effects, lease/fencing identity, pool wait, timeout, and trace correlation.
- Stop condition: An illegal transition, duplicate external effect, unbounded pool wait, or ambiguous owner is observed.
- Recovery condition: One legal committed outcome remains and stale ownership is reconciled without duplicate effects.

### Acceptance

- `S1-AC-01` [pending]: Duplicate lifecycle, deployment, and artifact effects are zero.
- `S1-AC-02` [pending]: Conflicting mutations end in one legal terminal state.
- `S1-AC-03` [pending]: Pool exhaustion returns a bounded observable failure rather than hanging.
- `S1-AC-04` [pending]: Worker loss and retry preserve one committed outcome.

### Current Evidence

- No accepted execution evidence yet.

### Chronological Updates

- `2026-08-14T19:34:00Z` `design` / `planned`: The authoritative in-place scenario contract was reviewed against the existing ML Serve API system.

## S2: Bounded Queue & Backpressure

- Status: `planned`
- Engineering question: Does overload stay memory-bounded while accepted work reaches an explicit outcome?
- Why now: A bounded queue is required before capacity and recovery experiments scale up.
- Observed gap: Durable admission, byte/age bounds, retry budget, and DLQ are incomplete.
- Existing-system baseline: The existing worker polls lifecycle files and dispatches task assignments, but admission, bytes, age, retries, and poison-work isolation do not share one bounded durable queue contract.
- Architecture before: Admission and retries do not share one end-to-end resource boundary.
- Architecture after: Durable queue, local semaphore, rejection, retry, DLQ, and CPU scale are bounded.
- Verdict: `not_run`
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario. A scenario pass does not replace final cross-scenario system validation.
- Next action: Begin after S1 has protected mutation idempotency.

### Affected Existing Components

- Task admission and dispatch: `src/evm/control_panel/operations.py`, `src/evm/control_panel/lifecycle_worker.py`
- Control API: `src/evm/control_panel/router.py`, `apps/api/main.py`
- Operational telemetry: `monitoring/prometheus/prometheus.yml`, `src/evm/operations/metrics.py`

### Architecture Delta

- Before: Admission and retries do not share one end-to-end resource boundary.
- After: Durable queue, local semaphore, rejection, retry, DLQ, and CPU scale are bounded.
- Selection reason: Overload must fail explicitly before high-volume capacity tests begin.
- Alternative/trade-off: An unbounded broker is operationally easy to start but moves memory risk downstream; process-local queues alone lose durable ownership on restart.

### Proposed Design

- Combine a durable queue with a bounded process-local async queue and semaphore.
- Bound depth, bytes, age, and time; reject excess work with Retry-After.
- Retry only transient failures with backoff, jitter, and a global budget; isolate poison work.
- Scale CPU workers from queue telemetry while keeping the GPU worker count at one.

### Implementation Delta

- No existing-system code change has started.
- Compatibility: Existing task payload and lifecycle state-machine contracts must remain valid.
- Migration: Introduce bounded admission before redirecting existing producers and retain a rollback path to the prior dispatcher.

### Experiment Contract

- Workload/input: Independent burst, sustained, duplicate, expired, and poison task streams through the existing assignment endpoint.
- Precondition: S1 commits one idempotent task outcome and S0 telemetry is queryable.
- Controlled variable: Arrival rate, queue depth/bytes/age, worker count, timeout, retry budget, and poison ratio.
- Signal: Admission status, Retry-After, queue depth/bytes/age, RSS, wait time, retry amplification, DLQ, and terminal closure.
- Stop condition: RSS or in-flight bytes exceed the bound, accepted work loses terminal closure, or duplicates appear.
- Recovery condition: Queue drains to zero, poison work is quarantined, workers are healthy, and no duplicate side effect remains.

### Acceptance

- `S2-AC-01` [pending]: Queue depth, in-flight bytes, and process memory remain bounded under overload.
- `S2-AC-02` [pending]: Accepted work completes or reaches one explicit terminal failure.
- `S2-AC-03` [pending]: Duplicate effects are zero and poison work does not block healthy work.
- `S2-AC-04` [pending]: Over-capacity demand is rejected with an observable retry contract.

### Current Evidence

- No accepted execution evidence yet.

### Chronological Updates

- `2026-08-14T19:34:00Z` `design` / `planned`: The authoritative in-place scenario contract was reviewed against the existing ML Serve API system.

## S3: HIGGS Lightweight Capacity Envelope

- Status: `planned`
- Engineering question: Where are sustainable capacity, tail-latency limits, and the first CPU bottleneck?
- Why now: Lightweight probes separate infrastructure overhead from model compute.
- Observed gap: No repeated CPU-model capacity envelope or saturation knee exists.
- Existing-system baseline: The existing API can execute model inference and expose metrics, but it has no governed high-volume tabular corpus or repeated CPU/API saturation envelope.
- Architecture before: API behavior is functional but not characterized with low-compute probes.
- Architecture after: A measured CPU/API capacity envelope supplies operational and queue limits.
- Verdict: `not_run`
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario. A scenario pass does not replace final cross-scenario system validation.
- Next action: Begin after S0, S1, and provisional S2 safety gates are in place.

### Affected Existing Components

- Existing scenario intake and execution: `src/evm/control_panel/scenario_workloads.py`, `src/evm/model_runtime/workload_runner.py`
- Existing online API and metrics: `apps/api/main.py`, `src/evm/operations/metrics.py`

### Architecture Delta

- Before: API behavior is functional but not characterized with low-compute probes.
- After: A measured CPU/API capacity envelope supplies operational and queue limits.
- Selection reason: Lightweight models expose API, serialization, queue, and CPU limits without model compute dominating the result.
- Alternative/trade-off: A heavy vision or generative model is more domain-specific but hides the first systems bottleneck behind accelerator compute.

### Proposed Design

- Use one governed high-volume tabular corpus across multiple lightweight CPU probes.
- Run closed concurrency and open arrival-rate sweeps with fixed corpus, split, and seed.
- Compare API replicas and CPU worker counts while tracing validation and prediction stages.
- Measure co-located load-generator consumption separately from the system under test.

### Implementation Delta

- No existing-system code change has started.
- Compatibility: The existing workload registry and metric projection remain the control-plane entry point.
- Migration: Register governed tabular profiles without changing existing image or generative workload contracts.

### Experiment Contract

- Workload/input: A fixed public high-volume tabular split replayed across lightweight CPU estimators.
- Precondition: S0 passes and S1/S2 provide idempotent bounded execution.
- Controlled variable: Model, split, seed, arrival model, concurrency, API replicas, CPU workers, warmup, and duration.
- Signal: RPS, p50/p95/p99, errors, queue wait, validation/transform/predict spans, CPU, RAM, and load-generator cost.
- Stop condition: Error or latency guardrail is crossed, queue no longer drains, or resource saturation risks the host.
- Recovery condition: Load stops, queues drain, replicas return to baseline, and each repetition has complete evidence.

### Acceptance

- `S3-AC-01` [pending]: Every probe has p95, p99, throughput, error, and resource curves.
- `S3-AC-02` [pending]: The first bottleneck is explained by trace and resource telemetry.
- `S3-AC-03` [pending]: The sustainable operating point is explicitly lower than peak throughput when required.
- `S3-AC-04` [pending]: Three independent repetitions and their variance are retained.

### Current Evidence

- No accepted execution evidence yet.

### Chronological Updates

- `2026-08-14T19:34:00Z` `design` / `planned`: The authoritative in-place scenario contract was reviewed against the existing ML Serve API system.

## S4: HIGGS Tiny MLP GPU Batching

- Status: `planned`
- Engineering question: Which small-model batch and queue-delay settings maximize throughput within p99 and VRAM?
- Why now: A lightweight GPU probe reveals scheduler and batching cost without a large model.
- Observed gap: No throughput-latency-VRAM Pareto curve exists for the accelerator path.
- Existing-system baseline: The existing single-accelerator path supports training and serving with an exclusive lease, but dynamic batching, queue delay, and VRAM capacity have no measured operating envelope.
- Architecture before: The accelerator path runs models but lacks a controlled batching envelope.
- Architecture after: One measured batch, delay, instance, and VRAM operating point governs inference.
- Verdict: `not_run`
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario. A scenario pass does not replace final cross-scenario system validation.
- Next action: Begin after the common baseline and bounded queue instrumentation are ready.

### Affected Existing Components

- Existing serving runtime: `apps/api/efficientnet_serving.py`, `src/evm/control_panel/lifecycle_kubernetes.py`
- Existing accelerator workload control: `src/evm/control_panel/scenario_workload_control.py`, `src/evm/model_runtime/workload_runner.py`

### Architecture Delta

- Before: The accelerator path runs models but lacks a controlled batching envelope.
- After: One measured batch, delay, instance, and VRAM operating point governs inference.
- Selection reason: A tiny MLP isolates scheduler and batch formation cost within the available single-GPU boundary.
- Alternative/trade-off: Multiple concurrent training jobs were excluded because the hardware cannot provide trustworthy isolation without MIG.

### Proposed Design

- Sweep batches 1, 4, 8, 16, and 32 with bounded queue delays.
- Keep one model instance by default and vary instance count separately from batch size.
- Record allocated, reserved, and peak VRAM, utilization, and formed batch size.
- Run training under a separate exclusive lease, never during inference benchmarking.

### Implementation Delta

- No existing-system code change has started.
- Compatibility: Existing exclusive training lease and serving identity gates remain authoritative.
- Migration: Add batching as an opt-in serving profile and keep batch-one behavior as rollback.

### Experiment Contract

- Workload/input: Fixed tabular samples served by one tiny GPU MLP under a batch and delay matrix.
- Precondition: S0 telemetry and S2 bounds pass; no training or unrelated GPU workload is active.
- Controlled variable: Batch size, bounded queue delay, model instance count, arrival rate, seed, and warmup.
- Signal: Throughput, p95/p99, formed batch size, queue delay, GPU utilization, allocated/reserved/peak VRAM, and OOM count.
- Stop condition: Any OOM, lease conflict, thermal risk, unbounded queue, or p99 stop threshold occurs.
- Recovery condition: The batch-one known-good profile is restored and accelerator memory returns to baseline.

### Acceptance

- `S4-AC-01` [pending]: Throughput-p99 and throughput-peak-VRAM Pareto curves exist.
- `S4-AC-02` [pending]: The selected operating point has zero accelerator out-of-memory failures.
- `S4-AC-03` [pending]: Model instance count and batch size effects are measured separately.
- `S4-AC-04` [pending]: Queue limits are recalculated from the selected service rate.

### Current Evidence

- No accepted execution evidence yet.

### Chronological Updates

- `2026-08-14T19:34:00Z` `design` / `planned`: The authoritative in-place scenario contract was reviewed against the existing ML Serve API system.

## S5: Criteo Spark Memory-bounded Data Scale

- Status: `planned`
- Engineering question: Can larger partitioned data remain memory-bounded, deterministic, and restartable?
- Why now: Single-process data preparation does not demonstrate executor or shuffle behavior.
- Observed gap: Partition sizing, spill, skew, retry, and idempotent distributed commits are unproven.
- Existing-system baseline: The existing Airflow data path uses deterministic Python and columnar processing, but distributed executor, shuffle, spill, skew, and retry behavior are not implemented or evidenced.
- Architecture before: Data processing is reproducible at local scale but remains mostly process-local.
- Architecture after: Spark executors process governed partitions with bounded memory and deterministic commits.
- Verdict: `not_run`
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario. A scenario pass does not replace final cross-scenario system validation.
- Next action: Begin after S1 and S2 protect ownership, retry, and output idempotency.

### Affected Existing Components

- Existing data pipeline: `src/evm/core/pipeline.py`, `orchestration/airflow/dags/enterprise_vision_mlops_daily.py`
- Existing Kubernetes job scaffold: `infra/kubernetes/local/pipeline-job.yaml`, `src/evm/control_panel/kubernetes_task_executor.py`

### Architecture Delta

- Before: Data processing is reproducible at local scale but remains mostly process-local.
- After: Spark executors process governed partitions with bounded memory and deterministic commits.
- Selection reason: Spark provides executor, partition, shuffle, spill, skew, and retry controls that the current single-process path lacks.
- Alternative/trade-off: Flink is stronger for streaming but adds a second execution model before batch-scale correctness is established.

### Proposed Design

- Compare single-process columnar processing, local Spark, and Kubernetes executors.
- Increase a governed tabular subset through staged sizes with fixed manifests.
- Tune executor memory, partitions, shuffle, and adaptive execution under explicit bounds.
- Inject skew and one executor loss, then verify idempotent output commit and digest closure.

### Implementation Delta

- No existing-system code change has started.
- Compatibility: Existing manifests, lineage digests, and Airflow handoff remain authoritative inputs and outputs.
- Migration: Run single-process and Spark paths in parallel for digest comparison before making Spark selectable in profiles.

### Experiment Contract

- Workload/input: Progressively larger governed click-log partitions with fixed source, schema, partition, and output manifests.
- Precondition: S1/S2 protect ownership and retries; governed subset manifests and F-drive capacity checks pass.
- Controlled variable: Subset size, executor count, executor memory, partition size, shuffle partitions, skew fixture, and retry point.
- Signal: Records/s, MiB/s, peak memory, GC, shuffle, spill, skew, retries, row count, duplicates, and output digest.
- Stop condition: Disk or memory guardrail is crossed, output integrity changes, or cleanup cannot be guaranteed.
- Recovery condition: Executor loss is reconciled, output digest is deterministic, and temporary shuffle/output data is cleaned.

### Acceptance

- `S5-AC-01` [pending]: Records per second, storage rate, peak executor memory, GC, shuffle, spill, and skew are reported.
- `S5-AC-02` [pending]: Output contains zero missing and zero duplicate records.
- `S5-AC-03` [pending]: Retry preserves row count and output digest.
- `S5-AC-04` [pending]: Generated load volume is not represented as new semantic diversity.

### Current Evidence

- No accepted execution evidence yet.

### Chronological Updates

- `2026-08-14T19:34:00Z` `design` / `planned`: The authoritative in-place scenario contract was reviewed against the existing ML Serve API system.

## S6: API Rolling Continuity & GPU Controlled Handoff

- Status: `planned`
- Engineering question: Can API replicas roll continuously while a single-GPU handoff remains measured and reversible?
- Why now: API continuity and GPU availability are different failure domains and claims.
- Observed gap: Rolling API drain and controlled single-GPU switch are not proven under load.
- Existing-system baseline: The existing system deploys and rolls back model targets and has target-scoped recovery guards, but stateless API rolling continuity and single-GPU model handoff have not been measured as separate claims under load.
- Architecture before: Deployment works but API continuity and single-GPU interruption are conflated.
- Architecture after: Stateless API continuity and controlled GPU handoff have separate evidence and claims.
- Verdict: `not_run`
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario. A scenario pass does not replace final cross-scenario system validation.
- Next action: Begin after S3 capacity, S4 GPU bounds, and S2 queue recalibration pass.

### Affected Existing Components

- Existing API deployment: `infra/kubernetes/local/api.yaml`, `apps/api/main.py`
- Existing release and rollback control: `src/evm/control_panel/deployment_executor.py`, `src/evm/control_panel/lifecycle_kubernetes.py`

### Architecture Delta

- Before: Deployment works but API continuity and single-GPU interruption are conflated.
- After: Stateless API continuity and controlled GPU handoff have separate evidence and claims.
- Selection reason: Separate CPU/API continuity from the unavoidable interruption boundary of one physical GPU.
- Alternative/trade-off: A Recreate rollout is simpler but cannot demonstrate request drain; claiming GPU HA would be false without another accelerator.

### Proposed Design

- Run two or three stateless API replicas with zero-unavailable rolling replacement.
- Use readiness, pre-stop drain, termination grace, and target-scoped Pod termination.
- Gate GPU candidates by shadow quality, identity, and tail latency before queue drain and switch.
- Measure endpoint interruption and exact known-good rollback identity separately.

### Implementation Delta

- No existing-system code change has started.
- Compatibility: Existing approval, identity, readiness, and known-good rollback gates remain mandatory.
- Migration: Enable replicated API RollingUpdate independently; retain exact-target GPU handoff as a maintenance-window operation.

### Experiment Contract

- Workload/input: Controlled replay near the verified operating point during one API replica replacement and one separately approved GPU handoff.
- Precondition: S2 queue bounds, S3 operating point, S4 GPU boundary, and clean rollback identity pass.
- Controlled variable: Replica count, rollout surge/unavailable, drain grace, request rate, target identity, and handoff timing.
- Signal: Accepted loss/duplicates, p99, drain time, readiness, replacement time, GPU interruption, rollback identity, and recovery.
- Stop condition: Target identity is ambiguous, rollback preflight fails, accepted requests are lost, or impact exceeds the maintenance boundary.
- Recovery condition: API replicas are healthy, exact known-good GPU identity is serving, queues are drained, and monitoring is green.

### Acceptance

- `S6-AC-01` [pending]: API rolling update loses and duplicates zero accepted requests.
- `S6-AC-02` [pending]: API drain and replacement recovery time are measured.
- `S6-AC-03` [pending]: GPU handoff interruption and rollback identity are measured.
- `S6-AC-04` [pending]: The result is not described as zero-downtime GPU high availability.

### Current Evidence

- No accepted execution evidence yet.

### Chronological Updates

- `2026-08-14T19:34:00Z` `design` / `planned`: The authoritative in-place scenario contract was reviewed against the existing ML Serve API system.

## S7: Image/VLM/LLM Auxiliary Admission

- Status: `planned`
- Engineering question: Do image and generative workloads use model-family-specific cost admission and metrics?
- Why now: Tabular capacity does not cover image decode, pixels, tokens, or long requests.
- Observed gap: Image, token, and in-flight cost bounds are not uniformly enforced or measured.
- Existing-system baseline: The existing workload ledger can run image, VLM, and LLM profiles sequentially, but admission is not uniformly derived from decode, pixels, tokens, in-flight cost, and long-request fairness.
- Architecture before: Multiple model families run without one cost-aware admission proof.
- Architecture after: Image, pixel, token, and in-flight budgets govern family-specific queues and metrics.
- Verdict: `not_run`
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario. A scenario pass does not replace final cross-scenario system validation.
- Next action: Begin after S2 bounds and S4 accelerator operating point are verified.

### Affected Existing Components

- Existing workload catalog and control: `src/evm/control_panel/scenario_workloads.py`, `src/evm/control_panel/scenario_workload_control.py`
- Existing model-family runner: `src/evm/model_runtime/workload_runner.py`, `configs/scenario_workloads/live-presets.json`

### Architecture Delta

- Before: Multiple model families run without one cost-aware admission proof.
- After: Image, pixel, token, and in-flight budgets govern family-specific queues and metrics.
- Selection reason: Retain real family-specific paths as auxiliary probes after common queue and GPU limits are known.
- Alternative/trade-off: Replacing the main scale workload with VLM/LLM would reduce achievable request volume and confound admission with model size.

### Proposed Design

- Measure image size, decode, preprocessing, batch, and pixel admission costs.
- Bound input tokens, output tokens, and total in-flight tokens for language workloads.
- Measure first-token, per-token, queue, fairness, and peak-memory behavior.
- Run model families sequentially on one GPU and use quantization only with runtime proof.

### Implementation Delta

- No existing-system code change has started.
- Compatibility: Existing family metric schemas remain distinct and unsupported metrics remain absent.
- Migration: Add explicit cost budgets to existing profiles without changing validated model artifacts.

### Experiment Contract

- Workload/input: Fixed small/large image and short/long text request mixes for existing image, VLM, and LLM workloads.
- Precondition: S2 bounded admission and S4 accelerator operating point pass; model families run sequentially.
- Controlled variable: Image bytes/pixels, decode work, input/output tokens, in-flight tokens, concurrency, and quantization identity.
- Signal: Family-specific p95/p99, quality, TTFT/TPOT where supported, queue wait, fairness, starvation, peak VRAM, and OOM.
- Stop condition: Any OOM, starvation, unsupported metric claim, or identity ambiguity occurs.
- Recovery condition: Family queue drains, GPU memory returns to baseline, and the prior known-good workload remains selectable.

### Acceptance

- `S7-AC-01` [pending]: Each model family has distinct p95, p99, and quality metric schemas.
- `S7-AC-02` [pending]: Selected admission limits have zero OOM and zero starvation.
- `S7-AC-03` [pending]: Long-request head-of-line behavior and fairness are measured.
- `S7-AC-04` [pending]: Unsupported or unverified metrics remain absent.

### Current Evidence

- No accepted execution evidence yet.

### Chronological Updates

- `2026-08-14T19:34:00Z` `design` / `planned`: The authoritative in-place scenario contract was reviewed against the existing ML Serve API system.

## S8: Dependency Soak & Resource-efficiency Closure

- Status: `planned`
- Engineering question: Do bounded retries and selected operating points remain stable during faults and soak?
- Why now: Closure requires long-run resource trends and dependency recovery after isolated passes.
- Observed gap: Retry amplification, resource slope, efficiency, and final re-hash are unproven.
- Existing-system baseline: The existing system has service-specific failure guards, bounded retries in selected paths, monitoring, and recovery evidence, but no distributed-scale dependency soak or resource-efficiency closure exists.
- Architecture before: Isolated guards exist without a distributed-scale soak and efficiency closure.
- Architecture after: Dependency faults, sustained load, cleanup, efficiency, and hashes close one ledger.
- Verdict: `not_run`
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario. A scenario pass does not replace final cross-scenario system validation.
- Next action: Begin only after S0 through S7 have accepted evidence and clean cleanup.

### Affected Existing Components

- Existing HTTP and lifecycle recovery: `src/evm/core/http.py`, `src/evm/control_panel/lifecycle_worker.py`
- Existing operational evidence: `src/evm/operations/failure_evidence.py`, `monitoring/prometheus/prometheus.yml`

### Architecture Delta

- Before: Isolated guards exist without a distributed-scale soak and efficiency closure.
- After: Dependency faults, sustained load, cleanup, efficiency, and hashes close one ledger.
- Selection reason: Run dependency faults and soak only after isolated correctness and capacity boundaries are accepted.
- Alternative/trade-off: A broad chaos framework would add operational surface before target-scoped fault contracts are proven.

### Proposed Design

- Inject service-scoped latency or timeout with bounded proxy or deterministic fixtures.
- Enforce retry budget, exponential backoff, jitter, circuit hold, and queue drain.
- Run a 30 to 60 minute soak near the selected safe operating point.
- Calculate CPU/GPU time efficiency and re-hash every accepted evidence artifact.

### Implementation Delta

- No existing-system code change has started.
- Compatibility: Existing A-E guard evidence remains baseline-only and cannot substitute for fresh scale-soak evidence.
- Migration: Introduce fault controls behind explicit profiles and retain a no-fault operating profile for rollback.

### Experiment Contract

- Workload/input: One bounded dependency fault at a time followed by a 30-60 minute controlled replay near 70 percent of measured sustainable capacity.
- Precondition: S0 through S7 have accepted evidence, cleanup proof, and one selected safe operating point.
- Controlled variable: Dependency target, fault latency/duration, timeout, retry budget, backoff, jitter, circuit hold, and load rate.
- Signal: Retry amplification, MTTR, request impact, memory/FD/pool/queue slopes, CPU/GPU time, efficiency, cleanup, and evidence hashes.
- Stop condition: Fault scope escapes the target, retry budget is exceeded, resource slope is unbounded, or cleanup becomes uncertain.
- Recovery condition: Fault is removed, dependencies and queues recover, resources return to baseline, and every accepted artifact re-hashes.

### Acceptance

- `S8-AC-01` [pending]: Retry amplification stays within the declared budget.
- `S8-AC-02` [pending]: Memory, file descriptor, pool, queue, and artifact slopes remain bounded.
- `S8-AC-03` [pending]: MTTR, request impact, efficiency Pareto, and residual risk are recorded.
- `S8-AC-04` [pending]: One final evidence index re-hashes every accepted result.

### Current Evidence

- No accepted execution evidence yet.

### Chronological Updates

- `2026-08-14T19:34:00Z` `design` / `planned`: The authoritative in-place scenario contract was reviewed against the existing ML Serve API system.
