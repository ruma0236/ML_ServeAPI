# Runbook: mac-mini Remote Worker Setup

## Goal

Attach `ruma-macmini` as a Tailscale-connected remote worker for the MLOps platform.

The mac-mini should be used as a heterogeneous worker, not as a replacement for GPU cluster infrastructure. Its main value is ARM64 validation, edge inference testing, MPS/CoreML experiments, and remote CI-style execution.

## Current Network Facts

- Tailscale host: `ruma-macmini`
- Tailscale IP: `100.104.142.2`
- SSH port: `22`
- 2026-06-18 probe result: online and SSH port open

## Hardware Snapshot

- CPU: Apple M4 Pro
- Cores: 12
- Memory: 24GB
- OS: macOS 26.5.1
- Architecture: arm64

## Current Runtime Snapshot

- Git: available
- System Python: 3.9.6
- Project Python: 3.11.15 installed through `uv`
- Docker/Homebrew/Colima: not installed at initial probe time
- Remote repo path: `~/mlops-lab/ML_ServeAPI/enterprise-vision-mlops`
- Base branch: `codex/local-infra-mvp`
- mac-mini worker branch: `codex/mac-mini-worker`
- Password-based SSH was used only for the initial public-key bootstrap.
- Persistent automation now uses `~/.ssh/evm_macmini_ed25519` from the Windows control-plane.

## Worker Bootstrap Performed

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv python install 3.11
mkdir -p ~/mlops-lab
cd ~/mlops-lab
git clone --branch codex/local-infra-mvp https://github.com/ruma0236/ML_ServeAPI.git
cd ML_ServeAPI/enterprise-vision-mlops
~/.local/bin/uv run --python 3.11 python -m compileall src scripts
~/.local/bin/uv run --python 3.11 python scripts/run_pipeline.py data-ingest --config configs/local.toml
~/.local/bin/uv run --python 3.11 python scripts/run_pipeline.py data-validate --config configs/local.toml
```

## mac-mini Branch Refresh

The mac-mini specific branch is separated from the base infra branch:

```bash
cd ~/mlops-lab/ML_ServeAPI
git fetch origin codex/mac-mini-worker
git checkout codex/mac-mini-worker
git pull --ff-only origin codex/mac-mini-worker
cd enterprise-vision-mlops
bash infra/remote-workers/mac-mini/run_mac_worker_smoke.sh
```

## Validation From Windows

```powershell
tailscale ping ruma-macmini
Test-NetConnection 100.104.142.2 -Port 22
ssh -i $HOME\.ssh\evm_macmini_ed25519 -o BatchMode=yes ruma@ruma-macmini.tail35433c.ts.net 'whoami; hostname; uname -m'
python scripts/run_pipeline.py remote-inventory --config configs/local.toml
```

## Next Automation Target

Once the remote-worker code is pushed and pulled on mac-mini, add a remote command runner that can execute:

```bash
git pull --ff-only origin codex/mac-mini-worker
bash infra/remote-workers/mac-mini/run_mac_worker_smoke.sh
```

on mac-mini and report the result back to `artifacts/reports/remote_workers.md`.
