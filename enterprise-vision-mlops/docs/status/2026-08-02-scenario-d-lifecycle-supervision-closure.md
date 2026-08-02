# Scenario D Lifecycle Supervision Closure

Date: 2026-08-02
Issue: `EVM-269 / SCRUM-175`
Decision: PASS for the admitted single-node local host-process scope

## Scope

Scenario D validates the Windows-host lifecycle worker and Kubernetes observer
under one canonical supervisor. It covers exact process identity, heartbeat
freshness, source revision, lease/fencing, restart budget/backoff, duplicate
guards, per-run claims, observability, and bounded child-only recovery. Scenario
A/P0 evidence is retained as a baseline reference and does not close D.

## Implemented System

- strict child state machine with PID, Win32 process-start time, command marker,
  process instance, source revision, supervisor lease, and fencing identity;
- zero/multiple/unknown-owner and PID-reuse ambiguity fail closed;
- persistent restart ledger, three-attempt/300-second budget, 1/2/4-second
  backoff, circuit-open state, and duplicate-incident replay protection;
- fenced lifecycle-run claims with epoch, renewal, expiry, and release;
- supervised integrated startup, heartbeat/revision injection, and exact-child
  launch/recovery;
- Control Panel runtime-health API, low-cardinality Prometheus metrics, five
  alert rules, and four Grafana runtime panels;
- immutable fixture and live-proof runners with single-use approval binding,
  monotonic timing, real CUDA safety checks, common evidence reports, and
  SHA-256 indexes.

Implementation commits are `15e0850`, `30362ea`, `8eadbda`, `336322c`,
`398c3c2`, and executable closure revision `37ec89d`.

## Non-Disruptive Evidence

Fixture run `scenario-d-fixtures-20260802T065643Z-8eadbdaf` passed all `13 / 13`
state/action cases, lifecycle claim guards, readiness closure, and `4 / 4`
artifact hashes. Cases include stopped worker/observer, stale heartbeat,
revision mismatch, prior lease/fence, duplicate processes, stale unrelated PID,
identity mismatch, exhausted restart budget, and duplicate incident replay.

## Failure And RCA Evidence

1. Runtime convergence at `8eadbda` failed closed as `blocked_identity`.
   `GetProcessTimes` had no explicit ctypes signatures, so a fallback timestamp
   differed from the OS process start. Commit `336322c` binds the Win32 API and
   adds regression coverage.
2. Initial D8 series `scenario-d-series-20260802T081243Z-398c3c2e` recovered all
   exact targets but is superseded, not closure. Independent review found
   heartbeat p95 was recorded but omitted from mandatory acceptance; its
   cross-outage deltas were `8 / 9 / 11 s`. Commit `37ec89d` requires two
   post-recovery heartbeat intervals and series-level acceptance. The detailed
   RCA is `docs/status/2026-08-02-scenario-d-heartbeat-closure-rca.md`.

Neither failure changed production B0, the NVIDIA device-plugin, source data,
or cluster resources.

## Approved Live Proof

Authoritative series:
`scenario-d-series-20260802T082205Z-37ec89d6`.

| Run | Exact target | Detection | Recovery | Healthy heartbeat p95 |
|---|---|---:|---:|---:|
| 1 | lifecycle worker PID `46496` | `5.870 s` | `9.049 s` | `5.0 s` |
| 2 | Kubernetes observer PID `19100` | `2.546 s` | `7.456 s` | `5.0 s` |
| 3 | lifecycle worker PID `47388` | `5.062 s` | `8.232 s` | `5.0 s` |

Every run passed `10 / 10` required postconditions, consumed one exact expiring
approval binding, and independently validated `9 / 9` indexed artifacts.
Common `live_proof` validation returned zero errors for all three reports.
Maximum detection, recovery, and recovered heartbeat p95 were `5.870 s`,
`9.049 s`, and `5.0 s`, within `10 / 60 / 7.5 s` targets.

The unaffected child PID stayed unchanged in each run, exact command process
counts returned to one, active lifecycle runs and claims remained zero, and
the supervisor plus both children converged to `37ec89d` under one lease/fence.

## Production And Observability Postconditions

- production Deployment UID remained
  `cfdab424-dcc5-4d5f-a46f-ae7530441ef4`, `1 / 1 Ready`;
- model SHA remained
  `abcb8504a36c1128d32021722cfedce6357fd73598a52f6c2a0d60aca9d9a27f`;
- pre/post VisA sample inference returned `normal` on real CUDA;
- Docker Desktop GPU allocatable and device-plugin remained `1 / 1`;
- production and `evm-api` Prometheus targets remained `up`;
- rebuilt API image `sha256:49f1104e7e658aad3e2f2863299528996b154c54f8701490a088d0c1ab7258cc`
  exposes healthy supervisor/children at
  `/control-panel/v1/runtime-supervisor`;
- Prometheus scrapes `evm_control_panel_runtime_supervisor_healthy = 1`; five
  host-runtime alert rules loaded, evaluated healthy, and remained inactive;
- Grafana's versioned `EVM Operational Reliability` dashboard contains runtime
  supervisor, child state, heartbeat age, and process/restart panels. Its file
  is mounted through provisioning; authenticated dashboard API inspection was
  not performed because the active password is not the repository default.

Verification: `39 / 39` Scenario D and control-plane tests pass, the full
repository suite passes `389 / 389`, and Ruff passes for all touched Python
modules.

## Portfolio Boundary

Factual claim: a controlled local failure experiment recovered one exact
supervised worker or observer at a time, enforced identity and restart safety,
measured detection/recovery/cadence, preserved production CUDA serving, and
published API/Prometheus/Grafana observability with immutable evidence.

Do not claim distributed consensus, multi-node HA, zero downtime, real-user
traffic resilience, business A/B impact, or enterprise SLA compliance. The
supervisor is Windows-host local, the cluster is single-node, the GPU is single,
and no production traffic generator participated.

## Interview Review

- Why PID alone is unsafe and how process-start plus command ownership prevents
  PID-reuse termination.
- Why heartbeat, process existence, source revision, lease, and fencing are
  separate signals with deterministic precedence.
- Why at-least-once execution requires idempotency claims and why this is not
  exactly-once delivery.
- Why restart budgets fail closed and why the first valid-looking evidence
  series was rejected after an independent acceptance audit.
