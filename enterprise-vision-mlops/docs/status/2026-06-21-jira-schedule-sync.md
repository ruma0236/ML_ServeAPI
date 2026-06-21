# 2026-06-21 Jira Schedule Sync Status

## Summary

Jira schedule sync scaffold를 추가했다. 현재 Jira 전용 connector는 없으므로
Jira Cloud REST API를 호출하는 local script 방식으로 진행한다.

## Configuration Notes

- API token name: `mlops_key`
- Workspace / project name: `MLOps`
- Recommended project key: `MLOPS`
- Real API token value is not stored in Git.

실제 sync 실행 전 필요한 추가 값:

- `JIRA_BASE_URL`
- `JIRA_EMAIL`
- verified `JIRA_PROJECT_KEY`

## Implemented

- `scripts/dev/jira_sync.py`
- `docs/runbooks/jira-schedule-sync.md`
- `docs/governance/git-jira-issue-workflow.md` update
- `.env.example` Jira sync placeholders

## Verification

```powershell
python -m py_compile .\scripts\dev\jira_sync.py
python .\scripts\dev\jira_sync.py --project-root . --project-key EVM --dry-run
python .\scripts\dev\jira_sync.py --project-root . --project-key EVM --mode tasks --dry-run
```

Result:

- Default sync candidates: 48
- Task-only sync candidates: 41
- `Done` and `Deferred` items are excluded by default.
- `codex-managed` label is not emitted.

## Blocker

Actual Jira API sync is pending until Jira site URL, login email, and verified project key are
provided.
