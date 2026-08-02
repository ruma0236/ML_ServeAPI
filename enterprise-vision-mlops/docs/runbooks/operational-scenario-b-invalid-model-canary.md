# Scenario B: Invalid Model Canary and Rollback

Issue: `EVM-267 / SCRUM-173`
State: B0 contract passed; non-disruptive EVM-244 implementation admitted.
Contract: `docs/status/2026-08-02-scenario-b-controlled-canary-contract.md`

## Purpose

Prevent a challenger that violates quality, latency, or error guardrails from
silently replacing the stable model, and prove exact rollback.

## Preconditions

- Scenario A passed and is referenced only as baseline evidence.
- Stable/challenger artifact and image digests are immutable.
- CT data is isolated from training and tuning.
- Approval identity differs from the initiating actor where policy requires it.
- Router records request assignment, metric window and stop decision.

## Traffic Sequence

1. Shadow: duplicate controlled VisA requests; discard challenger responses.
2. Isolated controlled canary: after shadow/readiness/CT pass, select at most
   10% of evidence-only replay responses from the challenger. This cannot alter
   the production endpoint.
3. Stop: breach of quality, p95 latency or error-rate policy sets challenger
   allocation to zero.
4. Rollback: restore the exact captured stable digest and verify inference.

Use a real known under-threshold candidate or a versioned policy it genuinely
violates. Do not lower production policy merely to manufacture success.

## Required Signals

Route assignments and counts, response source, success/error rate, p50/p95/p99,
CT quality metrics, candidate/stable digests, policy version, deployment intent,
Kubernetes revision, stop reason, rollback audit and post-rollback inference.

## Success

- Shadow does not affect the returned response.
- Canary never exceeds its configured bound.
- Decision includes sample count and observation window.
- Breach stops challenger traffic deterministically.
- Exact stable identity is restored within 300 seconds.
- Any stable-path interruption is measured.

## Failure and Rollback

Immediately stop challenger routing on missing identity, missing telemetry,
excess allocation, guardrail breach, or evaluator error. Apply only the approved
rollback artifact. Health without digest equality is failure.

## Experiment Boundary

This is an isolated operational canary over controlled replay traffic. It is
not a business A/B experiment: there are no real users, sticky cohorts, power
analysis, business KPIs, or production exposure. A Kubernetes production
canary remains blocked by the incomplete Scenario D exit, single-GPU admission,
and a separate maintenance approval.

## Interview Evidence

- Demonstrates: safe model delivery, routing guards and immutable rollback.
- Expected questions: shadow/canary/A-B differences; minimum samples; metric
  peeking; rollback identity; fail-open versus fail-closed.
- Claim allowed: local operational canary and rollback validation.
- Claim prohibited: business uplift or real-user experiment validity.
