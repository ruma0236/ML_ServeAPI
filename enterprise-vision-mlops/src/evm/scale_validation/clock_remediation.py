from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ClockRemediationThresholds:
    sample_count: int = 1_800
    cadence_ns: int = 100_000_000
    step_threshold_ns: int = 100_000_000
    os_bracket_max_ns: int = 5_000_000
    database_rtt_max_ns: int = 50_000_000
    sampler_gap_extra_ns: int = 100_000_000
    cross_clock_delta_tolerance_ns: int = 5_000_000
    suspend_threshold_ns: int = 100_000_000


def _strict_int(value: object, field: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{field} must be an integer")
    return value


def _exact_sequences(
    samples: Sequence[Mapping[str, Any]], thresholds: ClockRemediationThresholds
) -> None:
    if len(samples) != thresholds.sample_count:
        raise ValueError(f"expected {thresholds.sample_count} samples, observed {len(samples)}")
    sequences = [_strict_int(sample.get("sequence"), "sequence") for sample in samples]
    if sequences != list(range(thresholds.sample_count)):
        raise ValueError("sample sequences are not the exact contiguous frozen set")


def analyze_runtime_clock_samples(
    samples: Sequence[Mapping[str, Any]],
    *,
    thresholds: ClockRemediationThresholds,
) -> dict[str, Any]:
    _exact_sequences(samples, thresholds)
    normalized: list[dict[str, int]] = []
    domain: str | None = None
    bracket_violations: list[int] = []
    for sample in samples:
        observed_domain = sample.get("domain")
        if not isinstance(observed_domain, str) or not observed_domain:
            raise ValueError("runtime clock domain is missing")
        if domain is None:
            domain = observed_domain
        elif domain != observed_domain:
            raise ValueError("runtime clock domain identity changed")
        raw_before = _strict_int(sample.get("raw_before_ns"), "raw_before_ns")
        raw_after = _strict_int(sample.get("raw_after_ns"), "raw_after_ns")
        realtime = _strict_int(sample.get("realtime_unix_ns"), "realtime_unix_ns")
        monotonic = _strict_int(sample.get("monotonic_ns"), "monotonic_ns")
        auxiliary = _strict_int(sample.get("auxiliary_monotonic_ns"), "auxiliary_monotonic_ns")
        if raw_after < raw_before:
            raise ValueError("runtime raw clock bracket regressed")
        bracket = raw_after - raw_before
        sequence = _strict_int(sample.get("sequence"), "sequence")
        if bracket > thresholds.os_bracket_max_ns:
            bracket_violations.append(sequence)
        midpoint = (raw_before + raw_after) // 2
        normalized.append(
            {
                "sequence": sequence,
                "raw_midpoint_ns": midpoint,
                "realtime_unix_ns": realtime,
                "monotonic_ns": monotonic,
                "auxiliary_monotonic_ns": auxiliary,
                "uncertainty_ns": (bracket + 1) // 2,
                "offset_ns": realtime - midpoint,
            }
        )

    steps: list[dict[str, int]] = []
    backward_steps: list[dict[str, int]] = []
    sampler_gaps: list[dict[str, Any]] = []
    unclassified_gaps: list[dict[str, Any]] = []
    for previous, current in zip(normalized, normalized[1:], strict=False):
        sequence = current["sequence"]
        raw_delta = current["raw_midpoint_ns"] - previous["raw_midpoint_ns"]
        realtime_delta = current["realtime_unix_ns"] - previous["realtime_unix_ns"]
        monotonic_delta = current["monotonic_ns"] - previous["monotonic_ns"]
        auxiliary_delta = current["auxiliary_monotonic_ns"] - previous["auxiliary_monotonic_ns"]
        if raw_delta <= 0 or monotonic_delta < 0 or auxiliary_delta < 0:
            raise ValueError("runtime monotonic clock regressed")
        pair_uncertainty = current["uncertainty_ns"] + previous["uncertainty_ns"]
        offset_change = current["offset_ns"] - previous["offset_ns"]
        is_step = abs(offset_change) > thresholds.step_threshold_ns + pair_uncertainty
        if is_step:
            steps.append(
                {
                    "sequence": sequence,
                    "realtime_unix_ns": current["realtime_unix_ns"],
                    "offset_change_ns": offset_change,
                    "pair_uncertainty_ns": pair_uncertainty,
                    "raw_delta_ns": raw_delta,
                    "realtime_delta_ns": realtime_delta,
                }
            )
        if realtime_delta < 0:
            backward_steps.append(
                {
                    "sequence": sequence,
                    "realtime_delta_ns": realtime_delta,
                    "raw_delta_ns": raw_delta,
                }
            )
        if raw_delta <= thresholds.cadence_ns + thresholds.sampler_gap_extra_ns:
            continue
        spread = max(raw_delta, monotonic_delta, auxiliary_delta) - min(
            raw_delta, monotonic_delta, auxiliary_delta
        )
        suspend_delta = auxiliary_delta - monotonic_delta
        if is_step:
            classification = "realtime_discontinuity"
        elif suspend_delta > thresholds.suspend_threshold_ns:
            classification = "suspend_resume"
        elif (
            spread <= thresholds.cross_clock_delta_tolerance_ns
            and abs(realtime_delta - raw_delta) <= thresholds.step_threshold_ns + pair_uncertainty
        ):
            classification = "scheduler_pause"
        else:
            classification = "unclassified"
        gap = {
            "sequence": sequence,
            "classification": classification,
            "raw_delta_ns": raw_delta,
            "realtime_delta_ns": realtime_delta,
            "monotonic_delta_ns": monotonic_delta,
            "auxiliary_delta_ns": auxiliary_delta,
            "cross_clock_spread_ns": spread,
        }
        sampler_gaps.append(gap)
        if classification == "unclassified":
            unclassified_gaps.append(gap)

    offsets = [sample["offset_ns"] for sample in normalized]
    passed = not bracket_violations and not steps and not backward_steps and not unclassified_gaps
    return {
        "domain": domain,
        "sample_count": len(samples),
        "offset_min_ns": min(offsets),
        "offset_max_ns": max(offsets),
        "offset_step_count": len(steps),
        "offset_steps": steps,
        "backward_wall_step_count": len(backward_steps),
        "backward_wall_steps": backward_steps,
        "bracket_violation_count": len(bracket_violations),
        "bracket_violation_sequences": bracket_violations,
        "sampler_gap_count": len(sampler_gaps),
        "sampler_gaps": sampler_gaps,
        "unclassified_sampler_gap_count": len(unclassified_gaps),
        "passed": passed,
    }


def analyze_database_clock_samples(
    samples: Sequence[Mapping[str, Any]],
    *,
    thresholds: ClockRemediationThresholds,
) -> dict[str, Any]:
    _exact_sequences(samples, thresholds)
    normalized: list[dict[str, int]] = []
    invariant_violations: list[dict[str, Any]] = []
    backend_pid: int | None = None
    for sample in samples:
        sequence = _strict_int(sample.get("sequence"), "sequence")
        raw_send_before = _strict_int(
            sample.get("client_raw_send_before_ns"), "client_raw_send_before_ns"
        )
        raw_send_after = _strict_int(
            sample.get("client_raw_send_after_ns"), "client_raw_send_after_ns"
        )
        raw_receive_before = _strict_int(
            sample.get("client_raw_receive_before_ns"), "client_raw_receive_before_ns"
        )
        raw_receive_after = _strict_int(
            sample.get("client_raw_receive_after_ns"), "client_raw_receive_after_ns"
        )
        monotonic_send = _strict_int(
            sample.get("client_monotonic_send_ns"), "client_monotonic_send_ns"
        )
        monotonic_receive = _strict_int(
            sample.get("client_monotonic_receive_ns"), "client_monotonic_receive_ns"
        )
        database_clock = _strict_int(sample.get("database_clock_unix_ns"), "database_clock_unix_ns")
        statement = _strict_int(
            sample.get("database_statement_unix_ns"), "database_statement_unix_ns"
        )
        transaction = _strict_int(
            sample.get("database_transaction_unix_ns"), "database_transaction_unix_ns"
        )
        now = _strict_int(sample.get("database_now_unix_ns"), "database_now_unix_ns")
        observed_backend = _strict_int(sample.get("backend_pid"), "backend_pid")
        if (
            not (raw_send_before <= raw_send_after <= raw_receive_before <= raw_receive_after)
            or monotonic_receive < monotonic_send
        ):
            raise ValueError("database client bracket regressed")
        if observed_backend <= 0:
            raise ValueError("database backend identity is invalid")
        if backend_pid is None:
            backend_pid = observed_backend
        elif backend_pid != observed_backend:
            raise ValueError("database backend identity changed inside one window")
        raw_rtt = raw_receive_after - raw_send_before
        monotonic_rtt = monotonic_receive - monotonic_send
        reasons: list[str] = []
        if raw_rtt > thresholds.database_rtt_max_ns:
            reasons.append("database_rtt_bound")
        send_bracket = raw_send_after - raw_send_before
        receive_bracket = raw_receive_after - raw_receive_before
        cross_clock_delta_error = abs(raw_rtt - monotonic_rtt)
        if (
            send_bracket > thresholds.os_bracket_max_ns
            or receive_bracket > thresholds.os_bracket_max_ns
            or cross_clock_delta_error
            > send_bracket + receive_bracket + thresholds.cross_clock_delta_tolerance_ns
        ):
            reasons.append("client_raw_monotonic_bracket_bound")
        if transaction != now:
            reasons.append("now_transaction_timestamp_mismatch")
        if not (transaction <= statement <= database_clock):
            reasons.append("database_timestamp_order")
        if reasons:
            invariant_violations.append({"sequence": sequence, "reasons": reasons})
        midpoint = (raw_send_after + raw_receive_before) // 2
        normalized.append(
            {
                "sequence": sequence,
                "client_raw_midpoint_ns": midpoint,
                "client_monotonic_midpoint_ns": (monotonic_send + monotonic_receive) // 2,
                "database_clock_unix_ns": database_clock,
                "uncertainty_ns": (raw_rtt + 1) // 2,
                "offset_ns": database_clock - midpoint,
                "raw_rtt_ns": raw_rtt,
                "monotonic_rtt_ns": monotonic_rtt,
            }
        )

    steps: list[dict[str, int]] = []
    backward_steps: list[dict[str, int]] = []
    unclassified_gaps: list[dict[str, int]] = []
    for previous, current in zip(normalized, normalized[1:], strict=False):
        raw_delta = current["client_raw_midpoint_ns"] - previous["client_raw_midpoint_ns"]
        monotonic_delta = (
            current["client_monotonic_midpoint_ns"] - previous["client_monotonic_midpoint_ns"]
        )
        wall_delta = current["database_clock_unix_ns"] - previous["database_clock_unix_ns"]
        if raw_delta <= 0 or monotonic_delta < 0:
            raise ValueError("database client monotonic clock regressed")
        pair_uncertainty = current["uncertainty_ns"] + previous["uncertainty_ns"]
        offset_change = current["offset_ns"] - previous["offset_ns"]
        if abs(offset_change) > thresholds.step_threshold_ns + pair_uncertainty:
            steps.append(
                {
                    "sequence": current["sequence"],
                    "database_clock_unix_ns": current["database_clock_unix_ns"],
                    "offset_change_ns": offset_change,
                    "pair_uncertainty_ns": pair_uncertainty,
                    "client_raw_delta_ns": raw_delta,
                    "database_clock_delta_ns": wall_delta,
                }
            )
        if wall_delta < 0:
            backward_steps.append(
                {
                    "sequence": current["sequence"],
                    "database_clock_delta_ns": wall_delta,
                    "client_raw_delta_ns": raw_delta,
                }
            )
        if raw_delta > thresholds.cadence_ns + thresholds.sampler_gap_extra_ns:
            if abs(raw_delta - monotonic_delta) > thresholds.cross_clock_delta_tolerance_ns:
                unclassified_gaps.append(
                    {
                        "sequence": current["sequence"],
                        "client_raw_delta_ns": raw_delta,
                        "client_monotonic_delta_ns": monotonic_delta,
                    }
                )

    offsets = [sample["offset_ns"] for sample in normalized]
    rtts = [sample["raw_rtt_ns"] for sample in normalized]
    passed = not invariant_violations and not steps and not backward_steps and not unclassified_gaps
    return {
        "domain": "postgresql",
        "backend_pid": backend_pid,
        "sample_count": len(samples),
        "offset_min_ns": min(offsets),
        "offset_max_ns": max(offsets),
        "rtt_max_ns": max(rtts),
        "midpoint_rtt_invariant_violation_count": len(invariant_violations),
        "midpoint_rtt_invariant_violations": invariant_violations,
        "offset_step_count": len(steps),
        "offset_steps": steps,
        "backward_wall_step_count": len(backward_steps),
        "backward_wall_steps": backward_steps,
        "unclassified_sampler_gap_count": len(unclassified_gaps),
        "unclassified_sampler_gaps": unclassified_gaps,
        "passed": passed,
    }


def analyze_remediation_window(
    *,
    mode: str,
    os_domains: Mapping[str, Sequence[Mapping[str, Any]]],
    database_samples: Sequence[Mapping[str, Any]] | None,
    thresholds: ClockRemediationThresholds,
) -> dict[str, Any]:
    expected_domains = (
        {"windows_host", "wsl_ubuntu"}
        if mode == "docker-off"
        else {"windows_host", "wsl_ubuntu", "docker_evm_api"}
    )
    if mode not in {"docker-off", "full-stack"}:
        raise ValueError("clock remediation mode is invalid")
    if set(os_domains) != expected_domains:
        raise ValueError("clock remediation window lacks the exact frozen OS domains")
    if (mode == "full-stack") != (database_samples is not None):
        raise ValueError("PostgreSQL samples do not match the frozen window mode")
    os_analysis = {
        domain: analyze_runtime_clock_samples(samples, thresholds=thresholds)
        for domain, samples in sorted(os_domains.items())
    }
    database_analysis = (
        analyze_database_clock_samples(database_samples, thresholds=thresholds)
        if database_samples is not None
        else None
    )
    passed = all(item["passed"] is True for item in os_analysis.values()) and (
        database_analysis is None or database_analysis["passed"] is True
    )
    return {
        "mode": mode,
        "os_domains": os_analysis,
        "postgresql": database_analysis,
        "passed": passed,
    }
