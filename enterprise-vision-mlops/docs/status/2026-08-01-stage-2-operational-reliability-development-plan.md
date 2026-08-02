# Stage 2 Operational Reliability Development Plan

Date: 2026-08-01
Status: plan complete; A-E independent execution complete; cross-scenario not started.
Parent: `EVM-265 / SCRUM-171`
Scenario issues: `EVM-266..270 / SCRUM-172..176`
Predecessor:
`docs/status/2026-08-01-operational-failure-validation-master-plan.md`
Jira timebox: Sprint `178`, `EVM S2 A-E 2026-08-01~08-02`, active from
`2026-08-01 23:07:32 KST` through `2026-08-02 23:59 KST`.

Jira schedules the five scenario subtasks through parent `SCRUM-171` because
subtasks cannot be assigned independently. The timebox covers readiness,
implementation start, and non-disruptive validation. At planning time it did
not change the unstarted A0-A7 state or authorize A8/live mutation.

Execution update on `2026-08-02`: Scenarios A-E now have independent local
closure evidence. Scenario A used its separately approved bounded maintenance
action; B-E remained non-disruptive. Cross-scenario validation and the final
VisA operations drill remain open, so the parent master stays In Progress.

## Objective

Stage 2 implements, verifies, and optimizes reliability controls before running
the approved live fault drills. The outcome is not five shell scripts. It is a
reusable safety/evidence layer that can prove target selection, detection,
containment, recovery, rollback identity, timing, and residual risk.

No Pod, Deployment, device-plugin, production endpoint, dataset, or model is
changed by this planning checkpoint.

## Honest Environment Boundary

- One Windows workstation, one Docker Desktop Kubernetes node, WSL2 GPU-PV,
  and one NVIDIA GPU.
- Production B0 currently owns the GPU; B7 staging is intentionally `0/0`.
- Controlled VisA replay is available. Real user traffic is not.
- A one-replica restart can cause a measured outage and is not zero downtime.
- Independent stable/challenger GPU Pods cannot currently run concurrently
  when both request the exclusive `nvidia.com/gpu: 1` resource.
- Shadow and bounded routing mechanics may be validated. Business A/B, user
  impact, multi-node HA, and production SLA claims are prohibited.

## Why The Order Is A And D, Then B, E, C

1. **A establishes target truth and recovery.** Every later scenario needs a
   trustworthy answer to which active resource failed, when it failed, and
   whether the exact serving identity returned.
2. **D establishes control-plane liveness and idempotency.** Canary stop,
   integrity admission, and retraining review are unsafe if the worker or
   observer can become stale, execute twice, or run a different revision.
3. **B establishes release containment.** Before generating more candidates,
   the platform must stop a bad model path and restore the known-good identity.
4. **E establishes trusted inputs.** Drift and retraining decisions are invalid
   when split leakage, missing lineage, or artifact mismatch can pass admission.
5. **C consumes B and E.** Quality degradation may create a candidate only when
   inputs are trusted and limited deployment/rollback is already governed.
6. **Cross-scenario tests come last.** Combining failures before each detector
   and recovery path is independently deterministic makes RCA ambiguous and
   increases the blast radius.

Implementation of common components may overlap, but a scenario cannot be
closed or used as evidence for the next scenario until its exit criteria pass.

## Shared Reliability Foundation

### Planned Code And Configuration

| Unit | Planned path | Purpose | Required by |
|---|---|---|---|
| evidence models | `src/evm/operations/failure_evidence.py` | typed report, identity, timing, limitation and portfolio fields | A-E |
| state and lease | `src/evm/operations/failure_scenarios.py` | atomic state transitions, one active owner, idempotency and recovery | A-E |
| target health | `src/evm/operations/target_health.py` | exact resource selection; active versus historical terminal state | A, D, B |
| runtime adapters | `src/evm/operations/runtime_adapters.py` | read-only Kubernetes, Prometheus, heartbeat and artifact collectors | A-E |
| policy evaluator | `src/evm/operations/policy.py` | approval, namespace, mutation, identity and dependency gates | A-E |
| metrics projection | `src/evm/operations/metrics.py` | low-cardinality SLI projection for API/Prometheus | A-E |
| CLI | `scripts/dev/run_operational_scenario.py` | plan, baseline, execute, recover and validate commands | A-E |
| validator | `scripts/dev/validate_operational_failure_evidence.py` | deterministic contract and artifact-index validation | A-E |
| local policy | `configs/operations/local_failure_validation.toml` | allowlist, timeouts, targets, evidence root and approval modes | A-E |
| alert rules | `monitoring/prometheus/rules/operational-reliability.yml` | local rule evaluation and alert state | A-E |
| Prometheus config | `monitoring/prometheus/prometheus.yml` | load the versioned rule file | A-E |
| Grafana dashboard | `monitoring/grafana/dashboards/operational-reliability.json` | target health, detection, recovery, blockers and scenario state | A-E |
| Control Panel view | `apps/control-panel/src/views/ReliabilityView.tsx` | concise scenario/readiness/evidence view; no raw-log wall | A-E |

The API exposes current scenario summaries and Prometheus metrics. Run IDs,
digests, file paths, model versions, and record identities stay in evidence and
audit payloads rather than metric labels to avoid cardinality growth.

### Shared State Machine

```text
planned -> baseline_validated -> non_disruptive_validated
-> pending_approval -> approved -> injecting -> detected -> contained
-> recovering -> verifying -> passed
```

Any state may become `blocked` or `failed`. A failed run is immutable. A retry
creates a new run linked by `supersedes_run_id`; it does not rewrite history.

### Approval Tiers

| Tier | Examples | Execution rule |
|---|---|---|
| read-only | baseline, config render, metric query, digest compare | no external approval |
| isolated mutation | temporary evidence copy, fixture heartbeat, local test process | automatic only inside allowlisted test root |
| live bounded mutation | restart the only serving Pod, stop a supervised child | explicit scenario approval and captured rollback |
| cluster-wide mutation | device-plugin hostPath/config, Docker Desktop restart | explicit maintenance approval; excluded by default |

### Shared Metrics

- `evm_operational_scenario_state{scenario,target,state}`
- `evm_operational_target_health{scenario,target,signal}`
- `evm_operational_detection_seconds{scenario,target}`
- `evm_operational_containment_seconds{scenario,target}`
- `evm_operational_recovery_seconds{scenario,target}`
- `evm_operational_validation_total{scenario,result}`
- `evm_operational_active_blockers{scenario,code}`

No `run_id`, digest, URI, Pod name, or free-form reason is allowed as a metric
label. The dashboard links to the evidence API for those details.

### Shared Test Layers

1. Pydantic/schema unit tests for valid and invalid evidence.
2. State/lease/policy unit tests, including process crash and stale lock.
3. Adapter contract tests with recorded Kubernetes/Prometheus payloads.
4. Non-disruptive integration tests against the live read-only runtime.
5. UI contract and Playwright tests for state, blocker, timing and approval.
6. Approved live drills, repeated three times where the action is safe enough,
   with one immutable report per run.

## Scenario A: GPU And Serving Failure

Issue: `EVM-266 / SCRUM-172`
Runbook: `docs/runbooks/operational-scenario-a-gpu-serving-failure.md`

### 1. Baseline And Gap

Current evidence proves GPU/plugin/production `1/1`, CUDA readiness, Prometheus
up, and the prior WSL driver-path recovery. Missing capabilities are a reusable
scenario engine, exact target-scoped health, automated incident timing, alert
rules, immutable run evidence, and a safely approved live restart workflow.

The observer aggregate currently includes historical failed Jobs and replaced
Pods. It cannot be used alone as the active target decision. B7 staging is
`0/0`, so a staging Pod restart is not presently executable.

### 2. Additions

- shared evidence, state, policy, target-health, adapter and metric foundation;
- Kubernetes selectors for Node, device-plugin and the selected Deployment/Pod;
- active/historical classification without deleting historical failures;
- readiness/inference/Prometheus consecutive-success postconditions;
- alerts for GPU allocatable loss, plugin unavailable, serving target down,
  recovery budget exceeded and identity mismatch;
- Grafana timeline: baseline -> injected -> detected -> recovering -> verified;
- Control Panel target, approval, blocker and evidence summary.

### 3. Units And Dependencies

| Order | Backlog | Dependency | Output |
|---:|---|---|---|
| A0 | typed evidence and validator | contract v1 | valid/invalid report tests |
| A1 | atomic state, lease and approval policy | A0 | idempotent transition tests |
| A2 | target-scoped Kubernetes/Prometheus collectors | A0 | recorded adapter fixtures |
| A3 | target health and historical classification | A2 | named-target baseline report |
| A4 | metrics, Prometheus rules and dashboard | A1-A3 | query/rule/dashboard tests |
| A5 | read-only CLI baseline and recovery planner | A1-A4 | F-drive plan/baseline evidence |
| A6 | non-disruptive stale-hostPath planner test | A5 | no live DaemonSet mutation proof |
| A7 | approval package and rollback capture | A5-A6 | pending_approval evidence |
| A8 | live Pod restart and recovery proof | explicit approval | three-run result or blocker |

### 4. Validation Stages

**Non-disruptive:** schema/state tests, recorded payloads, live read-only target
baseline, stale-hostPath fixture, alert expression tests, dashboard/API/UI tests.

**Approval required:** preferred mode restarts the one production B0 Pod in a
local maintenance window. It does not patch the Deployment. The alternative
production-to-staging handover is used only if separately approved. Live
device-plugin mutation stays excluded from the default drill.

### 5. SLI And Local SLO Targets

| SLI | Target | Measurement |
|---|---:|---|
| failure detection | <=30 s | injection timestamp to first target-scoped unhealthy signal; two 15 s scrape intervals |
| serving recovery | <=300 s | injection to three consecutive 5 s readiness/inference successes and two healthy Prometheus scrapes |
| identity recovery | 100% exact | expected and observed model/image/artifact digests |
| false active blocker | 0 | historical terminal resources must not mark the named active target down |
| mutation scope | exact target only | before/after Kubernetes object diff |

These are local engineering objectives, not a production SLA.

### 6. Acceptance

- all A0-A7 tests and non-disruptive evidence pass;
- unapproved live and cluster-wide modes return `blocked` before mutation;
- approved Pod loss is detected and recovers within budget;
- any single-replica outage is measured, not hidden;
- exact identity, CUDA inference, target health and Prometheus recover;
- three safe repetitions have no orphan lease or unexpected object diff;
- validator returns `passed` with a complete artifact index.

### 7. Redesign And Optimization Loop

On missed/noisy detection, fix target scope, debounce, signal source, or query;
do not simply widen the objective. On slow recovery, separate scheduling, image
load, probe, model load and scrape delays, optimize the dominant phase, then
rerun with the same policy. On identity mismatch, stop and restore the approved
artifact before any retry. Failed runs and RCA remain immutable.

### 8. Evidence And Synchronization

Evidence root: `.../failure_scenarios/A/<run_id>`. Required extras include
resource before/after, events, readiness/inference samples, Prometheus samples,
alert transitions, object diff and rollback identity.

Sync checkpoints: A0-A4 implementation, A5-A7 non-disruptive validation, each
failed live attempt/RCA, and final A exit. Each checkpoint updates one Git
commit/push, Jira comment/status, Notion evidence summary, and Obsidian work
log/Current Context/index/graph as one consistent bundle.

### 9. Interview Positioning

Explain controller reconciliation versus custom orchestration, target-scoped
health versus global aggregates, why two Prometheus intervals define the local
detection budget, exclusive GPU scheduling, exact identity verification, and
why a one-replica drill is not HA or zero downtime.

### A Exit Criteria

Common foundation and target health are reusable, A live result is passed or
honestly blocked by approval/hardware, rollback is verified, and no unresolved
P0/P1 defect remains. D implementation may proceed earlier, but B cannot enter
live validation while A is failed or has an unsafe rollback path.

## Scenario D: Lifecycle Supervision Failure

Issue: `EVM-269 / SCRUM-175`
Runbook: `docs/runbooks/operational-scenario-d-lifecycle-supervision.md`

Execution update `2026-08-02`: D0 is fixed by
`docs/status/2026-08-02-scenario-d-lifecycle-supervision-contract.md`. A/P0 is
baseline-only. The implementation must bind exact process-start/command/source/
lease/fence identity, persistent restart budgeting, fenced run claims, API and
Prometheus state, deterministic fixtures, and three fresh exact-child recovery
runs before D can close.

Closure update `2026-08-02`: D0-D8 passed for the admitted single-node local
scope at executable revision `37ec89d`. The authoritative worker/observer/worker
series is `scenario-d-series-20260802T082205Z-37ec89d6`; maximum detection,
recovery, and healthy heartbeat p95 are `5.870 / 9.049 / 5.0 s`. All three
reports pass common closure and `9 / 9` hashes. The first D8 series remains
immutable superseded RCA evidence. See
`docs/status/2026-08-02-scenario-d-lifecycle-supervision-closure.md`.

### 1. Baseline And Gap

P0 proved lifecycle-worker termination and automatic restart. Observer loss,
stale heartbeat, revision mismatch, stale PID/lock, in-flight run recovery and
duplicate-execution prevention are not yet one repeatable scenario.

### 2. Additions

- supervisor/worker/observer adapters and normalized health reasons;
- lease fencing token and execution idempotency key;
- revision reconciliation that records Git HEAD and runtime code revision;
- in-flight stage recovery decision: resume, retry-safe, or deterministic block;
- alerts for worker/observer offline, stale heartbeat, revision mismatch,
  duplicate owner and restart storm;
- dashboard panels for heartbeat age, revision, PID, restarts and current run.

### 3. Units And Dependencies

D0 consumes A0-A4. D1 adds heartbeat/revision fixtures. D2 adds fencing and
idempotency. D3 adds in-flight recovery tests. D4 adds metrics/alerts/UI. D5
runs isolated stale/revision tests. D6 terminates one approved supervisor-owned
child and validates recovery.

### 4. Validation Stages

**Non-disruptive:** fake clock, stale heartbeat file under test root, mismatched
revision process fixture, PID reuse fixture, concurrent lease attempts, and an
in-memory/in-test lifecycle stage.

**Approval required:** terminate one real supervisor-owned child at a time.
Never stop Docker Desktop, databases, API, or unrelated user processes.

### 5. SLI And Local SLO Targets

| SLI | Target | Measurement |
|---|---:|---|
| dead child detection | <=10 s | termination to supervisor incident, two 5 s checks |
| stale heartbeat detection | <=25 s | last heartbeat plus 20 s threshold and one check |
| child recovery | <=60 s | incident to live heartbeat with matching revision |
| duplicate execution | 0 | idempotency/fencing audit for the same stage key |
| wrong-process termination | 0 | PID plus command-line ownership evidence |

### 6. Acceptance

All fixtures fail closed; only owned children restart; revision converges;
restart reason/count persists; in-flight work resumes safely or blocks once;
three approved child terminations create no duplicate mutation; alerts and UI
agree with evidence; reports validate.

### 7. Redesign And Optimization Loop

Duplicate work triggers a state-machine/fencing redesign, not a retry increase.
False stale alerts trigger clock/atomic-write/debounce analysis. Restart storms
open a circuit breaker and require operator review. Recovery optimization may
reduce process startup time but cannot bypass revision or ownership checks.

### 8. Evidence And Synchronization

Persist heartbeat series, process identity, lease/fencing decisions, stage
audit, restart timeline and postconditions under Scenario D. Sync the four
systems after foundation implementation, non-disruptive suite, each failed RCA,
and final approved recovery proof.

### 9. Interview Positioning

Explain PID reuse, heartbeat versus process existence, fencing tokens,
at-least-once execution, idempotency, revision skew, split brain, restart storm
control, and why this Windows supervisor is not distributed control-plane HA.

### D Exit Criteria

Worker/observer health and revision are trustworthy, stage mutation is
idempotent across restart, and the incident/alert timeline is deterministic.
B cannot run a live canary unless both A and D exits pass.

Scenario D exit is satisfied for local host-process recovery. This does not
remove B's separate single-GPU dual-model admission and maintenance boundaries.

## Scenario B: Invalid Model Canary And Rollback

Issue: `EVM-267 / SCRUM-173`
Runbook: `docs/runbooks/operational-scenario-b-invalid-model-canary.md`

### 1. Baseline And Gap

Deployment intents, approval, CT gates and exact rollback exist. EVM-244 router,
bounded assignment, metric windows, stop policy and reproducible canary evidence
remain unimplemented. The single exclusive GPU prevents two independent
GPU-requesting Pods from running concurrently.

### 2. Additions

- versioned route policy and stable/challenger immutable identities;
- paired shadow replay and bounded assignment ledger;
- offline labeled CT guard for quality; runtime guard for error and latency;
- deterministic stop decision and zero-allocation containment;
- exact rollback tied to the pre-canary stable digest;
- canary decision metrics, alerts, Grafana panels and Control Panel evidence;
- co-residency admission benchmark before any dual-model same-GPU design.

### 3. Units And Dependencies

B0 uses A as a baseline reference and admits non-disruptive implementation.
Scenario D exit is required only for live production routing. B1 implements
EVM-244 policy/router contracts. B2 adds shadow replay. B3 adds metric-window
evaluation and stop state. B4 binds rollback evidence. B5 adds evidence
validation. B6 runs the isolated controlled replay. B7 records the truthful
closure. B8-live remains blocked until D, GPU admission and maintenance approval
pass. The exact B0 contract is
`docs/status/2026-08-02-scenario-b-controlled-canary-contract.md`.

### 4. Validation Stages

**Non-disruptive:** deterministic router tests, archived/recorded inference
replay, real sequential shadow comparison, policy/metric-window tests,
stop/rollback dry-run, and co-residency memory benchmark planning.

**Approval required:** only after the benchmark. If stable/challenger fit within
an approved VRAM headroom policy, a single-process dual-model operational
canary may be used and explicitly labeled as sharing one failure domain. If not,
live concurrent canary is `blocked` until another compatible GPU or approved
GPU sharing exists. Sequential replay is not renamed as canary.

### 5. SLI And Local SLO Targets

| SLI | Target | Measurement |
|---|---:|---|
| shadow paired requests | >=500 | same immutable request manifest |
| canary allocation | <=10% | assignment ledger; minimum 1,000 total and 100 challenger requests |
| runtime breach stop | <=30 s after evaluable window | decision timestamp to challenger allocation zero |
| exact rollback | <=300 s | stop decision to stable identity and postconditions |
| assignment/response identity | 100% | route ledger joined to response model digest |

Quality is decided from labeled isolated CT before routing. Unlabeled online
replay cannot prove model accuracy.

### 6. Acceptance

Shadow never changes the response path; production canary does not start without
A/D, CT, readiness, approval and rollback; isolated replay allocation stays
bounded; sample/window
evidence is complete; a real configured guardrail breach stops traffic; exact
stable identity returns; shared-failure-domain or hardware-blocked status is
explicit; no business A/B claim appears.

### 7. Redesign And Optimization Loop

Insufficient samples extend the replay window, not the conclusion. Unstable
latency is stratified by warmup, model, batch and GPU contention. OOM or unsafe
co-residency blocks the mode and triggers capacity/design review. False stop
decisions require threshold/window sensitivity analysis without lowering
policy to force a pass.

### 8. Evidence And Synchronization

Persist route policy, assignment ledger, request manifest, CT decision,
latency/error samples, stop event, deployment intent and rollback identity.
Sync four systems after router/evaluator implementation, shadow validation,
co-residency decision, every failed run/RCA, and final passed or hardware-blocked
claim.

### 9. Interview Positioning

Explain shadow, canary and A/B differences; online versus labeled offline
quality; sample/window design; metric peeking; GPU exclusivity; shared failure
domains; immutable rollback; and why controlled replay is not user A/B.

### B Exit Criteria

Bad-candidate containment and exact rollback are deterministic. A truthful live
mode passed, or the canary is explicitly hardware-blocked with shadow/router
evidence only. E implementation may proceed independently, but C limited
deployment cannot close without B's safe release path.

## Scenario E: Data And Artifact Integrity

Issue: `EVM-270 / SCRUM-176`
Runbook: `docs/runbooks/operational-scenario-e-integrity-gate.md`

Execution update `2026-08-02`: E0 is fixed in
`docs/status/2026-08-02-scenario-e-data-artifact-integrity-contract.md`.
Implementation and non-disruptive proof remain open. The contract separates
manifest byte checksums from semantic dataset identity and adds an Ed25519
local trust root, exact legacy-exception scope and zero-intent admission fence.

### 1. Baseline And Gap

Readiness already checks contracts, split, lineage, MLflow, model card and
digests. Missing are one corruption harness, stable blocker taxonomy, canonical
immutability proof, deterministic fingerprints, downstream no-mutation proof,
performance measurement and operator-visible alert/evidence state.

### 2. Additions

- isolated corruption builder under the F-drive test root;
- streaming record/digest checks and exact leakage identity output;
- lineage graph consistency and MLflow/model-card/served-artifact join;
- admission fence before training, promotion and deployment queue creation;
- alerts for integrity blocker and canonical identity change;
- dashboard/UI blocker category, affected identity and evidence link.

### 3. Units And Dependencies

E0 consumes shared evidence/policy. E1 defines blocker enum and fixtures. E2
implements isolated corruption generation. E3 integrates validators. E4 binds
admission fences. E5 adds metrics/alerts/UI. E6 runs the real VisA manifest and
all isolated corruption cases three times.

### 4. Validation Stages

All E injections are non-disruptive isolated copies. No canonical file or
approved artifact is writable. An approval is required only if a future test
targets an external object-store version or retention action; that is outside
the current plan.

### 5. SLI And Local SLO Targets

| SLI | Target | Measurement |
|---|---:|---|
| corruption detection | 100% matrix | expected blocker versus observed blocker |
| canonical false positive | 0 | known-good VisA admission, three runs |
| downstream mutation | 0 | task/intent ledgers before and after |
| deterministic decision | 100% | same input digest yields same fingerprint/blocker |
| VisA admission time | <=max(120 s, 1.5x measured baseline) | cold plus three warm wall-clock runs on F drive |

### 6. Acceptance

Every duplicate, leakage, missing record/shard, manifest digest, lineage,
MLflow/model-card and artifact mismatch maps to its stable blocker; canonical
digests are unchanged; no downstream task is queued; corrected isolated copies
pass; performance target and evidence validator pass.

### 7. Redesign And Optimization Loop

Any false negative is P0 and blocks C. False positives require canonicalization
and identity-rule review. Slow validation is profiled by I/O, parsing, hashing
and join; optimize with streaming, cached immutable digests or partitioned
indexes while preserving exact results. Never skip a check to meet latency.

### 8. Evidence And Synchronization

Persist corruption recipe/seed, parent/output digests, affected identities,
blocker, lineage diff, ledger diff, timing profile and canonical postcheck.
Sync after taxonomy/harness implementation, full non-disruptive matrix, every
false positive/negative RCA, optimization, and final E exit.

### 9. Interview Positioning

Explain data contracts, cross-split leakage, content versus manifest digests,
lineage joins, artifact supply chain, fail-closed placement, performance versus
correctness, and why this is not organization-wide cryptographic attestation.

### E Exit Criteria

Trusted canonical input passes, every isolated corruption fails deterministically,
no downstream mutation occurs, and validation performance is measured. C
cannot start its derived-data proof before E exits.

## Scenario C: Quality Degradation And Retraining Gate

Issue: `EVM-268 / SCRUM-174`
Runbook: `docs/runbooks/operational-scenario-c-quality-degradation.md`

Execution update `2026-08-02`: the admitted non-disruptive scope passed with
real VisA CUDA evidence, one idempotent review event/candidate, manual hold,
zero deployment intents, and unchanged production B0. Closure is
`docs/status/2026-08-02-scenario-c-quality-degradation-closure.md`. The release
branch (real candidate training, MLflow, isolated CT, and limited deployment)
remains blocked by Scenario E and is not part of this pass.

### 1. Baseline And Gap

Measured drift state, `review_required` workflow, diagnostics and no-auto-retrain
policy exist. Missing are a versioned shift generator, baseline window registry,
event idempotency across retries, candidate creation linkage, isolated CT and
limited-release handoff evidence, sensitivity analysis and complete monitoring.

### 2. Additions

- deterministic VisA derived-shift recipes and lineage;
- immutable baseline/window/method/threshold policy registry;
- measured batch evaluator with deduplicated event fingerprint;
- governed retraining-candidate profile and MLflow lineage;
- approval and limited-release linkage to B;
- drift/event/candidate/approval metrics, alerts, dashboard and UI timeline.

### 3. Units And Dependencies

C0 requires E exit and B release controls. C1 implements shift recipes. C2
implements baseline/window registry. C3 hardens event fingerprint/idempotency.
C4 creates candidate profile without queueing production. C5 links evaluation,
isolated CT and approval. C6 adds metrics/alerts/UI. C7 runs sensitivity and the
approved controlled degradation workflow.

### 4. Validation Stages

**Non-disruptive:** generate derived copies, validate lineage/integrity, compare
known no-shift and shifted batches, replay identical events, create dry-run
candidate profile, and test approval/promotion blockers.

**Approval required:** real candidate training consumes GPU and limited staging
deployment uses the only GPU path. These occur only after E and B exits, with
captured resource and rollback plans. Drift never triggers production mutation.

### 5. SLI And Local SLO Targets

| SLI | Target | Measurement |
|---|---:|---|
| batch decision latency | <=300 s after batch materialization | manifest ready timestamp to signed drift report |
| duplicate event | 0 | same evidence fingerprint replayed three times |
| candidate linkage | 100% | event -> data/profile -> MLflow -> CT identities |
| automatic production mutation | 0 | deployment/task/intent ledger audit |
| no-shift false alert | 0 in controlled baseline suite | repeated deterministic baseline batches |

This is batch operational validation, not real-time production concept-drift
detection.

### 6. Acceptance

Known baseline remains within policy; controlled shift creates one event with
method/window/threshold/slices; repeat is deduplicated; candidate is reproducible;
isolated CT remains uncontaminated; approval is mandatory; limited deployment
uses B controls; production is never automatically replaced.

### 7. Redesign And Optimization Loop

Unstable decisions trigger threshold/window sensitivity and bootstrap analysis,
not hand-tuned passing thresholds. False alerts require baseline segmentation
or seasonal/context policy review. Candidate underperformance returns to data
review or training design. Training duration is recorded but is not hidden by
raising quality criteria or bypassing CT.

### 8. Evidence And Synchronization

Persist shift recipe, lineage, baseline and observed summaries, policy, report,
event transition, candidate profile, MLflow/CT links, approval and limited
release result. Sync after evaluator hardening, non-disruptive sensitivity,
candidate implementation, each failed/RCA cycle, and final C exit.

### 9. Interview Positioning

Explain covariate versus concept drift, delayed labels, reference windows,
threshold sensitivity, event idempotency, human-in-the-loop retraining, CT
isolation, and why drift does not mean automatic deployment.

### C Exit Criteria

The controlled shift is detected reproducibly, review/candidate/CT/approval
lineage is complete, no auto-production mutation exists, and any limited
deployment is governed by B. Only then may cross-scenario work start.

## Cross-Scenario Validation

Start only after A-E exits. Combine at most two bounded conditions per run:

1. D worker restart while E integrity validation is read-only.
2. A serving restart while B router is already contained at stable-only.
3. C review event while D observer heartbeat becomes stale in an isolated
   fixture, proving stale evidence cannot approve a candidate.

The correlation engine must preserve both causal timelines, choose the safer
containment action, avoid duplicate recovery, and link one parent incident to
both scenario reports. Cluster-wide device-plugin corruption remains excluded.

Cross-scenario acceptance requires deterministic precedence, no unapproved
mutation, one recovery owner, exact stable identity and complete linked evidence.

## Failure, RCA And Optimization Record

Every failed or blocked run creates:

- immutable `validation-report.json` with observed versus expected state;
- `rca.md` containing symptom, timeline, impact, root cause, contributing
  factors, action, prevention and residual risk;
- test reproducing the defect before the fix and passing after it;
- `supersedes_run_id` linkage from the next run;
- explicit statement of whether the local objective, design, or implementation
  changed and why.

A task is not marked Done merely because a later retry passed.

## Four-System Synchronization Contract

At each meaningful checkpoint, update all four systems as one bundle:

| Checkpoint | Git | Jira | Notion | Obsidian |
|---|---|---|---|---|
| plan | versioned plan/contract commit and push | master/subtask plan comment; execution stays To Do | design, dependency, boundary | work log, Current Context, retrieval index, graph |
| implementation | code/config/test commit and push | transition scenario to In Progress; implementation evidence comment | architecture and test summary | implementation log and next handoff |
| non-disruptive validation | test/evidence index commit | validation comment; do not mark Done | observed SLI/blockers | command evidence and RCA links |
| failed live run | fix/test commit only after RCA | failed/blocked status and factual comment | failure, cause, action, prevention | immutable failure work log and graph edge |
| passed live run | final code/index commit and push | Done only after acceptance | final evidence and claim boundary | final log, Current Context and index |

Never store secrets, raw credentials, model binaries, datasets, or large logs in
Git, Jira, Notion, or Obsidian. Those remain under the F-drive evidence root.

## First Implementation Backlog After Approval To Proceed

1. `A0`: evidence models and validator.
2. `A1`: atomic state/lease/approval policy.
3. `A2-A3`: runtime collectors and target-scoped health.
4. `A4`: low-cardinality metrics, rules and dashboard contract.
5. `A5-A6`: read-only CLI and non-disruptive reconciliation validation.
6. `A7`: approval package and rollback capture.
7. Stop and report. Do not perform `A8` live Pod restart until explicit
   maintenance approval is recorded.

## Stage 2 Plan Exit

- this plan is committed and pushed;
- Jira master and all scenario subtasks contain the same execution boundary;
- Notion and Obsidian point to the same commit and statuses;
- A0-A7 backlog and acceptance are reported to the coordinator;
- implementation and live fault injection remain not started.
