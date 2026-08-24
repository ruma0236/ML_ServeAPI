# Distributed Scale Operational Validation Plan V4

Date: 2026-08-24

Status: `contract_frozen` for the umbrella and E0; later work items remain
`planned` until their dependencies and exact runtime identities are closed.

V3 prerequisite closure:
`80a56e501cf46359a8de908fc39dc3c02a642fc1`

## Purpose

V4 strengthens the existing ML Serve system in place. It does not create a
parallel demo service. The execution order is fixed:

```text
E0 environment and profiler baseline
  -> S6B-M Triton model Blue/Green
  -> X1 heterogeneous-model concurrency
  -> integrated S8-V4 fault and soak closure
```

The V3 `35 RPS` soak load is historical and must not be reused automatically.
Integrated V4 load is frozen only after X1 non-credit calibration measures each
model's GPU-ms and service capacity.

## State And Credit Contract

Allowed states are:

```text
planned -> contract_frozen -> ready -> running -> review_pending -> verified
                                      \-> remediation_required -> new attempt_id
```

Every failed, interrupted, wrong-revision, incomplete-evidence, or
cleanup-failed attempt is permanently `zero_credit`. Calibration is
`non_credit`. Only an acceptance attempt can be `credit`.

An item can become `verified` only when all of these are independently backed
by evidence: definition alignment, experiment-purpose alignment,
validation-purpose alignment, test-purpose alignment, all acceptance criteria,
required repetitions, evidence hashes, provenance, regression, cleanup and
resource baseline, claim boundary, zero unresolved blockers, and reviewer
sign-off.

## Global Identity And Evidence Contract

Every attempt records:

- attempt and correlation identity;
- source Git revision and tree, container image digest, model/data/config
  digests, Triton repository/version identity, GPU UUID, and runtime versions;
- frozen workload, seed, warmup, measurement, cooldown, arrival/concurrency,
  queue, timeout, retry, and stop conditions;
- request, trace, model route, lease/fence, Pod/process, and external effect
  identity;
- raw metrics, traces, logs, profiler output, failure assertions, RCA, recovery,
  and cleanup;
- public-safe evidence URI and SHA-256 plus private artifact inventory SHA-256;
- `credit`, `zero_credit`, or `non_credit`.

The machine ledger is append-only. Existing events are never edited or deleted.
Corrections use an amendment event whose `previous_event_hash` points to the
prior event. Public JSON and JSONL use canonical LF bytes.

## E0: Triton Environment And Profiler Baseline

Tracking: `EVM-299` under the V4 umbrella.

State at plan freeze: `contract_frozen`; runtime not started.

### Definition

Establish one reproducible WSL2/Docker/CUDA/Triton runtime and prove exact image,
GPU, model, inference, metrics, profiler, and cleanup identity before model
lifecycle work begins.

### Engineering Question

Can the current host start and stop the exact Triton GPU runtime three times
without identity drift, silent CPU fallback, stale model state, telemetry gaps,
or resource residue?

### Experiment Purpose

Measure environment reproducibility and establish a trusted baseline for all
later V4 comparisons.

### Validation Purpose

Fail closed on wrong image/model/config digest, wrong GPU UUID, missing CUDA,
unqueryable metrics, profiler unavailability, or incomplete cleanup.

### Test Purpose

Protect identity parsing, digest comparison, readiness, metrics projection,
profiler evidence, and cleanup invariants with positive and mutation tests.

### Dependencies

- V3 S0-S8 verified at the canonical closure;
- B0 known-good serving identity and actual CUDA inference healthy;
- queue, lease, outcome-unknown, and temporary V4 resources at zero;
- exact WSL2, Docker, NVIDIA driver, CUDA, framework, Triton image, model
  repository, and GPU UUID captured before mutation.

### Frozen Procedure And Thresholds

- Three independent start -> ready -> batch-one inference -> metrics -> profiler
  probe -> stop -> cleanup repetitions.
- Expected Triton image digest, model repository digest, model artifact digest,
  config digest, and GPU UUID must match in all three repetitions.
- Batch-one known-good prediction must complete `3/3` with CUDA active and no
  transport, model, or identity error.
- Triton readiness and Prometheus target must become healthy within `30 s`.
- At least one supported Nsight Systems or CUPTI capture path must produce a
  parseable GPU activity timeline. This does not itself prove overlap.
- Within `120 s` after stop, V4 Pod/container/process/model-repository leases are
  zero and used VRAM is within `max(256 MiB, 5% of total VRAM)` of the preflight
  baseline.

### Acceptance Criteria

- `E0-AC-01`: environment, image, model, config, and GPU identities match `3/3`.
- `E0-AC-02`: actual Triton CUDA batch-one inference and readiness pass `3/3`.
- `E0-AC-03`: Triton/Prometheus metrics and a parseable profiler timeline are
  present and tied to the attempt identity.
- `E0-AC-04`: cleanup and post-baseline restoration pass `3/3` with no orphan.

### Stop And Recovery

Stop on identity ambiguity, CPU fallback, GPU owner conflict, unexpected OOM,
probe failure, metrics gap, or cleanup residue. Restore B0, release exact leases,
remove only allowlisted V4 resources, retain zero-credit evidence, and open a
new attempt after remediation.

### Required Evidence

Environment manifest, image/model/config digests, GPU inventory, `/ready` and
inference payloads, Prometheus queries, profiler metadata and timeline summary,
process/Pod/container inventory, VRAM baseline/delta, cleanup proof, regression
logs, and independent validation result.

### Claim Boundary

One Windows/WSL2 physical node and one RTX 4080. E0 is not production readiness,
HA, DR, multi-GPU, MIG, MPS, or CUDA overlap evidence.

## S6B-M: Triton Model Blue/Green

Tracking: `EVM-300` under the V4 umbrella.

State at plan freeze: `planned`; it cannot enter `contract_frozen` until E0 is
verified and exact Blue/Green artifacts are selected.

### Definition

Use one Triton GPU Pod with two governed model versions to exercise warmup,
canary, route switch, in-flight drain, old-version unload, and exact rollback.

### Engineering Question

Can model-version changes be controlled independently from API Pod rollout,
with exact digest identity, no lost or duplicate accepted request, no illegal GPU
owner overlap, and deterministic rollback?

### Experiment, Validation, And Test Purpose

The experiment measures model lifecycle interruption and request outcomes. The
validator recomputes route, model, lease, request, trace, and monotonic timelines
from raw evidence. Tests reject digest mismatch, approval reuse, premature
drain, wrong route target, stale lease/fence, duplicate effect, and rollback
identity mismatch.

### Dependencies

E0 verified; one exact Triton Pod/image/GPU identity; Blue known-good and Green
candidate model/config/artifact digests; rollback target; S1 idempotency and S2
bounded admission active; maintenance impact recorded.

### Frozen Procedure And Thresholds

- Three independent baseline controls.
- Three independent successful warmup -> `10%` canary -> switch -> drain ->
  unload -> exact rollback repetitions, with at least `1,000` logical requests
  per repetition.
- Three independent wrong-digest attempts that must create zero route switch and
  zero accepted Green effect. These are acceptance fault probes, not successful
  release repetitions.
- Accepted logical requests must have one terminal result, lost `0`, duplicate
  external effect `0`, wrong-version result `0`, and complete trace identity.
- Exactly one externally active route target is allowed. Blue and Green may be
  resident during warmup/canary, but route weights and GPU ownership must match
  the frozen phase state.
- Drain reaches in-flight `0` before unload. Interruption is measured and
  reported; zero interruption is not a required or implied claim.

### Acceptance Criteria

- `S6B-M-AC-01`: exact warmup/canary/switch/drain/unload/rollback identities and
  phase order pass `3/3`.
- `S6B-M-AC-02`: accepted loss, duplicates, wrong-version responses, and illegal
  GPU owner overlap are all zero.
- `S6B-M-AC-03`: wrong digest fails closed in `3/3` with route and serving source
  unchanged.
- `S6B-M-AC-04`: exact Blue rollback, B0/source service health, metrics, queue,
  lease, model unload, and cleanup pass `3/3`.

### Claim Boundary

This is controlled model Blue/Green inside one Triton GPU Pod. It is not
multi-replica GPU HA, zero-downtime production serving, or node failover.

## X1: Heterogeneous-model Concurrency

Tracking: `EVM-301` under the V4 umbrella.

State at plan freeze: `planned`; exact artifacts and per-model batch candidates
are frozen by append-only amendment only after S6B-M is verified.

### Definition

Run four governed lightweight model archetypes through the existing Workloads
API and one Triton GPU Pod: tabular Tiny MLP, image classifier, compact VLM, and
compact 4-bit LLM. Compare solo, serial, concurrent, and per-model batching while
varying API replicas `1/2` and CPU workers `1/2/4`.

### Engineering Question

What capacity, latency, fairness, VRAM, and scheduling boundaries appear when
four heterogeneous models share one physical GPU, and is any apparent
concurrency actual CUDA kernel overlap or only request/process overlap?

### Experiment Purpose

Measure per-model GPU-ms, solo capacity, mixed balanced and hot-load capacity,
per-model batching effects, first saturation knee, fairness, HOL behavior, and
resource efficiency.

### Validation Purpose

Recompute per-model arrival, admission, service, terminal, queue, latency,
quality, VRAM, and trace identity. A CUDA parallelism claim is enabled only when
Nsight/CUPTI shows nonzero overlapping GPU kernel intervals tied to distinct
model/request identities.

### Test Purpose

Reject stale model/version identity, illegal queue bypass, budget overflow,
starvation, wrong-family metrics, silent fallback, owner conflict, profiler
misattribution, OOM, and incomplete unload/cleanup.

### Non-credit Calibration And Load Freeze

- Per-model solo calibration: `4 models x 3 repetitions` with fixed warmup,
  measurement, cooldown, seed, and arrival controls.
- For model `i`, record mean GPU service demand `g_i` in GPU-seconds/request and
  safe solo service rate `mu_i`.
- Balanced mix assigns equal GPU-time shares. Hot mix assigns `70%` GPU time to
  one selected model and `10%` to each other model.
- Candidate total load is the minimum of `70%` measured GPU-time capacity,
  `70%` API capacity, and `70%` CPU-worker capacity. Per-model RPS is derived
  from the frozen share and `g_i`.
- Resulting RPS values are appended before `ready`; V3 `35 RPS` is not an input.

### Planned Credit Matrix

- Serial baseline: API replicas `1/2` x CPU workers `1/2/4` x `3` repetitions.
- Concurrent balanced mix: `1/2 x 1/2/4 x 3` repetitions.
- Concurrent hot mix: `1/2 x 1/2/4 x 3` repetitions.
- Per-model batching: four models x two frozen batch candidates x `3`
  repetitions.
- Every point uses fixed warmup, measurement, cooldown, request identity, and
  cleanup. Failed points receive zero credit and do not mix with passing counts.

### Acceptance Criteria

- `X1-AC-01`: all points record offered/service RPS, p50/p95/p99, errors,
  queue/batch wait, formed batch distribution, CPU/RSS, GPU utilization, and
  allocated/reserved/peak VRAM by exact model identity.
- `X1-AC-02`: accepted work has one terminal outcome; unexpected OOM, silent
  fallback, illegal owner overlap, lost work, and duplicate effect are zero.
- `X1-AC-03`: balanced mix has selected/admitted starvation zero and Jain
  service-attainment fairness `>= 0.90`; hot mix preserves non-hot terminal
  progress with bounded queue/deadline outcomes.
- `X1-AC-04`: solo, serial, concurrent, and batch effects are separated; API
  replica and CPU-worker effects are reported without claiming scale-out when
  throughput does not improve.
- `X1-AC-05`: CUDA kernel overlap is claimed only if the profiler reports a
  nonzero overlap interval for distinct model/request identities. Otherwise the
  verdict explicitly says `kernel_overlap_not_evidenced`.
- `X1-AC-06`: exact source service, route, model repository, queue, lease, GPU
  memory, Prometheus, process, Pod, and container baseline is restored.

### Claim Boundary

Four lightweight governed models on one RTX 4080. No MIG, MPS, multi-GPU,
multi-node, tenant isolation, or production capacity claim. Resident models do
not imply simultaneous kernel execution.

## Integrated S8-V4: Fault, Recovery, And Soak Closure

Tracking: `EVM-302` under the V4 umbrella.

State at plan freeze: `planned`; it cannot be frozen until X1 selects the
balanced and hot operating points.

### Definition

Combine bounded admission, exact identity, Triton Blue/Green, mixed-model
fairness, retry/timeout/DLQ, and deterministic cleanup across API Pod, worker,
Triton process, model, dependency, and telemetry faults, then run a resource
soak at X1-derived load.

### Fault And Soak Matrix

- API Pod termination, queue-worker termination, Triton process restart, model
  load/digest rejection, downstream dependency latency/timeout, and telemetry
  interruption: `6 fault classes x 3 independent repetitions`.
- Balanced mix soak: three independent `30 minute` measurement windows after a
  fixed warmup and before a fixed cooldown.
- A short hot-mix validation precedes each soak and is acceptance calibration
  only after the X1 load values are frozen; any recalibration creates a new
  non-credit attempt and amendment.

### Acceptance Criteria

- `V4-AC-01`: all fault repetitions preserve one terminal outcome per accepted
  identity, lost work `0`, duplicate logical effect `0`, bounded retry/DLQ, and
  exact recovery ownership.
- `V4-AC-02`: Blue/Green route/model identity and mixed-family fairness remain
  derivable during faults; ambiguous identity or illegal GPU ownership is zero.
- `V4-AC-03`: every accepted soak stays within frozen latency/error/queue/VRAM
  guardrails and has non-positive unbounded RSS, FD, queue, pool, artifact, and
  VRAM leak verdicts.
- `V4-AC-04`: MTTR, request impact, retry amplification, DLQ/expiry, throughput
  per CPU/GPU resource, and profiler result are reported from raw evidence.
- `V4-AC-05`: current-revision focused, real-PostgreSQL, lifecycle/host, full
  Python, Control Panel, frontend, Kubernetes, Prometheus, CUDA, evidence, and
  mutation validations pass.
- `V4-AC-06`: cleanup returns routes, models, B0/source serving, queue, lease,
  outcome-unknown, GPU memory/process, Pods/containers, file-SD targets, and
  temporary evidence resources to the declared baseline.

### Claim Boundary

Integrated controlled traffic on one Windows/WSL2 physical node and one RTX
4080. Customer production SLA, multi-node/zone HA/DR, multi-GPU/MIG/MPS,
autoscaling, tenant security isolation, privacy/compliance, FinOps, end-to-end
training/retraining, and long-term drift/quality remain unverified backlog.

## Review Boundary

E0, S6B-M, X1, and integrated V4 must first enter `review_pending`. The review
package must include the frozen contract, commit, raw/private evidence path,
public calculations, failed attempts/RCA, cleanup, and remaining gaps. Only an
independent pass can append a `verified` event and unlock the next dependency.
