# Scenario B Controlled Canary And Rollback Contract

Issue: `EVM-267 / SCRUM-173`  
Router dependency: `EVM-244 / SCRUM-144`  
Contract state: `PASS - non-disruptive closure complete`
Captured at: `2026-08-02T02:07:32Z`

Final closure:
`docs/status/2026-08-02-scenario-b-controlled-replay-closure.md`

## Decision

Scenario B will implement and prove an **isolated controlled replay** path. It
will use real VisA records, real CUDA inference, deterministic route assignment,
an immutable under-threshold challenger, bounded exposure, fail-closed metric
windows, zero-allocation containment, and exact restoration of the captured
stable route identity.

This contract does not authorize a Kubernetes production rollout. The local
runtime has one node, one GPU and one production replica. The B7 staging
Deployments are intentionally `0/0`, and Scenario D has not closed. A live
production canary therefore remains blocked until supervision, dual-model GPU
admission, exact-target preflight and a separate maintenance approval pass.

Scenario A evidence is recorded only as `baseline_reference`. Its recovery
times and live-proof closure cannot satisfy any Scenario B acceptance item.

## Immutable Identities

### Stable rollback target

- target: `evm-production/Deployment/evm-b0-production`
- deployment UID at contract capture:
  `cfdab424-dcc5-4d5f-a46f-ae7530441ef4`
- candidate: `effnet-b0-img224-expedited-adamw`
- dataset: `visa-open-data-e35d93d5561f`
- model SHA-256:
  `abcb8504a36c1128d32021722cfedce6357fd73598a52f6c2a0d60aca9d9a27f`
- image digest:
  `sha256:227b483f466678e00fbf13fd6b3ad1059ca2c6771239d204494fb610fa7d9f7a`
- readiness baseline: CUDA available, model loaded, production Deployment
  `1/1 Ready`

The stable identity is the exact route rollback target. It is not silently
re-evaluated under a different release policy during this incident drill.

### Invalid challenger fixture

- candidate: `effnet-b7-img600-finetune-adamw`
- dataset: `visa-open-data-f1f1c9ee9922`
- model SHA-256:
  `3058c67eb8eda61a8504d32aa14003206768dca96efc7fb2ddcdb1e01791d6f4`
- candidate-summary SHA-256:
  `5b05f7e925d8772b14c397719bacda3e82e0703c643aa434cce09d015b0e5e77`
- MLflow run: `f16f8203371c4ea1b798d9097ddc9caf`
- measured F1: `0.6369426752`
- policy minimum F1: `0.75`
- deterministic blocker: `f1<0.75`

The model and its blocker predate Scenario B. The policy is not lowered or the
artifact modified to manufacture a successful drill.

### Replay data

- snapshot: `ct-visa-open-data-e35d93d5561f-test-c6b466afb907`
- immutable holdout records: `2,181`
- training overlap: `0`
- holdout manifest SHA-256:
  `7635a75aa7d5a5fd66b4dbb121203a809eceef637f8909c153899c53b4492566`
- minimum shadow sample: `500` paired requests
- minimum bounded replay sample: `1,000` total requests
- challenger allocation: exactly `100` requests and never greater than `10%`

The challenger was trained on a different VisA dataset version. Scenario B
must record both identities and evaluate against the immutable replay manifest;
it must not claim same-training-lineage equivalence.

## Terminology And Mode Boundary

- **Shadow:** stable output remains authoritative; the same replay request is
  copied to the challenger and challenger output is discarded.
- **Isolated controlled canary:** an evidence-only router selects challenger
  output for at most 10% of the immutable replay manifest. It cannot affect the
  production endpoint or real users.
- **Production canary:** a live router directs bounded production traffic to a
  separately admitted challenger. This mode is blocked in the current runtime.
- **A/B test:** a user cohort experiment with business outcomes and statistical
  power. Scenario B is not an A/B test.

Sequential model evaluation by itself is called replay, never canary. The
controlled-canary claim additionally requires a deterministic per-request
assignment ledger joined to the observed response model identity.

## Guardrails

Policy version: `scenario-b-visa-v1`

| Signal | Pass condition | Decision role |
|---|---:|---|
| labeled CT accuracy | `>=0.80` | release admission |
| labeled CT F1 | `>=0.75` | release admission and expected breach |
| labeled CT AUROC | `>=0.80` | release admission |
| replay p95 latency | `<=30 ms` after warmup | runtime stop |
| replay error rate | `<=1%` | runtime stop |
| challenger assignment | `<=10%` and exactly `100/1000` | routing safety |
| route/response identity | `100%` | evidence integrity |

Missing identity, duplicate assignment, insufficient samples, a partial metric
window, evaluator error, or conflicting signals is a blocker. Signal precedence
is identity/integrity, error rate, latency, then offline quality. Any blocker
sets challenger allocation to zero.

## Rollout State Machine

1. `planned`: validate immutable identities and replay manifest.
2. `shadow`: collect at least 500 paired observations; stable remains the
   authoritative response.
3. `admission`: evaluate labeled CT plus latency/error/identity windows.
4. `canary_ready`: entered only when every guardrail passes.
5. `blocked`: the selected invalid challenger is expected to enter here because
   F1 is below policy; challenger allocation remains zero.
6. `contained`: persist stop reason and zero-allocation ledger.
7. `rolled_back`: route identity equals the captured stable identity and a
   postcondition inference succeeds.

The invalid fixture must never enter `canary_ready`. A separate deterministic
passing fixture proves the state machine can reach bounded assignment without
weakening the invalid-candidate decision.

## Timing And Acceptance

- stop decision to zero challenger allocation: `<=30 s`
- containment to exact stable route restoration: `<=300 s`
- shadow paired requests: `>=500`
- controlled replay assignments: `1,000` total and exactly `100` challenger
- assignment/response identity: `100%`
- stable endpoint mutation: `0`
- production interruption: `0 s`
- real CUDA challenger evaluation: required
- deterministic failing and known-good fixtures: required
- common operational evidence schema and live-proof validator: required

Scenario B non-disruptive closure is PASS only when the router/evaluator tests,
real CUDA evidence, assignment ledger, stop event, rollback identity and
postcondition inference all agree. A Kubernetes production canary remains a
separate `blocked_live_mutation` closure until its prerequisites pass.

## Evidence Schema

Evidence root:
`F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/scenario-b/<run_id>`

The run must contain:

- `contract.json`: policy, identities, mode and immutable input digests;
- `request-manifest.jsonl`: deterministic replay subset and request digests;
- `assignment-ledger.jsonl`: route, response identity and monotonic timing;
- `metric-windows.json`: sample counts, quality, latency and errors;
- `decision.json`: ordered blockers, stop trigger and allocation state;
- `rollback.json`: captured and restored stable route identities;
- `postconditions.json`: production readiness, inference and target checks;
- `report.json`: `evm.operational_failure_evidence.v1` closure envelope;
- `evidence-index.json`: SHA-256 for every evidence artifact.

All timestamps are UTC. Durations use monotonic elapsed time. Evidence is
written atomically and a failed attempt is retained for RCA rather than edited.

## RCA And Optimization Loop

- An identity mismatch blocks the run and requires artifact/route provenance
  repair; it is never bypassed.
- An insufficient window extends collection; it never produces a pass.
- A noisy latency breach is rerun after fixed warmup and reported by model,
  batch and contention; thresholds are not raised to force success.
- A false route allocation triggers hash/seed/ledger reconciliation and a new
  run ID.
- A rollback mismatch keeps the run failed even if health probes are green.

## Production Mutation Preflight

Before any future Kubernetes rollout, all of these must pass: Scenario D exit,
exact namespace/name/UID selector, source/runtime revision equality, immutable
image/model/artifact/CT identities, clean worktree, known-good rollback target,
Prometheus and supervisor health, dual-model GPU headroom, maintenance impact,
single-use approval binding and expiry. Zero or multiple targets fail closed.

No production mutation is authorized by this contract.

## Portfolio Boundary

Allowed claim: a local single-node system used real VisA/CUDA evidence to build
and validate fail-closed shadow and bounded controlled-replay delivery, reject
an immutable under-threshold model, and restore an exact stable route identity.

Prohibited claims: production user traffic, statistically powered business A/B,
high availability, multi-node isolation, or enterprise SLA validation.

## Implementation Backlog

- `B1`: complete - typed policy, identity, assignment, window and decision contracts.
- `B2`: complete - deterministic router plus failing/known-good fixtures.
- `B3`: complete - real CUDA replay collector and paired shadow ledger.
- `B4`: complete - guardrail evaluator, zero-allocation containment and rollback evidence.
- `B5`: complete - operational evidence report, validator and focused tests.
- `B6`: complete - controlled replay execution and non-disruptive closure.
- `B7`: in progress - final four-system synchronization and claim audit.
- `B8-live`: blocked; future separately approved Kubernetes production canary.
