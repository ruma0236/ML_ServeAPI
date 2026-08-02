# Cross-Scenario Correlation And Recovery Validation Plan

Date: 2026-08-02
Status: plan complete; design validation PASS for implementation planning;
implementation, fault injection, and runtime mutation not started.
Parent program: `EVM-265 / SCRUM-171`
Workstream epic: `EVM-EPIC-22 / SCRUM-177`
Planning issue: `EVM-271 / SCRUM-178`
Design validation:
`docs/status/2026-08-02-cross-scenario-correlation-recovery-design-validation.md`

## Objective

Prove that the independently closed operational scenarios A-E can be observed
and coordinated without losing exact identity, merging unrelated events,
duplicating recovery, or allowing an untrusted signal to authorize mutation.

This is a separate workstream after A-E. It does not reopen or rewrite their
independent closure evidence. This turn defines the contract and validates the
design only. No correlation code, fault injection, Kubernetes action, process
termination, release routing change, data mutation, or production mutation is
authorized by this plan.

## Verified Baselines

The following evidence is a baseline reference only. A child scenario pass is
not a cross-scenario pass.

| Scenario | Jira | Independent evidence | Measured local result |
|---|---|---|---|
| A: GPU/serving | `EVM-266 / SCRUM-172`, Done | three UID-bound production B0 recovery runs | detection `0.170-0.200 s`; recovery `10.082-25.087 s`; exact CUDA identity `21/21` |
| B: bad release | `EVM-267 / SCRUM-173`, Done | controlled replay and stable-route containment | `1,000` observations per run; exact routing `100/1,000`; recovery `0.0392-0.0498 s` |
| C: quality/drift | `EVM-268 / SCRUM-174`, Done | real CUDA windows and deterministic shift | windows `2,136 / 2,181 / 205`; one held candidate; zero deployment intents |
| D: lifecycle | `EVM-269 / SCRUM-175`, Done | exact worker/observer/worker recovery | detection max `5.870 s`; recovery max `9.049 s`; no duplicate process |
| E: integrity | `EVM-270 / SCRUM-176`, Done | real VisA identities and isolated corruptions | `14` fixtures, `42` replays, corrupted `39/39` blocked, zero intent delta |

The source baseline commit is
`ecbf52f91475abe5c63e6edd87e27667741651f1`. Later planning commits may update
the documentation revision, but must not be represented as executable runtime
evidence.

## Environment And Claim Boundary

- One Windows host, Docker Desktop Kubernetes, one GPU, and one production B0
  replica.
- B7 staging is normally `0/0`; replay is controlled and local.
- There is no real customer traffic, multi-node failover, business A/B test,
  distributed control-plane HA, enterprise KMS/Sigstore, or contractual SLA.
- The valid future claim is bounded local incident-correlation and recovery
  coordination over real prior A-E evidence plus deterministic fixtures and
  controlled replay.
- The invalid claim is autonomous enterprise remediation or production HA.

## Explicit Exclusions During Planning And Initial Implementation

- cluster-wide NVIDIA device-plugin mutation;
- production B0 Pod restart or Deployment change;
- worker or observer process termination;
- staging B7 rollout or production traffic routing;
- source VisA data, canonical manifests, model artifacts, or MLflow mutation;
- automatic retraining, promotion, deployment, or rollback;
- closing `EVM-265`, changing Sprint 178, or changing A-E Done states.

## Workstream Breakdown

| Order | Internal ID | Jira | Unit | State after this turn | Dependency |
|---:|---|---|---|---|---|
| 0 | `EVM-271` | `SCRUM-178` | master contract and independent design validation | Done after final sync | A-E independent closure |
| 1 | `EVM-272` | `SCRUM-179` | normalized event, identity, causality, correlation and dedupe engine | To Do | `EVM-271` PASS |
| 2 | `EVM-273` | `SCRUM-180` | fail-closed recovery ownership plus read-only incident API/metrics/UI | To Do | `EVM-272` |
| 3 | `EVM-274` | `SCRUM-181` | non-disruptive pairwise fixtures and controlled replay evidence | To Do | `EVM-272..273` |
| 4 | `EVM-275` | `SCRUM-182` | maintenance-gated live pair and cross-scenario closure | To Do, approval gated | `EVM-274` PASS |

The new workstream is intentionally not added to Sprint 178. A separate future
timebox may be created only after the implementation backlog is reviewed.

## Normalized Event Contract

Every source event must conform before correlation:

```text
event_id
correlation_id
causation_id
parent_incident_id
scenario_id
event_type
cause_code
severity
observed_at_utc
monotonic_elapsed_ms
collector_cadence_ms
fresh_until_utc
source_revision
policy_version
evidence_digest
semantic_identity_digest
subject_identity
actor_or_controller
recommended_action
```

### Identifier Semantics

- `event_id`: globally unique immutable event instance.
- `correlation_id`: UUIDv7 allocated by the correlation coordinator when a
  root incident is admitted. Producers may propose but cannot overwrite it.
- `causation_id`: exact parent event that caused this event. Empty is allowed
  only for an admitted root observation.
- `parent_incident_id`: immutable incident envelope that owns the causal DAG.
- IDs are never inferred only from timestamps, log text, labels, or severity.
- `semantic_identity_digest`: canonical digest of stable decision inputs. Raw
  collection time, volatile log fields, and output-file bytes are excluded.
- `evidence_digest`: hash of the exact raw evidence object retained for audit.
  It is not used as the idempotency key because timestamps and collection
  metadata may legitimately differ across equivalent observations.

The coordinator stores a durable `root_fingerprint -> parent_incident_id`
index. Incident creation is an atomic compare-and-create operation. A replay or
coordinator restart therefore resolves the existing open incident instead of
allocating another UUIDv7. The root fingerprint and every state transition are
included in the append-only decision ledger.

### Exact Subject Identity

The subject tuple is typed by scenario and includes every relevant field:

- data: dataset version, source manifest digest, split manifest digest, CT
  digest, lineage root, and record/shard identity;
- model/release: MLflow run, candidate ID, model artifact digest, image digest,
  policy digest, stable/challenger role, and environment;
- Kubernetes: cluster, namespace, kind, name, UID, pod UID, container/image
  digest, and expected replica identity;
- lifecycle: host, supervisor revision, process role, PID, process start time,
  command digest, lease ID, and fencing token;
- evidence: scenario run ID, source revision, schema version, artifact index
  digest, and validation report digest.

Missing required identity, zero target matches, multiple target matches,
revision mismatch, stale evidence, invalid hash closure, or an unsupported
schema version blocks mutation.

### Timing And Freshness

- Every producer emits `producer_boot_id`, monotonically increasing
  `producer_sequence`, `observed_at_utc`, and local monotonic elapsed time.
- The coordinator records `ingested_at_utc` and its own monotonic elapsed time.
- Initial local policy uses a `5 s` collector cadence, `20 s` active-signal
  freshness budget, `30 s` correlation decision deadline, `300 s` closed-event
  recurrence window, and at most `2 s` tolerated wall-clock offset.
- Required and optional signals are declared per combination rule. If a
  required signal is absent, stale, or contradictory by `30 s` after the first
  admissible root event is ingested, the incident enters `held/blocked`; the
  coordinator does not wait indefinitely or infer success.
- UTC supports audit ordering. Durations and SLOs use one process's monotonic
  clock. Cross-producer ordering uses sequence, causal IDs, and coordinator
  ingestion, never wall-clock proximity alone.

## Signal Precedence

This order governs permission to act, not whether an alert is important:

1. **E trust and integrity:** an untrusted data, model, image, CT, registry, or
   evidence identity denies all dependent mutation.
2. **D control-plane freshness and ownership:** stale heartbeat, revision
   mismatch, duplicate ownership, or invalid lease freezes mutation and permits
   only exact-owner recovery.
3. **A active serving health:** restore an exact known-good serving identity
   only after E and D trust gates pass.
4. **B active release guardrail:** contain challenger allocation and restore
   the exact stable route; it cannot replace A or D recovery ownership.
5. **C quality/drift review:** create or preserve a manual hold and candidate;
   it never authorizes production mutation.

Containment actions may run in parallel only when target identities and leases
are disjoint. Every mutation target has one active recovery owner. The
correlation coordinator grants or denies a lease and records ordering; it does
not replace the specialized A-E controllers.

### Deadlock-Free Containment Boundary

Trust gates distinguish risk-reducing containment from risk-increasing
mutation:

- An E failure blocks training, candidate admission, rollout, promotion, and
  model/artifact replacement. It does not block D from restarting one exact
  supervisor-owned worker/observer when the process lease, command digest, and
  revision are independently valid.
- A D freshness/ownership failure blocks data/model/release/serving mutation.
  D may recover only its exact fenced child so that fresh evidence can resume.
- B may reduce challenger allocation to zero using a pre-bound stable identity
  even while other gates are held; it may not increase allocation or deploy.
- C may emit a non-mutating review/hold event while E or D is blocked, but may
  not create training, promotion, or deployment intent.
- A may restore only a pre-captured, immutable known-good serving identity when
  that rollback trust root and exact target approval remain valid. If that
  identity is implicated by E or target ownership is ambiguous, A also stops.

These exceptions are narrow, monotonic risk-reduction actions. Each requires an
exact owner lease and audit record; none permits a new candidate or broader
resource mutation.

## Causality And Alert Dedupe

### Causality Rules

- The parent incident stores a directed acyclic graph of events and actions.
- A causal edge requires exact identity compatibility, a declared dependency,
  and a valid evidence reference. Time proximity alone is insufficient.
- A cycle, missing parent, conflicting root cause, or ambiguous identity creates
  a separate held incident and blocks mutation.
- A later symptom may be attached to an existing incident only when its
  producer supplies the causal parent or a deterministic rule proves the edge.

### Dedupe Fingerprint

```text
sha256(
  policy_version + scenario_id + event_type + cause_code +
  exact_subject_identity + semantic_identity_digest + source_revision
)
```

- Replays with the same fingerprint remain one event while its incident is
  active. A closed event recurring within the initial local `300 s` window is
  linked as a recurrence; after the window it creates a new event with an
  explicit `recurs_from_event_id` edge, never a silent merge.
- Events close in time but with different exact identities remain separate.
- A deduped alert count, first/last observed timestamps, and source event IDs
  remain queryable without multiplying recovery actions.

## Recovery Ownership And Fail-Closed Policy

| Domain | Authorized owner | Permitted action in initial phases | Forbidden action |
|---|---|---|---|
| E integrity | integrity admission controller | hold/quarantine isolated input; deny intent | mutate canonical data/artifacts |
| D lifecycle | supervisor for exact process lease | recover exact owned worker/observer | global process restart or PID-only kill |
| A serving | exact-target serving recovery controller | plan and validate known-good recovery | cluster-wide or ambiguous target action |
| B release | release guardrail/controller | set challenger allocation to zero in replay | business A/B or unapproved production rollout |
| C quality | quality review controller/operator | emit review and manual hold | automatic training/promotion/deployment |
| Cross-scenario | correlation coordinator | correlate, order, fence, recommend, and audit | execute domain mutation without owner lease |

An action approval is bound to correlation ID, incident ID, exact target UID or
process lease, action digest, source revision, policy version, expiry, actor,
and single-use nonce. Expired, reused, mismatched, or partial approvals fail
closed. A recovery owner must release or expire its fenced lease before another
controller can act on the same target.

The owner ledger is durable and uses atomic compare-and-set. Each exact target
has a monotonically increasing fencing token. Initial local policy renews an
active owner lease every `5 s` and expires it after `20 s`; an expired owner
cannot commit an action result. A new owner may act only with a higher fencing
token after reconciling the prior action ledger and exact target state.

## Incident State Machine

```text
observed -> normalized -> correlated -> held
held -> contained -> recovery_pending -> recovery_owned
recovery_owned -> recovered -> validated -> closed
any state -> blocked
any mutation-capable state -> rollback_pending -> rolled_back -> validated
```

Only `validated` may transition to `closed`. A failed or blocked attempt remains
immutable and may be superseded by a later run; it is never rewritten as PASS.

## Combination Candidates And Safe Order

| Order | Pair | Mode | Expected precedence | Exit before next |
|---:|---|---|---|---|
| 1 | D + E | deterministic fixture, then read-only replay | stale controller evidence freezes E admission; recovered exact owner replays once | zero duplicate validation/intent; exact lease and evidence DAG |
| 2 | C + E | isolated shifted window plus invalid/stale manifest | E denies candidate admission; C remains one manual review hold | zero training/deployment intent; no source mutation; deterministic blocker |
| 3 | B + C | controlled replay only | B contains challenger while C records review; neither auto-promotes/retrains | exact stable route; one held candidate; separate causal branches |
| 4 | A + D | fixture first; live only under later maintenance approval | D ownership/freshness gates A action; direct A target health avoids global restart | one action owner; no double restart; child SLOs plus coordinator overhead |
| 5 | A + B | controlled replay first; highest-risk live mode deferred | B stops challenger route; A restores known-good serving only after trust gates | stable identity, zero conflicting action, explicit maintenance approval |

The initial executable candidate is D + E because both can be proven with
isolated state and read-only validation before any live process or production
action. A + B is last because a single GPU and single serving replica create the
largest interruption and ownership-conflict risk.

## Cross-Scenario SLI And SLO Contract

| SLI | Planning target | Measurement |
|---|---:|---|
| required identity completeness | `100%` | schema validator over every event/action/evidence edge |
| stale or ambiguous evidence that causes mutation | `0` | intent/action audit delta |
| false causal merge | `0` | negative near-time/unrelated identity fixtures |
| duplicate parent incidents for an idempotent replay | `0` | three independent deterministic replays |
| duplicate recovery action per target/fence | `0` | action ledger and target lease audit |
| correlation decision latency | `<= 30 s` after final required signal | UTC audit plus monotonic elapsed, fixed `5 s` collector cadence |
| coordinator compute overhead | provisional `<= 2 s p95` | measured separately from collection and child recovery |
| parent/child evidence hash closure | `100%` | artifact-index and causal-edge validator |
| production mutation in non-disruptive phases | `0` | before/after intent, Kubernetes, process, and data identities |

Child detection and recovery SLOs remain visible and are never replaced by a
single aggregate duration. Cross-scenario reports record collection delay,
correlation overhead, containment delay, child recovery, and final validation
separately.

The first performance proof uses at least `1,000` normalized events with at
least `100` unrelated near-time negative events in each of three independent
offline replay series. These counts validate only this local test workload;
they are not a production throughput claim.

## Evidence Contract

Each future run is stored outside Git under the F-drive evidence root and must
include:

- `incident.json`: state, identity, policy, owner, approval, and claim boundary;
- `events.jsonl`: normalized source events with UTC and monotonic timing;
- `causal-graph.json`: nodes, edges, edge rule, confidence type, and blockers;
- `identity-map.json`: canonical subject tuples and stable semantic digests;
- `policy-decision.json`: required/optional signals, precedence, deadline, and
  decision result;
- `dedupe-ledger.jsonl`: fingerprint, TTL, source IDs, and count;
- `action-ledger.jsonl`: lease, fencing token, action digest, result, rollback;
- `child-evidence-index.json`: immutable A-E references and digests;
- `signals.jsonl`: target-scoped raw observations and freshness;
- `validation-report.json`: expected versus observed acceptance;
- `artifact-index.json`: SHA-256 closure for every evidence file;
- `review.md` and any `rca.md`: factual outcome, failed attempt, action,
  prevention, limitation, and superseding run.

Git stores schemas, policies, tests, dashboards, summary indexes, and this plan,
not raw data, model binaries, credentials, or large evidence.

## Required Negative Tests

1. Same timestamp, different target UID: must create two incidents.
2. Same target label, missing UID: mutation must be blocked.
3. Same fingerprint replay inside TTL: one event/action, count increments.
4. Same fingerprint after TTL: a new event linked as recurrence.
5. Causal graph cycle or unknown parent: held/blocked, no action.
6. Stale D heartbeat plus healthy cached E report: cached report cannot admit.
7. Invalid E digest plus A/B health signal: serving/release mutation is denied.
8. Conflicting A and B action leases on one target: exactly one owner wins by
   declared precedence; the other records a fenced rejection.
9. Source revision or policy version mismatch: no correlation-assisted action.
10. Approval replay, expiry, or wrong target/action digest: no mutation.
11. Coordinator restart between incident admission and action: the durable root
    index and action ledger return one parent, one owner, and one action.
12. Raw evidence timestamps differ but semantic inputs match: one event is
    retained with multiple raw observation digests.

## Implementation Phases And Gates

### X0: Contract And Fixtures

Add typed event, identity, incident, causal-edge, dedupe, lease, approval, and
evidence schemas plus deterministic positive and negative fixtures. Exit only
when schema closure, anti-correlation, cycle, freshness, and approval tests pass.

### X1: Correlation And Causality Engine

Implement normalization, exact-identity matching, causal DAG construction,
dedupe TTL, precedence decisions, and an append-only decision ledger. Exit only
after three deterministic replay series produce zero false merges, duplicate
parents, or duplicate actions.

### X2: Recovery Coordination And Read-Only Incident Plane

Implement fenced leases, approval binding, recovery recommendations, low-cardinality
metrics, alerts, read-only API, and Control Panel incident timeline. No mutation
endpoint is admitted in this phase. Exit only when stale/ambiguous evidence
blocks, ownership is singular, and API/UI fields match the evidence schema.

### X3: Non-Disruptive Pairwise Proof

Run D+E, C+E, and B+C with isolated fixtures and controlled replay. Production
B0, device-plugin, host processes, and canonical data remain unchanged. Each
pair needs three independent runs, one negative anti-merge run, full hash
closure, and immutable failed/RCA history.

### X4: Maintenance-Gated Live Proof

A+D and A+B may proceed only after X3 PASS and a new preflight-bound maintenance
approval. Approval must bind exact target UID, rollback identity, action digest,
source/policy revision, expiry, owner, and interruption impact. A failed gate
leaves this phase Blocked, not partially Done.

## Overall Acceptance

1. A-E references are immutable and clearly marked baseline-only.
2. Every event and action has complete exact identity and revision.
3. Causality is explicit; no merge occurs from timing alone.
4. All negative anti-correlation, stale, duplicate, cycle, and approval fixtures
   fail closed with zero mutation intent.
5. Dedupe yields one parent incident and one action per fence across three
   `1,000`-event replays that each include at least `100` unrelated near-time
   negative events and a coordinator-restart fixture.
6. Exactly one recovery owner acts on a target; rejected owners are audited.
7. Signal precedence is deterministic and preserves E/D trust gates.
8. Parent evidence closes over every child report and hash.
9. Coordinator latency and child detection/recovery are measured separately.
10. Non-disruptive pairwise runs leave production, GPU/plugin, processes,
    canonical data, and stable model identity unchanged.
11. Any live action is isolated behind a separate maintenance approval and exact
    rollback preflight.
12. Portfolio statements retain the local single-node and controlled-replay
    boundary.

## Failure And Optimization Loop

Every failed or ambiguous run stops progression and records symptom, causal
timeline, impact, root cause, contributing factors, correction, regression
test, prevention, residual risk, and `supersedes_run_id`. Thresholds or
precedence may change only through a versioned policy and replay of the full
negative fixture suite. A later pass cannot erase an earlier failed run.

## Four-System Synchronization Contract

Each meaningful checkpoint is synchronized as one bundle:

- Git: plan, schema/code/tests, summary index, commit, and pushed branch;
- Jira: exact state, dependency/link, evidence comment, and claim boundary;
- Notion: master/detail context with the same commit and observed result;
- Obsidian: work log, Current Context, retrieval index, hub, and graph edges.

Planning completion is not implementation completion. Future tasks remain To Do
until their own implementation and evidence gates pass.

## Portfolio And Interview Contract

After successful implementation, this work may demonstrate exact identity
propagation, event normalization, causal correlation, alert dedupe, fencing,
single-owner recovery, fail-closed policy, evidence design, and SLI decomposition
in a bounded local MLOps platform.

Expected interview questions include:

- Why is time proximity insufficient to infer causality?
- How do fencing tokens prevent duplicate recovery?
- Why do integrity and control-plane freshness outrank serving recovery?
- How are child RTO and coordinator overhead separated?
- What changes in a multi-node, multi-region, real-traffic system?

Do not claim business A/B, multi-node HA, autonomous production remediation,
enterprise incident response, or SLA compliance without separate evidence.

## Planning Exit

- this plan and the parent master/register are versioned and pushed;
- a separate Jira Epic and planning/implementation issue structure exists;
- A-E Done issues are linked for traceability without status or sprint changes;
- an independent read-only design validation records verdict and remediation;
- Notion and Obsidian point to the same final Git SHA and Jira states;
- implementation, fault injection, and runtime mutation remain not started.
