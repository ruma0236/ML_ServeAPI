# S5-S7 Post-closure Independent Audit

## Scope and safety

- Scope: Scenario S5, S6, and S7 only. Scenario S8 remains planned and not run.
- Historical closure commits are immutable references: S5 `5aff042`, S6 `4f503a3`, and S7 `3ec3039`.
- Failed attempts retain zero acceptance credit and remain outside accepted private inventories.
- Runtime claim boundary remains one local physical node. No customer traffic, production SLA, physical multi-node HA/DR, multi-GPU, or broad model-quality claim is made.

## Audit verdict before remediation

| Scenario | Verdict | Finding | Required action |
| --- | --- | --- | --- |
| S5 | Reopened | Closure v1 recomputed the 30-point matrix but trusted regression, current-revision smoke, and cleanup summaries. | Bind canonical smoke and regression evidence, rehash private logs, add mutation tests, rerun current-revision regressions/smoke, then reclose without rerunning the unchanged matrix. |
| S6 | Pass with evidence hardening | Final repetitions prove exact Pod UID drain events under traffic, but their drain waits were 2-3 microseconds; a separate preflight proves one approximately 2-second in-flight drain. | Narrow the claim and fix negative tests for trace, drain UID, owner overlap, rollback, and approval reuse. No full matrix rerun. |
| S7 | Reopened | Closure v1 did not recompute final totals, conflated intentional over-limit rejection with admitted starvation, and did not bind family provenance or observed LLM 4-bit readiness. | Reproject the immutable matrix, capture all-family current-revision CUDA readiness, strengthen numerical/provenance validators, rerun regressions, then reclose. |

## Historical evidence retained

- S5: 30 accepted points and 57 private artifacts remain the acceptance dataset. Peak Spark executor memory is 150,994,944 bytes; peak single-process columnar process memory is 489,484,288 bytes.
- S5 skew: the provisional bound changed from 200 to 400 after a pre-acceptance pilot observed 280.18. This is pilot-informed tuning, not a sensitivity analysis.
- S6: 3 API rolling and 3 GPU handoff repetitions remain accepted under the narrowed drain-evidence claim.
- S7: 36 accepted profile repetitions remain immutable. Raw accounting is 162 completed, 54 intentional pre-admission rejections, selected/admitted starvation 0, and full-matrix long noncompletion 54.
- S7 provenance: image uses a CC-BY-4.0 dataset contract, VLM uses CC-BY-NC-SA-4.0 and is non-commercial portfolio/research evidence only, and LLM uses CC-BY-SA-3.0.

## Remediation status

- S5: implementing strict closure v2 and current-revision evidence binding.
- S6: validator mutation coverage implemented; scope synchronization pending final checkpoint.
- S7: implementing scoped outcome accounting, provenance/cache binding, and observed quantization readiness.
- S8: not started.
