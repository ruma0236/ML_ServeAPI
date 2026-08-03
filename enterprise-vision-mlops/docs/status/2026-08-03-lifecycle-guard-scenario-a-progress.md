# Lifecycle Guard Scenario A Integrated Progress

Status: In Progress
Jira: `SCRUM-190 / EVM-282`, entry comment `10555`

## Acceptance Boundary

Scenario A does not reuse the earlier M0-only Pod recovery as acceptance. It
uses the accepted Scenario D lifecycle package as `M1`, seals the current exact
production B0 package as `M0`, and executes:

1. target-bound prepare, approval, apply, CUDA/readiness/Prometheus verification
   and stable-pointer commit for M1;
2. one exact UID-preconditioned restart of the committed M1 Pod;
3. a separately approved M0 rollback and exact post-rollback verification.

The transaction binds source revision, Deployment UID/resourceVersion, active
Pod UID, lifecycle series/run/attempt/correlation, model/data/image/MLflow/CT
identities, artifact hashes and action digests. Zero/multiple targets, stale
resource versions, changed evidence, approval replay, or unhealthy telemetry
fail closed before the next mutation.

## Fixed Identities

- M0: `effnet-b0-img224-expedited-adamw`, model
  `abcb8504a36c1128d32021722cfedce6357fd73598a52f6c2a0d60aca9d9a27f`.
- M1: lifecycle `lifecycle-20260802T202558-a50d19fe`, model
  `2df0b78a0f792e17b7711e7415380f4f5c17960d8fd8ba57d986a5c65692707e`,
  MLflow `6bde62844771481aa24898454e909f96`, isolated CT
  `ct-eval-ef1b2504186b3c5e`.
- Serving image remains the immutable digest
  `sha256:227b483f466678e00fbf13fd6b3ad1059ca2c6771239d204494fb610fa7d9f7a`.

## Safety And Claims

Only `evm-production/evm-b0-production` is mutable. Device-plugin, data,
registry, staging and unrelated workloads are excluded. The single replica
means M1 apply, exact-Pod restart and M0 rollback can each interrupt the local
endpoint. Results support only a controlled local maintenance-drill claim, not
HA, zero downtime, real production traffic or an SLA.
