# Bounded smoke measurement contract (CT-M04)

This is a containerized client lane, not a new production admission authority. Existing
R7 failures and Windows Job/ETW/WSL/host-exit gates are unchanged. No inference from this
lane may close r7s5, r8, or S8-V4. The running target's image/revision is recorded separately
from the checkout revision and read-only client source hashes.

## Reused contracts and limits

- `apps/api/control_panel_workloads.py`: capacity-probe catalog and prediction routes.
- `src/evm/control_panel/scenario_workloads.py`: request, response, catalog, runtime and
  timing contracts. Freeze the actual service OpenAPI document and validate these schemas;
  additionally reject nonfinite features and identity/trace mismatches.
- `src/evm/scale_validation/s3_runtime.py`: request/trace linkage, separate client latency
  and server queue/stage timings, nearest-rank percentiles, and full accounting.
- `configs/s3_higgs_capacity.toml` and `configs/s3_capacity_runtime.toml`: zero-error smoke
  within the existing error-rate ceiling 0.01, p99 response <=250 ms, generator lag <=100 ms,
  host CPU <=90%. These limits are frozen before requests, never reduced after results.
- `S0RuntimeConfig` in `src/evm/scale_validation/s0_runtime.py`: low-load control bounds
  adapted to three requests, one concurrent user, a 60-second ceiling, and 20-second pacing.
  The S0 suite itself is NOT invoked: it also performs out-of-scope lifecycle operations.

Use Locust's standard single-user runner and `constant_pacing`, not a new arrival scheduler.
This is CLOSED CONCURRENCY. A three-request paced smoke is neither an open-arrival experiment
nor a statistically meaningful capacity/SLA estimate. Report p50/p95/p99 descriptively, the
planned/actual counts, actual issuance rate, elapsed interval, pacing lag and resource samples.
Before resuming full experiments, compare the old and new generators under a common governed
baseline load. Retain raw outputs in separate result series.

## Completion and uncertainty

The selected capacity-probe API is synchronous, read-only model inference. A validated
completed response is its terminal outcome; 200 alone is insufficient. Require the frozen
model/dataset/family identity, response schema, finite values, configured worker identity,
and request/response trace identity. Preserve the response and client monotonic start/end.
HTTP response latency ends when bytes arrive; end-to-end verification latency additionally
includes local contract validation. Both use the same generator monotonic clock. There is no
separate asynchronous job-completion latency for this synchronous route.
Never subtract timestamps from different hosts to construct latency.

There is no job ID, idempotency key, cancellation API or durable mutation-effect row in this
route's contract. Mark them NOT_APPLICABLE_FOR_THIS_ROUTE, not PASS. Async job terminal-state,
reconciliation, duplicate effect, admission/rollback/row-digest and lease tests remain NOT_RUN
in their original lanes. Do not invent IDs or claim those obligations have been replaced.
A transport timeout leaves acceptance unknown and fails the smoke without implicit retry.
Unissued, missing, invalid or uncertain results cannot become success or governed skips.

## Observability and resources

Every logical request has an exact run/request ID and W3C trace; record server queue/admission/
compute timings and replica/worker identity. Preserve trace evidence and pre/post metric
snapshots outside the container. The parent captures Docker resource samples and physical
Windows host CPU. The client records its cgroup resource counters; missing required observation
is reported as a blocker, not zero. Same-physical-node resource contention limits performance
claims. No Docker socket, host PID namespace, privileged mode or GPU is granted to the client.

Tool fixtures run with `--network none` and only an in-container loopback fake service.
Fixture success/failure/timeout/termination results are TOOL_ONLY and never actual API credit.

## Deferred open-arrival lane

Do not reinterpret Locust pacing or user spawning as fixed arrival rate. The existing S3
open-arrival implementation must first be evaluated for portability, source identity and
generator-overload accounting. If unsuitable, choose a separate pinned public-tool image
(for example k6's arrival-rate executor) in a later micro-task. Do not build a generic Python
load scheduler to force an all-Python solution.

References: [Locust library runner](https://docs.locust.io/en/stable/use-as-lib.html),
[Locust wait-time semantics](https://docs.locust.io/en/stable/writing-a-locustfile.html#wait-time-attribute).
