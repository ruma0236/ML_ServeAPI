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

- focused lifecycle, Kubernetes executor, run-state, ledger, and Scenario D
  runner tests: `50 / 50` pass;
- full repository Python suite: `458 / 458` pass;
- Ruff: all touched Python modules and tests pass;
- wrong lifecycle Job label produces a deterministic identity blocker and no
  reconciliation evidence or mutation;
- a matching running Job is observed before resumed execution and the durable
  side-effect entry reaches `completed` with the same runtime identity;
- a degraded supervisor is rejected by the runtime-revision guard.

The dedicated runner now covers three independent replays for the accepted
golden ledger, duplicate keys, wrong run identity, terminal-state regression,
exact running-Job observation, and wrong candidate observation. It snapshots
Kubernetes Job, MLflow run, candidate, deployment-intent, production serving,
GPU, supervisor, observer, worker, and Prometheus identities before and after.
The runner itself is implemented but its immutable F-drive execution remains
pending a clean pushed revision.

### First runner attempt RCA

Attempt `scenario-d-lifecycle-20260802T181803Z-532dd42e` stopped before an
exact-observation decision because its nested isolated task-evidence path
crossed the Windows path-length boundary. No Kubernetes apply/delete/patch,
MLflow write, candidate creation, deployment intent, or production/process
mutation occurred. The partial attempt is retained as immutable RCA evidence.
Generated manifests and isolated operation ledgers now use a short run-root
path; canonical and external locations are unchanged.

Attempt `scenario-d-lifecycle-20260802T181928Z-8b8f9f67` completed every
runtime, external-identity and branch safety check, but final acceptance rejected
the wrong-observation replay because the decision fingerprint included each
independent attempt's `side_effect_key`. The semantic blocker was identical and
external identity delta remained zero. The key remains in audit evidence, while
the stability fingerprint now excludes attempt-specific key fields.

### Accepted non-disruptive and child-live proofs

- Non-disruptive attempt
  `scenario-d-lifecycle-20260802T182104Z-fdcf0047` passed all seven checks.
  Golden terminal ledger, duplicate key, wrong run, terminal-state regression,
  exact observation, and wrong observation each ran three times. Kubernetes
  Job `25`, MLflow run `79`, candidate `27`, and intent `11` identity sets had
  zero delta. Production/runtime identity was unchanged and evidence re-hashed
  `62 / 62`; index SHA-256 is
  `2a60818436ad9dcbce015cdab1a9b2b429754f46d56e2db2bf7fb9f6193542e9`.
- Exact-child series
  `scenario-d-series-20260802T182356Z-fdcf0047` passed worker/observer/worker.
  Maximum detection was `5.212014 s`, maximum recovery `8.6442343 s`, and
  heartbeat p95 `5.0 s`. Every run passed preflight `12 / 12` and
  postconditions `10 / 10`; artifact hashes re-matched `24 / 24`.
- The source, API, supervisor, worker, and observer converged on `fdcf004`.
  Production B0, CUDA, GPU/device-plugin, and Prometheus recovered unchanged.

These proofs are intentionally separate. They do not yet prove a worker exit
while the real lifecycle training Job is active. A dedicated integrated runner
is implemented to create and queue profile v9, inject only when the task is
running, the exact Job UID exists, and its side effect is `reserved`, then wait
without manual repair for training, MLflow, CT, staging, CUDA, Prometheus, and
production restoration. Full Python verification is now `461 / 461`.

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
