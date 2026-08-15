# S2 Bounded Queue Implementation Checkpoint

Status: `implementing`

This checkpoint strengthens the existing ML Serve task path in place. It is not
an S2 benchmark, overload pass, availability claim, or production proof.

## Existing Boundary

Before S2, `POST /control-panel/v1/tasks` wrote a task assignment to the
operations ledger. A caller dispatched a queued assignment explicitly. The
lifecycle worker was and remains a separate LifecycleRun consumer; it was never
the `/tasks` consumer.

## Implemented Delta

- PostgreSQL admission now reserves queue depth, aggregate canonical UTF-8
  payload bytes, task collection state, and the idempotency response in one
  transaction.
- An already accepted idempotency-key replay consumes no new capacity. A 429
  rejection does not persist the key.
- A single oversized item is rejected with 413. Aggregate depth or byte pressure
  is rejected with 429 and an integer `Retry-After`.
- Claims use `FOR UPDATE SKIP LOCKED`, owner identity, lease epoch, and expiry.
- The dedicated `task_queue_worker` has bounded local item and byte budgets,
  CPU hysteresis, exactly one GPU consumer, transient-only exponential retry
  with deterministic jitter, a durable global retry budget, and DLQ isolation.
- Each blocking dispatch runs in an exact child process. The worker terminates
  that child at the frozen work timeout, avoiding a cancelled Python thread that
  could commit a late external effect after the lease was retried.
- `EVM_TASK_ADMISSION_MODE=legacy` is the explicit rollback switch. The local
  Compose path enables durable admission and starts the dedicated worker;
  Prometheus scrapes its bounded-label metrics separately.

Frozen configuration: `configs/s2_bounded_queue_v1.toml`

Configuration digest: `eb34454413f7c14a67727e3a5608ec2fd454f0897420c6f1e0b3c9964318bfe8`

## Checkpoint Verification

- 39 focused queue, API, transaction, dispatch, contract, metrics, and fixture tests passed against
  a real PostgreSQL 16 control-plane service where required.
- One isolated external Uvicorn request was accepted with 202.
- A separate real queue-worker process claimed and dispatched the item.
- The durable queue closed with active `0` and completed `1`.
- The deterministic Airflow-compatible dependency recorded one unique external
  effect and zero duplicate effects.
- The isolated database schema and processes were removed after the proof.

The raw checkpoint result remains outside Git. No exact host, schema, process,
task, or path identity is published here.

## Pending Acceptance

`S2-AC-01` through `S2-AC-04` remain pending. The following are not yet run:

- three independent repetitions of baseline, depth burst, byte burst, sustained
  overload, duplicate, expired, transient-budget, poison-plus-healthy,
  timeout-plus-worker-restart, and GPU-bound profiles;
- queue/RSS hard-bound and slope analysis;
- accepted-equals-terminal and zero active/leased/local closure across every run;
- real worker restart recovery and GPU max-in-flight evidence;
- full regression, lifecycle E2E, S0/S1 regression, public evidence hashes, and
  canonical Git-blob rehash closure.

Claim boundary: one local physical node, controlled traffic, isolated database
schemas, and no customer traffic, production SLA, HA, or DR claim.
