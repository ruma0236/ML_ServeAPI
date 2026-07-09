# 2026-07-09 W7 Control Panel v0

## Priority Distribution

W7 implementation is split by dependency and evidence depth:

1. P0 foundation: `EVM-224` live CycleRun aggregation API and `EVM-238-A`
   no-mock/no-smoke policy guard.
2. P1 operator surface: `EVM-225` Control Panel v0 bound to the live CycleRun
   API.
3. P1/P2 UI depth: `EVM-229` Kubernetes topology, `EVM-231` pipeline timeline,
   `EVM-233` tenancy/environment, `EVM-234` drift, `EVM-235` CD/CT, and
   `EVM-236` readiness panels.
4. P2 controls: `EVM-230` task authoring and `EVM-232` command intent/audit
   guardrails.
5. P2 real model execution: `EVM-237` EfficientNet-B0/B7 MLflow/artifact/GPU
   evidence and `EVM-238-B` evidence validation.
6. Closeout: `EVM-228` compressed W6/W7 integration review.

## Summary

`EVM-225` implements the first usable Control Panel surface. The UI reads the
live `GET /control-panel/v1/cycles/latest` endpoint and renders cycle state,
data/model readiness, model matrix policy, drift, CD/CT, stage timeline, and
resource topology from the `CycleRun` response.

## Implementation

- `apps/control-panel/package.json`
  - `lint`, `test`, `build`, and `test:e2e` scripts.
- `apps/control-panel/src/api/controlPanelClient.ts`
  - live CycleRun client and deterministic field mappers used by the UI/tests.
- `apps/control-panel/src/App.tsx`
  - tabbed Control Panel shell.
- `apps/control-panel/src/views/*`
  - cycle overview, data/model readiness, gate/risk, pipeline timeline, and
    resource topology views.
- `tests/control-panel/cycle-overview.contract.test.ts`
  - verifies UI field mapping from the CycleRun contract example.
- `tests/control-panel/cycle-overview.spec.ts`
  - Playwright e2e over the live FastAPI CycleRun API and Vite UI.

## Evidence

F-drive source-of-truth UI evidence root:

- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel_ui/evm-225-20260709T113820Z/`

Captured visual evidence:

- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel_ui/evm-225-20260709T113820Z/cycle-overview.png`
- `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/control_panel_ui/evm-225-20260709T113820Z/cycle-overview-mobile.png`

## Verification

```powershell
npm --prefix apps/control-panel install
npm --prefix apps/control-panel run lint
npm --prefix apps/control-panel run test
npm --prefix apps/control-panel run build
npm --prefix apps/control-panel run test:e2e -- --project=chromium
npm --prefix apps/control-panel run test:e2e -- --project=MobileChrome
C:\Users\opop0\miniconda3\python.exe -m pytest tests\test_control_panel_contract.py tests\test_control_panel_aggregation.py tests\test_w7_real_test_policy.py -q
```

Result:

- TypeScript lint passed.
- Vitest contract tests passed.
- Vite production build passed.
- Playwright Chromium and MobileChrome e2e passed against live `CycleRun`.
- Python Control Panel and policy tests returned `10 passed`.

## Closure

`EVM-225` is complete for Control Panel v0. It does not claim completion for
`EVM-229`, `EVM-230`, `EVM-231`, or `EVM-232`; those remain deeper UI/control
tasks that now have a concrete app surface to extend.
