# Scenario B Integrated Lifecycle Guard Progress

- Work item: `EVM-281` / Jira `SCRUM-189`
- State: implementation complete; clean-source real lifecycle execution pending
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

## Claim Boundary

The implementation supports controlled local release validation. It is not a
real-user A/B router, production Kubernetes canary, HA system or enterprise
SLA. Only new clean-source runs with real VisA, CUDA, MLflow, isolated CT,
hash closure and unchanged stable runtime may close Scenario B.
