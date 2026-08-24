# V3 To V4 Crosswalk

Date: 2026-08-24

V3 closure: `80a56e501cf46359a8de908fc39dc3c02a642fc1`

V4 extends the existing ML Serve system in place. V3 evidence remains immutable.
Any V3 boundary affected by Triton, routing, or model lifecycle must be
revalidated or extended; it cannot be marked retained without a current-runtime
check.

| V3 ID and claim | V3 evidence/status | V4 disposition | Changed dependency | V4 work item | Rerun and reason | Planned V4 evidence |
|---|---|---|---|---|---|---|
| S0 runtime/evidence identity | Verified | revalidated | Triton image, model repository, GPU profiler, and router identities join the chain | E0 and integrated V4 | Yes; topology and provenance edges change | E0 identity manifest, profiler capability, current-runtime smoke |
| S1 transactional state/idempotency | Verified | revalidated | Model load/unload and route switch become one-time effects | S6B-M and integrated V4 | Focused rerun; no need to repeat 100/250/500 unless contract changes | Route/model effect reservations, duplicate-effect mutation proof |
| S2 bounded queue/backpressure | Verified | extended | Per-model queue, outstanding GPU work, and mixed-family budget | X1 and integrated V4 | Yes; queue accounting and service rates change | Per-model depth/bytes/tokens, 429/DLQ/terminal identity closure |
| S3 API/CPU capacity | Verified | revalidated | Router plus Triton dependency; API replica 1/2 and CPU worker 1/2/4 axes | X1 | Yes; current service path differs | Closed/open calibration, resource curves, co-located load-generator cost |
| S4 Tiny MLP GPU batching | Verified | extended | Triton dynamic/per-model batching and four model families | X1 | Yes; one-model result cannot prove mixed behavior | Solo/serial/concurrent/per-model batch matrix and Pareto analysis |
| S5 Spark data scale | Verified | retained | No Triton or router dependency in accepted Spark path | Integrated regression only | No matrix rerun unless source or contract changes | Current-head digest/cleanup regression |
| S6 API rolling continuity | Verified | revalidated | API now routes to Triton and per-model versions | Integrated V4 | Yes; dependency graph changes | Exact request/trace, old/new Pod/image, drain and retry evidence |
| S6 GPU controlled handoff | Verified | replaced | Process-level exclusive handoff becomes model Blue/Green inside one Triton Pod | S6B-M | Yes; new model lifecycle contract | Warmup, canary, switch, drain, unload, rollback, digest mismatch evidence |
| S7 family admission/fairness | Verified | extended | Families may be concurrently resident and contending instead of serialized | X1 | Yes; fairness and HOL semantics change | Per-family arrivals/admission/service/terminal records and profiler correlation |
| S8 dependency soak/resource closure | Verified | replaced | Router, Triton, model repository, per-model queues, and telemetry add dependencies | Integrated V4 | Yes; V3 35 RPS is prohibited as an inherited V4 load | X1-derived balanced/hot load, fault matrix, three soaks, cleanup index |
| EVM-271..283 lifecycle guards | Verified local guard evidence | revalidated | Model/version/router identity and Triton faults cross existing gates | Integrated V4 | Focused guard and mutation rerun | Admission/quality/integrity/approval/rollback/fault causality evidence |
| EVM-284 final operations drill | Planned | retained | Remains a separate operator drill | Separate backlog | No V4 acceptance credit | Future operator-run evidence |
| EVM-285 full lifecycle guard ledger | Verified | revalidated | New serving and routing transition points | Integrated V4 | Yes; targeted guard regression, not full historical rerun by default | Fresh transition-point guard outcomes and exact cleanup |
| EVM-286 generic workload contract | Verified | extended | Triton model repository and per-model serving identity | E0, S6B-M, X1 | Yes | Exact model/config/data/runtime identity and readiness payloads |
| EVM-287 image-text lifecycle | Verified | extended | Compact VLM becomes one X1 governed model | X1 | Yes; runtime and concurrency boundary changes | License/provenance, quality floor, queue/VRAM/latency evidence |
| EVM-288 text lifecycle | Verified | extended | Compact 4-bit LLM becomes one X1 governed model | X1 | Yes; loaded quantization and concurrency must be re-proven | Exact loaded dtype/quantization, token budgets, TTFT/TPOT evidence |
| EVM-289 cross-family observability | Verified | extended | Mixed concurrent model routing and Triton telemetry | X1 and integrated V4 | Yes | Per-model Prometheus, trace, route, lease, and cleanup closure |
| Triton environment baseline | None | new | WSL2/Docker/CUDA/Triton/Nsight | E0 | Yes | Three reproducible preflights and cleanup proofs |
| Triton model Blue/Green | None | new | One Triton GPU Pod and two governed model versions | S6B-M | Yes | Three switch/rollback runs plus digest-mismatch fail-closed runs |
| Heterogeneous-model concurrency | None | new | Four lightweight models sharing one GPU without MIG/MPS | X1 | Yes | Calibrations, matrix, fairness, Pareto, and resource evidence |
| CUDA kernel overlap | None | new | Nsight Systems or CUPTI timeline | X1 | Yes; claim disabled if profiler proof is absent | Nonzero overlap intervals tied to request/model trace identity |

## V3 Evidence Preservation

V3 public and private artifacts remain at their existing paths and hashes. V4
may reference them as prerequisite evidence but never edits, moves, or grants
new acceptance credit to them. A changed source boundary requires fresh V4
evidence even when the underlying V3 scenario remains verified.
