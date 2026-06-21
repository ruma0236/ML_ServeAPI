# Runbook: PyCharm Remote Development on mac-mini

## Goal

Use `ruma-macmini` as the remote development host for PyCharm while keeping the
Windows machine as the control-plane for Docker Compose, GitHub pushes, and
orchestration checks.

## JetBrains Client Choice

Do not use JetBrains Gateway's plain SSH flow for this mac-mini target.

JetBrains Gateway's SSH backend accepts Linux remote hosts only. For a macOS
remote host, use JetBrains Toolbox App remote development instead. Toolbox App
2.6+ supports SSH remote hosts on Linux, macOS, and Windows.

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

1. Install or update JetBrains Toolbox App on Windows.
2. Open Toolbox App and switch to the SSH or Remote Development context.
3. Import the existing SSH config entry, or create a connection using
   `ruma-macmini-mlops`.
4. Use Toolbox App to install or launch PyCharm for the remote host.
5. Select the project directory:
   `/Users/ruma/mlops-lab/ML_ServeAPI/enterprise-vision-mlops`.
6. Let JetBrains install its remote backend under the user cache directory.
7. Configure the project interpreter to `.venv/bin/python` if PyCharm does not
   detect it automatically.

If Gateway shows a message that only Linux remote hosts are supported, that is
expected for Gateway. Close Gateway and use Toolbox App for this macOS host.

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
