# Distributed Scale Scenario Progress

- Schema: `evm.scale_validation.progress.v2`
- Generated: `2026-08-23T10:20:55.921057Z`
- Authoritative plan: `docs/agenda/2026-08-15-distributed-scale-operational-validation-plan-v3.md`
- Claim boundary: This ledger reports local development evidence only. Planned or implementing work is not benchmark, availability, scale, or production proof.

Only a scenario with passed acceptance criteria and hashed evidence may be `verified`.

## S0: Runtime Baseline & Evidence Contract

- Status: `verified`
- Engineering question: Can every load result start from a healthy, reproducible runtime whose identity, metrics, and trace propagation are machine-verifiable?
- Why now: No scale result is credible without a stable and attributable control case.
- Observed gap: Degraded readiness could return HTTP 200, and no strict benchmark closure contract requires identity, cross-layer traces, repeated metrics, and hashed evidence.
- Existing-system baseline: The existing control plane uses a FastAPI API, file-ledger task assignments, a supervised lifecycle worker, Compose Airflow and MLflow services, Kubernetes model serving, and Prometheus. Health and metrics exist, but one exported cross-runtime trace and machine-enforced evidence closure do not yet cover that real path.
- Architecture before: Health, metrics, and logs exist without one machine-enforced evidence closure.
- Architecture after: Readiness, bounded telemetry, distributed trace identity, and benchmark closure gate every later scenario.
- Verdict: `passed`
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario. A scenario pass does not replace final cross-scenario system validation.
- Next action: Begin S1 transactional job state and idempotency implementation while keeping the corrected S0 suite as the regression baseline.

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

- `S0-AC-01` [passed]: Only healthy active targets are included in the baseline.
- `S0-AC-02` [passed]: Exact source, data, model, and runtime identity is complete.
- `S0-AC-03` [passed]: p50, p95, p99, fixed-window throughput, queue, load-generator permit wait, retry, CPU, RAM, GPU, and VRAM are queryable.
- `S0-AC-04` [passed]: Trace propagation spans every declared lifecycle stage with zero missing links.
- `S0-AC-05` [passed]: Three independent controls are comparable and variance is reported.

### Current Evidence

- `docs/status/evidence/s0-otel-implementation-checkpoint.json` (`fbef5bc797a91097e136a2510bb4740fd85c9efc5255e9cd5501e68c1fd046d7`): Collector configuration, one OTLP probe, and 583-test regression passed; runtime-wide S0 acceptance remains pending.
- `docs/status/evidence/s0-in-place-telemetry-boundary-checkpoint.json` (`65e86d95d63869f6c47e80f35c14b713d66c7ee56bbe81ce95d1bcabe44ef55e`): Bounded telemetry, desired-state target discovery, local Spark stage, and 588-test regression passed at contract level; runtime S0 acceptance remains pending.
- `docs/status/evidence/s0-spark-runtime-path-remediation-checkpoint.json` (`a30afb6ac712b82e6d2e6e768e36f2fce4ce1af738a4b3b90e273fd65e51a9e1`): A real local Spark computation exposed JVM and cross-runtime path gaps; both were remediated with 590-test regression, but a fresh accepted Spark evidence run remains pending.
- `docs/status/evidence/s0-spark-runtime-component-checkpoint.json` (`11a31ffe92c5b1b5bd6c4fa1e6ae2e4230ad6497b91b0bbc79c9da766270b62f`): One real bounded local Spark component run persisted through the existing data mount and exported linked OTLP spans; full S0 lifecycle acceptance remains pending.
- `docs/status/evidence/s0-serving-runtime-identity-contract-checkpoint.json` (`d47a2b0d7a8d5c47cc3ee7948b85aada58cd40f54f6dea65a131054b9af7dd2a`): Model-source and serving-runtime revisions are now separate in the existing serving contract; 593 tests passed, while live serving revision alignment and S0 runtime acceptance remain pending.
- `docs/status/evidence/s0-low-load-control-runner-checkpoint.json` (`f0bd6847bfec46694b4917c463a0774b121b4ba704d5288a1031f35ef4f9b89e`): An in-place low-load control runner and runtime-wide revision labels passed 600 tests; live controls and S0 acceptance remain pending.
- `docs/status/evidence/s0-first-control-rca-checkpoint.json` (`c3f80a305f377e301a591f9758f05032b4a20056acb6da68ca574543d48d7b3e`): One fresh control traversed Airflow, Spark, MLflow, and real CUDA inference but failed closed because the serving OTLP stage was absent; the RCA remediation passed 603 tests and acceptance remains pending.
- `docs/status/evidence/s0-serving-runtime-dependency-rca-checkpoint.json` (`1f9046690fb1fff2e0fd05b886c2684cfddb578ca1e0564920421b4725106729`): An exact serving replacement failed closed on a missing OTLP exporter, the B0 fallback recovered at 1/1, and compatible host-runtime pins plus 604-test regression passed; accepted controls remain at zero.
- `docs/status/evidence/s0-evidence-contract-rca-checkpoint.json` (`7a587a0697da2cc13112bb4ac20e8f9966acab90feba228e06db23d87cc90db0`): An independent audit invalidated the first S0 closure because public hashes used CRLF worktree bytes, fixed-window pacing and seed application were absent, and permit wait was mislabeled as pool wait.
- `docs/status/evidence/s0-low-load-control-1.json` (`d1f3c212a7a61b4f76c7f796b27e0eee06e18ddb55ea5d61add8397b73bdedbb`): S0 fixed-window control 1 passed the runtime contract.
- `docs/status/evidence/s0-low-load-control-2.json` (`516d3112ab50a8d0d440fe9c0b261868f7d4ed89dc3bcba6f052cf04ecae75c6`): S0 fixed-window control 2 passed the runtime contract.
- `docs/status/evidence/s0-low-load-control-3.json` (`392d6b440a2b11ad60fd21598147fc7e1253cbe19fbb8bdd2adf683242f2d8fc`): S0 fixed-window control 3 passed the runtime contract.
- `docs/status/evidence/s0-cross-runtime-trace-summary.json` (`531ec01523fb41aaa8f74088bd2b5ef370552ab5f749457794f147b82b922e05`): All fresh fixed-window controls observed every required runtime stage.
- `docs/status/evidence/s0-low-load-benchmark-evidence.json` (`5f33c3ad010bcdb0568001a41efe2213ede3b74517f359662eed3e8184461eb2`): Three fresh fixed-window controls passed with canonical Git-byte hashes, deterministic seeded inputs, complete identity, and reported variance.

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
- `2026-08-15T10:25:23.802715Z` `verification` / `verified`: Three fresh controls executed the declared fixed-rate 60-second window with deterministic seeded inputs. Cross-runtime traces, healthy targets, bounded metrics, canonical Git-byte hashes, variance, cleanup, and regression all passed.

## S1: Transactional Job State & Idempotency

- Status: `verified`
- Engineering question: Can 100 to 500 concurrent mutations commit one legal and idempotent outcome?
- Why now: Durable state correctness must precede retries and queued high-volume mutation.
- Observed gap: The earlier store-level concurrency proof stopped at concurrency 64 and simulated lease time. It did not prove measured 100/250/500 external HTTP concurrency, bounded pool failure through the API, exact worker PID loss, non-vacuous effects, or PostgreSQL-to-JSON mirror recovery.
- Existing-system baseline: The existing control plane serializes file-ledger writes and uses run claims and side-effect ledgers, but concurrent durable state transitions are not owned by one transactional database contract.
- Architecture before: The first S1 revision had transactional storage but accepted only an in-process concurrency-64 proof and simulated lease expiry.
- Architecture after: The existing API and supervised worker expose measured external concurrency, bounded pool failure, exact process-loss fencing, one-time effects, and database-authoritative JSON mirror recovery.
- Verdict: `passed`
- Claim boundary: Verified external HTTP transactional mutation and real supervised worker-loss recovery on one local physical node with isolated PostgreSQL schemas. No customer traffic, multi-node database HA, disaster recovery, production availability, or SLA claim.
- Next action: Keep S1 as the regression boundary. S2 is explicitly not started in this work unit and requires a separate implementation checkpoint.

### Affected Existing Components

- Lifecycle and task state: `src/evm/control_panel/lifecycle_runs.py`, `src/evm/control_panel/operations.py`
- Worker ownership: `src/evm/control_panel/lifecycle_worker.py`, `src/evm/operations/scenario_d_supervision.py`
- Control API: `apps/api/main.py`, `apps/api/control_panel_lifecycle.py`, `apps/api/control_panel_tasks.py`, `apps/api/control_panel_deployments.py`

### Architecture Delta

- Before: The first S1 revision had transactional storage but accepted only an in-process concurrency-64 proof and simulated lease expiry.
- After: The existing API and supervised worker expose measured external concurrency, bounded pool failure, exact process-loss fencing, one-time effects, and database-authoritative JSON mirror recovery.
- Selection reason: Correctness claims must cross the real TCP API, PostgreSQL pool, OS process, supervisor, worker, outbox, and rollback-mirror boundaries.
- Selection reason: Measured peak in-flight distinguishes actual concurrency from submitted task count.
- Alternative/trade-off: An in-process store harness is faster and deterministic but cannot prove HTTP admission, process metrics, supervisor recovery, or OS fencing.
- Alternative/trade-off: The retained JSON mirror provides rollback compatibility but requires explicit reconciliation and remains operational debt.

### Proposed Design

- Use database transactions and unique idempotency keys for every state mutation.
- Enforce legal transitions, atomic lease claims, fencing, and stale-owner reconciliation.
- Bound connection-pool size and wait time and expose conflict outcomes as telemetry.

### Implementation Delta

- Dedicated transactional control-plane repository: `src/evm/control_panel/transactional_store.py`, `infra/postgres/control-plane/001_transactional_control_plane.sql`, `docker-compose.yml`, `pyproject.toml`, `apps/api/requirements.txt`
- Existing lifecycle state and worker ownership: `src/evm/control_panel/lifecycle_runs.py`, `src/evm/control_panel/lifecycle_worker.py`, `src/evm/control_panel/lifecycle_guards.py`, `src/evm/operations/scenario_d_supervision.py`, `scripts/dev/start_lifecycle_worker.ps1`, `scripts/dev/start_host_runtime_supervisor.ps1`
- Existing task, deployment, and side-effect boundaries: `src/evm/control_panel/operations.py`, `src/evm/control_panel/deployment_intents.py`, `src/evm/control_panel/schemas.py`, `apps/api/control_panel_tasks.py`, `apps/api/control_panel_deployments.py`, `apps/api/control_panel_lifecycle.py`, `apps/api/main.py`
- Migration, experiment, and evidence validation: `scripts/dev/migrate_control_plane_state.py`, `scripts/validation/run_s1_migration_parity.py`, `scripts/validation/run_s1_transactional_state_experiment.py`, `tests/test_transactional_control_plane.py`, `tests/test_control_panel_contract.py`, `contracts/control-panel/control-panel.openapi.json`, `src/evm/scale_validation/s1_runtime.py`, `tests/test_s1_external_runtime_contract.py`, `tests/test_host_runtime_supervisor_contract.py`, `tests/test_lifecycle_runs.py`
- Compatibility: Existing lifecycle, task, deployment, identifiers, API payloads, lifecycle guards, and S0 telemetry remain compatible.
- Compatibility: PostgreSQL remains authoritative in dual mode while JSON mirrors remain readable and repairable for rollback.
- Migration: Import existing control-plane ledgers into a dedicated isolated PostgreSQL schema and prove repeat-import digest parity.
- Migration: Keep the file-mode rollback switch; do not share or mutate MLflow or Airflow PostgreSQL.
- Migration: Reconcile a missing JSON mirror from the authoritative PostgreSQL entity after a controlled commit-to-mirror gap.

### Experiment Contract

- Workload/input: External create, approve, cancel, and retry mutations at measured peak in-flight 100, 250, and 500, plus API pool exhaustion and one exact supervised worker-process loss.
- Precondition: S0 corrected identity, trace, health, load-profile, and canonical evidence contracts pass.
- Precondition: The isolated API revision, schema, exact worker marker, supervisor state, and cleanup target are unambiguous.
- Controlled variable: Peak client/server in-flight concurrency, route mix, idempotency-key reuse, pool size/acquire timeout, exact worker PID, lease epoch, mirror-gap point, and recovery timeout.
- Signal: Per-route status and latency, request/trace identity, committed state/version, pool timeout metric, worker PID/process instance, lease epoch, stale commit result, outbox effect counts, mirror digest/version, and cleanup.
- Stop condition: Any illegal transition, missing trace identity, unbounded pool wait, ambiguous PID/owner, stale owner commit, duplicate or zero required effect, mirror parity failure, or dirty cleanup stops acceptance.
- Recovery condition: A different supervised process owns the higher epoch, one legal terminal commit remains, all three required effects equal one with duplicates zero, database and JSON match, and isolated resources are removed.

### Acceptance

- `S1-AC-01` [passed]: A real worker commits at least one lifecycle, deployment, and artifact effect, and each required effect is committed exactly once with duplicate count zero.
- `S1-AC-02` [passed]: External create, approve, cancel, and retry conflicts at measured peak in-flight 100, 250, and 500 end in one legal database outcome.
- `S1-AC-03` [passed]: Pool exhaustion through the external API returns a bounded observable failure rather than hanging.
- `S1-AC-04` [passed]: Exact worker process loss advances the fencing epoch, blocks the stale owner, commits one terminal outcome, and restores PostgreSQL/JSON payload and version parity.

### Current Evidence

- `docs/status/evidence/s1-control-plane-migration-evidence.json` (`af1aea3f22d635dd7a940b15638319f5e95e6c3a3d28dc785e397d48634965e9`): Existing ledgers imported once and produced exact repeat-import parity in an isolated PostgreSQL schema.
- `docs/status/evidence/s1-transactional-state-evidence.json` (`f5058d7e2fb91f94dabe2df541bbb70d2be1b82d16408b72150a86d280e1291c`): External HTTP peak-concurrency 100/250/500, bounded API pool failure, and actual supervised worker-loss evidence passed S1-AC-01 through S1-AC-04.
- `docs/status/evidence/s1-transactional-state-closure.json` (`5ed8ffc5eeae086f35b0bfbe7303faa13ab7bf74cc744857ef760b85f115548e`): Strict S1 closure records superseded evidence, non-vacuous effects, PostgreSQL/JSON parity, rejected attempts, tests, cleanup, residual risks, and claim limits.

### Chronological Updates

- `2026-08-14T19:34:00Z` `design` / `planned`: The authoritative in-place scenario contract was reviewed against the existing ML Serve API system.
- `2026-08-15T10:35:00Z` `implementation` / `implementing`: The existing lifecycle, task, deployment, and side-effect writes were placed behind a dedicated PostgreSQL transaction boundary with idempotency, optimistic versions, leases, fencing, a bounded pool, and a durable outbox.
- `2026-08-15T11:17:00Z` `experiment` / `implementing`: Two evidence-runner preflights failed before workload mutation because of connection API and mapping-row access defects; isolated schemas were cleaned and both failures remain in the closure RCA.
- `2026-08-15T11:23:00Z` `recovery` / `implementing`: A full regression exposed a stale OpenAPI contract. Lifecycle, task, and deployment idempotency/version fields were aligned and explicit contract assertions were added.
- `2026-08-15T11:30:04.498992Z` `verification` / `verified`: At aa95f39, migration parity, 395 real-PostgreSQL mutations, actual HTTP idempotent state transitions, 613 general tests, 7 real-PostgreSQL tests, 48 lifecycle regressions, 20 S0 regressions, runtime revision health, and cleanup passed.
- `2026-08-15T12:00:00Z` `design` / `implementing`: An independent audit reopened S1 because concurrency 64, direct-store execution, simulated lease time, and vacuous duplicate counts did not meet the reviewed contract.
- `2026-08-15T14:13:36.513676Z` `verification` / `verified`: At 8a8f54c, external HTTP measured peaks 100/250/500, bounded API pool exhaustion, exact worker PID loss, epoch fencing, non-vacuous exactly-once effects, PostgreSQL/JSON reconciliation, 622 general tests, 7 real-PostgreSQL tests, 57 lifecycle tests, 20 S0 tests, and cleanup passed.
- `2026-08-15T14:31:30.830558Z` `verification` / `verified`: Git closure, Jira SCRUM-202 comment 10607, the Notion V3 canonical page and knowledge-base hub, and the Obsidian canonical work log, Current Context, retrieval index, and graph now report the same strict S1 result and claim boundary.

## S2: Bounded Queue & Backpressure

- Status: `verified`
- Engineering question: Does overload stay memory-bounded while accepted work reaches an explicit outcome?
- Why now: A bounded queue is required before capacity and recovery experiments scale up.
- Observed gap: The prior S2 closure did not persist every V3 bound and wait signal or independently recompute acceptance from numeric evidence; its 30-profile result is retained as historical baseline only until a fresh strict-evidence rerun passes.
- Existing-system baseline: The existing task API wrote assignments to the operations ledger and required explicit dispatch, while the separate lifecycle worker exclusively processed LifecycleRun state. Task admission had no shared depth, byte, age, retry-budget, or poison-work boundary and no dedicated task consumer.
- Architecture before: Admission and retries do not share one end-to-end resource boundary.
- Architecture after: Durable queue, local semaphore, rejection, retry, DLQ, and CPU scale are bounded.
- Verdict: `passed`
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario. A scenario pass does not replace final cross-scenario system validation.
- Next action: Keep S2 as the strict regression boundary and evaluate the S3 start gate.

### Affected Existing Components

- Task admission and dispatch: `src/evm/control_panel/operations.py`, `src/evm/control_panel/task_queue_worker.py`, `src/evm/control_panel/transactional_store.py`, `src/evm/control_panel/task_queue_executor.py`
- Control API: `apps/api/control_panel_tasks.py`, `apps/api/task_ingress.py`, `apps/api/main.py`
- Operational telemetry: `monitoring/prometheus/prometheus.yml`, `src/evm/control_panel/admission_queue.py`
- Runtime and migration: `configs/s2_bounded_queue_v1.toml`, `configs/s2_bounded_queue_cpu1_control.toml`, `infra/postgres/control-plane/002_bounded_admission_queue.sql`, `infra/postgres/control-plane/003_task_queue_safety.sql`, `infra/postgres/control-plane/004_task_entity_storage.sql`, `infra/postgres/control-plane/005_task_queue_operational_safety.sql`, `docker-compose.yml`

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

- Durable admission and task state: `src/evm/control_panel/admission_queue.py`, `src/evm/control_panel/transactional_store.py`, `src/evm/control_panel/operations.py`, `apps/api/control_panel_tasks.py`, `apps/api/task_ingress.py`, `apps/api/main.py`
- Dedicated bounded task worker: `src/evm/control_panel/task_queue_worker.py`, `src/evm/control_panel/task_queue_executor.py`, `src/evm/scale_validation/s2_airflow_fixture.py`
- Frozen runtime, migration, and telemetry: `configs/s2_bounded_queue_v1.toml`, `configs/s2_bounded_queue_cpu1_control.toml`, `infra/postgres/control-plane/002_bounded_admission_queue.sql`, `infra/postgres/control-plane/003_task_queue_safety.sql`, `infra/postgres/control-plane/004_task_entity_storage.sql`, `infra/postgres/control-plane/005_task_queue_operational_safety.sql`, `docker-compose.yml`, `monitoring/prometheus/prometheus.yml`, `configs/s2_experiment_matrix_v1.toml`
- Focused verification: `contracts/control-panel/control-panel.openapi.json`, `tests/test_bounded_task_queue.py`, `tests/test_task_ingress.py`, `tests/test_task_queue_process_safety.py`, `tests/test_control_panel_contract.py`, `tests/test_s2_airflow_fixture.py`, `src/evm/scale_validation/s2_runtime.py`, `scripts/validation/run_s2_bounded_queue_experiment.py`, `scripts/dev/close_s2_scale_validation.py`, `tests/test_s2_runtime.py`
- Compatibility: Existing task payload and lifecycle state-machine contracts must remain valid.
- Migration: The 002 migration adds the durable queue and retry budget to the dedicated control-plane schema without modifying Airflow or MLflow databases.
- Migration: The 003 and 004 migrations add lease-safe execution, row-level task entities, and bounded terminal-history rollups; the JSON collection remains a bounded rollback mirror.
- Migration: The 005 migration adds fair runtime polling, outcome-unknown state, bounded idempotency tombstones, and effect-state fencing without changing Airflow or MLflow stores.
- Migration: EVM_TASK_ADMISSION_MODE=legacy retains the prior dispatcher as an explicit rollback path; durable is enabled in the local Compose stack for S2 validation.

### Experiment Contract

- Workload/input: Independent burst, sustained, duplicate, expired, and poison task streams through the existing assignment endpoint.
- Precondition: S1 commits one idempotent task outcome and S0 telemetry is queryable.
- Controlled variable: The versioned frozen profile controls depth, aggregate bytes, item bytes, age, wait, local buffers, work timeout, CPU min/max, GPU=1, lease, retry/jitter/budget, drain, RSS cap, and RSS slope tolerance.
- Controlled variable: Arrival rate and independent baseline, depth burst, byte burst, sustained, duplicate, expired, transient-budget, poison-plus-healthy, timeout-restart, and GPU-bound workload identities.
- Signal: Admission status, Retry-After, queue depth/bytes/age, RSS, wait time, retry amplification, DLQ, and terminal closure.
- Stop condition: RSS, queue depth, queue bytes, local bytes, or GPU in-flight exceed the frozen bound; accepted work is lost or unclosed; duplicate effects, poison head-of-line blocking, retry amplification, unbounded wait, trace gaps, or S0/S1 regression appear.
- Recovery condition: Admission closes, active/leased/local work drains to zero, poison work is quarantined, stale ownership is reconciled, worker and Prometheus health recover, temporary schema/processes are removed, and duplicate effects remain zero.

### Acceptance

- `S2-AC-01` [passed]: Queue depth, in-flight bytes, and process memory remain bounded under overload.
- `S2-AC-02` [passed]: Accepted work completes or reaches one explicit terminal failure.
- `S2-AC-03` [passed]: Duplicate effects are zero and poison work does not block healthy work.
- `S2-AC-04` [passed]: Rejection, wait, retry, and DLQ behavior are measured from persisted numeric evidence.

### Current Evidence

- `docs/status/evidence/s2-operational-safety-checkpoint.json` (`1b9316b397662b0ade60861a7d1b77b2a32cc62082cf99561bf5d307dc5c6faf`): At revision 5de1c41, in-place S2 operational-safety code and regressions passed; all external workload and S2 acceptance evidence remains pending.
- `docs/status/evidence/s2-bounded-queue-experiment.json` (`3ad2c5173e1b19effe75267713d1e5e264b2dfb1a55758db4eb37ab1ab87a7cd`): Thirty independent A-J profile repetitions passed the frozen external HTTP, PostgreSQL, worker, Prometheus, OTLP, recovery, and one-GPU contract.
- `docs/status/evidence/s2-bounded-queue-closure.json` (`dc9da8e714a8a8bece40f31591e6f389df90c20f7d25d672c2157a45785800d8`): S2 closure records regressions, 5 retained failed attempts with RCA, cleanup, residual limits, and the accepted claim boundary.

### Chronological Updates

- `2026-08-14T19:34:00Z` `design` / `planned`: The authoritative in-place scenario contract was reviewed against the existing ML Serve API system.
- `2026-08-15T15:47:07.929559Z` `implementation` / `implementing`: Durable bounded admission, distinct 413/429 responses, a dedicated queue worker with a killable child-process timeout boundary, retry/DLQ policy, frozen configuration, Compose startup and Prometheus scrape wiring passed 39 focused tests and an isolated real-PostgreSQL API-worker checkpoint. No acceptance criterion is credited before the full external workload matrix.
- `2026-08-15T17:05:42.248111Z` `implementation` / `implementing`: Durable dispatch no longer holds the global operations-ledger lock across Airflow HTTP, task state is row-addressable, and terminal queue/effect/task history is compacted into fixed-cardinality rollups with a bounded JSON mirror. Fifty-eight focused tests passed, including two concurrent real-PostgreSQL dispatches, retained-versus-compacted history accounting, and progress/evidence contract validation. External throughput, process-tree memory, three-repeat workload, and all S2 acceptance criteria remain pending.
- `2026-08-16T14:38:09Z` `implementation` / `implementing`: Revision 5de1c41 closes audited ingress, tombstone, deadline, lease/effect fencing, fair polling, outcome-unknown, cutover, mirror parity, process-tree RSS, executor cleanup, consumer supervision, CPU scaling, and GPU downstream-bound gaps. Thirty-eight focused, 661 full Python, and 59 Control Panel tests plus build and Compose validation passed. A-J external experiments and all S2 ACs remain pending.
- `2026-08-16T17:38:16.247374Z` `experiment` / `implementing`: The first complete 30-profile suite preserved all passing profile assertions but failed S2-AC-01 because instantaneous sampling missed three short-lived executor process trees. The failure and cleanup were retained; no acceptance credit was awarded.
- `2026-08-16T17:55:36.258827Z` `recovery` / `implementing`: After retained executor RSS was added, a transient Windows heartbeat replace lock terminated the D CPU-one worker. The suite stopped before E-J, cleanup passed, and bounded heartbeat replacement retry was added.
- `2026-08-17T02:00:00.262659Z` `recovery` / `implementing`: Independent V3 review found that the previous public projection trusted assertion booleans and did not persist every durable/local/in-flight byte, ingress, wait, retry-delay, DLQ, and process-tree RSS signal. S2 was reopened; old 30-profile evidence is baseline-only. Strict numeric recomputation and mutation tests passed focused verification, while the mandatory fresh A-J x3 rerun remains pending.
- `2026-08-17T02:55:16.782956Z` `recovery` / `implementing`: The first strict-evidence rerun was rejected before acceptance because the Python editable installation resolved s2_runtime.py from a different worktree, so it executed the historical v6/v5 contract rather than the checked-out v7/v6 revision. The generated public file was restored, the private failed-attempt RCA and cleanup proof were retained, and the entrypoint now pins and verifies the repository-local runtime module with hostile-PYTHONPATH and root-mismatch regressions. A fresh A-J x3 rerun is still required.
- `2026-08-17T03:39:06.132903Z` `experiment` / `implementing`: The clean d41ab21 strict A-J x3 run completed all 30 profile repetitions but failed closed: S2-AC-01 and S2-AC-04 were false. Profile G reached 20 CPU downstream outstanding against the frozen limit of 8, and profile E lost its pre-restart queue-wait histogram from the final projection. Identity closure, exactly-once effects, traces, GPU activity, worker recovery, and cleanup passed, but no S2 acceptance credit was awarded; private raw evidence and RCA were retained.
- `2026-08-17T03:50:33.637758Z` `recovery` / `implementing`: Known-rejected deterministic submissions now reset their fenced effect to reserved only after a follow-up GET proves absence; ambiguous submissions become outcome_unknown immediately and continue to consume the downstream bound. Profile E carries its pre-restart queue-wait histogram into final evidence. Sixty-five focused real-PostgreSQL tests, 688 full Python tests, 59 Control Panel tests, lint, and the production frontend build passed. A clean external preflight and full A-J x3 rerun remain required.
- `2026-08-17T04:01:46.723977Z` `recovery` / `implementing`: The clean 277d578 E/G/H preflight confirmed that all three E runs retained 520 queue-wait observations and downstream peaks stayed within the frozen limit, while the first G run closed 20 transient tasks in DLQ with a CPU downstream peak of 4. The preflight stopped because the evidence helper preferred the generic last failure class over the retry-budget terminal reason. Reason precedence and its regression were corrected; no acceptance credit was awarded and a fresh clean preflight remains required.
- `2026-08-17T04:14:09.495721Z` `experiment` / `implementing`: At clean revision 1e49f81, the independent E/G/H preflight passed all nine profile repetitions. Every E run retained 520 queue-wait observations and exactly-once identity closure, every G run measured bounded retry delay and retry-budget DLQ closure with CPU downstream outstanding at four of eight, every H run isolated poison work while healthy work completed, and every isolated schema/process cleanup passed. This partial matrix receives no S2 acceptance credit; the full clean A-J x3 matrix remains required.
- `2026-08-17T04:41:22.389871Z` `recovery` / `implementing`: The clean 9db0f56 A-J rerun stopped fail-closed after F repetition three: the PostgreSQL-clock-minus-one-second expiry fixture was still future-dated relative to the worker clock, so the intended expired item completed and emitted an external effect. Eighteen completed repetitions and cleanup were retained outside Git with RCA. The injection now selects the earlier PostgreSQL/worker clock minus a fixed ten-second margin and records both clocks, delta, deadline, and margin; 46 focused and real-PostgreSQL tests passed. A clean F x3 preflight and full A-J x3 rerun remain required.
- `2026-08-17T05:19:47.579355Z` `recovery` / `implementing`: The clean 48b5742 rerun passed A through H for 24 profile repetitions and then stopped fail-closed at I repetition one. Worker replacement, epoch-two recovery, slow and healthy exactly-once effects, explicit timeout failure without an effect, PostgreSQL/JSON parity, Prometheus targets, and cleanup all succeeded. The evidence runner incorrectly classified the timeout closure as a success-effect and full-success-trace obligation. Private RCA was retained; the runner now separates success and timeout-failure effect/trace contracts. An independent I x3 preflight and a fresh complete A-J x3 run remain required, so no S2 acceptance credit is awarded.
- `2026-08-17T05:33:02.210477Z` `experiment` / `implementing`: At clean revision 3b32136, the isolated I preflight passed all three repetitions. Each run replaced the exact worker process with no orphan child, recovered the slow task at lease epoch two, closed two effect-eligible tasks exactly once, closed one timeout task as an explicit failure with no effect, completed all three required trace contracts, preserved PostgreSQL/JSON parity, and cleaned its schema and processes. The private evidence aggregate is c844843160bb9e93b1785869312a0440251630fa56511a8661e825ecf7d3beea. This partial run receives no S2 acceptance credit; a fresh complete A-J x3 run remains required.
- `2026-08-17T06:12:10.817351Z` `verification` / `verified`: At e5b399a, the strict-evidence A-J matrix passed 30 of 30 profile repetitions, S2-AC-01 through S2-AC-04, all 11 readiness gates, regressions, and the production frontend build. Canonical public evidence and retained failed-attempt RCAs are hash-linked.

## S3: HIGGS Lightweight Capacity Envelope

- Status: `verified`
- Engineering question: Where are sustainable capacity, tail-latency limits, and the first CPU bottleneck?
- Why now: Lightweight probes separate infrastructure overhead from model compute.
- Observed gap: No repeated CPU-model capacity envelope or saturation knee exists.
- Existing-system baseline: The existing API can execute model inference and expose metrics, but it has no governed high-volume tabular corpus or repeated CPU/API saturation envelope.
- Architecture before: API behavior is functional but not characterized with low-compute probes.
- Architecture after: A measured CPU/API capacity envelope supplies operational and queue limits.
- Verdict: `passed`
- Claim boundary: Measured lightweight HIGGS CPU/API capacity, bounded process-local queue behavior, external TCP/HTTP handling, Prometheus telemetry, W3C trace attribution, and colocated replica/worker comparisons on one local physical node. No production or customer traffic, SLA, physical multi-node or multi-zone HA, stateful HA/DR, multi-GPU, business A/B, or terabyte claim.
- Next action: Commit and Git-blob validate this strict reclosure, then begin S4 preparation without carrying S3 runtime evidence forward as S4 acceptance credit.

### Affected Existing Components

- Existing scenario intake and execution: `src/evm/control_panel/scenario_workloads.py`, `src/evm/model_runtime/capacity_probe.py`, `src/evm/model_runtime/capacity_executor.py`, `apps/api/control_panel_workloads.py`, `apps/api/main.py`
- Existing online API and metrics: `apps/api/main.py`, `src/evm/operations/metrics.py`
- External capacity runner and frozen runtime experiment contract: `configs/s3_capacity_runtime.toml`, `src/evm/scale_validation/s3_runtime.py`, `scripts/dev/run_s3_capacity_experiment.py`, `tests/test_s3_runtime.py`
- Strict S3 evidence projection and Git source identity: `src/evm/scale_validation/s3_runtime.py`, `src/evm/scale_validation/s3_evidence.py`, `scripts/dev/reproject_s3_capacity_evidence.py`, `scripts/dev/validate_s3_capacity_evidence.py`, `tests/test_s3_runtime.py`, `tests/test_s3_capacity_evidence.py`

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

- Existing Workloads API and S3 tabular capacity runtime: `src/evm/control_panel/scenario_workloads.py`, `src/evm/model_runtime/capacity_probe.py`, `src/evm/model_runtime/capacity_executor.py`, `apps/api/control_panel_workloads.py`, `apps/api/main.py`
- Governed HIGGS preparation and frozen S3 experiment configuration: `src/evm/scale_validation/s3_higgs.py`, `scripts/dev/prepare_s3_higgs_capacity.py`, `configs/s3_higgs_capacity.toml`
- External capacity runner and frozen runtime experiment contract: `configs/s3_capacity_runtime.toml`, `src/evm/scale_validation/s3_runtime.py`, `scripts/dev/run_s3_capacity_experiment.py`, `tests/test_s3_runtime.py`
- Strict S3 evidence projection and Git source identity: `src/evm/scale_validation/s3_runtime.py`, `src/evm/scale_validation/s3_evidence.py`, `scripts/dev/reproject_s3_capacity_evidence.py`, `scripts/dev/validate_s3_capacity_evidence.py`, `tests/test_s3_runtime.py`, `tests/test_s3_capacity_evidence.py`
- Compatibility: The existing VLM/LLM workload types, routes, lifecycle semantics, and response schemas are unchanged.
- Compatibility: S3 uses static capacity-probe subroutes under the existing scenario-workloads router and does not create a parallel service.
- Migration: Register governed tabular profiles without changing existing image or generative workload contracts.

### Experiment Contract

- Workload/input: A fixed public high-volume tabular split replayed across lightweight CPU estimators.
- Precondition: S0 passes and S1/S2 provide idempotent bounded execution.
- Controlled variable: Model, split, seed, arrival model, concurrency, API replicas, CPU workers, warmup, and duration.
- Signal: RPS, p50/p95/p99, errors, queue wait, validation/transform/predict spans, CPU, RAM, and load-generator cost.
- Stop condition: Error or latency guardrail is crossed, queue no longer drains, or resource saturation risks the host.
- Recovery condition: Load stops, queues drain, replicas return to baseline, and each repetition has complete evidence.

### Acceptance

- `S3-AC-01` [passed]: Every probe has p95, p99, throughput, error, and resource curves.
- `S3-AC-02` [passed]: The first bottleneck is explained by trace and resource telemetry.
- `S3-AC-03` [passed]: The sustainable operating point is explicitly lower than peak throughput when required.
- `S3-AC-04` [passed]: S2 queue capacity is recalculated from the measured service rate, with the prior and selected values, units, formula, safety factor, and rollback value retained.

### Current Evidence

- `docs/status/evidence/s3-higgs-preparation-checkpoint.json` (`f565ac51948b108c9dc1975536f9e795efa70bf7cfc2d85a4343040a19df2e87`): Governed full-source preparation, artifact integrity, and external API implementation smoke passed; all S3 capacity acceptance criteria remain pending.
- `docs/status/evidence/s3-bounded-executor-checkpoint.json` (`7067b26fa4c4b62c48fb0b04c6f01a5f53797f31f1d66dc44715e3dc04e46ff6`): Bounded count/byte admission, CPU worker execution, overload response, trace propagation, and terminal drain passed through the actual external API; capacity acceptance remains pending.
- `docs/status/evidence/s3-capacity-runner-checkpoint.json` (`af7a4b0b9991a6648f4456b45b337f645ce1a7705724663e2f79378ae27d3884`): The actual external S3 orchestration path, bounded executor telemetry, sampled cross-thread trace chain, resource sampling, and cleanup passed one low-load implementation pilot; all S3 capacity acceptance criteria remain pending.
- `docs/status/evidence/s3-capacity-experiment-attempt-01.json` (`ea6a2ec373c88ae5412363f8fc65a125df0a7163c6c6982982f9937311c6a82c`): The first full matrix attempt retained 30 point repetitions and stopped fail closed on an overly strict transport-error assertion; the RCA pilot separated measured errors from evidence identity without granting acceptance credit.
- `docs/status/evidence/s3-capacity-experiment-attempt-02.json` (`2fd4396c464888c009688edf4407289780aaa0724627173deb310387e0ce406d`): The second full matrix attempt retained 21 point repetitions and stopped on an incomplete OTLP exporter tail; bounded polling closed 52/52 sampled traces in a three-repetition RCA pilot without granting acceptance credit.
- `docs/status/evidence/s3-capacity-experiment-attempt-03.json` (`62f68e276b67c7a3149fcdf91d331ef317b4aeccd3bc60292fbf5c4e7d8455c5`): The third attempt retained the Windows shutdown trace-loss failure; pre-stop bounded export closure then completed 34/34 sampled chains across a three-repetition RCA pilot.
- `docs/status/evidence/s3-capacity-experiment-attempt-04.json` (`77b3d23475b0bf75a4f3a109e527cb8cc3354a6b4b1e0193595f51c48cbdb845`): The fourth attempt retained 85 point repetitions and exposed an outcome-insensitive trace assertion; a three-repetition overload pilot then closed full, admission, and client-only contracts separately.
- `docs/status/evidence/s3-capacity-experiment.json` (`f2567c647e9eee409c467f2bd548ff589a8a37eae3fb31d7162aa81646d3e343`): The preserved 111-point external matrix was deterministically reprojected at frozen precision; all four ACs passed from persisted raw-derived signals.
- `docs/status/evidence/s3-capacity-closure.json` (`61866eb2f5bce8bf8d1c0a234c26d2ca03b170dd1ba1999b58bbde65af9d9b77`): Strict closure binds canonical Git source identities, cross-Python projection, current-revision runtime smoke, regressions, CUDA proof, private rehash, and cleanup.
- `docs/status/evidence/s3-strict-reclosure-remediation-checkpoint.json` (`3f646c35a08ef63609007cbd258f0012f8e5a210ec720665fda06b2d142df967`): The source-revision, line-ending, float-portability, and weak AC validation defects were retained as an explicit remediation checkpoint before reclosure.

### Chronological Updates

- `2026-08-14T19:34:00Z` `design` / `planned`: The authoritative in-place scenario contract was reviewed against the existing ML Serve API system.
- `2026-08-17T06:22:20.608833Z` `implementation` / `implementing`: The strict S0-S2 Git-blob evidence gate passed at synchronized revision f7beb6e. S3 entered in-place implementation with verdict not_run, all four acceptance criteria pending, three independent repetitions retained as a global procedure, and S3-AC-04 restored to measured-service-rate S2 queue-capacity recalculation.
- `2026-08-17T06:44:10.199901Z` `implementation` / `implementing`: The governed HIGGS preparation path and frozen experiment TOML were implemented and covered by synthetic streaming/runtime tests. The official source was downloaded outside Git and observed at 2,816,407,858 bytes with SHA-256 ea302c18164d4e3d916a1e2e83a9a8d07069fa6ebc7771e4c0540d54e593b698; full preparation has not run yet.
- `2026-08-17T06:53:33.066608Z` `implementation` / `implementing`: The governed full-source preparation completed at bf76c44, all 12 split and five model hashes matched, five external TCP predictions and the existing VLM/LLM preset contract returned 200, four bounded S3 metric families were observed, and runtime cleanup passed. This remains an implementation checkpoint with all S3 acceptance criteria pending.
- `2026-08-17T07:05:29.142118Z` `implementation` / `implementing`: At clean revision 69876df, the actual external API enforced process-local count and byte admission with a fixed CPU worker pool: 100 concurrent requests produced 32 successes and 68 bounded 429 responses with valid Retry-After, all 100 trace IDs propagated, and queue, bytes, in-flight, and outstanding gauges drained to zero. This is implementation smoke only; all S3 ACs remain pending.
- `2026-08-17T07:35:13Z` `implementation` / `implementing`: At 6f43d87, the external capacity runner and frozen runtime contract passed 30 focused tests. A clean-revision low-load pilot completed 3,371 requests with zero errors, p99 12.50 ms, 34/34 complete sampled trace chains, nonzero API and load-generator resource telemetry, terminal drain, and exact cleanup. The initial zero-CPU telemetry defect was retained as RCA and corrected with cumulative CPU-time deltas. This is not capacity acceptance.
- `2026-08-17T08:29:41Z` `experiment` / `implementing`: The first full frozen-matrix attempt stopped after 30 point repetitions when one transport error in 2,984 observations failed an absolute-zero assertion. The failed aggregate and private hash were retained. The runner now keeps transport failures in the error-rate curve, requires client identity for every attempt and response trace identity for every server response, and a three-repetition same-point RCA pilot passed all evidence assertions. Full acceptance remains pending and must restart from a clean revision.
- `2026-08-17T08:51:24Z` `experiment` / `implementing`: The second full attempt stopped after 21 point repetitions because 4 of 52 sampled traces were absent from a one-shot OTLP tail read despite complete API identities and cleanup. The frozen runtime now uses bounded polling rather than a fixed sleep; the same point then completed 52/52 sampled traces in three independent RCA repetitions with at most 0.27 seconds flush wait. No acceptance credit is carried forward, and the full matrix must restart from the clean fix revision.
- `2026-08-17T08:58:07Z` `experiment` / `implementing`: The third full attempt stopped after two repetitions because Windows terminated the API before two sampled trace chains were exported; a post-stop poll could not recover dropped BatchSpanProcessor data. Trace closure now runs against the live API before process termination and final tail rehash. A three-repetition same-point pilot completed 34/34 chains each time with exact cleanup. Full acceptance remains pending.
- `2026-08-17T10:05:25Z` `experiment` / `implementing`: The fourth full attempt reached 85 valid point repetitions before a topology overload mixed successful, admission-rejected, and transport-only outcomes. The prior assertion incorrectly required the full execution chain for all three. Outcome-aware contracts now require six spans for 200, route plus admission spans for server rejection, and client identity only for transport failure. A three-repetition same-point pilot closed every applicable contract while preserving error and saturation metrics. Full acceptance remains pending.
- `2026-08-17T11:44:27Z` `verification` / `verified`: At runtime revision 20524b9 and strict closure revision 1fda82b, 111 external point repetitions, S3-AC-01 through S3-AC-04, private inventory rehash, 723 real-PostgreSQL Python tests, 50 lifecycle/host tests, 59 Control Panel tests, the production frontend build, Git-blob validation, current-revision runtime smoke, and cleanup passed. Four failed attempts and RCA remain linked. S4 was not started.
- `2026-08-22T23:34:14.631000Z` `recovery` / `implementing`: S3 strict closure was reopened after Git-object, line-ending-sensitive hash, cross-Python float, and AC-02/03 validation defects were independently reproduced. The 111 accepted repetitions and four prior RCA remain preserved; no workload result is being rerun or reinterpreted as verified at this checkpoint.
- `2026-08-23T00:12:31.382496Z` `verification` / `verified`: S3 strict reclosure passed after deterministic cross-Python reprojection, canonical Git-object binding, raw-derived AC validation, current-revision external smoke, private rehash, trusted CUDA regression, and cleanup.

## S4: HIGGS Tiny MLP GPU Batching

- Status: `verified`
- Engineering question: Which small-model batch and queue-delay settings maximize throughput within p99 and VRAM?
- Why now: A lightweight GPU probe reveals scheduler and batching cost without a large model.
- Observed gap: No throughput-latency-VRAM Pareto curve exists for the accelerator path.
- Existing-system baseline: The existing single-accelerator path already provided governed training, serving, and an exclusive lease. It had no in-place tabular CUDA batching endpoint or measured batch-delay-instance-VRAM operating envelope.
- Architecture before: The accelerator path ran one request-oriented model runtime without a measured dynamic batching envelope.
- Architecture after: The existing Workloads API now provides a bounded CUDA Tiny MLP batcher with versioned batch, delay, instance, lease, trace, and VRAM identities, a baseline-relative quiet recovery gate, an independently validated zero-OOM candidate, and a measured open-loop service rate used to recommend rather than silently apply an S2 queue bound.
- Verdict: `passed`
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario. A scenario pass does not replace final cross-scenario system validation.
- Next action: Keep S5 planned and not started; separately reconcile the historical task-queue cutover mismatch before claiming the entire shared runtime is healthy.

### Affected Existing Components

- Existing Workloads API and application lifecycle: `apps/api/control_panel_workloads.py`, `apps/api/main.py`
- Existing exclusive accelerator lease contract: `src/evm/control_panel/scenario_workloads.py`
- In-place bounded CUDA batching runtime: `src/evm/model_runtime/gpu_batch_probe.py`, `src/evm/model_runtime/tiny_mlp.py`
- Versioned S4 runtime, runner, and container contract: `src/evm/scale_validation/s4_runtime.py`, `scripts/dev/prepare_s4_tiny_mlp.py`, `scripts/dev/run_s4_gpu_batching_experiment.py`, `configs/s4_gpu_batching_runtime.toml`, `infra/docker/gpu-batching/Dockerfile`
- Focused S4 regression coverage: `tests/test_s4_gpu_batch_probe.py`, `tests/test_s4_runtime.py`

### Architecture Delta

- Before: The accelerator path ran one request-oriented model runtime without a measured dynamic batching envelope.
- After: The existing Workloads API now provides a bounded CUDA Tiny MLP batcher with versioned batch, delay, instance, lease, trace, and VRAM identities, a baseline-relative quiet recovery gate, an independently validated zero-OOM candidate, and a measured open-loop service rate used to recommend rather than silently apply an S2 queue bound.
- Selection reason: A tiny MLP isolates GPU scheduler and batch-formation cost while reusing the real Workloads API and single-GPU lease boundary.
- Alternative/trade-off: Multiple concurrent training jobs and parallel model-serving claims remain excluded because the hardware cannot provide trustworthy MIG-style isolation.

### Proposed Design

- Sweep batches 1, 4, 8, 16, and 32 with bounded queue delays.
- Keep one model instance by default and vary instance count separately from batch size.
- Record allocated, reserved, and peak VRAM, utilization, and formed batch size.
- Run training under a separate exclusive lease, never during inference benchmarking.

### Implementation Delta

- Existing Workloads API and application lifecycle: `apps/api/control_panel_workloads.py`, `apps/api/main.py`
- Existing exclusive accelerator lease contract: `src/evm/control_panel/scenario_workloads.py`
- In-place bounded CUDA batching runtime: `src/evm/model_runtime/gpu_batch_probe.py`, `src/evm/model_runtime/tiny_mlp.py`
- Versioned S4 runtime, runner, and container contract: `src/evm/scale_validation/s4_runtime.py`, `scripts/dev/prepare_s4_tiny_mlp.py`, `scripts/dev/run_s4_gpu_batching_experiment.py`, `configs/s4_gpu_batching_runtime.toml`, `infra/docker/gpu-batching/Dockerfile`
- Focused S4 regression coverage: `tests/test_s4_gpu_batch_probe.py`, `tests/test_s4_runtime.py`
- Independent S4 evidence and closure validation: `src/evm/scale_validation/s4_evidence.py`, `scripts/dev/validate_s4_gpu_batching_evidence.py`, `tests/test_s4_gpu_batching_evidence.py`
- Compatibility: Existing VLM/LLM request and lifecycle schemas are unchanged; the tabular probe is an opt-in subroute and the legacy serving holder is restored after each controlled window.
- Migration: No data-store migration is required. Batch-one is the frozen rollback profile, and all S4 state is source/config/model/lease identity bound.

### Experiment Contract

- Workload/input: Fixed tabular samples served by one tiny GPU MLP under a batch and delay matrix.
- Precondition: S0-S3 canonical evidence and regressions pass at the current branch history.
- Precondition: The exact single-GPU serving holder is healthy, no unrelated accelerator lease exists, and cleanup can restore the same Deployment UID.
- Precondition: The governed HIGGS split, Tiny MLP artifact, source revision, runtime config, and CUDA identity all match.
- Controlled variable: Batch size, bounded queue delay, model instance count, arrival rate, seed, and warmup.
- Signal: Throughput, p95/p99, formed batch size, queue delay, GPU utilization, allocated/reserved/peak VRAM, and OOM count.
- Stop condition: Any OOM, lease conflict, thermal risk, unbounded queue, or p99 stop threshold occurs.
- Recovery condition: The batch-one known-good profile is restored and accelerator memory returns to baseline.

### Acceptance

- `S4-AC-01` [passed]: Throughput-p99 and throughput-peak-VRAM Pareto curves exist.
- `S4-AC-02` [passed]: The selected operating point has zero accelerator out-of-memory failures.
- `S4-AC-03` [passed]: Model instance count and batch size effects are measured separately.
- `S4-AC-04` [passed]: Queue limits are recalculated from the selected service rate.

### Current Evidence

- `docs/status/evidence/s4-preparation-checkpoint.json` (`c52f4cd814d5bb0627f7adc2ab6684cd2f0bbeedc120f92a1602aec7a071961a`): Final frozen-config external batch-one preparation smoke passed through the existing Workloads API with CUDA inference, distinct operating and hard-stop latency gates, Prometheus/OTLP identity, terminal drain, exact lease release, and serving-holder restoration; S4 acceptance remains pending.
- `docs/status/evidence/s4-gpu-batching-attempt-01.json` (`ec883b9142048f2bc3ff613344bf2245a09b8f11c93dc80ed07403afe1b4223d`): The first full S4 matrix attempt retained 15 completed repetitions and stopped fail closed when 4 of 8 sampled trace chains missed the prior 10-second exporter window; cleanup passed and no acceptance credit was granted.
- `docs/status/evidence/s4-gpu-batching-attempt-02.json` (`b7e73af30a7b64edf9cea2fcd292e1f1df7a02a43442d99cce8d9d9dae035255`): The first corrected trace pilot proved the gap was request-to-batch trace ownership rather than exporter delay: 5 of 8 chains completed after 30 seconds, point-private evidence and cleanup were retained, and no acceptance credit was granted.
- `docs/status/evidence/s4-gpu-batching-attempt-03.json` (`fbb30240e918c917e179c2e49a1c8d4ffc53f38f0854598366babd0f7d1bf579`): The per-request trace fix closed every sampled chain across three pilot repetitions, but concurrency 128 produced one client p99 hard-stop breach; all results and cleanup were retained and no acceptance credit was granted.
- `docs/status/evidence/s4-trace-flush-rca-checkpoint.json` (`bdcd7ba9fe4d8f5043650aaa49f6ff1ddb63429a26ce1ee9d6026e93f33cd888`): At frozen concurrency 64, the corrected batch-4/delay-2 pilot passed three independent repetitions with every sampled trace chain complete, bounded latency, Prometheus recovery, terminal drain, exact cleanup, and no S4 acceptance credit.
- `docs/status/evidence/s4-gpu-batching-attempt-04.json` (`aa2422bcf31fd0698062d8704e443af0bd2a2a32c3d0f3cc001c7e3c7ca43a4d`): A fresh 60-point matrix plus three instance-axis repetitions completed 195,587 requests with zero errors/OOM/trace gaps and exact cleanup, then stopped before open-loop because the analyzer conflated saturation selection with the sustainable latency SLO; no acceptance credit was granted.
- `docs/status/evidence/s4-gpu-batching-attempt-05.json` (`c4f3addf5059a0d83e7c8e0201ed2ad16d6def559292f74e6551cac1a09844df`): The first complete 66-repetition run retained 208,058 requests and complete trace/cleanup evidence, but the 80-percent selected-point replay exceeded the fixed operating queue SLO in two of three repetitions; no acceptance credit was granted.
- `docs/status/evidence/s4-gpu-batching-attempt-06.json` (`5b34c1f19d50c65f8a9e9547ccc7e96538750162a216e780cd1194c2dcf71c52`): A 70-percent rerun retained 64 repetitions and stopped on an invalid open-loop hard-tail result; audit found catch-up burst semantics and missing delivery-fidelity telemetry, so the result receives no acceptance credit.
- `docs/status/evidence/s4-gpu-batching-attempt-07.json` (`959237e8330ceac6474bf24eb8891c5250ac77c4a8a1d3e1e652c6230f1f7af6`): The no-catch-up 107.4 RPS calibration delivered 96.99 percent and exceeded the operating tail SLO; it established a real local capacity boundary and received no acceptance credit.
- `docs/status/evidence/s4-open-loop-pacing-checkpoint.json` (`5ec641448cef87d65a3fe2591b46290ea6b3db91b57d60ece8aa5ef0fd3384b1`): Three external 80 RPS pacing repetitions passed measured delivery fidelity, fixed operating p99 and queue-wait SLOs, complete traces, zero OOM, terminal drain, and exact cleanup; this is calibration evidence only.
- `docs/status/evidence/s4-gpu-batching-attempt-08.json` (`e71f662c94e168ecc7835cf4f6b9418cc9c0cfb8027314c5ea3c9f98fa7aed43`): The clean integrated run retained 64 of 66 repetitions and stopped fail closed when the post-saturation 80 RPS confirmation missed delivery by 0.125 percentage points and exceeded both operating tail SLOs; zero OOM and exact cleanup were preserved.
- `docs/status/evidence/s4-open-loop-60rps-checkpoint.json` (`a977e02ac4ade893791ffe4d5f871c97b6305a24a4de5cffa3a526cc1d6a487f`): Three external no-catch-up 60 RPS calibration repetitions passed delivery fidelity, fixed p99 and queue-wait SLOs, complete traces, zero OOM, terminal drain, and exact cleanup; this remains non-acceptance calibration evidence.
- `docs/status/evidence/s4-gpu-batching-attempt-09.json` (`efbe117319651b4c1a1b812d46c81b56740c4aedc3cc5f4e0a2310447292eb99`): A clean run retained 60 matrix and three instance repetitions, then stopped before open-loop because the initial quiet gate misattributed desktop GPU spikes to the experiment; zero OOM and exact cleanup were preserved.
- `docs/status/evidence/s4-open-loop-stabilization-checkpoint.json` (`f3e439af59887e7721fb4c03011c4337e9d47513caec1ad11baddad018208530`): The baseline-relative quiet gate and three external 60 RPS repetitions passed delivery, fixed latency, trace, zero-OOM, drain, lease, serving-restoration, and cleanup gates; this remains non-acceptance evidence.
- `docs/status/evidence/s4-gpu-batching-experiment.json` (`8d2b3525eee115e38dab33f19b4426b9b8ce529ecd78cdd7b86d15eaf8530a22`): A fresh clean-revision run completed all 66 planned repetitions with all four S4 ACs, zero OOM, complete traces, quiet recovery, and exact cleanup; the independent closure recomputes and validates these claims.
- `docs/status/evidence/s4-current-revision-runtime-smoke.json` (`f5d5f3e2ef8b711a70808dcd5dec4fcbba8f37ec119a62a88141e72af6f4e27b`): At verification revision 5fcfcd7, an external batch-one CUDA smoke completed 456/456 requests at 91.2 requests per second with 24.61 ms p99, one complete sampled trace chain, zero OOM, and exact cleanup; it grants regression evidence, not additional matrix acceptance credit.
- `docs/status/evidence/s4-gpu-batching-closure.json` (`903eef3b356ae5f4dea7e0bc31d8cf5db06c50968d02ef285a61ca3f462bd1a6`): Independent closure recomputes S4-AC-01 through S4-AC-04 from the canonical experiment blob, validates all regressions and 69 private artifacts, and records the single-node claim boundary plus the separate shared-runtime queue-worker residual.

### Chronological Updates

- `2026-08-14T19:34:00Z` `design` / `planned`: The authoritative in-place scenario contract was reviewed against the existing ML Serve API system.
- `2026-08-23T01:00:31.560710Z` `implementation` / `implementing`: At revision 536ba05, the external batch-one preparation smoke completed 389/389 CUDA requests at concurrency 1 with zero errors/OOM/trace gaps, Prometheus UP, terminal drain, exact lease release, and serving-holder restoration. This grants no S4 acceptance credit.
- `2026-08-23T01:05:57.477193Z` `implementation` / `implementing`: At final frozen-config revision ca5dfb1, batch-one completed 388/388 requests at 77.6 RPS with p99 31.21 ms and queue p99 0.078 ms. Operating and hard-stop gates, Prometheus/OTLP, drain, lease release, and holder restoration passed; ACs remain pending.
- `2026-08-23T01:28:26.405439Z` `recovery` / `implementing`: The first full S4 attempt retained 15 completed repetitions and stopped fail closed at batch 4/delay 2 when only 4 of 8 sampled trace chains arrived within the prior 10-second exporter window. CUDA requests, hard bounds, Prometheus, exact cleanup, and holder restoration passed. The runner now uses a frozen 30-second bounded poll and writes failed point evidence before raising; no acceptance credit is carried forward.
- `2026-08-23T01:35:37.399439Z` `recovery` / `implementing`: The 30-second trace pilot completed 3,790/3,790 CUDA requests but only 5 of 8 sampled chains because a shared batch span belonged to the first request parent. Point-private evidence and cleanup were retained. The runtime now separates one shared compute span from request-owned completion spans and bounds final Prometheus recovery; no acceptance credit was granted.
- `2026-08-23T01:41:33.075076Z` `recovery` / `implementing`: Per-request trace ownership closed every sampled chain across all three pilot repetitions, but concurrency 128 produced a 5.87-second client p99 hard-stop breach in repetition three despite zero errors/OOM, bounded server queue wait, Prometheus UP, and exact cleanup. Closed concurrency is now frozen at 64 before acceptance; no pilot result is credited.
- `2026-08-23T01:46:21.293987Z` `implementation` / `implementing`: The frozen-concurrency-64 trace pilot passed three repetitions at 123.77, 125.93, and 127.73 RPS; p99 stayed between 657.30 and 703.84 ms, all 24 sampled chains completed, and Prometheus, drain, lease release, holder restoration, and cleanup passed. This is a non-acceptance RCA checkpoint.
- `2026-08-23T03:00:40.688338Z` `recovery` / `implementing`: A fresh 60-point matrix plus three instance-axis repetitions completed 195,587 requests with zero errors, OOMs, hard-stop failures, or sampled-trace gaps and exact cleanup. The run stopped before open-loop because all saturated points were incorrectly required to satisfy the sustainable 250 ms/100 ms SLO. Candidate discovery is now separated from open-loop SLO validation, and none of the 63 repetitions is reused for acceptance.
- `2026-08-23T04:13:16Z` `recovery` / `implementing`: The first complete 66-repetition run selected batch 8/delay 10 at 140.33 RPS and preserved 208,058 requests, 449/449 sampled traces, zero OOMs, hard safety, terminal drain, and exact restoration. Its 80-percent open-loop replay passed the fixed 250 ms p99/100 ms queue SLO only once; repetitions two and three exceeded queue wait and repetition three exceeded p99. Thresholds remain fixed, a 70-percent replay fraction is now frozen before a fresh full rerun, and no acceptance credit is granted.
- `2026-08-23T05:27:14Z` `recovery` / `implementing`: The 70-percent rerun retained 60 matrix, three instance-axis, and one open-loop repetition with 220,648 requests, 477/477 sampled traces, zero OOMs, and exact cleanup. The open-loop point hard-failed, but its load generator delivered only 83.23 of the configured 107.40 RPS and did not expose release fidelity. Audit found catch-up burst semantics; no-catch-up pacing, release lag, skipped-release, and delivery-ratio gates are now implemented, with acceptance still pending.
- `2026-08-23T05:36:10.550153Z` `recovery` / `implementing`: The no-catch-up 107.4 RPS calibration delivered 104.17 RPS with 96.99-percent fidelity, p99 751.35 ms, queue p99 464.64 ms, complete traces, zero OOM, and exact cleanup. It failed closed and established that this rate is not a reproducible operating point.
- `2026-08-23T05:40:03.158111Z` `implementation` / `implementing`: A separate three-repeat 80 RPS calibration delivered 78.53 to 78.70 RPS with 98.17 to 98.38-percent fidelity, release-lag p99 about 10 ms, p99 72.97 to 119.40 ms, queue p99 23.34 to 40.92 ms, 15/15 sampled traces, zero OOM, and exact cleanup. This freezes an 80 RPS ceiling and grants no acceptance credit before the fresh full run.
- `2026-08-23T06:51:58.597747Z` `recovery` / `implementing`: The integrated 80 RPS run retained all 60 matrix and three instance repetitions, then stopped on its first open-loop confirmation: delivery was 97.875 percent, p99 was 511.80 ms, and queue p99 was 283.08 ms. Zero OOM, complete sampled traces, serving restoration, lease release, and cleanup passed. The standalone 80 RPS result is therefore calibration-only and no acceptance credit is granted.
- `2026-08-23T06:56:57.118483Z` `implementation` / `implementing`: A separate no-catch-up 60 RPS calibration passed all three repetitions at 98.33 to 99.00-percent delivery, p99 57.58 to 85.22 ms, queue p99 15.32 to 24.02 ms, complete traces, zero OOM, terminal drain, and exact cleanup. The next clean run freezes 60 RPS and a 60-second quiet GPU recovery gate; all S4 ACs remain pending.
- `2026-08-23T08:15:10.264354Z` `recovery` / `implementing`: A clean run completed all 60 matrix and three instance repetitions with zero OOM, then the new quiet gate stopped before open-loop. The experiment container was absent, VRAM was below the pre-run baseline, and the exact lease matched, but whole-device Windows display utilization alternated between low samples and transient spikes; an absolute 15-percent maximum therefore produced a false blocker. The gate now retains spikes as evidence while using exact container ownership, stable VRAM/temperature, and a five-sample median relative to the measured baseline. Cleanup and serving restoration passed; no acceptance credit is granted.
- `2026-08-23T08:44:21.746261Z` `implementation` / `implementing`: At clean revision 72ec3d2, the baseline-relative 60-second quiet gate passed with the experiment container absent and exact lease identity. Three external 60 RPS repetitions delivered 58.90 to 59.23 RPS, p99 59.17 to 72.45 ms, queue p99 15.83 to 21.07 ms, 12/12 sampled traces, zero OOM, terminal drain, exact serving restoration, and cleanup. This grants no acceptance credit before the fresh full run.
- `2026-08-23T09:55:37.605649Z` `experiment` / `implementing`: At clean revision a760a49, the full run completed 60 matrix, three instance-axis, and three open-loop repetitions. The selected saturation candidate was batch 8/delay 10/instance 1 at 160.70 mean RPS; the three no-catch-up confirmations delivered 58.90 to 59.10 RPS with p99 63.87 to 73.79 ms, queue p99 17.98 to 20.36 ms, complete traces, zero OOM, and exact cleanup. The runner projected all four ACs as passed, but S4 remains implementing until independent evidence recomputation and closure pass.
- `2026-08-23T10:03:14.085331Z` `verification` / `implementing`: The independent S4 validator recomputed all 66 point identities and four ACs, rehashed the canonical Git experiment blob and 69 private artifacts, and verified source/config ancestry, trace/drain/OOM, quiet recovery, instance effects, S2 capacity, and cleanup. Twenty-seven focused tests and nine negative evidence mutations passed. Full regressions, current-revision smoke, and closure remain pending.
- `2026-08-23T10:20:55.921057Z` `verification` / `verified`: Independent closure passed all four S4 criteria from 66 fresh CUDA repetitions, 69 rehashed private artifacts, 33 focused tests including closure mutations, 768 Python tests with real PostgreSQL, 144 lifecycle/host tests, 59 Control Panel tests, a production frontend build, S0-S3 validators, and current-revision batch-one smoke. The shared queue worker was stopped fail closed after its separate cutover gate identified 38 historical queued entities without durable queue rows.

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
