# Control Panel Cross-Origin And Runtime Hardening

Date: 2026-07-14

## Decision

Ports `4173` and `4174` are two access paths to the same Control Panel service.
They must resolve the same root URL to the same latest LIVE CycleRun. Operator
navigation and historical inspection must be explicit, shareable, and
reproducible instead of depending on origin-scoped browser storage.

## Ordered Development Plan

| Stage | Issue | Scope | State |
|---|---|---|---|
| 0 | `EVM-259` | cross-origin state, request-race suppression, sync semantics, runtime preflight | Done |
| 1 | `EVM-244` | bounded A/B canary router, evaluator, stop policy, and exact rollback | Planned |
| 2 | `EVM-255` | real BANKING77 text training, CT, serving, monitoring, and rollback | Planned |
| 3 | `EVM-256` | pinned VLM evaluation and governed serving adapter | Planned |
| 4 | `EVM-257` | multi-tenant SSO, RBAC, quotas, secrets, and negative isolation proof | Planned |
| 5 | `EVM-258` | HA/DR, load, chaos, SLO, GitOps reconciliation, and recovery proof | Planned |

The stages retain full acceptance depth. Their order only expresses runtime and
governance dependencies.

## Implemented Remediation

- Removed legacy Cycle, Run, and tab selection from `localStorage`.
- Root entry defaults to the latest LIVE CycleRun on every origin.
- Historical Cycle, selected lifecycle Run, and active view are encoded as
  `cycle`, `run`, and `view` query parameters.
- Added explicit `LIVE DATA`, `HISTORICAL SNAPSHOT`, `LOADING LIVE`, and
  `LOADING SNAPSHOT` states plus `Return to Live`.
- Split source synchronization into Connecting, Live, Partial, and Unavailable.
- Added a selection generation guard so a late historical refresh cannot
  overwrite a newer LIVE selection.
- Added retry behavior for critical API failures.
- Added Playwright live-runtime preflight for the host lifecycle worker and
  Kubernetes observer with exact recovery commands.
- Restored the stopped host worker and observer. Current checks report worker
  `online` and Kubernetes observation `live`.
- Updated browser contracts for the purpose-based Pipeline/Infrastructure UI,
  human-readable task states, and the implemented cross-validation executor.

## Verification

```powershell
npm --prefix apps/control-panel run test
npm --prefix apps/control-panel run build
npm --prefix apps/control-panel run test:e2e
```

Results:

- frontend contract tests: `43 passed`;
- production build: passed;
- browser scenarios: `20 passed` across Desktop Chrome and Mobile Chrome;
- local root: `http://127.0.0.1:4173/` -> latest LIVE CycleRun;
- tailnet root: `http://ruma.tail35433c.ts.net:4174/` -> the same CycleRun;
- initial tailnet load: Connecting, then Live 5s without a false Degraded state;
- selected local and tailnet Cycle ID:
  `cycle-w7-visa-open-data-e35d93d5561f-efficientnet-b0-visa-anomaly-v7f2d6d1cd5ed4d8bac48d114fd767ba9`.

The current LIVE CycleRun remains model-policy blocked. That business state is
real and is separate from this Control Panel transport and state-consistency
closure. Historical passing CycleRuns remain accessible through explicit URLs.

## Tracking

- Jira: `EVM-259 / SCRUM-165`, Done, W8 sprint 145.
- Jira parent: `EVM-EPIC-21 / SCRUM-156`.
- Repository status source: this document and `docs/issues/issue-register.md`.
