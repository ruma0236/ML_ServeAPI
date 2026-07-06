# 2026-07-06 Current-week Sprint Realignment

## Summary

On 2026-07-06 KST, the active Enterprise Vision MLOps plan was compressed so the
new VLM-first enterprise MLOps scope is planned for completion within the
current week, 2026-07-06 to 2026-07-12.

The previous W5 VLM reliability work is now part of the same completion sprint.
W5 and later are redefined as post-completion research and operating-system
work around real model lifecycle, drift/special-case tracking, draft/decision
management, large-scale data acquisition/cleaning, AgentOps, and serving-scale
research.

## Current-week Completion Plan

| Date | Scope | Issue IDs |
|---|---|---|
| 2026-07-06 | Domain pack foundation completed and synchronized | `EVM-130` to `EVM-133` |
| 2026-07-07 | Image quality validation and shard/split builder | `EVM-134`, `EVM-135` |
| 2026-07-08 | VLM adapter contract and multimodal router | `EVM-141`, `EVM-142` |
| 2026-07-09 | Manifest-based batch inference and VLM output validation | `EVM-143`, `EVM-144` |
| 2026-07-10 | Prompt/model registry, regression gate, audit/RCA, failure suite | `EVM-151`, `EVM-152`, `EVM-161`, `EVM-162` |
| 2026-07-11 | VLM metrics, benchmark, observability, CI, demo evidence | `EVM-171`, `EVM-181`, `EVM-061` to `EVM-075` |
| 2026-07-12 | Integration buffer, release note, final review and handoff | `EVM-074`, `EVM-075` |

## W5+ Plan

| Sprint | Date | Focus | Issue IDs |
|---|---|---|---|
| W5 | 2026-07-13 to 2026-07-19 | Real model lifecycle, drift/special-case tracking, RCA feedback, draft governance | `EVM-191` to `EVM-195` |
| W6 | 2026-07-20 to 2026-07-26 | Large-scale data acquisition and cleaning research | `EVM-201` to `EVM-205` |
| W7 | 2026-07-27 to 2026-07-31 | Draft/decision governance, AgentOps reliability design, scale serving research, portfolio stabilization | `EVM-211` to `EVM-214` |

## Source-of-truth Updates

- `docs/issues/issue-register.md` now contains current-week override rows for
  `EVM-061` to `EVM-075`, `EVM-130` to `EVM-181`, plus new W5+ backlog items
  `EVM-191` to `EVM-214`.
- `docs/agenda/enterprise-mlops-accelerated-weekly-schedule.md` now treats W4
  as the 2026-07-06 to 2026-07-12 completion sprint and W5 to W7 as follow-up
  research/operations sprints.
- `docs/agenda/enterprise-mlops-roadmap.md` now reflects the compressed Gantt
  plan and W5+ roadmap.
- `scripts/dev/jira_sync.py` now emits sprint goals that match the redefined
  W4 to W7 sprint meanings.

## Jira Sync Evidence

Live Jira sync was completed from the local-only Jira environment config.

| Sprint | Jira Sprint ID | Date |
|---|---:|---|
| W4 | 41 | 2026-07-06 to 2026-07-12 |
| W5 | 42 | 2026-07-13 to 2026-07-19 |
| W6 | 43 | 2026-07-20 to 2026-07-26 |
| W7 | 44 | 2026-07-27 to 2026-07-31 |

Issue sync result:

- Updated existing epics: `SCRUM-10`, `SCRUM-11`, `SCRUM-60`.
- Created new epics: `SCRUM-61` to `SCRUM-64`.
- Updated existing current-week tasks: `SCRUM-40` to `SCRUM-48`, `SCRUM-56` to
  `SCRUM-59`.
- Created current-week VLM tasks: `SCRUM-65` to `SCRUM-76`.
- Created W5+ backlog tasks: `SCRUM-77` to `SCRUM-90`.
- Assigned 25 tasks to W4, 5 tasks to W5, 5 tasks to W6, and 4 tasks to W7.

## Operating Interpretation

The platform remains domain-general if domain-specific data, model, prompt, and
evaluation policies stay in domain packs and registries rather than being
hard-coded into platform services. Manufacturing visual inspection is the first
concrete policy pack and validation target, not the only future use case.
