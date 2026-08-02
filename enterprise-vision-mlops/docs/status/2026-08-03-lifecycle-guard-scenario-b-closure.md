# Scenario B Integrated Lifecycle Guard Closure

- Work item: `EVM-281` / Jira `SCRUM-189`
- Status: PASS
- Evidence source: `1e541de0422a2b4dbb9d83aca9672d0034de4067`
- Series: `scenario-b-lifecycle-20260802T230659Z-1e541de0`
- Scope: controlled local single-node VisA/CUDA release validation

## Accepted Runs

| Branch | Lifecycle run | MLflow | CT | Guard result |
|---|---|---|---|---|
| quality | `lifecycle-20260802T230714-5e948d45` | `c0d628e5e36340a6ad09c60ee49a6b72` | `ct-eval-22423ae443774a62` | `rejected_release` |
| runtime | `lifecycle-20260802T232208-c58e1e77` | `f1c3917053ac48e195d9936c300af0cb` | `ct-eval-7eb840ce1e712722` | `rolled_back` |

Both runs independently completed the real Airflow data DAG, Kubernetes CUDA
EfficientNet-B0 training, MLflow logging, artifact readiness and isolated CT
before reaching release admission. Each trained for four of twenty requested
epochs and stopped at the fixed accuracy rule. The deterministic model digest
was `226b99e7fefd4d5b2a679744a35a23e8cc6acaae3d5d892ea31a641bc8ee8739`.

Measured validation metrics were accuracy `0.962079`, F1 `0.823529` and AUROC
`0.973746`; training took `122.402 s` and `123.070 s`, with peak GPU memory
`2846.96 MiB`. Both isolated CT runs evaluated 2,181 records with zero training
overlap and passed all 18 checks. CT F1 was `0.807512` and AUROC `0.982720`.

## Quality Rejection

The quality policy fixed minimum F1 at `0.90` before run creation. The measured
candidate F1 `0.823529` produced `quality_f1_below_minimum`, zero challenger
assignment, `rejected_release`, approval HTTP 422 and deployment intent zero.
No metric or threshold was edited during execution.

## Runtime Containment

The runtime branch issued 1,000 stable replay requests and exactly 100
deterministic challenger assignments. Two isolated challenger errors produced
error rate `0.02`, above the fixed `0.01` limit. Assignment/response identity
matched `1,000 / 1,000`; challenger p95 latency was `16.3502 ms`.

The decision became `rolled_back` with `runtime_error_rate_exceeded`, detection
`0.016 s`, zero post-decision challenger allocation and exact stable-route
restoration `0.016 s`. The approval request returned HTTP 422 and no deployment
intent was created.

## Runtime And Evidence Closure

- Quality post-run restoration: `15.437 s`, exact UID/1/1/CUDA/plugin/source
  identity and two distinct Prometheus up scrapes.
- Runtime post-run restoration: `22.140 s` with the same checks.
- Final stable Deployment UID:
  `cfdab424-dcc5-4d5f-a46f-ae7530441ef4`.
- Final stable model digest:
  `abcb8504a36c1128d32021722cfedce6357fd73598a52f6c2a0d60aca9d9a27f`.
- Final state: active lifecycle runs 0, B0 1/1 CUDA, GPU allocatable 1,
  device-plugin 1/1, supervisor/worker/observer healthy, Prometheus up.
- Independent re-hash: five indexes, 95/95 artifacts matched; both operational
  reports passed live-proof validation with zero errors.
- Series index SHA-256:
  `cc652b048ea1f02105419f6c4ddc882efe8e974226e80125db4de3ea0755c988`.
- Quality replay index SHA-256:
  `b8105c93fd5c87c26e043ed90ce2c49bcb97b2f6148cce2591894a9df36040b4`.
- Runtime replay index SHA-256:
  `9a023b45cca6b7ad5f196da58cdcf9711d71aec2a0d1e2b9c4049fd691c66be8`.

Regression verification passed 492/492 Python tests and touched-file Ruff.

## RCA Retained

Three superseded attempts remain immutable evidence: Torch runtime discovery,
CT host/mount path resolution and post-CT Prometheus scrape convergence. Each
failed closed before release mutation, was followed by a reproducing test and
versioned correction, and was never counted as acceptance evidence.

## Claim Boundary

This proves two fresh, controlled local single-node lifecycle runs can reject a
measured quality breach and contain an isolated replay error breach before
release mutation while retaining exact stable CUDA serving identity. It does
not prove real-user traffic, business A/B, Kubernetes production canary, HA,
multi-node failover, uninterrupted service or an enterprise SLA.

