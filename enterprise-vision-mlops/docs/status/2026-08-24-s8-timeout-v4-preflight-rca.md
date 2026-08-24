# S8 Timeout V4 Preflight RCA

- Status: failed zero-credit preflight.
- Source revision: `8c1696e96a88cf2eb77d884375eb679e1eacd5b6`.
- Acceptance matrix and soak: not started.

## Result

The fail-closed state contract improved: all six admitted tasks reached terminal
states, four healthy tasks completed, two timeout tasks failed with no external
effect, duplicate effects were zero, outcome-unknown returned to zero, and
cleanup passed. The run still failed because only two of six trace contracts
were complete and fault-to-terminal recovery was 72.828 seconds against the
frozen 60-second bound.

## Root Cause

The runner passed a single 12-second delay to the mixed timeout profile. The
deterministic dependency therefore delayed both timeout and healthy requests,
causing six worker timeouts instead of the intended two. Healthy effects were
later reconciled, but their executor and dispatch spans had already been lost.

## Remediation

V5 keeps the v4 terminal policy and scopes the delay to `timeout_once` requests.
Healthy requests carry zero injected delay and must complete with the full trace
chain. Timeout work must retain the failure trace subset, exact zero effect, and
explicit failed terminal outcome. A zero-credit preflight must pass before all
21 acceptance repetitions restart.

## Boundary

This is controlled local preflight evidence only. It is not S8 acceptance,
production availability, HA/DR, or physical multi-node evidence.
