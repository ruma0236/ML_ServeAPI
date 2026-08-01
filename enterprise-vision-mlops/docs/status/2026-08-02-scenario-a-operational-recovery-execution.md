# Scenario A Operational Recovery Execution

Status: Complete
Environment: single-node Docker Desktop Kubernetes, one RTX 4080 SUPER, one
production EfficientNet-B0 replica
Jira: `SCRUM-172 / EVM-266`
Jira status: Done; completion evidence comment `10444`

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
- `A8`: UID-preconditioned Pod delete, monotonic fixed-cadence sampling, CUDA
  inference, identity recovery, immutable result evidence, cooldown, and three
  independent maintenance replays.

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
allowlisting, stale/current plans, and zero/multiple paths. The blocked run was
not reopened or reused; all live proof below used fresh run IDs.

## A8 Three-Run Live Proof

All three independent runs used source revision
`193dd1077c9d1eb8cb8ce0e5f1a6277e417d7108`. Each run began with a fresh
baseline and exact Pod UID, captured the DaemonSet before and after a
non-mutating `no_change` reconciliation plan, passed the complete A7 preflight,
and consumed a separate expiring single-use approval.

| Run | Detection | Endpoint interruption | Recovery | Result |
|---|---:|---:|---:|---|
| `scenario-a-20260801T162419Z-9b384eca` | 0.200 s | 9.872 s | 10.082 s | PASS |
| `scenario-a-20260801T162757Z-53992165` | 0.178 s | 9.898 s | 25.084 s | PASS |
| `scenario-a-20260801T162953Z-69e939e5` | 0.170 s | 9.904 s | 25.087 s | PASS |

The series ledger is
`F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/failure_scenarios/A/_series/production-b0.json`.
The three `live-report.json` files passed the machine validator with required
closure `live_proof`. Detection was `3/3 <= 30 s`, recovery was
`3/3 <= 300 s`, and the required dataset, split, model, artifact, image, CT,
and rollback identities were `21/21` exact matches. Two historical failed Pods
remained visible as context but produced `0` false target blockers.

Each live report proves that Kubernetes replaced a different exact old UID,
the rollback target remained unchanged, a real VisA image was inferred on
CUDA, and the exact Prometheus target recovered. No device-plugin, staging B7,
data, Deployment template, or cluster-wide resource was mutated.

## Final Runtime And Verification

- Docker Desktop node: Ready, GPU capacity/allocatable `1 / 1`.
- NVIDIA device-plugin DaemonSet: desired/ready `1 / 1`.
- `evm-production/evm-b0-production`: ready/available/updated `1 / 1 / 1`.
- Final active Pod UID: `6bf1d8ca-f4b2-4dea-9d87-d6a06ceae3c2`.
- Readiness: production B0 candidate loaded on CUDA with model SHA-256
  `abcb8504a36c1128d32021722cfedce6357fd73598a52f6c2a0d60aca9d9a27f`.
- Prometheus target `evm-b0-production / host.docker.internal:30800`: `up`.
- Supervisor: healthy; lifecycle worker and Kubernetes observer are live and
  both match revision `193dd10`.
- Verification: 49 focused pytest tests and Ruff passed.
- Jira `SCRUM-172` transitioned to Done after completion evidence comment
  `10444`; the Stage 2 master and Scenarios B-E remain open.

No manual patch or rollback was needed during any run. Recovery was performed
by the Kubernetes Deployment controller.

## Claim Boundary

The evidence supports controlled local operational validation, exact identity
gating, real CUDA CT, bounded detection, and controller-driven single-Pod
recovery. It does not support claims of high availability, zero downtime,
production user traffic, business A/B, or cluster-wide resilience. The measured
9.87-9.90 second interruption is expected for this single-replica design.
