# Scenario C Integrated Lifecycle Progress

## State

- Work item: `EVM-280` / Jira `SCRUM-188`
- State: In Progress
- Scope: measured quality/drift review, pre-training hold, and governed resume
- Runtime proof: not started at this checkpoint

## Implemented Contract

The lifecycle now accepts a Scenario C review only when policy, source identity,
review event, retraining candidate, and immutable derived manifest all match
their supplied SHA-256 digests. The review is bound to the exact lifecycle
run, profile/version/digest, effective config, source commit, series, attempt,
and correlation identities.

The model-training handler checks this binding before materializing a training
bundle or creating a Kubernetes task. `review_required`, `manual_hold`,
`rejected`, expired approval, identity mismatch, or action-digest mismatch all
fail closed. An independent `approved_for_training` action can be consumed
once; retry observes the same receipt instead of consuming or creating a
second external action.

The API exposes two explicit operations:

- `POST /control-panel/v1/lifecycle-runs/{run_id}/quality-review`
- `POST /control-panel/v1/lifecycle-runs/{run_id}/quality-review/action`

The action changes training governance only. It cannot approve release, bypass
Scenario E, bypass isolated CT, create a deployment intent, or replace the
served model.

## Verified At This Checkpoint

- focused contract and lifecycle tests: `59 / 59` passed;
- Ruff: passed for the changed Python files;
- exact and stale signal replay retains one event and one candidate;
- requester self-approval is blocked;
- rejection and manual hold block training;
- approval is bound to the exact candidate/run/revision and is single-use;
- held training remains at attempt `0` with no task, runtime ID, training
  bundle, Kubernetes Job, MLflow run, or deployment intent.

## Remaining Before Closure

1. build and restart the API/worker runtime at the new source revision;
2. run real CUDA VisA quality measurement and bind its immutable evidence;
3. prove hold and duplicate/stale replay with zero downstream side effects;
4. exercise actual manual-hold and governed approved-for-training actions;
5. resume the same lifecycle through real GPU training, MLflow and isolated CT;
6. stop at the independent release boundary and prove production B0 unchanged;
7. independently hash all evidence and synchronize the final result.

This checkpoint is implementation evidence, not Scenario C completion.

## Integrated Runner Checkpoint

The source-bound runner now executes three distinct branches without allowing
release mutation:

1. an isolated rejected dry-run branch with an actual API audit;
2. the main run with three exact/stale registrations and a manual hold after
   real Airflow data completion;
3. the same main run resumed by an independent approved-for-training action
   through real Kubernetes GPU training, MLflow and isolated CT, stopping at
   the independent release-approval boundary.

It records pre/hold/post runtime snapshots, exact external identity deltas,
handoff receipts, lifecycle timeline, controlled cleanup and a content hash
index. The source Scenario C evidence index is independently re-hashed before
admission. Hold acceptance requires zero Kubernetes Job, MLflow, candidate and
deployment-intent delta plus unchanged CUDA B0/Prometheus state. Resume
acceptance requires exactly two Jobs, one MLflow run, one candidate, zero
deployment intents, single-use quality approval and exact B0 restoration.

The PowerShell wrapper runs a fresh source-bound real CUDA VisA drift proof
before the lifecycle runner. Runner tests pass `5 / 5`; the complete Python
suite passes `480 / 480`; Ruff and PowerShell parsing pass. Runtime execution
is still pending, so the issue remains In Progress.

## Attempt 1 RCA

Fresh source `427b400` CUDA evidence completed before lifecycle admission:

- run `scenario-c-20260802T211757Z-427b4002`;
- known-good `within_policy`, shifted `review_required`;
- decision `18.476526197 s`;
- event `quality-review-9346197b9b22f4a26000`;
- candidate `retrain-dbef6fe25b9b10bcfc46`;
- production mutation and deployment intent remained false.

The wrapper then selected PATH Python `C:/Python314/python.exe`, which does not
contain the project package, and stopped before creating any LifecycleRun or
Airflow/Kubernetes/MLflow/release action. This is a harness runtime-resolution
failure, not a Scenario C guard result. The wrapper now selects only a Python
runtime that successfully imports both `evm` and `pydantic`, preferring the
configured or project Conda runtime. A new commit-bound CUDA proof and
lifecycle attempt are required.

## Attempt 2 RCA

Source `bd01cdc` produced a fresh real CUDA drift proof in `18.102172372 s`
(`scenario-c-20260802T212544Z-bd01cdcf`). Known-good stayed
`within_policy`; shifted data became `review_required`; no deployment intent
or production mutation was created.

The isolated rejection run `lifecycle-20260802T212614-e8f1d96a` then failed
closed before registration. The API attempted to persist its review under the
read-only `/mnt/evm-data` mount and returned HTTP 500. The run never queued
data, training, MLflow, CT, or deployment work and was explicitly cancelled.

Root cause was a storage-boundary error: `quality_review_path()` mapped the
host artifact URI through the generic read path instead of the API's writable
`EVM_LIFECYCLE_RUN_ROOT`. Quality evidence now uses the configured writable
lifecycle root, with a regression test that separates host/read-only and
API/write mount semantics. A new source-bound run remains required.
