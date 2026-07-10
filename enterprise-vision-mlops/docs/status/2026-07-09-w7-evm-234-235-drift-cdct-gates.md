# 2026-07-09 W7 EVM-234/235 Drift CD/CT Gates

> Superseded completion interpretation, 2026-07-10: this document proves the
> read-only UI/schema baseline only. `EVM-234` and `EVM-235` are reopened for a
> measured B7 `review_required` event and CI-gated deployment state machine.
> See `docs/reviews/2026-07-10-w7-portfolio-readiness-reprioritization.md`.
> The executable EVM-235 implementation superseding this read-only baseline is
> recorded in `docs/status/2026-07-10-w7-evm-235-ci-deployment-intent.md`.

## Scope

This closes the W7 read-only risk and release gate surface for:

- `EVM-234`: drift detection and retraining trigger surface.
- `EVM-235`: CD/CT push verification and promotion gate.

The Control Panel dark theme was also adjusted from a green-tinted dark palette
to a black/neutral charcoal base. Status colors remain visible for pass, warn,
running, and blocked states.

## Implementation Files

- `src/evm/control_panel/drift.py`
- `src/evm/control_panel/cdct.py`
- `src/evm/control_panel/aggregation.py`
- `src/evm/control_panel/schemas.py`
- `contracts/control-panel/control-panel.openapi.json`
- `contracts/control-panel/examples/cycle-run.json`
- `apps/control-panel/src/api/types.ts`
- `apps/control-panel/src/views/DriftReview.tsx`
- `apps/control-panel/src/views/CDCTGatePanel.tsx`
- `apps/control-panel/src/views/GateAndRiskPanel.tsx`
- `apps/control-panel/src/styles.css`
- `tests/test_control_panel_drift.py`
- `tests/test_control_panel_cdct.py`
- `tests/control-panel/gate-risk.contract.test.ts`
- `tests/control-panel/gate-risk.spec.ts`
- `tests/control-panel/all-tabs-visual.spec.ts`

## Result

- `CycleRun.drift` now exposes:
  - separate data drift and prediction drift states
  - reference/current dataset versions
  - drift report URI
  - review queue count
  - severity
  - recommended action
  - retraining candidate requirement
- `CycleRun.cdct_gate` now exposes:
  - separate CI, CD, and CT states
  - required/passed/failed checks
  - gate report URI
  - promotion decision
  - block reason
  - per-check verification summary
- The Control Panel Gates tab now renders:
  - Promotion Gate blockers
  - Drift Review action rail
  - Reference/current dataset visibility
  - CD/CT check matrix
  - Promotion blockers and block reason
- All Control Panel tabs were captured across desktop and mobile viewports.
- Dark theme base colors were changed to black/neutral charcoal.

## Evidence Root

Source-of-truth evidence:

`F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/drift_cdct/evm-234-235-20260709T2302/`

Key files:

- `chromium-gate-risk.png`
- `MobileChrome-gate-risk.png`
- `all_tabs/chromium-overview.png`
- `all_tabs/chromium-readiness.png`
- `all_tabs/chromium-timeline.png`
- `all_tabs/chromium-operate.png`
- `all_tabs/chromium-gates.png`
- `all_tabs/MobileChrome-overview.png`
- `all_tabs/MobileChrome-readiness.png`
- `all_tabs/MobileChrome-timeline.png`
- `all_tabs/MobileChrome-operate.png`
- `all_tabs/MobileChrome-gates.png`
- `all_tabs/contact-sheet-desktop.png`
- `all_tabs/contact-sheet-mobile.png`

## Verification

Commands run:

- `python -m py_compile src\evm\control_panel\drift.py src\evm\control_panel\cdct.py src\evm\control_panel\aggregation.py src\evm\control_panel\schemas.py`
- `python -m json.tool contracts\control-panel\examples\cycle-run.json`
- `python -m pytest tests\test_control_panel_contract.py tests\test_control_panel_aggregation.py tests\test_control_panel_enterprise_readiness.py tests\test_control_panel_drift.py tests\test_control_panel_cdct.py tests\test_w7_real_test_policy.py tests\test_control_panel_tasks.py tests\test_control_panel_commands.py -q`
- `npm --prefix apps/control-panel run lint`
- `npm --prefix apps/control-panel run test`
- `npm --prefix apps/control-panel run build`
- `npm --prefix apps/control-panel run test:e2e -- --grep '@w7-drift-cdct|@w7-all-tabs-visual'`
- `npm --prefix apps/control-panel run test:e2e`

Results:

- Python tests: `29 passed`.
- Frontend contract tests: `6 files / 15 tests passed`.
- Frontend build: passed.
- Targeted Playwright drift/CDCT and all-tab visual run: passed after locator
  correction.
- Full Playwright E2E: `14 passed` across `chromium` and `MobileChrome`.
- UI screenshot review: desktop and mobile contact sheets show all tabs with a
  black/neutral dark base and no obvious component overlap.

## Synchronization

- Git implementation commit: `c85163e`
- Jira:
  - `SCRUM-112` transitioned to Done, comment `10195`
  - `SCRUM-113` transitioned to Done, comment `10196`
  - `SCRUM-98` parent epic comment `10197`
- Notion:
  - W7 acceptance matrix comment:
    `39810ad2-dcad-812b-b6fb-001d13f769ab`
  - Knowledge Base comment:
    `39810ad2-dcad-81ae-95cb-001d2c477f0c`
- Obsidian:
  - `F:/mlops_obsidian_db/mlops/08_Codex_Memory/01_Work_Logs/2026-07-09 W7 EVM-234 235 Drift CDCT Gates.md`
  - Current Context Pack, Retrieval Index, and Work Log Graph updated with the
    `c85163e` handoff.

## Boundaries

- This is a read-only risk/release gate surface.
- It does not execute live retraining, rollback, or promotion mutation.
- `EVM-226`, `EVM-227`, `EVM-237`, `EVM-238-B`, and W7 closeout `EVM-228`
  remain open.
