# 2026-06-21 Jira Schedule Sync Status

## Summary

Jira schedule sync scaffold를 추가했다. 현재 Jira 전용 connector는 없으므로
Jira Cloud REST API를 호출하는 local script 방식으로 진행한다.

## Configuration Notes

- API token name: `mlops_key`
- Workspace / project name: `MLOps`
- Confirmed Jira Software project key: `SCRUM`
- Confirmed Jira Software project id: `10000`
- Confirmed Jira board id: `1`
- Confirmed Jira board name: `SCRUM board`
- Observed Jira site root: `https://opop0236.atlassian.net`
- Observed URL project key candidate: `ZWIW`
- Organization id was provided, but it is not required for Jira issue/sprint REST sync.
- Real API token value is not stored in Git.

실제 sync 실행 전 필요한 추가 값:

- `JIRA_BASE_URL`
- `JIRA_EMAIL`
- verified `JIRA_PROJECT_KEY`

## Implemented

- `scripts/dev/jira_sync.py`
- `scripts/dev/jira_github_issue_sync.py`
- `.github/workflows/jira-realtime-sync.yml`
- `docs/runbooks/jira-schedule-sync.md`
- `docs/governance/git-jira-issue-workflow.md` update
- `docs/governance/jira-realtime-sync-architecture.md`
- `.env.example` Jira sync placeholders

## Verification

```powershell
python -m py_compile .\scripts\dev\jira_sync.py
python -m py_compile .\scripts\dev\jira_github_issue_sync.py
python .\scripts\dev\jira_sync.py --project-root . --project-key EVM --dry-run
python .\scripts\dev\jira_sync.py --project-root . --project-key EVM --mode tasks --dry-run
python .\scripts\dev\jira_sync.py --project-root . --project-key MLOPS --sync-sprints --assign-sprints --transition-statuses --dry-run
python .\scripts\dev\jira_github_issue_sync.py --github-event-path <sample-event.json> --project-key MLOPS --dry-run
```

Result:

- Default sync candidates: 48
- Task-only sync candidates: 41
- Planned sprint candidates: 6
- GitHub Issue event dry-run mapped `[EVM-021]` with `in-progress` label to `In Progress`.
- `Done` and `Deferred` items are excluded by default.
- `codex-managed` label is not emitted.

## Blocker

Actual Jira API sync is pending until Jira site URL, login email, and verified project key are
provided.

2026-06-21 read-only probe result:

- `JIRA_BASE_URL` normalized to `https://opop0236.atlassian.net`.
- Initial token probe returned `401 Unauthorized`.
- New token probe returned `200 OK` for `/rest/api/3/myself`.
- `MLOps` and `MLOPS` are not valid REST project keys.
- `SCRUM` is the valid Jira Software project key for project name `MLOps`.
- Board id `1` exists and belongs to `MLOps (SCRUM)`.
- Board sprint API returned existing sprint data, so sprint/backlog automation can target board `1`.

Do not store the actual API token in Git. The token was only used as an environment variable for
read-only verification.
