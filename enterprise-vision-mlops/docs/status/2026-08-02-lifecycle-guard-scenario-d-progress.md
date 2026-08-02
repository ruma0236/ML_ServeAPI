# Lifecycle Guard Scenario D Progress

Date: 2026-08-02
Issue: `EVM-279 / SCRUM-187`
Status: In Progress; implementation checkpoint complete, integrated replay and
exact child-process recovery proof pending

## Integration Gap Confirmed

The independent Scenario D supervisor proof recovered exact worker and observer
processes, but the integrated lifecycle had a separate continuity gap. If the
lifecycle worker exited after a Kubernetes Job was admitted and before the
side-effect ledger was committed, the restarted worker saw a `reserved` entry
and blocked with `side_effect_reconciliation_required`. It could not safely
reattach to the already-running Job.

The runtime-revision guard also retained child revision strings when the host
supervisor was degraded. That could allow a stale observer condition to appear
revision-matched even though the supervision source was not healthy.

## Implemented Guard Boundary

- A restarted worker may observe an existing Kubernetes Job only when the task
  is already `running` or `done` and the persisted task runtime is Kubernetes.
- The live Job must match the exact namespace, name, non-empty UID, lifecycle
  run label, candidate label, container image, and selected source-revision
  environment identity from one exact versioned Job manifest.
- The observation path is read-only and writes a hash-addressed reconciliation
  record. It never applies, replaces, deletes, or redispatches a Job.
- Zero-match, malformed, wrong-label, wrong-container, queued, and ambiguous
  states fail closed before the executor can resume.
- A matching running Job is marked `reconciled`, then the executor enters its
  existing recovery path without delete/apply. A terminal matching task is
  committed without a second external action.
- A degraded host supervisor now makes worker and observer runtime revisions
  unavailable to the lifecycle guard, even if stale child payloads retain the
  expected revision string.

## Verification At This Checkpoint

- focused lifecycle, Kubernetes executor, and run-state tests: `40 / 40` pass;
- full repository Python suite: `453 / 453` pass;
- Ruff: all touched Python modules and tests pass;
- wrong lifecycle Job label produces a deterministic identity blocker and no
  reconciliation evidence or mutation;
- a matching running Job is observed before resumed execution and the durable
  side-effect entry reaches `completed` with the same runtime identity;
- a degraded supervisor is rejected by the runtime-revision guard.

## Remaining Exit Work

This checkpoint does not close Scenario D. The remaining proof must:

1. run deterministic side-effect replay against an isolated copy of the real
   accepted lifecycle evidence;
2. prove zero delta in Kubernetes Job, MLflow run, candidate, and deployment
   intent identity sets;
3. perform bounded live termination of only the exact supervisor-owned worker
   and observer after source/runtime convergence;
4. measure detection, recovery, revision convergence, duplicate-process and
   production/GPU/CUDA/Prometheus invariants;
5. hash-close the dedicated F-drive evidence attempt and synchronize the final
   result across Git, Jira, Notion, and Obsidian.

## Claim Boundary

This checkpoint proves tested reconciliation logic, not an integrated live
recovery result. It does not claim distributed exactly-once execution, HA,
multi-node failover, or uninterrupted production traffic.
