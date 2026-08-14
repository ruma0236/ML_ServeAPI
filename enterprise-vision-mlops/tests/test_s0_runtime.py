from __future__ import annotations

import json
from pathlib import Path

import pytest

from evm.scale_validation.s0_runtime import (
    S0RuntimeError,
    coefficient_of_variation,
    percentile,
    read_trace_spans,
    request_latency_statistics,
    total_ram_gib,
)


def test_percentiles_use_deterministic_nearest_rank() -> None:
    values = [0.4, 0.1, 0.3, 0.2]

    assert percentile(values, 0.50) == 0.2
    assert percentile(values, 0.95) == 0.4
    assert request_latency_statistics(values)["p99"] == 0.4


def test_empty_metric_samples_fail_closed() -> None:
    with pytest.raises(S0RuntimeError, match="s0_metric_samples_missing"):
        request_latency_statistics([])


def test_coefficient_of_variation_is_zero_for_identical_controls() -> None:
    assert coefficient_of_variation([2.0, 2.0, 2.0]) == 0.0


def test_host_memory_inventory_is_positive() -> None:
    assert total_ram_gib() > 0


def test_trace_reader_preserves_stage_and_runtime_revision(tmp_path: Path) -> None:
    trace_id = "1" * 32
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "evm-api"}},
                        {"key": "service.version", "value": {"stringValue": "a" * 40}},
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": "2" * 16,
                                "name": "POST /control-panel/v1/lifecycle-runs",
                                "attributes": [
                                    {"key": "evm.stage", "value": {"stringValue": "api"}}
                                ],
                            },
                            {
                                "traceId": "3" * 32,
                                "spanId": "4" * 16,
                                "name": "unrelated",
                                "attributes": [],
                            },
                        ]
                    }
                ],
            }
        ]
    }
    path = tmp_path / "traces.json"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    spans = read_trace_spans(path, trace_id)

    assert spans == [
        {
            "name": "POST /control-panel/v1/lifecycle-runs",
            "span_id": "2" * 16,
            "parent_span_id": None,
            "stage": "api",
            "service_name": "evm-api",
            "service_version": "a" * 40,
            "attributes": {"evm.stage": "api"},
        }
    ]
