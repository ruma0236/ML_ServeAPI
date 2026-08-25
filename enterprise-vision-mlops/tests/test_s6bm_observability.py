from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from evm.scale_validation.s6bm_observability import (
    S6BMObservabilityError,
    attempt_span_count,
    collect_attempt_trace_export,
    direct_metric_aggregate,
    find_triton_compute_start,
    model_lifecycle_counter_delta,
    prometheus_value,
    project_drain_trace_timeline,
    project_trace_join,
)
from evm.scale_validation.s6bm_runtime import S6BMConfig, SUCCESS_PHASES


REVISION = "a" * 40
ATTEMPT_ID = "s6bm-success-1-unit"
RUN_ID = "s8-v4-s6bm-unit"
CONFIG = Path(__file__).resolve().parents[1] / "configs/s8_v4_s6bm_blue_green_v1.toml"


def _attributes(values: dict[str, str | int | bool]) -> list[dict[str, object]]:
    result = []
    for key, value in values.items():
        kind = (
            "boolValue"
            if isinstance(value, bool)
            else "intValue"
            if isinstance(value, int)
            else "stringValue"
        )
        result.append({"key": key, "value": {kind: value}})
    return result


def _entry(
    *,
    trace_id: str,
    span_id: str,
    parent_span_id: str,
    name: str,
    start: int,
    end: int,
    attributes: dict[str, str | int | bool],
) -> dict[str, object]:
    return {
        "resource": {
            "attributes": _attributes(
                {"service.name": "evm-s8-v4-s6bm-api", "service.version": REVISION}
            )
        },
        "scope": {"name": "evm.observability"},
        "span": {
            "traceId": trace_id,
            "spanId": span_id,
            "parentSpanId": parent_span_id,
            "name": name,
            "startTimeUnixNano": str(start),
            "endTimeUnixNano": str(end),
            "attributes": _attributes(attributes),
        },
    }


def _record_and_export() -> tuple[dict[str, object], dict[str, object]]:
    trace_id = "1" * 32
    effect_id = "e" * 64
    parent_id = "2" * 16
    identity = {
        "model_role": "blue",
        "model_name": "s6bm_blue",
        "model_version": "1",
        "artifact_sha256": "b" * 64,
    }
    record: dict[str, object] = {
        "run_id": RUN_ID,
        "attempt_id": ATTEMPT_ID,
        "request_id": "request-0001",
        "trace_id": trace_id,
        "effect_id": effect_id,
        "offered_traceparent": f"00-{trace_id}-{parent_id}-01",
        "offered_identity": identity,
        **identity,
    }
    bound = {
        "evm.run.id": RUN_ID,
        "evm.attempt.id": ATTEMPT_ID,
        "evm.request.id": "request-0001",
        "evm.model.role": "blue",
        "evm.model.name": "s6bm_blue",
        "evm.model.version": "1",
        "evm.model.artifact.sha256": "b" * 64,
        "evm.effect.id": effect_id,
        "evm.terminal.outcome": "completed",
    }
    entries = [
        _entry(
            trace_id=trace_id,
            span_id="3" * 16,
            parent_span_id=parent_id,
            name="POST /control-panel/v1/scenario-workloads/triton-blue-green/predict",
            start=100,
            end=600,
            attributes={
                **bound,
                "evm.stage": "api",
                "evm.request.replayed": False,
                "http.response.status_code": 200,
            },
        ),
        _entry(
            trace_id=trace_id,
            span_id="4" * 16,
            parent_span_id="3" * 16,
            name="s6bm.controller.predict",
            start=200,
            end=500,
            attributes={**bound, "evm.stage": "s6bm_controller"},
        ),
        _entry(
            trace_id=trace_id,
            span_id="5" * 16,
            parent_span_id="4" * 16,
            name="s6bm.triton.infer",
            start=300,
            end=400,
            attributes={**bound, "evm.stage": "triton_inference"},
        ),
    ]
    export = {
        "schema_version": "evm.s8_v4.s6bm_raw_otlp_export.v1",
        "attempt_id": ATTEMPT_ID,
        "trace_count": 1,
        "span_count": 3,
        "entries": entries,
    }
    return record, export


def test_trace_join_recomputes_request_effect_and_topology() -> None:
    record, export = _record_and_export()
    result = project_trace_join(
        export,
        [record],
        attempt_id=ATTEMPT_ID,
        run_id=RUN_ID,
        source_revision=REVISION,
    )

    assert result["request_trace_effect_bound"] == 1
    assert result["unique_effect_count"] == 1
    assert result["topology_complete"] is True


def test_drain_trace_timeline_binds_hold_effect_and_pre_unload_gate() -> None:
    record, export = _record_and_export()
    record.update(
        attempted_monotonic=10.0,
        completed_monotonic=11.3,
        elapsed_ms=1300.0,
        model_role="blue",
    )
    entries = export["entries"]  # type: ignore[assignment]
    timings = (
        (1_000_000_000, 2_300_000_000),
        (1_010_000_000, 2_290_000_000),
        (1_020_000_000, 1_030_000_000),
    )
    for entry, (start, end) in zip(entries, timings, strict=True):
        entry["span"]["startTimeUnixNano"] = str(start)
        entry["span"]["endTimeUnixNano"] = str(end)
    raw = {
        "phase_timeline": [
            {"phase": phase, "monotonic_seconds": timestamp}
            for phase, timestamp in zip(
                SUCCESS_PHASES,
                (9.0, 9.5, 9.8, 10.5, 10.6, 12.0, 13.0, 14.0, 15.0, 16.0),
                strict=True,
            )
        ],
        "request_records": [record],
        "blue_in_flight_before_unload": 0,
    }
    snapshot = {"captured_at": "1970-01-01T00:00:03Z"}
    config = S6BMConfig.from_path(CONFIG)

    result = project_drain_trace_timeline(export, raw, config, snapshot)

    assert result["hold_trace_effect_bound"] == 1
    assert result["blue_in_flight_at_switch"] == 1
    assert result["blue_in_flight_at_pre_unload_gate"] == 0

    mutated = copy.deepcopy(export)
    for entry in mutated["entries"]:  # type: ignore[index]
        entry["span"]["startTimeUnixNano"] = str(
            int(entry["span"]["startTimeUnixNano"]) + 2_000_000_000
        )
        entry["span"]["endTimeUnixNano"] = str(
            int(entry["span"]["endTimeUnixNano"]) + 2_000_000_000
        )
    with pytest.raises(S6BMObservabilityError, match="s6bm_drain_effect_after_pre_unload_gate"):
        project_drain_trace_timeline(mutated, raw, config, snapshot)


def test_trace_join_distinguishes_idempotent_replay_attempt_from_effect() -> None:
    record, export = _record_and_export()
    replay = copy.deepcopy(export["entries"][0])  # type: ignore[index]
    replay["span"]["spanId"] = "6" * 16  # type: ignore[index]
    for attribute in replay["span"]["attributes"]:  # type: ignore[index]
        if attribute["key"] == "evm.request.replayed":
            attribute["value"] = {"boolValue": True}
    export["entries"].append(replay)  # type: ignore[union-attr]
    export["span_count"] = 4
    replay_record = {**record, "replayed": True}

    result = project_trace_join(
        export,
        [record],
        attempt_id=ATTEMPT_ID,
        run_id=RUN_ID,
        source_revision=REVISION,
        replay_record=replay_record,
    )

    assert result["request_trace_effect_bound"] == 1
    assert result["server_span_count"] == 2
    assert result["replay_server_span_count"] == 1


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("evm.attempt.id", "mixed-attempt", "s6bm_trace_attribute_binding"),
        ("evm.model.version", "2", "s6bm_trace_attribute_binding"),
    ],
)
def test_trace_join_rejects_unbound_attributes(field: str, value: str, reason: str) -> None:
    record, export = _record_and_export()
    candidate = copy.deepcopy(export)
    attributes = candidate["entries"][1]["span"]["attributes"]  # type: ignore[index]
    next(item for item in attributes if item["key"] == field)["value"] = {  # type: ignore[index]
        "stringValue": value
    }

    with pytest.raises(S6BMObservabilityError, match=reason):
        project_trace_join(
            candidate,
            [record],
            attempt_id=ATTEMPT_ID,
            run_id=RUN_ID,
            source_revision=REVISION,
        )


def test_collector_offset_excludes_historical_trace_batches(tmp_path: Path) -> None:
    _record, export = _record_and_export()
    old = {
        "resourceSpans": [
            {
                "resource": export["entries"][0]["resource"],  # type: ignore[index]
                "scopeSpans": [
                    {
                        "scope": {},
                        "spans": [export["entries"][0]["span"]],  # type: ignore[index]
                    }
                ],
            }
        ]
    }
    path = tmp_path / "traces.json"
    path.write_text(json.dumps(old) + "\n", encoding="utf-8", newline="\n")
    offset = path.stat().st_size
    entries = export["entries"]  # type: ignore[assignment]
    new = {
        "resourceSpans": [
            {
                "resource": entries[1]["resource"],
                "scopeSpans": [
                    {
                        "scope": {},
                        "spans": [entries[1]["span"], entries[2]["span"]],
                    }
                ],
            }
        ]
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(new) + "\n")

    assert attempt_span_count(path, ATTEMPT_ID, stage="s6bm_controller", start_offset=offset) == 1
    collected = collect_attempt_trace_export(path, ATTEMPT_ID, start_offset=offset)
    assert collected["span_count"] == 2
    assert collected["collector_start_offset"] == offset


def test_collector_ignores_only_concurrent_partial_tail(tmp_path: Path) -> None:
    _record, export = _record_and_export()
    entry = export["entries"][1]  # type: ignore[index]
    complete = {
        "resourceSpans": [
            {
                "resource": entry["resource"],
                "scopeSpans": [
                    {
                        "scope": {},
                        "spans": [entry["span"]],
                    }
                ],
            }
        ]
    }
    path = tmp_path / "traces.json"
    path.write_bytes(
        json.dumps(complete).encode("utf-8") + b"\n" + b'{"resourceSpans":[{"resource":'
    )

    assert attempt_span_count(path, ATTEMPT_ID, stage="s6bm_controller") == 1


def test_collector_rejects_newline_terminated_malformed_record(tmp_path: Path) -> None:
    path = tmp_path / "traces.json"
    path.write_bytes(b'{"resourceSpans":[]}\nnot-json\n')

    with pytest.raises(S6BMObservabilityError, match="s6bm_trace_collector_json:2"):
        attempt_span_count(path, ATTEMPT_ID, stage="s6bm_controller")


def _write_triton_compute_start(
    path: Path,
    *,
    request_nonce: str = "nonce-blue-causal-0001",
    trace_id: str = "1" * 32,
    service_name: str = "triton-inference-server",
) -> None:
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": _attributes({"service.name": service_name})
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "triton"},
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": "2" * 16,
                                "parentSpanId": "3" * 16,
                                "name": "s6bm_blue",
                                "startTimeUnixNano": "1000000000",
                                "endTimeUnixNano": "1002000000",
                                "attributes": _attributes(
                                    {
                                        "request_id": request_nonce,
                                        "model_name": "s6bm_blue",
                                        "model_version": "1",
                                    }
                                ),
                            },
                            {
                                "traceId": trace_id,
                                "spanId": "4" * 16,
                                "parentSpanId": "2" * 16,
                                "name": "compute",
                                "startTimeUnixNano": "1001000000",
                                "endTimeUnixNano": "1001500000",
                                "events": [
                                    {
                                        "name": "COMPUTE_START",
                                        "timeUnixNano": "1001000000",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8", newline="\n")


def test_triton_compute_start_requires_exact_actor_and_request_identity(tmp_path: Path) -> None:
    path = tmp_path / "triton-otel.jsonl"
    _write_triton_compute_start(path)

    result = find_triton_compute_start(
        path,
        request_nonce="nonce-blue-causal-0001",
        trace_id="1" * 32,
        model_name="s6bm_blue",
        model_version="1",
    )

    assert result is not None
    assert result["compute_start_unix_ns"] == 1_001_000_000
    assert result["span_id"] == "4" * 16
    assert result["model_request_span_id"] == "2" * 16
    assert result["resource"]["service.name"] == "triton-inference-server"
    assert (
        find_triton_compute_start(
            path,
            request_nonce="nonce-blue-causal-9999",
            trace_id="1" * 32,
            model_name="s6bm_blue",
            model_version="1",
        )
        is None
    )

    _write_triton_compute_start(path, service_name="substituted-service")
    with pytest.raises(
        S6BMObservabilityError,
        match="s6bm_triton_trace_resource_identity",
    ):
        find_triton_compute_start(
            path,
            request_nonce="nonce-blue-causal-0001",
            trace_id="1" * 32,
            model_name="s6bm_blue",
            model_version="1",
        )


def test_prometheus_value_aggregates_exact_identity_series() -> None:
    snapshot = {
        "queries": {
            "triton_green_success": {
                "response": {
                    "status": "success",
                    "data": {
                        "result": [
                            {
                                "metric": {
                                    "attempt_id": ATTEMPT_ID,
                                    "model": "s6bm_green",
                                    "version": "1",
                                },
                                "value": [1, "10"],
                            },
                            {
                                "metric": {
                                    "attempt_id": ATTEMPT_ID,
                                    "model": "s6bm_green",
                                    "version": "1",
                                    "gpu_uuid": "GPU-unit",
                                },
                                "value": [1, "20"],
                            },
                        ]
                    },
                }
            }
        }
    }

    assert (
        prometheus_value(
            snapshot,
            "triton_green_success",
            {
                "attempt_id": ATTEMPT_ID,
                "model": "s6bm_green",
                "version": "1",
            },
            expected_series_count=None,
        )
        == 30
    )


def test_direct_metric_aggregate_preserves_series_identity() -> None:
    metrics = (
        "# TYPE nv_inference_request_success counter\n"
        'nv_inference_request_success{model="s6bm_green",version="1"} 10\n'
        'nv_inference_request_success{gpu_uuid="GPU-unit",model="s6bm_green",version="1"} 20\n'
    )

    result = direct_metric_aggregate(
        metrics,
        "nv_inference_request_success",
        {"model": "s6bm_green", "version": "1"},
    )

    assert result["value"] == 30
    assert result["series_count"] == 2
    assert result["series_labels"] == [
        {"gpu_uuid": "GPU-unit", "model": "s6bm_green", "version": "1"},
        {"model": "s6bm_green", "version": "1"},
    ]


def test_prometheus_value_rejects_mixed_identity_series() -> None:
    snapshot = {
        "queries": {
            "triton_green_success": {
                "response": {
                    "status": "success",
                    "data": {
                        "result": [
                            {
                                "metric": {
                                    "attempt_id": ATTEMPT_ID,
                                    "model": "s6bm_green",
                                    "version": "2",
                                },
                                "value": [1, "10"],
                            }
                        ]
                    },
                }
            }
        }
    }

    with pytest.raises(S6BMObservabilityError, match="s6bm_prometheus_identity"):
        prometheus_value(
            snapshot,
            "triton_green_success",
            {
                "attempt_id": ATTEMPT_ID,
                "model": "s6bm_green",
                "version": "1",
            },
            expected_series_count=None,
        )


def test_model_lifecycle_counter_delta_accounts_for_blue_reload_only() -> None:
    assert (
        model_lifecycle_counter_delta("blue", before=750, before_unload=845, after=21, code="unit")
        == 116
    )
    assert (
        model_lifecycle_counter_delta("green", before=0, before_unload=920, after=920, code="unit")
        == 920
    )

    with pytest.raises(S6BMObservabilityError, match="unit_blue_counter_reset_missing"):
        model_lifecycle_counter_delta("blue", before=750, before_unload=845, after=900, code="unit")

    with pytest.raises(S6BMObservabilityError, match="unit_green_counter_changed_after_drain"):
        model_lifecycle_counter_delta("green", before=0, before_unload=920, after=921, code="unit")
