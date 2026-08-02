# Scenario E: Data and Artifact Integrity Gate

Issue: `EVM-270 / SCRUM-176`
State: E0-E8 Done for non-disruptive local operational validation.

Authoritative contract:
`docs/status/2026-08-02-scenario-e-data-artifact-integrity-contract.md`.
Implementation checkpoint:
`docs/status/2026-08-02-scenario-e-integrity-implementation.md`.
Closure:
`docs/status/2026-08-02-scenario-e-data-artifact-integrity-closure.md`.

## Purpose

Fail closed on data leakage, duplicates, missing data, digest/lineage breakage,
and model artifact mismatch before downstream mutation.

## Preconditions

Immutable source/split/CT manifests, expected counts, lineage, MLflow run, model
card, artifact/image digests and rollback identity are readable. Canonical data
is mounted or treated read-only.

The validator distinguishes the byte checksum of a changing operational
manifest from its stable semantic dataset identity. A versioned Ed25519 public
key verifies the signed trust manifest; its private key remains outside Git.

## Safe Injection Matrix

Create isolated copies under the scenario evidence root:

| Injection | Expected blocker |
|---|---|
| duplicate record/sample ID | `duplicate_record_identity` |
| train and CT overlap | `split_leakage_detected` |
| missing shard or record | `manifest_count_mismatch` |
| changed manifest bytes | `manifest_digest_mismatch` |
| disconnected lineage parent | `lineage_parent_missing` |
| wrong model artifact bytes | `model_artifact_digest_mismatch` |
| MLflow/model-card mismatch | `model_identity_mismatch` |
| invalid/unknown trust signature | `trust_signature_invalid` |
| expired signed manifest | `trust_manifest_stale` |
| container digest mismatch | `container_image_digest_mismatch` |

Blocker names are part of the planned implementation contract and must remain
stable once tests and UI consume them.

## Signals

Schema result, source and manifest digests, counts, duplicate/leakage identities,
lineage edges, MLflow/model-card identity, artifact checksum, readiness checks,
command-intent audit and proof that no downstream work was queued.

## Success

- Every isolated corruption yields its deterministic blocker.
- Training, promotion and deployment remain unqueued.
- Canonical source/artifact digests do not change.
- Corrected isolated copies pass the same validator.
- Repeated validation returns the same decision and fingerprint.
- Raw evaluation timestamps remain in evidence but are excluded from the
  decision fingerprint; signed issue/expiry times remain fingerprint-bound.
- Admission TTL is signed at `3,600 s`; expiry requires a fresh full validation
  and cannot be extended by editing the latest pointer.
- A blocked validation creates zero deployment intents through the E admission
  fence.

## Failure and Mitigation

Any corrupted input passing the gate is a P0 failure. Quarantine the derived
copy, cancel downstream intents, restore only immutable references, and retain
the failed validation evidence for RCA.

## Interview Evidence

- Demonstrates: data contracts, leakage controls, lineage and supply-chain integrity.
- Expected questions: digest validation location; scalable leakage detection;
  artifact identity across MLflow, registry and serving; fail-open trade-offs.
- Claim allowed: deterministic validation over real VisA identities and isolated
  corruptions.
- Claim prohibited: organization-wide governance or cryptographic attestation.
