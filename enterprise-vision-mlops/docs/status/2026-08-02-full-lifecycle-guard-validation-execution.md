# Full Lifecycle Guard Validation Execution

Date: 2026-08-02; canonical actual-injection suite resumed 2026-08-03
Status: In Progress; fresh-suite Scenario E linkage PASS. Scenario C is next;
B, D, and A remain unstarted in this suite.
Parent plan: `EVM-276 / SCRUM-184`
Execution ledger: `EVM-285 / SCRUM-193`
Workstream Epic: `EVM-EPIC-23 / SCRUM-183`
Branch: `codex/mac-mini-worker`
Entry revision: `01a980c240779da538f34e6b1b32f23174160604`

## Purpose

Execute the A-E guards at their real lifecycle transitions rather than repeat
their isolated proofs. This file is the versioned execution ledger for design,
implementation checkpoints, immutable failed attempts, RCA, corrections,
measured runs, and final closure.

No result in the planning documents closes this execution ledger. Each phase
below requires new evidence bound to this execution series.

## Canonical Actual Injection Suite

This file is the single Git status ledger for the 2026-08-03 fresh A-E suite.
Jira `SCRUM-193`, the matching Notion execution page, and the matching Obsidian
work log are the other canonical views. Scenario-specific documents are
evidence references only and do not replace this ledger.

Execution order is `E -> C -> B -> D -> A`. Every new LifecycleRun carries at
most one intentional scenario injection. A scenario advances only after its
result, evidence hashes, cleanup, runtime invariants, and four-system checkpoint
are complete. Scenario A additionally creates a fresh no-fault lifecycle
candidate so it does not reuse a run already injected by Scenario D.

### Cross-Suite Summary

| Scenario | State | Fresh lifecycle/injection | Result |
|---|---|---|---|
| E | PASS | fresh L2/L4/L6 run identities are bound and 32/32 artifacts re-hashed | suite manifest entry complete |
| C | pending | fresh quality/drift injection and governed hold/resume required | not started |
| B | pending | fresh quality and runtime branches, one release injection per run | not started |
| D | pending | fresh run; exact worker stop only at reserved/running training side effect | not started |
| A | pending | fresh no-fault candidate run, then exact B0 serving restart | not started |

### Scenario E

- **Purpose:** prove data/artifact identity fails closed at real L2 and L6
  boundaries while a corrected branch reaches real CUDA training, MLflow and
  isolated CT.
- **Injection point:** accepted fresh evidence
  `scenario-e-integrated-20260803T030435Z-55e9f243`.
- **Pre-state/identity:** exact VisA manifest/shards, source `55e9f24`, stable
  B0 UID/model, GPU/plugin and Prometheus snapshot.
- **Intentional failure:** corrupt run-local data identity and release identity;
  no canonical data mutation.
- **Expected guard:** training and release approval fail closed; deployment
  intent remains zero.
- **SLI/SLO:** identity/hash closure 100%; unintended mutation zero.
- **Result/evidence:** PASS in suite
  `full-lifecycle-actual-injection-20260803T050000Z-cbbbbb77`; exact runs
  `lifecycle-20260803T030442-5aed7911` and
  `lifecycle-20260803T031337-7a609762`; result SHA-256
  `2eebbf212393f6957bd5b725cfcdff44a26f1a8de2bf516e0372471a4b5c3dd2`;
  evidence index SHA-256
  `7fc7321392a7a2cb3d1cca974ae85f09e91d0a27b3324539669a3487ab693c33`;
  artifacts matched `32 / 32`.
- **RCA/remediation:** prior unmapped `/app/artifacts` receipt attempt remains
  immutable no-credit RCA; accepted proof contains the path-mapping fix.
- **Invariants:** canonical data and exact B0/GPU/plugin/Prometheus unchanged.
- **Claim boundary:** controlled local single-node VisA/CUDA proof, not live
  customer production, HA, business A/B, or an SLA.
- **Status:** PASS; admits Scenario C.

### Scenario C

- **Purpose:** validate drift/quality review, hold and governed retraining resume.
- **Injection point:** pre-training quality review on a fresh LifecycleRun.
- **Pre-state/identity:** pending fresh snapshot.
- **Intentional failure:** deterministic VisA quality/distribution violation.
- **Expected guard:** review hold, training attempt zero before approval, no
  automatic deployment intent; independent single-use approval may resume only
  the same run.
- **SLI/SLO:** guard decision within policy timeout; duplicate/stale signal
  creates no duplicate candidate or side effect.
- **Result/evidence:** pending.
- **RCA/remediation:** pending; any failed attempt remains immutable.
- **Invariants:** production B0, canonical data and unrelated runtime unchanged.
- **Claim boundary:** local batch drift validation, not online business drift.
- **Status:** pending.

### Scenario B

- **Purpose:** reject a bad candidate and contain a controlled runtime breach.
- **Injection point:** release admission after real Airflow, CUDA training,
  MLflow, readiness and isolated CT.
- **Pre-state/identity:** pending fresh snapshot.
- **Intentional failure:** one measured quality breach and one deterministic
  challenger error-rate breach, each in its own fresh LifecycleRun.
- **Expected guard:** HTTP 422 admission denial or zero-allocation rollback;
  deployment intent zero and stable identity retained.
- **SLI/SLO:** detection <=30 s, recovery <=300 s, request identity 100%.
- **Result/evidence:** pending.
- **RCA/remediation:** pending; no branch may borrow evidence from the other.
- **Invariants:** exact production B0 and real-user traffic unchanged.
- **Claim boundary:** controlled replay, not business A/B or production canary.
- **Status:** pending.

### Scenario D

- **Purpose:** validate idempotent lifecycle continuity across worker loss.
- **Injection point:** exact training Job and side-effect key both
  reserved/running on a fresh LifecycleRun.
- **Pre-state/identity:** pending fresh PID/lease/fence/Job/task/key snapshot.
- **Intentional failure:** stop only the exact owned lifecycle worker process.
- **Expected guard:** supervisor recovers one worker and the same Job/task/key
  completes without redispatch or duplicate effects.
- **SLI/SLO:** detection <=10 s, worker recovery <=60 s, runtime restoration
  <=90 s, duplicate effects zero.
- **Result/evidence:** pending.
- **RCA/remediation:** pending; ambiguous ownership blocks the stop.
- **Invariants:** production B0, device-plugin, data and cluster-wide resources
  unchanged.
- **Claim boundary:** local process recovery, not distributed exactly-once or HA.
- **Status:** pending.

### Scenario A

- **Purpose:** validate serving recovery after a fresh candidate lifecycle and
  bounded model transition.
- **Injection point:** exact committed-M1 B0 Pod after a fresh no-fault
  LifecycleRun creates the M1 package.
- **Pre-state/identity:** pending fresh candidate plus exact M0 Deployment UID,
  Pod UID, model/image/data/CT/source and rollback target.
- **Intentional failure:** restart only the exact committed-M1 Pod.
- **Expected guard:** detection, exact identity recovery, CUDA inference and
  Prometheus recovery, followed by a separately approved exact M0 rollback.
- **SLI/SLO:** detection <=30 s, recovery <=300 s, identity 100%.
- **Result/evidence:** pending.
- **RCA/remediation:** pending; any ambiguous target or rollback identity blocks
  mutation.
- **Invariants:** device-plugin, cluster-wide resources, canonical data and real
  user traffic unchanged.
- **Claim boundary:** approved local single-replica maintenance interruption,
  not zero downtime, HA, customer production, or an SLA.
- **Status:** pending.

## Execution Series

The future series ID is sealed immediately before the no-fault golden run. Its
attempts follow this order:

1. prerequisite normalized event/correlation/dedupe engine (`SCRUM-179`);
2. fail-closed recovery ownership and read-only incident plane (`SCRUM-180`);
3. common lifecycle identity envelope and no-fault L0-L7 golden path
   (`SCRUM-185`);
4. E data and artifact trust boundaries (`SCRUM-186`);
5. D worker/observer continuity and side-effect reconciliation (`SCRUM-187`);
6. C drift review, hold, and governed retraining resume (`SCRUM-188`);
7. B real candidate release guard and stable containment (`SCRUM-189`);
8. A committed-M1 serving recovery and separate M0 rollback (`SCRUM-190`);
9. single-scenario integrated closure (`SCRUM-191`).

No phase advances on a failed, ambiguous, stale, or incomplete gate.

## Initial Runtime Baseline

Observed before code or runtime mutation:

- Git HEAD and remote are both
  `01a980c240779da538f34e6b1b32f23174160604`; only unrelated sibling
  untracked paths exist;
- Docker Compose API, Control Panel, Airflow scheduler/webserver, MLflow,
  MinIO, Postgres, Prometheus, and Grafana are running; health-checked services
  report healthy;
- Docker Desktop Kubernetes node is Ready and reports GPU capacity and
  allocatable `1 / 1`;
- NVIDIA device-plugin is `1 / 1 Running`;
- `evm-production/evm-b0-production` is `1 / 1 Ready`, image digest
  `sha256:227b483f466678e00fbf13fd6b3ad1059ca2c6771239d204494fb610fa7d9f7a`;
- two historical failed/unknown B0 Pods remain visible and must never satisfy
  an active-target selector;
- Prometheus reports `evm-api`, `evm-b0-production`, and `prometheus` targets
  `up`;
- host supervisor, lifecycle worker, and Kubernetes observer are live with one
  exact process each, lease `271574bb7884418884fcff9ee288d55d`, fencing token
  `4`, and executable revision `37ec89d6...`;
- host GPU observation was 13,855 MiB used of 16,376 MiB and 58% utilization,
  so shared-CUDA validation is not admitted without a fresh bounded preflight;
- generic API `/ready` currently identifies `vision-baseline v17`, stage
  `Shadow`, type `image_feature_centroid`, while the Kubernetes stable target
  is the digest-pinned EfficientNet-B0 deployment. The validator must keep
  these as separate target-scoped identities and fail closed on substitution.

The initial endpoint and runtime observations are baseline references only.
They are not golden-path or scenario acceptance.

## Common Safety And Evidence Contract

- every attempt has a new immutable attempt ID under one lifecycle series;
- every event carries lifecycle/correlation/causation/producer identity,
  component-specific revision, data/model/artifact/image/target identity,
  UTC audit time, monotonic elapsed time, semantic digest, and raw evidence
  digest;
- every mutating transition requires one exact target, fresh policy/evidence,
  an action digest, current owner lease/fence, and an immutable rollback target;
- zero or multiple target matches, historical resource selection, stale
  heartbeat, revision mismatch, dirty tracked source, missing evidence, or
  ambiguous external side effect blocks the action;
- canonical source data, unrelated processes, device-plugin, cluster-wide
  resources, and real user traffic are excluded from injection;
- evidence root:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/lifecycle_guard_validation/`;
- Git stores schemas, tests, policies, summaries, and artifact indexes only.

## Checkpoint Ledger

| Checkpoint | State | Evidence | Next gate |
|---|---|---|---|
| tracking and runtime baseline | complete | Git/Jira/Notion/Obsidian execution section plus read-only runtime observations | implement `SCRUM-179` |
| normalized event/correlation/dedupe | complete | source `627209a`; 12 focused, 98 A-E regression, and 407 full tests passed; three independent 1,000-event replays, 300 unrelated events, zero false merge/duplicate parent/action; p95 30.183-32.374 ms; F-drive artifact hashes 625/625 | `SCRUM-180` |
| recovery ownership/read-only incident plane | complete | source `c905d7d`, stale-state UI correction `3467315`; 16 focused and 423 full Python tests at implementation, 16 files/51 Control Panel tests after UI correction; three independent recommendation-only series, 41/41 artifact hash closure, zero mutation; GET-only APIs, low-cardinality metrics, browser proof, CUDA B0 and runtime invariants preserved | `SCRUM-185` |
| no-fault golden lifecycle | complete | attempt `lifecycle-20260802T165525-279cf1dc` at `85867e1` passed 11/11 preflight, Airflow 18/18, real RTX 4080 SUPER CUDA training, MLflow, readiness 13/13, isolated CT 18/18, approval, staging deployment, CUDA inference and Prometheus; all 10 stages, 23 guard decisions and 8 side effects completed; cleanup restored exact production B0 1/1 and staging 0/0; a post-run phase-evidence overwrite was remediated with independent training/CT handoff files and 437 full tests | `SCRUM-186` E guard |
| E lifecycle guard | complete | source `bc726d9`; real VisA 10,821 records/23 shards; six canonical/corrupt/corrected branches and 18/18 deterministic replays; zero Kubernetes Job, MLflow, candidate or intent delta; runtime/canonical identity delta zero; evidence hashes 133/133 | `SCRUM-187` D guard |
| D lifecycle guard | complete | source `7f253ac`; full lifecycle 10/10, exact-worker detection/recovery `6.9141254 / 10.0661301 s`, same Job without redispatch, B0/Prometheus restored | C guard |
| C lifecycle guard | complete | source `39d4cd2`; 18/18 checks, real drift hold/resume, CUDA training, MLflow, isolated CT, intent zero and 21/21 integrated hashes | B guard |
| B lifecycle guard | in progress | exact-run release guard, immutable profile requirement, evidence closure, two-branch runner and 54 focused tests implemented; fresh lifecycle execution pending | A maintenance preflight |
| A lifecycle guard | complete | fresh M1 transaction, exact M1 Pod recovery, separate M0 rollback and 38/38 hashes PASS | integrated closure |
| final integrated closure | in progress | A-E lifecycle-reachability and evidence validator pending | cross-scenario handoff |

## Current Findings

1. `SCRUM-179` and `SCRUM-180` now have tested implementation and replay
   closure. The incident plane is deliberately read-only; its static proof
   snapshot becomes visibly stale and cannot authorize recovery.
2. Existing A-E evidence is reusable only as baseline and fixtures. It cannot
   close any integrated lifecycle attempt.
3. Stable-serving identity must be target-scoped. The general API registry
   readiness and Kubernetes B0 production deployment are distinct runtimes.
4. The current single GPU has insufficient evidence for shared CT admission;
   resource arbitration must be explicit before L5.
5. Direct per-event durable writes to the capacity-oriented F drive exceeded
   the initial five-minute replay command limit. The accepted proof used a
   transient SSD spool and hash-verified canonical publication to F. This
   retains audit integrity but is not an F-drive ingestion throughput claim;
   the failed partial attempt remains immutable RCA evidence.
6. The initial host runtime revision gap was closed before the first attempt.
   API, supervisor, worker, and observer were all bound to `329b609` for its
   preflight. Every later corrective commit still requires a fresh restart and
   exact revision convergence before a replacement attempt is admitted.
7. The first golden launch exposed two real admission gaps without mutating
   Airflow, Kubernetes, MLflow, or the stable B0 target. Profile v8 had valid
   parameters but stale source and split file digests, while the catalog still
   displayed it as ready. Commit `329b609` makes catalog execution readiness
   include replay identity, and the normal profile API created replay-ready v9
   from the current immutable inputs.
8. Attempt `lifecycle-20260802T160919-97ba37d2` then passed 11/11 target,
   CUDA, Prometheus, runtime, profile, CI, rollback, and clean-source checks.
   It was blocked before Airflow dispatch because the Windows host worker tried
   to open the container-only `/app/artifacts/.../side_effect_ledger.json` URI.
   Commit `0f1a8ab` routes guard evidence through the shared runtime path
   resolver. The failed run and its unconsumed handoff approvals remain
   immutable RCA evidence; they are not retried across source revisions.
9. The replacement attempt `lifecycle-20260802T161927-4992f133` was bound to
   source `1e1e251`, passed the same 11/11 preflight, and produced preflight
   evidence SHA-256
   `2ae6ad5f7157382dc25fc615df8b990606754f6243b66d22a8e75f49920604aa`.
   It then failed closed before Airflow dispatch because the host worker read
   the supervisor snapshot from the literal container URI
   `/app/artifacts/w7/host_runtime/supervisor.json`. The underlying supervisor,
   worker, and observer snapshots were live and revision-matched on F drive;
   only URI resolution was wrong. Commit `1d39845` applies the shared runtime
   path resolver to this snapshot and adds a Windows-host/container-URI
   regression test. All 434 tests pass. This run and its approvals also remain
   immutable and are not reused.
10. Attempt `lifecycle-20260802T163003-ec64fa8c`, bound to source `51731ed`,
    passed a fresh 11/11 preflight with evidence SHA-256
    `aef59b79653fa6089d494c9c8475fa5230274e1e5cf1bd7b1223a8453a04c8ac`.
    The real Airflow run `cp__20260802T163224-4ade7238` completed all 18 tasks
    and verified source provenance. Its training handoff consumed the exact,
    single-use approval for deployment UID
    `cfdab424-dcc5-4d5f-a46f-ae7530441ef4`, scaled B0 from one to zero, but a
    label-wide `kubectl wait` also selected two historical Failed Pods. The
    120-second wait timed out before the training Job was created. Automatic
    rollback restored the unchanged B0 deployment and candidate to 1/1;
    Prometheus returned `up=1` and serving readiness passed. Failure artifact
    SHA-256 is
    `794315a86f4c8c28a6e4eeb9d851ed53e84ed93672ba4c365d77353aee5d4385`.
    Commit `4d716a2` captures non-terminal Pod name and UID before scale, waits
    only those exact Pods, and blocks zero/multiple active identities before
    mutation. Ten focused and 436 full tests pass. The failed run remains
    immutable and is not retried.
11. Attempt `lifecycle-20260802T165525-279cf1dc`, bound to source `85867e1`,
    is the accepted no-fault golden run. It passed 11/11 preflight, real
    Airflow 18/18, Kubernetes training Job
    `evm-lifecycle-train-0e8fdaf4493e`, MLflow run
    `bf06465b8ff44f8a9dba2e02bdf90552`, readiness 13/13, isolated CT 18/18,
    explicit local-staging approval, deployment intent
    `deploy-9ab54c1cda4b604a`, exact CUDA serving validation and Prometheus.
    Training stopped at epoch 4/20 after validation accuracy `0.958333`
    crossed the predeclared `0.93` threshold. The final candidate recorded
    accuracy `0.962079`, F1 `0.823529`, AUROC `0.973746`, and model SHA-256
    `2df0b78a...2707e`. All 10 lifecycle stages, 23 guard decisions and eight
    side effects completed. Cleanup left staging at 0/0 and restored the same
    production deployment UID to 1/1 with CUDA inference and Prometheus up.
12. Post-run review found that isolated CT reused
    `training_gpu_handoff.json`, preserving both single-use approval receipts
    but overwriting the earlier training lease command history. The runtime
    behavior and restoration result remain valid, while the audit durability
    gap is explicitly recorded. Training and isolated CT now use independent
    evidence paths; the sequential regression and all 437 tests pass. The
    original run remains immutable and bound to `85867e1`.
13. Scenario E integration review found two lifecycle-specific gaps that the
    isolated Scenario E closure did not cover. Airflow completion previously
    proved file provenance but did not semantically validate the generated
    shard membership, split isolation, labels, content identities, or embedded
    index identity before GPU training. Release admission also lacked one
    run-local seal joining source revision, candidate, dataset, model artifact,
    MLflow run, serving image, CT evaluation, readiness, and model matrix.
    The current implementation adds both fail-closed boundaries, revalidates
    the release seal at approval and immediately before deployment mutation,
    and exposes their evidence URIs through the API/OpenAPI/UI contract. The
    focused suite passes 48 tests, the full Python suite passes 447 tests, and
    the Control Panel typecheck and production build pass. Scenario E remains
    implementation checkpoint remained in progress until immutable F-drive
    replay evidence and unchanged runtime invariants were captured.
14. Scenario E attempt
    `scenario-e-lifecycle-20260802T175222Z-bc726d96` passed all seven
    acceptance checks. Six real-evidence branches produced 18/18 deterministic
    decisions: canonical and corrected data passed, wrong shard identity and
    split leakage blocked, canonical release passed, and wrong model identity
    blocked. Maximum data decision time was `0.361160 s`; maximum release
    decision time was `0.027395 s`. Kubernetes Job, MLflow run, candidate, and
    deployment intent identity sets had zero delta. Production B0, CUDA, GPU,
    device-plugin, worker, observer, Prometheus, and all golden hashes remained
    unchanged. Independent evidence re-hashing matched 133/133 files. This
    closes controlled local Scenario E and admits Scenario D; it does not claim
    a new live Airflow corruption run or production release.

## Scenario E Lifecycle Guard Checkpoint

### Implemented guard boundaries

- Airflow success is followed by semantic validation of the run-local source
  manifest, shard files, split membership, record/content identity, labels,
  counts, boundaries, and `identity_sha256`; any ambiguity blocks before the
  model-training stage is queued.
- Isolated CT completion creates `validation/release-submission.json`, sealing
  the source commit, candidate, dataset, actual model file digest, MLflow run,
  serving image digest, CT evaluation, readiness report, and model matrix.
- Independent approval revalidates the sealed bytes and may bind the request to
  the expected candidate/model/CT tuple. Deployment revalidates the same seal
  before manifest generation, intent creation, or Kubernetes mutation, closing
  the approval-to-deploy time-of-check/time-of-use window.
- Missing, empty, duplicated, cross-split, malformed, stale, or digest-mismatched
  evidence is fail-closed. No fallback to an unsealed candidate is permitted.

### Current verification

- canonical data and exact release identity each pass three deterministic
  replays with one stable decision fingerprint;
- corrupt shard semantic identity, cross-split duplicate, empty data, wrong
  expected model identity, modified model bytes, and modified readiness bytes
  are blocked;
- an Airflow `success` observation with a corrupt derived index leaves model
  training `not_started`;
- a modified release submission is rejected before the first deployment
  mutation callback;
- targeted Ruff, 48 focused Python tests, 447 full Python tests, Control Panel
  TypeScript lint, and production build pass;
- repository-wide Ruff still reports nine pre-existing findings in unrelated
  files. They are not changed or counted as Scenario E regressions.

### Exit gate result

All listed exit checks passed in the immutable F-drive attempt. Closure details
are in `docs/status/2026-08-02-lifecycle-guard-scenario-e-closure.md`.
`SCRUM-186 / EVM-278` is complete and Scenario D is the next dependency.

## Scenario D Lifecycle Continuity Checkpoint

Scenario D entered implementation under `SCRUM-187 / EVM-279`. The independent
host-process recovery closure remains baseline evidence only. Integrated code
review confirmed that a worker restart after Kubernetes Job admission could
leave the side-effect ledger `reserved` and permanently block rather than
reattach to the exact existing Job.

The checkpoint implementation adds read-only reconciliation of one exact Job
identity before resumed execution. Namespace, name, UID, lifecycle label,
candidate label, image and source-revision identity must match the versioned
manifest; any missing or mismatched observation fails closed without apply,
delete or redispatch. Degraded supervisor state also now invalidates runtime
revision admission instead of trusting retained child revision strings.

Focused tests pass `50 / 50`, the full Python suite passes `458 / 458`, and
Ruff passes for touched files. A dedicated runner now exercises three
independent golden-ledger, duplicate-key, wrong-run, terminal-regression,
exact-observation and wrong-observation branches while snapshotting external
and runtime identities. This is an implementation checkpoint only. The runner
still must execute from a clean pushed revision, and bounded exact
worker/observer live recovery remains required before Scenario D can close.
Detailed progress is in
`docs/status/2026-08-02-lifecycle-guard-scenario-d-progress.md`.

The first runner attempt, `scenario-d-lifecycle-20260802T181803Z-532dd42e`,
stopped before the exact-observation guard decision on a Windows path-length
failure in its nested isolated evidence root. No external or process mutation
occurred. The immutable failed attempt is retained, and only isolated generated
manifest/operation paths were shortened for the next clean-source attempt.

The second attempt, `scenario-d-lifecycle-20260802T181928Z-8b8f9f67`, passed
all branch, runtime and external-identity checks but was rejected because its
wrong-observation stability fingerprint included the unique per-attempt
`side_effect_key`. Semantic blockers were identical and side-effect delta was
zero. The audit key remains recorded; only the cross-attempt decision
fingerprint is normalized to semantic blocker fields for the next attempt.

Accepted D replay `scenario-d-lifecycle-20260802T182104Z-fdcf0047` passed
seven checks with 18 deterministic branch decisions, zero Kubernetes Job,
MLflow, candidate or deployment-intent identity delta, unchanged runtime, and
`62 / 62` evidence hashes. Exact-child series
`scenario-d-series-20260802T182356Z-fdcf0047` then passed
worker/observer/worker with maximum detection `5.212014 s`, recovery
`8.6442343 s`, heartbeat p95 `5.0 s`, and `24 / 24` artifact hashes.

Scenario D remains open because those proofs were separate. A dedicated
integrated runner is implemented and tested to terminate the exact worker only
after a real lifecycle training task is running, one exact Job UID is admitted,
and its durable side effect remains `reserved`. It then observes autonomous
same-Job reconciliation and full lifecycle completion. The full suite passes
`461 / 461`; clean-source runtime execution is the remaining D exit gate.

The first combined attempt at source `7a68097`, runner series
`scenario-d-training-20260802T183826Z-7a68097a`, completed the real Airflow
data stage with all 18 tasks terminal, then failed closed before worker
termination or Kubernetes Job creation with
`gpu_handoff_approval_missing:training`. Production B0 remained `1/1` with
CUDA and there was no Job, MLflow, candidate, intent, or process mutation.
This exposed a harness admission gap, not a Scenario D recovery result. The
runner now issues three exact, bounded, single-use GPU handoff approvals before
queueing and binds the later independent release approval to the sealed
candidate/model/CT tuple. Missing or replayed evidence still blocks. Focused
tests pass `32 / 32` and the full suite passes `463 / 463`; a new immutable
source-bound attempt is required.

The second combined attempt at source `649114f`, runner series
`scenario-d-training-20260802T190053Z-649114f1`, proved the new phase approvals
were valid: real Airflow reached 18 terminal tasks, the exact training approval
was consumed, production B0 was released, and Job
`evm-lifecycle-train-9a468dd9c8ae` was admitted. The runner then failed closed
before worker termination on `training_job_exact_identity_mismatch`. The
manifest correctly used canonical `sha256(run_id)[:12]`; the runner and shared
reconciler still assumed the final 12 run-ID characters. Automatic cancellation
removed the Job and released the handoff in `48 s`; production returned 1/1
and CUDA-ready, with no MLflow/candidate/CT/intent outcome. The reconciler,
runner, and fixtures now share `short_run_id()` and exact manifest workload
identity. Focused tests pass `15 / 15`, full tests `464 / 464`; D remains open
for a fresh source-bound attempt.

The third combined attempt at source `689b775`, series
`scenario-d-training-20260802T192008Z-689b7758`, completed real Airflow 18/18
and began the exact training handoff. Its bounded admission poll observed the
normal task/ledger `running/reserved` state just before Kubernetes persisted
the Job and incorrectly treated API `NotFound` as terminal. No worker was
terminated. Automatic cancellation released the handoff in `10 s` and restored
B0 CUDA; no Job outcome, MLflow, candidate, CT, release, or intent followed.
Only `NotFound` is now transient during admission; all other kubectl and
identity failures remain fail closed. Focused tests pass `16 / 16`, full tests
`465 / 465`; another immutable attempt is required.

The fourth combined attempt at source `b82f6b4`, series
`scenario-d-training-20260802T193451Z-b82f6b49`, reached the intended live
fault boundary. The exact approved worker exited while Job UID
`c28d3d36-8cf3-49e6-9fff-9a3a0fe64fe1` and its reserved side effect were
active. Detection took `2.3490377 s`, recovery `5.631701 s`, replacement PID
`41968` retained the same revision/lease/fencing identity, and the same Job
completed epoch 4/20 and step 102/102 without redispatch. MLflow run
`4bf93169cc174e989ab18a2d8f59164b`, readiness 13/13, and isolated CT over
2,181 records also completed.

The lifecycle then failed closed at independent release approval with HTTP
422. The release seal validates on the host, but the API container did not map
the CT host URI under `F:/EnterpriseMLOps_CT/enterprise-vision-mlops` to the
already-mounted `/mnt/evm-ct` evidence root. Automatic cancellation restored
production B0 1/1 and CUDA, with no approval, deployment intent, or candidate
serving mutation. CT host/mount runtime-path resolution and bounded API error
evidence are now implemented; focused tests pass 30/30 and the full suite
466/466. D remains open until container validation and a fresh 10/10 attempt.

The fifth combined attempt at source `ea1a014`, series
`scenario-d-training-20260802T200109Z-ea1a014f`, proved the CT path correction
inside the full lifecycle and completed run
`lifecycle-20260802T200116-548bea16` with all 10 stages. Exact-worker detection
was `4.2323827 s`, recovery `7.0983775 s`, and the same training Job UID
continued without redispatch. Three tasks, two Jobs, eight unique committed
side effects, three GPU handoff approvals, independent release approval,
deployment, and CUDA serving completed.

Acceptance was `10 / 11`: exact production UID, replica 1/1, CUDA, and
device-plugin 1/1 were restored, while the immediate final Prometheus snapshot
captured one endpoint restart-window `EOF/down` at `20:16:46Z`. The same target
returned to `up` autonomously by `20:17:05Z`. The attempt remains immutable
blocked evidence with 15 indexed artifacts. Final restoration now requires
two distinct consecutive successful Prometheus scrape timestamps plus all
runtime identities within a bounded 90-second window; timeout fails closed.
Focused tests pass 14/14 and the full suite 468/468.

Accepted source `7f253ac` series
`scenario-d-training-20260802T202554Z-7f253ace` then completed lifecycle
`lifecycle-20260802T202558-a50d19fe` with `11 / 11` checks and `10 / 10`
stages. Exact-worker detection/recovery was `6.9141254 s / 10.0661301 s`;
the same training Job UID continued without redispatch. Final identity, CUDA,
device-plugin, revision, and two-distinct-scrape restoration passed in
`28.9677976 s`.

All eight side effects were unique and committed. The exact external delta was
two Kubernetes Jobs, one MLflow run, one candidate, and one deployment intent.
Independent re-hashing matched `16 / 16` artifacts. Production returned to the
same B0 UID at 1/1 with CUDA and Prometheus up; active lifecycle runs were zero
and worker/observer process counts were one each. This closes controlled local
Scenario D; details are in
`docs/status/2026-08-03-lifecycle-guard-scenario-d-closure.md`.

Scenario C implementation is now in progress. The first checkpoint adds an
exact-run quality-review envelope, immutable evidence digest checks,
duplicate/stale signal accounting, independent hold/reject/approve-for-training
actions, and single-use training authorization. The model-training handler
evaluates this contract before bundle materialization or any Kubernetes task.
Focused tests pass `59 / 59`; held training remains attempt `0` with no task or
runtime identity. No real CUDA lifecycle attempt has been run at this
checkpoint, so `EVM-280` remains open. Details are in
`docs/status/2026-08-03-lifecycle-guard-scenario-c-progress.md`.

The integrated runner is now implemented with rejected, held and governed
resume branches. It independently re-hashes the source drift proof, requires
zero downstream mutation at hold, resumes the same exact run through GPU
training/MLflow/isolated CT, and stops before release approval. Runner tests
pass `5 / 5` and the full suite `480 / 480`. No runtime attempt has started.

Scenario C attempt 1 at source `427b400` produced a valid fresh CUDA drift
proof in `18.476526197 s` but the wrapper selected PATH Python 3.14 without the
project package and stopped before lifecycle admission. No LifecycleRun,
Airflow task, Kubernetes Job, MLflow run or deployment intent was created.
The wrapper now resolves only an import-capable project Python. This attempt is
retained as harness RCA, not a guard pass.

Scenario C attempt 2 at source `bd01cdc` produced another fresh CUDA guard
PASS in `18.102172372 s`, then found an API persistence boundary defect before
any lifecycle stage was queued. The quality review attempted to write through
read-only `/mnt/evm-data`; the isolated run was cancelled with zero
Airflow/Kubernetes/MLflow/deployment effect. The remediation writes lifecycle
quality state through `EVM_LIFECYCLE_RUN_ROOT` and adds regression coverage for
separate read-only data and writable API artifact mounts.

Accepted Scenario C source `39d4cd2`, series
`scenario-c-lifecycle-20260802T213154Z-39d4cd2e`, and lifecycle
`lifecycle-20260802T213202-1c0776fc` passed all `18 / 18` guard checks. Hold
kept training at attempt 0 with zero training/release effects; independent
approval resumed the same run through real CUDA training, MLflow and isolated
CT, then stopped at release approval with deployment intents still zero. The
exact external delta was Jobs +2, MLflow +1 and candidate +1. Source hashes
matched 17/17 and integrated hashes/sizes 21/21. Exact B0 CUDA, plugin and two
Prometheus scrapes restored in `29.0953264 s`.

## Scenario B Lifecycle Release Checkpoint

Scenario B entered implementation under `SCRUM-189 / EVM-281`. The previous
standalone quality rejection and runtime rollback remain baseline evidence and
cannot close this lifecycle attempt.

The current implementation adds an immutable `require_controlled_replay`
profile policy, copies it into each LifecycleRun and binds one Scenario B
evidence index at the release-approval boundary. Every artifact hash and the
run/series/attempt/correlation, profile/config, source, candidate/model,
isolated CT and sealed submission identity must match. A quality rejection or
runtime rollback then denies approval before any deployment intent.

Two predeclared policies and a source-bound runner create separate fresh
quality and runtime lifecycle runs. Each must complete real Airflow,
Kubernetes GPU training, MLflow, readiness and isolated CT before using 1,000
real holdout requests. The quality branch requires measured F1 below fixed
`0.90`; the runtime branch assigns exactly 100 requests and injects two
isolated challenger failures. Stable B0 mutation and real-user traffic remain
excluded. Focused Python tests pass 54/54, Control Panel tests 52/52, typecheck
and build pass, and touched-file Ruff passes. Runtime proof is not started, so
Scenario B remains In Progress. Detailed contract and progress are in
`docs/status/2026-08-03-lifecycle-guard-scenario-b-progress.md`.

The first integrated invocation stopped before LifecycleRun creation because
the wrapper selected a Conda base runtime without Torch and PowerShell promoted
the failed import probe to a terminating error. No Airflow, Kubernetes, MLflow,
candidate, replay or deployment-intent effect occurred. The wrapper now falls
through failed candidates and explicitly discovers the established F-drive
CUDA runtime. The failed invocation is RCA evidence, not Scenario B evidence;
both branches remain fresh-run requirements.

The next fresh quality run reached the release boundary after real Airflow,
CUDA training, MLflow, readiness and isolated CT, then failed closed before
replay because the Windows runner treated the CT manifest's `/mnt/evm-ct`
image URI as a native host path. Intended pre-release delta was Jobs +2,
MLflow +1, candidate +1 and deployment intent 0. Automatic cancellation
restored active runs 0 and preserved exact B0 CUDA/Prometheus identity. The
loader now applies the shared CT host/mount mapping before path and digest
checks; both B branches still require fresh execution.

The following quality run passed the corrected 1,000-record loader but sampled
Prometheus once during the expected B0 restart convergence window after CT.
It failed closed before replay, guard registration or approval; intended delta
remained Jobs +2, MLflow +1, candidate +1 and intent 0. The same exact target
was autonomously `up` on a new scrape about 22 seconds later. Replay admission
now reuses the proven runtime-restoration gate: exact UID/1/1/CUDA/plugin/source
identity and two distinct consecutive successful scrapes within 90 seconds.

Scenario B then closed PASS at source `1e541de`, series
`scenario-b-lifecycle-20260802T230659Z-1e541de0`. Two fresh lifecycle runs each
completed Airflow, CUDA training, MLflow, readiness and isolated CT. The quality
branch registered `rejected_release`; the runtime branch registered
`rolled_back` after 100/1,000 assignments and two controlled errors. Both
approval requests returned HTTP 422, both deployment-intent deltas were zero,
and exact B0 UID/model/CUDA/Prometheus identity was retained. Independent
re-hash matched 95/95 artifacts across five indexes. Scenario A is now the next
dependency.

Scenario A implementation seals the current exact B0 package as M0 and the
accepted Scenario D lifecycle package as M1. It binds Deployment UID and
resourceVersion, current active Pod UID, source revision, lifecycle
series/run/attempt/correlation, MLflow, CT, model/data/image hashes and
single-use apply/rollback action digests. M1 can become the local stable pointer
only after CUDA inference and two distinct Prometheus scrapes pass. The existing
UID-preconditioned Scenario A engine then restarts the committed M1 Pod, after
which a separate approved transaction must restore exact M0. The first live
attempt applied and verified M1 but failed before the exact M1 Pod restart when
a 275-character Windows atomic state path exceeded the host limit. Emergency
rollback patched M0 but the `Recreate` controller stalled with zero active Pods;
an exact bounded reconcile completed M0 recovery, CUDA inference, two distinct
Prometheus scrapes and pointer restoration. The failed attempt and delayed
rollback remain immutable non-acceptance RCA. Compact recovery storage, a
240-character pre-mutation path budget and exact M0 rollout reconciliation are
implemented for a fresh attempt. `SCRUM-190 / EVM-282` remains In Progress.

## Golden Attempt Log

| Attempt | Source | Result | External mutation | Disposition |
|---|---|---|---|---|
| pre-run profile v8 | `329b609` | rejected: source manifest, split file and reproducibility digests changed since snapshot | none | retain rejection; v8 remains blocked |
| `lifecycle-20260802T160919-97ba37d2` | `329b609` | 11/11 preflight pass, then `side_effect_ledger_invalid` at data dispatch | none; no Airflow task assignment was created | retain run; path RCA fixed at `0f1a8ab`; create a new source-bound attempt |
| `lifecycle-20260802T161927-4992f133` | `1e1e251` | 11/11 preflight pass, then `runtime_revision_unavailable` for worker and observer before data dispatch | none; no Airflow task assignment was created | retain run; supervisor URI-path RCA fixed at `1d39845`; create a new source-bound attempt |
| `lifecycle-20260802T163003-ec64fa8c` | `51731ed` | 11/11 preflight and Airflow 18/18 pass; training handoff then failed on a label-wide delete wait that included two historical Failed Pods | approved B0 1-to-0 handoff occurred; automatic rollback restored 1/1; no training Job, model, or deployment intent was created | retain run and failure artifact; exact active-Pod fix at `4d716a2`; create a new source-bound attempt |
| `lifecycle-20260802T165525-279cf1dc` | `85867e1` | PASS: 11/11 preflight, Airflow 18/18, real CUDA training, MLflow, readiness 13/13, isolated CT 18/18, local-staging approval/deploy, CUDA serving and Prometheus; 10/10 stages complete | eight intended side effects; bounded single-GPU handoffs; cleanup restored exact production B0 1/1 and staging 0/0 | accepted golden baseline; result under F-drive `validation/integrated-attempt-result.json`; proceed to E on a new immutable attempt |
| `lifecycle-20260802T183833-47cdc111` | `7a68097` | Airflow 18/18 terminal; training blocked before Job admission with `gpu_handoff_approval_missing:training` | none; no worker termination or resource handoff; production B0 remained CUDA-ready | retain immutable RCA; runner now pre-issues exact phase approvals and independently approves the sealed release; retry from a new source revision |
| `lifecycle-20260802T190057-c8cae6d4` | `649114f` | Airflow 18/18 terminal; exact training approval consumed and Job admitted; blocked before worker termination on stale run-label expectation | bounded production handoff occurred; automatic cancel deleted Job and released handoff in 48 s; B0 returned 1/1 CUDA; no MLflow/candidate/CT/intent | retain immutable RCA; unify runner/reconciler/fixtures on canonical `short_run_id()` and exact manifest identity; retry from a new source revision |
| `lifecycle-20260802T192014-183e0bc1` | `689b775` | Airflow 18/18 terminal; training handoff began; admission poll hit the normal pre-persistence Job `NotFound` window | no worker termination; automatic cancel released handoff in 10 s and restored B0 CUDA; no downstream outcome | retain immutable RCA; wait only on `NotFound` during bounded admission while every other error remains fail closed; retry from a new source revision |
| `lifecycle-20260802T193458-04b6cb7b` | `b82f6b4` | Airflow 18/18; exact worker detection 2.3490377 s and recovery 5.631701 s; same training Job completed; MLflow, readiness 13/13 and CT 2,181 completed; release approval returned 422 because API could not resolve the CT host URI through `/mnt/evm-ct` | exact approved worker only; no Job redispatch; automatic cancellation restored B0 1/1 CUDA; no release approval, deployment intent or candidate serving mutation | retain immutable integrated recovery/RCA evidence; map configured CT host and mount roots, validate inside API, then retry full 10/10 lifecycle |
| `lifecycle-20260802T200116-548bea16` | `ea1a014` | 10/10 lifecycle completed; worker detection 4.2323827 s and recovery 7.0983775 s; same Job, MLflow, CT, approval, deploy and CUDA serving passed; final acceptance 10/11 because immediate Prometheus snapshot saw one restart-window EOF | three tasks, two intended Jobs, eight committed effects and release intent; exact B0 UID/1/1/CUDA/plugin restored; Prometheus autonomously up 19 s later | retain immutable blocked/RCA evidence; require two distinct consecutive successful scrapes within bounded restoration window, then rerun from a new revision |
| `lifecycle-20260802T202558-a50d19fe` | `7f253ac` | PASS: 11/11 checks, 10/10 stages, worker detection 6.9141254 s, recovery 10.0661301 s, same training Job, MLflow, CT, approval, deploy, CUDA serve and two-distinct-scrape restoration 28.9677976 s | exact intended delta: Jobs +2, MLflow +1, candidate +1, intent +1; eight unique committed effects; B0 same UID 1/1 CUDA/plugin/Prometheus restored | accepted controlled local Scenario D closure; 16/16 hashes; proceed to Scenario C without HA or distributed exactly-once claim |
| Scenario C contract and runner checkpoint | `25ba10a` plus pending runner commit | exact-run quality evidence, pre-training hold, single-use approved-for-training action, rejected branch and source-bound integrated runner implemented; 480/480 tests | none; runtime proof not started | commit/deploy runner, then run a fresh source-bound CUDA lifecycle attempt |
| Scenario C attempt 1 | `427b400` | fresh CUDA drift PASS in 18.476526197 s, then wrapper selected Python 3.14 without `evm` and stopped before lifecycle admission | none; no LifecycleRun or external lifecycle side effect | retain harness RCA; pin import-capable project Python and rerun from a new source revision |
| Scenario C attempt 2 | `bd01cdc` | fresh CUDA drift PASS in 18.102172372 s; isolated rejection review then returned HTTP 500 because quality state targeted read-only `/mnt/evm-data` | none; run cancelled before data/training/MLflow/CT/deployment dispatch | retain storage-boundary RCA; use writable lifecycle root, test split mount semantics, rerun from a new source revision |
| `lifecycle-20260802T213202-1c0776fc` | `39d4cd2` | PASS: 18/18 guards; rejection audit; Airflow; hold at training attempt 0; single-use governed resume; real CUDA training, MLflow, readiness and isolated CT; stop at independent release approval | Jobs +2, MLflow +1, candidate +1, intent 0; exact B0 CUDA/plugin/Prometheus restored in 29.0953264 s | accepted controlled local Scenario C closure; source 17/17 and integrated 21/21 hashes; proceed to Scenario B |
| Scenario B pre-launch attempt 1 | `fabe6a2` | wrapper stopped during Python discovery because Conda base lacked Torch and native stderr terminated fallback | none; no LifecycleRun, Airflow, Kubernetes, MLflow, candidate, replay or intent | retain harness RCA; discover F-drive CUDA Python safely and run both branches fresh from the corrected revision |
| `lifecycle-20260802T222607-175d2b88` | `ab72f9f` | Airflow 18/18, CUDA training, MLflow, readiness and CT passed; replay then failed closed because `/mnt/evm-ct` was not mapped to the Windows host path | intended pre-release Jobs +2, MLflow +1, candidate +1, intent 0; automatic cancel; B0 1/1 CUDA/Prometheus unchanged | retain mount-boundary RCA; map CT mount paths before path/digest validation and rerun both branches fresh |
| `lifecycle-20260802T224642-da79b5a0` | `ec2ce22` | full quality lifecycle and corrected replay manifest preflight passed; one Prometheus snapshot caught the post-CT B0 restart window before replay | Jobs +2, MLflow +1, candidate +1, intent 0; target autonomously up on a distinct scrape about 22 s later; exact B0 unchanged | retain convergence RCA; require exact runtime plus two distinct up scrapes before replay and rerun both branches fresh |
| `lifecycle-20260802T230714-5e948d45` plus `lifecycle-20260802T232208-c58e1e77` | `1e541de` | PASS: two fresh full lifecycles; measured quality rejection; 100/1,000 runtime replay with two errors; approval HTTP 422 for both | each branch Jobs +2, MLflow +1, candidate +1, intent 0; exact B0 UID/model/CUDA/plugin/Prometheus retained | accepted Scenario B closure; 95/95 indexed artifacts; proceed to Scenario A |
| `scenario-a-lifecycle-20260803T000920Z-0209bac1-47eaa66e` | `0209bac` | M1 apply/verify/pointer PASS in 35.879184 s; failed before M1 restart because nested atomic state path was 275 characters | M1 rollout committed; emergency M0 patch; Recreate rollout then stalled at zero active Pods | immutable failed attempt; no acceptance credit; compact path and rollout reconciliation RCA required |
| `rollback-recovery-20260803T001831Z` | `0209bac` | delayed M0 recovery PASS: exact CUDA inference and two distinct Prometheus scrapes in 30.560015 s | one exact Deployment reconcile annotation under the already-consumed rollback approval; device-plugin/data/cluster unchanged | pointer revision 2 restored M0; operational recovery evidence only, not Scenario A acceptance |
| Scenario A remediation checkpoint | `9c98bfd` | compact recovery ID/root, 240-character atomic path preflight and exact Recreate rollout reconciliation; 7/7 focused and 499/499 full tests | none after delayed rollback closure | converge runtime and start a fresh immutable attempt |
| `scenario-a-lifecycle-20260803T003224Z-d121c9c5-351ae3c7` | `d121c9c` | PASS: M1 apply/commit 18.173018 s; exact M1 Pod detection/recovery 0.2045879/10.0966768 s; interruption 9.8788259 s; M0 rollback 57.634592 s | two exact model rollouts, one exact M1 Pod restart; device-plugin/data/registry/cluster-wide zero | accepted Scenario A closure; 38/38 hashes; exact M0 1/1 CUDA/Prometheus/plugin restored; proceed to EVM-283 |
| `closure-20260803T010400Z-b62d29f8` | `b62d29f` | BLOCKED: A-D lifecycle reachability/determinism/hash/claim checks PASS; E deterministic replay and 133 hashes PASS but no fresh L2/L4/L6 LifecycleRun injection | read-only audit only; current exact M0 1/1 CUDA/GPU/plugin/Prometheus/supervisor revision passed; mutation zero | retain blocker; implement isolated run-local E data and release-admission injections before EVM-274/EVM-284 |
| `scenario-e-integrated-20260803T023734Z-c7a08409` | `c7a0840` | actual L2 block and corrected Airflow/CUDA/MLflow/CT/L6 HTTP 422 passed; host three-replay evidence failed on unmapped `/app/artifacts` URI | Jobs +2, MLflow +1, candidate +1, intent 0; cleanup active runs 0; B0/plugin/Prometheus healthy | immutable failed RCA, no acceptance credit; runtime-map receipt URI and re-hash before a fresh run |
| `scenario-e-integrated-20260803T030435Z-55e9f243` | `55e9f24` | PASS: real L2 integrity block; corrected L4 Airflow/CUDA/MLflow/readiness/CT; real L6 approval and duplicate HTTP 422; 20/20 checks | Jobs +2, MLflow +1, candidate +1, intent 0; canonical data and exact B0 unchanged; 32/32 integrated artifacts | accepted Scenario E integrated closure; proceed to independent A-E audit |
| `closure-20260803T032754Z-55e9f243` | `55e9f24` | PASS: A-E reachability, deterministic decisions, evidence hash closure and claim boundaries; E 165 artifacts | read-only closure audit; exact M0 1/1 CUDA/GPU/plugin/Prometheus/supervisor revision PASS | accepted EVM-283; next technical gate admitted |

## Synchronization Rule

Each meaningful checkpoint updates this ledger, Git commit/push, the matching
Jira issue and evidence comment, the Notion execution page, and the Obsidian
work log/current context/index/graph. A plan, partial implementation, failed
attempt, or historical baseline is never recorded as a completed proof.
