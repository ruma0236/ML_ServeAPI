from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from prometheus_client.parser import text_string_to_metric_families

from evm.scale_validation.s6bm_runtime import S6BMRuntimeError, project_raw_drain_timeline


TRACE_ID = re.compile(r"^[a-f0-9]{32}$")
SPAN_ID = re.compile(r"^[a-f0-9]{16}$")
PREDICT_ROUTE = "POST /control-panel/v1/scenario-workloads/triton-blue-green/predict"
REQUIRED_ARTIFACTS = {
    "trace_export",
    "api_metrics_before",
    "api_metrics_before_blue_unload",
    "api_metrics_after",
    "triton_metrics_before",
    "triton_metrics_before_blue_unload",
    "triton_metrics_after",
    "prometheus_before",
    "prometheus_before_blue_unload",
    "prometheus_after",
    "join_report",
}
STRICT_V4_REQUIRED_ARTIFACTS = REQUIRED_ARTIFACTS | {
    "triton_metrics_after_blue_unload",
    "prometheus_after_blue_unload",
}


class S6BMObservabilityError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _otlp_value(payload: Mapping[str, Any]) -> Any:
    for key in ("stringValue", "boolValue", "intValue", "doubleValue", "bytesValue"):
        if key in payload:
            return payload[key]
    return None


def _attributes(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {str(item.get("key")): _otlp_value(dict(item.get("value", {}))) for item in items}


def _iter_otlp_spans(path: Path, *, start_offset: int = 0):
    if not path.is_file():
        raise S6BMObservabilityError("s6bm_trace_collector_file_absent")
    with path.open("rb") as handle:
        snapshot_size = path.stat().st_size
        if start_offset < 0 or start_offset > snapshot_size:
            raise S6BMObservabilityError("s6bm_trace_collector_offset")
        if start_offset:
            handle.seek(start_offset - 1)
            if handle.read(1) != b"\n":
                handle.readline()
        else:
            handle.seek(0)
        payload = handle.read(max(0, snapshot_size - handle.tell()))
        lines = payload.splitlines(keepends=True)
        for line_number, raw_line in enumerate(lines, start=1):
            # The file exporter appends concurrently. A snapshot may end in the middle
            # of its final JSON record; only newline-terminated records are committed.
            if line_number == len(lines) and not raw_line.endswith(b"\n"):
                break
            try:
                batch = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise S6BMObservabilityError(f"s6bm_trace_collector_json:{line_number}") from exc
            for resource_spans in batch.get("resourceSpans", []):
                resource = dict(resource_spans.get("resource", {}))
                for scope_spans in resource_spans.get("scopeSpans", []):
                    scope = dict(scope_spans.get("scope", {}))
                    for span in scope_spans.get("spans", []):
                        yield {
                            "resource": resource,
                            "scope": scope,
                            "span": dict(span),
                        }


def attempt_span_count(path: Path, attempt_id: str, *, stage: str, start_offset: int = 0) -> int:
    count = 0
    for entry in _iter_otlp_spans(path, start_offset=start_offset):
        attributes = _attributes(entry["span"].get("attributes", []))
        if attributes.get("evm.attempt.id") == attempt_id and attributes.get("evm.stage") == stage:
            count += 1
    return count


def collect_attempt_trace_export(
    path: Path, attempt_id: str, *, start_offset: int = 0
) -> dict[str, Any]:
    trace_ids: set[str] = set()
    for entry in _iter_otlp_spans(path, start_offset=start_offset):
        attributes = _attributes(entry["span"].get("attributes", []))
        if attributes.get("evm.attempt.id") == attempt_id:
            trace_ids.add(str(entry["span"].get("traceId", "")))
    if not trace_ids:
        raise S6BMObservabilityError("s6bm_trace_attempt_absent")
    entries = [
        entry
        for entry in _iter_otlp_spans(path, start_offset=start_offset)
        if str(entry["span"].get("traceId", "")) in trace_ids
    ]
    return {
        "schema_version": "evm.s8_v4.s6bm_raw_otlp_export.v1",
        "attempt_id": attempt_id,
        "collector_start_offset": start_offset,
        "trace_count": len(trace_ids),
        "span_count": len(entries),
        "entries": entries,
    }


def find_triton_compute_start(
    path: Path,
    *,
    request_nonce: str,
    trace_id: str,
    model_name: str,
    model_version: str,
    start_offset: int = 0,
) -> dict[str, Any] | None:
    """Find one official Triton OTel COMPUTE_START bound to a request nonce."""

    def first(attributes: Mapping[str, Any], names: Sequence[str]) -> str:
        for name in names:
            value = attributes.get(name)
            if value is not None:
                return str(value)
        return ""

    trace_entries = [
        entry
        for entry in _iter_otlp_spans(path, start_offset=start_offset)
        if str(entry["span"].get("traceId", "")) == trace_id
    ]
    model_matches: list[dict[str, Any]] = []
    for entry in trace_entries:
        span = dict(entry["span"])
        attributes = _attributes(span.get("attributes", []))
        resource = _attributes(dict(entry.get("resource", {})).get("attributes", []))
        observed_request = first(attributes, ("request_id", "triton.request_id"))
        observed_model = first(attributes, ("model_name", "triton.model_name"))
        observed_version = first(attributes, ("model_version", "triton.model_version"))
        if (
            observed_request != request_nonce
            or observed_model != model_name
            or observed_version != model_version
        ):
            continue
        if first(resource, ("service.name",)) != "triton-inference-server":
            raise S6BMObservabilityError("s6bm_triton_trace_resource_identity")
        model_matches.append(entry)
    if len(model_matches) > 1:
        raise S6BMObservabilityError(
            f"s6bm_triton_request_span_ambiguous:{len(model_matches)}"
        )
    if not model_matches:
        return None
    model_entry = model_matches[0]
    model_span = dict(model_entry["span"])
    model_span_id = str(model_span.get("spanId", ""))
    compute_matches = [
        entry
        for entry in trace_entries
        if str(entry["span"].get("parentSpanId", "")) == model_span_id
        and str(entry["span"].get("name", "")).lower() == "compute"
    ]
    if len(compute_matches) != 1:
        raise S6BMObservabilityError(
            f"s6bm_triton_compute_span_cardinality:{len(compute_matches)}"
        )
    compute_entry = compute_matches[0]
    compute_span = dict(compute_entry["span"])
    compute_resource = _attributes(
        dict(compute_entry.get("resource", {})).get("attributes", [])
    )
    if first(compute_resource, ("service.name",)) != "triton-inference-server":
        raise S6BMObservabilityError("s6bm_triton_compute_resource_identity")
    events = [
        dict(event)
        for event in compute_span.get("events", [])
        if str(event.get("name", "")).upper() == "COMPUTE_START"
    ]
    if len(events) != 1:
        raise S6BMObservabilityError(f"s6bm_triton_compute_start_event:{len(events)}")
    try:
        started_unix_ns = int(events[0]["timeUnixNano"])
    except (KeyError, TypeError, ValueError) as exc:
        raise S6BMObservabilityError("s6bm_triton_compute_start_timestamp") from exc
    if started_unix_ns <= 0:
        raise S6BMObservabilityError("s6bm_triton_compute_start_timestamp")
    return {
        "schema_version": "evm.s8_v4.s6bm_triton_compute_start.v1",
        "request_nonce": request_nonce,
        "trace_id": trace_id,
        "span_id": str(compute_span.get("spanId", "")),
        "parent_span_id": str(compute_span.get("parentSpanId", "")),
        "model_request_span_id": model_span_id,
        "model_name": model_name,
        "model_version": model_version,
        "compute_start_unix_ns": started_unix_ns,
        "resource": compute_resource,
        "raw_model_entry": model_entry,
        "raw_compute_entry": compute_entry,
    }


def _normalized_span(entry: Mapping[str, Any]) -> dict[str, Any]:
    resource = dict(entry.get("resource", {}))
    span = dict(entry.get("span", {}))
    return {
        "trace_id": str(span.get("traceId", "")),
        "span_id": str(span.get("spanId", "")),
        "parent_span_id": str(span.get("parentSpanId", "")),
        "name": str(span.get("name", "")),
        "start_time_unix_nano": str(span.get("startTimeUnixNano", "")),
        "end_time_unix_nano": str(span.get("endTimeUnixNano", "")),
        "attributes": _attributes(span.get("attributes", [])),
        "resource": _attributes(resource.get("attributes", [])),
    }


def _require_one(spans: Sequence[Mapping[str, Any]], code: str) -> Mapping[str, Any]:
    if len(spans) != 1:
        raise S6BMObservabilityError(f"{code}:{len(spans)}")
    return spans[0]


def project_trace_join(
    trace_export: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    *,
    attempt_id: str,
    run_id: str,
    source_revision: str,
    replay_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if (
        trace_export.get("schema_version") != "evm.s8_v4.s6bm_raw_otlp_export.v1"
        or trace_export.get("attempt_id") != attempt_id
    ):
        raise S6BMObservabilityError("s6bm_trace_export_identity")
    spans = [_normalized_span(item) for item in trace_export.get("entries", [])]
    if int(trace_export.get("span_count", -1)) != len(spans):
        raise S6BMObservabilityError("s6bm_trace_span_count_projection")
    by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for span in spans:
        if (
            TRACE_ID.fullmatch(span["trace_id"]) is None
            or SPAN_ID.fullmatch(span["span_id"]) is None
        ):
            raise S6BMObservabilityError("s6bm_trace_span_identity")
        by_trace[span["trace_id"]].append(span)

    effect_ids: set[str] = set()
    records_by_request = {str(item.get("request_id", "")): item for item in records}
    if len(records_by_request) != len(records):
        raise S6BMObservabilityError("s6bm_trace_request_identity")
    replay_trace_id: str | None = None
    if replay_record:
        replay_request_id = str(replay_record.get("request_id", ""))
        original = records_by_request.get(replay_request_id)
        if (
            original is None
            or replay_record.get("replayed") is not True
            or any(
                replay_record.get(key) != original.get(key)
                for key in (
                    "run_id",
                    "attempt_id",
                    "trace_id",
                    "effect_id",
                    "model_role",
                    "model_name",
                    "model_version",
                    "artifact_sha256",
                )
            )
        ):
            raise S6BMObservabilityError("s6bm_trace_replay_identity")
        replay_trace_id = str(replay_record.get("trace_id", ""))
    bound = 0
    for record in records:
        if record.get("attempt_id") != attempt_id or record.get("run_id") != run_id:
            raise S6BMObservabilityError("s6bm_trace_record_attempt")
        trace_id = str(record.get("trace_id", ""))
        traceparent = str(record.get("offered_traceparent", ""))
        parts = traceparent.split("-")
        if len(parts) != 4 or parts[1] != trace_id or SPAN_ID.fullmatch(parts[2]) is None:
            raise S6BMObservabilityError("s6bm_traceparent_binding")
        candidates = by_trace.get(trace_id, [])
        servers = [
            item
            for item in candidates
            if item["name"] == PREDICT_ROUTE and item["attributes"].get("evm.stage") == "api"
        ]
        expected_server_count = 2 if trace_id == replay_trace_id else 1
        if len(servers) != expected_server_count:
            raise S6BMObservabilityError(f"s6bm_trace_server_span:{len(servers)}")
        server = _require_one(
            [item for item in servers if item["attributes"].get("evm.request.replayed") is False],
            "s6bm_trace_original_server_span",
        )
        replay_servers = [
            item for item in servers if item["attributes"].get("evm.request.replayed") is True
        ]
        if len(replay_servers) != (1 if trace_id == replay_trace_id else 0):
            raise S6BMObservabilityError(f"s6bm_trace_replay_server_span:{len(replay_servers)}")
        controllers = [
            item
            for item in candidates
            if item["name"] == "s6bm.controller.predict"
            and item["attributes"].get("evm.stage") == "s6bm_controller"
        ]
        expected_controller_count = 2 if trace_id == replay_trace_id else 1
        if len(controllers) != expected_controller_count:
            raise S6BMObservabilityError(f"s6bm_trace_controller_span:{len(controllers)}")
        controller = _require_one(
            [item for item in controllers if item["attributes"].get("evm.request.replayed") is False],
            "s6bm_trace_original_controller_span",
        )
        replay_controllers = [
            item for item in controllers if item["attributes"].get("evm.request.replayed") is True
        ]
        if len(replay_controllers) != (1 if trace_id == replay_trace_id else 0):
            raise S6BMObservabilityError(
                f"s6bm_trace_replay_controller_span:{len(replay_controllers)}"
            )
        inference = _require_one(
            [
                item
                for item in candidates
                if item["name"] == "s6bm.triton.infer"
                and item["attributes"].get("evm.stage") == "triton_inference"
            ],
            "s6bm_trace_inference_span",
        )
        if (
            server["parent_span_id"] != parts[2]
            or controller["parent_span_id"] != server["span_id"]
            or inference["parent_span_id"] != controller["span_id"]
        ):
            raise S6BMObservabilityError("s6bm_trace_topology")
        try:
            server_start = int(server["start_time_unix_nano"])
            server_end = int(server["end_time_unix_nano"])
            controller_start = int(controller["start_time_unix_nano"])
            controller_end = int(controller["end_time_unix_nano"])
            inference_start = int(inference["start_time_unix_nano"])
            inference_end = int(inference["end_time_unix_nano"])
        except ValueError as exc:
            raise S6BMObservabilityError("s6bm_trace_timestamp") from exc
        if not (
            0
            < server_start
            <= controller_start
            <= inference_start
            <= inference_end
            <= controller_end
            <= server_end
        ):
            raise S6BMObservabilityError("s6bm_trace_timestamp_order")
        if int(server["attributes"].get("http.response.status_code", 0)) != 200:
            raise S6BMObservabilityError("s6bm_trace_server_outcome")
        expected = {
            "evm.run.id": run_id,
            "evm.attempt.id": attempt_id,
            "evm.request.id": record.get("request_id"),
            "evm.model.role": record.get("model_role"),
            "evm.model.name": record.get("model_name"),
            "evm.model.version": record.get("model_version"),
            "evm.model.artifact.sha256": record.get("artifact_sha256"),
            "evm.effect.id": record.get("effect_id"),
            "evm.terminal.outcome": "completed",
        }
        server_expected = {**expected, "evm.request.replayed": False}
        if any(server["attributes"].get(key) != value for key, value in server_expected.items()):
            raise S6BMObservabilityError("s6bm_trace_server_attribute_binding")
        if replay_servers:
            replay_server = replay_servers[0]
            replay_controller = replay_controllers[0]
            replay_expected = {**expected, "evm.request.replayed": True}
            if (
                replay_server["parent_span_id"] != parts[2]
                or int(replay_server["attributes"].get("http.response.status_code", 0)) != 200
                or any(
                    replay_server["attributes"].get(key) != value
                    for key, value in replay_expected.items()
                )
            ):
                raise S6BMObservabilityError("s6bm_trace_replay_binding")
            try:
                replay_server_start = int(replay_server["start_time_unix_nano"])
                replay_server_end = int(replay_server["end_time_unix_nano"])
                replay_controller_start = int(replay_controller["start_time_unix_nano"])
                replay_controller_end = int(replay_controller["end_time_unix_nano"])
            except ValueError as exc:
                raise S6BMObservabilityError("s6bm_trace_replay_timestamp") from exc
            if (
                replay_controller["parent_span_id"] != replay_server["span_id"]
                or not (
                    replay_server_start
                    <= replay_controller_start
                    <= replay_controller_end
                    <= replay_server_end
                )
                or any(
                    replay_controller["attributes"].get(key) != value
                    for key, value in replay_expected.items()
                )
            ):
                raise S6BMObservabilityError("s6bm_trace_replay_controller_binding")
            if replay_controller["resource"].get("service.version") != source_revision:
                raise S6BMObservabilityError("s6bm_trace_source_revision")
        original_expected = {**expected, "evm.request.replayed": False}
        if any(
            controller["attributes"].get(key) != value
            for key, value in original_expected.items()
        ):
            raise S6BMObservabilityError("s6bm_trace_attribute_binding")
        if any(inference["attributes"].get(key) != value for key, value in expected.items()):
                raise S6BMObservabilityError("s6bm_trace_attribute_binding")
        for span in (controller, inference):
            if span["resource"].get("service.version") != source_revision:
                raise S6BMObservabilityError("s6bm_trace_source_revision")
        if server["resource"].get("service.version") != source_revision:
            raise S6BMObservabilityError("s6bm_trace_source_revision")
        effect_id = str(record.get("effect_id", ""))
        if not effect_id or effect_id in effect_ids:
            raise S6BMObservabilityError("s6bm_trace_effect_identity")
        effect_ids.add(effect_id)
        bound += 1
    if set(by_trace) != {str(item.get("trace_id")) for item in records}:
        raise S6BMObservabilityError("s6bm_trace_attempt_mix")
    if int(trace_export.get("trace_count", -1)) != len(by_trace):
        raise S6BMObservabilityError("s6bm_trace_count_projection")
    return {
        "request_count": len(records),
        "trace_count": len(by_trace),
        "request_trace_effect_bound": bound,
        "unique_effect_count": len(effect_ids),
        "server_span_count": sum(item["name"] == PREDICT_ROUTE for item in spans),
        "replay_server_span_count": sum(
            item["name"] == PREDICT_ROUTE and item["attributes"].get("evm.request.replayed") is True
            for item in spans
        ),
        "controller_span_count": sum(item["name"] == "s6bm.controller.predict" for item in spans),
        "replay_controller_span_count": sum(
            item["name"] == "s6bm.controller.predict"
            and item["attributes"].get("evm.request.replayed") is True
            for item in spans
        ),
        "triton_client_span_count": sum(item["name"] == "s6bm.triton.infer" for item in spans),
        "topology_complete": bound == len(records),
    }


def _utc_unix_nano(value: Any, code: str) -> int:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise S6BMObservabilityError(code) from exc
    if parsed.tzinfo is None:
        raise S6BMObservabilityError(code)
    return int(parsed.astimezone(UTC).timestamp() * 1_000_000_000)


def project_drain_trace_timeline(
    trace_export: Mapping[str, Any],
    raw: Mapping[str, Any],
    config: Any,
    pre_unload_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the Blue hold/drain timeline across requests, spans, effects, and phases."""
    try:
        raw_projection = project_raw_drain_timeline(raw, config)
    except S6BMRuntimeError as exc:
        raise S6BMObservabilityError(f"s6bm_drain_raw:{exc}") from exc
    records = [dict(item) for item in raw.get("request_records", [])]
    spans = [_normalized_span(item) for item in trace_export.get("entries", [])]
    by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for span in spans:
        by_trace[span["trace_id"]].append(span)

    gate_unix_nano = _utc_unix_nano(
        pre_unload_snapshot.get("captured_at"), "s6bm_drain_pre_unload_timestamp"
    )
    switch = float(raw_projection["green_active_monotonic"])
    unload_completed = float(raw_projection["blue_unload_completed_monotonic"])
    hold_ids = set(raw_projection["hold_request_ids"])
    pre_switch_blue = [
        item
        for item in records
        if item.get("model_role") == "blue"
        and float(item.get("attempted_monotonic", math.inf)) < switch
    ]
    if not pre_switch_blue:
        raise S6BMObservabilityError("s6bm_drain_pre_switch_blue_absent")

    hold_details: list[dict[str, Any]] = []
    gate_monotonic_estimates: list[float] = []
    for record in pre_switch_blue:
        request_id = str(record.get("request_id", ""))
        trace_id = str(record.get("trace_id", ""))
        candidates = by_trace.get(trace_id, [])
        server = _require_one(
            [
                item
                for item in candidates
                if item["name"] == PREDICT_ROUTE
                and item["attributes"].get("evm.request.replayed") is False
            ],
            "s6bm_drain_server_span",
        )
        controller = _require_one(
            [item for item in candidates if item["name"] == "s6bm.controller.predict"],
            "s6bm_drain_controller_span",
        )
        inference = _require_one(
            [item for item in candidates if item["name"] == "s6bm.triton.infer"],
            "s6bm_drain_inference_span",
        )
        try:
            server_start = int(server["start_time_unix_nano"])
            server_end = int(server["end_time_unix_nano"])
            controller_start = int(controller["start_time_unix_nano"])
            controller_end = int(controller["end_time_unix_nano"])
            inference_start = int(inference["start_time_unix_nano"])
            inference_end = int(inference["end_time_unix_nano"])
        except ValueError as exc:
            raise S6BMObservabilityError("s6bm_drain_span_timestamp") from exc
        if not (
            server_start
            <= controller_start
            <= inference_start
            < inference_end
            <= controller_end
            <= server_end
        ):
            raise S6BMObservabilityError("s6bm_drain_span_phase_order")
        attempted = float(record.get("attempted_monotonic", 0.0))
        completed = float(record.get("completed_monotonic", 0.0))
        client_duration_ms = (completed - attempted) * 1000.0
        server_duration_ms = (server_end - server_start) / 1_000_000.0
        if (
            server_duration_ms <= 0
            or server_duration_ms > client_duration_ms + 25.0
            or client_duration_ms - server_duration_ms > 250.0
        ):
            raise S6BMObservabilityError("s6bm_drain_client_server_timeline")
        if any(value > gate_unix_nano for value in (server_end, controller_end, inference_end)):
            raise S6BMObservabilityError("s6bm_drain_effect_after_pre_unload_gate")
        if any(
            span["attributes"].get("evm.request.id") != request_id
            or span["attributes"].get("evm.effect.id") != record.get("effect_id")
            or span["attributes"].get("evm.model.role") != "blue"
            for span in (server, controller, inference)
        ):
            raise S6BMObservabilityError("s6bm_drain_span_effect_binding")

        clock_offset_seconds = server_end / 1_000_000_000.0 - completed
        effect_monotonic = controller_end / 1_000_000_000.0 - clock_offset_seconds
        gate_monotonic = gate_unix_nano / 1_000_000_000.0 - clock_offset_seconds
        gate_monotonic_estimates.append(gate_monotonic)
        if not completed < gate_monotonic <= unload_completed:
            raise S6BMObservabilityError("s6bm_drain_pre_unload_gate_order")

        if request_id in hold_ids:
            if not switch < effect_monotonic <= completed:
                raise S6BMObservabilityError("s6bm_drain_terminal_effect_order")
            if server_duration_ms < float(config.procedure["long_in_flight_hold_ms"]):
                raise S6BMObservabilityError("s6bm_drain_hold_span_duration")
            hold_details.append(
                {
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "effect_id": str(record.get("effect_id", "")),
                    "model_name": str(record.get("model_name", "")),
                    "model_version": str(record.get("model_version", "")),
                    "request_started_monotonic": attempted,
                    "request_completed_monotonic": completed,
                    "server_start_unix_nano": server_start,
                    "server_end_unix_nano": server_end,
                    "terminal_effect_unix_nano": controller_end,
                    "terminal_effect_monotonic_estimate": effect_monotonic,
                    "server_duration_ms": server_duration_ms,
                    "pre_unload_gate_unix_nano": gate_unix_nano,
                    "pre_unload_gate_monotonic_estimate": gate_monotonic,
                }
            )
    if {item["request_id"] for item in hold_details} != hold_ids:
        raise S6BMObservabilityError("s6bm_drain_hold_trace_set")
    if max(float(item["completed_monotonic"]) for item in pre_switch_blue) >= min(
        gate_monotonic_estimates
    ):
        raise S6BMObservabilityError("s6bm_drain_blue_completion_after_gate")

    return {
        **raw_projection,
        "pre_unload_gate_captured_at": str(pre_unload_snapshot["captured_at"]),
        "pre_unload_gate_unix_nano": gate_unix_nano,
        "blue_in_flight_at_pre_unload_gate": 0,
        "hold_trace_effect_bound": len(hold_details),
        "hold_requests": hold_details,
        "unload_contract": (
            "the source-bound runner captures this gate after Blue in-flight reaches zero "
            "and before invoking explicit Blue unload; green_only is the post-unload phase"
        ),
    }


def _metric_samples(text: str, name: str) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    accepted_names = {name}
    if not name.endswith("_total"):
        # prometheus_client normalizes an exposed counter name to a `_total`
        # sample even when Triton publishes the wire name without that suffix.
        accepted_names.add(f"{name}_total")
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name in accepted_names:
                samples.append({"labels": dict(sample.labels), "value": float(sample.value)})
    return samples


def direct_metric_value(text: str, name: str, labels: Mapping[str, str]) -> float:
    return float(direct_metric_sample(text, name, labels)["value"])


def direct_metric_sample(text: str, name: str, labels: Mapping[str, str]) -> dict[str, Any]:
    matches = [
        sample
        for sample in _metric_samples(text, name)
        if all(sample["labels"].get(key) == value for key, value in labels.items())
    ]
    if len(matches) != 1:
        raise S6BMObservabilityError(f"s6bm_direct_metric_cardinality:{name}:{len(matches)}")
    return matches[0]


def direct_metric_aggregate(text: str, name: str, labels: Mapping[str, str]) -> dict[str, Any]:
    aggregate = direct_metric_optional_aggregate(text, name, labels)
    if aggregate is None:
        raise S6BMObservabilityError(f"s6bm_direct_metric_aggregate:{name}:0")
    return aggregate


def direct_metric_optional_aggregate(
    text: str,
    name: str,
    labels: Mapping[str, str],
) -> dict[str, Any] | None:
    matches = [
        sample
        for sample in _metric_samples(text, name)
        if all(sample["labels"].get(key) == value for key, value in labels.items())
    ]
    if not matches:
        return None
    if any(not math.isfinite(float(item["value"])) for item in matches):
        raise S6BMObservabilityError(f"s6bm_direct_metric_aggregate:{name}:{len(matches)}")
    return {
        "value": sum(float(item["value"]) for item in matches),
        "series_count": len(matches),
        "series_labels": sorted(
            (dict(item["labels"]) for item in matches),
            key=canonical,
        ),
    }


def prometheus_value(
    snapshot: Mapping[str, Any],
    key: str,
    expected_labels: Mapping[str, str],
    *,
    expected_series_count: int | None = 1,
) -> float:
    value = prometheus_optional_value(
        snapshot,
        key,
        expected_labels,
        expected_series_count=expected_series_count,
    )
    if value is None:
        raise S6BMObservabilityError(f"s6bm_prometheus_cardinality:{key}:0:0")
    return value


def prometheus_optional_value(
    snapshot: Mapping[str, Any],
    key: str,
    expected_labels: Mapping[str, str],
    *,
    expected_series_count: int | None = 1,
) -> float | None:
    capture = dict(dict(snapshot.get("queries", {})).get(key, {}))
    response = dict(capture.get("response", {}))
    result = list(dict(response.get("data", {})).get("result", []))
    if response.get("status") != "success":
        raise S6BMObservabilityError(f"s6bm_prometheus_cardinality:{key}:0:{len(result)}")
    if not result:
        return None
    if expected_series_count is not None and len(result) != expected_series_count:
        raise S6BMObservabilityError(
            f"s6bm_prometheus_cardinality:{key}:{len(result)}:{expected_series_count}"
        )
    values: list[float] = []
    for item in result:
        metric = dict(item.get("metric", {}))
        if any(metric.get(label) != value for label, value in expected_labels.items()):
            raise S6BMObservabilityError(f"s6bm_prometheus_identity:{key}")
        value = list(item.get("value", []))
        if len(value) != 2 or not math.isfinite(float(value[1])):
            raise S6BMObservabilityError(f"s6bm_prometheus_value:{key}")
        values.append(float(value[1]))
    return sum(values)


def _require_counter_delta(observed: float, expected: int, code: str) -> int:
    if not math.isfinite(observed) or not math.isclose(
        observed, float(expected), rel_tol=0, abs_tol=1e-9
    ):
        raise S6BMObservabilityError(f"{code}:{observed}:{expected}")
    return expected


def model_lifecycle_counter_delta(
    role: str,
    *,
    before: float,
    before_unload: float,
    after_unload: float | None = None,
    after: float,
    code: str,
) -> float:
    if any(not math.isfinite(value) or value < 0 for value in (before, before_unload, after)):
        raise S6BMObservabilityError(f"{code}_counter_value")
    if before_unload < before:
        raise S6BMObservabilityError(f"{code}_counter_regressed_before_unload")
    if role == "blue":
        if after_unload is not None:
            if after >= before_unload:
                raise S6BMObservabilityError(f"{code}_blue_counter_reset_missing")
        # Triton resets a model's counter generation on reload. The post-rollback
        # counter may exceed the pre-unload generation after warmup, so compare
        # the two generations independently. Strict V4 proves the unloaded
        # boundary by requiring the Blue series to be absent in both raw sources.
        return before_unload - before + after
    if role == "green":
        if after_unload is None:
            raise S6BMObservabilityError(f"{code}_green_counter_absent_after_blue_unload")
        if not (
            math.isclose(before_unload, after_unload, rel_tol=0.0, abs_tol=1e-9)
            and math.isclose(after_unload, after, rel_tol=0.0, abs_tol=1e-9)
        ):
            raise S6BMObservabilityError(f"{code}_green_counter_changed_after_drain")
        return after - before
    raise S6BMObservabilityError(f"{code}_model_role")


def _resolve_artifact(root: Path, reference: Mapping[str, Any], key: str) -> Path:
    relative = Path(str(reference.get("path", "")))
    if relative.is_absolute() or ".." in relative.parts:
        raise S6BMObservabilityError(f"s6bm_observability_path:{key}")
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise S6BMObservabilityError(f"s6bm_observability_path:{key}") from exc
    if not path.is_file() or sha256_file(path) != reference.get("sha256"):
        raise S6BMObservabilityError(f"s6bm_observability_sha:{key}")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise S6BMObservabilityError(f"s6bm_observability_noncanonical:{path.name}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise S6BMObservabilityError(f"s6bm_observability_object:{path.name}")
    return value


def validate_observability_bundle(
    private_root: Path,
    raw: Mapping[str, Any],
    config: Any,
    *,
    compare_projection: bool = True,
    require_drain_timeline: bool = False,
) -> dict[str, Any]:
    evidence = dict(raw.get("observability", {}))
    artifacts = dict(evidence.get("artifacts", {}))
    required_artifacts = (
        STRICT_V4_REQUIRED_ARTIFACTS
        if str(config.schema_version).endswith((".v3", ".v4"))
        else REQUIRED_ARTIFACTS
    )
    expected_artifacts = (
        required_artifacts if compare_projection else required_artifacts - {"join_report"}
    )
    if set(artifacts) != expected_artifacts:
        raise S6BMObservabilityError("s6bm_observability_artifact_set")
    paths = {
        key: _resolve_artifact(private_root, dict(reference), key)
        for key, reference in artifacts.items()
    }
    trace_export = _read_json(paths["trace_export"])
    records = [dict(item) for item in raw.get("request_records", [])]
    attempt_id = str(raw.get("attempt_id", ""))
    run_id = str(dict(raw.get("identities", {})).get("lease", {}).get("run_id", ""))
    source_revision = str(raw.get("source_revision", ""))
    trace = project_trace_join(
        trace_export,
        records,
        attempt_id=attempt_id,
        run_id=run_id,
        source_revision=source_revision,
        replay_record=dict(dict(raw.get("idempotent_replay", {})).get("record", {})) or None,
    )

    api_before = paths["api_metrics_before"].read_text(encoding="utf-8")
    api_before_blue_unload = paths["api_metrics_before_blue_unload"].read_text(encoding="utf-8")
    api_after = paths["api_metrics_after"].read_text(encoding="utf-8")
    triton_before = paths["triton_metrics_before"].read_text(encoding="utf-8")
    triton_before_blue_unload = paths["triton_metrics_before_blue_unload"].read_text(
        encoding="utf-8"
    )
    triton_after_blue_unload = (
        paths["triton_metrics_after_blue_unload"].read_text(encoding="utf-8")
        if "triton_metrics_after_blue_unload" in paths
        else triton_before_blue_unload
    )
    triton_after = paths["triton_metrics_after"].read_text(encoding="utf-8")
    prom_before = _read_json(paths["prometheus_before"])
    prom_before_blue_unload = _read_json(paths["prometheus_before_blue_unload"])
    prom_after_blue_unload = (
        _read_json(paths["prometheus_after_blue_unload"])
        if "prometheus_after_blue_unload" in paths
        else prom_before_blue_unload
    )
    prom_after = _read_json(paths["prometheus_after"])
    target_suite_ids: set[str] = set()
    for snapshot in (
        prom_before,
        prom_before_blue_unload,
        prom_after_blue_unload,
        prom_after,
    ):
        if snapshot.get("attempt_id") != attempt_id or snapshot.get("run_id") != run_id:
            raise S6BMObservabilityError("s6bm_prometheus_attempt_identity")
        target = dict(snapshot.get("target_identity", {}))
        if (
            target.get("attempt_id") != attempt_id
            or target.get("api_up") is not True
            or target.get("triton_up") is not True
        ):
            raise S6BMObservabilityError("s6bm_prometheus_target_identity")
        suite_id = str(target.get("suite_id", ""))
        labels = [dict(item) for item in target.get("labels", [])]
        if (
            not suite_id
            or len(labels) != 2
            or any(
                item.get("attempt_id") != attempt_id
                or item.get("suite_id") != suite_id
                or item.get("scenario") != "s8-v4-s6bm"
                for item in labels
            )
        ):
            raise S6BMObservabilityError("s6bm_prometheus_target_labels")
        target_suite_ids.add(suite_id)
    if len(target_suite_ids) != 1:
        raise S6BMObservabilityError("s6bm_prometheus_suite_identity")

    drain_timeline = (
        project_drain_trace_timeline(trace_export, raw, config, prom_before_blue_unload)
        if require_drain_timeline
        else None
    )

    served = Counter(str(item.get("model_role")) for item in records)
    api_deltas: dict[str, int] = {}
    effect_deltas: dict[str, int] = {}
    triton_deltas: dict[str, int] = {}
    prometheus_api_deltas: dict[str, int] = {}
    prometheus_effect_deltas: dict[str, int] = {}
    prometheus_triton_deltas: dict[str, int] = {}
    triton_series: dict[str, dict[str, Any]] = {}
    auxiliary = {
        "blue": int(config.procedure["warmup_requests"]) + 1,
        "green": int(config.procedure["warmup_requests"]),
    }
    for role in ("blue", "green"):
        model = config.blue if role == "blue" else config.green
        identity = {
            "model_role": role,
            "model_name": model.model_name,
            "model_version": model.model_version,
        }
        api_labels = {**identity, "outcome": "completed"}
        effect_labels = {**identity, "outcome": "committed"}
        triton_labels = {"model": model.model_name, "version": model.model_version}
        api_before_sample = direct_metric_sample(api_before, "evm_s6bm_requests_total", api_labels)
        api_before_blue_unload_sample = direct_metric_sample(
            api_before_blue_unload, "evm_s6bm_requests_total", api_labels
        )
        api_after_sample = direct_metric_sample(api_after, "evm_s6bm_requests_total", api_labels)
        effect_before_sample = direct_metric_sample(
            api_before, "evm_s6bm_terminal_effects_total", effect_labels
        )
        effect_before_blue_unload_sample = direct_metric_sample(
            api_before_blue_unload,
            "evm_s6bm_terminal_effects_total",
            effect_labels,
        )
        effect_after_sample = direct_metric_sample(
            api_after, "evm_s6bm_terminal_effects_total", effect_labels
        )
        triton_before_aggregate = direct_metric_aggregate(
            triton_before, "nv_inference_request_success", triton_labels
        )
        triton_before_blue_unload_aggregate = direct_metric_aggregate(
            triton_before_blue_unload,
            "nv_inference_request_success",
            triton_labels,
        )
        triton_after_blue_unload_aggregate = direct_metric_optional_aggregate(
            triton_after_blue_unload,
            "nv_inference_request_success",
            triton_labels,
        )
        if str(config.schema_version).endswith((".v3", ".v4")):
            if role == "blue" and triton_after_blue_unload_aggregate is not None:
                raise S6BMObservabilityError("s6bm_triton_blue_present_after_unload")
            if role == "green" and triton_after_blue_unload_aggregate is None:
                raise S6BMObservabilityError("s6bm_triton_green_absent_after_blue_unload")
        elif triton_after_blue_unload_aggregate is None:
            triton_after_blue_unload_aggregate = triton_before_blue_unload_aggregate
        triton_after_aggregate = direct_metric_aggregate(
            triton_after, "nv_inference_request_success", triton_labels
        )
        for before_sample, transition_sample, after_sample, code in (
            (
                api_before_sample,
                api_before_blue_unload_sample,
                api_after_sample,
                "api",
            ),
            (
                effect_before_sample,
                effect_before_blue_unload_sample,
                effect_after_sample,
                "effect",
            ),
        ):
            if not (
                before_sample["labels"] == transition_sample["labels"] == after_sample["labels"]
            ):
                raise S6BMObservabilityError(f"s6bm_direct_metric_identity_changed:{role}:{code}")
        api_delta = float(api_after_sample["value"]) - float(api_before_sample["value"])
        effect_delta = float(effect_after_sample["value"]) - float(effect_before_sample["value"])
        triton_before_value = float(triton_before_aggregate["value"])
        triton_transition_value = float(triton_before_blue_unload_aggregate["value"])
        triton_after_unload_value = (
            None
            if triton_after_blue_unload_aggregate is None
            else float(triton_after_blue_unload_aggregate["value"])
        )
        triton_after_value = float(triton_after_aggregate["value"])
        triton_delta = model_lifecycle_counter_delta(
            role,
            before=triton_before_value,
            before_unload=triton_transition_value,
            after_unload=triton_after_unload_value,
            after=triton_after_value,
            code="s6bm_triton",
        )
        expected_triton = served[role] + auxiliary[role]
        api_deltas[role] = _require_counter_delta(
            api_delta, served[role], f"s6bm_direct_api_delta:{role}"
        )
        effect_deltas[role] = _require_counter_delta(
            effect_delta, served[role], f"s6bm_direct_effect_delta:{role}"
        )
        triton_deltas[role] = _require_counter_delta(
            triton_delta, expected_triton, f"s6bm_direct_triton_delta:{role}"
        )
        triton_series[role] = {
            "before_count": int(triton_before_aggregate["series_count"]),
            "before_blue_unload_count": int(triton_before_blue_unload_aggregate["series_count"]),
            "after_blue_unload_count": (
                0
                if triton_after_blue_unload_aggregate is None
                else int(triton_after_blue_unload_aggregate["series_count"])
            ),
            "after_count": int(triton_after_aggregate["series_count"]),
            "before_labels": triton_before_aggregate["series_labels"],
            "before_blue_unload_labels": triton_before_blue_unload_aggregate["series_labels"],
            "after_blue_unload_labels": (
                []
                if triton_after_blue_unload_aggregate is None
                else triton_after_blue_unload_aggregate["series_labels"]
            ),
            "after_labels": triton_after_aggregate["series_labels"],
        }

        prom_identity = {
            "attempt_id": attempt_id,
            "scenario": "s8-v4-s6bm",
        }
        api_key = f"api_{role}_completed"
        effect_key = f"api_{role}_effect"
        triton_key = f"triton_{role}_success"
        prom_api_delta = prometheus_value(
            prom_after,
            api_key,
            {**prom_identity, **api_after_sample["labels"]},
            expected_series_count=1,
        ) - prometheus_value(
            prom_before,
            api_key,
            {**prom_identity, **api_before_sample["labels"]},
            expected_series_count=1,
        )
        prom_effect_delta = prometheus_value(
            prom_after,
            effect_key,
            {**prom_identity, **effect_after_sample["labels"]},
            expected_series_count=1,
        ) - prometheus_value(
            prom_before,
            effect_key,
            {**prom_identity, **effect_before_sample["labels"]},
            expected_series_count=1,
        )
        prom_triton_after = prometheus_value(
            prom_after,
            triton_key,
            {**prom_identity, **triton_labels},
            expected_series_count=None,
        )
        prom_triton_before = prometheus_value(
            prom_before,
            triton_key,
            {**prom_identity, **triton_labels},
            expected_series_count=None,
        )
        prom_triton_before_blue_unload = prometheus_value(
            prom_before_blue_unload,
            triton_key,
            {**prom_identity, **triton_labels},
            expected_series_count=None,
        )
        prom_triton_after_blue_unload = prometheus_optional_value(
            prom_after_blue_unload,
            triton_key,
            {**prom_identity, **triton_labels},
            expected_series_count=None,
        )
        if str(config.schema_version).endswith((".v3", ".v4")):
            if role == "blue" and prom_triton_after_blue_unload is not None:
                raise S6BMObservabilityError("s6bm_prometheus_blue_present_after_unload")
            if role == "green" and prom_triton_after_blue_unload is None:
                raise S6BMObservabilityError("s6bm_prometheus_green_absent_after_blue_unload")
        elif prom_triton_after_blue_unload is None:
            prom_triton_after_blue_unload = prom_triton_before_blue_unload
        prom_triton_delta = model_lifecycle_counter_delta(
            role,
            before=prom_triton_before,
            before_unload=prom_triton_before_blue_unload,
            after_unload=prom_triton_after_blue_unload,
            after=prom_triton_after,
            code="s6bm_prometheus",
        )
        prometheus_api_deltas[role] = _require_counter_delta(
            prom_api_delta, served[role], f"s6bm_prometheus_api_delta:{role}"
        )
        prometheus_effect_deltas[role] = _require_counter_delta(
            prom_effect_delta,
            served[role],
            f"s6bm_prometheus_effect_delta:{role}",
        )
        prometheus_triton_deltas[role] = _require_counter_delta(
            prom_triton_delta,
            expected_triton,
            f"s6bm_prometheus_triton_delta:{role}",
        )

    summary = {
        "attempt_id": attempt_id,
        "run_id": run_id,
        "accepted_requests": len(records),
        "trace": trace,
        "served_by_role": dict(sorted(served.items())),
        "api_request_delta_by_role": api_deltas,
        "api_effect_delta_by_role": effect_deltas,
        "triton_success_delta_by_role": triton_deltas,
        "prometheus_api_request_delta_by_role": prometheus_api_deltas,
        "prometheus_api_effect_delta_by_role": prometheus_effect_deltas,
        "prometheus_triton_success_delta_by_role": prometheus_triton_deltas,
        "triton_metric_series_by_role": triton_series,
        "auxiliary_inferences_by_role": auxiliary,
        "trace_correlation_complete": trace["request_trace_effect_bound"] == len(records),
        "metric_delta_complete": sum(api_deltas.values()) == len(records),
    }
    if compare_projection:
        if _read_json(paths["join_report"]) != summary:
            raise S6BMObservabilityError("s6bm_observability_join_projection")
        if dict(evidence.get("summary", {})) != summary:
            raise S6BMObservabilityError("s6bm_observability_summary_projection")
    if drain_timeline is None:
        return summary
    return {**summary, "raw_drain_timeline": drain_timeline}
