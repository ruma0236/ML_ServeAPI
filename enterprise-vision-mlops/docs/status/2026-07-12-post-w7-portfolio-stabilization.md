# Post-W7 Portfolio Stabilization

## Decision

EVM-214 is complete. The post-W7 replay found a real reproducibility defect,
fixed it, and verified the correction with all 10,821 VisA records on both the
Windows host and the Airflow scheduler container runtime. The selected
EfficientNet-B7 candidate remains `ready` even after mutable latest data moves
to a different dataset version.

This does not change the top-level legacy `vision-baseline v11` CycleRun to a
promotable state. That lifecycle remains `blocked` by its historical model and
drift evidence; the selected B7 artifact-readiness decision is a separate,
passing claim.

## Defect And Root Cause

An Airflow-container data rerun changed mutable latest evidence and regressed
the selected B7 readiness from `ready` to `blocked`. The observed blockers were
dataset metadata, quality-report version, and source-shard digest mismatches.

Four causes were corrected:

1. Dataset intake compared container-visible files against the original
   F-drive root, allowing absolute runtime paths to leak into record identity.
2. Record digests included the runtime-specific `image_uri`.
3. Whole-file shard SHA values included host/container paths and trace fields.
4. Readiness aggregation read mutable latest data artifacts instead of the
   evidence that belonged to the selected model run.

## Implementation

- `src/evm/core/dataset.py` defines canonical record and semantic shard
  identity independent of host/container mount paths.
- `src/evm/core/data_intake.py` and the intake pipeline bind canonical identity
  to the mapped runtime root.
- `src/evm/core/readiness_snapshot.py` creates digest-bound, immutable
  model-run evidence snapshots and rejects conflicting rewrites.
- EfficientNet training captures the snapshot automatically for future runs.
- The CycleRun aggregator and readiness evaluator validate the selected
  candidate against its snapshot rather than mutable latest data.
- The backfill and verification scripts make the historical B7 correction
  explicit and reproducible.
- Control Panel E2E scenarios run with one worker by default because they share
  live API state and F-drive evidence paths. Parallelism remains opt-in through
  `EVM_CONTROL_PANEL_E2E_WORKERS`.

## Full VisA Cross-runtime Proof

| Evidence | Windows host | Airflow scheduler container |
|---|---:|---:|
| Dataset version | `visa-open-data-e35d93d5561f` | `visa-open-data-e35d93d5561f` |
| Manifest digest | `e35d93d5561f15fcf3d8f170fbad69e231cb3ecb3b9361bbcf3d7aeec07ef856` | same |
| Records | 10,821 | 10,821 |
| Split | 6,504 / 2,136 / 2,181 | same |
| Semantic shard SHA | `64043adeeca6654467b842c7b5bb8fc64ce8a0b2c78ca158623164f829a38cd0` | same |
| Compared identity fields | 10 | 10 |
| Mismatches | 0 | 0 |

The container execution proves path and runtime reproducibility inside the
Airflow scheduler image. It was run directly in that container; it is not
presented as a successful scheduled DAG task instance.

## Immutable B7 Readiness Proof

- Candidate: `effnet-b7-img600-finetune-adamw`.
- Selected dataset: `visa-open-data-f1f1c9ee9922`.
- Mutable latest dataset after replay: `visa-open-data-e35d93d5561f`.
- Readiness: `readiness-f246cfd691261294`, decision `ready`, blockers `[]`.
- Snapshot manifest SHA:
  `73f8a1c3cb5c8fe5e86503efefed58930882874e2d4eba927226e1704bb69827`.
- Dataset metadata, source shard, and quality checks all resolve under the
  candidate run's `_readiness_inputs` directory and pass digest validation.

The live API exposes this passing B7 readiness while retaining the blocked
legacy CycleRun state, so the Control Panel does not collapse two different
lifecycle claims into one status.

## UI And Regression Proof

- Python regression: 127 passed.
- Frontend contract tests: 19 passed.
- TypeScript and production build: pass.
- Full responsive Playwright replay: 14/14 pass with the default one-worker
  configuration; the focused policy/all-tabs subset also passes 4/4.
- All five Control Panel tabs were captured at Desktop Chrome and Pixel 5
  viewport sizes; the enterprise-readiness view was captured for both.
- Visual inspection found no incoherent overlap, clipping, broken stage ring,
  or dark-theme color regression.
- Production policy evaluation returns
  `two-person-production-approval`, blocks without an approver, and exposes the
  required approval and separation-of-duties checks.

## Verification Commands

```powershell
C:\Users\opop0\miniconda3\python.exe -m pytest -q
npm --prefix apps/control-panel run test -- --run
npm --prefix apps/control-panel run build
npm --prefix apps/control-panel run test:e2e -- --grep "@w7-enterprise-readiness|@w7-all-tabs-visual"
```

```powershell
$env:PYTHONPATH = "src"
C:\Users\opop0\miniconda3\python.exe scripts/dev/verify_readiness_snapshot.py `
  --config configs/local_visa.toml `
  --require-mutable-mismatch `
  --output F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/portfolio_stabilization/evm-214-20260712T0504Z/readiness_snapshot_after_container_rerun.json
```

```powershell
$env:PYTHONPATH = "src"
C:\Users\opop0\miniconda3\python.exe scripts/dev/verify_cross_runtime_data_identity.py `
  --config configs/local_visa.toml `
  --runtime-label windows-host `
  --output F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/portfolio_stabilization/evm-214-20260712T0504Z/windows-host-identity.json
```

## Evidence Index

Primary root:

`F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/portfolio_stabilization/evm-214-20260712T0504Z/`

- `windows-host-identity.json`: host identity capture.
- `airflow-container-identity.json`: container comparison with zero mismatch.
- `readiness_snapshot_verification.json`: initial immutable snapshot proof.
- `readiness_snapshot_after_container_rerun.json`: isolation proof after latest
  data changed.
- `readiness_snapshot_after_full_ui_replay.json`: isolation proof after the
  complete Control Panel E2E replay.
- `ui/all-tabs/`: five tabs for desktop and mobile.
- `ui/enterprise-readiness/`: promotion/readiness evidence for both viewports.
- `ui-full/`: complete 14-scenario desktop/mobile Playwright evidence.

## Remaining Boundaries

- The scheduled Airflow DAG task instance should be replayed separately when
  DAG-level operational evidence is required; this pass isolates runtime and
  data identity correctness.
- Full VisA processing is I/O-heavy on the F-drive. Stage-level timing and
  cache/parallel-read optimization belong in a separate performance task so
  reproducibility changes are not mixed with throughput tuning.
- EVM-211 to EVM-213 were subsequently completed with executable contracts and
  explicit design/runtime boundaries. EVM-215 also added live diagnostics and
  an audited drift workflow. See
  `docs/status/2026-07-12-post-w7-governance-diagnostics-closeout.md`.
