# Enterprise Multimodal Plan Realignment

Date: 2026-07-05
Branch: `codex/mac-mini-worker`
Scope: Planning, governance, and cross-system synchronization update after W2.

## Summary

The project direction has been expanded from a local image MLOps pipeline into
an enterprise vision/multimodal MLOps platform target. The current July W0-W5
roadmap remains the local control-plane MVP, while the new long-range target
adds lakehouse query, data quality, lineage, multimodal dataset handling,
embedding/vector retrieval, VLM evaluation, scalable serving, and enterprise
operations controls.

This status note does not start W3 implementation. W3 remains planned and should
begin with registry-driven serving.

Update: the W3 registry-driven serving tranche was completed later on
2026-07-05. See `docs/status/2026-07-05-w3-registry-driven-serving.md`.
The W3 remote execution track was completed later on 2026-07-05.

## Current Confirmed State

| Area | State |
|---|---|
| W0 Airflow foundation | Done |
| W1 full DAG and MLflow traceability | Done |
| W2 object storage data platform | Done |
| W3 registry-driven serving and remote execution | Serving Done; remote execution Done |
| Runtime stack | Docker Compose multi-container local stack |
| Airflow DAG | `enterprise_vision_mlops_daily` loaded and previously verified successful |
| Object storage | MinIO raw/processed/validated/mlflow-artifacts buckets |
| Serving gap | `/predict` still returns placeholder output before `EVM-053` |

## Plan Updates

- Added a dedicated target roadmap:
  `docs/agenda/enterprise-multimodal-mlops-target-roadmap.md`.
- Expanded `docs/issues/issue-register.md` with Phase 8 enterprise backlog
  items:
  `EVM-081` to `EVM-083`, `EVM-091` to `EVM-092`, `EVM-101` to `EVM-103`,
  `EVM-111` to `EVM-113`, and `EVM-121` to `EVM-122`.
- Updated the implementation agenda with the post-W2 status and target
  realignment.
- Updated W2/W3 review docs so reviewers can see what is complete, what is
  deferred, and why W3 should still focus on registry-driven serving first.

## Jira Synchronization State

The repository-side Jira source data is updated through
`docs/issues/issue-register.md`. Live Jira API synchronization requires these
environment variables in the shell running `scripts/dev/jira_sync.py`:

```text
JIRA_BASE_URL
JIRA_EMAIL
JIRA_API_TOKEN
JIRA_PROJECT_KEY
```

No `JIRA_*` variables were visible in the current shell at the time of the
initial plan update, so the Phase 8 roadmap expansion was dry-run only. Later on
2026-07-05, credentials were supplied for a one-command W3 serving sync and
`SCRUM-9`, `SCRUM-35`, `SCRUM-36`, `SCRUM-37`, `SCRUM-38`, and `SCRUM-39` were
updated and transitioned to `완료`. The Phase 8 IDs still need a separate live
sync when those backlog items are ready to be materialized in Jira.

Dry-run verification:

```powershell
python scripts\dev\jira_sync.py --project-root . --project-key SCRUM --source-id EVM-EPIC-09,EVM-EPIC-10,EVM-EPIC-11,EVM-EPIC-12,EVM-081,EVM-082,EVM-083,EVM-091,EVM-092,EVM-101,EVM-102,EVM-103,EVM-111,EVM-112,EVM-113,EVM-121,EVM-122 --dry-run
```

Result:

```text
project_key=SCRUM
mode=all
total=17
items parsed=4 epics, 13 tasks
live write=not performed for Phase 8 roadmap expansion; W3 serving live sync completed separately for SCRUM-9 and SCRUM-35 through SCRUM-39
```

## Review Guardrails

- Treat W2 as a data-platform foundation, not a complete lakehouse.
- Keep synthetic/local image data as known technical debt until real image
  ingestion and larger dataset handling are introduced.
- Preserve dataset version and validated Parquet URI through W3 serving and
  remote execution work.
- Do not move VLM/multimodal implementation ahead of W3 registry-driven serving;
  VLM work should build on a real model loading, readiness, rollback, and
  observability contract.

## Evidence To Carry Forward

- W2 review: `docs/reviews/w2-object-storage-data-platform-review.md`
- W3 prework: `docs/status/2026-07-03-w3-prework-readiness.md`
- Issue register: `docs/issues/issue-register.md`
- Target roadmap: `docs/agenda/enterprise-multimodal-mlops-target-roadmap.md`
- Obsidian vault: `F:\mlops_obsidian_db\mlops`
- Notion hub: `Enterprise Vision MLOps Knowledge Base`
