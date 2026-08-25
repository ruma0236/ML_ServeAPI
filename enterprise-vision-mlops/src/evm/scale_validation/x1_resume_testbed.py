from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import tomllib
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_SCHEMA_VERSION = "evm.s8_v4.x1_resume_testbed_config.v1"
EVIDENCE_SCHEMA_VERSION = "evm.s8_v4.x1_resume_testbed.v1"
MANIFEST_SCHEMA_VERSION = "evm.s8_v4.x1_resume_model_repository.v1"
CLAIM_CLASS = "preliminary_controlled_testbed"
CREDIT = "non_credit"
EXPECTED_MODELS = (
    "higgs_logistic_regression",
    "higgs_gaussian_nb",
    "higgs_tiny_mlp",
    "criteo_dlrm_lite",
)
EXPECTED_PROMETHEUS_JOBS = (
    "evm-api",
    "evm-b0-production",
    "evm-otel-collector",
    "evm-task-queue-worker",
    "prometheus",
)


class X1ResumeTestbedError(RuntimeError):
    pass


def prometheus_baseline_state(
    snapshot: Mapping[str, Any], expected_jobs: Sequence[str]
) -> str:
    jobs = snapshot.get("jobs")
    total = snapshot.get("total")
    up = snapshot.get("up")
    if (
        not isinstance(jobs, list)
        or any(not isinstance(job, str) for job in jobs)
        or type(total) is not int
        or type(up) is not int
    ):
        return "invalid_snapshot"
    expected = {str(job) for job in expected_jobs}
    if len(jobs) != len(expected) or set(jobs) != expected or total != len(expected):
        return "invalid_snapshot"
    if up == len(expected):
        return "ready"
    if up == len(expected) - 1:
        return "retryable_4_of_5"
    return "invalid_snapshot"


def prometheus_baseline_ready(
    snapshot: Mapping[str, Any], expected_jobs: Sequence[str]
) -> bool:
    return prometheus_baseline_state(snapshot, expected_jobs) == "ready"


def wait_for_prometheus_baseline(
    health_check: Callable[[float], Mapping[str, Any]],
    expected_jobs: Sequence[str],
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    observed_at: Callable[[], str],
) -> tuple[dict[str, Any], float, list[dict[str, Any]], bool, str]:
    if timeout_seconds <= 0 or poll_interval_seconds <= 0:
        raise ValueError("prometheus restore timeout and poll interval must be positive")
    started = monotonic()
    deadline = started + timeout_seconds
    samples: list[dict[str, Any]] = []
    last_snapshot: dict[str, Any] = {}
    while True:
        probe_started = monotonic()
        remaining = deadline - probe_started
        if remaining <= 0:
            return last_snapshot, probe_started - started, samples, False, "timeout"
        timestamp = observed_at()
        try:
            last_snapshot = dict(health_check(remaining))
        except Exception as exc:
            probe_finished = monotonic()
            samples.append(
                {
                    "observed_at": timestamp,
                    "error": f"{type(exc).__name__}:{exc}",
                    "probe_budget_seconds": remaining,
                    "probe_finished_elapsed_seconds": probe_finished - started,
                    "probe_started_elapsed_seconds": probe_started - started,
                    "state": "probe_error",
                }
            )
            reason = "deadline_exceeded" if probe_finished > deadline else "probe_error"
            return last_snapshot, probe_finished - started, samples, False, reason
        probe_finished = monotonic()
        state = prometheus_baseline_state(last_snapshot, expected_jobs)
        samples.append(
            {
                "observed_at": timestamp,
                "probe_budget_seconds": remaining,
                "probe_finished_elapsed_seconds": probe_finished - started,
                "probe_started_elapsed_seconds": probe_started - started,
                "snapshot": last_snapshot,
                "state": state,
            }
        )
        if probe_finished > deadline:
            return last_snapshot, probe_finished - started, samples, False, "deadline_exceeded"
        if state == "ready":
            return last_snapshot, probe_finished - started, samples, True, "ready"
        if state != "retryable_4_of_5":
            return last_snapshot, probe_finished - started, samples, False, state
        remaining = deadline - probe_finished
        if remaining <= 0:
            return last_snapshot, probe_finished - started, samples, False, "timeout"
        sleep(min(poll_interval_seconds, remaining))


def _validate_prometheus_restore_evidence(
    final_checks: Mapping[str, Any], *, timeout_seconds: float
) -> None:
    samples = final_checks.get("prometheus_restore_samples")
    elapsed = final_checks.get("prometheus_restore_seconds")
    if (
        not isinstance(samples, list)
        or not samples
        or not isinstance(elapsed, (int, float))
        or isinstance(elapsed, bool)
        or not math.isfinite(float(elapsed))
        or float(elapsed) < 0
        or float(elapsed) > timeout_seconds
        or final_checks.get("prometheus_restore_ready") is not True
        or final_checks.get("prometheus_restore_terminal_reason") != "ready"
    ):
        raise X1ResumeTestbedError("x1_resume_private_prometheus_cleanup")

    previous_finished = 0.0
    states: list[str] = []
    for sample in samples:
        if not isinstance(sample, Mapping) or "error" in sample:
            raise X1ResumeTestbedError("x1_resume_private_prometheus_cleanup")
        snapshot = sample.get("snapshot")
        started = sample.get("probe_started_elapsed_seconds")
        finished = sample.get("probe_finished_elapsed_seconds")
        budget = sample.get("probe_budget_seconds")
        if (
            not isinstance(snapshot, Mapping)
            or not all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in (started, finished, budget)
            )
        ):
            raise X1ResumeTestbedError("x1_resume_private_prometheus_cleanup")
        started_value = float(started)
        finished_value = float(finished)
        budget_value = float(budget)
        state = prometheus_baseline_state(snapshot, EXPECTED_PROMETHEUS_JOBS)
        if (
            not str(sample.get("observed_at") or "")
            or not all(math.isfinite(value) for value in (started_value, finished_value, budget_value))
            or started_value < previous_finished
            or finished_value < started_value
            or finished_value > timeout_seconds
            or budget_value <= 0
            or budget_value > timeout_seconds - started_value + 1e-9
            or sample.get("state") != state
        ):
            raise X1ResumeTestbedError("x1_resume_private_prometheus_cleanup")
        states.append(state)
        previous_finished = finished_value

    terminal_snapshot = dict(samples[-1].get("snapshot", {}))
    if (
        any(state != "retryable_4_of_5" for state in states[:-1])
        or states[-1] != "ready"
        or not math.isclose(float(elapsed), previous_finished, rel_tol=0.0, abs_tol=1e-9)
        or dict(final_checks.get("prometheus", {})) != terminal_snapshot
    ):
        raise X1ResumeTestbedError("x1_resume_private_prometheus_cleanup")


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("ascii")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(payload) + "\n", encoding="ascii", newline="\n")


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    display_name: str
    dataset: str
    source_kind: str
    source_key: str
    input_width: int


@dataclass(frozen=True)
class CellSpec:
    cell_id: str
    repetitions: int
    model_mix: Mapping[str, float]
    batching: str
    client_lanes: int
    client_workers: int
    analytical_roles: tuple[str, ...]


@dataclass(frozen=True)
class X1ResumeConfig:
    path: Path
    sha256: str
    seed: int
    triton_image: str
    triton_image_digest: str
    expected_gpu_name: str
    expected_gpu_uuid: str
    http_port: int
    grpc_port: int
    metrics_port: int
    readiness_timeout_seconds: int
    cleanup_timeout_seconds: int
    input_paths: Mapping[str, str]
    sample_rows_per_dataset: int
    warmup_seconds: int
    measurement_seconds: int
    offered_rps: int
    minimum_offered_rate_attainment: float
    matched_load_relative_tolerance: float
    queue_depth_per_api: int
    request_timeout_seconds: int
    sample_gpu_interval_ms: int
    q0_activity_seconds_per_model: int
    q0_workers: int
    q0_request_batch_size: int
    models: tuple[ModelSpec, ...]
    cells: tuple[CellSpec, ...]
    batching: Mapping[str, Mapping[str, Any]]
    profiler: Mapping[str, Any]
    claim_boundary: str

    @classmethod
    def from_path(cls, path: Path) -> "X1ResumeConfig":
        raw = path.read_bytes()
        payload = tomllib.loads(raw.decode("utf-8"))
        runtime = dict(payload.get("runtime", {}))
        inputs = dict(payload.get("inputs", {}))
        load = dict(payload.get("load", {}))
        q0 = dict(payload.get("q0", {}))
        config = cls(
            path=path,
            sha256=hashlib.sha256(raw).hexdigest(),
            seed=int(payload.get("seed", 0)),
            triton_image=str(runtime.get("triton_image") or ""),
            triton_image_digest=str(runtime.get("triton_image_digest") or ""),
            expected_gpu_name=str(runtime.get("expected_gpu_name") or ""),
            expected_gpu_uuid=str(runtime.get("expected_gpu_uuid") or ""),
            http_port=int(runtime.get("http_port", 0)),
            grpc_port=int(runtime.get("grpc_port", 0)),
            metrics_port=int(runtime.get("metrics_port", 0)),
            readiness_timeout_seconds=int(runtime.get("readiness_timeout_seconds", 0)),
            cleanup_timeout_seconds=int(runtime.get("cleanup_timeout_seconds", 0)),
            input_paths={
                key: str(value) for key, value in inputs.items() if key != "sample_rows_per_dataset"
            },
            sample_rows_per_dataset=int(inputs.get("sample_rows_per_dataset", 0)),
            warmup_seconds=int(load.get("warmup_seconds", 0)),
            measurement_seconds=int(load.get("measurement_seconds", 0)),
            offered_rps=int(load.get("offered_requests_per_second", 0)),
            minimum_offered_rate_attainment=float(load.get("minimum_offered_rate_attainment", 0)),
            matched_load_relative_tolerance=float(load.get("matched_load_relative_tolerance", 0)),
            queue_depth_per_api=int(load.get("queue_depth_per_api", 0)),
            request_timeout_seconds=int(load.get("request_timeout_seconds", 0)),
            sample_gpu_interval_ms=int(load.get("sample_gpu_interval_ms", 0)),
            q0_activity_seconds_per_model=int(q0.get("activity_seconds_per_model", 0)),
            q0_workers=int(q0.get("workers", 0)),
            q0_request_batch_size=int(q0.get("request_batch_size", 0)),
            models=tuple(ModelSpec(**item) for item in payload.get("models", [])),
            cells=tuple(
                CellSpec(
                    cell_id=str(item.get("cell_id") or ""),
                    repetitions=int(item.get("repetitions", 0)),
                    model_mix={
                        str(key): float(value)
                        for key, value in dict(item.get("model_mix", {})).items()
                    },
                    batching=str(item.get("batching") or ""),
                    client_lanes=int(item.get("client_lanes", 0)),
                    client_workers=int(item.get("client_workers", 0)),
                    analytical_roles=tuple(
                        str(value) for value in item.get("analytical_roles", [])
                    ),
                )
                for item in payload.get("cells", [])
            ),
            batching={key: dict(value) for key, value in dict(payload.get("batching", {})).items()},
            profiler=dict(payload.get("profiler", {})),
            claim_boundary=str(dict(payload.get("claim", {})).get("boundary") or ""),
        )
        config.validate(payload)
        return config

    def validate(self, raw: Mapping[str, Any] | None = None) -> None:
        if raw is not None and raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
            raise X1ResumeTestbedError("x1_resume_config_schema")
        if raw is not None and (raw.get("claim_class"), raw.get("credit")) != (CLAIM_CLASS, CREDIT):
            raise X1ResumeTestbedError("x1_resume_config_claim_class")
        if tuple(model.model_id for model in self.models) != EXPECTED_MODELS:
            raise X1ResumeTestbedError("x1_resume_model_set")
        if any(model.input_width not in {28, 39} for model in self.models):
            raise X1ResumeTestbedError("x1_resume_model_input_width")
        if self.warmup_seconds != 10 or self.measurement_seconds != 30:
            raise X1ResumeTestbedError("x1_resume_measurement_window")
        if not self.triton_image_digest.startswith("sha256:"):
            raise X1ResumeTestbedError("x1_resume_triton_digest")
        if not self.expected_gpu_uuid.startswith("GPU-"):
            raise X1ResumeTestbedError("x1_resume_gpu_uuid")
        if len({self.http_port, self.grpc_port, self.metrics_port}) != 3:
            raise X1ResumeTestbedError("x1_resume_ports")
        if not 0 < self.minimum_offered_rate_attainment <= 1:
            raise X1ResumeTestbedError("x1_resume_minimum_offered_rate_attainment")
        if not 0 < self.matched_load_relative_tolerance <= 0.10:
            raise X1ResumeTestbedError("x1_resume_matched_load_relative_tolerance")
        if (
            min(
                self.seed,
                self.readiness_timeout_seconds,
                self.cleanup_timeout_seconds,
                self.sample_rows_per_dataset,
                self.offered_rps,
                self.queue_depth_per_api,
                self.request_timeout_seconds,
                self.sample_gpu_interval_ms,
                self.q0_activity_seconds_per_model,
                self.q0_workers,
                self.q0_request_batch_size,
            )
            <= 0
        ):
            raise X1ResumeTestbedError("x1_resume_positive_bounds")
        if set(self.batching) != {"off", "on"}:
            raise X1ResumeTestbedError("x1_resume_batching_profiles")
        if len(self.cells) != 10 or sum(cell.repetitions for cell in self.cells) != 22:
            raise X1ResumeTestbedError("x1_resume_cell_arithmetic")
        if len({cell.cell_id for cell in self.cells}) != len(self.cells):
            raise X1ResumeTestbedError("x1_resume_cell_identity")
        for cell in self.cells:
            if (
                cell.batching not in self.batching
                or min(cell.repetitions, cell.client_lanes, cell.client_workers) <= 0
            ):
                raise X1ResumeTestbedError("x1_resume_cell_bounds")
            if set(cell.model_mix) - set(EXPECTED_MODELS):
                raise X1ResumeTestbedError("x1_resume_cell_model_mix")
            if not math.isclose(sum(cell.model_mix.values()), 1.0, abs_tol=1e-9):
                raise X1ResumeTestbedError("x1_resume_cell_mix_sum")
            if not cell.analytical_roles:
                raise X1ResumeTestbedError("x1_resume_cell_roles")
        if (
            CLAIM_CLASS not in self.claim_boundary.lower()
            or "non-credit" not in self.claim_boundary.lower()
        ):
            raise X1ResumeTestbedError("x1_resume_claim_boundary")

    @property
    def immutable_image(self) -> str:
        return f"{self.triton_image.rsplit(':', 1)[0]}@{self.triton_image_digest}"

    @property
    def expected_physical_runs(self) -> int:
        return sum(cell.repetitions for cell in self.cells)


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def jain_fairness(values: Sequence[float]) -> float:
    finite = [float(value) for value in values if math.isfinite(float(value)) and float(value) >= 0]
    if not finite or sum(value * value for value in finite) == 0:
        return 0.0
    return sum(finite) ** 2 / (len(finite) * sum(value * value for value in finite))


def deterministic_model_schedule(model_mix: Mapping[str, float]) -> tuple[str, ...]:
    """Build a deterministic 100-slot smooth weighted round-robin schedule."""
    weights = {
        model_id: round(float(model_mix.get(model_id, 0)) * 100) for model_id in EXPECTED_MODELS
    }
    if sum(weights.values()) != 100:
        raise X1ResumeTestbedError(f"x1_resume_mix_schedule:{weights}")
    current = {model_id: 0 for model_id in EXPECTED_MODELS}
    slots: list[str] = []
    for _ in range(100):
        for model_id in EXPECTED_MODELS:
            current[model_id] += weights[model_id]
        selected = max(EXPECTED_MODELS, key=lambda model_id: current[model_id])
        current[selected] -= 100
        slots.append(selected)
    if Counter(slots) != Counter(weights):
        raise X1ResumeTestbedError("x1_resume_mix_schedule_counts")
    return tuple(slots)


def request_interval_overlap(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    events: list[tuple[int, int, str]] = []
    for item in records:
        if item.get("outcome") != "completed":
            continue
        events.append((int(item["started_ns"]), 0, str(item["model_id"])))
        events.append((int(item["finished_ns"]), 1, str(item["model_id"])))
    active: Counter[str] = Counter()
    pairs: set[tuple[str, str]] = set()
    for _timestamp, kind, model_id in sorted(events):
        if kind == 0:
            for other, count in active.items():
                if count > 0 and other != model_id:
                    pairs.add(tuple(sorted((model_id, other))))
            active[model_id] += 1
        else:
            active[model_id] -= 1
    return {
        "observed": bool(pairs),
        "distinct_model_pairs": [list(pair) for pair in sorted(pairs)],
        "scope": "client request intervals overlap; not CUDA kernel-overlap evidence",
    }


def triton_trace_compute_counts(path: Path) -> dict[str, int]:
    counts = {model_id: 0 for model_id in EXPECTED_MODELS}
    if not path.is_file():
        return counts
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise X1ResumeTestbedError("x1_resume_trace_utf8") from exc
    values: list[Any] = []
    try:
        values.append(json.loads(text))
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        offset = 0
        while offset < len(text):
            while offset < len(text) and (text[offset].isspace() or text[offset] == ","):
                offset += 1
            if offset >= len(text):
                break
            try:
                value, offset = decoder.raw_decode(text, offset)
            except json.JSONDecodeError as exc:
                raise X1ResumeTestbedError("x1_resume_trace_json") from exc
            values.append(value)

    model_by_id: dict[str, str] = {}
    compute_by_id: Counter[str] = Counter()

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, Mapping):
            return
        trace_id = str(value.get("id") or value.get("trace_id") or "")
        model_name = str(value.get("model_name") or "")
        if trace_id and model_name in EXPECTED_MODELS:
            if trace_id in model_by_id and model_by_id[trace_id] != model_name:
                raise X1ResumeTestbedError(f"x1_resume_trace_model_conflict:{trace_id}")
            model_by_id[trace_id] = model_name
        timestamps = value.get("timestamps", [])
        if trace_id and isinstance(timestamps, list):
            compute_by_id[trace_id] += sum(
                isinstance(item, Mapping) and str(item.get("name") or "").upper() == "COMPUTE_START"
                for item in timestamps
            )
        for key, item in value.items():
            if key != "timestamps" and isinstance(item, (list, Mapping)):
                visit(item)

    for value in values:
        visit(value)
    for trace_id, amount in compute_by_id.items():
        model_id = model_by_id.get(trace_id)
        if amount and not model_id:
            raise X1ResumeTestbedError(f"x1_resume_trace_unbound_compute:{trace_id}")
        if model_id:
            counts[model_id] += int(amount)
    return counts


def summarize_requests(
    *,
    offered: int,
    admitted: int,
    local_admission_rejected: int,
    records: Sequence[Mapping[str, Any]],
    measurement_seconds: float,
    measurement_end_ns: int,
    drain_seconds: float,
    model_mix: Mapping[str, float],
) -> dict[str, Any]:
    terminal_ids = [str(item.get("request_id") or "") for item in records]
    duplicate = sum(count - 1 for count in Counter(terminal_ids).values() if count > 1)
    cohort_completed = sum(item.get("outcome") == "completed" for item in records)
    cohort_failures_5xx = sum(item.get("outcome") == "5xx" for item in records)
    cohort_other_errors = sum(item.get("outcome") == "error" for item in records)
    cohort_terminal = cohort_completed + cohort_failures_5xx + cohort_other_errors
    window_records = [
        item
        for item in records
        if int(item.get("finished_ns", measurement_end_ns + 1)) <= measurement_end_ns
    ]
    window_completed = sum(item.get("outcome") == "completed" for item in window_records)
    window_failures_5xx = sum(item.get("outcome") == "5xx" for item in window_records)
    window_other_errors = sum(item.get("outcome") == "error" for item in window_records)
    latencies = [
        float(item["latency_ms"]) for item in window_records if item.get("outcome") == "completed"
    ]
    queue_waits = [
        float(item["queue_wait_ms"])
        for item in window_records
        if item.get("outcome") == "completed"
    ]
    per_model = {}
    for model_id in EXPECTED_MODELS:
        model_records = [item for item in records if item.get("model_id") == model_id]
        model_cohort_completed = sum(item.get("outcome") == "completed" for item in model_records)
        model_window_records = [
            item
            for item in model_records
            if int(item.get("finished_ns", measurement_end_ns + 1)) <= measurement_end_ns
        ]
        model_window_completed = sum(
            item.get("outcome") == "completed" for item in model_window_records
        )
        model_latencies = [
            float(item["latency_ms"])
            for item in model_window_records
            if item.get("outcome") == "completed"
        ]
        per_model[model_id] = {
            "window_completed": model_window_completed,
            "admitted_cohort_completed": model_cohort_completed,
            "throughput_rps": model_window_completed / max(measurement_seconds, 1e-9),
            "p99_ms": percentile(model_latencies, 0.99),
        }
    raw_rates = [per_model[item]["throughput_rps"] for item in EXPECTED_MODELS]
    actual_offered_rps = offered / max(measurement_seconds, 1e-9)
    attainment = [
        per_model[item]["throughput_rps"]
        / max(float(model_mix.get(item, 0.0)) * actual_offered_rps, 1e-9)
        for item in EXPECTED_MODELS
        if float(model_mix.get(item, 0.0)) > 0
    ]
    return {
        "offered": offered,
        "admitted": admitted,
        "local_admission_rejected": local_admission_rejected,
        "window_completed": window_completed,
        "window_http_5xx": window_failures_5xx,
        "window_other_errors": window_other_errors,
        "admitted_cohort_completed": cohort_completed,
        "admitted_cohort_http_5xx": cohort_failures_5xx,
        "admitted_cohort_other_errors": cohort_other_errors,
        "tail_completed": cohort_completed - window_completed,
        "loss": max(0, admitted - cohort_terminal),
        "duplicates": duplicate,
        "throughput_rps": window_completed / max(measurement_seconds, 1e-9),
        "actual_offered_rps": actual_offered_rps,
        "drain_seconds": drain_seconds,
        "throughput_scope": "completions whose terminal timestamp is inside the fixed measurement window",
        "terminal_scope": "all requests admitted during the fixed measurement window after bounded drain",
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
        },
        "queue_wait_ms": {
            "p50": percentile(queue_waits, 0.50),
            "p95": percentile(queue_waits, 0.95),
            "p99": percentile(queue_waits, 0.99),
        },
        "per_model": per_model,
        "fairness_target_basis": "model_mix * actual_window_offered_rps",
        "raw_throughput_jain_fairness": jain_fairness(raw_rates),
        "normalized_attainment_jain_fairness": jain_fairness(attainment),
    }


def _bound_file(root: Path, identity: Mapping[str, Any], label: str) -> Path:
    relative = Path(str(identity.get("path") or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise X1ResumeTestbedError(f"x1_resume_private_path:{label}")
    path = root / relative
    if (
        not path.is_file()
        or path.stat().st_size != int(identity.get("bytes", -1))
        or sha256_file(path) != identity.get("sha256")
    ):
        raise X1ResumeTestbedError(f"x1_resume_private_digest:{label}:{relative}")
    return path


def _triton_metrics_for_model(text: str, model_id: str) -> dict[str, float]:
    fields = {
        "nv_inference_request_success": "success",
        "nv_inference_compute_infer_duration_us": "compute_us",
        "nv_inference_count": "inference_count",
        "nv_inference_exec_count": "execution_count",
    }
    result = {field: 0.0 for field in fields.values()}
    for line in text.splitlines():
        if f'model="{model_id}"' not in line:
            continue
        for metric, field in fields.items():
            if line.startswith(metric):
                try:
                    result[field] += float(line.rsplit(" ", 1)[1])
                except (IndexError, ValueError):
                    raise X1ResumeTestbedError(
                        f"x1_resume_private_q0_metric_parse:{model_id}:{metric}"
                    ) from None
    return result


def validate_private_evidence(
    payload: Mapping[str, Any],
    *,
    config: X1ResumeConfig,
    private_suite_root: Path,
    model_repository_root: Path,
    source_root: Path,
) -> dict[str, Any]:
    """Recompute private evidence bindings before a report is used for resume claims."""
    for item in payload.get("source_blobs", []):
        identity = dict(item)
        path = source_root / str(identity.get("path") or "")
        if not path.is_file() or sha256_file(path) != identity.get("sha256"):
            raise X1ResumeTestbedError(f"x1_resume_source_blob:{identity.get('path')}")

    manifest_path = model_repository_root / "model-repository-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise X1ResumeTestbedError("x1_resume_private_manifest") from exc
    environment = dict(payload.get("environment", {}))
    if sha256_file(manifest_path) != environment.get("repository_manifest_sha256"):
        raise X1ResumeTestbedError("x1_resume_private_manifest_digest")
    entries = list(manifest.get("entries", []))
    if manifest.get("repository_sha256") != environment.get(
        "repository_sha256"
    ) or canonical_sha256(entries) != manifest.get("repository_sha256"):
        raise X1ResumeTestbedError("x1_resume_private_repository_aggregate")
    for item in entries:
        _bound_file(model_repository_root, dict(item), "repository_entry")
    profile_identities = dict(manifest.get("profile_identities", {}))
    model_identities = dict(manifest.get("model_identities", {}))
    for profile in ("off", "on"):
        selected = [
            item for item in entries if str(item.get("path", "")).startswith(f"batch-{profile}/")
        ]
        if dict(profile_identities.get(profile, {})) != {
            "entry_count": len(selected),
            "repository_sha256": canonical_sha256(selected),
        }:
            raise X1ResumeTestbedError(f"x1_resume_private_profile:{profile}")
        selected_by_path = {str(item.get("path")): item for item in selected}
        for model_id in EXPECTED_MODELS:
            artifact = dict(selected_by_path.get(f"batch-{profile}/{model_id}/1/model.pt", {}))
            model_config = dict(
                selected_by_path.get(f"batch-{profile}/{model_id}/config.pbtxt", {})
            )
            if dict(model_identities.get(f"{profile}:{model_id}", {})) != {
                "artifact_sha256": artifact.get("sha256"),
                "config_sha256": model_config.get("sha256"),
            } or not all((artifact, model_config)):
                raise X1ResumeTestbedError(f"x1_resume_private_profile_model:{profile}:{model_id}")
    for item in manifest.get("source_blobs", []):
        identity = dict(item)
        path = source_root / str(identity.get("path") or "")
        if not path.is_file() or sha256_file(path) != identity.get("sha256"):
            raise X1ResumeTestbedError(f"x1_resume_manifest_source_blob:{identity.get('path')}")

    q0_evidence = dict(dict(payload.get("profile_evidence", {})).get("q0_isolated", {}))
    trace_path = _bound_file(private_suite_root, dict(q0_evidence.get("trace", {})), "q0_trace")
    log_path = _bound_file(private_suite_root, dict(q0_evidence.get("log", {})), "q0_log")
    log_text = log_path.read_text(encoding="utf-8", errors="strict")
    trace_counts = triton_trace_compute_counts(trace_path)
    if trace_counts != q0_evidence.get("compute_start_counts"):
        raise X1ResumeTestbedError("x1_resume_private_q0_trace_counts")

    for item in payload.get("q0", []):
        model_id = str(item.get("model_id") or "")
        expected = dict(model_identities.get(f"off:{model_id}", {}))
        artifact_path = model_repository_root / f"batch-off/{model_id}/1/model.pt"
        config_path = model_repository_root / f"batch-off/{model_id}/config.pbtxt"
        if (
            not artifact_path.is_file()
            or not config_path.is_file()
            or sha256_file(artifact_path) != item.get("artifact_sha256")
            or sha256_file(config_path) != item.get("config_sha256")
            or expected
            != {
                "artifact_sha256": item.get("artifact_sha256"),
                "config_sha256": item.get("config_sha256"),
            }
        ):
            raise X1ResumeTestbedError(f"x1_resume_private_q0_model:{model_id}")
        raw_path = _bound_file(private_suite_root, dict(item.get("private_raw", {})), "q0_raw")
        raw = json.loads(raw_path.read_bytes())
        gpu_samples = list(raw.get("gpu_samples", []))
        before_metrics = _triton_metrics_for_model(str(raw.get("metrics_before", "")), model_id)
        after_metrics = _triton_metrics_for_model(str(raw.get("metrics_after", "")), model_id)
        gpu_lines = [str(line) for line in raw.get("gpu_log_lines", [])]
        if (
            raw.get("model_id") != model_id
            or canonical_sha256(raw.get("metrics_before", "")) != item.get("metrics_before_sha256")
            or canonical_sha256(raw.get("metrics_after", "")) != item.get("metrics_after_sha256")
            or len(gpu_samples) != item.get("isolated_gpu_sample_count")
            or sum(float(sample.get("utilization_percent", 0)) > 0 for sample in gpu_samples)
            != item.get("isolated_gpu_busy_samples")
            or raw.get("isolated_request_count") != item.get("isolated_request_count")
            or not math.isclose(
                after_metrics["success"] - before_metrics["success"],
                float(item.get("triton_success_delta", float("nan"))),
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            or not math.isclose(
                after_metrics["compute_us"] - before_metrics["compute_us"],
                float(item.get("triton_compute_delta", float("nan"))),
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            or [canonical_sha256(line) for line in gpu_lines] != item.get("gpu_log_line_sha256")
            or any(line not in log_text.splitlines() for line in gpu_lines)
            or trace_counts.get(model_id, 0)
            != int(item.get("triton_trace_compute_start_count", -1))
        ):
            raise X1ResumeTestbedError(f"x1_resume_private_q0_raw:{model_id}")
    if any(
        trace_counts.get(str(item.get("model_id")), 0)
        != int(item.get("triton_trace_compute_start_count", -1))
        for item in payload.get("q0", [])
    ):
        raise X1ResumeTestbedError("x1_resume_private_q0_trace_binding")

    for profile in ("off", "on"):
        profile_evidence = dict(dict(payload.get("profile_evidence", {})).get(profile, {}))
        _bound_file(
            private_suite_root,
            dict(profile_evidence.get("log", {})),
            f"profile_log:{profile}",
        )

    for item in payload.get("runs", []):
        raw_path = _bound_file(private_suite_root, dict(item.get("private_raw", {})), "attempt")
        raw = json.loads(raw_path.read_bytes())
        raw_cell = dict(raw.get("cell", {}))
        raw_mix = {
            str(key): float(value) for key, value in dict(raw_cell.get("model_mix", {})).items()
        }
        window = dict(raw.get("measurement_window", {}))
        admission = dict(raw.get("admission", {}))
        records = list(raw.get("records", []))
        recomputed_metrics = summarize_requests(
            offered=int(admission.get("offered", -1)),
            admitted=int(admission.get("admitted", -1)),
            local_admission_rejected=int(admission.get("local_admission_rejected", -1)),
            records=records,
            measurement_seconds=float(window.get("seconds", 0)),
            measurement_end_ns=int(window.get("end_ns", -1)),
            drain_seconds=float(raw.get("drain_seconds", float("nan"))),
            model_mix=raw_mix,
        )
        before_text = str(raw.get("metrics_before", ""))
        after_text = str(raw.get("metrics_after", ""))
        recomputed_deltas = {}
        for model_id in EXPECTED_MODELS:
            before = _triton_metrics_for_model(before_text, model_id)
            after = _triton_metrics_for_model(after_text, model_id)
            recomputed_deltas[model_id] = {
                key: after[key] - before[key]
                for key in ("success", "compute_us", "inference_count", "execution_count")
            }
        active_models = {model_id for model_id, fraction in raw_mix.items() if fraction > 0}
        inference_count = sum(
            recomputed_deltas[model_id]["inference_count"] for model_id in active_models
        )
        execution_count = sum(
            recomputed_deltas[model_id]["execution_count"] for model_id in active_models
        )
        formed_batch_size = inference_count / execution_count if execution_count > 0 else 0.0
        recomputed_batching = {
            "inference_count_delta": inference_count,
            "execution_count_delta": execution_count,
            "formed_mean_batch_size": formed_batch_size,
            "formed_batch_observed": formed_batch_size > 1.0,
        }
        recomputed_overlap = request_interval_overlap(records)
        if (
            raw.get("attempt_id") != item.get("attempt_id")
            or raw_cell.get("cell_id") != item.get("cell_id")
            or int(raw.get("repetition", -1)) != int(item.get("repetition", -2))
            or raw_mix != item.get("model_mix")
            or float(window.get("seconds", 0)) <= 0
            or recomputed_metrics != item.get("metrics")
            or recomputed_deltas != item.get("triton_metric_deltas")
            or recomputed_overlap != item.get("cross_model_request_overlap")
            or recomputed_batching != item.get("batching_proof")
            or raw.get("metrics") != item.get("metrics")
            or raw.get("triton_metric_deltas") != item.get("triton_metric_deltas")
            or raw.get("cross_model_request_overlap") != item.get("cross_model_request_overlap")
            or raw.get("batching_proof") != item.get("batching_proof")
        ):
            raise X1ResumeTestbedError(f"x1_resume_private_attempt:{item.get('attempt_id')}")
    cleanup_path = _bound_file(
        private_suite_root, dict(payload.get("cleanup_evidence", {})), "cleanup"
    )
    cleanup_raw = json.loads(cleanup_path.read_bytes())
    if cleanup_raw.get("cleanup") != payload.get("cleanup") or canonical_sha256(
        cleanup_raw.get("final_checks", {})
    ) != dict(payload.get("cleanup_evidence", {})).get("final_checks_sha256"):
        raise X1ResumeTestbedError("x1_resume_private_cleanup")
    _validate_prometheus_restore_evidence(
        dict(cleanup_raw.get("final_checks", {})),
        timeout_seconds=config.cleanup_timeout_seconds,
    )
    return {
        "private_artifacts_valid": True,
        "private_attempt_count": len(payload.get("runs", [])),
        "repository_entry_count": len(entries),
    }


def validate_evidence(
    payload: Mapping[str, Any],
    config: X1ResumeConfig,
    *,
    private_suite_root: Path | None = None,
    model_repository_root: Path | None = None,
    source_root: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        errors.append("schema_version")
    if payload.get("claim_class") != CLAIM_CLASS or payload.get("credit") != CREDIT:
        errors.append("claim_class")
    if payload.get("canonical_x1") is not False or payload.get("acceptance_credit") is not False:
        errors.append("canonical_or_credit")
    if payload.get("config_sha256") != config.sha256:
        errors.append("config_sha256")
    if payload.get("claim_boundary") != config.claim_boundary:
        errors.append("claim_boundary")
    status = str(payload.get("status") or "")
    if status not in {"running", "complete", "failed"}:
        errors.append("status")
    q0 = payload.get("q0", [])
    runs = payload.get("runs", [])
    if not isinstance(q0, list) or not isinstance(runs, list):
        errors.append("run_collections")
        q0, runs = [], []
    if status == "complete":
        if Counter(str(item.get("model_id")) for item in q0) != Counter(EXPECTED_MODELS):
            errors.append("q0_model_set")
        for item in q0:
            if (
                item.get("cuda_activity_observed") is not True
                or item.get("cpu_fallback_observed") is not False
            ):
                errors.append("q0_cuda_contract")
            if item.get("triton_gpu_instance_proof") is not True:
                errors.append("q0_gpu_instance_proof")
            if float(item.get("triton_compute_delta", 0)) <= 0:
                errors.append("q0_compute_delta")
            if int(item.get("isolated_gpu_busy_samples", 0)) <= 0:
                errors.append("q0_gpu_busy_samples")
            if int(item.get("isolated_request_count", 0)) < 64:
                errors.append("q0_trace_sampling_opportunity")
            if int(item.get("triton_trace_compute_start_count", 0)) <= 0:
                errors.append("q0_trace_compute_start")
            if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("artifact_sha256") or "")):
                errors.append("q0_artifact_sha256")
            if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("config_sha256") or "")):
                errors.append("q0_config_sha256")
            config_readback = dict(item.get("triton_config_readback", {}))
            if "KIND_GPU" not in canonical(config_readback):
                errors.append("q0_config_readback_gpu")
        observed = Counter(
            (str(item.get("cell_id")), int(item.get("repetition", 0))) for item in runs
        )
        expected = Counter(
            (cell.cell_id, repetition)
            for cell in config.cells
            for repetition in range(1, cell.repetitions + 1)
        )
        if observed != expected:
            errors.append("physical_run_matrix")
        if len(runs) != config.expected_physical_runs:
            errors.append("physical_run_count")
        for run in runs:
            metrics = run.get("metrics", {})
            if not isinstance(metrics, Mapping):
                errors.append("metrics_mapping")
                continue
            offered = int(metrics.get("offered", -1))
            admitted = int(metrics.get("admitted", -1))
            local_rejected = int(metrics.get("local_admission_rejected", -1))
            window_completed = int(metrics.get("window_completed", -1))
            cohort_completed = int(metrics.get("admitted_cohort_completed", -1))
            cohort_5xx = int(metrics.get("admitted_cohort_http_5xx", -1))
            cohort_errors = int(metrics.get("admitted_cohort_other_errors", -1))
            loss = int(metrics.get("loss", -1))
            if (
                offered != admitted + local_rejected
                or admitted != cohort_completed + cohort_5xx + cohort_errors + loss
                or int(metrics.get("tail_completed", -1)) != cohort_completed - window_completed
                or window_completed > cohort_completed
            ):
                errors.append("request_arithmetic")
            if (
                int(metrics.get("duplicates", -1)) != 0
                or cohort_5xx != 0
                or cohort_errors != 0
                or loss != 0
                or int(metrics.get("window_http_5xx", -1)) != 0
                or int(metrics.get("window_other_errors", -1)) != 0
            ):
                errors.append("resume_success_errors_or_loss")
            per_model = dict(metrics.get("per_model", {}))
            if set(per_model) != set(EXPECTED_MODELS):
                errors.append("per_model_set")
            else:
                model_window_sum = sum(
                    int(dict(per_model[model_id]).get("window_completed", -1))
                    for model_id in EXPECTED_MODELS
                )
                model_cohort_sum = sum(
                    int(dict(per_model[model_id]).get("admitted_cohort_completed", -1))
                    for model_id in EXPECTED_MODELS
                )
                if model_window_sum != window_completed or model_cohort_sum != cohort_completed:
                    errors.append("per_model_arithmetic")
                for model_id in EXPECTED_MODELS:
                    model_metrics = dict(per_model[model_id])
                    expected_rate = int(model_metrics.get("window_completed", -1)) / max(
                        config.measurement_seconds, 1e-9
                    )
                    if not math.isclose(
                        float(model_metrics.get("throughput_rps", float("nan"))),
                        expected_rate,
                        rel_tol=1e-9,
                        abs_tol=1e-9,
                    ):
                        errors.append("per_model_throughput_recompute")
                    p99 = float(model_metrics.get("p99_ms", float("nan")))
                    if not math.isfinite(p99) or p99 < 0:
                        errors.append("per_model_percentile")
            throughput = float(metrics.get("throughput_rps", float("nan")))
            actual_offered_rps = float(metrics.get("actual_offered_rps", float("nan")))
            drain_seconds = float(metrics.get("drain_seconds", float("nan")))
            if (
                not math.isfinite(throughput)
                or throughput < 0
                or not math.isclose(
                    throughput,
                    window_completed / max(config.measurement_seconds, 1e-9),
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                )
                or not math.isclose(
                    actual_offered_rps,
                    offered / max(config.measurement_seconds, 1e-9),
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                )
                or not math.isfinite(drain_seconds)
                or not 0 <= drain_seconds <= config.cleanup_timeout_seconds
            ):
                errors.append("window_metric_recompute")
            offered_rate_attainment = actual_offered_rps / max(config.offered_rps, 1e-9)
            if (
                not math.isfinite(offered_rate_attainment)
                or offered_rate_attainment < config.minimum_offered_rate_attainment
                or offered_rate_attainment > 1 + config.matched_load_relative_tolerance
            ):
                errors.append("offered_load_attainment")
            for percentile_key in ("latency_ms", "queue_wait_ms"):
                percentiles = dict(metrics.get(percentile_key, {}))
                values = [
                    float(percentiles.get(name, float("nan"))) for name in ("p50", "p95", "p99")
                ]
                if not all(
                    math.isfinite(value) and value >= 0 for value in values
                ) or values != sorted(values):
                    errors.append("percentile_order")
            fairness_values = [
                float(metrics.get("raw_throughput_jain_fairness", float("nan"))),
                float(metrics.get("normalized_attainment_jain_fairness", float("nan"))),
            ]
            if not all(math.isfinite(value) and 0 <= value <= 1 for value in fairness_values):
                errors.append("fairness_bounds")
            if run.get("cpu_fallback_observed") is not False:
                errors.append("run_cpu_fallback")
            if run.get("triton_execution_proved") is not True:
                errors.append("run_triton_execution")
            cell = next((item for item in config.cells if item.cell_id == run.get("cell_id")), None)
            overlap_required = bool(
                cell
                and cell.client_workers > 1
                and sum(value > 0 for value in cell.model_mix.values()) > 1
            )
            if cell:
                topology = dict(run.get("client_topology", {}))
                load_contract = dict(run.get("load_contract", {}))
                if (
                    run.get("batching") != cell.batching
                    or topology.get("lanes") != cell.client_lanes
                    or topology.get("workers") != cell.client_workers
                    or run.get("model_mix") != dict(cell.model_mix)
                    or load_contract
                    != {
                        "target_offered_rps": config.offered_rps,
                        "minimum_offered_rate_attainment": config.minimum_offered_rate_attainment,
                        "matched_load_relative_tolerance": config.matched_load_relative_tolerance,
                        "warmup_seconds": config.warmup_seconds,
                        "measurement_seconds": config.measurement_seconds,
                    }
                ):
                    errors.append("run_frozen_load_topology")
                if set(per_model) == set(EXPECTED_MODELS):
                    if any(
                        int(dict(per_model[model_id]).get("window_completed", 0)) <= 0
                        for model_id, fraction in cell.model_mix.items()
                        if fraction > 0
                    ):
                        errors.append("active_model_window_progress")
                    raw_rates = [
                        float(dict(per_model[model_id])["throughput_rps"])
                        for model_id in EXPECTED_MODELS
                    ]
                    target_total_rps = offered / max(config.measurement_seconds, 1e-9)
                    attainment = [
                        float(dict(per_model[model_id])["throughput_rps"])
                        / max(float(cell.model_mix.get(model_id, 0)) * target_total_rps, 1e-9)
                        for model_id in EXPECTED_MODELS
                        if float(cell.model_mix.get(model_id, 0)) > 0
                    ]
                    if not math.isclose(
                        fairness_values[0], jain_fairness(raw_rates), rel_tol=1e-9, abs_tol=1e-9
                    ) or not math.isclose(
                        fairness_values[1],
                        jain_fairness(attainment),
                        rel_tol=1e-9,
                        abs_tol=1e-9,
                    ):
                        errors.append("fairness_recompute")
            if overlap_required and (
                run.get("cross_model_request_overlap_required") is not True
                or dict(run.get("cross_model_request_overlap", {})).get("observed") is not True
            ):
                errors.append("cross_model_request_overlap")
            if run.get("cell_id") == "balanced-concurrent-batch-on" and (
                dict(run.get("batching_proof", {})).get("formed_batch_observed") is not True
                or float(dict(run.get("batching_proof", {})).get("formed_mean_batch_size", 0))
                <= 1.0
            ):
                errors.append("batch_not_formed")
            if run.get("cell_id") == "hot-dlrm-l2w4":
                progress = dict(metrics.get("per_model", {}))
                if any(
                    int(dict(progress.get(model_id, {})).get("window_completed", 0)) <= 0
                    for model_id in EXPECTED_MODELS[:-1]
                ):
                    errors.append("hot_non_hot_progress")
        comparison_cells = (
            "balanced-serial",
            "balanced-concurrent-batch-off",
            "balanced-concurrent-batch-on",
        )
        comparison_rates = {
            cell_id: [
                float(dict(run.get("metrics", {})).get("actual_offered_rps", float("nan")))
                for run in runs
                if run.get("cell_id") == cell_id
            ]
            for cell_id in comparison_cells
        }
        tolerance_rps = config.offered_rps * config.matched_load_relative_tolerance
        if any(
            not rates
            or not all(math.isfinite(rate) for rate in rates)
            or max(rates) - min(rates) > tolerance_rps
            for rates in comparison_rates.values()
        ):
            errors.append("matched_load_repetition_tolerance")
        if all(comparison_rates.values()):
            comparison_medians = [statistics.median(rates) for rates in comparison_rates.values()]
            if max(comparison_medians) - min(comparison_medians) > tolerance_rps:
                errors.append("matched_load_median_tolerance")
    cleanup = payload.get("cleanup", {})
    if status == "complete":
        required_cleanup_true = (
            "container_absent",
            "ports_absent",
            "gpu_lease_absent",
            "b0_identity_restored",
            "b0_cuda_restored",
            "queue_active_zero",
            "queue_leased_zero",
            "queue_outcome_unknown_zero",
            "prometheus_5_of_5",
            "prometheus_exact_jobs_restored",
        )
        if (
            not isinstance(cleanup, Mapping)
            or any(cleanup.get(key) is not True for key in required_cleanup_true)
            or cleanup.get("triton_gpu_process_residue") != []
            or cleanup.get("errors") != []
        ):
            errors.append("cleanup")
        cleanup_evidence = dict(payload.get("cleanup_evidence", {}))
        if (
            not cleanup_evidence.get("path")
            or int(cleanup_evidence.get("bytes", 0)) <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(cleanup_evidence.get("sha256") or ""))
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(cleanup_evidence.get("final_checks_sha256") or "")
            )
        ):
            errors.append("cleanup_evidence")
        profiler = payload.get("profiler", {})
        if not isinstance(profiler, Mapping) or profiler.get("kernel_overlap_proved") is not False:
            errors.append("profiler_claim_boundary")
    if errors:
        raise X1ResumeTestbedError("x1_resume_evidence_invalid:" + ",".join(sorted(set(errors))))
    result = {
        "valid": True,
        "status": status,
        "claim_class": CLAIM_CLASS,
        "credit": CREDIT,
        "q0_count": len(q0),
        "physical_run_count": len(runs),
        "expected_physical_run_count": config.expected_physical_runs,
    }
    private_paths = (private_suite_root, model_repository_root, source_root)
    if any(value is not None for value in private_paths):
        if any(value is None for value in private_paths):
            raise X1ResumeTestbedError("x1_resume_private_validation_paths_incomplete")
        result.update(
            validate_private_evidence(
                payload,
                config=config,
                private_suite_root=private_suite_root,
                model_repository_root=model_repository_root,
                source_root=source_root,
            )
        )
    return result


def generate_report(
    payload: Mapping[str, Any],
    config: X1ResumeConfig,
    *,
    private_suite_root: Path,
    model_repository_root: Path,
    source_root: Path,
) -> dict[str, Any]:
    validation = validate_evidence(
        payload,
        config,
        private_suite_root=private_suite_root,
        model_repository_root=model_repository_root,
        source_root=source_root,
    )
    if validation["status"] != "complete":
        raise X1ResumeTestbedError("x1_resume_report_requires_complete_evidence")
    runs = list(payload["runs"])

    def summarize_distribution(values: Sequence[float]) -> dict[str, Any]:
        return {
            "n": len(values),
            "median": statistics.median(values),
            "min": min(values),
            "max": max(values),
        }

    def distribution_for(cell_id: str, field: str) -> dict[str, Any]:
        values = [
            float(dict(item["metrics"])[field]) for item in runs if item["cell_id"] == cell_id
        ]
        return summarize_distribution(values)

    serial_distribution = distribution_for("balanced-serial", "throughput_rps")
    concurrent_distribution = distribution_for("balanced-concurrent-batch-off", "throughput_rps")
    batch_on_distribution = distribution_for("balanced-concurrent-batch-on", "throughput_rps")
    offered_load_distributions = {
        cell_id: distribution_for(cell_id, "actual_offered_rps")
        for cell_id in (
            "balanced-serial",
            "balanced-concurrent-batch-off",
            "balanced-concurrent-batch-on",
        )
    }
    serial = float(serial_distribution["median"])
    concurrent = float(concurrent_distribution["median"])
    batch_on = float(batch_on_distribution["median"])
    batch_on_runs = [item for item in runs if item["cell_id"] == "balanced-concurrent-batch-on"]
    batch_size_distribution = summarize_distribution(
        [float(item["batching_proof"]["formed_mean_batch_size"]) for item in batch_on_runs]
    )
    batch_on_mean_size = float(batch_size_distribution["median"])
    hot_runs = [item for item in runs if item["cell_id"] == "hot-dlrm-l2w4"]
    hot_fairness_distribution = summarize_distribution(
        [float(item["metrics"]["normalized_attainment_jain_fairness"]) for item in hot_runs]
    )
    hot_fairness = float(hot_fairness_distribution["median"])
    concurrent_delta = ((concurrent / serial) - 1.0) * 100 if serial > 0 else 0.0
    batching_delta = ((batch_on / concurrent) - 1.0) * 100 if concurrent > 0 else 0.0
    service_errors = sum(
        int(item["metrics"]["admitted_cohort_http_5xx"])
        + int(item["metrics"]["admitted_cohort_other_errors"])
        + int(item["metrics"]["loss"])
        for item in runs
    )
    admission_rejections = sum(int(item["metrics"]["local_admission_rejected"]) for item in runs)
    bullet = (
        "Built and measured a preliminary single-node Triton/RTX 4080 testbed for four "
        f"HIGGS/Criteo-derived CUDA models across {len(runs)} physical runs; fixed-window balanced "
        f"concurrent throughput was n=3 median {concurrent:.2f} req/s "
        f"[{concurrent_distribution['min']:.2f}, {concurrent_distribution['max']:.2f}] "
        f"({concurrent_delta:+.1f}% vs serial median); batch-on throughput was n=3 median "
        f"{batch_on:.2f} req/s [{batch_on_distribution['min']:.2f}, "
        f"{batch_on_distribution['max']:.2f}] ({batching_delta:+.1f}% vs batch-off) and formed "
        f"mean batch size median {batch_on_mean_size:.2f} "
        f"[{batch_size_distribution['min']:.2f}, {batch_size_distribution['max']:.2f}]; "
        f"and the 70% hot-model mix observed n=3 median normalized-attainment Jain fairness "
        f"{hot_fairness:.3f} [{hot_fairness_distribution['min']:.3f}, "
        f"{hot_fairness_distribution['max']:.3f}]; "
        f"service errors/loss={service_errors}, local admission rejections={admission_rejections}. "
        "The Criteo DLRM-lite path used deterministic seeded test parameters and makes no "
        "training-quality or model-accuracy claim."
    )
    return {
        "schema_version": "evm.s8_v4.x1_resume_report.v1",
        "claim_class": CLAIM_CLASS,
        "credit": CREDIT,
        "evidence_suite_id": payload.get("suite_id"),
        "measured": {
            "physical_runs": len(runs),
            "throughput_scope": "fixed 30-second measurement-window completions",
            "offered_load_contract": {
                "target_rps": config.offered_rps,
                "minimum_attainment": config.minimum_offered_rate_attainment,
                "matched_relative_tolerance": config.matched_load_relative_tolerance,
                "comparison_distributions": offered_load_distributions,
            },
            "serial_throughput_rps": serial_distribution,
            "concurrent_throughput_rps": concurrent_distribution,
            "concurrent_vs_serial_percent": concurrent_delta,
            "batch_on_throughput_rps": batch_on_distribution,
            "batch_on_vs_off_percent": batching_delta,
            "batch_on_formed_mean_batch_size": batch_size_distribution,
            "hot_mix_normalized_attainment_jain_fairness": hot_fairness_distribution,
            "service_errors_or_loss": service_errors,
            "local_admission_rejections": admission_rejections,
            "criteo_dlrm_lite_parameter_origin": "deterministic_seeded_testbed_initialization",
            "model_accuracy_claim": False,
            "topology_comparison_scope": "compound client-driver L1W1-to-L2W4 topology; not deployed API replica or service-worker causality",
        },
        "resume_bullets": [bullet],
        "mandatory_disclosure": config.claim_boundary,
    }
