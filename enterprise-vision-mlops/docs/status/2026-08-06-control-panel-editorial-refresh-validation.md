# Control Panel Editorial Refresh And Transformer Evidence Validation

Date: 2026-08-06
Branch: `codex/control-panel-editorial-refresh`
Status: PASS for desktop controlled-local portfolio evidence

## Verdict

The actual Control Panel at `http://127.0.0.1:4173` was refreshed without
replacing the underlying lifecycle, API routes, state transitions or release
contracts. The resulting desktop UI exposes one operational path:

`intake -> pipeline -> train/evaluate -> release/deploy -> observe`

The proof uses stored immutable lifecycle evidence and live host/runtime
telemetry. It is not a reconstructed portfolio mock. A new real VLM lifecycle
was also executed on the RTX 4080 SUPER to verify that the VLM metric schema,
release evidence and retired staging state are rendered from actual artifacts.

Mobile visual acceptance is intentionally excluded from this closure following
the user direction on 2026-08-06.

## UI And Data-Flow Changes

- Reorganized navigation into `Overview`, `Build`, `Deploy` and `Govern`
  workspaces while preserving every existing view and action.
- Added the five-stage lifecycle map so the current workspace remains connected
  to the end-to-end operating flow.
- Applied the technical-editorial themes: black/white/fluorescent green in dark
  mode and white/black/orange in light mode.
- Reduced panel height and duplicated explanation while retaining operational
  identifiers, blockers, diagnostics, actions and keyboard/accessibility state.
- Added status filters to the immutable AI Workload ledger. Failed records stay
  available for RCA and are never rewritten as completed.
- Split the 5-second live control-plane refresh from the 30-second historical
  cycle catalog refresh. Parsed historical evidence is reused through a bounded
  cache rather than rereading 500 JSON files for every request.
- Added a read-only evaluation summary to the scenario-workload API. It resolves
  the run's real evaluation and training artifacts; it does not invent metrics.

## Accepted VLM Refresh Run

- Run: `scenario-workload-20260805T211030-dcd99a4b`.
- Source revision: `70f2a8adc5ec50bee1cea00ccf25b500c7141f68`.
- Model: `HuggingFaceTB/SmolVLM-500M-Instruct` at
  `a7da5b986cb59b408707209984f360a5f4ad7e47`.
- Data: ScienceQA image-bearing deterministic view, 48 records split
  `32/8/8`; data identity `8bce3a7a...bed7a1`.
- Training: real CUDA BF16 LoRA, 8 steps, 409,600 trainable parameters,
  6.581508 seconds.
- Evaluation: 6/8 correct (`accuracy=0.75`), 8/8 parsed
  (`parse_rate=1.0`), p95 `0.615524 s`.
- Compute: peak allocated `7,268.164 MiB`; peak reserved `8,374 MiB`.
- MLflow run: `c62d1734e89e4203ad015a494a340b59`.
- Adapter digest:
  `9135f9045b92f2bb5914818e47d168187c327307b2ff2826a4d319b78cb6534b`.
- Staging proof: real CUDA response `2`, latency `1.079727 s`, Prometheus
  target observed up, then bounded staging retired.
- Evidence index:
  `f29508f7eaa938447f21093e31583414740dc64a4bc2ee96dd1c522133cd6061`.

## Metric Contracts

| Family | Quality metrics | Calculation | Release evidence |
|---|---|---|---|
| VLM | `accuracy`, `parse_rate` | correct parsed answers / evaluated records; parsed answers / evaluated records | VLM local guardrails in the sealed `training-result.json` |
| LLM | `validation_loss`, `mean_token_f1`, `nonempty_rate` | held-out mean loss; mean generated/reference token-overlap F1; non-empty generations / generated records | LLM loss-regression and non-empty guardrails in the sealed `training-result.json` |
| Common | p95 latency, evaluated records, peak GPU MiB, training seconds | nearest-rank p95 over measured inference latency; artifact counts and runtime measurements | displayed separately from family quality metrics |

The UI changes schema when the selected model family changes. The accepted LLM
run `scenario-workload-20260805T121811-dcee8c89` displays loss `1.918441`, token
F1 `0.343650`, non-empty rate `1.0`, p95 `5.113666 s`, 8 evaluated records,
peak allocated `1,951.45 MiB` and training time `8.432306 s`.

The API returns release `pass` only when the training artifact status is `pass`
and `promotion_blockers` is empty. A recognized failed/blocked status or any
blocker returns `blocked`; missing or unresolved evidence returns `unavailable`.

## Failure And RCA

The first refresh validation attempt,
`scenario-workload-20260805T210910-68a82698`, was not counted as accepted.
PowerShell `ErrorActionPreference=Stop` promoted a nonfatal Torch stderr message
(`triton not found; flop counting will not work`) into a terminating wrapper
error. The exact run was marked failed at 30%, its GPU lease/fencing state was
released, and no downstream stage was marked complete. The retry used a wrapper
that preserved Torch stderr while relying on the process exit code. This is
recorded as automation RCA rather than model or CUDA failure.

## Verification

- TypeScript lint: PASS.
- Production frontend build: PASS.
- UI unit tests: `17` files, `56` tests PASS.
- Python cycle-catalog and scenario-workload API tests: `7` PASS.
- Desktop Playwright acceptance: all views/no-horizontal-overflow, Gate/CDCT,
  Pipeline Studio version/save/replay/dry-run paths, `3` focused tests PASS.
- Mobile acceptance: skipped by user direction; not part of the verdict.
- Manual desktop browser review: Overview, Pipeline Studio, AI Workloads VLM and
  LLM schemas, Models, Readiness, Quality/Drift, dark and light themes PASS.
- Browser console/page errors during recorded demo: `0`.
- A combined 22-test desktop/mobile invocation exceeded the 180-second command
  envelope. It produced no valid aggregate verdict; the acceptance suites were
  rerun in bounded groups and passed.

Runtime invariants after validation:

- Control Panel, API, Airflow, MLflow and Prometheus readiness endpoints: HTTP
  `200`.
- Kubernetes `evm-b0-production`: `1/1` ready and available.
- Active scenario GPU lease: `null`.
- Bounded staging port `30920`: closed after validation.
- Existing production B0, device plugin, canonical source data and cluster-wide
  resources were not mutated by the VLM refresh run.

## Visual And Video Evidence

Artifact root:
`F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/control-panel-editorial-refresh/2026-08-06`

- Before screenshots: `before/screenshots/`.
- Final desktop screenshots: `after/screenshots/`.
- Raw recording: `video/raw/control-panel-vlm-llm-demo.webm`.
- Final video: `video/final/control-panel-vlm-llm-demo.mp4`.
- Recording manifest: `video/recording-manifest.json`.
- Seek proof frame: `video/final/seek-proof-00m30s.png`.
- Transformer-only 60 fps video:
  `video/60fps/final/control-panel-vlm-llm-demo-60fps.mp4`.
- 60 fps recording manifest: `video/60fps/recording-manifest-60fps.json`.
- 60 fps visual review frames: `video/60fps/final/review-frames/final-*.png`.
- Accepted VLM lifecycle log: `validation/real-vlm-lifecycle-retry.log`.
- Failed wrapper evidence:
  `artifacts/scenario_workloads/scenario-workload-20260805T210910-68a82698/failure.json`
  and `workload_run.json` under the F-drive data root.

The final video is 67.53 seconds at 1440x900. It uses H.264 High/yuv420p at
30 fps, AAC-LC stereo, a fixed two-second keyframe cadence and a front-loaded
`moov` atom (`moov` byte 36, `mdat` byte 75,649). A direct seek to 30 seconds
decoded the VLM metric frame successfully.

### Transformer-Only 60 FPS Correction

The 60 fps delivery is a separate corrected recording. It does not visit the
global EfficientNet deployment, readiness or drift views. The sequence is the
completed SmolVLM lifecycle and VLM metric schema, its data/compute/artifact
identity, the completed Qwen lifecycle and LLM metric schema, and the exact
MLflow FINISHED runs for experiments `9` and `10`. The selected workload view
also labels its footer as an independent run-evidence context instead of
repeating the unrelated classification CycleRun identifier.

- VLM run: `scenario-workload-20260805T211030-dcd99a4b`.
- LLM run: `scenario-workload-20260805T121811-dcee8c89`.
- Source capture: `3,521` frames over `58.75` seconds, measured `59.932 fps`,
  zero duplicated frames and zero dropped frames.
- Delivery: 1440x900 H.264 Main/yuv420p at exact 60 fps with AAC-LC stereo,
  two-second GOP and front-loaded `moov` (`moov` byte `36`, `mdat` byte
  `95,437`).
- Cadence normalization: `minterpolate=fps=60:mi_mode=blend`; the source and
  delivery cadence are recorded separately rather than presenting the
  normalized stream as a native 60.000 fps capture.
- Browser console/page errors: `0`; shell right edge `1,419` inside a
  1,440-pixel viewport; direct 30-second seek and nonblank pixel proof PASS.
- Video SHA-256:
  `1CB91CAC59682733CA1B68C568526D0C47711EEAB5AD5257F84212BD40365BD6`.

Earlier experimental 60 fps captures that showed the wrong desktop window,
clipped the right edge, mixed global EfficientNet views, or produced excessive
cadence loss are not accepted evidence. They were used only for capture RCA.

## Claim Boundary

This evidence supports a claim that a local single-node MLOps platform can
present real VLM and LLM lifecycle evidence with model-specific metric schemas,
immutable model/data/source identities, MLflow lineage, bounded staging,
release gates and live resource/serving visibility through one control surface.

It does not support claims of customer production, HA, distributed scheduling,
production throughput/SLO, real-user A/B, a ScienceQA benchmark, full-model
fine-tuning, full privacy certification or a large-model production fleet.
