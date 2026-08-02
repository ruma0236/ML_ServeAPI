# Scenario E Data And Artifact Integrity Closure

Date: `2026-08-02`
Issue: `EVM-270 / SCRUM-176`
State: Done for non-disruptive local operational validation
Executable revision: `89c93a56ce69558cac542af3286c931c782da961`

## Scope And Boundary

Scenario E validates the real VisA dataset/model identity graph and isolated
derived corruptions before training, promotion or deployment intent mutation.
Canonical data, production EfficientNet-B0, the GPU device-plugin and the
Kubernetes serving workload were read-only throughout the proof.

This is a single-workstation, local-filesystem and Docker Desktop Kubernetes
claim. It is not enterprise PKI/KMS, Sigstore/SLSA, multi-writer object-store,
HA, real-user traffic or organization-wide supply-chain certification.

## Final Evidence

- run: `scenario-e-20260802T101122Z-89c93a56`;
- root:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/scenario-e/scenario-e-20260802T101122Z-89c93a56`;
- report SHA-256:
  `141b0baf723f82ba02e1ab8dacd2c6112267d5aa8497695a5d538dfa632ce471`;
- evidence-index SHA-256:
  `dcf28736a2ec36b16a7dd19b35f231ace6b4f6a2075e83fcb18f0c48f30eda31`;
- 36/36 indexed artifact hashes independently matched;
- readiness and live-proof common closure validators returned no errors.

## Acceptance Results

| Requirement | Result |
|---|---|
| real canonical identity | 10,821 records, 23 shards, CT exactly 2,181 test identities |
| canonical repeatability | 3/3 admitted, one decision fingerprint, false blocker 0 |
| validation latency | max `0.190359 s` against local target `120 s` |
| corruption matrix | 14/14 fixtures, 42/42 replays, deterministic fingerprint per fixture |
| corrupted fixtures | 39/39 blocked; corrected isolated fixture 3/3 admitted |
| evidence integrity | 36/36 hashes and both common closures passed |
| downstream mutation | production ledger count `10 -> 10`, SHA unchanged |
| admission freshness | signed TTL and measured lifetime both `3,600 s` |
| expiry behavior | minute 59 admitted; exact expiry blocked as `integrity_admission_stale` |

The blocked fixtures cover invalid signature, stale trust, missing/tampered
manifest, missing shard, duplicate identity, split leakage, CT mismatch,
missing lineage, model identity mismatch, tampered model, MLflow mismatch and
container digest mismatch. The corrected isolated fixture is the only admitted
fixture.

## Runtime Invariants

- production deployment UID remained
  `cfdab424-dcc5-4d5f-a46f-ae7530441ef4`, `1 / 1` Ready;
- model SHA remained
  `abcb8504a36c1128d32021722cfedce6357fd73598a52f6c2a0d60aca9d9a27f`;
- serving image remained digest
  `sha256:227b483f466678e00fbf13fd6b3ad1059ca2c6771239d204494fb610fa7d9f7a`;
- GPU allocatable remained `1`; ready device-plugin UID remained
  `66a4391b-7e6c-491a-bd9a-519fc27c5f8a`;
- real inference remained CUDA; `evm-api` and `evm-b0-production` Prometheus
  targets remained up;
- all 30 canonical byte-digest subjects and the 10-entry deployment ledger
  remained byte-identical.

## Live Control-Plane Proof

The API was rebuilt and converged to executable revision `89c93a5` with
Scenario E enforcement enabled. The exact final admission returned zero
integrity blockers. A live deployment request remained blocked by independent
CI/readiness/policy gates, and the production ledger count and SHA did not
change.

An isolated one-off API container used a missing admission and an ephemeral
`/tmp` intent root. It returned `integrity_admission_missing`, created no
deployment ledger and no deployment record. It did write its ordinary
`latest_ci_validation.json` auxiliary evaluation before aggregate admission,
which is not a deployment intent.

The API exports Scenario E `state="passed"` and healthy identity/integrity
metrics. Prometheus loaded `EVMDataArtifactIntegrityBlocked`; it is inactive
for the final state. API health is `ok` and both monitored targets are up.

## Tests And Configuration

- focused integrity/deployment/metrics tests: `21 / 21`;
- full repository tests: `395 / 395`;
- Ruff: pass;
- Docker Compose config: pass;
- Prometheus rule validation: `11` rules, pass.

## RCA History

Attempt `scenario-e-20260802T095506Z-65b08a3b` failed closed because raw
evaluation time changed a stable decision fingerprint. Commit `5b67483`
preserved audit time but removed it from decision identity and added a
distinct-time regression test.

Run `scenario-e-20260802T100123Z-5b67483a` passed the matrix but was withheld
from closure because admission inherited a 30-day trust-manifest lifetime.
Commit `89c93a5` signed a one-hour TTL and made pointer extension fail closed.
Both earlier runs remain history and are not represented as final evidence.

## Portfolio Claim

Supported claim: implemented and validated a signed, fail-closed local
data/model integrity admission over real VisA identities, deterministic
corruption replays, MLflow/lineage/model/container joins, one-hour freshness,
zero deployment intent for missing trust, immutable evidence indexing and
unchanged real CUDA serving state.

Do not claim enterprise PKI/KMS, Sigstore/SLSA compliance, remote registry
attestation, production traffic, multi-node HA or organization-wide policy.
Automatic admission renewal and cross-scenario correlation remain future work.
