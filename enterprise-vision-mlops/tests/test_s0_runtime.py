from __future__ import annotations

import json
from pathlib import Path

import pytest

from evm.scale_validation.s0_runtime import (
    S0RuntimeError,
    coefficient_of_variation,
    deterministic_sample_selectors,
    execute_fixed_window_requests,
    fixed_window_request_count,
    percentile,
    read_trace_spans,
    request_latency_statistics,
    total_ram_gib,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


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


def test_fixed_window_paces_declared_profile_and_applies_seed() -> None:
    clock = FakeClock()
    observed_selectors: list[int] = []

    def request(selector: int) -> str:
        observed_selectors.append(selector)
        clock.advance(0.25)
        return f"sample-{selector % 7}"

    result = execute_fixed_window_requests(
        target_rps=0.05,
        duration_seconds=60.0,
        request_count=3,
        seed=20260815,
        request=request,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert result.request_start_offsets_seconds == pytest.approx((0.0, 20.0, 40.0))
    assert result.observed_elapsed_seconds == pytest.approx(60.0)
    assert observed_selectors == list(
        deterministic_sample_selectors(seed=20260815, request_count=3)
    )
    assert len(result.sample_ids) == 3


def test_fixed_window_rejects_request_count_that_does_not_match_profile() -> None:
    assert fixed_window_request_count(target_rps=0.05, duration_seconds=60.0) == 3
    with pytest.raises(S0RuntimeError, match="request_count_mismatch"):
        execute_fixed_window_requests(
            target_rps=0.05,
            duration_seconds=60.0,
            request_count=2,
            seed=20260815,
            request=lambda _: "unused",
        )
