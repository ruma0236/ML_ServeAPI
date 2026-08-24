# S8 Evidence Non-finite Histogram RCA

- Date: 2026-08-24
- Scenario: S8 Dependency Soak & Resource-efficiency Closure
- Status: runtime exercised; hash closure blocked pending deterministic reprojection
- Runtime revision: `9f0416c7593c7947dc4584aab4750cd0ee45e07f`

## Observed Failure

The fresh S8 run completed all 21 dependency-fault scopes and all three 30-minute
soak repetitions. The independent evidence validator then failed before hash
closure because three retry-budget queue-wait histograms serialized the Prometheus
`+Inf` overflow bucket as the non-standard JSON value `Infinity`.

The accepted runtime outcomes were not affected. The defect was limited to the
representation of `observed_upper_bound` when every finite histogram bucket was
below the observed maximum. The exact per-request maximum remained present in the
same metric summary.

## Root Cause

`histogram_metric_summary()` used `float("inf")` when no finite bucket contained
all observations. The private and public JSON writers allowed Python's non-standard
`Infinity` token, while the strict S8 canonical comparator correctly rejected
non-finite numbers.

## Remediation

- Represent finite-bucket overflow as `observed_upper_bound: null` plus
  `observed_upper_bound_status: overflowed_finite_buckets`.
- Reject NaN and Infinity in public and private canonical evidence writers.
- Make the S8 validator report non-finite evidence as a controlled fail-closed
  validation error instead of raising an unrelated JSON encoder exception.
- Require the private evidence index to cover every private artifact except its
  self-referential index and summary files.
- Preserve original bytes in an append-only private amendment directory, then
  deterministically reproject only the three affected private files and all
  derived public summaries.
- Bind the projection script to a committed Git blob and retain the original
  runtime revision separately from the projection revision.

## Rerun Decision

The 21 fault scopes and three soak repetitions are not rerun because neither the
runtime path nor any measured outcome changes. Acceptance credit remains withheld
until the normalized private projection, public artifact, independent validator,
Git-blob rehash, regression evidence, and final closure all pass.

## Projection Attempt History

- Attempt 1 stopped before writing the public artifact or replacement private
  index because three derived `strict_evidence.waits.queue` fields had the same
  overflow representation and were not in the initial amendment allowlist.
- The three normalized private profile files and all original backups were retained.
  The projection tool was made resumable without overwriting those backups, and the
  three derived public paths were added to the exact allowlist. This attempt receives
  no closure credit; the resumed attempt must regenerate and validate the complete
  projection.

## Claim Boundary

This remains controlled single-local-physical-node evidence. It does not establish
customer production SLA, physical multi-node or multi-zone HA/DR, multi-GPU
behavior, or simultaneous multi-model GPU residency.
