# 2026-06-21 GitHub Issue Automation Status

## Summary

Added a GitHub Issue workflow for Codex-managed bug discovery and resolution.

This enables the following operating flow:

```text
bug/error detected
-> GitHub Issue created or drafted
-> fix branch/current branch update
-> validation commands executed
-> root cause/fix/verification comment posted
-> issue closed after verification
```

## Added

- `scripts/dev/github_issue.py`
- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `docs/governance/codex-github-issue-resolution-workflow.md`
- bug register section in `docs/issues/issue-register.md`

## Label Policy

Default bug labels:

- `mlops`
- `bug`

Do not add `codex-managed`. Codex involvement is tracked through issue body,
comments, commits, and status documents.

## Validation

Static check:

```powershell
python -m py_compile .\enterprise-vision-mlops\scripts\dev\github_issue.py
```

Dry-run issue creation:

```powershell
python .\enterprise-vision-mlops\scripts\dev\github_issue.py --cwd . create-bug --issue-id EVM-BUG-001 --summary "remote-inventory reports tailnet unavailable despite SSH success" --reproduction "python scripts/run_pipeline.py remote-inventory --config configs/local.toml" --observed "tailnet_status_available=false while remote_exec_ready=true" --expected "Inventory should distinguish Tailscale API access from SSH reachability" --validation "python -m compileall src scripts`npython scripts/run_pipeline.py remote-inventory --config configs/local.toml" --dry-run
```

Dry-run issue resolution:

```powershell
python .\enterprise-vision-mlops\scripts\dev\github_issue.py --cwd . resolve --issue-number 123 --root-cause "The current Windows shell cannot access the protected Tailscale local API pipe." --fix "Separated tailnet status, TCP probe, SSH remote exec, and effective connectivity state." --verification "python -m compileall src scripts`npython scripts/run_pipeline.py remote-inventory --config configs/local.toml" --residual-risk "tailnet_online remains unavailable without Tailscale pipe permission." --close --dry-run
```

## Current Limitation

The current shell does not have `GITHUB_TOKEN` or `GH_TOKEN` set, so no real
GitHub Issue was created during this setup. The automation is ready for live use
after a GitHub token with repository Issues read/write permission is configured.
