# EVM-263 GPU Utilization Semantics Correction

Date: 2026-07-14
Sprint: 2026-07-W8
Epic: EVM-EPIC-21 / SCRUM-156

## Finding

The first real-time telemetry implementation displayed NVIDIA
`utilization.gpu` as generic `GPU load`. That value is NVML GPU activity over
its sample interval. Windows Task Manager derives its headline value from the
busiest physical GPU engine. On a WDDM desktop with browser and compositor
activity, the two values are not interchangeable.

## Reproduction

- NVIDIA `nvidia-smi dmon`: SM activity varied from 29% to 45%.
- Windows `GPU Engine` counters: the busiest NVIDIA 3D engine varied around
  5% to 15% over the same investigation.
- The GPU consumed only about 23 W to 25 W of its 320 W limit, supporting the
  conclusion that the generic 30% to 45% presentation overstated operator
  intuition about current workload intensity.

## Correction

- Added a persistent native Windows PDH query for `GPU Engine(*)/Utilization
  Percentage` and `GPU Adapter Memory(*)/Dedicated Usage`.
- Per-process counters are grouped by adapter and physical engine. The busiest
  engine is selected using the same semantics operators see in Task Manager.
- The discrete NVIDIA adapter is matched using dedicated-memory usage.
- `AcceleratorTelemetry` now carries `engine_utilization_percent`,
  `engine_utilization_source=windows_pdh`, and `busiest_engine`.
- The main KPI and GPU ring use Windows engine utilization. Existing NVML
  activity remains visible as a separately labeled secondary value.
- Multi-node Kubernetes remains expected to use DCGM Exporter; this Windows
  PDH path is the local workstation implementation.

## Live Proof

After rebuilding the observer, API, and Control Panel:

- API: Windows engine 11.12%, busiest engine `3D`, NVML activity 37%, VRAM
  2741 MiB, 42 C, 23.36 W.
- Browser: `GPU engine 9.1%`, detail `3D / Windows`, and `NVML activity 21%`
  were rendered simultaneously from a later five-second observation.
- GPU allocation remained separate at `1 / 1`.

## Verification

- Backend observer, aggregation, diagnostics, and contract tests: 31 passed.
- Frontend unit suite: 15 files and 49 tests passed.
- Frontend lint and production build: passed.
- Focused desktop and MobileChrome Overview E2E: 2 passed.
