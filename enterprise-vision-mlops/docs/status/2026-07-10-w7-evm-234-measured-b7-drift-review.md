# W7 EVM-234 Measured B7 Drift Review

Date: 2026-07-10

## Decision

EVM-234 is complete. The prior queue-count heuristic has been replaced by a
real EfficientNet-B7 inference comparison that emits a review-first event. The
event routes records to label review and approval; it cannot automatically
start retraining, deployment, or promotion.

## Real Inputs

- Candidate: `effnet-b7-img600-finetune-adamw`
- Model SHA-256:
  `f5aafeb1060e10048359cbb78393ea2c7519cfa47f501bf8c6b065373c87fe47`
- Shard index SHA-256:
  `49584d29e7ebf7dd8d8f7e13fb54cd0ba81bdb60a892b92e1d43f80368cc4f7d`
- Baseline: VisA validation split, all product classes, 2,136 records
- Current window: VisA test split, `pcb3` intake, 205 records
- Baseline source observation range: `2026-07-09T14:19:00Z` to
  `2026-07-09T14:19:59Z`
- Current source observation range: `2026-07-09T14:19:44Z` to
  `2026-07-09T14:19:50Z`
- Sample ID overlap: 0
- Content SHA-256 coverage: 1.0
- Runtime: CUDA, NVIDIA GeForce RTX 4080 SUPER

The current window represents one real product-line intake window. No image or
prediction was synthetically perturbed to force the decision.

## Measured Decision

| Signal | Value | Threshold | Result |
|---|---:|---:|---|
| input category Jensen-Shannon divergence | 0.742829 | 0.10 | review trigger |
| predicted class Jensen-Shannon divergence | 0.000128 | 0.05 | within policy |
| confidence PSI | 0.016077 | 0.10 | within policy |
| mean confidence drop | 0.008071 | 0.05 | within policy |
| low-confidence rate increase | 0.012067 | 0.10 | within policy |

Baseline confidence quantiles were P10 `0.872545`, P50 `0.981800`, and P90
`0.999633`. Current quantiles were P10 `0.853174`, P50 `0.981280`, and P90
`0.989394`. The low-confidence rate changed from `3.1835%` to `4.3902%`.

The input composition rule alone exceeded policy. Prediction and confidence
signals remained stable, so this is a label/approval review event rather than
evidence of model degradation.

## Event And Evidence

- Run: `drift-review-20260710T120826Z`
- Event: `drift-cf8be9047505ec32`
- Event type: `review_required`
- Event state: `open`, approval `pending`
- Label-review queue: 128 real current-window records
- Automatic retraining: false
- Automatic deployment: false
- Automatic promotion: false
- Evidence root:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/drift_review/drift-review-20260710T120826Z`

The evidence index pins report, event, baseline predictions, current
predictions, and label-review queue with SHA-256 digests.

## Product Integration

- `scripts/run_pipeline.py drift-review` exposes the modular pipeline.
- `scripts/dev/w7_b7_drift_review.ps1` runs the proof in the pinned CUDA/Torch
  training image.
- `CycleRun.drift` reads the F-drive measured report and review event.
- The existing Gates view shows the metric decision, windows, quantiles,
  triggered rules, queue, approval state, and explicit no-auto-retrain state.
- The Timeline includes a `Measured B7 Drift Review` stage.
- CD/CT remains blocked while the open drift review awaits human handling.

## Verification

- Python: 99 passed
- Frontend contracts: 19 passed
- TypeScript lint and production build: pass
- Full Playwright desktop/mobile regression: 14 passed
- Final drift UI desktop/mobile proof: 2 passed
- CycleRun Pydantic/OpenAPI validation: pass
- Docker Compose config: pass
- Kubernetes model-runtime Kustomize dry-run: pass

## CI And Cross-System Synchronization

- Implementation commit: `81f433f`
- GitHub CI run `29091783811`: success
- CI artifact `8227065139`, archive SHA-256:
  `f004110d3180aa216dffbc29615be94f173801ee596e33d888d405763e1c7959`
- Deployment admission run `29091815311`: success
- Admission artifact `8227073932`, archive SHA-256:
  `df0122e24925f627a8134b6302837d457b2c081ea874ed494d06caec2dfe2ca8`
- Jira Realtime Sync run `29091783807`: success
- Jira EVM-234 / `SCRUM-112`: Done, comment `10243`
- Jira EVM-228 / `SCRUM-106`: In Progress, boundary comment `10244`
- Jira parent Epic / `SCRUM-98`: In Progress, checkpoint comment `10245`
- Notion detail:
  `https://app.notion.com/p/39910ad2dcad81908071f73ff500ca60`
- Obsidian work log:
  `F:/mlops_obsidian_db/mlops/08_Codex_Memory/01_Work_Logs/2026-07-10 W7 EVM-234 Measured B7 Drift Review.md`

## Claim Boundary

This closes the measured EVM-234 review event and UI integration. It does not
claim that the input change is harmful, that retraining is required, or that a
model was promoted. W7 closeout remains blocked by EVM-226 Kubernetes GPU
execution and the EVM-235 real apply/rollback path.
