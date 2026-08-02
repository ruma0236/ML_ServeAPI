# Cross-Scenario Correlation And Recovery Design Validation

Date: 2026-08-02
Review mode: read-only design review; no implementation, fault injection,
runtime mutation, production action, Jira status rewrite, or evidence reuse.
Reviewed draft: `cae9f63`
Final verdict: **PASS for implementation planning only**

## Scope

The review compared the new workstream plan against the A-E independent closure
summaries, the operational-failure master, the Stage 2 dependency contract, the
issue register, and live Jira hierarchy/status evidence. A-E evidence remains a
baseline reference and was not counted as combined acceptance.

## Initial Draft Findings And Remediation

| Severity | Finding in draft `cae9f63` | Risk | Exact remediation recorded in the final plan |
|---|---|---|---|
| P1 | the dedupe fingerprint included raw `evidence_digest` | volatile timestamps or serialization could create duplicate incidents, repeating the Scenario E timestamp-fingerprint class | separate stable `semantic_identity_digest` from raw audit digest; key dedupe on stable semantics and retain multiple raw observations |
| P1 | E-before-D precedence did not fully define recovery exceptions | stale observer evidence could block the exact D recovery required to restore evidence, or a broad exception could bypass integrity | add a deadlock-free, risk-reducing containment matrix; D exact-child recovery and B allocation-to-zero are narrowly allowed, while rollout/promotion remain blocked |
| P1 | UUIDv7 and leases lacked durable restart semantics | coordinator restart could create duplicate parent incidents, owners, or actions | add atomic root-fingerprint compare-and-create, durable owner/action ledgers, monotonic fencing tokens, renewal/expiry, and restart fixtures |
| P2 | freshness and decision timing lacked fixed local values and signal closure | latency could be measured from a convenient timestamp or wait indefinitely for missing signals | fix initial cadence/freshness/deadline/clock tolerance, producer boot/sequence, coordinator ingestion, and held/blocked behavior at the deadline |
| P2 | replay acceptance did not define event volume or anti-correlation density | three very small fixtures could satisfy the words without meaningful collision coverage | require three independent 1,000-event replays, each with at least 100 unrelated near-time events and a coordinator-restart fixture |
| P2 | evidence did not explicitly separate identity and policy decision artifacts | reviewers could not prove which stable inputs drove a correlation decision | add `identity-map.json` and `policy-decision.json` to the evidence contract |

## Requirement Validation

| Requirement | Result | Evidence after remediation |
|---|---|---|
| A-E baselines and exclusions | PASS | exact Jira/evidence summaries are baseline-only; runtime, data, process and sprint exclusions are explicit |
| signal precedence | PASS | E/D permission gates, A/B/C action order and narrow risk-reducing exceptions are defined |
| identity propagation | PASS | data, model, Kubernetes, lifecycle and evidence tuples plus semantic/raw digests are required |
| correlation and causality | PASS | UUIDv7 root, durable fingerprint index, explicit causation DAG and unknown/cycle blockers are defined |
| alert dedupe and anti-correlation | PASS | semantic fingerprint, active/recurrence behavior, TTL, near-time negative fixtures and volume are fixed |
| recovery ownership | PASS | one durable CAS owner, monotonically increasing fencing token, lease cadence/expiry and action ledger are defined |
| dependency and safe order | PASS | D+E, C+E, B+C, A+D, A+B follows mutation risk and evidence dependencies |
| fail-closed boundary | PASS | zero/multiple/stale/mismatched/ambiguous evidence and approvals create zero risk-increasing mutation |
| SLI/SLO and measurement | PASS | identity, false merge, duplicate action, decision latency, coordinator overhead and child timing are separated |
| acceptance and evidence | PASS | three replay series, negative fixtures, restart behavior, parent/child hash closure and immutable RCA are required |
| portfolio boundary | PASS | only bounded local correlation/recovery coordination is allowed; HA, SLA, business A/B and autonomous remediation are prohibited |
| Jira structure | PASS | `SCRUM-177` Epic and `SCRUM-178..182` dependency chain exist outside Sprint 178; A-E Done states are unchanged |

## Residual Risks And Entry Gates

- The targets are design values, not measured SLOs. Implementation must retain
  them as provisional until EVM-272/EVM-274 evidence exists.
- The local file/state store is not a distributed consensus system. A future
  multi-node design would require an external transactional store, leader
  election, clock discipline, and partition testing.
- Low-cardinality metric names, API/OpenAPI schemas, Control Panel views, and
  alert routing are deliberately left to EVM-273 after the core event contract.
- A+D or A+B live proof remains blocked until EVM-274 PASS and a new exact-target
  maintenance approval. This review grants no such approval.

## Start Decision

`EVM-272 / SCRUM-179` may start in a later implementation turn because the
planning contract is now sufficiently specific. This turn must stop after plan
and four-system synchronization. `EVM-273..275` remain To Do, and no live action
is admitted.

## Portfolio Review

The plan can be described as an architecture and validation contract. It cannot
yet be described as an implemented incident-correlation engine, automated
recovery controller, combined failure proof, or production reliability result.
