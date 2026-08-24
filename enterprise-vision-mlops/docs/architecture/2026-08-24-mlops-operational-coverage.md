# MLOps Operational Coverage

Date: 2026-08-24

Canonical V3 closure: `80a56e501cf46359a8de908fc39dc3c02a642fc1`

This matrix is a claim inventory, not a feature checklist. `verified` means the
named local contract has hash-linked runtime evidence. It never upgrades local
evidence into a production, SLA, HA, DR, security, or compliance claim.

Allowed status values are `verified`, `partial`, `in_progress`, `planned`, and
`not_evidenced`.

| Coverage area | Status | Current evidence | Gap or V4 action | Claim boundary |
|---|---|---|---|---|
| Operating contract and SLO | partial | S0-S8 freeze local SLI thresholds and fail-closed acceptance | Define V4 per-model SLI and recovery targets; no business SLO | Local engineering thresholds only |
| Execution-environment reproducibility | partial | Compose, Kubernetes manifests, config digests, runtime and GPU identity are captured | E0 binds WSL2, Docker, CUDA, Triton image, profiler, and cleanup identities | One named host and GPU; no fleet reproducibility |
| Git and artifact provenance | verified | S0-S8 canonical Git-byte and private inventory rehash | Revalidate every V4 runtime/config/model/data/evidence edge | Local evidence chain only |
| CI and regression | partial | Focused, real-PostgreSQL, lifecycle, Control Panel, frontend, and evidence gates are recorded | Add V4 contract, mutation, Triton, profiler, and integrated regression gates | Local and repository CI evidence; no production release policy claim |
| Data quality and lineage | verified | EVM-271 through EVM-289 and lifecycle guard evidence bind data, split, lineage, and artifact identities | Revalidate model-family inputs at the V4 router boundary | Governed project data only |
| Large-scale Spark processing | verified | S5 accepted 30 local single-node Spark/columnar points and strict reclosure | Retain; run only integration regression unless V4 changes its boundary | Single physical node, not a distributed cluster claim |
| Training and retraining | partial | Classification, VLM, LLM training/adaptation and guarded candidate workflows exist | V4 focuses serving; end-to-end retraining remains a separate backlog | No continuous production retraining claim |
| Model quality and drift | partial | EVM-274/275 and scenario C create review/hold behavior and candidate gates | Revalidate route identity and hold semantics; long-term drift remains untested | Controlled fixtures and local runs |
| Registry and promotion | verified | MLflow identity, approval, staging, promotion and rollback guards have local evidence | S6B-M adds Triton repository/version identity and route switch evidence | Local registry and promotion workflow |
| API idempotency | verified | S1 proves transactional idempotency and one-time effects under external concurrency | Revalidate through V4 router and model lifecycle transitions | Local PostgreSQL and controlled traffic |
| Bounded queue and backpressure | verified | S2 proves depth/byte bounds, 413/429, Retry-After, terminal closure, and cleanup | Extend to per-model outstanding work and mixed-model admission | One local queue and worker topology |
| Retry, timeout, and DLQ | verified | S2 and S8 prove bounded retry budgets, timeout disposition, DLQ, and dependency recovery | Revalidate against Triton/model/dependency faults in integrated V4 | Controlled dependency faults only |
| Worker lease and fencing | verified | S1/S2 prove lease epoch, stale-owner fencing, and worker recovery | Extend ownership to Triton model load/unload and route activation | Single control-plane database |
| API and CPU capacity | verified | S3 records closed/open sweeps, p95/p99, resource curves, and selected operating point | X1 compares API replicas 1/2 and CPU workers 1/2/4 | Single host; load generator cost remains visible |
| GPU batching | verified | S4 records batch/delay trade-offs on one Tiny MLP and one GPU | X1 extends batching per model; S4 is not multi-model evidence | One model at a time in V3 |
| Family fairness and HOL | verified | S7 records image/VLM/LLM admission, intentional rejection, and selected/admitted starvation zero | X1 revalidates fairness with concurrent heterogeneous models | Sequential family execution in V3 |
| API rolling continuity | verified | S6 records three two-replica API rollouts, request continuity, identity, and rollback | Integrated V4 revalidates when the router and Triton dependency are present | Local Kubernetes continuity, not HA |
| GPU controlled handoff | verified | S6 records exclusive source/target handoff, interruption, and exact rollback | S6B-M extends this to model Blue/Green inside one Triton GPU Pod | Controlled handoff, not zero-downtime GPU HA |
| Triton model Blue/Green | planned | No V3 acceptance evidence | S6B-M: warmup, canary, switch, drain, unload, rollback, digest fail-closed | One Triton GPU Pod |
| Heterogeneous-model concurrency | planned | V3 families are serialized behind an exclusive GPU lease | X1: four governed lightweight models, solo/serial/concurrent/per-model batching | One physical GPU; no MPS/MIG/multi-GPU |
| CUDA kernel overlap | not_evidenced | Request overlap and GPU utilization do not prove kernel overlap | Claim only after Nsight/CUPTI reports nonzero interval overlap | No overlap claim until profiler proof exists |
| Kubernetes lifecycle | partial | Jobs, Deployments, probes, rollout, cleanup, and local device-plugin evidence exist | Add Triton Pod/model lifecycle and exact orphan checks | Docker Desktop single-node cluster |
| Dependency fault and recovery | verified | S8 records 21 isolated fault repetitions and bounded recovery | Integrated V4 adds router, Triton, model, and telemetry dependency faults | Controlled deterministic faults |
| Soak and resource leak | verified | S8 records three independent 30-minute soaks and bounded slopes | Integrated V4 repeats soak at X1-derived load, never inherited 35 RPS | 90 minutes total accepted local soak, not long-term production endurance |
| Observability | verified | Prometheus, structured evidence, W3C traces, queue/resource metrics, and identity checks exist | Add Triton model metrics, per-model queueing, profiler correlation, and fault causality | Local telemetry topology |
| Incident and RCA | verified | EVM-271 through EVM-289 and S0-S8 retain failed attempts, RCA, recovery, and cleanup | V4 keeps zero-credit failures in an append-only hash chain | Engineering incident drills only |
| Security | partial | Some container security context, namespace, secret, and approval boundaries exist | Tenant authentication, threat testing, and isolation remain backlog | No security-assurance claim |
| Privacy and compliance | not_evidenced | Dataset provenance and license restrictions are recorded | No privacy, regulatory, or compliance audit in V4 | ScienceQA remains non-commercial research/portfolio evidence |
| Cost and FinOps | not_evidenced | CPU/GPU-seconds and throughput efficiency exist without monetary allocation | Keep efficiency metrics; cost attribution remains backlog | No cloud-cost optimization claim |
| Autoscaling | not_evidenced | Bounded CPU worker hysteresis is not infrastructure autoscaling | Separate backlog after stable multi-node environment exists | No HPA/KEDA production claim |
| Backup and restore | not_evidenced | JSON mirror reconciliation is not a backup/restore drill | Separate database/artifact restore exercise | No backup, RPO, or RTO claim |
| HA and DR | not_evidenced | API continuity and GPU handoff occur on one physical node | Requires independent nodes, durable state failover, and network/zone faults | Explicitly outside V4 |
| Cleanup and decommission | verified | S0-S8 enforce process, container, queue, lease, target, GPU, and evidence cleanup | V4 adds Triton repository/model unload and profiler residue checks | Scoped local resources only |

## Coverage Interpretation

V3 established a broad local MLOps and systems-engineering baseline. V4 is not a
replacement for V3. It targets four missing claims: a reproducible Triton GPU
runtime, model-level Blue/Green lifecycle, heterogeneous-model contention, and
an integrated fault/soak closure. Security, privacy/compliance, FinOps,
autoscaling, backup/restore, and HA/DR remain deliberately unverified backlog.
