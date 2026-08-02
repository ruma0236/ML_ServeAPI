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
