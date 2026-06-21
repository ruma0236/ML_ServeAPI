# 2026-06-21 mac-mini Worker Branch Status

## Decision

mac-mini specific worker automation is managed on `codex/mac-mini-worker`.

The base local control-plane remains on `codex/local-infra-mvp`. This keeps
macOS ARM64 worker bootstrap, smoke tests, and future MPS/CoreML experiments
separate from the generic Docker Compose MLOps platform.

## Added on This Branch

- macOS worker bootstrap script:
  - `infra/remote-workers/mac-mini/bootstrap_macos_worker.sh`
- Repeatable mac-mini smoke script:
  - `infra/remote-workers/mac-mini/run_mac_worker_smoke.sh`
- Make target:
  - `make mac-mini-smoke`
- Runbook updates for branch refresh and validation.

## Validation Contract

The mac-mini worker branch is considered healthy when this command passes on
`ruma-macmini`:

```bash
bash infra/remote-workers/mac-mini/run_mac_worker_smoke.sh
```

The smoke check must compile the local pipeline code and run:

- `data-ingest`
- `data-validate`

`remote-inventory` is optional on mac-mini. It should normally run from the
Windows control-plane because that environment owns the SSH key and authoritative
Tailscale view.

## 2026-06-21 Validation Result

Validated on `ruma-macmini` after checking out commit `54b8cef`.

- `bash -n bootstrap_macos_worker.sh`: passed
- `bash -n run_mac_worker_smoke.sh`: passed
- `compileall`: passed
- `data-ingest`: passed with 8 synthetic manifest records
- `data-validate`: passed with 8 valid records
- `remote-inventory`: command passed from mac-mini context

Note: mac-mini local `remote-inventory` is not the authoritative control-plane
view. The Windows control-plane has the SSH key and Tailscale context used for
non-interactive worker probing, so Windows-side inventory remains the source of
truth for `online_workers` and `remote_exec_ready`.

## Runtime Review

Observed mac-mini smoke runtime was about 4 seconds after the branch was warm.
No long-running `uv` or Python process remained on mac-mini after completion.

Longer runtime should be expected only in these cases:

- First bootstrap installs `uv`, installs Python 3.11, and clones from GitHub.
- `remote-inventory` is enabled from mac-mini and waits on network probes.
- Commands are accidentally run through the sandbox `ssh` wrapper instead of
  `C:\Windows\System32\OpenSSH\ssh.exe` from the Windows control-plane.
- Docker Desktop or WSL is cold-starting on the Windows machine.

## Next Work

1. Add mac-mini remote command execution from the Windows control-plane.
2. Add ARM64 container build validation after Docker/Colima/OrbStack is installed.
3. Add MPS/CoreML model export and inference smoke tests.
4. Promote this worker into a generic remote job-runner abstraction only after
   a Linux worker is also available for comparison.
