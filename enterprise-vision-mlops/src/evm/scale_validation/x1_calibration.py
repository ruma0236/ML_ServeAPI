from __future__ import annotations

import math
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from prometheus_client.parser import text_string_to_metric_families

from evm.scale_validation.x1_contract import MODEL_IDS, X1Contract
from evm.scale_validation.x1_topology import validate_runtime_topology_readback


class X1CalibrationError(RuntimeError):
    pass


_TRACE_ID = re.compile(r"^[a-f0-9]{32}$")


def project_calibration_attempt(raw: Mapping[str, Any], contract: X1Contract) -> dict[str, Any]:
    contract.assert_unchanged()
    attempt_id = _string(raw, "attempt_id")
    model_id = raw.get("model_id")
    if model_id is not None and model_id not in MODEL_IDS:
        raise X1CalibrationError("x1_calibration_model_id")
    repetition = _integer(raw, "repetition")
    if repetition not in {1, 2, 3}:
        raise X1CalibrationError("x1_calibration_repetition")
    topology = _mapping(raw, "topology")
    expected_replicas = _integer(topology, "api_replicas")
    expected_workers = _integer(topology, "cpu_workers")
    validate_runtime_topology_readback(
        _mapping(raw, "topology_readback"),
        expected_replicas=expected_replicas,
        expected_workers=expected_workers,
    )
    steps = raw.get("steps")
    expected_steps = list(contract.payload["calibration"]["arrival_steps_rps"])
    if not isinstance(steps, list) or len(steps) != len(expected_steps):
        raise X1CalibrationError("x1_calibration_step_count")
    projections: list[dict[str, Any]] = []
    all_request_ids: set[str] = set()
    for index, step in enumerate(steps):
        if _integer(step, "offered_rps") != expected_steps[index]:
            raise X1CalibrationError("x1_calibration_step_identity")
        runtime_attempt_id = _string(step, "runtime_attempt_id")
        if runtime_attempt_id != f"{attempt_id}-step-{index:02d}":
            raise X1CalibrationError("x1_calibration_runtime_attempt_identity")
        projections.append(
            _project_step(
                step,
                attempt_id=attempt_id,
                runtime_attempt_id=runtime_attempt_id,
                step_index=index,
                model_id=model_id,
                all_request_ids=all_request_ids,
            )
        )
    safe = [
        step
        for step in projections
        if step["error_rate"] <= float(contract.payload["guardrails"]["maximum_error_rate"])
        and step["p99_ms"] <= float(contract.payload["guardrails"]["maximum_p99_ms"])
        and step["lost"] == 0
        and step["duplicate_effects"] == 0
        and step["outcome_unknown"] == 0
        and step["silent_fallback"] == 0
        and step["unexpected_oom"] == 0
    ]
    if not safe:
        raise X1CalibrationError("x1_calibration_no_safe_step")
    selected = safe[-1]
    return {
        "schema_version": "evm.s8_v4.x1_calibration_projection.v1",
        "attempt_id": attempt_id,
        "mode": _string(raw, "mode"),
        "model_id": model_id,
        "topology_id": f"r{expected_replicas}-w{expected_workers}",
        "repetition": repetition,
        "safe_service_rps": selected["service_rps"],
        "gpu_seconds_per_request": selected["gpu_seconds_per_request"],
        "selected_offered_rps": selected["offered_rps"],
        "steps": projections,
        "topology_readback": dict(_mapping(raw, "topology_readback")),
    }


def _project_step(
    step: Mapping[str, Any],
    *,
    attempt_id: str,
    runtime_attempt_id: str,
    step_index: int,
    model_id: str | None,
    all_request_ids: set[str],
) -> dict[str, Any]:
    window = _mapping(step, "measurement_window")
    start_ns = _integer(window, "start_ns")
    end_ns = _integer(window, "end_ns")
    if end_ns <= start_ns:
        raise X1CalibrationError("x1_calibration_window")
    records = step.get("requests")
    if not isinstance(records, list) or not records:
        raise X1CalibrationError("x1_calibration_requests")
    request_ids: set[str] = set()
    accepted: list[Mapping[str, Any]] = []
    rejected = 0
    for sequence, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise X1CalibrationError("x1_calibration_request_schema")
        request_id = _string(record, "request_id")
        if (
            request_id != f"{attempt_id}-s{step_index:02d}-{sequence:08d}"
            or request_id in request_ids
            or request_id in all_request_ids
        ):
            raise X1CalibrationError("x1_calibration_request_identity")
        request_ids.add(request_id)
        all_request_ids.add(request_id)
        enqueued_ns = _integer(record, "enqueued_ns")
        if enqueued_ns < start_ns or enqueued_ns >= end_ns:
            raise X1CalibrationError("x1_calibration_enqueue_window")
        if model_id is not None and record.get("model_id") != model_id:
            raise X1CalibrationError("x1_calibration_request_model")
        outcome = record.get("admission_outcome")
        if outcome == "accepted":
            accepted.append(record)
        elif outcome == "rejected":
            if not _string(record, "rejection_reason"):
                raise X1CalibrationError("x1_calibration_rejection_reason")
            rejected += 1
        else:
            raise X1CalibrationError("x1_calibration_admission_outcome")
    terminal: list[Mapping[str, Any]] = []
    completed: list[Mapping[str, Any]] = []
    measured_completed: list[Mapping[str, Any]] = []
    response_ids: set[str] = set()
    effect_ids: set[str] = set()
    topology_pairs: set[tuple[str, str]] = set()
    latencies: list[float] = []
    queue_waits: list[float] = []
    predictions: list[float] = []
    failures = 0
    silent_fallback = 0
    unexpected_oom = 0
    outcome_unknown = 0
    for record in accepted:
        request_id = _string(record, "request_id")
        status = _integer(record, "status_code")
        terminal_outcome = record.get("terminal_outcome")
        started_ns = _integer(record, "started_ns")
        finished_ns = _integer(record, "finished_ns")
        if started_ns < _integer(record, "enqueued_ns") or finished_ns <= started_ns:
            raise X1CalibrationError("x1_calibration_terminal_timeline")
        if request_id in response_ids:
            raise X1CalibrationError("x1_calibration_duplicate_response")
        response_ids.add(request_id)
        latency = _finite(record, "latency_ms")
        observed_latency = (finished_ns - started_ns) / 1_000_000
        if abs(latency - observed_latency) > 1e-6:
            raise X1CalibrationError("x1_calibration_latency_recompute")
        if terminal_outcome not in {"completed", "failed"}:
            raise X1CalibrationError("x1_calibration_terminal_outcome")
        if record.get("oom_detected") is not False:
            unexpected_oom += 1
        if record.get("outcome_unknown") is not False:
            outcome_unknown += 1
        if status != 200 or terminal_outcome != "completed":
            failures += 1
            terminal.append(record)
            continue
        effect_id = _string(record, "effect_id")
        if effect_id in effect_ids:
            raise X1CalibrationError("x1_calibration_duplicate_effect")
        effect_ids.add(effect_id)
        topology = _mapping(record, "topology")
        topology_pairs.add((_string(topology, "pod_uid"), _string(topology, "worker_slot")))
        if finished_ns <= end_ns:
            latencies.append(latency)
            queue_waits.append(_nonnegative(record, "queue_wait_ms"))
            predictions.append(_nonnegative(record, "prediction_ms"))
            measured_completed.append(record)
        if (
            record.get("runtime_device") != "cuda"
            or record.get("triton_instance_kind") != "KIND_GPU"
            or record.get("triton_instance_count") != 1
            or record.get("triton_gpu_device") != 0
        ):
            silent_fallback += 1
        completed.append(record)
        terminal.append(record)
    effects = step.get("durable_effects")
    if not isinstance(effects, list):
        raise X1CalibrationError("x1_calibration_effect_export")
    effect_by_request: dict[str, Mapping[str, Any]] = {}
    effect_wrappers: dict[str, Mapping[str, Any]] = {}
    for wrapper in effects:
        if not isinstance(wrapper, Mapping):
            raise X1CalibrationError("x1_calibration_effect_schema")
        effect = _mapping(wrapper, "payload")
        request_id = _string(effect, "request_id")
        if request_id in effect_by_request:
            raise X1CalibrationError("x1_calibration_effect_duplicate_request")
        effect_id = _string(effect, "effect_id")
        if (
            wrapper.get("entity_id") != effect_id
            or wrapper.get("state") != "completed"
            or wrapper.get("scope") != f"x1.terminal-effect.{runtime_attempt_id}"
            or wrapper.get("idempotency_key") != request_id
        ):
            raise X1CalibrationError("x1_calibration_effect_wrapper_identity")
        created = _utc_timestamp(wrapper, "entity_created_at")
        updated = _utc_timestamp(wrapper, "entity_updated_at")
        captured = _utc_timestamp(wrapper, "captured_at")
        if created > updated or updated > captured:
            raise X1CalibrationError("x1_calibration_effect_wrapper_timeline")
        effect_by_request[request_id] = effect
        effect_wrappers[request_id] = wrapper
    expected_effect_requests = {_string(record, "request_id") for record in completed}
    if set(effect_by_request) != expected_effect_requests:
        raise X1CalibrationError("x1_calibration_effect_request_set")
    for record in completed:
        effect = effect_by_request.get(_string(record, "request_id"))
        if effect is None or effect.get("effect_id") != record.get("effect_id"):
            raise X1CalibrationError("x1_calibration_effect_join")
        for field in (
            "suite_id",
            "attempt_id",
            "request_id",
            "trace_id",
            "effect_id",
            "model_id",
            "model_version",
            "artifact_sha256",
            "config_sha256",
            "runtime_device",
            "triton_instance_kind",
            "triton_instance_count",
            "triton_gpu_device",
            "result_sha256",
            "terminal_outcome",
            "topology",
        ):
            if effect.get(field) != record.get(field):
                raise X1CalibrationError(f"x1_calibration_effect_identity:{field}")
        if not isinstance(
            effect_wrappers[_string(record, "request_id")].get("request_sha256"), str
        ):
            raise X1CalibrationError("x1_calibration_effect_request_sha")
    _validate_trace_export(
        _mapping(step, "trace_export"),
        completed,
        attempt_id=runtime_attempt_id,
    )
    lost = len(accepted) - len(terminal)
    duplicate_effects = len(effects) - len(expected_effect_requests)
    duration_seconds = (end_ns - start_ns) / 1_000_000_000
    metrics = _project_metrics(
        _mapping(step, "triton_metrics"),
        completed_by_model=Counter(_string(record, "model_id") for record in completed),
    )
    _validate_gpu_samples(step.get("gpu_samples"))
    if not measured_completed:
        raise X1CalibrationError("x1_calibration_terminal_empty")
    return {
        "offered_rps": _integer(step, "offered_rps"),
        "offered": len(records),
        "accepted": len(accepted),
        "rejected": rejected,
        "terminal": len(terminal),
        "completed": len(completed),
        "measured_completed": len(measured_completed),
        "late_terminal": len(completed) - len(measured_completed),
        "lost": lost,
        "duplicate_effects": duplicate_effects,
        "failures": failures,
        "outcome_unknown": outcome_unknown,
        "silent_fallback": silent_fallback,
        "unexpected_oom": unexpected_oom,
        "error_rate": failures / len(accepted) if accepted else 1.0,
        "service_rps": len(measured_completed) / duration_seconds,
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "queue_wait_p99_ms": percentile(queue_waits, 99),
        "prediction_p99_ms": percentile(predictions, 99),
        "formed_batch_mean": metrics["formed_batch_mean"],
        "gpu_seconds_per_request": metrics["gpu_seconds_per_request"],
        "observed_pod_worker_pairs": sorted([list(pair) for pair in topology_pairs]),
    }


def _project_metrics(
    metrics: Mapping[str, Any], *, completed_by_model: Mapping[str, int]
) -> dict[str, float]:
    model_ids = metrics.get("model_ids")
    if (
        not isinstance(model_ids, list)
        or any(type(model_id) is not str for model_id in model_ids)
        or len(model_ids) != len(set(model_ids))
        or not set(model_ids).issubset(set(MODEL_IDS))
        or set(model_ids) != set(completed_by_model)
    ):
        raise X1CalibrationError("x1_calibration_metric_model_set")
    before_raw = metrics.get("before_raw")
    after_raw = metrics.get("after_raw")
    if not isinstance(before_raw, str) or not isinstance(after_raw, str):
        raise X1CalibrationError("x1_calibration_metric_raw")
    before = _metric_snapshot(before_raw, model_ids)
    after = _metric_snapshot(after_raw, model_ids)
    keys = ("success_count", "inference_count", "execution_count", "compute_duration_us")
    delta: dict[str, float] = {}
    for key in keys:
        prior = sum(before[model_id][key] for model_id in model_ids)
        observed = sum(after[model_id][key] for model_id in model_ids)
        if observed < prior:
            raise X1CalibrationError(f"x1_calibration_counter_decrease:{key}")
        delta[key] = observed - prior
    completed = sum(completed_by_model.values())
    for model_id in model_ids:
        success_delta = after[model_id]["success_count"] - before[model_id]["success_count"]
        inference_delta = after[model_id]["inference_count"] - before[model_id]["inference_count"]
        if (
            success_delta != completed_by_model[model_id]
            or inference_delta != completed_by_model[model_id]
        ):
            raise X1CalibrationError(f"x1_calibration_triton_terminal_delta:{model_id}")
    if delta["execution_count"] <= 0 or delta["compute_duration_us"] <= 0:
        raise X1CalibrationError("x1_calibration_triton_activity")
    if delta["execution_count"] > delta["inference_count"]:
        raise X1CalibrationError("x1_calibration_triton_execution_count")
    return {
        "formed_batch_mean": delta["inference_count"] / delta["execution_count"],
        "gpu_seconds_per_request": delta["compute_duration_us"] / max(1, completed) / 1_000_000,
    }


def _metric_snapshot(text: str, model_ids: Sequence[str]) -> dict[str, dict[str, float]]:
    names = {
        "success_count": "nv_inference_request_success",
        "inference_count": "nv_inference_count",
        "execution_count": "nv_inference_exec_count",
        "compute_duration_us": "nv_inference_compute_infer_duration_us",
    }
    samples: list[tuple[str, Mapping[str, str], float]] = []
    try:
        for family in text_string_to_metric_families(text):
            for sample in family.samples:
                samples.append((sample.name, dict(sample.labels), float(sample.value)))
    except ValueError as exc:
        raise X1CalibrationError("x1_calibration_metric_parse") from exc
    result: dict[str, dict[str, float]] = {}
    for model_id in model_ids:
        values: dict[str, float] = {}
        for field, wire_name in names.items():
            accepted_names = {wire_name, f"{wire_name}_total"}
            matches = [
                value
                for name, labels, value in samples
                if name in accepted_names
                and labels.get("model") == model_id
                and labels.get("version") == "1"
            ]
            if len(matches) != 1:
                raise X1CalibrationError(
                    f"x1_calibration_metric_cardinality:{model_id}:{field}:{len(matches)}"
                )
            value = matches[0]
            if not math.isfinite(value) or value < 0:
                raise X1CalibrationError(f"x1_calibration_metric_value:{model_id}:{field}")
            if field != "compute_duration_us" and not value.is_integer():
                raise X1CalibrationError(f"x1_calibration_metric_integer:{model_id}:{field}")
            values[field] = value
        result[model_id] = values
    return result


def _validate_gpu_samples(value: Any) -> None:
    if not isinstance(value, list) or not value:
        raise X1CalibrationError("x1_calibration_gpu_samples")
    for sample in value:
        if not isinstance(sample, Mapping):
            raise X1CalibrationError("x1_calibration_gpu_sample_schema")
        utilization = _nonnegative(sample, "utilization_percent")
        used = _nonnegative(sample, "memory_used_mib")
        total = _finite(sample, "memory_total_mib")
        if utilization > 100 or total <= 0 or used > total:
            raise X1CalibrationError("x1_calibration_gpu_sample_range")
        if not _string(sample, "gpu_uuid") or not _string(sample, "gpu_name"):
            raise X1CalibrationError("x1_calibration_gpu_identity")


def _validate_trace_export(
    export: Mapping[str, Any],
    completed: Sequence[Mapping[str, Any]],
    *,
    attempt_id: str,
) -> None:
    if (
        export.get("schema_version") != "evm.s8_v4.x1_raw_otlp_export.v1"
        or export.get("attempt_id") != attempt_id
    ):
        raise X1CalibrationError("x1_calibration_trace_export_identity")
    entries = export.get("entries")
    if not isinstance(entries, list):
        raise X1CalibrationError("x1_calibration_trace_export_schema")
    indexed: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise X1CalibrationError("x1_calibration_trace_entry_schema")
        span = _mapping(entry, "span")
        attributes = _otlp_attributes(span.get("attributes"))
        request_id = attributes.get("evm.x1.request_id")
        if request_id is None:
            continue
        indexed.setdefault(str(request_id), []).append((entry, attributes))
    expected = {_string(record, "request_id") for record in completed}
    if set(indexed) != expected:
        raise X1CalibrationError("x1_calibration_trace_request_set")
    for record in completed:
        request_id = _string(record, "request_id")
        matches = indexed[request_id]
        if len(matches) != 1:
            raise X1CalibrationError("x1_calibration_trace_cardinality")
        entry, attributes = matches[0]
        span = _mapping(entry, "span")
        trace_id = _string(span, "traceId")
        topology = _mapping(record, "topology")
        resource = _mapping(entry, "resource")
        resource_attributes = _otlp_attributes(resource.get("attributes"))
        if _TRACE_ID.fullmatch(trace_id) is None or trace_id != record.get("trace_id"):
            raise X1CalibrationError("x1_calibration_trace_identity")
        expected_attributes = {
            "evm.x1.suite_id": record.get("suite_id"),
            "evm.x1.attempt_id": attempt_id,
            "evm.x1.request_id": request_id,
            "evm.x1.model_id": record.get("model_id"),
            "evm.x1.model_version": record.get("model_version"),
            "evm.x1.artifact_sha256": record.get("artifact_sha256"),
            "evm.x1.effect_id": record.get("effect_id"),
            "evm.x1.runtime_device": "cuda",
            "evm.x1.pod_uid": topology.get("pod_uid"),
            "evm.terminal.outcome": "completed",
        }
        if any(attributes.get(key) != value for key, value in expected_attributes.items()):
            raise X1CalibrationError("x1_calibration_trace_attribute_join")
        if resource_attributes.get("service.instance.id") != topology.get("service_instance_id"):
            raise X1CalibrationError("x1_calibration_trace_resource_identity")


def _otlp_attributes(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        raise X1CalibrationError("x1_calibration_trace_attributes")
    result: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, Mapping) or not isinstance(item.get("key"), str):
            raise X1CalibrationError("x1_calibration_trace_attribute_schema")
        key = str(item["key"])
        payload = item.get("value")
        if key in result or not isinstance(payload, Mapping):
            raise X1CalibrationError("x1_calibration_trace_attribute_schema")
        present = [
            name
            for name in ("stringValue", "boolValue", "intValue", "doubleValue")
            if name in payload
        ]
        if len(present) != 1:
            raise X1CalibrationError("x1_calibration_trace_attribute_value")
        result[key] = payload[present[0]]
    return result


def _utc_timestamp(value: Mapping[str, Any], key: str) -> datetime:
    observed = _string(value, key)
    try:
        parsed = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    except ValueError as exc:
        raise X1CalibrationError(f"x1_calibration_timestamp:{key}") from exc
    if parsed.tzinfo is None:
        raise X1CalibrationError(f"x1_calibration_timestamp:{key}")
    return parsed.astimezone(UTC)


def percentile(values: Sequence[float], percentile_value: int) -> float:
    if not values or percentile_value < 0 or percentile_value > 100:
        raise X1CalibrationError("x1_calibration_percentile")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile_value / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    observed = value.get(key)
    if not isinstance(observed, Mapping):
        raise X1CalibrationError(f"x1_calibration_mapping:{key}")
    return observed


def _string(value: Mapping[str, Any], key: str) -> str:
    observed = value.get(key)
    if not isinstance(observed, str) or not observed:
        raise X1CalibrationError(f"x1_calibration_string:{key}")
    return observed


def _integer(value: Mapping[str, Any], key: str) -> int:
    observed = value.get(key)
    if type(observed) is not int:
        raise X1CalibrationError(f"x1_calibration_integer:{key}")
    return observed


def _finite(value: Mapping[str, Any], key: str) -> float:
    observed = value.get(key)
    if isinstance(observed, bool) or not isinstance(observed, (int, float)):
        raise X1CalibrationError(f"x1_calibration_numeric:{key}")
    number = float(observed)
    if not math.isfinite(number):
        raise X1CalibrationError(f"x1_calibration_finite:{key}")
    return number


def _nonnegative(value: Mapping[str, Any], key: str) -> float:
    number = _finite(value, key)
    if number < 0:
        raise X1CalibrationError(f"x1_calibration_nonnegative:{key}")
    return number


def summarize_model_distribution(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(str(record.get("model_id")) for record in records)
    if set(counts) - set(MODEL_IDS):
        raise X1CalibrationError("x1_calibration_model_distribution")
    return {model_id: counts.get(model_id, 0) for model_id in MODEL_IDS}
