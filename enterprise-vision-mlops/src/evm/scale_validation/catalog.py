from __future__ import annotations

from typing import Any

from evm.scale_validation.contracts import SCENARIO_TITLES


SCENARIO_DEFINITIONS: dict[str, dict[str, Any]] = {
    "S0": {
        "engineering_question": (
            "Can every load result start from a healthy, reproducible runtime whose identity, "
            "metrics, and trace propagation are machine-verifiable?"
        ),
        "why_now": "No scale result is credible without a stable and attributable control case.",
        "observed_gap": (
            "Degraded readiness could return HTTP 200, and no strict benchmark closure contract "
            "requires identity, cross-layer traces, repeated metrics, and hashed evidence."
        ),
        "proposed_design": [
            "Return HTTP 503 whenever serving dependencies or the promoted model are not ready.",
            "Propagate W3C trace context through API, queue, worker, data, tracking, and serving.",
            "Keep metric labels bounded while exact identities remain in traces and structured logs.",
            "Capture three low-load controls with latency, throughput, resource, pool, and retry data.",
        ],
        "steps": [
            "Validate progress and benchmark contracts with focused unit tests.",
            "Reconcile serving, accelerator inference, monitoring, and supervisors without mutation.",
            "Verify trace propagation and run three independent low-load controls.",
        ],
        "acceptance": [
            "Only healthy active targets are included in the baseline.",
            "Exact source, data, model, and runtime identity is complete.",
            "p50, p95, p99, throughput, queue, pool, retry, CPU, RAM, GPU, and VRAM are queryable.",
            "Trace propagation spans every declared lifecycle stage with zero missing links.",
            "Three independent controls are comparable and variance is reported.",
        ],
        "before": "Health, metrics, and logs exist without one machine-enforced evidence closure.",
        "after": (
            "Readiness, bounded telemetry, distributed trace identity, and benchmark closure gate "
            "every later scenario."
        ),
        "next_action": "Complete S0 contracts and telemetry gap audit before any load execution.",
    },
    "S1": {
        "engineering_question": (
            "Can 100 to 500 concurrent mutations commit one legal and idempotent outcome?"
        ),
        "why_now": "Durable state correctness must precede retries and queued high-volume mutation.",
        "observed_gap": (
            "Transactional ownership, atomic claims, fencing, and bounded connection-pool wait are "
            "not yet proven under concurrency."
        ),
        "proposed_design": [
            "Use database transactions and unique idempotency keys for every state mutation.",
            "Enforce legal transitions, atomic lease claims, fencing, and stale-owner reconciliation.",
            "Bound connection-pool size and wait time and expose conflict outcomes as telemetry.",
        ],
        "steps": [
            "Exercise concurrent create, approve, cancel, and retry requests.",
            "Interrupt one exact owner and reconcile the stale lease.",
            "Count committed outcomes, illegal states, pool waits, and duplicate external effects.",
        ],
        "acceptance": [
            "Duplicate lifecycle, deployment, and artifact effects are zero.",
            "Conflicting mutations end in one legal terminal state.",
            "Pool exhaustion returns a bounded observable failure rather than hanging.",
            "Worker loss and retry preserve one committed outcome.",
        ],
        "before": "Control-plane state still relies partly on process-local or file-ledger ownership.",
        "after": "Transactions, idempotency, leases, and fencing own every concurrent transition.",
        "next_action": "Begin after S0 evidence contracts pass their focused tests.",
    },
    "S2": {
        "engineering_question": (
            "Does overload stay memory-bounded while accepted work reaches an explicit outcome?"
        ),
        "why_now": "A bounded queue is required before capacity and recovery experiments scale up.",
        "observed_gap": "Durable admission, byte/age bounds, retry budget, and DLQ are incomplete.",
        "proposed_design": [
            "Combine a durable queue with a bounded process-local async queue and semaphore.",
            "Bound depth, bytes, age, and time; reject excess work with Retry-After.",
            "Retry only transient failures with backoff, jitter, and a global budget; isolate poison work.",
            "Scale CPU workers from queue telemetry while keeping the GPU worker count at one.",
        ],
        "steps": [
            "Use provisional safe bounds before S3, then recalibrate from measured service rate.",
            "Inject burst, sustained, duplicate, expired, and poison requests independently.",
            "Measure queue age, bytes, RSS, terminal closure, rejection, retry, and DLQ behavior.",
        ],
        "acceptance": [
            "Queue depth, in-flight bytes, and process memory remain bounded under overload.",
            "Accepted work completes or reaches one explicit terminal failure.",
            "Duplicate effects are zero and poison work does not block healthy work.",
            "Over-capacity demand is rejected with an observable retry contract.",
        ],
        "before": "Admission and retries do not share one end-to-end resource boundary.",
        "after": "Durable queue, local semaphore, rejection, retry, DLQ, and CPU scale are bounded.",
        "next_action": "Begin after S1 has protected mutation idempotency.",
    },
    "S3": {
        "engineering_question": (
            "Where are sustainable capacity, tail-latency limits, and the first CPU bottleneck?"
        ),
        "why_now": "Lightweight probes separate infrastructure overhead from model compute.",
        "observed_gap": "No repeated CPU-model capacity envelope or saturation knee exists.",
        "proposed_design": [
            "Use one governed high-volume tabular corpus across multiple lightweight CPU probes.",
            "Run closed concurrency and open arrival-rate sweeps with fixed corpus, split, and seed.",
            "Compare API replicas and CPU worker counts while tracing validation and prediction stages.",
            "Measure co-located load-generator consumption separately from the system under test.",
        ],
        "steps": [
            "Warm each probe and run three repetitions at every safe load step.",
            "Calculate p95, p99, throughput, errors, resources, and the first telemetry slope change.",
            "Select an operating point below maximum throughput and feed it back into S2 bounds.",
        ],
        "acceptance": [
            "Every probe has p95, p99, throughput, error, and resource curves.",
            "The first bottleneck is explained by trace and resource telemetry.",
            "The sustainable operating point is explicitly lower than peak throughput when required.",
            "Three independent repetitions and their variance are retained.",
        ],
        "before": "API behavior is functional but not characterized with low-compute probes.",
        "after": "A measured CPU/API capacity envelope supplies operational and queue limits.",
        "next_action": "Begin after S0, S1, and provisional S2 safety gates are in place.",
    },
    "S4": {
        "engineering_question": (
            "Which small-model batch and queue-delay settings maximize throughput within p99 and VRAM?"
        ),
        "why_now": "A lightweight GPU probe reveals scheduler and batching cost without a large model.",
        "observed_gap": "No throughput-latency-VRAM Pareto curve exists for the accelerator path.",
        "proposed_design": [
            "Sweep batches 1, 4, 8, 16, and 32 with bounded queue delays.",
            "Keep one model instance by default and vary instance count separately from batch size.",
            "Record allocated, reserved, and peak VRAM, utilization, and formed batch size.",
            "Run training under a separate exclusive lease, never during inference benchmarking.",
        ],
        "steps": [
            "Establish an exclusive-accelerator batch-one baseline.",
            "Sweep safe batch and delay combinations and retain all failed/OOM attempts.",
            "Select one operating point and recalculate S2 queue bounds.",
        ],
        "acceptance": [
            "Throughput-p99 and throughput-peak-VRAM Pareto curves exist.",
            "The selected operating point has zero accelerator out-of-memory failures.",
            "Model instance count and batch size effects are measured separately.",
            "Queue limits are recalculated from the selected service rate.",
        ],
        "before": "The accelerator path runs models but lacks a controlled batching envelope.",
        "after": "One measured batch, delay, instance, and VRAM operating point governs inference.",
        "next_action": "Begin after the common baseline and bounded queue instrumentation are ready.",
    },
    "S5": {
        "engineering_question": (
            "Can larger partitioned data remain memory-bounded, deterministic, and restartable?"
        ),
        "why_now": "Single-process data preparation does not demonstrate executor or shuffle behavior.",
        "observed_gap": "Partition sizing, spill, skew, retry, and idempotent distributed commits are unproven.",
        "proposed_design": [
            "Compare single-process columnar processing, local Spark, and Kubernetes executors.",
            "Increase a governed tabular subset through staged sizes with fixed manifests.",
            "Tune executor memory, partitions, shuffle, and adaptive execution under explicit bounds.",
            "Inject skew and one executor loss, then verify idempotent output commit and digest closure.",
        ],
        "steps": [
            "Verify semantic and schema correctness on the smallest governed subset.",
            "Advance through staged corpus sizes only after memory and integrity gates pass.",
            "Compare throughput, memory, garbage collection, shuffle, spill, skew, and retry.",
        ],
        "acceptance": [
            "Records per second, storage rate, peak executor memory, GC, shuffle, spill, and skew are reported.",
            "Output contains zero missing and zero duplicate records.",
            "Retry preserves row count and output digest.",
            "Generated load volume is not represented as new semantic diversity.",
        ],
        "before": "Data processing is reproducible at local scale but remains mostly process-local.",
        "after": "Spark executors process governed partitions with bounded memory and deterministic commits.",
        "next_action": "Begin after S1 and S2 protect ownership, retry, and output idempotency.",
    },
    "S6": {
        "engineering_question": (
            "Can API replicas roll continuously while a single-GPU handoff remains measured and reversible?"
        ),
        "why_now": "API continuity and GPU availability are different failure domains and claims.",
        "observed_gap": "Rolling API drain and controlled single-GPU switch are not proven under load.",
        "proposed_design": [
            "Run two or three stateless API replicas with zero-unavailable rolling replacement.",
            "Use readiness, pre-stop drain, termination grace, and target-scoped Pod termination.",
            "Gate GPU candidates by shadow quality, identity, and tail latency before queue drain and switch.",
            "Measure endpoint interruption and exact known-good rollback identity separately.",
        ],
        "steps": [
            "Hold traffic near the verified safe operating point.",
            "Replace one API replica and measure accepted request completion and drain.",
            "Run a controlled GPU candidate handoff only after non-disruptive gates pass.",
        ],
        "acceptance": [
            "API rolling update loses and duplicates zero accepted requests.",
            "API drain and replacement recovery time are measured.",
            "GPU handoff interruption and rollback identity are measured.",
            "The result is not described as zero-downtime GPU high availability.",
        ],
        "before": "Deployment works but API continuity and single-GPU interruption are conflated.",
        "after": "Stateless API continuity and controlled GPU handoff have separate evidence and claims.",
        "next_action": "Begin after S3 capacity, S4 GPU bounds, and S2 queue recalibration pass.",
    },
    "S7": {
        "engineering_question": (
            "Do image and generative workloads use model-family-specific cost admission and metrics?"
        ),
        "why_now": "Tabular capacity does not cover image decode, pixels, tokens, or long requests.",
        "observed_gap": "Image, token, and in-flight cost bounds are not uniformly enforced or measured.",
        "proposed_design": [
            "Measure image size, decode, preprocessing, batch, and pixel admission costs.",
            "Bound input tokens, output tokens, and total in-flight tokens for language workloads.",
            "Measure first-token, per-token, queue, fairness, and peak-memory behavior.",
            "Run model families sequentially on one GPU and use quantization only with runtime proof.",
        ],
        "steps": [
            "Replay fixed short/long and small/large request mixes for each family.",
            "Increase admitted request cost only until the safe fairness and memory boundary is known.",
            "Confirm unsupported metrics remain absent from evidence and release gates.",
        ],
        "acceptance": [
            "Each model family has distinct p95, p99, and quality metric schemas.",
            "Selected admission limits have zero OOM and zero starvation.",
            "Long-request head-of-line behavior and fairness are measured.",
            "Unsupported or unverified metrics remain absent.",
        ],
        "before": "Multiple model families run without one cost-aware admission proof.",
        "after": "Image, pixel, token, and in-flight budgets govern family-specific queues and metrics.",
        "next_action": "Begin after S2 bounds and S4 accelerator operating point are verified.",
    },
    "S8": {
        "engineering_question": (
            "Do bounded retries and selected operating points remain stable during faults and soak?"
        ),
        "why_now": "Closure requires long-run resource trends and dependency recovery after isolated passes.",
        "observed_gap": "Retry amplification, resource slope, efficiency, and final re-hash are unproven.",
        "proposed_design": [
            "Inject service-scoped latency or timeout with bounded proxy or deterministic fixtures.",
            "Enforce retry budget, exponential backoff, jitter, circuit hold, and queue drain.",
            "Run a 30 to 60 minute soak near the selected safe operating point.",
            "Calculate CPU/GPU time efficiency and re-hash every accepted evidence artifact.",
        ],
        "steps": [
            "Validate one dependency fault at a time and prove cleanup before the next.",
            "Run the cross-layer soak only after every isolated scenario has passed.",
            "Measure amplification, recovery, resource slopes, efficiency, integrity, and residual risk.",
        ],
        "acceptance": [
            "Retry amplification stays within the declared budget.",
            "Memory, file descriptor, pool, queue, and artifact slopes remain bounded.",
            "MTTR, request impact, efficiency Pareto, and residual risk are recorded.",
            "One final evidence index re-hashes every accepted result.",
        ],
        "before": "Isolated guards exist without a distributed-scale soak and efficiency closure.",
        "after": "Dependency faults, sustained load, cleanup, efficiency, and hashes close one ledger.",
        "next_action": "Begin only after S0 through S7 have accepted evidence and clean cleanup.",
    },
}


def validate_catalog() -> None:
    if list(SCENARIO_DEFINITIONS) != list(SCENARIO_TITLES):
        raise ValueError("scenario catalog must preserve authoritative S0 through S8 order")
