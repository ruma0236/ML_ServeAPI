# S8 Dependency Soak And Resource-efficiency Reconciliation

## Status

- Scenario: `S8` / `EVM-298` / `SCRUM-208`
- State: implementing; acceptance credit is zero until fresh experiments and hash closure pass
- Start revision: `c6973cae960e2e78f502b400ba104ce4faa42b9a`
- Authority: `docs/agenda/2026-08-15-distributed-scale-operational-validation-plan-v3.md`

## Frozen Acceptance Contract

| ID | Required proof | Fail-closed interpretation |
| --- | --- | --- |
| `S8-AC-01` | retry amplification remains within the declared budget | attempts, retries, backoff, jitter, circuit opens, DLQ, and unique effects must be independently recomputable |
| `S8-AC-02` | memory, file descriptor, pool, queue, and artifact slopes remain bounded | three independent 30-minute measurement windows must have finite samples, bounded slopes, terminal drain, and no cleanup residue |
| `S8-AC-03` | MTTR, request impact, efficiency Pareto, residual risk, and cleanup are recorded | latency, transient, timeout/worker-loss, retry-budget, and poison paths remain separate from the no-fault soak |
| `S8-AC-04` | one final evidence index re-hashes every accepted result | public Git blobs and private raw artifacts must both re-hash; a copied summary boolean is insufficient |

The soak target is frozen at `35 RPS`, derived from the accepted S3 sustainable
rate `49.977777777778 RPS * 0.70`. Each repetition uses a 60-second warmup,
1,800-second measurement, and 30-second cooldown. The accepted matrix requires
three independent repetitions. Fault runs are isolated from the soak and from
one another.

## Original Intent To Implemented Evidence

| Intent raised in the current task and prior scale plan | Implemented boundary | S8 reconciliation | Claim boundary |
| --- | --- | --- | --- |
| lightweight ML request concurrency, batching, and parallel handling | S2 durable bounded queue; S3 HIGGS CPU/API capacity; S4 Tiny MLP GPU batching | soak the accepted S3 operating point and re-hash S2-S4 accepted evidence | local single-node capacity only |
| large-data parallelism and memory bounds | S5 Spark executor and columnar engine evidence | include S5 accepted hashes and resource residual risk in the final index; do not rerun its accepted matrix | no distributed physical cluster claim |
| nearly uninterrupted API rollout and controlled GPU ownership change | S6 API rolling continuity and single-GPU handoff | report API continuity, in-flight drain, GPU interruption, and rollback as separate measurements | not production HA or node failover |
| image, VLM, and LLM family-aware admission and fairness | S7 family-specific admission, HOL, quality, and resource guards | include strict-v2 S7 provenance and accepted evidence in final closure | sequential single-GPU ownership, not concurrent residency |
| dependency degradation, long-run stability, and efficiency | S8 | add target-scoped fault profiles, circuit hold, 35 RPS soak, resource slopes, and final evidence index | controlled dependency and traffic only |
| lifecycle guards for data, artifact, drift, canary, rollback, GPU recovery, and incidents | EVM-271 through EVM-289 and A-E guard evidence | preserve these as baseline-only cross-references; S8 does not convert them into production claims | guard validation is separate from scale-soak acceptance |

## GPU Semantics

The verified system supports family-aware admission followed by exclusive use of
one CUDA device and controlled sequential handoff. It does not prove that
multiple image, VLM, or LLM models remain resident and execute concurrently on
the GPU. That experiment is outside V3 and remains a follow-up because the host
does not provide MIG isolation and the accepted S4/S7 contracts intentionally
serialize accelerator ownership.

S8 uses the accepted S4 GPU result as a hash-verified efficiency reference and
performs a fresh known-good CUDA readiness/cleanup check. It does not train or
serve another model concurrently with the production B0 workload.

## Continuity Semantics

| Measurement | Existing evidence | S8 treatment |
| --- | --- | --- |
| API request continuity | S6 external logical requests and attempts | retained as a separately hashed result |
| in-flight drain | S6 preflight and final rollout drain evidence | retained with its strict-v2 narrowed wording |
| GPU handoff interruption | S6 monotonic handoff timeline | reported as measured interruption, never as zero-downtime HA |
| rollback | S6 exact source identity restoration | revalidated by current serving identity and cleanup checks |

## Architecture Delta

Before, transient failures were retried with bounded attempts, backoff, jitter,
and a durable retry budget, but new durable claims continued while the dependency
was degraded. After, an opt-in worker-local dependency circuit opens after a
frozen consecutive-failure threshold, pauses new claims for a bounded hold, and
admits exactly one half-open probe. Success closes the circuit; another transient
failure reopens it. Lease/fencing, PostgreSQL authority, idempotency, DLQ, and
existing API response contracts remain unchanged.

The circuit is disabled by default in the accepted S2 profile. It is enabled only
by `configs/s8_dependency_soak_v3.toml`, providing a direct rollback to the prior
behavior without a schema migration.

## Experiment And Stop Contract

1. Revalidate S0-S7 status and source/evidence hashes.
2. Run no-fault, bounded latency, retry-budget, poison/DLQ, and timeout plus
   exact-worker-recovery profiles in isolated PostgreSQL schemas.
3. Require each profile to drain with accepted identity equal to one terminal
   identity, duplicate logical effects zero, complete trace attribution, and no
   process or schema residue.
4. Run the 35 RPS soak only after isolated faults pass and cleanup is proven.

## Failed-attempt Revisions

- Attempt 01 retained a pre-admission ephemeral Prometheus host-port race. It
  has zero acceptance credit; the shared isolated runtime now retries only
  explicit port-collision errors with a fresh port and three-attempt cap.
- Attempt 02 retained nine passing control/latency/transient repetitions, then
  rejected retry-budget repetition 1 because runner/config drift and a budget
  above circuit throughput produced five expiry outcomes instead of the
  intended DLQ closure. It also has zero acceptance credit.
- The v3 profile reads the frozen twelve transient plus four healthy request
  counts, limits the retry budget to eight per 60 seconds, and records
  monotonic fault-to-terminal elapsed time. A zero-credit v2 calibration
  observed 28 seconds of circuit hold and 40.109 seconds to terminal closure,
  so v3 freezes a 60-second MTTR guardrail before acceptance. Final acceptance
  restarts all 21 fault repetitions from this revision.
5. Stop on retry budget escape, unbounded slope, error or p99 guardrail breach,
   trace gap, identity ambiguity, or cleanup uncertainty.
6. Re-hash all accepted public and private artifacts before closure.

Historical `ContainerStatusUnknown` and old terminal Kubernetes objects are
recorded as unrelated local-cluster debt. They are not deleted, hidden, or counted
as S8 acceptance failures unless S8 creates or changes them.

## Portfolio Boundary

Allowed: a controlled single-local-node experiment showing bounded retries,
target-scoped dependency recovery, sustained lightweight CPU/API capacity,
resource trend measurement, deterministic cleanup, and evidence integrity.

Not allowed: customer production traffic, SLA, physical multi-node or multi-zone
HA/DR, multi-GPU scaling, simultaneous multi-model GPU execution, or a general
claim that all dependency failures have been covered.
