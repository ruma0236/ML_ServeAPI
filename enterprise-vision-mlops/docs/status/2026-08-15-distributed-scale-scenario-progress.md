# Distributed Scale Scenario Progress

- Schema: `evm.scale_validation.progress.v1`
- Generated: `2026-08-14T20:09:42Z`
- Authoritative plan: `docs/agenda/2026-08-15-distributed-scale-operational-validation-plan-v3.md`
- Claim boundary: This ledger reports local development evidence only. Planned or implementing work is not benchmark, availability, scale, or production proof.

Only a scenario with passed acceptance criteria and hashed evidence may be `verified`.

## S0: Runtime Baseline & Evidence Contract

- Status: `implementing`
- Engineering question: Can every load result start from a healthy, reproducible runtime whose identity, metrics, and trace propagation are machine-verifiable?
- Why now: No scale result is credible without a stable and attributable control case.
- Observed gap: Degraded readiness could return HTTP 200, and no strict benchmark closure contract requires identity, cross-layer traces, repeated metrics, and hashed evidence.
- Architecture before: Health, metrics, and logs exist without one machine-enforced evidence closure.
- Architecture after: Readiness, bounded telemetry, distributed trace identity, and benchmark closure gate every later scenario.
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario until separately exercised.
- Next action: Complete S0 contracts and telemetry gap audit before any load execution.

### Proposed Design

- Return HTTP 503 whenever serving dependencies or the promoted model are not ready.
- Propagate W3C trace context through API, queue, worker, data, tracking, and serving.
- Keep metric labels bounded while exact identities remain in traces and structured logs.
- Capture three low-load controls with latency, throughput, resource, pool, and retry data.

### Acceptance

- `S0-AC-01` [pending]: Only healthy active targets are included in the baseline.
- `S0-AC-02` [pending]: Exact source, data, model, and runtime identity is complete.
- `S0-AC-03` [pending]: p50, p95, p99, throughput, queue, pool, retry, CPU, RAM, GPU, and VRAM are queryable.
- `S0-AC-04` [pending]: Trace propagation spans every declared lifecycle stage with zero missing links.
- `S0-AC-05` [pending]: Three independent controls are comparable and variance is reported.

### Current Evidence

- No accepted execution evidence yet.

## S1: Transactional Job State & Idempotency

- Status: `planned`
- Engineering question: Can 100 to 500 concurrent mutations commit one legal and idempotent outcome?
- Why now: Durable state correctness must precede retries and queued high-volume mutation.
- Observed gap: Transactional ownership, atomic claims, fencing, and bounded connection-pool wait are not yet proven under concurrency.
- Architecture before: Control-plane state still relies partly on process-local or file-ledger ownership.
- Architecture after: Transactions, idempotency, leases, and fencing own every concurrent transition.
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario until separately exercised.
- Next action: Begin after S0 evidence contracts pass their focused tests.

### Proposed Design

- Use database transactions and unique idempotency keys for every state mutation.
- Enforce legal transitions, atomic lease claims, fencing, and stale-owner reconciliation.
- Bound connection-pool size and wait time and expose conflict outcomes as telemetry.

### Acceptance

- `S1-AC-01` [pending]: Duplicate lifecycle, deployment, and artifact effects are zero.
- `S1-AC-02` [pending]: Conflicting mutations end in one legal terminal state.
- `S1-AC-03` [pending]: Pool exhaustion returns a bounded observable failure rather than hanging.
- `S1-AC-04` [pending]: Worker loss and retry preserve one committed outcome.

### Current Evidence

- No accepted execution evidence yet.

## S2: Bounded Queue & Backpressure

- Status: `planned`
- Engineering question: Does overload stay memory-bounded while accepted work reaches an explicit outcome?
- Why now: A bounded queue is required before capacity and recovery experiments scale up.
- Observed gap: Durable admission, byte/age bounds, retry budget, and DLQ are incomplete.
- Architecture before: Admission and retries do not share one end-to-end resource boundary.
- Architecture after: Durable queue, local semaphore, rejection, retry, DLQ, and CPU scale are bounded.
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario until separately exercised.
- Next action: Begin after S1 has protected mutation idempotency.

### Proposed Design

- Combine a durable queue with a bounded process-local async queue and semaphore.
- Bound depth, bytes, age, and time; reject excess work with Retry-After.
- Retry only transient failures with backoff, jitter, and a global budget; isolate poison work.
- Scale CPU workers from queue telemetry while keeping the GPU worker count at one.

### Acceptance

- `S2-AC-01` [pending]: Queue depth, in-flight bytes, and process memory remain bounded under overload.
- `S2-AC-02` [pending]: Accepted work completes or reaches one explicit terminal failure.
- `S2-AC-03` [pending]: Duplicate effects are zero and poison work does not block healthy work.
- `S2-AC-04` [pending]: Over-capacity demand is rejected with an observable retry contract.

### Current Evidence

- No accepted execution evidence yet.

## S3: HIGGS Lightweight Capacity Envelope

- Status: `planned`
- Engineering question: Where are sustainable capacity, tail-latency limits, and the first CPU bottleneck?
- Why now: Lightweight probes separate infrastructure overhead from model compute.
- Observed gap: No repeated CPU-model capacity envelope or saturation knee exists.
- Architecture before: API behavior is functional but not characterized with low-compute probes.
- Architecture after: A measured CPU/API capacity envelope supplies operational and queue limits.
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario until separately exercised.
- Next action: Begin after S0, S1, and provisional S2 safety gates are in place.

### Proposed Design

- Use one governed high-volume tabular corpus across multiple lightweight CPU probes.
- Run closed concurrency and open arrival-rate sweeps with fixed corpus, split, and seed.
- Compare API replicas and CPU worker counts while tracing validation and prediction stages.
- Measure co-located load-generator consumption separately from the system under test.

### Acceptance

- `S3-AC-01` [pending]: Every probe has p95, p99, throughput, error, and resource curves.
- `S3-AC-02` [pending]: The first bottleneck is explained by trace and resource telemetry.
- `S3-AC-03` [pending]: The sustainable operating point is explicitly lower than peak throughput when required.
- `S3-AC-04` [pending]: Three independent repetitions and their variance are retained.

### Current Evidence

- No accepted execution evidence yet.

## S4: HIGGS Tiny MLP GPU Batching

- Status: `planned`
- Engineering question: Which small-model batch and queue-delay settings maximize throughput within p99 and VRAM?
- Why now: A lightweight GPU probe reveals scheduler and batching cost without a large model.
- Observed gap: No throughput-latency-VRAM Pareto curve exists for the accelerator path.
- Architecture before: The accelerator path runs models but lacks a controlled batching envelope.
- Architecture after: One measured batch, delay, instance, and VRAM operating point governs inference.
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario until separately exercised.
- Next action: Begin after the common baseline and bounded queue instrumentation are ready.

### Proposed Design

- Sweep batches 1, 4, 8, 16, and 32 with bounded queue delays.
- Keep one model instance by default and vary instance count separately from batch size.
- Record allocated, reserved, and peak VRAM, utilization, and formed batch size.
- Run training under a separate exclusive lease, never during inference benchmarking.

### Acceptance

- `S4-AC-01` [pending]: Throughput-p99 and throughput-peak-VRAM Pareto curves exist.
- `S4-AC-02` [pending]: The selected operating point has zero accelerator out-of-memory failures.
- `S4-AC-03` [pending]: Model instance count and batch size effects are measured separately.
- `S4-AC-04` [pending]: Queue limits are recalculated from the selected service rate.

### Current Evidence

- No accepted execution evidence yet.

## S5: Criteo Spark Memory-bounded Data Scale

- Status: `planned`
- Engineering question: Can larger partitioned data remain memory-bounded, deterministic, and restartable?
- Why now: Single-process data preparation does not demonstrate executor or shuffle behavior.
- Observed gap: Partition sizing, spill, skew, retry, and idempotent distributed commits are unproven.
- Architecture before: Data processing is reproducible at local scale but remains mostly process-local.
- Architecture after: Spark executors process governed partitions with bounded memory and deterministic commits.
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario until separately exercised.
- Next action: Begin after S1 and S2 protect ownership, retry, and output idempotency.

### Proposed Design

- Compare single-process columnar processing, local Spark, and Kubernetes executors.
- Increase a governed tabular subset through staged sizes with fixed manifests.
- Tune executor memory, partitions, shuffle, and adaptive execution under explicit bounds.
- Inject skew and one executor loss, then verify idempotent output commit and digest closure.

### Acceptance

- `S5-AC-01` [pending]: Records per second, storage rate, peak executor memory, GC, shuffle, spill, and skew are reported.
- `S5-AC-02` [pending]: Output contains zero missing and zero duplicate records.
- `S5-AC-03` [pending]: Retry preserves row count and output digest.
- `S5-AC-04` [pending]: Generated load volume is not represented as new semantic diversity.

### Current Evidence

- No accepted execution evidence yet.

## S6: API Rolling Continuity & GPU Controlled Handoff

- Status: `planned`
- Engineering question: Can API replicas roll continuously while a single-GPU handoff remains measured and reversible?
- Why now: API continuity and GPU availability are different failure domains and claims.
- Observed gap: Rolling API drain and controlled single-GPU switch are not proven under load.
- Architecture before: Deployment works but API continuity and single-GPU interruption are conflated.
- Architecture after: Stateless API continuity and controlled GPU handoff have separate evidence and claims.
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario until separately exercised.
- Next action: Begin after S3 capacity, S4 GPU bounds, and S2 queue recalibration pass.

### Proposed Design

- Run two or three stateless API replicas with zero-unavailable rolling replacement.
- Use readiness, pre-stop drain, termination grace, and target-scoped Pod termination.
- Gate GPU candidates by shadow quality, identity, and tail latency before queue drain and switch.
- Measure endpoint interruption and exact known-good rollback identity separately.

### Acceptance

- `S6-AC-01` [pending]: API rolling update loses and duplicates zero accepted requests.
- `S6-AC-02` [pending]: API drain and replacement recovery time are measured.
- `S6-AC-03` [pending]: GPU handoff interruption and rollback identity are measured.
- `S6-AC-04` [pending]: The result is not described as zero-downtime GPU high availability.

### Current Evidence

- No accepted execution evidence yet.

## S7: Image/VLM/LLM Auxiliary Admission

- Status: `planned`
- Engineering question: Do image and generative workloads use model-family-specific cost admission and metrics?
- Why now: Tabular capacity does not cover image decode, pixels, tokens, or long requests.
- Observed gap: Image, token, and in-flight cost bounds are not uniformly enforced or measured.
- Architecture before: Multiple model families run without one cost-aware admission proof.
- Architecture after: Image, pixel, token, and in-flight budgets govern family-specific queues and metrics.
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario until separately exercised.
- Next action: Begin after S2 bounds and S4 accelerator operating point are verified.

### Proposed Design

- Measure image size, decode, preprocessing, batch, and pixel admission costs.
- Bound input tokens, output tokens, and total in-flight tokens for language workloads.
- Measure first-token, per-token, queue, fairness, and peak-memory behavior.
- Run model families sequentially on one GPU and use quantization only with runtime proof.

### Acceptance

- `S7-AC-01` [pending]: Each model family has distinct p95, p99, and quality metric schemas.
- `S7-AC-02` [pending]: Selected admission limits have zero OOM and zero starvation.
- `S7-AC-03` [pending]: Long-request head-of-line behavior and fairness are measured.
- `S7-AC-04` [pending]: Unsupported or unverified metrics remain absent.

### Current Evidence

- No accepted execution evidence yet.

## S8: Dependency Soak & Resource-efficiency Closure

- Status: `planned`
- Engineering question: Do bounded retries and selected operating points remain stable during faults and soak?
- Why now: Closure requires long-run resource trends and dependency recovery after isolated passes.
- Observed gap: Retry amplification, resource slope, efficiency, and final re-hash are unproven.
- Architecture before: Isolated guards exist without a distributed-scale soak and efficiency closure.
- Architecture after: Dependency faults, sustained load, cleanup, efficiency, and hashes close one ledger.
- Claim boundary: No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed from this scenario until separately exercised.
- Next action: Begin only after S0 through S7 have accepted evidence and clean cleanup.

### Proposed Design

- Inject service-scoped latency or timeout with bounded proxy or deterministic fixtures.
- Enforce retry budget, exponential backoff, jitter, circuit hold, and queue drain.
- Run a 30 to 60 minute soak near the selected safe operating point.
- Calculate CPU/GPU time efficiency and re-hash every accepted evidence artifact.

### Acceptance

- `S8-AC-01` [pending]: Retry amplification stays within the declared budget.
- `S8-AC-02` [pending]: Memory, file descriptor, pool, queue, and artifact slopes remain bounded.
- `S8-AC-03` [pending]: MTTR, request impact, efficiency Pareto, and residual risk are recorded.
- `S8-AC-04` [pending]: One final evidence index re-hashes every accepted result.

### Current Evidence

- No accepted execution evidence yet.
