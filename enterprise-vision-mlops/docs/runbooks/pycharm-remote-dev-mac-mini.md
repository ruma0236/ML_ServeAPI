# Runbook: PyCharm Remote Development on mac-mini

## Goal

Use `ruma-macmini` as the remote development host for PyCharm while keeping the
Windows machine as the control-plane for Docker Compose, GitHub pushes, and
orchestration checks.

## Connection Profile

Use this SSH profile from Windows:

```sshconfig
Host ruma-macmini-mlops
  HostName ruma-macmini.tail35433c.ts.net
  User ruma
  IdentityFile ~/.ssh/evm_macmini_ed25519
  IdentitiesOnly yes
  ServerAliveInterval 30
  ServerAliveCountMax 3
  StrictHostKeyChecking accept-new
```

The profile is stored at:

```text
C:\Users\opop0\.ssh\config
```

## Remote Project

Open this directory in PyCharm Remote Development:

```text
/Users/ruma/mlops-lab/ML_ServeAPI/enterprise-vision-mlops
```

Expected branch:

```text
codex/mac-mini-worker
```

Recommended Python interpreter:

```text
/Users/ruma/mlops-lab/ML_ServeAPI/enterprise-vision-mlops/.venv/bin/python
```

Use the absolute `uv` path when running remote terminal commands:

```bash
$HOME/.local/bin/uv run --python 3.11 python scripts/run_pipeline.py data-validate --config configs/local.toml
```

## PyCharm Setup

1. Open PyCharm Professional or JetBrains Gateway on Windows.
2. Select `Remote Development` and choose `SSH`.
3. Use host `ruma-macmini-mlops`.
4. Select the project directory:
   `/Users/ruma/mlops-lab/ML_ServeAPI/enterprise-vision-mlops`.
5. Let JetBrains install its remote backend under the user cache directory.
6. Configure the project interpreter to `.venv/bin/python` if PyCharm does not
   detect it automatically.

## Server-Side Preflight

Run this from Windows before opening PyCharm if the remote backend behaves
unexpectedly:

```powershell
ssh ruma-macmini-mlops 'bash ~/mlops-lab/ML_ServeAPI/enterprise-vision-mlops/infra/remote-workers/mac-mini/check_pycharm_remote_dev.sh'
```

The preflight checks:

- SSH session visibility
- required macOS tools: `git`, `curl`, `tar`, `unzip`, `rsync`
- JetBrains RemoteDev cache write access
- free disk space
- project branch and commit
- `uv` and project `.venv`
- Python compile check for `src` and `scripts`

## Current Scope

mac-mini is configured as an ARM64 development and validation worker. It is not
yet the Docker/Kubernetes runtime for the whole platform. Keep Docker Compose and
control-plane checks on Windows until Docker/OrbStack/Colima is intentionally
added to the mac-mini worker branch.
