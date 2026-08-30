from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class ClockDiagnosticThresholds:
    sample_count: int = 1_200
    cadence_ns: int = 100_000_000
    step_threshold_ns: int = 100_000_000
    os_bracket_max_ns: int = 5_000_000
    database_rtt_max_ns: int = 50_000_000
    max_uncertain_fraction: float = 0.05
    suspend_threshold_ns: int = 100_000_000


def capture_os_clock_sample(
    *,
    domain: str,
    sequence: int,
    monotonic_ns: Callable[[], int],
    wall_ns: Callable[[], int],
    boottime_ns: Callable[[], int] | None = None,
) -> dict[str, Any]:
    monotonic_before_ns = monotonic_ns()
    wall_unix_ns = wall_ns()
    monotonic_after_ns = monotonic_ns()
    return {
        "domain": domain,
        "sequence": sequence,
        "monotonic_before_ns": monotonic_before_ns,
        "wall_unix_ns": wall_unix_ns,
        "monotonic_after_ns": monotonic_after_ns,
        "boottime_ns": boottime_ns() if boottime_ns is not None else None,
    }


def _strict_int(value: object, field: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{field} must be an integer")
    return value


def _validate_sequences(samples: Sequence[Mapping[str, Any]], expected_count: int) -> None:
    if len(samples) != expected_count:
        raise ValueError(f"expected {expected_count} samples, observed {len(samples)}")
    sequences = [_strict_int(sample.get("sequence"), "sequence") for sample in samples]
    if sequences != list(range(expected_count)):
        raise ValueError("sample sequences are not the exact contiguous frozen set")


def _offset_steps(
    samples: Sequence[Mapping[str, Any]],
    *,
    midpoint_field: str,
    wall_field: str,
    uncertainty_field: str,
    thresholds: ClockDiagnosticThresholds,
) -> tuple[list[dict[str, int]], list[int], list[int]]:
    offsets: list[int] = []
    midpoints: list[int] = []
    uncertainties: list[int] = []
    for sample in samples:
        midpoint = _strict_int(sample.get(midpoint_field), midpoint_field)
        wall = _strict_int(sample.get(wall_field), wall_field)
        uncertainty = _strict_int(sample.get(uncertainty_field), uncertainty_field)
        if uncertainty < 0:
            raise ValueError(f"{uncertainty_field} must be non-negative")
        midpoints.append(midpoint)
        offsets.append(wall - midpoint)
        uncertainties.append(uncertainty)
    steps: list[dict[str, int]] = []
    scheduler_delays: list[int] = []
    for index in range(1, len(samples)):
        offset_change = offsets[index] - offsets[index - 1]
        pair_uncertainty = uncertainties[index] + uncertainties[index - 1]
        actual_midpoint_delta = midpoints[index] - midpoints[index - 1]
        scheduler_delay = actual_midpoint_delta - thresholds.cadence_ns
        scheduler_delays.append(scheduler_delay)
        if abs(offset_change) > thresholds.step_threshold_ns + pair_uncertainty:
            steps.append(
                {
                    "sequence": index,
                    "offset_change_ns": offset_change,
                    "pair_uncertainty_ns": pair_uncertainty,
                    "actual_midpoint_delta_ns": actual_midpoint_delta,
                    "scheduler_delay_ns": scheduler_delay,
                }
            )
    return steps, offsets, scheduler_delays


def analyze_os_clock_samples(
    samples: Sequence[Mapping[str, Any]],
    *,
    thresholds: ClockDiagnosticThresholds,
) -> dict[str, Any]:
    _validate_sequences(samples, thresholds.sample_count)
    normalized: list[dict[str, Any]] = []
    domain: str | None = None
    uncertain_sequences: list[int] = []
    for sample in samples:
        current_domain = sample.get("domain")
        if not isinstance(current_domain, str) or not current_domain:
            raise ValueError("OS sample domain is missing")
        if domain is None:
            domain = current_domain
        elif domain != current_domain:
            raise ValueError("OS sample domain identity changed")
        before = _strict_int(sample.get("monotonic_before_ns"), "monotonic_before_ns")
        after = _strict_int(sample.get("monotonic_after_ns"), "monotonic_after_ns")
        wall = _strict_int(sample.get("wall_unix_ns"), "wall_unix_ns")
        if after < before:
            raise ValueError("OS monotonic bracket regressed")
        bracket = after - before
        sequence = _strict_int(sample.get("sequence"), "sequence")
        if bracket > thresholds.os_bracket_max_ns:
            uncertain_sequences.append(sequence)
        normalized.append(
            {
                **dict(sample),
                "midpoint_monotonic_ns": (before + after) // 2,
                "wall_unix_ns": wall,
                "uncertainty_ns": (bracket + 1) // 2,
            }
        )
    valid = [sample for sample in normalized if sample["sequence"] not in set(uncertain_sequences)]
    steps, offsets, scheduler_delays = _offset_steps(
        valid,
        midpoint_field="midpoint_monotonic_ns",
        wall_field="wall_unix_ns",
        uncertainty_field="uncertainty_ns",
        thresholds=thresholds,
    )
    suspend_events: list[dict[str, int]] = []
    boottime_pairs = [sample for sample in normalized if sample.get("boottime_ns") is not None]
    for previous, current in zip(boottime_pairs, boottime_pairs[1:], strict=False):
        boottime_delta = _strict_int(current["boottime_ns"], "boottime_ns") - _strict_int(
            previous["boottime_ns"], "boottime_ns"
        )
        monotonic_delta = current["midpoint_monotonic_ns"] - previous["midpoint_monotonic_ns"]
        suspend_delta = boottime_delta - monotonic_delta
        if suspend_delta > thresholds.suspend_threshold_ns:
            suspend_events.append(
                {
                    "sequence": int(current["sequence"]),
                    "boottime_minus_monotonic_delta_ns": suspend_delta,
                }
            )
    uncertain_fraction = len(uncertain_sequences) / thresholds.sample_count
    passed = (
        uncertain_fraction <= thresholds.max_uncertain_fraction
        and not steps
        and not suspend_events
        and bool(valid)
    )
    return {
        "domain": domain,
        "sample_count": len(samples),
        "valid_sample_count": len(valid),
        "uncertain_sequences": uncertain_sequences,
        "uncertain_fraction": uncertain_fraction,
        "offset_min_ns": min(offsets) if offsets else None,
        "offset_max_ns": max(offsets) if offsets else None,
        "offset_step_count": len(steps),
        "offset_steps": steps,
        "max_scheduler_delay_ns": max(scheduler_delays) if scheduler_delays else 0,
        "min_scheduler_delay_ns": min(scheduler_delays) if scheduler_delays else 0,
        "suspend_event_count": len(suspend_events),
        "suspend_events": suspend_events,
        "passed": passed,
    }


def analyze_database_clock_samples(
    samples: Sequence[Mapping[str, Any]],
    *,
    thresholds: ClockDiagnosticThresholds,
) -> dict[str, Any]:
    _validate_sequences(samples, thresholds.sample_count)
    normalized: list[dict[str, Any]] = []
    uncertain_sequences: list[int] = []
    backend_pid: int | None = None
    for sample in samples:
        send = _strict_int(sample.get("client_monotonic_send_ns"), "client_monotonic_send_ns")
        receive = _strict_int(
            sample.get("client_monotonic_receive_ns"), "client_monotonic_receive_ns"
        )
        database_clock = _strict_int(sample.get("database_clock_unix_ns"), "database_clock_unix_ns")
        current_backend_pid = _strict_int(sample.get("backend_pid"), "backend_pid")
        if receive < send or current_backend_pid <= 0:
            raise ValueError("database sample bracket or backend identity is invalid")
        if backend_pid is None:
            backend_pid = current_backend_pid
        elif backend_pid != current_backend_pid:
            raise ValueError("database backend identity changed inside one window")
        rtt = receive - send
        sequence = _strict_int(sample.get("sequence"), "sequence")
        if rtt > thresholds.database_rtt_max_ns:
            uncertain_sequences.append(sequence)
        normalized.append(
            {
                **dict(sample),
                "client_midpoint_monotonic_ns": (send + receive) // 2,
                "database_clock_unix_ns": database_clock,
                "uncertainty_ns": (rtt + 1) // 2,
                "rtt_ns": rtt,
            }
        )
    uncertain_set = set(uncertain_sequences)
    valid = [sample for sample in normalized if sample["sequence"] not in uncertain_set]
    steps, offsets, scheduler_delays = _offset_steps(
        valid,
        midpoint_field="client_midpoint_monotonic_ns",
        wall_field="database_clock_unix_ns",
        uncertainty_field="uncertainty_ns",
        thresholds=thresholds,
    )
    uncertain_fraction = len(uncertain_sequences) / thresholds.sample_count
    passed = uncertain_fraction <= thresholds.max_uncertain_fraction and not steps and bool(valid)
    return {
        "domain": "postgresql",
        "backend_pid": backend_pid,
        "sample_count": len(samples),
        "valid_sample_count": len(valid),
        "uncertain_sequences": uncertain_sequences,
        "uncertain_fraction": uncertain_fraction,
        "offset_min_ns": min(offsets) if offsets else None,
        "offset_max_ns": max(offsets) if offsets else None,
        "offset_step_count": len(steps),
        "offset_steps": steps,
        "max_scheduler_delay_ns": max(scheduler_delays) if scheduler_delays else 0,
        "min_scheduler_delay_ns": min(scheduler_delays) if scheduler_delays else 0,
        "passed": passed,
    }


def validate_transaction_timestamp_semantics(payload: Mapping[str, Any]) -> dict[str, bool]:
    required = {
        "first_clock_unix_ns",
        "first_statement_unix_ns",
        "first_transaction_unix_ns",
        "first_now_unix_ns",
        "second_clock_unix_ns",
        "second_statement_unix_ns",
        "second_transaction_unix_ns",
        "second_now_unix_ns",
    }
    if set(payload) != required:
        raise ValueError("transaction timestamp semantics payload has the wrong fields")
    values = {key: _strict_int(payload[key], key) for key in required}
    result = {
        "clock_advanced": values["second_clock_unix_ns"] > values["first_clock_unix_ns"],
        "statement_advanced": (
            values["second_statement_unix_ns"] > values["first_statement_unix_ns"]
        ),
        "transaction_stable": (
            values["second_transaction_unix_ns"] == values["first_transaction_unix_ns"]
        ),
        "now_stable": values["second_now_unix_ns"] == values["first_now_unix_ns"],
        "now_matches_transaction": (
            values["first_now_unix_ns"] == values["first_transaction_unix_ns"]
            and values["second_now_unix_ns"] == values["second_transaction_unix_ns"]
        ),
    }
    result["passed"] = all(result.values())
    return result


def analyze_clock_window(
    *,
    os_domains: Mapping[str, Sequence[Mapping[str, Any]]],
    database_samples: Sequence[Mapping[str, Any]],
    transaction_semantics: Mapping[str, Any],
    thresholds: ClockDiagnosticThresholds,
) -> dict[str, Any]:
    expected_domains = {"windows_host", "wsl_ubuntu", "docker_evm_api"}
    if set(os_domains) != expected_domains:
        raise ValueError("clock window lacks the exact frozen OS domains")
    os_analysis = {
        domain: analyze_os_clock_samples(samples, thresholds=thresholds)
        for domain, samples in sorted(os_domains.items())
    }
    database_analysis = analyze_database_clock_samples(
        database_samples,
        thresholds=thresholds,
    )
    transaction_analysis = validate_transaction_timestamp_semantics(transaction_semantics)
    passed = (
        all(item["passed"] is True for item in os_analysis.values())
        and database_analysis["passed"] is True
        and transaction_analysis["passed"] is True
    )
    return {
        "os_domains": os_analysis,
        "postgresql": database_analysis,
        "transaction_timestamp_semantics": transaction_analysis,
        "passed": passed,
    }
