# S8 Resource Sampler Preflight 01 RCA

- Status: rejected non-credit preflight.
- Source revision: `2d749b2`.
- Workload: actual external HIGGS branch-heavy API, `35 RPS`, 10-second
  warmup, 120-second measurement, 5-second cooldown.

## Result

The runtime completed `4,200/4,200` requests at exactly `35.0 RPS` with p99
`40.95 ms`, error rate `0`, complete request/trace identity, drained gauges,
and deterministic cleanup. The run produced `55` valid resource and runtime
gauge samples. The frozen S8 coverage rule requires at least `90%` of the
one-second cadence, or `108` samples for this preflight. It therefore receives
no acceptance credit.

The public artifact SHA-256 is
`8eb0e7ca826d807c61df4b870a84a98e199dfd87e05cf538c2680f952dd878ee`.
The six private artifacts have aggregate SHA-256
`967271b3146a76a4c6758d86644e6aa1554cd5733902a7bdbf6b372bf57cbee1`.

## Root Cause

The sampler still obtained queue and pool gauges by directly requesting the
busy API's `/metrics` endpoint. Although the timeout was now bounded and no
request failed, each metrics read competed with the measured workload and took
long enough that the nominal one-second sampling loop yielded only about one
sample every 2.2 seconds. Raising the timeout fixed exception propagation but
did not satisfy the frozen observation cadence.

## Remediation

The existing isolated Prometheus process already scrapes each API replica at a
one-second cadence. Runtime-gauge sampling will query Prometheus's cached,
timestamped series instead of issuing an additional direct request to the API.
Freshness is validated and unavailable/stale samples retain the same bounded
miss and three-consecutive-failure policy. Host/process/RSS/handle/artifact
sampling remains direct and independent.

The accepted load, guardrails, 30-minute duration, and repetition count remain
unchanged. A second non-credit preflight is required before the full suite.

## Boundary

This is controlled single-local-node evidence only, not production SLA, HA/DR,
multi-node, multi-GPU, or customer-traffic evidence.
