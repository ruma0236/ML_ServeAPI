from __future__ import annotations

from collections.abc import Iterator

import pytest

from evm.scale_validation.clock_diagnostic import (
    ClockDiagnosticThresholds,
    analyze_clock_window,
    analyze_database_clock_samples,
    analyze_os_clock_samples,
    capture_os_clock_sample,
    validate_transaction_timestamp_semantics,
)


def _source(values: list[int]) -> Iterator[int]:
    yield from values


def _os_samples(
    *,
    domain: str,
    count: int = 4,
    cadence_ns: int = 100_000_000,
    wall_step_sequence: int | None = None,
    scheduler_pause_sequence: int | None = None,
) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    elapsed = 0
    for sequence in range(count):
        if sequence:
            elapsed += cadence_ns
        if sequence == scheduler_pause_sequence:
            elapsed += 500_000_000
        wall = 1_700_000_000_000_000_000 + elapsed
        if wall_step_sequence is not None and sequence >= wall_step_sequence:
            wall += 250_000_000
        samples.append(
            {
                "domain": domain,
                "sequence": sequence,
                "monotonic_before_ns": elapsed,
                "wall_unix_ns": wall,
                "monotonic_after_ns": elapsed + 100_000,
                "boottime_ns": elapsed + 50_000,
            }
        )
    return samples


def _database_samples(
    *,
    count: int = 4,
    cadence_ns: int = 100_000_000,
    wall_step_sequence: int | None = None,
) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    for sequence in range(count):
        midpoint = sequence * cadence_ns
        database_clock = 1_700_000_000_000_000_000 + midpoint
        if wall_step_sequence is not None and sequence >= wall_step_sequence:
            database_clock -= 250_000_000
        samples.append(
            {
                "sequence": sequence,
                "client_monotonic_send_ns": midpoint - 500_000,
                "client_monotonic_receive_ns": midpoint + 500_000,
                "database_clock_unix_ns": database_clock,
                "backend_pid": 42,
            }
        )
    return samples


def _transaction_semantics() -> dict[str, int]:
    return {
        "first_clock_unix_ns": 1_000,
        "first_statement_unix_ns": 1_000,
        "first_transaction_unix_ns": 1_000,
        "first_now_unix_ns": 1_000,
        "second_clock_unix_ns": 2_000,
        "second_statement_unix_ns": 2_000,
        "second_transaction_unix_ns": 1_000,
        "second_now_unix_ns": 1_000,
    }


def test_capture_os_clock_sample_uses_injected_bracketed_time_sources() -> None:
    monotonic = _source([100, 120])
    wall = _source([10_000])
    boottime = _source([500])

    sample = capture_os_clock_sample(
        domain="unit",
        sequence=3,
        monotonic_ns=lambda: next(monotonic),
        wall_ns=lambda: next(wall),
        boottime_ns=lambda: next(boottime),
    )

    assert sample == {
        "domain": "unit",
        "sequence": 3,
        "monotonic_before_ns": 100,
        "wall_unix_ns": 10_000,
        "monotonic_after_ns": 120,
        "boottime_ns": 500,
    }


def test_os_clock_analysis_uses_actual_midpoint_delta_not_nominal_sleep() -> None:
    thresholds = ClockDiagnosticThresholds(sample_count=4)

    analysis = analyze_os_clock_samples(
        _os_samples(domain="windows_host", scheduler_pause_sequence=2),
        thresholds=thresholds,
    )

    assert analysis["passed"] is True
    assert analysis["offset_step_count"] == 0
    assert analysis["max_scheduler_delay_ns"] == 500_000_000


@pytest.mark.parametrize("step_sequence", [1, 2])
def test_os_clock_analysis_rejects_forward_or_backward_wall_step(step_sequence: int) -> None:
    thresholds = ClockDiagnosticThresholds(sample_count=4)
    samples = _os_samples(domain="windows_host", wall_step_sequence=step_sequence)
    if step_sequence == 2:
        for sample in samples[step_sequence:]:
            sample["wall_unix_ns"] = int(sample["wall_unix_ns"]) - 500_000_000

    analysis = analyze_os_clock_samples(samples, thresholds=thresholds)

    assert analysis["passed"] is False
    assert analysis["offset_step_count"] == 1


def test_database_clock_analysis_includes_rtt_uncertainty() -> None:
    thresholds = ClockDiagnosticThresholds(sample_count=4)
    samples = _database_samples(wall_step_sequence=2)

    analysis = analyze_database_clock_samples(samples, thresholds=thresholds)

    assert analysis["passed"] is False
    assert analysis["offset_step_count"] == 1
    assert analysis["offset_steps"][0]["pair_uncertainty_ns"] == 1_000_000


def test_transaction_stable_timestamps_are_not_clock_jump_samples() -> None:
    analysis = validate_transaction_timestamp_semantics(_transaction_semantics())

    assert analysis == {
        "clock_advanced": True,
        "statement_advanced": True,
        "transaction_stable": True,
        "now_stable": True,
        "now_matches_transaction": True,
        "passed": True,
    }


def test_full_clock_window_requires_all_domains_and_rejects_database_step() -> None:
    thresholds = ClockDiagnosticThresholds(sample_count=4)
    analysis = analyze_clock_window(
        os_domains={
            domain: _os_samples(domain=domain)
            for domain in ("windows_host", "wsl_ubuntu", "docker_evm_api")
        },
        database_samples=_database_samples(wall_step_sequence=2),
        transaction_semantics=_transaction_semantics(),
        thresholds=thresholds,
    )

    assert analysis["passed"] is False
    assert analysis["postgresql"]["offset_step_count"] == 1
    assert all(item["passed"] is True for item in analysis["os_domains"].values())
