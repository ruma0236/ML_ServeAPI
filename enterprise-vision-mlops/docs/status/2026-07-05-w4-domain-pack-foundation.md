# W4 Domain Pack Foundation

Date: 2026-07-05
Branch: `codex/mac-mini-worker`
Scope: W4 Manufacturing VLM P0 Foundation start.

## Summary

W4 started by turning the VLM-first manufacturing inspection direction into an
executable domain policy contract.

The platform should remain generic at the core MLOps layer. Manufacturing visual
inspection is now represented as the first reference domain pack, not as a
hard-coded platform boundary.

## Completed Items

| ID | Result |
|---|---|
| `EVM-130` | README and roadmap now position the project as Manufacturing Visual Inspection VLM-first AI Infra / MLOps / AIOps |
| `EVM-131` | Home lab roles are documented in README and encoded in the manufacturing domain pack |
| `EVM-132` | Dataset policy records VisA as primary and MVTec AD as fallback/secondary, with license review required before import |
| `EVM-133` | Manufacturing manifest schema `mvi_manifest_v1` is encoded in the domain pack and validated by `domain-pack-check` |

## Implementation

New domain pack:

- `domain_packs/README.md`
- `domain_packs/manufacturing_visual_inspection/domain_pack.toml`

New code:

- `src/evm/core/domain_pack.py`
- `src/evm/pipelines/domain_pack_check/__init__.py`
- `src/evm/pipelines/domain_pack_check/run.py`

Updated integration:

- `scripts/run_pipeline.py` now exposes `domain-pack-check`
- `configs/local.toml` and `configs/airflow.toml` include
  `pipelines.domain_pack_check`
- `Makefile` includes `domain-pack-check` and runs it first in `local-mvp`
- `orchestration/airflow/dags/enterprise_vision_mlops_daily.py` runs
  `domain_pack_check` before object-store bootstrap
- `docs/pipelines/00_pipeline_overview.md` includes the new pipeline
- `docs/pipelines/09_domain_packs.md` documents the Core MLOps + Domain Pack
  boundary

## Current Contract

The manufacturing VLM domain pack validates these surfaces:

- dataset candidates and access policy,
- required manifest fields,
- validation rule inventory,
- mock and real candidate model adapters,
- VLM request and response required fields,
- promotion gates,
- failure scenarios.

## Verification

Expected command:

```powershell
python scripts\run_pipeline.py domain-pack-check --config configs\local.toml
```

Observed result:

```text
status=pass
domain_pack_id=manufacturing_visual_inspection
primary_dataset=visa
dataset_candidates=visa,mvtec_ad
manifest_required_fields=13
validation_rules=5
promotion_gates=3
failure_scenarios=4
pipeline_run_id=domain-pack-check-20260705T114259Z
```

## Remaining W4 Work

Still open:

- `EVM-134`: implement actual image quality validation against imported images.
- `EVM-135`: implement dataset shard/split builder.
- `EVM-141`: implement mock VLM adapter.
- `EVM-142`: implement multimodal router request classification.
- `EVM-143`: implement manifest-based batch inference runner.
- `EVM-144`: implement VLM output schema validator.

## Jira / Notion / Obsidian

Repository source of truth is updated in `docs/issues/issue-register.md`.

Jira dry-run:

```powershell
python scripts\dev\jira_sync.py --project-root . --project-key SCRUM --source-id EVM-130,EVM-131,EVM-132,EVM-133 --include-done --labels vlm-first,manufacturing-visual-inspection --transition-statuses --dry-run
```

Result:

```text
total=4
EVM-130=Done
EVM-131=Done
EVM-132=Done
EVM-133=Done
```

Jira live write completed after adding a local-only env file at
`C:\Users\mlops\.evm\jira.local.env` and loading it through
`scripts\dev\load_jira_env.ps1`.

Live Jira result:

```text
EVM-130 -> SCRUM-56 -> 완료
EVM-131 -> SCRUM-57 -> 완료
EVM-132 -> SCRUM-58 -> 완료
EVM-133 -> SCRUM-59 -> 완료
EVM-EPIC-13 -> SCRUM-60 -> transition skipped for Next
```

Notion and Obsidian should record this as the first W4 implementation increment.
