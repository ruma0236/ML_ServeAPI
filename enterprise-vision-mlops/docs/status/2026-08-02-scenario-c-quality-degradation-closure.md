# Scenario C Quality Degradation Closure

Date: `2026-08-02`
Issue: `EVM-268 / SCRUM-174`
Scope: non-disruptive local batch drift and retraining-candidate governance
Verdict: PASS for the admitted scope

## Claim Boundary

This closure proves a deterministic, single-node local VisA batch workflow. It
does not prove organic production drift, delayed-label effectiveness, real-user
traffic, business impact, continuous online learning, multi-node scale, HA, or
an enterprise SLA.

The real candidate remains on manual hold. Candidate training, an MLflow run,
isolated CT, and limited deployment were not executed. Scenario E remains a
hard dependency for that release branch. No production promotion or deployment
intent was created.

## Source And Evidence

- executable source: `3c165ef8b44bcf1340c8688355414a0d77dd2942`
- branch: `codex/mac-mini-worker`
- run: `scenario-c-20260802T051154Z-3c165ef8`
- evidence root:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/scenario-c/scenario-c-20260802T051154Z-3c165ef8`
- report SHA-256:
  `033a54efa0a4a8feb4bdde6e241a7a629bfe9cd1d9fa365d4fde4e7552f814cf`
- evidence-index SHA-256:
  `eaebe08589558ec4b3aaeb3902613f237216ec27596a676d931d46a05c732230`

Immutable inputs:

| Identity | SHA-256 |
|---|---|
| VisA shard index | `8b6f281abc93e735545e060b0f6d8b8b7e506d8a0e296f45c84347cb44a91115` |
| production B0 model | `abcb8504a36c1128d32021722cfedce6357fd73598a52f6c2a0d60aca9d9a27f` |
| isolated CT manifest | `7635a75aa7d5a5fd66b4dbb121203a809eceef637f8909c153899c53b4492566` |
| derived `pcb3` manifest | `382685a478e4f3f62ec858e157c9388156550311dd71b995208e87cf0f23cee6` |

## Real CUDA Result

Runtime: NVIDIA GeForce RTX 4080 SUPER, Torch `2.13.0+cu126`, torchvision
`0.28.0+cu126`, peak allocated GPU memory `712.944 MiB`.

| Window | Records | Accuracy | Macro F1 | Decision |
|---|---:|---:|---:|---|
| validation baseline | 2,136 | 0.942416 | 0.822777 | reference |
| known-good test | 2,181 | 0.937643 | 0.790944 | `within_policy` |
| deterministic `pcb3` shift | 205 | 0.907317 | 0.753184 | `review_required` |

Triggered shift rules:

| Metric | Observed | Threshold |
|---|---:|---:|
| input-category JS | 0.742829 | 0.10 |
| confidence PSI | 0.548460 | 0.10 |
| mean-confidence drop | 0.087221 | 0.05 |

Accuracy drop `0.035099`, macro-F1 drop `0.069593`, predicted-class JS
`0.009899`, and low-confidence-rate increase `0.072371` remained below their
configured blockers. The decision used the complete ordered policy rather than
one selected passing metric.

Batch decision time was `37.891186 s` against the local `<=300 s` target.
End-to-end measured recovery/retention time was `37.992140 s`.

## Governance Result

- review event: `quality-review-462c8c17b5f5ccdf9b53`
- event fingerprint:
  `462c8c17b5f5ccdf9b53fb813de3d0f9f80842034a59e3d518a36d4244514524`
- retraining candidate: `retrain-a10f9ca12840729ebded`
- candidate digest:
  `a10f9ca12840729ebded989d0aa2273efcd16681dc0e6f17ca3598763bcd994d`
- three registration attempts converged to one event and one candidate;
- duplicate and stale retries did not overwrite the original record;
- the real gate returned `blocked` with `manual_hold` and
  `candidate_training_not_run`;
- deployment intent count was `0`, and `deployment-intents.jsonl` was empty;
- hold, reject, same-actor approval, expired approval, and Scenario E-open
  fixtures all blocked with exact reasons;
- a fully valid fixture produced only `limited_release_handoff`, never a
  deployment or production mutation.

## Independent Validation

- common operational evidence validator: live-proof PASS with zero errors;
- evidence index: `17 / 17` files exist and match SHA-256;
- report checks: `6 / 6` preconditions and `10 / 10` postconditions PASS;
- related operational/drift tests: `62 / 62` PASS;
- Ruff, Python compile, PowerShell parser, and diff checks PASS;
- production B0 stayed at deployment UID
  `cfdab424-dcc5-4d5f-a46f-ae7530441ef4`, `1 / 1 Ready`;
- before/after model candidate and digest remained exact B0;
- before/after Prometheus target `evm-b0-production` remained `up`;
- lifecycle worker and Kubernetes observer remained live with matching runtime
  revision.

## Failure And RCA History

Attempt 1, source `5315099`, stopped before evidence creation because Windows
PowerShell promoted Docker stderr to a terminating error and hid the traceback.
Commit `43e1164` preserves native output and evaluates the Docker exit code
separately. Jira evidence: comment `10468`.

Attempt 2, source `43e1164`, failed closed before CUDA inference because the
PowerShell-generated supervisor JSON contained a UTF-8 BOM. Commit `3c165ef`
uses BOM-safe strict decoding and adds a regression fixture. Jira evidence:
comment `10469`.

Neither failed attempt created a Scenario C evidence directory or changed raw
data, production B0, traffic, the device plugin, or a deployment intent.

## Portfolio Evidence

Factual claims:

- implemented and executed deterministic real-VisA CUDA batch drift evidence;
- bound data, model, source, CT, derived-manifest, event, and candidate
  identities with content digests;
- proved idempotent duplicate/stale handling and fail-closed human approval;
- retained the exact production B0 route while a review candidate was held.

Interview trade-offs:

- category-mix shift is deterministic and reproducible but not organic concept
  drift;
- automatic candidate creation reduces response latency, while manual review,
  Scenario E, evaluation, and isolated CT prevent automatic promotion;
- a persistent content-addressed registry improves retry safety but still needs
  a production database and concurrency/load evidence before scale claims.

## Remaining Release Branch

Scenario E must close data/artifact integrity before this candidate can enter
real training, MLflow tracking, isolated CT, independent approval, or a limited
deployment controlled by Scenario B. Those activities are not silently marked
complete by this non-disruptive closure.
