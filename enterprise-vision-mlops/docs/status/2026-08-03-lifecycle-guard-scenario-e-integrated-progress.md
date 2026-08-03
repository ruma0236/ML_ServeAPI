# Scenario E Integrated Lifecycle Guard Progress

Date: 2026-08-03
Jira: `SCRUM-191 / EVM-283`
Status: PASS; integrated runtime and A-E closure accepted

## Purpose

Close the only remaining full-lifecycle guard blocker without mutating the
canonical VisA source, current production B0 Deployment, GPU device plugin or
cluster-wide resources. This checkpoint implements exact, run-bound Scenario E
injections at the normal lifecycle admission boundaries.

## Implemented Boundaries

### L2 Data Admission

1. Create a dedicated dry-run LifecycleRun from the real VisA EfficientNet-B0
   profile.
2. Bind an expiring, single-use injection contract to the exact run, series,
   attempt, correlation, profile digest, source revision and target path.
3. Let the normal Airflow data task finish.
4. Change only the run-local shard-index embedded identity.
5. Run the normal lifecycle data-integrity validator.
6. Require `integrity_shard_index_identity_mismatch`, no training admission and
   zero Kubernetes Job, MLflow run, candidate or deployment-intent side effect.

### L4 And L6 Release Admission

1. Create a separate corrected LifecycleRun.
2. Run normal Airflow, real CUDA B0 training, MLflow registration, readiness and
   isolated CT.
3. Preserve the canonical release submission and create a derived run-local
   submission with the wrong model identity.
4. Exercise the actual approval endpoint and require HTTP 422 before any
   deployment intent is created.
5. Reusing the consumed injection contract must fail closed.
6. Cancel the evidence run without deployment and verify exact production B0
   identity and canonical dataset hashes are unchanged.

Both semantic guard decisions must be stable across three direct replays. The
closure validator now requires the previous deterministic replay proof and the
new L2/L4/L6 integrated proof together.

## Implementation Evidence

- Run-bound contract: `src/evm/control_panel/lifecycle_integrity_injection.py`
- Data hook: `src/evm/control_panel/lifecycle_orchestrator.py`
- Release approval hook: `src/evm/control_panel/lifecycle_runs.py`
- Integrated runner: `src/evm/operations/lifecycle_guard_e_integrated_runner.py`
- Operator entrypoint: `scripts/dev/lifecycle_guard_e_integrated_proof.ps1`
- Closure contract: `src/evm/operations/lifecycle_guard_closure_validator.py`

Verification at this checkpoint:

- focused integration and closure tests: `53 / 53` PASS;
- full Python regression: `514 / 514` PASS;
- Ruff: PASS;
- PowerShell parser: PASS.

Two implementation defects were found and fixed before runtime execution:

1. Docker and host representations of the same run root differed. Scope
   validation now accepts the representation boundary only when both roots end
   in the exact bound run ID; all other scope mismatches remain fail closed.
2. The first derived release-evidence path exceeded the Windows path budget.
   Run-local contract, claim, receipt and derived-release names were shortened
   without weakening identity binding.

## Superseded Pending Acceptance

No integrated runtime PASS is claimed at this checkpoint. The next action is to
commit and deploy this exact revision to the supervised local control plane,
execute both LifecycleRuns, seal the F-drive evidence graph and rerun the A-E
closure validator. `SCRUM-191` remains In Progress until that audit is PASS.

## Attempt 1 RCA

Attempt `scenario-e-integrated-20260803T023734Z-c7a08409` is immutable failed
RCA evidence and receives no acceptance credit.

- Data run `lifecycle-20260803T023740-fe17425c` reached real Airflow and was
  blocked at L2 on `integrity_shard_index_identity_mismatch`; training attempt
  remained zero.
- Corrected run `lifecycle-20260803T024632-4b0bb495` completed real Airflow,
  CUDA training, MLflow run `a83a82ad048f4f0ea7d614a7bee6a6cd`, readiness
  and isolated CT `ct-eval-53c70c908221a37d`.
- Actual approval returned HTTP 422 with five model-identity mismatches; repeat
  use of the injection contract returned HTTP 422 as already consumed; the
  deployment intent remained zero.
- The runner then failed its host-side three-replay assertion because the
  receipt's container URI `/app/artifacts/...` was opened directly on Windows
  and collapsed to `release_submission_invalid`. The guard itself had already
  produced the expected semantic blockers.
- Automatic cleanup left active runs zero. Exact production B0 remained 1/1,
  the device-plugin remained 1/1 and Prometheus API/B0 targets remained up.

Remediation maps the receipt URI through the shared runtime-path contract and
re-hashes the derived submission against the receipt before replay. A new
source-bound run is required; the failed attempt is not relabeled as PASS.

## Accepted Attempt And Closure

Fresh series `scenario-e-integrated-20260803T030435Z-55e9f243` passed all
`20/20` integrated checks at source `55e9f2432185580215b426a4f9c383e9ec27994d`.

- Data run `lifecycle-20260803T030442-5aed7911` completed real Airflow in
  `525 s`, blocked at L2 on `integrity_shard_index_identity_mismatch`, kept
  training attempt zero and produced zero external side effects.
- Corrected run `lifecycle-20260803T031337-7a609762` completed real Airflow in
  `523 s`, CUDA training in `148 s`, MLflow run
  `f603be0afe8140308bc1a0aa549d6536`, readiness and isolated CT
  `ct-eval-9263131a46cf1f85` in `41 s`.
- Canonical release passed three identical decisions. The run-local wrong
  model identity produced three identical blocked decisions and the actual
  approval plus duplicate use both returned HTTP 422.
- External delta was Kubernetes Jobs `+2`, MLflow runs `+1`, candidates `+1`
  and deployment intents `0`. Canonical dataset hashes and exact production
  B0 identity were unchanged; active lifecycle runs returned to zero.
- Integrated result SHA-256 is
  `2eebbf212393f6957bd5b725cfcdff44a26f1a8de2bf516e0372471a4b5c3dd2`.
  Its 32-artifact evidence-index SHA-256 is
  `7fc7321392a7a2cb3d1cca974ae85f09e91d0a27b3324539669a3487ab693c33`.

The final A-E audit `closure-20260803T032754Z-55e9f243` passed every scenario,
all runtime-restoration checks and admitted the next gate. Scenario E closed
two indexes and `165` artifacts. Closure result SHA-256 is
`fb8650adab5895bc232caba7f5c884eb8295204b5f2d279a49f53fbb98f6ff55`;
closure evidence-index SHA-256 is
`1663ffd624b4c1cb5f802dd496b980a49c1c7cd070bdb8a4b0dfbf73998c0ce9`.

Final regression is focused `54/54` and full Python `515/515`. Ruff passes for
all Scenario E and closure files changed by this work, and both PowerShell
entrypoints parse successfully. Repository-wide Ruff still reports nine
pre-existing findings in unrelated files; this closure does not relabel that
separate lint debt as green.

## Claim Boundary

This work can prove controlled local single-node VisA/CUDA lifecycle guard
behavior. It cannot prove real-user production traffic, HA, zero downtime,
business A/B, multi-node atomicity, distributed exactly-once processing or an
enterprise SLA.
