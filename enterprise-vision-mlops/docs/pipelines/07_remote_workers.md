# Remote Workers Pipeline

## Role

Tracks external compute nodes that can extend the local MLOps MVP into a heterogeneous enterprise-style environment.

## Why This Matters

The current Docker Compose stack is a local control-plane MVP, not a complete enterprise MLOps platform. Enterprise systems need multiple execution targets: local development, batch preprocessing nodes, training workers, edge inference nodes, and Kubernetes/GPU nodes. Tailscale-connected machines provide a practical way to demonstrate that expansion without waiting for managed cloud infrastructure.

## Current Worker Candidates

| Worker | Primary Value |
|---|---|
| `ruma-macmini` | ARM64 build validation, edge inference smoke tests, MPS/CoreML experiments, remote CI candidate |
| `ruma-ubuntu` | Linux worker candidate, Docker runtime candidate, future GPU or k3s node candidate |
| `k3s-master` | Future Kubernetes control-plane candidate |

## Inputs

- `configs/workers.toml`
- `tailscale status --json`
- TCP probe against each worker SSH port

## Outputs

- `artifacts/reports/remote_workers.md`
- `artifacts/runs/remote_workers/*/summary.json`

## Command

```bash
python scripts/run_pipeline.py remote-inventory --config configs/local.toml
```

## Current Status

- `ruma-macmini` is online over Tailscale and port `22` is open.
- mac-mini remote execution was validated on 2026-06-18 using Python 3.11 installed through `uv`.
- Automated non-interactive execution now uses the generated key `~/.ssh/evm_macmini_ed25519`.
- `ruma-ubuntu` is online over Tailscale but refused SSH during the initial probe.

## Extension Plan

- Add ARM64 Docker image build validation after Docker/Colima/OrbStack is installed.
- Add remote serving smoke test against mac-mini.
- Add remote worker job runner for preprocessing or model export tasks.
- Add MPS/CoreML experiment path for model export and edge inference validation.

## Update Log

- 2026-06-18: Added remote worker inventory pipeline and mac-mini worker candidate.
- 2026-06-18: Validated mac-mini remote Python 3.11 runtime, clone, compile, data ingest, and data validation.
- 2026-06-18: Added key-based remote execution probe for mac-mini.
