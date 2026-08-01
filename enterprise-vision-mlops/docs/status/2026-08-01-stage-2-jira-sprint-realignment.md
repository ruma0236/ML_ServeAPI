# Stage 2 Jira Sprint Realignment

Date: 2026-08-01 KST
Status: Jira schedule and hierarchy update complete; Stage 2 implementation not started.
Baseline Git HEAD: `f592368499f0f292ceadc2512da449a558311661`

## Scope

This checkpoint changes Jira planning metadata only. It does not implement an
A-E reliability unit, run fault injection, mutate the live Kubernetes runtime,
or satisfy A8 maintenance approval.

## Sprint History Cleanup

- Sprint `144`, `EVM W7 2026-07-13~2026-07-16`, was closed at
  `2026-08-01 23:06:25.500 KST`.
- `SCRUM-49`, `SCRUM-50`, `SCRUM-51`, `SCRUM-52`, and `SCRUM-144` were moved
  to the backlog before closure.
- Their `To Do` status and parent/Epic were not changed.
- Their prior closed-sprint memberships remain intact.
- Closed W0-W8 sprint history and issue states were not changed.

## Active Stage 2 Timebox

| Field | Jira value |
|---|---|
| Sprint ID | `178` |
| Name | `EVM S2 A-E 2026-08-01~08-02` |
| State | Active |
| Start | `2026-08-01 23:07:32.352 KST` |
| End | `2026-08-02 23:59:00 KST` |
| Board | `SCRUM board` / `1` |

Jira enforces a 30-character sprint-name limit, so the approved date-bearing
name was shortened without changing its scope.

The sprint goal is A-E readiness, implementation start, and non-disruptive
validation. It is not a commitment to complete A8, live mutation, or any other
approval-required action by the end date.

`SCRUM-172..176` are Jira subtasks and cannot be assigned to a sprint
independently. With explicit approval, parent `SCRUM-171` was assigned to
Sprint `178`; the five scenario issues inherit that sprint while retaining the
existing hierarchy.

| Work | Jira | Status after scheduling | Sprint membership |
|---|---|---|---|
| master | `SCRUM-171` | In Progress | direct `178` |
| A: GPU and serving | `SCRUM-172` | To Do | inherited `178` |
| D: lifecycle supervision | `SCRUM-175` | To Do | inherited `178` |
| B: invalid model rollback | `SCRUM-173` | To Do | inherited `178` |
| E: data/artifact integrity | `SCRUM-176` | To Do | inherited `178` |
| C: quality/retraining gate | `SCRUM-174` | To Do | inherited `178` |

No issue was transitioned to Done. A0-A7 remain unstarted, and A8 remains
outside the sprint completion promise pending separate maintenance approval.

## Epic Hierarchy Repair

The approved roadmap mapping was applied without changing issue status or
sprint assignment:

| Epic | Tasks linked |
|---|---|
| `SCRUM-119` | `SCRUM-123..127` |
| `SCRUM-120` | `SCRUM-128..130` |
| `SCRUM-121` | `SCRUM-131..133` |
| `SCRUM-122` | `SCRUM-134..135` |

`SCRUM-3` and the parent/status fields of `SCRUM-49..52` and `SCRUM-144` were
left unchanged because there is not enough evidence to cancel, close, or
reparent them.

## Audit Evidence

- Jira was re-queried after every mutation.
- Sprint `144` is closed and Sprint `178` is the only active sprint; there are
  no future sprints.
- Jira comments record the timebox boundary, inherited sprint behavior,
  backlog cleanup reason, and Epic mapping decisions.
- No credentials or tokens are stored in this document or the repository.

