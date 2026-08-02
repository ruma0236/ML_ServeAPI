# Scenario B Postcondition Contract Audit

Date: 2026-08-02  
Scenario: `EVM-267 / SCRUM-173`  
Router dependency: `EVM-244 / SCRUM-144`

## Finding

Two source-bound replay candidates passed the common machine validator and all
indexed artifact hashes:

- `scenario-b-quality-final-20260802T031145Z-3058c67e`
- `scenario-b-runtime-final-20260802T031309Z-1d1df27f`

They are not final Scenario B closure evidence. A manual contract-to-code audit
found that the collector checked production readiness and the exact Prometheus
target after evaluation, but did not execute and persist a new production B0
inference as a distinct postcondition. The B0 contract explicitly requires a
postcondition inference after containment or rollback.

## Impact

The quality decision, runtime decision, CUDA measurements, routing identity,
canonical artifact indexes and common report validation remain useful
diagnostic evidence. They do not prove the complete postcondition contract and
must not be promoted to final closure evidence.

Production Kubernetes was not mutated. The exact production B0 target remained
`1 / 1 Ready`, and Prometheus remained `up`.

## Corrective Action

- Execute one fresh stable B0 prediction after candidate evaluation and all
  rollback decisions.
- Require the response model digest to equal the captured stable digest.
- Persist the inference observation under `stable_after.inference`.
- Add `post_replay_inference` as a required live-proof postcondition.
- Fail closed before writing a passing report if the inference fails or the
  model identity differs.
- Add focused regression coverage and rerun the complete operational suite.

## Prevention

Machine schema validation and artifact hashing are necessary but insufficient.
Scenario closure also requires a line-by-line acceptance audit against the
versioned scenario contract. Candidate runs remain immutable when that audit
finds a missing check; the implementation is corrected and new run IDs are
generated.

## Claim Boundary

These two candidates demonstrate validator and artifact integrity, not complete
Scenario B closure. Only fresh runs containing a passing
`post_replay_inference` check can close B6.
