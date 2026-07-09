# W7 EVM-230/EVM-232 Operations Control

Date: 2026-07-09
Branch: `codex/mac-mini-worker`
Evidence root: `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/operations_ui/evm-230-232-20260709T222242Z0900`

## Scope

This closes the W7 operator-control surface for:

- `EVM-230`: Airflow and MLflow task authoring and assignment UI.
- `EVM-232`: Resource control protocol and audit guardrails.

The implementation is intentionally guarded. It creates task assignments and
command intents, records audit entries, and supports dry-run, queue,
pending-confirmation, and cancel states. It does not directly mutate Kubernetes,
Airflow, or MLflow resources.

## Implementation Files

- `src/evm/control_panel/schemas.py`
  - added task, command, and audit Pydantic schemas.
- `src/evm/control_panel/operations.py`
  - added F-drive JSON ledger persistence for task assignments and command
    intents.
- `apps/api/control_panel_tasks.py`
  - added `GET /control-panel/v1/tasks`, `GET /control-panel/v1/tasks/default`,
    and `POST /control-panel/v1/tasks`.
- `apps/api/control_panel_commands.py`
  - added `GET /control-panel/v1/commands`, `POST /control-panel/v1/commands`,
    `POST /commands/{command_id}/confirm`, and
    `POST /commands/{command_id}/cancel`.
- `apps/control-panel/src/views/TaskAuthoring.tsx`
  - added the production-oriented Operate tab for task authoring, CD/CT-aware
    assignment creation, and assignment ledger review.
- `apps/control-panel/src/components/CommandDrawer.tsx`
  - added command intent creation, confirm, cancel, and audit display.
- `apps/control-panel/src/styles.css`
  - improved production readability for operations cards, forms, ledgers, and
    mobile layout.
- `tests/test_control_panel_tasks.py`
- `tests/test_control_panel_commands.py`
- `tests/control-panel/operations.contract.test.ts`
- `tests/control-panel/operations.spec.ts`

## Input Data

- Live `CycleRun` from `GET /control-panel/v1/cycles/latest`.
- Runtime resources from `GET /control-panel/v1/resources`.
- Airflow mode/reference from `CycleRun.airflow`.
- MLflow experiment/model reference from `CycleRun.mlflow`.
- Environment scope from `CycleRun.environment`.
- CD/CT gate state from `CycleRun.cdct_gate`.

## Output Artifacts

- `ledger/task_assignments.json`
- `ledger/command_intents.json`
- `chromium-operations.png`
- `MobileChrome-operations.png`
- Playwright reports under `playwright-final/`
- API and UI logs:
  - `control-panel-api-8011.log`
  - `control-panel-ui-5174.log`

## Verification

Commands executed:

```powershell
C:\Users\opop0\miniconda3\python.exe -m py_compile apps\api\main.py apps\api\control_panel_tasks.py apps\api\control_panel_commands.py src\evm\control_panel\operations.py src\evm\control_panel\schemas.py
C:\Users\opop0\miniconda3\python.exe -m pytest tests\test_control_panel_contract.py tests\test_control_panel_aggregation.py tests\test_w7_real_test_policy.py tests\test_control_panel_tasks.py tests\test_control_panel_commands.py -q
npm --prefix apps/control-panel run lint
npm --prefix apps/control-panel run test
npm --prefix apps/control-panel run build
npm --prefix apps/control-panel run test:e2e -- --grep '@w7-operations'
```

Results:

- Operations API tests: `4 passed`.
- Control Panel regression and policy tests: `15 passed`.
- Vitest frontend contract tests: `4 passed`, `10 passed`.
- Production build: passed.
- Playwright desktop/mobile operations e2e: `2 passed`.

## Evidence Summary

- Task ledger contains dry-run and queued task assignments.
- Queued task includes Airflow `external-compose`, MLflow model/run context,
  environment scope, owner, resource profile, config payload, and audit event.
- Command ledger contains dry-run and cancelled command intents with actor,
  target, reason, parameters, and audit events.
- Command confirmation moves to `pending_confirmation` without setting
  `applied_at`; cancel records `mutation_applied=false`.

## Boundaries

- This work does not implement live apply/mutation against Kubernetes, Airflow,
  or MLflow.
- Future apply support must add RBAC, actor identity, approval policy, rollback
  linkage, and concrete executor integration before any command can become
  `applied`.
- `EVM-226` still owns Kubernetes real execution proof.
- `EVM-237` and `EVM-238-B` still own real EfficientNet run evidence and final
  real-test evidence validation.
