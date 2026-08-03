# Full Lifecycle Guard Integration Validation Plan

Date: 2026-08-02
Status: plan complete; design validation PASS for implementation planning;
implementation, lifecycle execution, fault injection, model training, and
runtime mutation not started.
Parent program: `EVM-265 / SCRUM-171`
Cross-scenario dependency: `EVM-EPIC-22 / SCRUM-177`
Workstream epic: `EVM-EPIC-23 / SCRUM-183`
Planning issue: `EVM-276 / SCRUM-184`
Design validation:
`docs/status/2026-08-02-full-lifecycle-guard-integration-design-validation.md`

## Objective

Validate scenarios A-E as guards inside one real MLOps lifecycle rather than as
isolated scenario proofs. The target is an operator-launched VisA lifecycle
that keeps an existing GPU model as the stable identity, trains and evaluates a
new real candidate, governs model replacement, serves the new candidate on
CUDA, and proves that each guard blocks, contains, resumes, rolls back, or
recovers at the correct transition without hidden code or state repair.

This is a development-platform operations drill. It is not a customer
production deployment, real-traffic experiment, HA proof, or SLA claim.

## Verified Starting Evidence

The following results are inputs, not acceptance for this workstream:

| Baseline | Verified evidence | Remaining integration gap |
|---|---|---|
| real lifecycle | `EVM-243`: run `lifecycle-20260713T164053-c701bd39` completed 10/10 with VisA 10,821, CUDA B0 training, MLflow `b35b5cc3d0704464abe2288e6e3548be`, CT 2,181, accuracy `0.9624`, F1 `0.8075`, AUROC `0.9827`, staging and monitoring | no A-E fault was injected through this same stage graph; new candidate was not proven as the persistent development-production identity |
| operator lifecycle | `EVM-241`: Control Panel launched a real data/train/evaluate/approve/stage/serve flow | Product was restored after a single-GPU handoff; the trained candidate was not retained as the replacement target |
| A | three exact-UID B0 Pod recoveries | recovery of a newly promoted candidate is unproven |
| B | real controlled replay and exact stable-route recovery | release guard is not yet driven by the complete lifecycle state machine |
| C | real CUDA shift, one review event/candidate, manual hold | hold, governed resume, training and later release are not connected end to end |
| D | exact worker/observer recovery | idempotent side-effect reconciliation during a real training/release run is unproven |
| E | real VisA identity and 42 corruption replays | data and model-artifact blockers have not been exercised at both lifecycle admission points |
| cross-scenario | `EVM-271`: planning/design PASS | `EVM-272..275` implementation and proof are not started |

The planning source baseline is Git `e247efd133a2c486e81a1e700aa68133876770a9`.

## Explicit Non-Goals For This Turn

- no pipeline or guard implementation;
- no Airflow, MLflow, Kubernetes, API, worker, observer, or Control Panel
  mutation;
- no VisA training, CUDA Job, model registration, deployment, or Pod restart;
- no canonical data, model artifact, registry, stable pointer, or deployment
  intent change;
- no live A-E injection or cross-scenario execution;
- no Sprint 178 or completed A-E status change.

## Workstream And Dependency Model

| Order | Internal ID | Jira | Unit | State after planning | Dependency |
|---:|---|---|---|---|---|
| 0 | `EVM-276` | `SCRUM-184` | lifecycle guard master contract and design validation | Done after four-system synchronization | A-E independent evidence |
| 1 | `EVM-277` | `SCRUM-185` | common lifecycle identity envelope, guard dispatcher, and no-fault golden path | Done | `EVM-272`, `EVM-273`, `EVM-276` |
| 2 | `EVM-278` | `SCRUM-186` | E data and artifact integrity guard in the lifecycle | Done | `EVM-277` |
| 3 | `EVM-279` | `SCRUM-187` | D idempotent lifecycle continuity and side-effect reconciliation | Done | `EVM-278` |
| 4 | `EVM-280` | `SCRUM-188` | C quality/drift review, hold, and governed resume | Done | `EVM-279` |
| 5 | `EVM-281` | `SCRUM-189` | B candidate release guardrail and stable rollback | Done; two fresh real lifecycle branches PASS | `EVM-280` |
| 6 | `EVM-282` | `SCRUM-190` | A post-promotion GPU serving recovery | Done; fresh M1 transaction/recovery/M0 rollback PASS, 38/38 hashes | `EVM-281` |
| 7 | `EVM-283` | `SCRUM-191` | single-scenario integrated evidence closure | In Progress; A-E evidence validator and lifecycle-reachability audit next | `EVM-282` |
| 8 | `EVM-284` | `SCRUM-192` | final VisA operations drill | To Do, final gate | `EVM-283`, `EVM-274`, `EVM-275` |

`EVM-283` must block the pairwise proof `EVM-274`: each guard must work inside
the real lifecycle before two guards are combined. `EVM-284` is the final
success/failure/recovery drill after both the single-scenario and cross-scenario
tracks pass.

## Lifecycle State And Stage Contract

One `lifecycle_series_id` owns immutable attempts. Each attempt has one
`lifecycle_run_id`, one `correlation_id`, one profile digest, and one source
revision map.

| Stage | Operation | Guard authority | Required handoff |
|---|---|---|---|
| L0 | preflight and blueprint seal | D, E | exact runtime/resource revisions, stable/rollback identity, data and profile digest |
| L1 | real VisA intake | E | immutable source and acquisition lineage |
| L2 | data quality, split, CT and integrity admission | E, C | data admission plus measured quality state |
| L3 | candidate training | D | one owned execution, checkpoint and idempotency key |
| L4 | MLflow registration and artifact seal | E, D | run/model/card/environment/artifact/image identity |
| L5 | isolated CT and evaluation | E, C, B | no leakage, metrics, slices, runtime profile and candidate decision |
| L6 | approval | E, D, C, B | fresh evidence, independent actor and exact action digest |
| L7 | staging and controlled replay | B, E, D | exact stable/challenger routing and guardrail window |
| L8 | development-production replacement | B, E, D | two-phase replacement transaction and rollback target |
| L9 | CUDA serving verification | A, B, E, D | exact model/image/dataset identity and target UID |
| L10 | Prometheus and drift monitoring | A, C, D | serving health, quality baseline and review state |

No stage may infer readiness from a previous scenario's closure. Each handoff
must validate current-run evidence and freshness.

## Common Identity Envelope

Every stage, guard decision, action, and artifact must carry:

- lifecycle series, run, attempt, correlation, incident and causation IDs;
- source branch, dirty-state digest, component-specific expected revision and
  executable image digest;
- pipeline profile ID/version/digest and policy ID/version/digest;
- dataset, source manifest, shard index, split, CT and lineage digests;
- MLflow experiment/run, candidate, model, model-card, environment, artifact
  and image digests;
- target environment, cluster, namespace, workload kind/name/UID and Pod UID;
- worker/observer PID, process start, command digest, lease and fencing token;
- stable-before, candidate, stable-after and rollback model identities;
- UTC audit timestamps, monotonic durations, evidence digest and stable
  semantic decision digest.

The runtime revision is a component map, not a false global-equality check.
Documentation HEAD, API, worker/observer, trainer image, serving image and model
artifact may be intentionally different only when the sealed revision manifest
declares the exact expected value for each component.

## Execution Modes And Single-GPU Boundary

### Preferred: Remote Train, Local Serve

- local RTX GPU keeps stable model `M0` serving;
- Mac mini M4 Pro trains candidate `M1` through the registered remote worker;
- remote worker preflight must prove host identity, MPS/runtime capability,
  source revision, profile digest, storage path, artifact transfer digest and
  heartbeat freshness;
- local CUDA performs isolated CT, controlled replay and serving verification
  only through the resource-arbitration contract below.

This mode may prove service continuity during remote training, but not HA.

### Fallback: Single-GPU Serial Handoff

- seal stable/rollback identity and exact interruption impact;
- release the GPU from stable serving, train/evaluate/stage serially, then
  restore or replace the stable target;
- record every interruption and forbid a concurrent-availability claim;
- any live target change requires a separate maintenance approval.

The execution mode is selected before the run and cannot change after a guard
fails.

### Local GPU Validation Arbitration

Remote training and local stable serving may overlap, but local candidate CT or
staging validation cannot assume a second schedulable GPU. Before L5, one of two
sealed modes is required:

1. `serial_cuda_validation_handoff`: an approved bounded handoff releases local
   stable GPU ownership, runs candidate CT/serving verification, then restores
   or replaces the stable target. Interruption is measured and no concurrent
   availability claim is allowed.
2. `shared_cuda_validation`: allowed only after an explicit VRAM/concurrency
   preflight proves both immutable workloads fit, exact process/container
   identity is visible, and an abort threshold is bound. Ad hoc Docker sharing
   outside the resource policy is forbidden.

If neither mode passes, L5 is `blocked_resource_admission`. The system cannot
silently move CT to CPU or change execution mode after launch.

## Real Model And Data Policy

- dataset: real VisA, `10,821` records and `23` current shards;
- development identities: `8,640`; isolated CT identities: `2,181`; overlap
  must remain zero;
- stable model `M0`: the exact current approved B0 serving identity captured at
  preflight;
- positive candidate `M1`: new Torch EfficientNet-B0 run with a new immutable
  model identity;
- negative release candidate `Mbad`: real EfficientNet-B0 profile
  `b-quality-negative-v1`, with a fixed frozen backbone and a newly initialized
  head trained for exactly `3` epochs on the unchanged training split. It is a
  real model run intended to produce an observed release-metric breach without
  corrupting labels, data identity or metric output;
- fixed seed, split and environment; minimum `3`, maximum `8` epochs;
- early stop is admitted only after the minimum depth and two consecutive
  validation observations meet accuracy `>=0.93`, F1 `>=0.75`, AUROC `>=0.80`;
- release runtime guardrails: p95 latency `<=30 ms`, error rate `<=1%`, exact
  assignment/response identity `100%`;
- thresholds, split, seed and model profile are immutable after launch.

If a positive candidate misses a threshold, the run becomes valid B/C blocker
evidence. The policy is not lowered during execution to force promotion.
If `Mbad` unexpectedly passes every release threshold, that attempt is valid
evidence but does not close the B-quality negative test. A new versioned stress
profile requires design review; metrics or thresholds are never altered.

## No-Hidden-Repair Operator Contract

Allowed operator actions:

- select and seal a Run Blueprint/profile;
- start, pause, approve, reject, continue or retry through the versioned Control
  Panel/API commands admitted by the runbook;
- issue the predeclared one-time scenario injection and rollback commands;
- inspect evidence, logs, metrics and artifacts.

Forbidden after launch:

- edit code, configuration, database rows, manifests or evidence files;
- direct `kubectl patch/delete/scale` outside the bound scenario executor;
- change metrics, thresholds, split or candidate identity;
- manually rewrite Airflow, MLflow, registry, intent or lifecycle state;
- delete a blocker or replace a failed artifact in place.

If a forbidden repair is required, the attempt is failed. The correction gets a
new commit, profile version and attempt linked with `supersedes_attempt_id`.

## Golden Path: G0-G3

### Purpose

Establish that the integrated lifecycle can succeed without a fault before a
guard is judged on an injected failure.

### Required Sequence

1. G0 read-only preflight captures `M0`, rollback, runtime revision map,
   storage, Airflow, MLflow, GPU/remote worker, Kubernetes and Prometheus.
2. G1 seals the real VisA profile, data/split/CT identity and guard policy.
3. G2 trains `M1`, registers MLflow/artifacts and passes isolated CT.
4. G3 deploys to staging, performs controlled replay, records an approval-ready
   replacement transaction and verifies rollback dry-run.

G3 does not modify the development-production stable pointer. The actual
replacement is reserved for Scenario A entry after all earlier guards pass.

### Golden Acceptance

- every L0-L7 stage reaches the expected state without retry or hidden repair;
- one training Job, one MLflow run, one candidate and zero duplicate intents;
- exact data/model/artifact/image/CT identity is complete;
- real GPU/MPS training and local CUDA CT are evidenced according to mode;
- Control Panel and API expose the same stage, progress, blocker and identity;
- existing `M0` and its rollback identity remain available and unchanged;
- all artifacts close under one hash index and machine validation passes.

## Guard Authority Matrix

| Guard | Owns | Must block | May resume/contain | Must never do |
|---|---|---|---|---|
| E integrity | trust of data, split, CT, model, artifact, image and lineage | any dependent training, approval or replacement using untrusted identity | admit a new immutable corrected attempt after full revalidation | repair canonical input or waive a digest mismatch |
| D continuity | freshness, ownership and idempotent stage continuity | mutating transition under stale/duplicate/revision-mismatched control | recover one exact owned worker/observer and reconcile the current stage | global restart, PID-only kill, optimistic retry or duplicate external side effect |
| C quality | measured data/model quality review and candidate governance | automatic training/promotion after review-required state | hold, create one candidate profile, accept an independent reviewed decision | equate drift with automatic deployment |
| B release | candidate release admission, allocation and stable rollback | challenger allocation or replacement after metric/runtime/identity breach | reduce allocation to zero and restore exact stable route | override E/D/C or call controlled replay business A/B |
| A serving | health and exact recovery of the committed stable serving target | ambiguous/global serving recovery | recreate exact committed workload/model identity | promote a candidate or choose rollback policy |

### Signal Ownership And Conflict Routing

| Signal class | Primary guard | Secondary effect |
|---|---|---|
| schema, checksum, lineage, split/CT leakage, artifact/image/model-card identity | E | all dependent transitions held |
| worker/observer heartbeat, revision, owner lease, duplicate side effect | D | mutation frozen until exact reconciliation |
| input distribution, delayed-label quality, prediction distribution/confidence | C | review/hold and candidate governance only |
| concrete candidate CT metrics, latency, error rate and allocation identity | B | reject candidate or contain/rollback route |
| committed stable target readiness, CUDA inference and scrape health | A | recover exact committed serving identity |

One signal has one primary decision owner. C may explain why a future candidate
is needed, but B alone decides whether a concrete candidate is releasable. A
restores the already committed stable target and cannot select a candidate.
E/D blockers outrank all risk-increasing transitions. If multiple guards fire,
each decision remains a separate causal child under one incident; the system
does not duplicate actions or let a lower guard clear a higher blocker.

## Fault Injection Safety Envelope

Every injection is an immutable manifest bound to scenario, lifecycle
run/attempt, exact target identity, expected current state, action digest,
actor, approval where required, expiry, maximum blast radius, abort condition,
rollback target and forbidden resources.

- E/C/B injections use only derived data, candidate or controlled replay state.
- D may terminate only one exact supervisor-owned PID/lease.
- A may restart only one exact committed serving Pod UID.
- device-plugin, cluster-wide resources, canonical data, unrelated processes
  and real user traffic are always excluded.
- zero/multiple target matches, dirty source, revision mismatch, stale baseline,
  missing rollback or expired approval blocks injection.
- an independent observer captures before/after invariants; the injector cannot
  certify its own success.

## Scenario E: Lifecycle Integrity Guard

### E-Data Injection At L2

- create an isolated derived manifest with one deterministic wrong shard digest
  or one train/CT overlap;
- preserve the canonical source and original manifest;
- submit the derived identity through the normal lifecycle intake, not directly
  to the validator.

Expected guard action:

- transition L2 to `blocked_integrity` with one deterministic blocker;
- create no training Job, MLflow run, candidate, approval or deployment intent;
- keep `M0`, serving UID, GPU/plugin and Prometheus state unchanged;
- write exact offending identity, policy and lineage edge to evidence.

### E-Artifact Injection At L4/L6

- use a successfully trained isolated candidate attempt;
- replace only the submitted artifact/model-card/image reference with a wrong
  digest in a derived admission request;
- do not modify the actual artifact.

Expected guard action:

- MLflow evidence remains historical, but approval and replacement are blocked;
- staging allocation and deployment intent remain zero;
- the candidate is quarantined and cannot be rebound to a corrected artifact.

### E Resume And Acceptance

- a corrected immutable input creates a new attempt, never edits the blocked
  one;
- three deterministic decision replays produce the same blocker/fingerprint;
- corrupted attempts admitted: `0`; unintended intents: `0`; canonical and
  stable identity delta: `0`; decision target `<=30 s`;
- both data-entry and release-entry E gates must pass before EVM-278 closes.

## Scenario D: Idempotent Lifecycle Continuity Guard

External Kubernetes, MLflow and object-storage writes do not share one atomic
transaction. The valid objective is one observable side-effect outcome through
idempotent dispatch and reconciliation, not a distributed exactly-once claim.

Each mutating operation uses:

```text
side_effect_key = sha256(
  lifecycle_run_id + attempt_id + stage_id + action_type + target_identity
)
```

The durable state is `planned -> dispatched -> externally_observed ->
committed`. After restart, the worker queries Kubernetes/MLflow/storage by the
same key before dispatch. An ambiguous external result enters `held_unknown`;
it never retries optimistically.

### D-Worker Injection At L3/L4

- after the Kubernetes training Job is admitted, terminate only the exact
  supervisor-owned lifecycle worker using PID/start/command/lease identity;
- the Kubernetes Job may continue; no Job or model artifact is deleted.

Expected guard action:

- detect within `10 s`, recover within `60 s`, converge to the expected
  component revision and fencing token;
- reconcile external Job/MLflow state before issuing another action;
- produce one training Job, one MLflow run, one candidate and one artifact seal.

### D-Observer Injection At L6

- stop only the exact observer or use a controlled stale heartbeat fixture
  before approval;
- stale evidence must hold approval and replacement until recovery.

### D Resume And Acceptance

- same run may resume only when the stage idempotency key and external-side
  effect ledger agree; ambiguity creates a blocked new attempt;
- false/global restarts `0`, duplicate workers `0`, duplicate Jobs/MLflow
  runs/intents `0`, revision convergence `100%`;
- Control Panel must show recovering, recovered and resumed states rather than
  silently changing the stage to success.

## Scenario C: Quality/Drift Hold And Governed Resume

### C Injection At L2 Or L10

- use an immutable derived VisA window with the existing deterministic category
  mix/prediction-confidence shift;
- retain baseline, method, window, threshold and affected slice identities;
- do not corrupt schema or digest, which belongs to E.

Expected guard action:

- create exactly one `review_required` event and one candidate profile;
- hold automatic training, approval, release and replacement;
- duplicate/stale signals update audit counts but create no duplicate candidate;
- existing `M0` remains served.

### C Governed Resume

- an independent operator either rejects the candidate, retains manual hold, or
  approves a versioned training plan after label/quality review;
- approval changes only governance state and cannot bypass E/B/CT gates;
- corrected or newly labeled data is a new immutable version and attempt.

### C Acceptance

- drift decision `<=300 s`; event/candidate cardinality `1`; deployment intent
  before release admission `0`;
- method/window/threshold/slices are complete and reproducible;
- reject, hold and approved-for-training paths each have an audit trail;
- no automatic production replacement is possible.

## Scenario B: Candidate Release Guard And Stable Rollback

### B-Quality Injection At L5/L6

- run a real `Mbad` candidate through training, MLflow and isolated CT;
- the predeclared stress profile must satisfy minimum training depth and produce
  an observed metric breach without mocked metrics.

Expected guard action:

- candidate state becomes `rejected_release`; approval and replacement intents
  remain zero; `M0` remains stable;
- observed metrics, threshold and exact candidate/artifact identity are joined.

### B-Runtime Injection At L7

- route exactly `10%` of `1,000` controlled replay assignments to an admitted
  candidate and inject a deterministic isolated error-rate breach;
- real user traffic and the stable endpoint are excluded.

Expected guard action:

- detect within `30 s`, set challenger allocation to zero and verify exact
  stable route within `300 s`;
- assignment/response identity is `100%`; stable endpoint mutation and
  interruption are zero in controlled-replay mode.

### B Acceptance

- quality breach and runtime breach are separate immutable attempts;
- no guard or operator can lower the policy mid-run;
- exact stable model/image/route/Prometheus identity passes after containment;
- this is controlled operational replay, not business A/B.

Closure PASS at evidence source `1e541de`: measured quality rejection and
1,000-request runtime containment both denied approval before deployment
intent, preserved exact B0 CUDA identity and independently closed 95/95 indexed
artifacts. See `docs/status/2026-08-03-lifecycle-guard-scenario-b-closure.md`.

## Scenario A: Post-Promotion Serving Recovery

Scenario A begins only after E, D, C and B integrated guards pass and a golden
`M1` replacement package is approval-ready.

### Two-Phase Development-Production Replacement

1. `prepare`: bind `M0`, `M1`, rollback, data/CT, artifact/image, policy,
   target UID and action digest.
2. `approve`: independent, expiring, single-use target-bound approval.
3. `apply`: deploy `M1` to the exact development-production target.
4. `verify`: readiness, real CUDA inference, metrics, Prometheus and exact
   identity.
5. `commit`: update stable pointer/ledger only after verification.

If apply or verify fails, rollback restores `M0`; an unverified `M1` never
becomes the committed stable pointer.

| Failure point | Required result |
|---|---|
| before apply | no workload or stable-pointer change |
| apply fails | delete/contain only the attempted M1 revision; M0 stays stable |
| readiness/inference/metrics verify fails | rollback exact M0 workload and prove serving health; pointer remains M0 |
| pointer CAS commit fails after M1 verifies | freeze further actions, reconcile current workload, then restore M0 unless the transaction can prove a single committed owner |
| observer becomes stale during apply/verify | hold commit and rollback through the exact B/D owner policy; never infer success |
| commit succeeds but later serving fails | A recovers committed M1; B rollback to M0 requires a separate action decision |

### A Injection At L9

- after `M1` is committed and cooldown passes, restart only its exact serving
  Pod UID through the approved scenario executor;
- Deployment template, device-plugin, data, registry and unrelated Pods remain
  unchanged.

Expected guard action:

- detect loss within `30 s`, recover `1/1 Ready` within `300 s`;
- restored identity must be `M1`, not the older `M0` unless an explicit
  rollback decision was made;
- real CUDA inference and the exact Prometheus target return healthy;
- actual endpoint interruption and recovery time are recorded.

### A Acceptance

- exact target/approval/rollback preflight `100%`;
- one restart action, one recovered workload, no global mutation;
- post-recovery model/data/image identity matches committed `M1`;
- a rollback exercise separately proves exact `M0` restoration without
  rewriting the successful `M1` recovery evidence.

Closure PASS at source `d121c9c`: fresh M1 apply/verify/commit completed in
`18.173018 s`; exact M1 Pod loss was detected in `0.2045879 s` and recovered in
`10.0966768 s` with `9.8788259 s` endpoint interruption; separate M0 rollback
completed in `57.634592 s`. The Recreate zero-Pod condition exercised the new
exact reconcile guard. Final M0/CUDA/Prometheus/plugin state passed and 38/38
evidence hashes matched. See
`docs/status/2026-08-03-lifecycle-guard-scenario-a-closure.md`.

## Retry, Resume And Immutability Rules

- each blocked, failed, rejected or rolled-back attempt is immutable;
- D may resume the same attempt only through an exact idempotency/side-effect
  reconciliation decision;
- E/C/B corrections create a new attempt with new input/decision identity;
- A controller recovery keeps the committed model identity; release rollback is
  a separate action and evidence record;
- every retry records reason, actor, source/profile/policy revisions and
  `supersedes_attempt_id`;
- a later PASS cannot replace a failed report or RCA.

Scenario attempts branch from the same sealed G3 golden snapshot. A terminal E,
C or B blocker in one attempt is never cleared so that the next scenario can
run. The next scenario receives a new attempt ID and an exact reference to the
same immutable golden inputs. This prevents a previous failure from leaking
state into another guard's acceptance.

## SLI, SLO And Invariants

| Area | Planning target |
|---|---:|
| identity completeness at every handoff | `100%` |
| canonical data or approved artifact mutation | `0` |
| duplicate Job, MLflow run, candidate or deployment intent | `0` |
| E corrupted admission and downstream intent | `0` |
| D worker detection/recovery | `<=10 s / <=60 s` |
| C decision and event/candidate cardinality | `<=300 s / 1 / 1` |
| B containment and stable-route recovery | `<=30 s / <=300 s` |
| A serving detection/recovery | `<=30 s / <=300 s` |
| exact assignment/response and post-recovery identity | `100%` |
| hidden repair or post-launch threshold change | `0` |
| parent/child evidence hash closure | `100%` |

Training duration is measured but not constrained as an availability SLO. Mode,
resource utilization, GPU/MPS profile and interruption are reported explicitly.

## Evidence Root And Required Artifacts

Original evidence remains under the F-drive:

```text
F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/
  lifecycle_guard_validation/<lifecycle_series_id>/<attempt_id>/
```

Each attempt contains:

- `blueprint.json`, `runtime-revision-map.json`, `resource-preflight.json`;
- `identity-envelope.json`, `stage-events.jsonl`, `guard-decisions.jsonl`;
- `action-ledger.jsonl`, `side-effect-ledger.jsonl`, `attempt-lineage.json`;
- data/split/CT/quality/integrity summaries and immutable digests;
- training/MLflow/model-card/artifact/image/GPU profile references;
- candidate matrix, approval, replacement transaction and rollback package;
- Kubernetes events, serving readiness/inference and Prometheus observations;
- `validation-report.json`, `artifact-index.json`, `review.md`, and `rca.md`
  when failed or superseded.

Git stores schemas, policies, tests, summaries and indexes, never credentials,
raw datasets, model binaries or large logs.

## Control Panel Acceptance

The operator must be able to perform the admitted workflow without editing
files:

- select/seal profile, dataset, stable model, candidate policy and execution
  mode;
- see every L0-L10 stage as Not Started, In Progress, Held, Blocked, Recovering,
  Rolled Back or Completed with real progress;
- inspect exact guard cause, owner, required action, evidence age and identity;
- approve/reject/continue/retry only when the server-side contract admits it;
- compare `M0`, `M1`, `Mbad` and rollback identity;
- observe training progress, worker/observer health, GPU/MPS resources,
  controlled allocation, serving and Prometheus state;
- see failed attempts and RCA without overwriting the active run.

Browser automation supplements but does not replace API/evidence validation.

## Execution Series

1. G0-G3 no-fault golden staging path.
2. E-data blocked plus corrected-attempt pass.
3. E-artifact blocked at approval/replacement.
4. D-worker exact recovery during real training reconciliation.
5. D-observer stale hold/recovery before approval.
6. C shift -> review/hold; duplicate/stale/reject/approve-for-training paths.
7. B real quality rejection.
8. B controlled runtime breach and exact stable containment.
9. A two-phase `M0 -> M1` replacement and exact `M1` serving recovery.
10. A explicit rollback package proof to `M0`.
11. EVM-283 closure and handoff to pairwise `EVM-274`.
12. After `EVM-274/275`, EVM-284 final VisA operations drill.

Each guard decision gets three deterministic replays. At least one actual full
lifecycle attempt reaches the guard's injection point; a decision replay alone
cannot close a scenario.

## Failure And RCA Loop

A run fails if the guard fires at the wrong stage, misses its blocker, mutates an
unowned target, produces a duplicate side effect, loses exact identity, needs a
forbidden repair, or cannot prove stable/rollback state. The next attempt starts
only after immutable RCA, a reproducing test, versioned correction, prevention
action and fresh preflight.

## Portfolio Claim Boundary

After implementation and evidence, the allowed claim is a real single-node
MLOps lifecycle in which independently tested guards are integrated with actual
VisA data, Torch training, MLflow, isolated CT, governed model replacement,
CUDA serving, observability and bounded recovery.

It remains invalid to claim real customer production, uninterrupted service in
serial GPU mode, business A/B, multi-node HA, distributed exactly-once delivery,
enterprise PKI, or a contractual SLA.

## Planning Exit

- this plan, master and issue register are versioned and pushed;
- a separate Jira Epic and detailed scenario backlog exist outside Sprint 178;
- cross-scenario implementation dependencies and the EVM-274/EVM-284 handoff
  are linked;
- a read-only design validation records gaps, remediation and final verdict;
- Git, Jira, Notion and Obsidian point to the same final SHA/state;
- implementation, lifecycle execution and runtime mutation remain not started.
