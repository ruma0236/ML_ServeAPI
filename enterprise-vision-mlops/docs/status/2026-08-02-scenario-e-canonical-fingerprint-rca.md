# Scenario E Canonical Fingerprint RCA

Date: `2026-08-02`
Issue: `EVM-270 / SCRUM-176`
Attempt: `scenario-e-20260802T095506Z-65b08a3b`
State: failed attempt retained; correction implemented; fresh proof required

## Failure

The first formal clean-revision execution stopped before fixture construction,
admission publication, metrics publication, or any runtime mutation with
`scenario_e_canonical_validation_failed`.

All three canonical validations were admitted and returned identical checks,
counts, blockers and identities. Their decision fingerprints differed because
the raw `trust_freshness.observed.evaluated_at` timestamp advanced by one second
between replays and was included in the fingerprint material.

## Impact And Containment

- no corrupted subject was admitted;
- no deployment intent was created or changed;
- no canonical data, model artifact, production B0, GPU, device-plugin or
  Kubernetes workload was changed;
- no `_latest` admission or global Scenario E metric was published;
- the partial F-drive attempt remains immutable RCA evidence.

An independent read-only post-check confirmed the same production deployment
UID `cfdab424-dcc5-4d5f-a46f-ae7530441ef4`, `1 / 1` Ready, GPU `1`, ready
device-plugin UID `66a4391b-7e6c-491a-bd9a-519fc27c5f8a`, model SHA
`abcb8504...9a27f`, real CUDA inference, both Prometheus targets up, and the
unchanged 10-entry intent ledger SHA `2c6dfee9...032c0`.

## Correction

The evaluator continues to retain `evaluated_at` in each raw freshness check,
but excludes that observation-time field from the stable decision fingerprint.
Issued-at, expiry, manifest, identity, checks, blockers, counts and exact
exception inputs remain fingerprint-bound.

The regression test now validates the same signed subject at two distinct
times, requires the raw audit timestamps to differ, and requires the decision
fingerprint to remain identical.

## Recurrence Prevention

- Formal closure still requires three clean-revision canonical validations and
  three replays for every fixture.
- A fingerprint mismatch remains fail closed and cannot publish admission.
- The failed attempt cannot be used as closure evidence; a new pushed source
  revision and fresh evidence root are mandatory.
