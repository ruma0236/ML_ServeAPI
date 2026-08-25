from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from prometheus_client.parser import text_string_to_metric_families


TRACE_ID = re.compile(r"^[a-f0-9]{32}$")
SPAN_ID = re.compile(r"^[a-f0-9]{16}$")
PREDICT_ROUTE = "POST /control-panel/v1/scenario-workloads/triton-blue-green/predict"
REQUIRED_ARTIFACTS = {
    "trace_export",
    "api_metrics_before",
    "api_metrics_after",
    "triton_metrics_before",
    "triton_metrics_after",
    "prometheus_before",
    "prometheus_after",
    "join_report",
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
        if start_offset < 0 or start_offset > path.stat().st_size:
            raise S6BMObservabilityError("s6bm_trace_collector_offset")
        if start_offset:
            handle.seek(start_offset - 1)
            if handle.read(1) != b"\n":
                handle.readline()
        else:
            handle.seek(0)
        for line_number, raw_line in enumerate(handle, start=1):
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
        server = _require_one(
            [
                item
                for item in candidates
                if item["name"] == PREDICT_ROUTE and item["attributes"].get("evm.stage") == "api"
            ],
            "s6bm_trace_server_span",
        )
        controller = _require_one(
            [
                item
                for item in candidates
                if item["name"] == "s6bm.controller.predict"
                and item["attributes"].get("evm.stage") == "s6bm_controller"
            ],
            "s6bm_trace_controller_span",
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
        for span in (controller, inference):
            if any(span["attributes"].get(key) != value for key, value in expected.items()):
                raise S6BMObservabilityError("s6bm_trace_attribute_binding")
            if span["resource"].get("service.version") != source_revision:
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
        "controller_span_count": sum(item["name"] == "s6bm.controller.predict" for item in spans),
        "triton_client_span_count": sum(item["name"] == "s6bm.triton.infer" for item in spans),
        "topology_complete": bound == len(records),
    }


def _metric_samples(text: str, name: str) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name == name:
                samples.append({"labels": dict(sample.labels), "value": float(sample.value)})
    return samples


def direct_metric_value(text: str, name: str, labels: Mapping[str, str]) -> float:
    matches = [
        sample
        for sample in _metric_samples(text, name)
        if all(sample["labels"].get(key) == value for key, value in labels.items())
    ]
    if len(matches) != 1:
        raise S6BMObservabilityError(f"s6bm_direct_metric_cardinality:{name}:{len(matches)}")
    return float(matches[0]["value"])


def prometheus_value(
    snapshot: Mapping[str, Any], key: str, expected_labels: Mapping[str, str]
) -> float:
    capture = dict(dict(snapshot.get("queries", {})).get(key, {}))
    response = dict(capture.get("response", {}))
    result = list(dict(response.get("data", {})).get("result", []))
    if response.get("status") != "success" or len(result) != 1:
        raise S6BMObservabilityError(f"s6bm_prometheus_cardinality:{key}:{len(result)}")
    metric = dict(result[0].get("metric", {}))
    if any(metric.get(label) != value for label, value in expected_labels.items()):
        raise S6BMObservabilityError(f"s6bm_prometheus_identity:{key}")
    value = list(result[0].get("value", []))
    if len(value) != 2:
        raise S6BMObservabilityError(f"s6bm_prometheus_value:{key}")
    return float(value[1])


def _require_counter_delta(observed: float, expected: int, code: str) -> int:
    if not math.isfinite(observed) or not math.isclose(
        observed, float(expected), rel_tol=0, abs_tol=1e-9
    ):
        raise S6BMObservabilityError(f"{code}:{observed}:{expected}")
    return expected


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
) -> dict[str, Any]:
    evidence = dict(raw.get("observability", {}))
    artifacts = dict(evidence.get("artifacts", {}))
    expected_artifacts = (
        REQUIRED_ARTIFACTS if compare_projection else REQUIRED_ARTIFACTS - {"join_report"}
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
    )

    api_before = paths["api_metrics_before"].read_text(encoding="utf-8")
    api_after = paths["api_metrics_after"].read_text(encoding="utf-8")
    triton_before = paths["triton_metrics_before"].read_text(encoding="utf-8")
    triton_after = paths["triton_metrics_after"].read_text(encoding="utf-8")
    prom_before = _read_json(paths["prometheus_before"])
    prom_after = _read_json(paths["prometheus_after"])
    target_suite_ids: set[str] = set()
    for snapshot in (prom_before, prom_after):
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

    served = Counter(str(item.get("model_role")) for item in records)
    api_deltas: dict[str, int] = {}
    effect_deltas: dict[str, int] = {}
    triton_deltas: dict[str, int] = {}
    prometheus_api_deltas: dict[str, int] = {}
    prometheus_effect_deltas: dict[str, int] = {}
    prometheus_triton_deltas: dict[str, int] = {}
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
        api_delta = direct_metric_value(
            api_after, "evm_s6bm_requests_total", api_labels
        ) - direct_metric_value(api_before, "evm_s6bm_requests_total", api_labels)
        effect_delta = direct_metric_value(
            api_after, "evm_s6bm_terminal_effects_total", effect_labels
        ) - direct_metric_value(api_before, "evm_s6bm_terminal_effects_total", effect_labels)
        triton_delta = direct_metric_value(
            triton_after, "nv_inference_request_success", triton_labels
        ) - direct_metric_value(triton_before, "nv_inference_request_success", triton_labels)
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

        prom_identity = {
            "attempt_id": attempt_id,
            "scenario": "s8-v4-s6bm",
        }
        api_key = f"api_{role}_completed"
        effect_key = f"api_{role}_effect"
        triton_key = f"triton_{role}_success"
        prom_api_delta = prometheus_value(
            prom_after, api_key, {**prom_identity, **api_labels}
        ) - prometheus_value(prom_before, api_key, {**prom_identity, **api_labels})
        prom_effect_delta = prometheus_value(
            prom_after, effect_key, {**prom_identity, **effect_labels}
        ) - prometheus_value(prom_before, effect_key, {**prom_identity, **effect_labels})
        prom_triton_delta = prometheus_value(
            prom_after,
            triton_key,
            {**prom_identity, "model": model.model_name, "version": model.model_version},
        ) - prometheus_value(
            prom_before,
            triton_key,
            {**prom_identity, "model": model.model_name, "version": model.model_version},
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
        "auxiliary_inferences_by_role": auxiliary,
        "trace_correlation_complete": trace["request_trace_effect_bound"] == len(records),
        "metric_delta_complete": sum(api_deltas.values()) == len(records),
    }
    if compare_projection:
        if _read_json(paths["join_report"]) != summary:
            raise S6BMObservabilityError("s6bm_observability_join_projection")
        if dict(evidence.get("summary", {})) != summary:
            raise S6BMObservabilityError("s6bm_observability_summary_projection")
    return summary
