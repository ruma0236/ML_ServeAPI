from __future__ import annotations

import pytest

from evm.scale_validation.clock_remediation import (
    ClockRemediationThresholds,
    analyze_database_clock_samples,
    analyze_remediation_window,
    analyze_runtime_clock_samples,
)


def _runtime_samples(
    domain: str,
    *,
    count: int = 4,
    step_sequence: int | None = None,
    scheduler_pause_sequence: int | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    elapsed = 0
    for sequence in range(count):
        if sequence:
            elapsed += 100_000_000
        if sequence == scheduler_pause_sequence:
            elapsed += 500_000_000
        realtime = 1_700_000_000_000_000_000 + elapsed
        if step_sequence is not None and sequence >= step_sequence:
            realtime += 250_000_000
        rows.append(
            {
                "domain": domain,
                "sequence": sequence,
                "raw_before_ns": elapsed,
                "realtime_unix_ns": realtime,
                "raw_after_ns": elapsed + 100_000,
                "monotonic_ns": elapsed + 50_000,
                "auxiliary_monotonic_ns": elapsed + 50_000,
            }
        )
    return rows


def _database_samples(
    *, count: int = 4, step_sequence: int | None = None, rtt_ns: int = 1_000_000
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sequence in range(count):
        midpoint = sequence * 100_000_000
        database_clock = 1_700_000_000_000_000_000 + midpoint
        if step_sequence is not None and sequence >= step_sequence:
            database_clock -= 250_000_000
        stable = database_clock - 100_000
        rows.append(
            {
                "sequence": sequence,
                "client_raw_send_before_ns": midpoint - rtt_ns // 2,
                "client_monotonic_send_ns": midpoint - rtt_ns // 2 + 50_000,
                "client_raw_send_after_ns": midpoint - rtt_ns // 2 + 100_000,
                "client_raw_receive_before_ns": midpoint + rtt_ns // 2 - 100_000,
                "client_monotonic_receive_ns": midpoint + rtt_ns // 2 - 50_000,
                "client_raw_receive_after_ns": midpoint + rtt_ns // 2,
                "database_clock_unix_ns": database_clock,
                "database_statement_unix_ns": stable,
                "database_transaction_unix_ns": stable,
                "database_now_unix_ns": stable,
                "backend_pid": 42,
            }
        )
    return rows


def test_runtime_domain_passes_stable_raw_offset() -> None:
    thresholds = ClockRemediationThresholds(sample_count=4)

    analysis = analyze_runtime_clock_samples(_runtime_samples("wsl_ubuntu"), thresholds=thresholds)

    assert analysis["passed"] is True
    assert analysis["offset_step_count"] == 0
    assert analysis["unclassified_sampler_gap_count"] == 0


def test_runtime_domain_classifies_scheduler_pause_without_a_clock_step() -> None:
    thresholds = ClockRemediationThresholds(sample_count=4)

    analysis = analyze_runtime_clock_samples(
        _runtime_samples("docker_evm_api", scheduler_pause_sequence=2),
        thresholds=thresholds,
    )

    assert analysis["passed"] is True
    assert analysis["sampler_gaps"][0]["classification"] == "scheduler_pause"


@pytest.mark.parametrize("step_ns", [250_000_000, -250_000_000])
def test_runtime_domain_rejects_forward_or_backward_realtime_step(step_ns: int) -> None:
    thresholds = ClockRemediationThresholds(sample_count=4)
    samples = _runtime_samples("wsl_ubuntu")
    for sample in samples[2:]:
        sample["realtime_unix_ns"] = int(sample["realtime_unix_ns"]) + step_ns

    analysis = analyze_runtime_clock_samples(samples, thresholds=thresholds)

    assert analysis["passed"] is False
    assert analysis["offset_step_count"] == 1
    assert analysis["backward_wall_step_count"] == (1 if step_ns < 0 else 0)


def test_database_rejects_rtt_violation_and_offset_step_independently() -> None:
    thresholds = ClockRemediationThresholds(sample_count=4)
    high_rtt = analyze_database_clock_samples(
        _database_samples(rtt_ns=60_000_000), thresholds=thresholds
    )
    stepped = analyze_database_clock_samples(
        _database_samples(step_sequence=2), thresholds=thresholds
    )

    assert high_rtt["midpoint_rtt_invariant_violation_count"] == 4
    assert high_rtt["passed"] is False
    assert stepped["offset_step_count"] == 1
    assert stepped["passed"] is False


def test_database_accepts_transaction_start_before_statement_start() -> None:
    thresholds = ClockRemediationThresholds(sample_count=4)
    samples = _database_samples()
    for sample in samples:
        sample["database_transaction_unix_ns"] = int(sample["database_statement_unix_ns"]) - 100_000
        sample["database_now_unix_ns"] = sample["database_transaction_unix_ns"]

    analysis = analyze_database_clock_samples(samples, thresholds=thresholds)

    assert analysis["midpoint_rtt_invariant_violation_count"] == 0
    assert analysis["passed"] is True


def test_database_rejects_ambiguous_client_clock_bracket() -> None:
    thresholds = ClockRemediationThresholds(sample_count=4)
    samples = _database_samples(rtt_ns=20_000_000)
    samples[2]["client_raw_send_after_ns"] = (
        int(samples[2]["client_raw_send_before_ns"]) + 6_000_000
    )
    samples[2]["client_monotonic_send_ns"] = (
        int(samples[2]["client_raw_send_before_ns"]) + 5_500_000
    )

    analysis = analyze_database_clock_samples(samples, thresholds=thresholds)

    assert (
        "client_raw_monotonic_bracket_bound"
        in analysis["midpoint_rtt_invariant_violations"][0]["reasons"]
    )
    assert analysis["passed"] is False


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("database_now_unix_ns", "now_transaction_timestamp_mismatch"),
        ("database_statement_unix_ns", "database_timestamp_order"),
    ],
)
def test_database_rejects_timestamp_semantic_mutation(field: str, reason: str) -> None:
    thresholds = ClockRemediationThresholds(sample_count=4)
    samples = _database_samples()
    samples[2][field] = int(samples[2][field]) + 500_000

    analysis = analyze_database_clock_samples(samples, thresholds=thresholds)

    assert reason in analysis["midpoint_rtt_invariant_violations"][0]["reasons"]
    assert analysis["passed"] is False


def test_docker_off_window_requires_only_host_and_wsl() -> None:
    thresholds = ClockRemediationThresholds(sample_count=4)
    analysis = analyze_remediation_window(
        mode="docker-off",
        os_domains={domain: _runtime_samples(domain) for domain in ("windows_host", "wsl_ubuntu")},
        database_samples=None,
        thresholds=thresholds,
    )

    assert analysis["passed"] is True
    assert analysis["postgresql"] is None


def test_full_stack_window_never_compares_raw_epoch_across_domains() -> None:
    thresholds = ClockRemediationThresholds(sample_count=4)
    domains = {
        domain: _runtime_samples(domain)
        for domain in ("windows_host", "wsl_ubuntu", "docker_evm_api")
    }
    for sample in domains["docker_evm_api"]:
        sample["raw_before_ns"] = int(sample["raw_before_ns"]) + 9_000_000_000_000
        sample["raw_after_ns"] = int(sample["raw_after_ns"]) + 9_000_000_000_000

    analysis = analyze_remediation_window(
        mode="full-stack",
        os_domains=domains,
        database_samples=_database_samples(),
        thresholds=thresholds,
    )

    assert analysis["passed"] is True
