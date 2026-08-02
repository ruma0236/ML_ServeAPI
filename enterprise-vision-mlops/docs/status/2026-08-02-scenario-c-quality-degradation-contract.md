# Scenario C Quality Degradation And Retraining Gate Contract

Date: 2026-08-02
Issue: `EVM-268 / SCRUM-174`
State: `C0 PASS - implementation in progress`

## Purpose

Scenario C proves that a measured batch data or model-quality change creates
one review event and one reproducible retraining candidate, while every model
promotion path remains fail closed until evaluation, isolated CT, independent
approval, and upstream integrity evidence are complete.

Candidate creation is not model training. Approval is not deployment.
No Scenario C signal may automatically replace the production model.

## Dependency Boundary

The Stage 2 plan originally orders Scenario E before Scenario C. The current
execution order starts C first by explicit user direction, but does not waive
the safety dependency:

- C1-C6 non-disruptive implementation and evidence may proceed;
- a candidate may be generated and evaluated as an immutable proposal;
- `scenario_e_integrity_passed=false` is a mandatory release blocker;
- approved fixture evidence may prove gate semantics, but cannot create a
  deployment intent in the real run;
- limited release and production promotion remain blocked until Scenario E
  exits and the existing Scenario B release controls admit the exact candidate.

## Immutable Baseline

- dataset: `visa-open-data-e35d93d5561f`;
- current shard index SHA: `8b6f281abc93e735545e060b0f6d8b8b7e506d8a0e296f45c84347cb44a91115`;
- stable model: `effnet-b0-img224-expedited-adamw`;
- stable model SHA:
  `abcb8504a36c1128d32021722cfedce6357fd73598a52f6c2a0d60aca9d9a27f`;
- isolated CT snapshot:
  `ct-visa-open-data-e35d93d5561f-test-c6b466afb907`;
- CT manifest SHA:
  `7635a75aa7d5a5fd66b4dbb121203a809eceef637f8909c153899c53b4492566`;
- source data is read only; all generated evidence is written below the
  F-drive Scenario C artifact root.

## Measurement Windows And Safe Shift

The real proof uses fresh CUDA predictions over immutable VisA records.

| Window | Source | Minimum | Purpose |
|---|---|---:|---|
| baseline | validation, all product classes | 2,000 | reference distribution and model quality |
| known-good | test, all product classes | 2,000 | controlled no-shift false-alert check |
| shifted | test, `pcb3` only | 150 | deterministic category-mix degradation |

The shift recipe selects existing records by pinned split, category, seed, and
content digest. It writes a derived selection manifest; it never edits, copies,
or transforms a raw image.

## Signal Policy

Evidence integrity has precedence over statistical drift.

1. identity: dataset, shard, model, CT, source revision;
2. schema and quality: required fields, missing values, duplicate sample/content
   identifiers, label and content-digest coverage;
3. data distribution: input-category Jensen-Shannon divergence;
4. model behavior: prediction JS, confidence PSI, confidence drop, low-confidence
   rate increase, accuracy drop, and F1 drop.

| Signal | Policy |
|---|---:|
| missing required field | `0` |
| duplicate sample ID | `0` |
| duplicate content digest | `0` within each measured window |
| label/content digest coverage | `1.0` |
| input-category JS | `<= 0.10` |
| predicted-class JS | `<= 0.05` |
| confidence PSI | `<= 0.10` |
| mean-confidence drop | `<= 0.05` |
| low-confidence-rate increase | `<= 0.10` |
| accuracy drop | `<= 0.05` |
| F1 drop | `<= 0.10` |

Missing, stale, conflicting, or identity-mismatched evidence returns
`blocked_invalid_evidence` and creates no candidate. A valid threshold breach
returns `review_required` and holds the stable model.

## Event And Candidate Contract

- event fingerprint is SHA-256 over policy, immutable identities, window
  digests, measured values, and triggered rules;
- replaying the same fingerprint three times creates one event and one
  candidate; conflicting payload under the same identity fails closed;
- candidate identity binds the event, derived data manifest, training profile,
  baseline model, source revision, environment, and requested CT snapshot;
- generated candidate state is `awaiting_manual_review`;
- `automatic_training`, `automatic_deployment`, and `automatic_promotion` are
  always false;
- registry writes are atomic and content-addressed.

## Evaluation And Approval Gate

The gate supports independent fixtures and immutable audit records for:

- `manual_hold`: candidate remains blocked for investigation or labeling;
- `rejected`: candidate is terminally rejected with an actor and reason;
- `approved`: requires passing evaluation and isolated CT, separation of
  duties, exact identities, and Scenario E completion.

Approval alone never deploys. A fully eligible candidate may only produce a
`limited_release_handoff` for Scenario B controls. The real C run must produce
zero deployment intents because Scenario E is open.

## Local SLI/SLO Targets

| SLI | Local target |
|---|---:|
| batch decision latency | `<= 300 s` |
| duplicate events after three retries | `0` |
| event-to-candidate identity linkage | `100%` |
| known-good false alerts | `0` |
| automatic production mutations | `0` |
| audit decisions with actor/reason/source identity | `100%` |

These are local batch-validation targets, not production SLOs.

## Implementation Units

1. `C1`: strict signal, event, candidate, evaluation, approval and gate models.
2. `C2`: deterministic VisA windows, derived manifest and real CUDA collector.
3. `C3`: content-addressed event/candidate registry and retry idempotency.
4. `C4`: evaluation, CT, hold/reject/approve and separation-of-duties gates.
5. `C5`: evidence index and common operational live-proof report.
6. `C6`: deterministic fixtures, failure/RCA tests and host validator.
7. `C7`: real known-good and shifted VisA execution, sensitivity, closure, and
   four-system synchronization.

Implementation targets are `src/evm/operations/scenario_c_quality.py`,
`src/evm/operations/scenario_c_runner.py`,
`scripts/dev/run_scenario_c_quality.py`, and focused tests.

## Acceptance

- known-good real window remains within policy;
- shifted real window creates exactly one deduplicated `review_required` event;
- three identical retries create no duplicate event or candidate;
- malformed identity creates neither event nor candidate;
- candidate lineage covers source revision, dataset/window/profile/model/CT
  identities at 100%;
- hold, reject, same-actor approval, stale approval, and E-not-passed fixtures
  block with exact reason codes;
- an approved fully valid fixture yields only a limited-release handoff, never
  a deployment or production mutation;
- real run remains on manual hold and records zero deployment intent;
- machine evidence validator and focused tests pass.

## Evidence And RCA

Original evidence root:
`F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/scenario-c`.

Every run retains policy, source identities, derived manifest, baseline/current
summaries, metrics, decision, event, candidate, approval audit, gate result,
artifact hashes, common report, failures, and limitations. Failed attempts are
immutable and may be superseded, never rewritten.

## Portfolio Claim Boundary

Supported after proof: deterministic local VisA batch drift and model-quality
governance, idempotent review/candidate creation, human approval gates, and
fail-closed prevention of automatic production promotion.

Not supported: organic production concept drift, delayed-label effectiveness,
real user traffic, business KPI impact, continuous online learning, multi-node
scale, HA, or enterprise SLA compliance.
