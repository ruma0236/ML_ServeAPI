# Scenario E Integrated Lifecycle Guard Progress

Date: 2026-08-03
Jira: `SCRUM-191 / EVM-283`
Status: IMPLEMENTED; integrated runtime proof pending

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

## Pending Acceptance

No integrated runtime PASS is claimed at this checkpoint. The next action is to
commit and deploy this exact revision to the supervised local control plane,
execute both LifecycleRuns, seal the F-drive evidence graph and rerun the A-E
closure validator. `SCRUM-191` remains In Progress until that audit is PASS.

## Claim Boundary

This work can prove controlled local single-node VisA/CUDA lifecycle guard
behavior. It cannot prove real-user production traffic, HA, zero downtime,
business A/B, multi-node atomicity, distributed exactly-once processing or an
enterprise SLA.
