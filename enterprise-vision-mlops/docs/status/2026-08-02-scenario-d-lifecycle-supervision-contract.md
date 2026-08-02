# Scenario D Lifecycle Supervision Contract

Date: `2026-08-02`
Issue: `EVM-269 / SCRUM-175`
State: D0 contract ready; implementation and live proof not started

## Scope And Independence

Scenario D proves local host-process supervision for the lifecycle worker and
Kubernetes observer. Scenario A/P0 recovery evidence is a
`baseline_reference` only. Its restart result, timing, or closure cannot satisfy
any Scenario D acceptance item.

The environment is one Windows host, one Docker Desktop Kubernetes node, one
GPU, and one production serving replica. Valid claims are limited to supervised
local worker/observer recovery. Distributed consensus, multi-node control-plane
HA, zero downtime, production traffic, and enterprise SLA claims are forbidden.

## Current Baseline And Gaps

Observed before D implementation:

- supervisor PID `29360`, observer PID `41932`, and worker PID `32288` are live;
- the supervisor and worker report source `193dd10`, while repository HEAD is
  `e41a910`;
- the observer snapshot does not bind PID, source revision, supervisor lease,
  fencing token, or process start identity;
- ownership checks use PID plus a broad command marker only;
- restart counts are in-memory and reset when the supervisor restarts;
- no persistent restart budget/backoff, circuit breaker, state-transition audit,
  duplicate-process detector, or per-run worker claim exists.

An internally matching old revision is not current-revision convergence. D must
distinguish these states and never report stale code as healthy.

## Child State Machine

Each child is evaluated independently using an ordered signal policy:

`ownership -> duplicate count -> heartbeat identity -> heartbeat freshness -> source revision -> lease/fence`.

States:

| State | Meaning | Allowed action |
|---|---|---|
| `starting` | canonical launcher owns a new child; fresh heartbeat pending | observe |
| `live` | exactly one process and all identities/freshness match | none |
| `suspect` | first stale/invalid sample inside debounce | observe; never report live |
| `recovering` | exact owned target is stopped or restartable | exact-target restart |
| `backoff` | retry is eligible but delay has not elapsed | wait |
| `blocked` | unknown owner, duplicate process, identity ambiguity, or invalid lease | no mutation |
| `circuit_open` | restart budget exhausted | operator review |

Zero or multiple exact targets fail closed. A PID that resolves to an unrelated
command is never terminated. A duplicate-owned-process observation blocks
global cleanup; D does not kill all matching processes.

## Identity, Lease, And Fencing

The supervisor owns one persistent lease containing:

- supervisor PID and process start time;
- source commit/branch;
- random lease ID;
- monotonically increasing fencing token;
- creation and last-renewed UTC timestamps.

Worker and observer heartbeats bind child PID, process start time, source
revision, supervisor lease ID, and fencing token. A replacement supervisor
increments the token. Heartbeats from an older token are stale owners and are
never accepted as live.

Lifecycle stage execution uses a per-run claim with worker PID, worker ID,
source revision, lease ID, fencing token, claim time, renewal time, and expiry.
Only the current claim may execute. Duplicate attempts are skipped and audited;
an expired claim may be replaced with a higher fencing token. This is
at-least-once execution with idempotent/fenced mutation, not exactly-once
delivery.

## Heartbeat And Restart Policy

- supervisor check cadence: `5 s`;
- worker heartbeat cadence: `5 s`;
- observer heartbeat cadence: `5 s`;
- stale threshold: `20 s`;
- stale debounce: `2` failed samples;
- restart budget: `3` attempts per child per `300 s`;
- backoff: `1 s`, `2 s`, then `4 s`;
- budget exhaustion: `circuit_open`, no automatic restart;
- future, malformed, unknown-owner, duplicate, or ambiguous state: fail closed.

Restart history and state changes are append-only audit records. Replaying the
same incident fingerprint is idempotent and does not consume another restart.

## Fixture Matrix

| Fixture | Expected decision | Mutation |
|---|---|---|
| stopped worker | `restart_exact` | worker only |
| stopped observer | `restart_exact` | observer only |
| stale heartbeat | `suspect`, then `restart_exact` | exact child only |
| source revision mismatch | `restart_exact` | exact old-revision child only |
| prior supervisor lease/fence | `restart_exact` | exact fenced child only |
| duplicate owned processes | `blocked_duplicate` | none |
| stale PID pointing to unrelated process | `blocked_unknown_owner` | none |
| heartbeat PID/process-start mismatch | `blocked_identity` | none |
| exhausted restart budget | `circuit_open` | none |
| duplicate incident replay | existing decision/audit reused | none |

## Live Proof Boundary

Allowed only after all fixtures pass and preflight proves:

- clean repository-local worktree and pushed source revision;
- exactly one supervisor, worker, and observer with matching lease/fence;
- no active or queued lifecycle mutation;
- exact PID, process start time, executable, and command ownership;
- current repository/supervisor/child revision convergence;
- production B0 exact UID/model remains `1 / 1 Ready` and Prometheus is up;
- rollback is the canonical supervisor start path.

Live injection may terminate one exact worker or observer PID at a time. It may
not stop production B0, the NVIDIA device plugin, Docker Desktop, databases,
the API, data processes, unrelated user processes, or cluster-wide resources.

Three independent child-termination runs are planned: worker, observer, worker.
Every run has a fresh run ID and cooldown. Any ambiguous identity, active work,
budget exhaustion, or unexpected process count blocks `Stop-Process`.

## SLI And Local SLO

| SLI | Target | Measurement |
|---|---:|---|
| stopped-child detection | `<=10 s` | kill monotonic point to incident audit |
| stale-heartbeat detection | `<=25 s` | last heartbeat to incident audit |
| child recovery | `<=60 s` | incident to fresh matching heartbeat |
| heartbeat cadence | `<=7.5 s` p95 in proof window | UTC/monotonic sample series |
| exact target identity | `100%` | PID/start/command/revision/lease/fence |
| source convergence | `100%` | supervisor and both children equal proof revision |
| false restart | `0` | unaffected child PID/restart count |
| duplicate process | `0` | exact command process census |
| duplicate stage mutation | `0` | claim/fencing/idempotency audit |
| production mutation | `0` | B0 UID/model and Prometheus pre/post |

These are controlled local measurements, not production SLOs.

## Alert And Audit Policy

Metrics and API state must expose supervisor status, child state/reason,
heartbeat age, revision match, lease/fence match, process count, restart count,
budget remaining, and circuit state. Alert conditions are:

- child not live for two checks;
- heartbeat older than `20 s`;
- source revision or lease/fence mismatch;
- duplicate/unknown owner;
- restart budget remaining `0`;
- supervisor heartbeat stale.

Every detection, decision, restart attempt/result, recovery, block, claim, and
claim conflict records UTC time, monotonic elapsed value where applicable,
incident fingerprint, exact target identity, reason, source revision, lease,
and fencing token.

## Acceptance

- every fixture returns the exact expected state/reason/action;
- duplicate and stale PID fixtures perform no termination;
- same incident replay is idempotent;
- persistent restart budget/backoff and circuit breaker pass;
- worker run claims prevent duplicate stage ownership;
- API/Prometheus never report stale or mismatched child state as healthy;
- each approved live run restarts only the selected child;
- detection/recovery targets pass, unaffected child PID stays unchanged, and
  duplicate process/stage mutation remain zero;
- supervisor/worker/observer converge to the executable source revision;
- production B0, device plugin, data, and cluster resources remain unchanged;
- common operational evidence and all artifact hashes validate.

## Evidence And Portfolio Boundary

Original evidence root:
`F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/scenario-d`.

Each run preserves policy, preflight, process census, heartbeat series,
incident/audit timeline, lease/fencing/claim state, restart ledger, API and
Prometheus observations, postconditions, common report, and SHA-256 index.
Failed runs remain immutable and are never relabeled as passing evidence.

Supported portfolio evidence: local host-process state-machine design,
heartbeat/lease semantics, PID-reuse safety, revision convergence, restart
budgeting, fenced at-least-once work, exact-target recovery, and failure RCA.

Unsupported claims: distributed leader election, quorum, multi-node failover,
production traffic resilience, zero downtime, HA, or enterprise SLA compliance.
