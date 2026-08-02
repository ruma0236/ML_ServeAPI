# Scenario B Integrated Lifecycle Guard Progress

- Work item: `EVM-281` / Jira `SCRUM-189`
- State: PASS; superseded by the Scenario B integrated closure
- Dependency: Scenario C integrated closure PASS
- Runtime scope: local Docker Desktop, one node, one RTX 4080 SUPER, one stable B0 replica

## Immutable Contract

Scenario B is now a release-bound lifecycle policy rather than a detached
replay report. A versioned pipeline profile can require controlled replay. The
requirement is copied into the immutable LifecycleRun at creation and cannot be
lowered during that run.

At the independent release boundary, the guard accepts one evidence index only
when every indexed artifact hash closes and a lifecycle binding exactly joins:

- run, series, attempt and correlation identity;
- profile and effective configuration digests;
- source revision and clean-state proof;
- candidate ID, actual model digest and candidate summary;
- isolated CT evaluation identity and digest;
- sealed release-submission digest;
- stable B0 model and deployment target identity.

The controlled replay must use 1,000 real holdout requests, at least 500 shadow
observations and exactly 100 deterministic challenger assignments. Candidate
inference must be CUDA-backed. Missing, duplicated, stale, digest-mismatched or
ambiguous evidence fails closed.

## Branches

The quality and runtime branches are separate fresh lifecycle runs.

1. Quality policy `lifecycle-guard-b-quality-v1` is fixed before launch with
   minimum F1 `0.90`. The measured candidate is trained normally; no metric is
   mocked or edited. An observed breach must produce `blocked_admission`,
   `rejected_release`, zero challenger assignment and zero deployment intent.
2. Runtime policy `lifecycle-guard-b-runtime-v1` retains minimum F1 `0.75`.
   Exactly 100 of 1,000 replay requests are assigned to the candidate and two
   deterministic isolated failures produce a 2% challenger error rate above
   the 1% limit. The expected result is `rolled_back`, zero post-decision
   challenger allocation and exact stable B0 restoration.

Both branches must first complete the real data pipeline, Kubernetes GPU
training, MLflow logging, artifact readiness and isolated CT. A release
approval request is then expected to return HTTP 422 from the bound B guard;
the approval stage must remain waiting and deployment intent must remain zero.

## Implementation

- `src/evm/control_panel/lifecycle_release_guard.py`: artifact closure,
  lifecycle identity binding and fail-closed authorization.
- `src/evm/control_panel/lifecycle_runs.py`: immutable profile policy,
  release-guard ledger fields, registration and approval enforcement.
- `src/evm/operations/scenario_b_replay_runtime.py`: optional lifecycle binding
  in the Scenario B evidence index.
- `src/evm/operations/lifecycle_guard_b_runner.py`: two independent full
  lifecycle runs, real CUDA replay, approval-denial proof and restoration.
- `configs/operations/lifecycle_guard_b_{quality,runtime}.toml`: predeclared
  policies; no mid-run threshold change.
- Control Panel profile editor and OpenAPI expose the policy and API boundary.

## Verification At This Checkpoint

- 54 focused Python tests PASS.
- 52 Control Panel tests PASS; TypeScript typecheck and production build PASS.
- Ruff PASS for all touched Python files.
- Real lifecycle and replay execution have not started from this revision, so
  `EVM-281` remains In Progress and no closure claim is allowed.

## Attempt 1 Pre-Launch RCA

The first wrapper invocation stopped during Python discovery, before creating a
LifecycleRun. `C:\Users\opop0\miniconda3\python.exe` could import the project
package and Pydantic but did not contain Torch. With PowerShell native errors
treated as terminating, that failed probe prevented fallback to another
runtime.

- External lifecycle effects: zero. No Airflow run, Kubernetes Job, MLflow run,
  candidate, replay, approval or deployment intent was created.
- Root cause: the wrapper neither listed the established F-drive CUDA Conda
  runtime nor safely continued after a candidate import failure.
- Correction: add `F:\evm_w7_torch\python.exe` as an explicit candidate and
  inspect each probe exit code under a temporary non-terminating native-error
  policy. The selected runtime reports Torch `2.13.0+cu126` and CUDA available.
- Acceptance consequence: this attempt is immutable harness RCA only. Both
  Scenario B lifecycle branches must still start fresh from the corrected,
  committed source revision.

## Attempt 2 Replay Mount-Boundary RCA

Source `ab72f9f` reached the independent release boundary on a fresh real
quality lifecycle, then the replay loader failed closed with
`replay_manifest_record_incomplete` before replay or guard registration.

- Lifecycle: `lifecycle-20260802T222607-175d2b88`; Airflow 18/18 in about
  521 seconds, real CUDA training early-stopped at epoch 4/20, MLflow run
  `9c6838726cf846e5923c5201ef7ca210`, readiness 13/13 and isolated CT 2,181.
- Measured candidate: accuracy `0.962079`, F1 `0.823529`, AUROC `0.973746`,
  training `124.344 s`, peak GPU memory `2846.96 MiB`.
- Root cause: the immutable CT manifest correctly stores image paths beneath
  `/mnt/evm-ct`, but the Windows host replay loader checked that Linux mount
  string as a native drive-absolute path without applying the established
  CT host/mount mapping.
- Side effects: intended pre-release delta only, Jobs +2, MLflow +1, candidate
  +1, deployment intents 0. Replay, release guard and approval did not run.
- Safety closure: automatic cancellation left active runs 0; exact production
  B0 remained 1/1 with the same deployment UID and model digest, CUDA ready,
  supervisor healthy and Prometheus up.
- Evidence index SHA-256:
  `8755646e798121a45fe79674465a3c8ba99a18ba86c50fdf80071a4f5022d6a5`.
- Correction: resolve manifest image paths with the shared runtime host/mount
  mapping before absolute-path and content-digest validation. A dedicated
  `/mnt/evm-ct` fixture prevents recurrence.

Attempt 2 is immutable integrated-pipeline RCA evidence, not Scenario B guard
acceptance. Quality and runtime branches must both run fresh on the corrected
source revision.

## Attempt 3 Pre-Replay Monitoring Convergence RCA

Source `ec2ce22` again completed a fresh real quality lifecycle and reached the
release boundary. The corrected loader passed all 1,000 CT manifest records,
but replay admission took one instantaneous Prometheus snapshot during the B0
restart window after isolated CT and failed closed with
`stable_prometheus_target_not_up` before issuing replay requests.

- Lifecycle: `lifecycle-20260802T224642-da79b5a0`; MLflow
  `464e16a5dab54eb486b85914861361a4`; CT
  `ct-eval-183aa57f0c4cb439`.
- Candidate: accuracy `0.962079`, F1 `0.823529`, AUROC `0.973746`, training
  `122.139 s`, peak GPU memory `2846.96 MiB`.
- Side effects: intended Jobs +2, MLflow +1, candidate +1, intent 0. Replay,
  guard registration and approval did not execute.
- Recovery: the exact stable target was autonomously `up` by the distinct
  Prometheus scrape at `23:00:44Z`, about 22 seconds after the failure record.
  Active runs returned to 0; exact B0 deployment UID/model remained 1/1 CUDA.
- Evidence index SHA-256:
  `71a9806b248baef519ead63632b354ee3726c4977b651a1a5a320452749b8b76`.
- Correction: before replay, require exact B0 UID/replica/CUDA/plugin/revision
  restoration plus two distinct consecutive successful Prometheus scrape
  timestamps within 90 seconds. Timeout remains fail closed with zero replay.

This is convergence-timing RCA, not Scenario B acceptance. Both branches still
require fresh execution from the corrected source revision.

## Integrated Closure

Source `1e541de` passed two fresh real lifecycle branches in series
`scenario-b-lifecycle-20260802T230659Z-1e541de0`.

- Quality: measured F1 `0.823529` against fixed minimum `0.90`,
  `rejected_release`, assignment 0, approval HTTP 422, intent 0.
- Runtime: 100/1,000 exact assignments, two controlled errors, error rate
  `0.02`, `rolled_back`, identity 1,000/1,000, allocation after 0, approval
  HTTP 422, intent 0.
- Detection and logical stable-route containment were `0.016 s`; exact runtime
  restoration with two Prometheus scrapes took `15.437 s` and `22.140 s`.
- Five indexes independently matched 95/95 artifacts; both operational reports
  passed live-proof validation. Full Python tests pass 492/492.
- Detailed evidence: `docs/status/2026-08-03-lifecycle-guard-scenario-b-closure.md`.

## Claim Boundary

The implementation supports controlled local release validation. It is not a
real-user A/B router, production Kubernetes canary, HA system or enterprise
SLA. Only new clean-source runs with real VisA, CUDA, MLflow, isolated CT,
hash closure and unchanged stable runtime may close Scenario B.
