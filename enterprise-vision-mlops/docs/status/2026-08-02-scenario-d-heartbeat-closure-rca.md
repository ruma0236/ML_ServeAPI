# Scenario D Heartbeat Closure RCA

Date: 2026-08-02
Issue: `SCRUM-175 / EVM-269`
Status: corrected; fresh proof required

## Incident

The first D8 series, `scenario-d-series-20260802T081243Z-398c3c2e`, safely
recovered the exact worker, observer, and worker targets. Common evidence
schema validation passed, detection was `3.142-6.828 s`, recovery was
`6.056-10.991 s`, and production B0 remained unchanged.

Independent contract review found that the runner recorded heartbeat deltas
but did not make the configured `<=7.5 s` p95 target a required closure check.
The recorded single deltas were `8.0 s`, `9.0 s`, and `11.0 s`. They span the
intentional process outage and do not establish recovered healthy cadence.
The series is therefore retained as immutable superseded evidence and is not
accepted as Scenario D closure, even though its per-run common schema reports
are valid.

## Root Cause

`collect_recovery()` stopped sampling as soon as the replacement child first
became live. That left only one cross-outage heartbeat interval and no stable
post-recovery window. The heartbeat p95 value was not included in the required
postcondition set, so the common schema could not detect this scenario-specific
omission.

## Correction

- sample at least three fresh heartbeats from the recovered exact child;
- require at least two post-recovery intervals and p95 `<=7.5 s`;
- make the cadence check mandatory at both run and series closure;
- preserve the cross-outage interval separately from healthy cadence;
- include policy, full process census, restart ledger, and lifecycle claim
  state in each before/after evidence bundle.

Focused verification after the correction: Ruff passes and `22 / 22` Scenario
D tests pass.

## Safety And Claim Boundary

No production Pod, NVIDIA device-plugin, source data, or cluster resource was
mutated. All three selected host children recovered, process counts returned to
one, production B0 remained `1 / 1` CUDA Ready with model SHA
`abcb8504a36c1128d32021722cfedce6357fd73598a52f6c2a0d60aca9d9a27f`,
and the Prometheus target remained `up`.

This RCA proves fail-closed evidence review in a single-node local environment.
It does not prove HA, zero downtime, production traffic resilience, or an
enterprise SLA.
