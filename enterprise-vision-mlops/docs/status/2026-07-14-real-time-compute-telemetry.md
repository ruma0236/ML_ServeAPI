# EVM-262 Real-Time Compute Telemetry

Date: 2026-07-14
Sprint: 2026-07-W8
Epic: EVM-EPIC-21 / SCRUM-156

## Problem

The fleet Overview previously showed Kubernetes GPU capacity and request
allocation as `1 / 1`. That proves scheduling ownership, not actual compute
utilization, and is not useful for operating a single-GPU workstation.

## Implementation

- The existing host-side Kubernetes observer now samples Windows CPU and
  physical memory plus NVIDIA GPU utilization, VRAM, temperature, and power.
- The observer writes a sanitized `evm.compute_telemetry.v1` payload into the
  F-drive runtime snapshot every five seconds. The API container does not gain
  host GPU access, a kubeconfig, or an `nvidia-smi` dependency.
- The resources API exposes typed `ComputeTelemetry` and
  `AcceleratorTelemetry` records with explicit live, stale, and unavailable
  states.
- Snapshot loading suppresses stale measurements instead of presenting them as
  current utilization.
- The Control Panel polls resources independently every five seconds, so slow
  CycleRun, drift, or decision requests cannot delay utilization updates.
- Overview now shows a live GPU-load KPI and animated CPU, RAM, GPU, and VRAM
  gauges. GPU allocation remains visible as scheduling context, alongside GPU
  model, temperature, power draw, and telemetry age.

## Live Proof

Two API observations were captured exactly five seconds apart:

- `2026-07-14T05:39:32Z`: CPU 27.54%, RAM 45.03%, GPU 48%, VRAM
  2770/16376 MiB, 51 C, 26.26 W.
- `2026-07-14T05:39:37Z`: CPU 36.43%, RAM 45.13%, GPU 47%, VRAM
  2784/16376 MiB, 51 C, 26.49 W.

The API health endpoint returned `ok`, the Control Panel returned HTTP 200,
and browser sampling showed telemetry age reset while CPU and GPU gauges
changed without a full page reload.

## Verification

- Backend aggregation, contract, diagnostics, and observer tests: 30 passed.
- Frontend type check: passed.
- Frontend unit tests: 49 passed.
- Frontend production build: passed.
- Focused Overview desktop/mobile Playwright: 2 passed.
- All-tab desktop/mobile visual Playwright: 2 passed, covering 20 viewport-tab
  captures with no horizontal overflow.

## Boundary

This is a local Windows host telemetry bridge suitable for the current Docker
Desktop Kubernetes workstation. A multi-node production cluster should replace
the host probe with node-exporter plus DCGM Exporter or an equivalent managed
metrics stack while preserving the same API/UI contract.
