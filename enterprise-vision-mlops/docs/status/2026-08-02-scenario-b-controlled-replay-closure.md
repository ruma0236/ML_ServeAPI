# Scenario B Controlled Replay Closure

Date: 2026-08-02
Scenario: `EVM-267 / SCRUM-173`
Router dependency: `EVM-244 / SCRUM-144`
Closure: `PASS - non-disruptive controlled replay`

## Scope And Boundary

Scenario B proves fail-closed model admission, deterministic bounded replay,
runtime guardrail containment and exact stable-route restoration with real VisA
records and real CUDA inference. It does not change the production Kubernetes
Deployment or serve real user traffic.

The local environment is one Docker Desktop Kubernetes node, one RTX 4080
SUPER and one production replica. Production canary remains a separate
`blocked_live_mutation` boundary until Scenario D, dual-model GPU admission,
exact-target preflight and separate maintenance approval pass. This is not a
business A/B test, high availability proof or enterprise SLA validation.

## Immutable Identity

- Collector source: `153af90076f3885f94acc1a1ca64f1648b8f9bd2`
- API, worker and observer runtime revision:
  `193dd1077c9d1eb8cb8ce0e5f1a6277e417d7108`
- Production Deployment UID: `cfdab424-dcc5-4d5f-a46f-ae7530441ef4`
- Active production Pod UID: `6bf1d8ca-f4b2-4dea-9d87-d6a06ceae3c2`
- Stable B0 model SHA:
  `abcb8504a36c1128d32021722cfedce6357fd73598a52f6c2a0d60aca9d9a27f`
- Stable image digest:
  `sha256:227b483f466678e00fbf13fd6b3ad1059ca2c6771239d204494fb610fa7d9f7a`
- CT manifest SHA:
  `7635a75aa7d5a5fd66b4dbb121203a809eceef637f8909c153899c53b4492566`
- CT snapshot: `ct-visa-open-data-e35d93d5561f-test-c6b466afb907`, 2,181
  holdout records and zero training overlap

The collector source and running service revision are deliberately recorded as
different identities. The isolated collector used the newer Scenario B code;
the supervised serving/control runtime remained at its known-good revision.

## Quality Admission Closure

Run: `scenario-b-quality-closure-20260802T032348Z-3058c67e`

- stable B0 observations: `1,000`, errors `0`;
- paired shadow observations: `500`, stable errors `0`, challenger errors `0`;
- challenger: real EfficientNet-B7 model SHA `3058c67e...d6f4`;
- measured F1: `0.6369426752`, policy minimum `0.75`;
- exact decision: `blocked_admission`;
- exact blocker: `quality_f1_below_minimum`;
- challenger allocation after decision: `0`;
- monotonic detection: `0.004751253 s`;
- verified stable recovery/postcondition: `0.039229892 s`;
- fresh post-replay B0 inference: PASS, exact stable model digest;
- common readiness closure: PASS;
- common live-proof closure: PASS;
- indexed evidence: `16 / 16` present, digest mismatches `0`.

## Runtime Rollback Closure

Run: `scenario-b-runtime-closure-20260802T032542Z-1d1df27f`

- stable B0 observations: `1,000`, errors `0`;
- raw B7 CUDA observations: `1,000`, errors `0`;
- paired shadow observations: `500`;
- deterministic assignments: `1,000` total, exactly `100` challenger;
- route-to-response identity: `1,000 / 1,000`;
- B7 quality: accuracy `0.9729481889`, F1 `0.8649885584`, AUROC
  `0.9874184855`;
- measured B7 p95 latency: `26.300635 ms`, policy maximum `30 ms`;
- controlled effective-observation failures: `2 / 100`, raw CUDA mutation `0`;
- error rate: `0.02`, policy maximum `0.01`;
- exact decision: `rolled_back`;
- exact blocker: `runtime_error_rate_exceeded`;
- challenger allocation after decision: `0`;
- monotonic detection: `0.009058264 s`;
- verified exact stable recovery: `0.049763517 s`;
- fresh post-replay B0 inference: PASS, exact stable model digest;
- common readiness closure: PASS;
- common live-proof closure: PASS;
- indexed evidence: `16 / 16` present, digest mismatches `0`.

Evidence root:
`F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/scenario-b`

## Runtime Postcondition

- GPU allocatable and device plugin: `1 / 1`;
- production Deployment: same UID, `1 / 1 Ready`;
- active Pod: same known-good Pod UID, `Running`, ready;
- readiness: exact B0 model, CUDA available;
- Prometheus target `evm-b0-production`: `up`;
- host supervisor: healthy;
- lifecycle worker and Kubernetes observer: live and revision matched;
- Kubernetes, data, model and serving route mutations: `0`.

Two old terminal Pods remain historical Kubernetes context. They do not affect
the ready Deployment, endpoint, exact target selection or current closure.

## Failed Attempts And Prevention

Scenario B retained every failed or incomplete attempt rather than rewriting
it:

1. Two first runs made correct challenger decisions but wrote container-local
   evidence URIs. Commit `e8da639` separated runtime and canonical host paths.
2. Two later candidates passed machine validation but lacked a distinct
   post-replay inference. Commit `5644276` added the missing required check.
3. Run `scenario-b-quality-closure-20260802T031803Z-3058c67e` then failed
   closed because the serving root was duplicated. Audit showed four earlier
   candidates had `1,000 / 1,000` stable HTTP failures. Commit `153af90`
   versioned the serving root and enforced zero stable errors at collector,
   typed-result and common-report layers.

RCA records:

- `docs/status/2026-08-02-scenario-b-evidence-uri-rca.md`
- `docs/status/2026-08-02-scenario-b-postcondition-contract-audit.md`
- `docs/status/2026-08-02-scenario-b-stable-replay-uri-rca.md`

Final verification passed `53 / 53` operational tests and Ruff.

Jira closure:

- `SCRUM-173 / EVM-267`: Done, evidence comment `10460`;
- `SCRUM-144 / EVM-244`: Done, evidence comment `10461`.

## Portfolio Claim

Supported: a single-node local MLOps system used immutable VisA/CUDA evidence
to reject a real under-threshold model, deterministically route a bounded
replay, detect a controlled runtime error breach, set challenger allocation to
zero and verify exact B0 recovery with machine-validated evidence.

Not supported: production user A/B, organic traffic behavior, multi-node or
multi-replica isolation, high availability, zero downtime, business uplift or
enterprise SLA compliance.
