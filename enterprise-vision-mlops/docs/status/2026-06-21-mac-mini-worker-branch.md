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
- `remote-inventory`

## Next Work

1. Add mac-mini remote command execution from the Windows control-plane.
2. Add ARM64 container build validation after Docker/Colima/OrbStack is installed.
3. Add MPS/CoreML model export and inference smoke tests.
4. Promote this worker into a generic remote job-runner abstraction only after
   a Linux worker is also available for comparison.
