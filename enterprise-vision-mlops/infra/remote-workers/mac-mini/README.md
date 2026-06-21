# mac-mini Worker

`ruma-macmini` is a Tailscale-connected macOS worker candidate.

## Branch Policy

mac-mini specific automation lives on `codex/mac-mini-worker`.

The base platform remains on `codex/local-infra-mvp`. Keep macOS-only bootstrap,
remote smoke, MPS/CoreML, and ARM64 validation scripts isolated in this folder
until they are generic enough to merge into the base branch.

Recommended responsibilities:

- ARM64 compatibility checks
- Lightweight edge inference validation
- MPS/CoreML export experiments
- Remote CI runner candidate

Do not position this node as a CUDA GPU training cluster. It is better represented as an enterprise heterogeneous/edge worker.

## Bootstrap

Run this on mac-mini when attaching or refreshing the worker:

```bash
bash infra/remote-workers/mac-mini/bootstrap_macos_worker.sh
```

The script installs `uv` if needed, ensures Python 3.11 exists, checks out
`codex/mac-mini-worker`, and runs the worker smoke checks.

## Repeated Smoke Check

Run this from `enterprise-vision-mlops` on mac-mini:

```bash
bash infra/remote-workers/mac-mini/run_mac_worker_smoke.sh
```

The smoke check compiles pipeline code and runs:

- `data-ingest`
- `data-validate`
- `remote-inventory`

Runtime reports are written under `artifacts/reports` and `artifacts/runs`.
