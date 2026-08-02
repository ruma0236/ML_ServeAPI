# Scenario B Evidence URI RCA

Date: 2026-08-02  
Scenario: `EVM-267 / SCRUM-173`  
Dependency: `EVM-244 / SCRUM-144`

## Incident

The first two real Scenario B replay attempts produced the expected model and
routing decisions, but host-side evidence validation failed. The attempts are:

- `scenario-b-quality-20260802T024226Z-3058c67e`
- `scenario-b-runtime-20260802T024431Z-1d1df27f`

Both attempts are retained unchanged as failed RCA evidence. They do not close
Scenario B.

## Observed Result Before Closure Failure

The quality attempt collected 1,000 successful production B0 observations and
1,000 successful low-F1 B7 CUDA observations. The evaluator detected
`quality_f1_below_minimum` in `0.0035 s`, retained the exact stable B0 route,
and observed production readiness and Prometheus `up` after replay.

The runtime attempt used a quality-passing B7 checkpoint and 1,000 successful
raw CUDA observations. An explicit copied-observation overlay injected two
transport failures only into the 100 deterministically assigned challenger
requests. The evaluator observed error rate `0.02 > 0.01`, stopped allocation
in `0.0069 s`, restored the exact B0 route, matched route/response identity
`100%`, and left production readiness and Prometheus healthy.

These measurements are diagnostic evidence only until the machine index passes.

## Root Cause

The evidence writer used `Path.resolve()` inside the Docker collector. It wrote
artifact URIs under `/mnt/evm-data/...` into `evidence-index.json`. Those paths
are valid inside the short-lived container but unavailable to the Windows host
validator, which correctly returned every indexed artifact as missing.

The file contents and hashes were not lost. The failure was a cross-runtime URI
contract defect.

## Corrective Action

- Add an explicit canonical evidence URI root to the writer.
- Continue hashing the actual container-mounted files.
- Store host-readable canonical F-drive URIs in the index.
- Unit-test container-write versus host-URI projection.
- Do not edit or reuse either failed attempt.
- Execute fresh quality-block and runtime-rollback runs after the fix is pushed.

## Prevention

Evidence writers crossing host/container boundaries must receive both the
runtime write root and canonical validation root. `Path.resolve()` is prohibited
as an implicit cross-runtime evidence identity. Scenario closure requires a
fresh host-side digest validation over every indexed artifact.

## Claim Boundary

No Scenario B completion claim is allowed from these attempts. They demonstrate
useful failure detection and RCA discipline, not completed canary evidence.

