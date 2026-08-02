# Full Lifecycle Guard Validation Execution

Date: 2026-08-02
Status: In Progress; tracking, `EVM-272` correlation, and `EVM-273` recovery
ownership prerequisites complete; integrated lifecycle execution remains open.
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
| E lifecycle guard | not started | pending | D guard |
| D lifecycle guard | not started | pending | C guard |
| C lifecycle guard | not started | pending | B guard |
| B lifecycle guard | not started | pending | A maintenance preflight |
| A lifecycle guard | maintenance gated | pending | integrated closure |
| final integrated closure | not started | pending | cross-scenario handoff |

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

## Golden Attempt Log

| Attempt | Source | Result | External mutation | Disposition |
|---|---|---|---|---|
| pre-run profile v8 | `329b609` | rejected: source manifest, split file and reproducibility digests changed since snapshot | none | retain rejection; v8 remains blocked |
| `lifecycle-20260802T160919-97ba37d2` | `329b609` | 11/11 preflight pass, then `side_effect_ledger_invalid` at data dispatch | none; no Airflow task assignment was created | retain run; path RCA fixed at `0f1a8ab`; create a new source-bound attempt |
| `lifecycle-20260802T161927-4992f133` | `1e1e251` | 11/11 preflight pass, then `runtime_revision_unavailable` for worker and observer before data dispatch | none; no Airflow task assignment was created | retain run; supervisor URI-path RCA fixed at `1d39845`; create a new source-bound attempt |
| `lifecycle-20260802T163003-ec64fa8c` | `51731ed` | 11/11 preflight and Airflow 18/18 pass; training handoff then failed on a label-wide delete wait that included two historical Failed Pods | approved B0 1-to-0 handoff occurred; automatic rollback restored 1/1; no training Job, model, or deployment intent was created | retain run and failure artifact; exact active-Pod fix at `4d716a2`; create a new source-bound attempt |
| `lifecycle-20260802T165525-279cf1dc` | `85867e1` | PASS: 11/11 preflight, Airflow 18/18, real CUDA training, MLflow, readiness 13/13, isolated CT 18/18, local-staging approval/deploy, CUDA serving and Prometheus; 10/10 stages complete | eight intended side effects; bounded single-GPU handoffs; cleanup restored exact production B0 1/1 and staging 0/0 | accepted golden baseline; result under F-drive `validation/integrated-attempt-result.json`; proceed to E on a new immutable attempt |

## Synchronization Rule

Each meaningful checkpoint updates this ledger, Git commit/push, the matching
Jira issue and evidence comment, the Notion execution page, and the Obsidian
work log/current context/index/graph. A plan, partial implementation, failed
attempt, or historical baseline is never recorded as a completed proof.
