# Jira Schedule Sync Runbook

작성일: 2026-06-21

## 목적

이 문서는 `docs/issues/issue-register.md`와
`docs/agenda/enterprise-mlops-accelerated-weekly-schedule.md`에 정리한 2026년 7월
enterprise MLOps 구현 일정을 Jira board에 동기화하기 위한 절차다.

현재 Codex 세션에는 Jira 전용 connector가 없으므로 Jira Cloud REST API 기반
script를 사용한다.

## 동기화 기준

Source of truth:

1. `docs/issues/issue-register.md`
2. `docs/agenda/enterprise-mlops-accelerated-weekly-schedule.md`
3. Jira board

Jira에는 다음 형태로 생성한다.

| Markdown | Jira |
|---|---|
| `EVM-EPIC-*` | Epic |
| `EVM-*`, `EVM-DOC-*`, `EVM-QA-*` | Task |
| `Due` 또는 `Target` | Due date |
| `Acceptance Criteria` 또는 `Output` | Description |
| source id | Jira label |

기본 label:

- `evm`
- `enterprise-mlops`
- source id label, for example `evm-021`
- `july-cut` 또는 `planning`

`codex-managed` label은 사용하지 않는다.

## 사전 준비

Jira Cloud project를 먼저 생성한다.

필요한 값:

- Jira site URL: `https://<site>.atlassian.net`
- Jira login email
- Jira API token
- Jira project key, for example `EVM`

PowerShell 환경 변수:

```powershell
$env:JIRA_BASE_URL="https://<site>.atlassian.net"
$env:JIRA_EMAIL="<email>"
$env:JIRA_API_TOKEN="<api-token>"
$env:JIRA_PROJECT_KEY="EVM"
```

Project issue type 이름이 다르면 다음도 설정한다.

```powershell
$env:JIRA_EPIC_ISSUE_TYPE="Epic"
$env:JIRA_TASK_ISSUE_TYPE="Task"
```

## Dry-run

Jira 인증 없이도 생성 예정 항목을 확인할 수 있다.

```powershell
python .\scripts\dev\jira_sync.py `
  --project-root . `
  --project-key EVM `
  --dry-run
```

Task만 확인:

```powershell
python .\scripts\dev\jira_sync.py `
  --project-root . `
  --project-key EVM `
  --mode tasks `
  --dry-run
```

## 실제 동기화

```powershell
python .\scripts\dev\jira_sync.py `
  --project-root . `
  --mode all
```

기본 동작:

- 동일 source id label이 있는 Jira issue를 찾는다.
- 있으면 summary, description, due date, label을 업데이트한다.
- 없으면 새 issue를 생성한다.
- `Done`과 `Deferred` 항목은 기본적으로 제외한다.

완료된 baseline이나 2027 확장 항목까지 함께 만들고 싶다면 다음 옵션을 추가한다.

```powershell
python .\scripts\dev\jira_sync.py `
  --project-root . `
  --mode all `
  --include-done `
  --include-deferred
```

Epic parent field를 지원하는 Jira project라면 다음 옵션을 사용한다.

```powershell
python .\scripts\dev\jira_sync.py `
  --project-root . `
  --mode all `
  --parent-mode parent-field
```

단, Jira project type에 따라 Epic parent field 정책이 다를 수 있으므로 최초에는
`--parent-mode none`으로 생성한 뒤 board에서 hierarchy를 확인한다.

## 권장 Board 구성

| Jira Column | Markdown Status |
|---|---|
| Backlog | `Planned` |
| Selected for Development | `Next` |
| In Progress | `In Progress` |
| Blocked | `Blocked` |
| Done | `Done` |

Due date 기준:

- W0: 2026-06-28
- W1: 2026-07-05
- W2: 2026-07-12
- W3: 2026-07-19
- W4: 2026-07-26
- W5: 2026-07-31

## 운영 규칙

- Markdown 일정이 바뀌면 script를 다시 실행해 Jira를 업데이트한다.
- Jira에서 일정만 바꾼 경우 `issue-register.md`에도 반드시 반영한다.
- 완료한 작업은 Jira issue, Git commit, `docs/status` 문서가 서로 연결되어야 한다.
- API token은 repository에 commit하지 않는다.

## 참고 API

- Jira Cloud REST API v3
- Issue create/update API
- JQL search API
