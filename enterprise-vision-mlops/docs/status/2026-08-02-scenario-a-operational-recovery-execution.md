# Scenario A Operational Recovery Execution

Status: In Progress
Environment: single-node Docker Desktop Kubernetes, one RTX 4080 SUPER, one
production EfficientNet-B0 replica
Jira: `SCRUM-172 / EVM-266`

## Implemented Gates

- `A0`: typed readiness/live-proof evidence and fail-closed validator.
- `A1`: revision-guarded state, exact target lease, expiring single-use approval.
- `A2/A3`: exact namespace/name/UID Kubernetes selection, exact Prometheus target,
  and historical terminal Pod exclusion.
- `A4`: low-cardinality metrics, five Prometheus alerts, and Grafana dashboard.
- `A5/A6`: read-only baseline, WSL driver reconciliation plan, and before/after
  DaemonSet template comparison with no mutation.
- `A7`: model/data/split/artifact/image/CT/rollback identity bundle, rollback
  capture, cooldown policy, maintenance preflight, and explicit approval command.
- `A8` executor: UID-preconditioned Pod delete, monotonic fixed-cadence sampling,
  CUDA inference, identity recovery, and immutable result evidence. It has not yet
  been executed in this record.

## Real CT Evidence

The production candidate `effnet-b0-img224-expedited-adamw` was evaluated against
the isolated VisA CT snapshot with 2,181 records and 8,640 development records.
The final report is
`F:/EnterpriseMLOps_CT/enterprise-vision-mlops/evaluations/ct-eval-b6a2574bc4e91b65/ct_evaluation.json`.
It passed on CUDA with zero overlap, no mutation, accuracy `0.939477`, F1
`0.694444`, AUROC `0.942967`, and model SHA-256
`abcb8504a36c1128d32021722cfedce6357fd73598a52f6c2a0d60aca9d9a27f`.

Two failed evaluations remain as immutable RCA evidence:

1. `ct-eval-407e4a0ce8c7d933`: canonical F-drive URI was not mapped to the
   container CT mount, so the evaluator failed closed with zero loaded records.
2. `ct-eval-f1e58e0e0c144694`: all integrity and isolation checks passed, but
   Docker's default shared memory exhausted in DataLoader workers.

The successful rerun separated canonical host and container mount roots, set a
2 GiB shared-memory limit, and used zero DataLoader workers for deterministic
local validation.

## A6 Invocation RCA

Run `scenario-a-20260801T161231Z-534f4ac2` passed its read-only baseline but was
blocked before A7. A PowerShell scalar containing the one discovered driver path
was indexed with `[0]`, which passed only `/` to the planner. The planner returned
`driver_path_discovery_cardinality:0`, wrote `mutation_performed=false`, and
terminalized the run as `blocked`. The NVIDIA DaemonSet was not changed.

Prevention is versioned in the CLI: when no explicit path is supplied, Python
invokes WSL discovery itself, deduplicates allowlisted `nv_dispi` directories,
and preserves zero/multiple cardinality as a blocker. Unit tests cover discovery,
allowlisting, stale/current plans, and zero/multiple paths. A fresh run is required;
the blocked run will not be reopened or reused.

## Claim Boundary

The evidence supports controlled local operational validation, exact identity
gating, and real CUDA CT. It does not support claims of high availability, zero
downtime, production user traffic, or cluster-wide resilience. The approved A8
maintenance action remains limited to restarting one exact production B0 Pod.
