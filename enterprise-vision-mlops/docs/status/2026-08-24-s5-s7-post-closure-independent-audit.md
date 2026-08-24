# S5-S7 Post-closure Independent Audit

## Verdict

**PASS within the declared local scope.** S5, S6, and S7 were revalidated from
their immutable accepted raw evidence and current-revision runtime checks. The
historical v1 closures remain in Git history and are superseded by strict v2
closures at reclosure commit `d585ed8`.

S8 was not started. This audit does not prove customer production behavior,
an SLA, physical multi-node or multi-zone HA/DR, or multi-GPU operation.

## Historical Ledger

| Scenario | Implementation and accepted evidence | Historical closure | Audit remediation and reclosure |
|---|---|---|---|
| S5 | `046a890` governed Spark path; `7b9e568` and `321dfc0` preserve failed replay/projection RCA; `c0ab34f` publishes the accepted 30-point matrix | `5aff042` | `6f8fbb4` validator hardening; `3bfbbce` final regression binding; `d585ed8` strict v2 reclosure |
| S6 | `c712534` contract; `abab6bb` accepted 3 API + 3 GPU repetitions; earlier drain/latency corrections remain in history | `4f503a3` | `8fcef53` raw trace, monotonic interruption, and drain-scope hardening; `d585ed8` strict v2 reclosure |
| S7 | `519ce49` family admission; retained diagnostic and projection RCA; `c94c70d` accepted 36-run matrix | `3ec3039` | `6f8fbb4` outcome/provenance/readiness hardening; `c2d3e6a` deterministic reprojection; `d585ed8` strict v2 reclosure |

Every failed attempt retained in the scenario evidence has
`acceptance_credit=false`. None is included in the accepted 30-point S5,
3+3-repetition S6, or 36-repetition S7 counts.

## Reproduced Defects

The following mutations reproduced material v1 validation gaps and are now
negative tests:

1. S7 admitted `image-small` changed from 6 completed to 5 completed plus 1
   rejection. The old analysis still passed; the strict validator now rejects
   incomplete admitted profiles and nonzero pre-admission rejection there.
2. S7 `image-over-limit` gained one OOM. The old analysis still passed; the
   strict validator now requires zero OOM and exact intentional reason codes.
3. S5 smoke used `output_digest=not-a-sha` and a duplicate fourth engine. The
   old smoke validator accepted it; strict validation now requires three exact
   engines, a 64-hex digest, 766,864 committed rows, and zero missing/duplicate
   records recomputed from private raw logs.
4. S6 sampled request trace headers and GPU monotonic interruption timestamps
   were changed while summary booleans remained true. Strict projection now
   recomputes trace identity and interruption durations from raw observations.

The focused mutation proof passed 9 tests. The final combined S5-S7 focused
suite passed 84 tests at implementation revision `8fcef53`.

## Scenario Results

### S5

- The original 30 accepted points and 57 private artifacts rehashed exactly.
- Current-revision smoke independently reproduced three engine paths, 766,864
  rows, one exact output digest, and zero missing or duplicate rows.
- Peak Spark executor memory was 150,994,944 bytes. The separate
  single-process columnar process peak was 489,484,288 bytes.
- The skew guardrail change from 200 to 400 is recorded as pilot-informed
  pre-acceptance tuning after an observed 280.18 ratio, not sensitivity proof.
- Closure Git-byte SHA-256:
  `e8d5e85fd6b9774677fe49bb2954bdc30c1f1da34e9b65138ed54e375217a849`.

### S6

- All 3 API rolling and 3 single-GPU handoff repetitions reprojected from raw
  evidence; accepted loss and duplicate effects remained zero.
- Raw sampled W3C trace headers and monotonic interruption timelines now drive
  closure values rather than trusted summary booleans.
- Final exact old-Pod UID drain events measured 3, 2, and 3 microseconds. They
  prove target-scoped drain events under traffic, not long in-flight waits.
- A separate non-acceptance preflight proves one approximately two-second
  request completed during termination.
- Closure Git-byte SHA-256:
  `fb3dc5c8773e6d7adde36fa59cf60e893e527b83be9497a4936c72ce8b458c31`.

### S7

- The unchanged 36-run inventory reprojected to 162 completed requests, 54
  intentional over-limit pre-admission rejections, zero expiry, transport
  failure, OOM, or selected/admitted starvation, and 54 full-matrix long
  noncompletions.
- Family asset source, revision, digest, license, provenance, cache identity,
  exact `/ready` payload, and observed LLM 4-bit state are evidence-bound.
- ScienceQA-derived VLM evidence is non-commercial portfolio/research evidence
  under CC-BY-NC-SA-4.0.
- Closure Git-byte SHA-256:
  `30fec6916f4463d39a86103ea0c0ea0cf583a5add18a2a30d78cf4b1994587bf`.

## Regression Evidence

The following suites passed at `8fcef53`; each command, exit code, count, byte
length, and private log SHA-256 is bound in the public regression evidence:

| Gate | Result |
|---|---|
| Changed-file Ruff | PASS |
| S5/S6/S7 focused | 84 passed |
| S5 focused | 17 passed |
| Real PostgreSQL | 38 passed |
| Lifecycle/host E2E | 144 passed |
| Full Python | 860 passed, 1 skipped, 1 warning |
| Control Panel Python + frontend | 77 + 59 passed |
| Frontend production build | 1,803 modules built |
| S0-S7 status/evidence | 94 passed |

Regression evidence Git-byte hashes are
`8e5f6b3d...246624` for S5 and `6a75a5eb...f25df` for S6/S7.

Two command mistakes were retained outside acceptance: one PowerShell wildcard
did not expand and one progress-validator invocation omitted `--progress`.
Both were corrected and rerun successfully. A pre-commit closure check also
mistakenly requested committed Git bytes, which correctly loaded historical v1;
the worktree closure was then validated before commit and committed Git bytes
were validated after reclosure.

## Runtime And Cleanup

The post-reclosure runtime audit records:

- source serving deployment ready `1/1`;
- staging target replicas `0`;
- actual CUDA readiness and inference;
- active queue, lease, and outcome-unknown count `0`;
- S5-S7 labeled temporary resource count `0`;
- Prometheus targets `5/5` UP.

The private runtime log is retained as
`private/post-closure-audit/d585ed8/runtime/cleanup-smoke.log` with SHA-256
`d6101692c400b8ff4e5ffa2c03002c18112365086965d4e7c19648e877b26f88`.

Three historical `ContainerStatusUnknown` serving Pods remain unrelated cluster
debt. They were not deleted, hidden, or counted toward acceptance.

## Synchronization

Git is the canonical source for this audit. Jira `SCRUM-206`, `SCRUM-205`, and
`SCRUM-207`, the canonical Notion V3 page, and the Obsidian work log/context/
retrieval graph are synchronized only after the Git progress ledger and this
report pass committed Git-byte validation.

The four-system synchronization was completed and cross-checked at
`2026-08-24T14:13:52+09:00` against Git revision
`16a41c4d2e31d3f2095bf0881947a623d9737cfc`:

- Jira `SCRUM-205`, `SCRUM-206`, and `SCRUM-207` remain Done with strict-v2
  descriptions and comments `10665`, `10664`, and `10666`; Epic `SCRUM-199`
  is In Progress with comment `10663` because S8 remains planned/not-run.
- Notion page `2026-08-15 Distributed Scale And Operational Load Validation
  Plan V3` contains the strict-v2 audit, narrowed claims, runtime cleanup, and
  S8-not-started boundary.
- Obsidian work log, Current Context, retrieval index, and work-log graph contain
  the same Git revision, outcomes, claim boundary, and Jira state.
