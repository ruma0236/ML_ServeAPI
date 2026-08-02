# Scenario E Data And Artifact Integrity Contract

Date: `2026-08-02`
Issue: `EVM-270 / SCRUM-176`
State: E0 contract complete; implementation and execution pending
Scope: non-disruptive local VisA data and release-artifact integrity admission

## Claim Boundary

Scenario E proves a fail-closed integrity gate on one local machine using the
real VisA identity graph and isolated derived corruptions. It does not prove a
managed KMS, organization-wide PKI, transparency log, remote registry trust,
multi-writer object-store consistency, HA, or production traffic safety.
Production B0, the GPU device plugin, canonical data and existing deployment
intents are read-only invariants throughout this scenario.

## E0 Baseline And Gap

Existing readiness and deployment code checks selected model and CI digests,
but there is no single signed admission that joins dataset, split, CT, MLflow,
model, container and evidence identities before downstream mutation.

The real baseline also demonstrates why byte and semantic identity must remain
separate. The current VisA shard index byte SHA-256 is
`8b6f281abc93e735545e060b0f6d8b8b7e506d8a0e296f45c84347cb44a91115`,
while its stable dataset identity remains
`64043adeeca6654467b842c7b5bb8fc64ce8a0b2c78ca158623164f829a38cd0`.
Trace and split metadata changed after training, but the 23 shard descriptors,
10,821 records, split counts and stable identity did not. Treating either value
as the other would create a false blocker or hide provenance change.

The production B0 lineage has no training source revision. Scenario E may
admit that legacy baseline only through an exact, expiring, signed exception
for `training_source_revision_missing`. No exception may override signature,
checksum, leakage, lineage, model, image, CT or evidence mismatch.

## Threat And Failure Model

| Domain | Failure or threat | Required decision |
|---|---|---|
| dataset | missing shard/record, duplicate sample/content, split leakage | block before training |
| manifest | missing, malformed, changed bytes, stale trust manifest | block all downstream intent |
| lineage | missing parent or dataset/split/candidate join mismatch | block training and release |
| CT | holdout missing, duplicated or not equal to the declared test identity | block evaluation/release |
| model | artifact bytes differ from lineage/model card/MLflow identity | block promotion/deployment |
| container | observed image digest differs from signed subject | block deployment |
| MLflow/registry | run missing/not finished or candidate/dataset identity differs | block promotion/deployment |
| evidence | report/index missing, digest mismatch or replay ambiguity | do not close the scenario |
| trust root | unknown key, invalid signature, expired grant or broad exception | block fail-closed |

## Trust Root And Subject Identity

The local proof uses an Ed25519 public key pinned in the versioned repository.
The private key is stored outside Git under the F-drive secret root with local
filesystem access restrictions. This is a local signing root, not KMS-backed
enterprise attestation.

The signed identity tuple is:

```text
dataset_version
shard_identity_sha256
shard_manifest_sha256
split_manifest_sha256
ct_manifest_sha256
candidate_id
model_digest
container_image_digest
mlflow_run_id
candidate_summary_sha256
lineage_sha256
model_card_sha256
policy_sha256
```

The trust manifest also binds expected record/shard/split counts, canonical
paths, issue, issuer, key id, issue/expiry timestamps, lineage parents and any
exact exception. Canonical JSON bytes are signed; the detached signature and
public key fingerprint are part of every validation report.

## Stable Blocker Taxonomy And Precedence

Signal precedence is trust -> freshness -> presence/checksum -> dataset
contract -> leakage/CT -> lineage/model/MLflow -> container -> admission.
Primary blockers remain stable while the full blocker list preserves all
diagnostics.

Required blockers include:

- `trust_signature_invalid`, `trust_key_unknown`, `trust_manifest_stale`;
- `manifest_missing`, `manifest_digest_mismatch`, `manifest_schema_invalid`;
- `shard_missing`, `shard_digest_mismatch`, `manifest_count_mismatch`;
- `duplicate_record_identity`, `duplicate_content_identity`,
  `split_leakage_detected`, `ct_identity_mismatch`;
- `lineage_parent_missing`, `dataset_identity_mismatch`,
  `model_artifact_digest_mismatch`, `model_identity_mismatch`,
  `mlflow_identity_mismatch`, `container_image_digest_mismatch`;
- `exception_invalid`, `evidence_digest_mismatch`,
  `integrity_admission_missing`, `integrity_admission_stale`.

## Implementation Levels And Dependencies

| Level | Deliverable | Exit condition |
|---|---|---|
| E0 | this contract and measured baseline | threat model, trust root and acceptance fixed |
| E1 | strict trust/evidence schemas and Ed25519 verification | malformed/unsigned/stale input fails closed |
| E2 | streaming VisA/shard/CT and model-lineage observer | real inputs produce one deterministic observation |
| E3 | isolated corruption builder and fixture matrix | each failure class has an exact recipe and blocker |
| E4 | deployment-intent admission fence | blocked integrity creates zero intents |
| E5 | immutable evidence, metrics and alert projection | operator can see pass/block and evidence URI |
| E6 | three deterministic fixture replays | blocker/fingerprint agreement is 100% |
| E7 | three real canonical validations and invariant audit | false blocker 0 and canonical hashes unchanged |
| E8 | RCA/closure and four-system synchronization | independent validator and records agree |

E0-E7 are non-disruptive. Any future external object retention test, registry
mutation, production rollout or canonical-file corruption is outside this
approval and requires a separate maintenance decision.

## SLI, SLO And Acceptance

| SLI | Local target | Measurement |
|---|---:|---|
| corruption detection | 100% | expected versus observed primary blocker |
| signature/checksum coverage | 100% required subjects | signed manifest and recomputed file digests |
| canonical false blocker | 0 across 3 runs | real VisA admission decision |
| deterministic decision | 100% across 3 fixture replays | decision fingerprint equality |
| blocked downstream mutation | 0 task/promotion/deployment intents | before/after ledgers |
| canonical mutation | 0 | before/after digest set equality |
| warm validation latency | `<= max(120 s, 1.5x baseline)` | monotonic full-scan timing |
| stale admission detection | `<= 30 s` after next evaluation | explicit UTC expiry evaluation |

Acceptance requires every fixture to fail with its stable blocker, a corrected
isolated bundle to pass, real canonical validation to pass three times, signed
evidence to validate, and production B0/GPU/device-plugin/data identities to
remain unchanged. Any false negative is P0 and stops closure. False positives
enter an RCA loop; latency is optimized only after exact decisions are stable.

## Admission, Audit, Hold And Rollback

The gate writes an immutable signed validation report and a short-lived latest
admission pointer only after all required checks pass. Training, promotion and
deployment entry points consume the exact subject fingerprint. Missing,
ambiguous, stale or mismatched admission produces no intent and records an
audit blocker. A correction creates a new signed manifest; failed evidence is
never overwritten. Because Scenario E performs no production mutation,
rollback is a hold/quarantine action rather than a deployment rollback.

The signed manifest fixes the admission TTL at `3,600 s`. The pointer expires
one hour after validation even though the underlying trust manifest and exact
legacy exception may remain valid longer. Refresh therefore requires a new
full canonical validation; an operator cannot extend TTL by editing the
unsigned latest pointer.

## Evidence And Portfolio Positioning

Each run stores the signed trust manifest, observation, check results, timing,
ledger and runtime invariant diffs, report, metric projection and SHA-256
evidence index under the F-drive scenario root.

Factual portfolio claim after E exits: implemented and executed a signed,
digest-bound fail-closed data/model supply-chain gate over real VisA identities
and deterministic isolated corruptions. Prohibited claims: enterprise PKI/KMS,
SLSA compliance, Sigstore transparency, registry attestation, HA or production
traffic validation.

Interview depth: byte versus semantic identity, cross-split leakage at scale,
streaming hash/index trade-offs, trust-root rotation, legacy exceptions,
TOCTOU between admission and execution, and why evidence must be immutable.
