# Distributed Scale And Operational Load Validation Plan V3

## Authority

- Status: planning only; implementation and load acceptance are not complete.
- Supersedes: `docs/agenda/2026-08-14-distributed-scale-operational-validation-plan.md`.
- Reviewed source: `output/pdf/distributed-scale-operational-validation-plan-ko-v3.pdf`.
- Reviewed source SHA-256:
  `85202d2c1401165901d1158bd7ddf7f0a1607a9044220871644c2d5b421a397a`.
- Review completion: 2026-08-15 04:34 KST.

The PDF review changed scenario order and workload selection, not only wording.
This Markdown is the versioned execution authority for Scenario S0 through S8.
The older plan remains historical context and must not drive new status updates.

## Scope Decisions

The representative systems probe is a high-volume public tabular corpus with a
small multilayer perceptron. Lightweight CPU estimators provide high-request-rate,
streaming, branch-heavy, and stateful batch probes. A partitioned public click-log
subset and Spark provide the data-scale extension. Image classification, VLM, and
LLM workloads remain auxiliary probes for decode, pixel, token, and accelerator
admission behavior.

- Distributed behavior is measured across API replicas, CPU workers, bounded
  queues, Spark executors, data partitions, and inference batching.
- GPU training requests may queue concurrently, but one exclusive training lease
  runs at a time.
- API rolling continuity and single-GPU controlled handoff are separate claims.
- OpenTelemetry context propagation precedes performance attribution.
- Organization-grade multi-tenancy, stateful HA/DR, physical-node HA,
  multi-GPU training, production SLA, customer traffic, and business A/B are out
  of scope.
- Basic secret hygiene, transactions, idempotency, leases, and bounded retries
  remain required even though full security and DR workstreams are excluded.

## Target Flow

```text
intake and manifest
  -> Spark or bounded CPU partitions
  -> admission and durable bounded queue
  -> CPU executors or one exclusive GPU lease
  -> train/evaluate and registry
  -> stateless API replicas or controlled GPU serving handoff
  -> metrics, traces, logs, and hashed evidence
```

Every accepted benchmark uses fixed source/data/model/runtime identity, warmup,
measurement window, seed, failed-assertion retention, cleanup proof, and at least
three independent repetitions where the scenario requires variance.

## Scenario S0: Runtime Baseline & Evidence Contract

Tracking: `EVM-290` / `SCRUM-200`.

Engineering question: can every later result start from a healthy runtime whose
identity, metrics, and cross-layer trace are machine-verifiable?

Design:

- distinguish active targets from historical failed or completed resources;
- return non-success HTTP readiness when dependencies or the promoted model are
  degraded;
- define machine-readable source/data/model/runtime identity and evidence
  manifests;
- expose latency histograms, queue, worker, CPU/RAM/GPU/VRAM, pool, and retry
  telemetry with bounded metric labels;
- propagate W3C trace context through API, queue, worker, Spark, tracking, and
  serving while retaining high-cardinality identity in traces and logs;
- run three low-load controls and report variance.

Acceptance:

- only healthy active targets are part of the baseline;
- identity and trace propagation have zero missing links;
- p50/p95/p99, throughput, resource, queue, pool, and retry metrics are queryable
  for the same run;
- three controls are comparable and variance is retained;
- no load scenario begins from an unhealthy or ambiguous baseline.

## Scenario S1: Transactional Job State & Idempotency

Tracking: `EVM-292` / `SCRUM-202`.

Engineering question: can 100 to 500 concurrent create, approve, cancel, and retry
mutations leave one legal and idempotent outcome?

Design:

- use PostgreSQL transactions, unique idempotency keys, and legal state
  transitions;
- use atomic claim, lease/fencing identity, and stale-owner reconciliation;
- bound connection-pool size and wait time and expose conflicts.

Acceptance:

- duplicate lifecycle, deployment, and artifact effects are zero;
- conflicting mutations terminate in one legal state;
- pool exhaustion becomes a bounded observable failure rather than a hang;
- worker loss and retry retain one committed outcome.

## Scenario S2: Bounded Queue & Backpressure

Tracking: `EVM-293` / `SCRUM-203`.

Engineering question: under sustained and burst overload, do queue memory and
process memory remain bounded while accepted work closes explicitly?

Design:

- combine a durable queue with bounded process-local async queues and semaphores;
- bound depth, bytes, age, and timeout and return `429` plus `Retry-After` when
  demand exceeds admission;
- retry transient failures only with exponential backoff, jitter, and a global
  retry budget;
- quarantine poison work in a DLQ;
- scale CPU workers from queue telemetry while keeping one GPU worker.

Acceptance:

- queue, in-flight bytes, and RSS remain bounded;
- accepted work completes or reaches one explicit terminal failure;
- duplicate effects are zero and poison work does not block healthy work;
- rejection, wait, retry, and DLQ behavior are measured.

## Scenario S3: HIGGS Lightweight Capacity Envelope

Tracking: `EVM-291` / `SCRUM-201`.

Engineering question: where are sustainable capacity, p95/p99 limits, and the
first CPU/API saturation knee when model compute is intentionally small?

Design:

- use one governed public high-volume tabular corpus and fixed split/seed across
  logistic, probabilistic, online linear, branch-heavy, and incremental probes;
- run closed-concurrency and open-arrival-rate sweeps;
- compare API replica counts and CPU worker counts;
- separate validation, transform, queue wait, prediction, and load-generator cost
  with traces and resource telemetry.

Acceptance:

- every probe has throughput, p95/p99, error, and resource curves;
- telemetry identifies the first bottleneck;
- a sustainable operating point is selected below peak throughput when required;
- S2 queue capacity is recalculated from the measured service rate.

## Scenario S4: HIGGS Tiny MLP GPU Batching

Tracking: `EVM-294` / `SCRUM-204`.

Engineering question: which batch and queue-delay settings maximize throughput
without violating p99 and accelerator memory limits?

Design:

- sweep batches `1, 4, 8, 16, 32` and bounded delays `0, 2, 5, 10 ms`;
- vary model instance count separately from batch size;
- record allocated, reserved, and peak VRAM, utilization, and formed batch size;
- keep training in a separate exclusive-lease window.

Acceptance:

- throughput-p99 and throughput-peak-VRAM Pareto curves exist;
- the selected point has zero GPU out-of-memory failures;
- instance and batch effects are distinguishable;
- queue bounds are recalculated from the selected service rate.

## Scenario S5: Criteo Spark Memory-bounded Data Scale

Tracking: `EVM-296` / `SCRUM-206`.

Engineering question: can progressively larger partitioned data remain
memory-bounded, deterministic, and restartable on one physical node?

Design:

- compare single-process columnar processing, Spark local execution, and one,
  two, and four Kubernetes executors;
- advance through staged public click-log subsets with fixed source, partition,
  and output manifests;
- tune executor memory, partition size, shuffle, spill, skew handling, and
  adaptive execution;
- stop one executor and validate stage retry plus idempotent output commit;
- keep the lightweight recommendation probe as one separate GPU job.

Acceptance:

- records/s, MiB/s, peak executor memory, GC, shuffle, spill, and skew are
  reported;
- missing and duplicate output records are zero;
- retry preserves row count and output digest;
- generated I/O volume is never claimed as new semantic diversity.

## Scenario S6: API Rolling Continuity & GPU Controlled Handoff

Tracking: `EVM-295` / `SCRUM-205`.

Engineering question: can stateless API replicas roll continuously while a
single-GPU model switch remains measured, interruptible, and reversible?

Design:

- run two or three API replicas with zero-unavailable rolling replacement;
- use readiness, pre-stop drain, termination grace, and target-scoped Pod
  termination;
- hold traffic near the verified operating point and measure accepted request
  loss, duplicates, and recovery;
- gate the GPU candidate with shadow quality, identity, and p99 before queue drain
  and controlled switch;
- restore exact known-good identity on rollback and record endpoint interruption.

Acceptance:

- API rolling loses and duplicates zero accepted requests;
- drain and API replacement time are measured;
- GPU interruption and rollback identity are measured separately;
- the result is not described as zero-downtime GPU HA.

## Scenario S7: Image/VLM/LLM Auxiliary Admission

Tracking: `EVM-297` / `SCRUM-207`.

Engineering question: do auxiliary model families use their actual image, pixel,
decode, token, and in-flight costs for admission and telemetry?

Design:

- measure image size, decode, preprocessing, and batch cost;
- bound VLM image pixels, decode work, and request size;
- bound LLM input tokens, output tokens, and total in-flight tokens;
- measure first-token and per-token latency, fairness, peak VRAM, and long-request
  head-of-line behavior;
- execute model families sequentially on one GPU and accept quantization only
  with exact runtime proof.

Acceptance:

- each family has distinct p95/p99 and quality metric schemas;
- selected admission limits have zero OOM and zero starvation;
- long-request fairness is measured;
- unsupported or unverified metrics remain absent.

## Scenario S8: Dependency Soak & Resource-efficiency Closure

Tracking: `EVM-298` / `SCRUM-208`.

Engineering question: do timeout, retry, and resource bounds remain stable during
dependency degradation and a sustained soak at the selected operating point?

Design:

- inject service-scoped bounded latency or timeout with a proxy or deterministic
  fixture;
- enforce timeout, retry budget, exponential backoff, jitter, circuit hold, and
  queue drain;
- run a 30 to 60 minute soak near 70% of measured sustainable capacity;
- measure memory, file descriptor, pool, and queue slopes;
- calculate CPU/GPU-seconds and throughput-per-resource efficiency;
- run cross-layer faults only after isolated passes and cleanup proof;
- re-hash every accepted evidence artifact.

Acceptance:

- retry amplification remains within budget;
- unbounded resource slopes are zero;
- MTTR, request impact, efficiency Pareto, residual risk, and cleanup are recorded;
- one final evidence index closes every accepted result by hash.

## Execution Order

```text
S0 evidence and trace baseline
  -> S1 transactional state
  -> S2 bounded queue safety
  -> S3 lightweight capacity and S2 recalibration
  -> S4 GPU batching and S2 recalibration
  -> S5 Spark data scale
  -> S6 API rolling and GPU handoff
  -> S7 auxiliary family admission
  -> S8 isolated dependency faults, soak, cleanup, and hash closure
```

S1 and S2 precede high-volume requests because retries are unsafe without
idempotent ownership and bounded admission. S3 establishes measured CPU/API
service rate. S4 changes accelerator service rate. S5 uses the same durability
and retry rules for distributed output. S6 requires verified capacity and queue
bounds. S8 remains last.

## Evidence And Claims

Large raw evidence stays outside Git. Git contains schemas, progress ledgers,
artifact indexes, and generalized summaries. Every accepted result retains entry
identity, raw samples, aggregate statistics, configuration, failed assertions,
cleanup, timestamps, artifact hashes, and claim boundary.

After successful execution, allowed claims are limited to measured single-node
capacity, bounded queue and worker concurrency, transactional state, one-GPU
batching trade-offs, Pod/process continuity, controlled GPU handoff, Spark
executor behavior on one node, model-family admission, and distributed trace
attribution. Multi-node or multi-zone production HA, stateful HA/DR, multi-GPU
training, production SLA, customer traffic, business A/B, and full-terabyte data
processing remain prohibited unless separately exercised.
