# 2026-07-09 W7 EVM-233/236 Enterprise Readiness

> Historical baseline note: this document originally proved the read-only
> UI/schema surface. As of 2026-07-10, `EVM-236` is closed by the artifact-content
> evaluator and `EVM-233` is closed by the executable server-side promotion
> policy. Final `ready` and staging `allow` execution evidence is indexed in
> `docs/status/2026-07-11-w7-kubernetes-b7-closeout.md`.
> The original implementation details below remain as history.

## 2026-07-10 Executable EVM-233 Closure

- Target environment and namespace now change the server-computed promotion
  result instead of changing display state only.
- Staging requires ownership, allow-listed namespace, readiness, and CI.
- Production additionally requires all CD/CT gates, immutable model and image
  digests, a valid rollback reference, an allow-listed namespace, and a distinct
  allow-listed approver.
- `promote_model` and environment-approval command intents recompute policy from
  current server evidence. Non-dry-run intent creation returns HTTP `409` unless
  the result is `allow`.
- Command target namespace and actor are authoritative; spoofed namespace,
  requester, or inline approver parameters are ignored.
- Live evidence:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/promotion_policy/evm-233-verification-20260710T184311/`.
- UI regression evidence:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel_ui/playwright/evm-233-regression-20260710T184751/`.
- Verification: Python `73 passed`, frontend contracts `17 passed`, full
  Playwright desktop/mobile suite `14 passed`, production build passed, and
  live CycleRun schema validation passed.
- Boundary: the current live decision remains correctly `blocked` while
  readiness, CT, and rollback evidence are incomplete. Deployment execution and
  rollback state transitions remain `EVM-235`, and the Docker Desktop GPU
  scheduling blocker remains `EVM-226`.

## 2026-07-10 EVM-233 Synchronization

- Git implementation commit: `6d9ac66`, pushed to
  `origin/codex/mac-mini-worker`.
- Jira: `EVM-233` / `SCRUM-111` transitioned to Done; implementation comment
  `10230`; parent Epic `SCRUM-98` comment `10231`.
- Notion detail:
  `https://app.notion.com/p/39910ad2dcad81a09c03c43a9bf5d0f6`.
- Notion W7 Implementation Acceptance Matrix and Evidence And Run Ledger were
  updated with the completed policy dependency and next handoff.
- Obsidian target:
  `F:/mlops_obsidian_db/mlops/08_Codex_Memory/01_Work_Logs/2026-07-10 W7 EVM-233 Environment Promotion Policy.md`.

## Scope

This closes the W7 read-only enterprise readiness surface for:

- `EVM-233`: enterprise service tenancy and environment scope.
- `EVM-236`: enterprise data/model pipeline readiness checklist.

Dark theme support was added as a UI sub-task only. It does not change W7
implementation priority or completion criteria.

## Implementation Files

- `src/evm/control_panel/org_context.py`
- `src/evm/control_panel/environment.py`
- `src/evm/control_panel/readiness.py`
- `src/evm/control_panel/aggregation.py`
- `src/evm/control_panel/schemas.py`
- `contracts/control-panel/control-panel.openapi.json`
- `contracts/control-panel/examples/cycle-run.json`
- `apps/control-panel/src/App.tsx`
- `apps/control-panel/src/api/types.ts`
- `apps/control-panel/src/views/ServiceScopeFilters.tsx`
- `apps/control-panel/src/views/ReadinessChecklist.tsx`
- `apps/control-panel/src/views/DataModelReadiness.tsx`
- `apps/control-panel/src/styles.css`
- `tests/test_control_panel_contract.py`
- `tests/test_control_panel_aggregation.py`
- `tests/test_control_panel_enterprise_readiness.py`
- `tests/control-panel/enterprise-readiness.contract.test.ts`
- `tests/control-panel/enterprise-readiness.spec.ts`

## Result

- `CycleRun.tenant` now includes owner coverage status and missing owner list.
- `CycleRun.environment` now includes approval policy and promotion blockers.
- `CycleRun.data_pipeline` now exposes source contract, quality, lineage,
  replay/backfill, owner approval, and blockers.
- `CycleRun.experiment_pipeline` now exposes MLflow tracking, evaluation,
  registry, model card, rollback, owner approval, and blockers.
- The Control Panel Readiness tab now renders Enterprise Scope, Owner Coverage,
  Environment Gate, Data Pipeline Checklist, and Model Pipeline Checklist from
  the live API response.
- The Control Panel has a dark/light theme toggle. Dark mode is the default and
  was verified on desktop and mobile screenshots.

## Evidence Root

Source-of-truth evidence:

`F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/enterprise_readiness/evm-233-236-20260709T2245/`

Key files:

- `chromium-enterprise-readiness.png`
- `MobileChrome-enterprise-readiness.png`
- `api-stderr.log`
- `ui-stderr.log`

## Verification

Commands run:

- `python -m py_compile src\evm\control_panel\org_context.py src\evm\control_panel\environment.py src\evm\control_panel\readiness.py src\evm\control_panel\aggregation.py src\evm\control_panel\schemas.py`
- `python -m pytest tests\test_control_panel_contract.py tests\test_control_panel_aggregation.py tests\test_control_panel_enterprise_readiness.py tests\test_w7_real_test_policy.py tests\test_control_panel_tasks.py tests\test_control_panel_commands.py -q`
- `npm --prefix apps/control-panel run lint`
- `npm --prefix apps/control-panel run test`
- `npm --prefix apps/control-panel run build`
- `npm --prefix apps/control-panel run test:e2e -- --grep '@w7-enterprise-readiness'`

Results:

- Python tests: `22 passed`.
- Frontend contract tests: `5 files / 13 tests passed`.
- Frontend build: passed.
- Playwright E2E: `2 passed` across `chromium` and `MobileChrome`.

## Synchronization

- Git implementation commit: `6916cc83`
- Jira:
  - `SCRUM-111` transitioned to Done, comment `10192`
  - `SCRUM-114` transitioned to Done, comment `10193`
  - `SCRUM-98` parent epic comment `10194`
- Notion:
  - W7 acceptance matrix comment:
    `39810ad2-dcad-8105-8f67-001d316bbe6f`
  - Knowledge Base comment:
    `39810ad2-dcad-8156-b36a-001d8c77a2e5`
- Obsidian:
  - `F:/mlops_obsidian_db/mlops/08_Codex_Memory/01_Work_Logs/2026-07-09 W7 EVM-233 236 Enterprise Readiness.md`
  - Current Context Pack, Retrieval Index, and Work Log Graph updated with the
    `6916cc83` handoff.

## Boundaries

- This is a read-only Control Panel readiness surface.
- It does not claim mutation/apply support for Airflow, MLflow, or Kubernetes.
- It does not close `EVM-237` or `EVM-238-B`; EfficientNet real training
  evidence is still required before those can close.
