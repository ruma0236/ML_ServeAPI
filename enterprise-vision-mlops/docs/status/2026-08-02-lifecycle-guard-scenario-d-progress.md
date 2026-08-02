# Lifecycle Guard Scenario D Progress

Date: 2026-08-02
Issue: `EVM-279 / SCRUM-187`
Status: In Progress; non-disruptive replay and exact child-process recovery
accepted, combined active-training recovery retry pending

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

### First combined active-training attempt RCA

Source `7a68097` attempt
`scenario-d-training-20260802T183826Z-7a68097a` created run
`lifecycle-20260802T183833-47cdc111`. The real Airflow data stage completed all
18 terminal tasks, including the F-drive intake audit (`326.018 s`) and image
quality gate (`285.105 s`). Before any worker termination or Kubernetes Job
creation, the training stage failed closed with
`gpu_handoff_approval_missing:training`.

The attempt had not issued the three single-use resource-handoff approvals
required by the existing single-GPU contract. No exact-worker fault injection,
Kubernetes Job, production scale, MLflow run, candidate, or deployment intent
occurred. Production B0 remained `1/1` with CUDA inference, and the attempt is
retained unchanged as RCA evidence.

The runner now issues `training`, `isolated_ct`, and `staging_deployment`
approval receipts against the exact run, source revision, active holder UID,
action digest, and bounded TTL before queueing. The later lifecycle release
approval is performed by a requester-independent validation actor using the
sealed candidate, model digest, and CT evaluation identity. Missing identity,
replayed approval, or missing receipt remains fail closed. Future runner
failures also persist the exact error and run snapshot. Focused approval and
orchestration tests pass `32 / 32`; the full Python suite passes `463 / 463`.

### Second combined active-training attempt RCA

Source `649114f` attempt
`scenario-d-training-20260802T190053Z-649114f1` created run
`lifecycle-20260802T190057-c8cae6d4`. All three handoff approvals were issued
against the exact production deployment UID and source revision. Real Airflow
again reached 18 terminal tasks. The training approval was consumed, the B0
holder was released, and Job `evm-lifecycle-train-9a468dd9c8ae` was admitted.

Before worker termination, the runner rejected the Job with
`training_job_exact_identity_mismatch`. RCA showed that the actual manifest
correctly uses canonical `sha256(run_id)[:12]` label `9a468dd9c8ae`, while the
runner and the newly added reconciliation fixture expected `run_id[-12:]`.
The same stale assumption existed in the shared read-only reconciler and would
have blocked the replacement worker during the intended live proof.

Automatic safety cancellation removed the admitted Job and released the
single-GPU handoff from `19:11:18Z` to `19:12:06Z` (`48 s`). The worker was
never terminated; no MLflow run, candidate, CT Job, release approval, or
deployment intent was created. Production B0 returned `1/1` and CUDA-ready.
The run, consumed training receipt, unconsumed downstream receipts, manifest,
failure snapshot, and cancellation audit remain immutable RCA evidence.

The shared reconciler, integrated runner, and deterministic replay fixtures
now use `short_run_id()` as the single canonical run-label contract and compare
live labels, container image, and revision environment against the exact
manifest. Focused regression passes `15 / 15`; the full suite passes
`464 / 464`.

### Third combined active-training attempt RCA

Source `689b775` attempt
`scenario-d-training-20260802T192008Z-689b7758` created run
`lifecycle-20260802T192014-183e0bc1`. Real Airflow again completed 18 terminal
tasks and the exact training handoff began. During the normal interval after
the task/ledger entered `running/reserved` but before Kubernetes persisted the
Job, the admission poll treated API `NotFound` as a terminal kubectl error.

No worker termination occurred. Automatic cancellation released the handoff
from `19:30:15Z` to `19:30:25Z` (`10 s`), and production B0 returned `1/1`
and CUDA-ready. No admitted Job remained and no MLflow, candidate, CT, release,
or deployment intent followed.

The runner now treats only Kubernetes `NotFound` during the bounded admission
poll as transient. Authentication, context, malformed JSON, ambiguous identity,
wrong labels/workload, and every other kubectl failure still fail closed.
Focused tests pass `16 / 16`; the full suite passes `465 / 465`.

### Fourth combined active-training attempt RCA

Source `b82f6b4` attempt
`scenario-d-training-20260802T193451Z-b82f6b49` created run
`lifecycle-20260802T193458-04b6cb7b`. Real Airflow completed 18/18 tasks and
the runner consumed the exact worker-fault approval only after the training
task was `running`, side effect was `reserved`, and Job UID
`c28d3d36-8cf3-49e6-9fff-9a3a0fe64fe1` was observed. The supervisor detected
the exact worker exit in `2.3490377 s`, recovered it in `5.631701 s`, and
started replacement PID `41968` with the same source revision, lease, and
fencing identity. The same Kubernetes Job continued without redispatch and
finished epoch `4 / 20`, step `102 / 102`.

The recovered lifecycle then created MLflow run
`4bf93169cc174e989ab18a2d8f59164b`, passed readiness `13 / 13`, and completed
isolated CT over 2,181 real records as evaluation
`ct-eval-2f48e705cd1aef45`. Independent release approval was nevertheless
blocked with HTTP `422`. Host-side validation of the sealed submission passed,
but the API container could not resolve the CT URI rooted at
`F:/EnterpriseMLOps_CT/enterprise-vision-mlops` through its existing
`/mnt/evm-ct` Compose mount. It therefore reported the CT identity fields as
missing or mismatched. This is a cross-runtime evidence-path defect discovered
after the Scenario D worker recovery, not a worker-recovery failure.

Automatic cancellation preserved the failed attempt and restored production
B0 `1/1`, CUDA inference, and the single-GPU holder. No release approval,
deployment intent, or staging/production candidate mutation occurred. Runtime
path resolution now treats the host CT root and CT mount as one configured
identity namespace, while retaining fail-closed behavior when neither root is
available. API errors also retain bounded response details in immutable runner
evidence. Focused contract tests pass `30 / 30`; the full suite passes
`466 / 466`.

## Remaining Exit Work

This checkpoint does not close Scenario D. The remaining proof must:

1. rebuild the API from the corrected source and prove the fourth attempt's
   sealed release submission validates identically inside the API container;
2. execute a new source-bound combined attempt with all exact single-use
   handoff approvals present;
3. terminate only the exact supervisor-owned worker while the real training
   Job is active and its side effect remains `reserved`;
4. prove same-Job read-only reconciliation, autonomous 10/10 lifecycle
   completion, revision convergence, and production/GPU/CUDA/Prometheus
   restoration;
5. hash-close the dedicated F-drive evidence attempt and synchronize the final
   result across Git, Jira, Notion, and Obsidian.

## Claim Boundary

This checkpoint proves exact-worker recovery and same-Job continuation through
real training, MLflow, readiness, and isolated CT in one integrated attempt.
It does not yet prove the autonomous release/deploy/serving tail or close the
10/10 lifecycle. It does not claim distributed exactly-once execution, HA,
multi-node failover, or uninterrupted production traffic.
