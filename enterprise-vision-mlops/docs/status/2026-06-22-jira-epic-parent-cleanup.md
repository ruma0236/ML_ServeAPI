# 2026-06-22 Jira Epic Parent Cleanup

## Scope

Cleaned up Jira board hierarchy after the W0 Airflow foundation work.

## Result

- Jira project: `SCRUM`
- Non-epic `evm` issues checked: 43
- Non-epic issues without parent after cleanup: 0

## Parent Assignment Rule

| Area | Epic |
|---|---|
| Airflow + MLflow Orchestration | `SCRUM-6` / `EVM-EPIC-02` |
| Object Storage Data Platform | `SCRUM-7` / `EVM-EPIC-03` |
| Remote Training Infra | `SCRUM-8` / `EVM-EPIC-04` |
| Registry-driven Serving | `SCRUM-9` / `EVM-EPIC-05` |
| Observability / Drift / SLO | `SCRUM-10` / `EVM-EPIC-06` |
| CI/CD / Governance | `SCRUM-11` / `EVM-EPIC-07` |

## Issues Updated

Assigned to `SCRUM-6` / `EVM-EPIC-02`:

- `SCRUM-14` / `EVM-023-A`
- `SCRUM-15` / `EVM-023-B`
- `SCRUM-16` / `EVM-DOC-021`
- `SCRUM-20` / `EVM-023-C`
- `SCRUM-21` / `EVM-023-D`
- `SCRUM-22` / `EVM-023-E`
- `SCRUM-53` / `EVM-BUG-002`
- `SCRUM-54` / `EVM-BUG-003`

Assigned to `SCRUM-11` / `EVM-EPIC-07`:

- `SCRUM-49` / `EVM-DOC-031`
- `SCRUM-50` / `EVM-DOC-032`
- `SCRUM-51` / `EVM-QA-001`
- `SCRUM-52` / `EVM-QA-002`

Each updated Jira issue received a comment explaining the parent assignment.

## Git And Issue Tracking Note

Git itself does not store issues. Issues are tracked outside Git through Jira or
GitHub Issues, then linked back to Git through branch names, commit messages,
status documents, and issue register entries.

For W0:

- `EVM-BUG-002` is tracked as Jira bug `SCRUM-53` and GitHub Issue `#2`.
- `EVM-BUG-003` is tracked as Jira bug `SCRUM-54` and GitHub Issue `#3`.
- Both bugs are documented in `docs/issues/issue-register.md`.
- The implementation and fixes are in commit `07551b7b41aaf65453ab32ec1ec0f588691cd95c`.
- W0 completion evidence is documented in `docs/status/2026-06-21-airflow-foundation.md`.

The bugs are not branch-owned records because Git branches do not own issues.
They are linked to the `codex/mac-mini-worker` branch through Jira/GitHub issue
comments, commit history, and status documents.
