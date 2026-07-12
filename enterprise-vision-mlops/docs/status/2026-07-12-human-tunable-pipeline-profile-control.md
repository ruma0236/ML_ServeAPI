# Human-Tunable Pipeline Profile Control

Date: 2026-07-12

## Objective

Provide a schema-aware Control Panel surface where an operator can inspect,
validate, version, and launch supported pipeline parameters without editing
runtime files by hand. The implementation must expose unsupported capabilities
as blockers instead of presenting a one-click lifecycle that does not exist.

## Dry-Run Semantics

| Surface | User action | Validation performed | Runtime mutation |
|---|---|---|---|
| Pipeline Profile Studio | `Validate Profile` | Pydantic contract, split and holdout policy, capability matrix, execution plan | None |
| Task authoring | `Preview Task` | Creates an audited task-assignment preview with runtime/config references | No orchestrator dispatch |
| Command drawer | `Preview Command` | Evaluates and audits the requested command; promotion commands reuse server policy | No target mutation |
| Release control | `Validate Deployment` | CI evidence, readiness, environment/namespace policy, immutable digests, rollback reference | Creates a `dry_run` intent; no Kubernetes apply |

`dry_run` is therefore a guarded intent and evidence preflight. It is not a
short training run, a mock pipeline, or an implicit deployment.

## Implemented Control Surface

- `evm.pipeline_profile.v1` models data, split, model, experiment, gate, and
  resource parameters as a typed contract.
- Profile versions are immutable and content-addressed by SHA-256 digest.
- Validation requires the source manifest and shard/split manifest to exist,
  rejects empty input, and matches the configured identity to the manifest's
  declared immutable identity.
- Base Airflow/model configs are restricted to existing TOML files under the
  repository `configs` allowlist.
- Original evidence is stored under
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/pipeline_profiles`.
- Airflow and model runtime JSON documents are rendered from the same saved
  profile version.
- Airflow accepts a per-run `pipeline_config_uri` through `dag_run.conf`.
- The profile runner skips model/release tasks with exit code `99` when the
  selected scope is `data_cycle`, preventing accidental training or promotion.
- The Configure tab provides a typed form plus an editable full JSON document.
- Timeline, stage detail, and release views use explicit Not Started, In
  Progress, Completed, and Blocked labels with percentages and animated bars.
- Reduced-motion clients receive static progress styling.

## Operational Evidence

| Profile | Result | Evidence |
|---|---|---|
| `standard-b0-manual-tuning/v1` | Valid profile, execution blocked | `capability_not_wired:full_lifecycle_orchestrator` |
| `standard-b0-data-cycle/v1` | Valid and executable data cycle | Airflow and model runtime JSON saved on the F-drive profile root |
| `standard-b0-data-cycle/v1` preview | Audited, not dispatched | Task `task-20260712T104035-9fe95e9f`, state `dry_run` |

The live CycleRun used for UI verification remained the previously validated
real VisA EfficientNet-B0 Production cycle. This change did not retrain or
repromote the model; it added the human tuning and launch-control plane around
the existing evidence.

## Capability Boundary

| Capability | Current state | Behavior |
|---|---|---|
| Versioned profile and F-drive evidence | Wired | Save, list, read, validate, and digest immutable versions |
| Data validation and split tuning | Wired | Renders effective Airflow runtime parameters |
| Manual EfficientNet hyperparameters | Wired as configuration | Renders pinned model config; execution still depends on lifecycle orchestrator |
| Immutable CT holdout policy | Wired as fail-closed validation | Rejects overlap, mutable holdout, split mismatch, and bad digest |
| Airflow data-cycle launch | Wired | Preview or queue guarded task assignment |
| Full lifecycle orchestration | Partial | Data and model runtimes exist but are not one dependency-aware state machine |
| Cross-validation fan-out and aggregation | Not wired | Enabling CV blocks execution |
| Grid/Bayesian trial orchestration | Not wired | Non-manual tuning blocks execution |
| A/B traffic split and evaluator | Not wired | Enabling A/B blocks execution |

## Regression Fixes

Rebuilding the Compose stack initially exposed a real configuration flaw: the
API reverted from the selected B0 Product metadata to the baseline registry
because the selected EfficientNet config had only been supplied as a transient
shell override. `docker-compose.yml` now pins
`configs/w7_b0_expedited_production.toml` as the default. Cycle aggregation also
falls back to immutable CI evidence for `release_ref` when `GIT_COMMIT` is not
injected. The live B0 Production CycleRun and deployment intent were restored
after the rebuild.

## Verification

```powershell
C:\Users\opop0\miniconda3\python.exe -m pytest -q
npm --prefix apps/control-panel run lint
npm --prefix apps/control-panel run test
npm --prefix apps/control-panel run build
npm --prefix apps/control-panel run test:e2e
docker compose config --quiet
```

Results:

- Python: `175 passed` with two existing FastAPI lifespan deprecation warnings.
- Frontend unit contract: `10` files and `26` tests passed.
- Production build: passed.
- Browser E2E: `20 passed` across Chromium and MobileChrome.
- In-app browser at 1440x1000 and 390x844: no horizontal overflow.
- Live timeline: `11 Completed`, `0 In Progress`, `0 Not Started`, `0 Blocked`,
  and `100%` overall progress.

## Next Implementation Order

1. Build a dependency-aware lifecycle run state machine that consumes one
   immutable profile snapshot and coordinates Airflow, Kubernetes, MLflow,
   readiness, approval, deployment, and rollback.
2. Add cross-validation fold fan-out, aggregate metrics, and bounded
   grid/Bayesian trial execution with GPU quotas.
3. Add an isolated CT snapshot evaluator and prove the holdout digest is never
   exposed to training workers.
4. Add a canary/A-B traffic router, metric window, statistical decision, and
   audited rollback.
