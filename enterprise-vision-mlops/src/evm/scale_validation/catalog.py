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
            "Capture three fixed-window low-load controls with latency, throughput, resource, load-generator permit-wait, and retry data.",
        ],
        "steps": [
            "Validate progress and benchmark contracts with focused unit tests.",
            "Reconcile serving, accelerator inference, monitoring, and supervisors without mutation.",
            "Verify trace propagation and run three independent low-load controls.",
        ],
        "acceptance": [
            "Only healthy active targets are included in the baseline.",
            "Exact source, data, model, and runtime identity is complete.",
            "p50, p95, p99, fixed-window throughput, queue, load-generator permit wait, retry, CPU, RAM, GPU, and VRAM are queryable.",
            "Trace propagation spans every declared lifecycle stage with zero missing links.",
            "Three independent controls are comparable and variance is reported.",
        ],
        "before": "Health, metrics, and logs exist without one machine-enforced evidence closure.",
        "after": (
            "Readiness, bounded telemetry, distributed trace identity, and benchmark closure gate "
            "every later scenario."
        ),
        "next_action": (
            "Revision-align the active serving runtime, then execute one full cross-runtime "
            "lifecycle trace and three independent low-load controls with hashed evidence."
        ),
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


SCENARIO_IN_PLACE_CONTRACTS: dict[str, dict[str, Any]] = {
    "S0": {
        "existing_system_baseline": (
            "The existing control plane uses a FastAPI API, file-ledger task assignments, a "
            "supervised lifecycle worker, Compose Airflow and MLflow services, Kubernetes model "
            "serving, and Prometheus. Health and metrics exist, but one exported cross-runtime "
            "trace and machine-enforced evidence closure do not yet cover that real path."
        ),
        "affected_components": [
            {
                "component": "Existing API and serving request boundaries",
                "files": [
                    "apps/api/main.py",
                    "apps/api/efficientnet_serving.py",
                    "src/evm/model_runtime/serving.py",
                ],
            },
            {
                "component": "Existing queue and lifecycle execution boundaries",
                "files": [
                    "src/evm/control_panel/operations.py",
                    "src/evm/control_panel/lifecycle_runs.py",
                    "src/evm/control_panel/lifecycle_orchestrator.py",
                    "src/evm/control_panel/lifecycle_worker.py",
                ],
            },
            {
                "component": "Existing pipeline and tracking clients",
                "files": [
                    "src/evm/core/config.py",
                    "src/evm/core/pipeline.py",
                    "src/evm/core/http.py",
                    "src/evm/core/mlflow_client.py",
                    "orchestration/airflow/dags/enterprise_vision_mlops_daily.py",
                    "src/evm/pipelines/spark_runtime_probe/run.py",
                ],
            },
            {
                "component": "Existing local runtime and monitoring stack",
                "files": [
                    "docker-compose.yml",
                    "monitoring/prometheus/prometheus.yml",
                    "src/evm/control_panel/kubernetes_observer.py",
                    "scripts/dev/start_kubernetes_observer.ps1",
                ],
            },
            {
                "component": "Scale-validation public contracts",
                "files": [
                    "src/evm/scale_validation/contracts.py",
                    "contracts/distributed-scale/scenario-progress.schema.json",
                    "contracts/distributed-scale/benchmark-evidence.schema.json",
                ],
            },
        ],
        "selection_reasons": [
            "Instrument the existing runtime boundaries so later load results are attributable to the system under test.",
            "Keep high-cardinality run identity in traces and logs while Prometheus labels remain bounded.",
        ],
        "alternatives": [
            "Full automatic instrumentation would reduce manual span code but obscures domain handoff boundaries and adds a larger dependency surface.",
            "A hosted trace backend would improve exploration but adds external state; a local Collector and immutable file evidence fit the current single-node scope.",
        ],
        "compatibility": [
            "Tracing is environment-gated and additive; existing API payloads and legacy trace identifiers remain readable.",
            "No trace or run identifier is introduced as a Prometheus label.",
            "An intentionally scaled-to-zero B0 deployment is absent from active scrape discovery rather than reported as a false outage.",
        ],
        "migration": [
            "Rebuild the existing API, Airflow, and serving images and restart the supervised worker after the source revision is committed.",
            "Historical artifacts remain historical evidence and cannot satisfy fresh S0 acceptance.",
        ],
        "preconditions": [
            "Existing control services, one promoted serving target, monitoring, worker, and observer are healthy and revision-aligned.",
            "Source, data, model, runtime, and load-profile identities are immutable and public-safe evidence roots are writable.",
        ],
        "workload_input": "Three independent controls, each pacing three real CUDA requests across a declared 60-second fixed-rate window through the existing lifecycle and serving path.",
        "controlled_variables": [
            "Source, data, model, runtime, seed-applied test-sample sequence, concurrency, warmup, and fixed measurement window.",
            "No concurrent training, deployment, or unrelated background load during each control repetition.",
        ],
        "signals": [
            "Readiness status, W3C trace stages, latency quantiles, fixed-window throughput, queue age/depth, worker activity, load-generator permit wait, retry count, CPU/RAM/GPU/VRAM.",
            "Focused regression tests and the existing end-to-end lifecycle regression result.",
        ],
        "stop_conditions": [
            "Any active dependency is unhealthy, identity is ambiguous, trace linkage is missing, or metric labels become unbounded.",
            "Unexpected production-like mutation, accelerator OOM, or dirty cleanup state is observed.",
        ],
        "recovery_conditions": [
            "All existing services return to the pre-run identity and health state and temporary work is removed.",
            "A failed repetition is retained with RCA and is never replaced by an unlinked rerun.",
        ],
    },
    "S1": {
        "existing_system_baseline": "The existing control plane serializes file-ledger writes and uses run claims and side-effect ledgers, but concurrent durable state transitions are not owned by one transactional database contract.",
        "affected_components": [
            {"component": "Lifecycle and task state", "files": ["src/evm/control_panel/lifecycle_runs.py", "src/evm/control_panel/operations.py"]},
            {"component": "Worker ownership", "files": ["src/evm/control_panel/lifecycle_worker.py", "src/evm/operations/scenario_d_supervision.py"]},
            {
                "component": "Control API",
                "files": [
                    "apps/api/main.py",
                    "apps/api/control_panel_lifecycle.py",
                    "apps/api/control_panel_tasks.py",
                    "apps/api/control_panel_deployments.py",
                ],
            },
        ],
        "selection_reasons": ["Durable atomic ownership must precede retry and overload experiments."],
        "alternatives": ["Extending file locks is simpler but cannot safely coordinate multiple replicas; SQLite improves transactions but not the intended multi-process pool behavior."],
        "compatibility": ["Existing read contracts and identifiers must remain stable while writes migrate behind a repository boundary."],
        "migration": ["Schema migration and dual-read verification must complete before file-ledger writes are retired."],
        "preconditions": ["S0 identity, trace, health, and evidence contracts pass."],
        "workload_input": "Concurrent create, approve, cancel, retry, and stale-owner mutation requests against existing lifecycle jobs.",
        "controlled_variables": ["Concurrency level, idempotency-key reuse, pool size, lock wait, owner loss point, and retry count."],
        "signals": ["Committed state, transition conflicts, duplicate effects, lease/fencing identity, pool wait, timeout, and trace correlation."],
        "stop_conditions": ["An illegal transition, duplicate external effect, unbounded pool wait, or ambiguous owner is observed."],
        "recovery_conditions": ["One legal committed outcome remains and stale ownership is reconciled without duplicate effects."],
    },
    "S2": {
        "existing_system_baseline": "The existing worker polls lifecycle files and dispatches task assignments, but admission, bytes, age, retries, and poison-work isolation do not share one bounded durable queue contract.",
        "affected_components": [
            {"component": "Task admission and dispatch", "files": ["src/evm/control_panel/operations.py", "src/evm/control_panel/lifecycle_worker.py"]},
            {"component": "Control API", "files": ["src/evm/control_panel/router.py", "apps/api/main.py"]},
            {"component": "Operational telemetry", "files": ["monitoring/prometheus/prometheus.yml", "src/evm/operations/metrics.py"]},
        ],
        "selection_reasons": ["Overload must fail explicitly before high-volume capacity tests begin."],
        "alternatives": ["An unbounded broker is operationally easy to start but moves memory risk downstream; process-local queues alone lose durable ownership on restart."],
        "compatibility": ["Existing task payload and lifecycle state-machine contracts must remain valid."],
        "migration": ["Introduce bounded admission before redirecting existing producers and retain a rollback path to the prior dispatcher."],
        "preconditions": ["S1 commits one idempotent task outcome and S0 telemetry is queryable."],
        "workload_input": "Independent burst, sustained, duplicate, expired, and poison task streams through the existing assignment endpoint.",
        "controlled_variables": ["Arrival rate, queue depth/bytes/age, worker count, timeout, retry budget, and poison ratio."],
        "signals": ["Admission status, Retry-After, queue depth/bytes/age, RSS, wait time, retry amplification, DLQ, and terminal closure."],
        "stop_conditions": ["RSS or in-flight bytes exceed the bound, accepted work loses terminal closure, or duplicates appear."],
        "recovery_conditions": ["Queue drains to zero, poison work is quarantined, workers are healthy, and no duplicate side effect remains."],
    },
    "S3": {
        "existing_system_baseline": "The existing API can execute model inference and expose metrics, but it has no governed high-volume tabular corpus or repeated CPU/API saturation envelope.",
        "affected_components": [
            {"component": "Existing scenario intake and execution", "files": ["src/evm/control_panel/scenario_workloads.py", "src/evm/model_runtime/workload_runner.py"]},
            {"component": "Existing online API and metrics", "files": ["apps/api/main.py", "src/evm/operations/metrics.py"]},
        ],
        "selection_reasons": ["Lightweight models expose API, serialization, queue, and CPU limits without model compute dominating the result."],
        "alternatives": ["A heavy vision or generative model is more domain-specific but hides the first systems bottleneck behind accelerator compute."],
        "compatibility": ["The existing workload registry and metric projection remain the control-plane entry point."],
        "migration": ["Register governed tabular profiles without changing existing image or generative workload contracts."],
        "preconditions": ["S0 passes and S1/S2 provide idempotent bounded execution."],
        "workload_input": "A fixed public high-volume tabular split replayed across lightweight CPU estimators.",
        "controlled_variables": ["Model, split, seed, arrival model, concurrency, API replicas, CPU workers, warmup, and duration."],
        "signals": ["RPS, p50/p95/p99, errors, queue wait, validation/transform/predict spans, CPU, RAM, and load-generator cost."],
        "stop_conditions": ["Error or latency guardrail is crossed, queue no longer drains, or resource saturation risks the host."],
        "recovery_conditions": ["Load stops, queues drain, replicas return to baseline, and each repetition has complete evidence."],
    },
    "S4": {
        "existing_system_baseline": "The existing single-accelerator path supports training and serving with an exclusive lease, but dynamic batching, queue delay, and VRAM capacity have no measured operating envelope.",
        "affected_components": [
            {"component": "Existing serving runtime", "files": ["apps/api/efficientnet_serving.py", "src/evm/control_panel/lifecycle_kubernetes.py"]},
            {"component": "Existing accelerator workload control", "files": ["src/evm/control_panel/scenario_workload_control.py", "src/evm/model_runtime/workload_runner.py"]},
        ],
        "selection_reasons": ["A tiny MLP isolates scheduler and batch formation cost within the available single-GPU boundary."],
        "alternatives": ["Multiple concurrent training jobs were excluded because the hardware cannot provide trustworthy isolation without MIG."],
        "compatibility": ["Existing exclusive training lease and serving identity gates remain authoritative."],
        "migration": ["Add batching as an opt-in serving profile and keep batch-one behavior as rollback."],
        "preconditions": ["S0 telemetry and S2 bounds pass; no training or unrelated GPU workload is active."],
        "workload_input": "Fixed tabular samples served by one tiny GPU MLP under a batch and delay matrix.",
        "controlled_variables": ["Batch size, bounded queue delay, model instance count, arrival rate, seed, and warmup."],
        "signals": ["Throughput, p95/p99, formed batch size, queue delay, GPU utilization, allocated/reserved/peak VRAM, and OOM count."],
        "stop_conditions": ["Any OOM, lease conflict, thermal risk, unbounded queue, or p99 stop threshold occurs."],
        "recovery_conditions": ["The batch-one known-good profile is restored and accelerator memory returns to baseline."],
    },
    "S5": {
        "existing_system_baseline": "The existing Airflow data path uses deterministic Python and columnar processing, but distributed executor, shuffle, spill, skew, and retry behavior are not implemented or evidenced.",
        "affected_components": [
            {"component": "Existing data pipeline", "files": ["src/evm/core/pipeline.py", "orchestration/airflow/dags/enterprise_vision_mlops_daily.py"]},
            {"component": "Existing Kubernetes job scaffold", "files": ["infra/kubernetes/local/pipeline-job.yaml", "src/evm/control_panel/kubernetes_task_executor.py"]},
        ],
        "selection_reasons": ["Spark provides executor, partition, shuffle, spill, skew, and retry controls that the current single-process path lacks."],
        "alternatives": ["Flink is stronger for streaming but adds a second execution model before batch-scale correctness is established."],
        "compatibility": ["Existing manifests, lineage digests, and Airflow handoff remain authoritative inputs and outputs."],
        "migration": ["Run single-process and Spark paths in parallel for digest comparison before making Spark selectable in profiles."],
        "preconditions": ["S1/S2 protect ownership and retries; governed subset manifests and F-drive capacity checks pass."],
        "workload_input": "Progressively larger governed click-log partitions with fixed source, schema, partition, and output manifests.",
        "controlled_variables": ["Subset size, executor count, executor memory, partition size, shuffle partitions, skew fixture, and retry point."],
        "signals": ["Records/s, MiB/s, peak memory, GC, shuffle, spill, skew, retries, row count, duplicates, and output digest."],
        "stop_conditions": ["Disk or memory guardrail is crossed, output integrity changes, or cleanup cannot be guaranteed."],
        "recovery_conditions": ["Executor loss is reconciled, output digest is deterministic, and temporary shuffle/output data is cleaned."],
    },
    "S6": {
        "existing_system_baseline": "The existing system deploys and rolls back model targets and has target-scoped recovery guards, but stateless API rolling continuity and single-GPU model handoff have not been measured as separate claims under load.",
        "affected_components": [
            {"component": "Existing API deployment", "files": ["infra/kubernetes/local/api.yaml", "apps/api/main.py"]},
            {"component": "Existing release and rollback control", "files": ["src/evm/control_panel/deployment_executor.py", "src/evm/control_panel/lifecycle_kubernetes.py"]},
        ],
        "selection_reasons": ["Separate CPU/API continuity from the unavoidable interruption boundary of one physical GPU."],
        "alternatives": ["A Recreate rollout is simpler but cannot demonstrate request drain; claiming GPU HA would be false without another accelerator."],
        "compatibility": ["Existing approval, identity, readiness, and known-good rollback gates remain mandatory."],
        "migration": ["Enable replicated API RollingUpdate independently; retain exact-target GPU handoff as a maintenance-window operation."],
        "preconditions": ["S2 queue bounds, S3 operating point, S4 GPU boundary, and clean rollback identity pass."],
        "workload_input": "Controlled replay near the verified operating point during one API replica replacement and one separately approved GPU handoff.",
        "controlled_variables": ["Replica count, rollout surge/unavailable, drain grace, request rate, target identity, and handoff timing."],
        "signals": ["Accepted loss/duplicates, p99, drain time, readiness, replacement time, GPU interruption, rollback identity, and recovery."],
        "stop_conditions": ["Target identity is ambiguous, rollback preflight fails, accepted requests are lost, or impact exceeds the maintenance boundary."],
        "recovery_conditions": ["API replicas are healthy, exact known-good GPU identity is serving, queues are drained, and monitoring is green."],
    },
    "S7": {
        "existing_system_baseline": "The existing workload ledger can run image, VLM, and LLM profiles sequentially, but admission is not uniformly derived from decode, pixels, tokens, in-flight cost, and long-request fairness.",
        "affected_components": [
            {"component": "Existing workload catalog and control", "files": ["src/evm/control_panel/scenario_workloads.py", "src/evm/control_panel/scenario_workload_control.py"]},
            {"component": "Existing model-family runner", "files": ["src/evm/model_runtime/workload_runner.py", "configs/scenario_workloads/live-presets.json"]},
        ],
        "selection_reasons": ["Retain real family-specific paths as auxiliary probes after common queue and GPU limits are known."],
        "alternatives": ["Replacing the main scale workload with VLM/LLM would reduce achievable request volume and confound admission with model size."],
        "compatibility": ["Existing family metric schemas remain distinct and unsupported metrics remain absent."],
        "migration": ["Add explicit cost budgets to existing profiles without changing validated model artifacts."],
        "preconditions": ["S2 bounded admission and S4 accelerator operating point pass; model families run sequentially."],
        "workload_input": "Fixed small/large image and short/long text request mixes for existing image, VLM, and LLM workloads.",
        "controlled_variables": ["Image bytes/pixels, decode work, input/output tokens, in-flight tokens, concurrency, and quantization identity."],
        "signals": ["Family-specific p95/p99, quality, TTFT/TPOT where supported, queue wait, fairness, starvation, peak VRAM, and OOM."],
        "stop_conditions": ["Any OOM, starvation, unsupported metric claim, or identity ambiguity occurs."],
        "recovery_conditions": ["Family queue drains, GPU memory returns to baseline, and the prior known-good workload remains selectable."],
    },
    "S8": {
        "existing_system_baseline": "The existing system has service-specific failure guards, bounded retries in selected paths, monitoring, and recovery evidence, but no distributed-scale dependency soak or resource-efficiency closure exists.",
        "affected_components": [
            {"component": "Existing HTTP and lifecycle recovery", "files": ["src/evm/core/http.py", "src/evm/control_panel/lifecycle_worker.py"]},
            {"component": "Existing operational evidence", "files": ["src/evm/operations/failure_evidence.py", "monitoring/prometheus/prometheus.yml"]},
        ],
        "selection_reasons": ["Run dependency faults and soak only after isolated correctness and capacity boundaries are accepted."],
        "alternatives": ["A broad chaos framework would add operational surface before target-scoped fault contracts are proven."],
        "compatibility": ["Existing A-E guard evidence remains baseline-only and cannot substitute for fresh scale-soak evidence."],
        "migration": ["Introduce fault controls behind explicit profiles and retain a no-fault operating profile for rollback."],
        "preconditions": ["S0 through S7 have accepted evidence, cleanup proof, and one selected safe operating point."],
        "workload_input": "One bounded dependency fault at a time followed by a 30-60 minute controlled replay near 70 percent of measured sustainable capacity.",
        "controlled_variables": ["Dependency target, fault latency/duration, timeout, retry budget, backoff, jitter, circuit hold, and load rate."],
        "signals": ["Retry amplification, MTTR, request impact, memory/FD/pool/queue slopes, CPU/GPU time, efficiency, cleanup, and evidence hashes."],
        "stop_conditions": ["Fault scope escapes the target, retry budget is exceeded, resource slope is unbounded, or cleanup becomes uncertain."],
        "recovery_conditions": ["Fault is removed, dependencies and queues recover, resources return to baseline, and every accepted artifact re-hashes."],
    },
}


def validate_catalog() -> None:
    if list(SCENARIO_DEFINITIONS) != list(SCENARIO_TITLES):
        raise ValueError("scenario catalog must preserve authoritative S0 through S8 order")
    if list(SCENARIO_IN_PLACE_CONTRACTS) != list(SCENARIO_TITLES):
        raise ValueError("in-place contracts must preserve authoritative S0 through S8 order")
