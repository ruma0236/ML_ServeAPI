# W7 Control Panel Cycle Ring Remediation

Date: 2026-07-09
Scope: EVM-225 / SCRUM-103 follow-up remediation

## Issue

The Overview tab `Cycle State` ring used a single element for both radial
placement and status animation. That made the stage nodes fragile when the
dark theme, responsive sizing, and moving ring affordance were combined. The
all-tabs visual evidence also allowed screenshots to be captured before the
live `CycleRun` payload finished loading.

## Remediation

- `apps/control-panel/src/views/CycleOverview.tsx`
  - Split each stage marker into a positioning wrapper and an inner status dot.
  - Added a dedicated `ring-sweep` visual layer for the moving orbit animation.
- `apps/control-panel/src/styles.css`
  - Added stable `--ring-size` and `--ring-radius` variables for desktop and
    mobile.
  - Added dark-theme ring track and sweep colors on a neutral black base.
  - Removed the mobile-only `transform-origin` override that could desync node
    placement from the ring geometry.
- `tests/control-panel/cycle-overview.spec.ts`
  - Added geometry assertions that every stage dot remains inside the ring and
    at the expected radius.
- `tests/control-panel/all-tabs-visual.spec.ts`
  - Added tab-specific readiness waits so visual evidence captures loaded UI,
    not the transient `Loading CycleRun` state.

## Verification

```powershell
npm --prefix apps/control-panel run lint
npm --prefix apps/control-panel run test
npm --prefix apps/control-panel run build
npm --prefix apps/control-panel run test:e2e -- cycle-overview.spec.ts
npm --prefix apps/control-panel run test:e2e -- all-tabs-visual.spec.ts
npm --prefix apps/control-panel run test:e2e
```

Result:

- TypeScript lint: pass
- Vitest: 6 files / 15 tests passed
- Production build: pass
- Overview e2e: Chromium and MobileChrome passed
- All-tabs visual e2e: Chromium and MobileChrome passed
- Full Playwright suite: 14 / 14 passed

## Evidence

- Desktop overview:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/all_tabs_visual/latest/chromium-overview.png`
- Mobile overview:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/all_tabs_visual/latest/MobileChrome-overview.png`

## Status

The Overview `Cycle State` ring is now stable in desktop and mobile visual
captures. The moving ring sweep is separated from stage-dot status animations,
and the visual test now waits for real `CycleRun` data before capturing.
