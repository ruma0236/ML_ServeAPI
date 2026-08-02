# Operational Failure Validation Master Plan

Date: 2026-08-01
Status: A-E independently complete; cross-scenario correlation/recovery
planning and design validation in progress; final VisA operations drill not
started.
Parent epic: `EVM-EPIC-21 / SCRUM-156`
Master issue: `EVM-265 / SCRUM-171`
Stage 2 development plan:
`docs/status/2026-08-01-stage-2-operational-reliability-development-plan.md`
Active Jira timebox: Sprint `178`, `EVM S2 A-E 2026-08-01~08-02`, ending
`2026-08-02 23:59 KST`. `SCRUM-171` is the directly scheduled parent and
`SCRUM-172..176` inherit the sprint without hierarchy or status changes.
This is not an A8/live-mutation completion commitment.

## Objective

Turn the project from a collection of successful lifecycle proofs into a
reproducible operations portfolio: define a failure, observe the right signals,
contain its blast radius, restore an exact known-good identity, measure recovery,
and retain evidence that can be challenged in an interview.

This plan does not expand the number of model domains. Operational reliability
evidence precedes BANKING77 and VLM runtime work because a second model domain
would otherwise reproduce the same unproven failure modes.

## Verified Starting Point

The starting baseline is the completed P0 local runtime recovery at commit
`1c6e908798fdeac18aadfd77f1d06ca9fa202ad2`:

- Docker Desktop node GPU capacity/allocatable `1/1`;
- NVIDIA device-plugin `1/1 Ready`;
- `evm-production/evm-b0-production` `1/1 Ready`;
- real VisA CUDA inference: `normal`, confidence `0.998909`;
- Prometheus serving target `up`;
- supervised lifecycle worker and Kubernetes observer with matching revision;
- worker termination and automatic restart evidence;
- focused regression tests `57/57` passing.

This is a local single-node recovery proof. It is not multi-node HA, real user
traffic, or a production SLA.

## Work Breakdown

| Order | ID | Jira | Scenario | Initial state | Dependency |
|---:|---|---|---|---|---|
| 0 | `EVM-265` | `SCRUM-171` | shared contract and governance | In progress | P0 `EVM-264` |
| 1 | `EVM-266` | `SCRUM-172` | A: GPU and serving failure | To do | master contract |
| 2 | `EVM-269` | `SCRUM-175` | D: lifecycle worker/observer failure | To do | master contract |
| 3 | `EVM-267` | `SCRUM-173` | B: invalid model canary and rollback | To do | A and `EVM-244` router |
| 4 | `EVM-270` | `SCRUM-176` | E: data/artifact integrity | In progress | E0 contract fixed; implementation/proof open |
| 5 | `EVM-268` | `SCRUM-174` | C: quality degradation and retraining gate | To do | E, then B for limited release |
| 6 | `EVM-EPIC-22 / EVM-271..275` | `SCRUM-177..182` | cross-scenario correlation and recovery | plan/design PASS; implementation To Do | A-E passed |
| 7 | future child | not created | VisA end-to-end operations drill | Blocked | cross-scenario passed |

Jira execution order follows dependencies rather than issue-number order.

Execution update on `2026-08-02`: A-E now have independent local closure
evidence. The master remains In Progress because cross-scenario validation and
the final VisA operations drill are not complete.

The versioned cross-scenario workstream contract is
`docs/status/2026-08-02-cross-scenario-correlation-recovery-validation-plan.md`.
It is a separate Epic/backlog after A-E and does not alter Sprint 178 or the
Done states of `SCRUM-172..176`. Planning and design validation do not imply
that correlation code, combined fault injection, or runtime mutation exists.
Its read-only design review is
`docs/status/2026-08-02-cross-scenario-correlation-recovery-design-validation.md`.

## Deployment Experiment Vocabulary

| Method | Response path | Assignment | Valid claim in this lab |
|---|---|---|---|
| Shadow | stable only; challenger result is discarded | duplicated controlled request | output/latency compatibility comparison |
| Canary | a bounded fraction can reach challenger | controlled router and replay workload | operational guardrail and rollback validation |
| A/B | distinct user cohorts receive alternatives | sticky randomized assignment | not currently available |

An A/B experiment requires real users, sample-size design, exposure logging,
business metrics, and statistical analysis. The planned local router may test
routing mechanics but must not be described as business A/B evidence.

## Shared Implementation Contract

Before Scenario A executes, Stage 2 must add these fixed implementation units:

| Unit | Planned path | Responsibility |
|---|---|---|
| typed evidence schema | `src/evm/operations/failure_evidence.py` | validate required identity, signal, timing and portfolio fields |
| scenario state store | `src/evm/operations/failure_scenarios.py` | atomic lifecycle and fail-closed transitions |
| configuration | `configs/operations/local_failure_validation.toml` | namespaces, targets, timeouts, evidence root and approval policy |
| executor CLI | `scripts/dev/run_operational_scenario.py` | `plan`, `baseline`, `execute`, `recover`, `validate` |
| evidence validator | `scripts/dev/validate_operational_failure_evidence.py` | deterministic report validation |
| contract tests | `tests/test_operational_failure_evidence.py` | required fields, invalid claims and digest failures |
| state tests | `tests/test_operational_failure_scenarios.py` | safety, idempotency, failure and rollback transitions |

The exact evidence definition is in
`docs/contracts/operational-failure-validation.md`.

## Safety and Approval Policy

- Default target is `evm-staging`; production mutation is disabled.
- Only one scenario may own a resource at a time.
- Baseline, rollback identity and evidence-root checks precede injection.
- Canonical data and approved artifacts are never modified.
- Live device-plugin hostPath mutation is cluster-wide on this machine. It is
  excluded from the default Scenario A run and requires explicit approval plus
  a maintenance window.
- Pod restart, child-process termination and isolated corrupted copies are the
  preferred bounded injections.
- A failure is retained as evidence; it is not rewritten as success after a
  later manual fix.

## Stage 2 Entry Plan: Scenario A

Scenario A is the first implementation target because GPU capacity and serving
availability are dependencies for every later real model validation.

### Read-only Preflight On 2026-08-01

- Git HEAD and remote are
  `be41fb6616e3b8de773bfa957f6c629e396d6cf2`.
- The API, supervisor, worker, and observer still report runtime revision
  `1c6e908798fdeac18aadfd77f1d06ca9fa202ad2`. The intervening commit changes
  documentation only, so Stage 1 records both revisions rather than restarting
  a healthy runtime. Scenario implementation will change executable paths;
  those components must match the new implementation commit before injection.
- Node GPU and device-plugin are `1/1`; production B0 is `1/1 Ready`; its
  readiness endpoint reports CUDA and immutable model SHA
  `abcb8504a36c1128d32021722cfedce6357fd73598a52f6c2a0d60aca9d9a27f`;
  Prometheus target `evm-b0-production` is `up`.
- `evm-b7-serving` is intentionally scaled to `0/0` because production B0 owns
  the single GPU. There is no staging Pod available for the planned default
  restart injection.
- The observer's aggregate `resource_status=fail` includes historical terminal
  failed Jobs and replaced Pods. Scenario admission must evaluate the named
  active target and retain historical failures as context; it must not use the
  unscoped aggregate as the only baseline decision.

Implementation of the schema, safety gate, CLI, validator, and tests can start
without runtime mutation. Live injection remains blocked until a bounded
single-GPU maintenance mode is selected and approved.

### Inputs

- source commit and dirty-state snapshot;
- current node, device-plugin and `evm-b7-serving` runtime state;
- selected immutable VisA model and rollback digest;
- real VisA inference input with source digest;
- Prometheus target baseline;
- P0 evidence index for comparison.

### Default Injection

The preferred live mode is one production B0 Pod restart during an approved
local maintenance window. It preserves the Deployment and model identity while
briefly interrupting the only inference replica. An alternative is a controlled
production-to-staging GPU handover, then one `evm-b7-serving` Pod restart and an
exact handback. Both mutate the only live GPU path and therefore require explicit
approval. A dry-run/stale-manifest fixture exercises device-plugin path
reconciliation logic without changing the live cluster-wide DaemonSet.

### Required Outputs

`baseline.json`, `injection.json`, `kubernetes-events.jsonl`, `signals.jsonl`,
`inference-before.json`, `inference-after.json`, `prometheus-before.json`,
`prometheus-after.json`, `recovery.json`, `validation-report.json`, and a
human-readable `review.md`, each with a SHA-256 entry in `artifact-index.json`.

### Fixed Verification Commands

The following interface is the implementation target, not an already working
claim:

```powershell
python scripts/dev/run_operational_scenario.py plan `
  --scenario gpu-serving `
  --config configs/operations/local_failure_validation.toml
python scripts/dev/run_operational_scenario.py baseline `
  --scenario gpu-serving `
  --config configs/operations/local_failure_validation.toml
python scripts/dev/run_operational_scenario.py execute `
  --scenario gpu-serving `
  --mode staging-pod-restart `
  --config configs/operations/local_failure_validation.toml
python scripts/dev/validate_operational_failure_evidence.py `
  --report <F-drive-run-root>/validation-report.json
python -m pytest tests/test_operational_failure_evidence.py `
  tests/test_operational_failure_scenarios.py -q
```

### Acceptance Criteria

1. Planning and baseline commands are read-only and fail closed when the
   revision, target namespace, rollback identity, or baseline is invalid.
2. The selected Pod loss is detected within 30 seconds.
3. No Deployment specification, model digest, or unapproved resource changes.
4. The Deployment returns `1/1 Ready` within 300 seconds.
5. Post-recovery inference uses CUDA and the exact expected model digest.
6. The Prometheus target returns `up` with no scrape error.
7. All required evidence files exist, are indexed by digest, and validate.
8. A repeated run is idempotent and does not create duplicate active owners.
9. Cluster-wide plugin mutation remains `blocked` without explicit approval.

### Blockers

- baseline GPU/plugin/serving/Prometheus state is not healthy;
- staging B7 serving cannot be scheduled on the single GPU;
- no approved local maintenance mode exists for restarting the only active GPU
  inference replica or handing the GPU to staging;
- the production B0 workload cannot be preserved or safely restored;
- no immutable rollback artifact is available;
- source/API/worker/observer revisions disagree;
- evidence root is unavailable;
- live cluster-wide device-plugin injection is requested without approval.

## Later Scenario Intent

### D: Lifecycle Supervision

Reuse the P0 worker-restart proof, then add observer termination, stale
heartbeat and revision-mismatch tests. Acceptance requires detection after the
20-second stale threshold, one owned child only, revision convergence, durable
restart reason, and no duplicate lifecycle mutation.

### B: Invalid Model Canary

Implement or complete `EVM-244` first. Compare shadow output, then route at most
10% of controlled staging replay requests to an immutable challenger. A quality,
p95-latency or error-rate breach must stop challenger traffic and restore the
exact stable digest within 300 seconds. It is not a business A/B test.

### E: Integrity Gate

Generate only isolated corruptions: duplicate IDs, train/CT overlap, missing
records, manifest digest changes, broken lineage, and wrong model artifact
digests. Every corruption must produce a deterministic blocker before training,
promotion, or deployment is queued.

### C: Quality Degradation

Create a versioned derived VisA dataset with deterministic distribution shift.
The system must emit one deduplicated `review_required` event and a reproducible
retraining candidate. Promotion remains candidate -> evaluation -> isolated CT
-> independent approval -> limited deployment. No drift event may replace
production automatically.

## Cross-Scenario and End-to-End Exit

After A-E pass independently, combine only two bounded failures at a time to
test signal correlation and rollback precedence. The final VisA drill covers:

```text
data intake -> quality/lineage -> training -> MLflow -> isolated CT
-> approval -> staging canary -> real CUDA inference -> Prometheus/Grafana
-> injected failure -> detection -> exact rollback/recovery
```

The final report must include source revision, dataset/split/model/artifact
identities, quality and operational metrics, detection/containment/recovery
times, failed attempts, residual risks, and the local-environment boundary.

Cross-scenario execution is now governed by the separate versioned contract
above. Its safe order is D+E, C+E, B+C, A+D, then A+B. E integrity and D
control-plane freshness are permission-to-act gates; ambiguous identity,
stale evidence, conflicting ownership, or an invalid causal edge must block
mutation. A later live pair requires a new target-bound maintenance approval.

## Portfolio Review Contract

| Evidence | Competency | Interview depth | Claim boundary |
|---|---|---|---|
| A | Kubernetes GPU and serving recovery | GPU allocatable, probes, reconciliation, RTO | local single-node drill |
| D | supervision and idempotency | PID ownership, heartbeat, revision, duplicate work | local host processes |
| B | safe model delivery | shadow/canary/A-B, guardrails, immutable rollback | controlled replay only |
| E | data/model supply-chain integrity | leakage, lineage, digest, fail-closed admission | one-machine data scale |
| C | quality governance | drift window, candidate policy, CT isolation, approval | no real-user concept drift |
| final | end-to-end incident operations | causal timeline, rollback precedence, evidence | not enterprise HA/SLA |

## Stage 1 Exit Criteria

- shared contract and five runbooks are committed and pushed;
- `EVM-265` and its five Jira subtasks link to the same scope;
- Scenario A implementation plan and acceptance criteria are reported before
  any new injection;
- Notion and Obsidian show planning as complete and all executions as not
  started;
- no runtime or production resource was mutated during Stage 1.
