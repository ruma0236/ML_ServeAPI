# Scenario C: Data and Model Quality Degradation

Issue: `EVM-268 / SCRUM-174`
State: non-disruptive local batch proof passed on `2026-08-02`. Scenario E
remains a hard blocker for real candidate training and limited release.

## Purpose

Turn a measured data/model quality change into a review and reproducible
retraining candidate without automatically replacing production.

## Preconditions

- Scenario E integrity gate is required before limited release. Until then the
  candidate/evaluation workflow may run, but release eligibility is false.
- Baseline dataset/model distributions and policy are versioned.
- Train, validation and isolated CT identities are immutable.
- Stable production model and rollback reference remain available.

## Safe Injection

Create a derived F-drive copy with deterministic brightness/noise or category
mix change. Store transform name, parameters, seed, parent digest and output
digest. Never alter canonical VisA data or baseline manifests.

## Signals

Data quality checks, baseline/observed statistics, drift method/window/threshold,
prediction confidence and error slices, lineage, review event fingerprint,
retraining profile, MLflow run, isolated CT result and approval state.

## Controlled Workflow

```text
measured breach -> review_required -> label/data review
-> immutable retraining candidate -> evaluation -> isolated CT
-> independent approval -> limited staging/canary deployment
```

No drift or quality event may directly mutate production.

## Success

- A true threshold breach emits one idempotent event.
- Event records method, window, threshold, observed values and affected slices.
- Repeated identical evidence is deduplicated by fingerprint.
- Candidate is reproducible from source, data, profile and environment identity.
- Promotion remains blocked until evaluation, CT and approval pass.

## Failure and Mitigation

Quarantine the derived dataset/candidate on source mutation, missing lineage,
CT leakage, approval bypass, unsupported method, or unbounded retraining. Keep
the stable model active and record the blocker.

## Interview Evidence

- Demonstrates: drift governance, reproducible retraining and human approval.
- Expected questions: covariate versus concept drift; window/threshold choice;
  label delay; deduplication; CT contamination.
- Claim allowed: controlled degradation and candidate workflow.
- Claim prohibited: proven production concept drift or business impact.

## Latest Verified Run

- source: `3c165ef8b44bcf1340c8688355414a0d77dd2942`
- run: `scenario-c-20260802T051154Z-3c165ef8`
- result: known-good `within_policy`; deterministic `pcb3` shift
  `review_required`;
- volume: baseline `2,136`, known-good `2,181`, shifted `205` records;
- idempotency: three attempts, one event, one candidate;
- release: manual hold, zero deployment intents, production B0 unchanged;
- validation: `17 / 17` artifact hashes, common live-proof PASS, `62 / 62`
  related tests.

Closure detail:
`docs/status/2026-08-02-scenario-c-quality-degradation-closure.md`.
