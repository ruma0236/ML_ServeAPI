from __future__ import annotations

import copy
from typing import Any

import pytest

from evm.scale_validation import phase_b2_r7s5_dual_clock as dual


RUN_UUID = "11111111-1111-4111-8111-111111111111"
GLOBAL_RUN_ID = "77777777-7777-4777-8777-777777777777"
ATTEMPT_UUID = "22222222-2222-4222-8222-222222222222"
CAPTURE_ID = "33333333-3333-4333-8333-333333333333"
DOMAIN_RUN_IDS = {
    "windows_host": "88888888-8888-4888-8888-888888888888",
    "wsl_ubuntu": "99999999-9999-4999-8999-999999999999",
}
FENCE_ID = "r7s5-single-start-fence"
FENCE_NS = 1_000_000_000_000


def _stream(limit: int, observed: int) -> dict[str, Any]:
    return {
        "limit_bytes": limit,
        "observed_bytes": observed,
        "captured_bytes": observed,
        "overflowed": False,
        "drained": True,
    }


def _domain(name: str, *, ready_ns: int, ended_ns: int, sha: str) -> dict[str, Any]:
    started_ns = FENCE_NS + 10
    sample_times = [
        started_ns + offset * 1_000_000 for offset in range(0, dual.DURATION_MS, dual.CADENCE_MS)
    ]
    return {
        "domain": name,
        "clock_domain": dual.CLOCK_DOMAINS[name],
        "global_run_id": GLOBAL_RUN_ID,
        "domain_run_id": DOMAIN_RUN_IDS[name],
        "run_uuid": RUN_UUID,
        "attempt_uuid": ATTEMPT_UUID,
        "capture_id": CAPTURE_ID,
        "fence_id": FENCE_ID,
        "collector_sha256": sha,
        "raw_artifact_sha256": ("c" if name == "windows_host" else "d") * 64,
        "raw_artifact_bytes": 100_000,
        "git_commit": "1" * 40,
        "git_tree": "2" * 40,
        "launcher_sha256": "3" * 64,
        "outer_sha256": "4" * 64,
        "bridge_sha256": "5" * 64,
        "runner_sha256": "6" * 64,
        "interpreter_sha256": "7" * 64,
        "interpreter_path": (
            r"C:\trusted\python.exe" if name == "windows_host" else "/usr/bin/python3"
        ),
        "interpreter_version": "3.11.15",
        "wsl_distro": None if name == "windows_host" else "Ubuntu",
        "invocation_arguments": ["--duration-ms", "180000", "--cadence-ms", "100"],
        "root_pid": 4100 if name == "windows_host" else 9001,
        "root_creation_identity": "filetime:123456" if name == "windows_host" else "ticks:123456",
        "containment_kind": "windows_job_object"
        if name == "windows_host"
        else "linux_process_group",
        "containment_identity": "job:file-id:abc" if name == "windows_host" else "pgrp:9001",
        "wall_clock_basis": "utc_system_clock_observational_only",
        "monotonic_clock_basis": dual.CLOCK_DOMAINS[name],
        "dispatch_requested_parent_monotonic_ns": ready_ns - 10,
        "acknowledged_parent_monotonic_ns": ready_ns,
        "ready_parent_monotonic_ns": ready_ns,
        "started_parent_monotonic_ns": started_ns,
        "first_sample_parent_monotonic_ns": sample_times[0],
        "last_sample_parent_monotonic_ns": sample_times[-1],
        "ended_parent_monotonic_ns": ended_ns,
        "process_exited_parent_monotonic_ns": ended_ns + 10,
        "stream_drained_parent_monotonic_ns": ended_ns + 20,
        "sample_count": dual.SAMPLE_COUNT,
        "sample_sequences": list(range(dual.SAMPLE_COUNT)),
        "sample_offsets_ms": list(range(0, dual.DURATION_MS, dual.CADENCE_MS)),
        "sample_parent_monotonic_ns": sample_times,
        "sample_wall_time_ns": [2_000_000_000_000 + item for item in sample_times],
        "discontinuity_count": 0,
        "backward_step_count": 0,
        "unclassified_gap_count": 0,
        "bracket_violation_count": 0,
        "stdout": _stream(dual.STDOUT_LIMIT_BYTES, 300_000),
        "stderr": _stream(dual.STDERR_LIMIT_BYTES, 0),
        "residual_state": "zero",
        "residual_pids": [],
    }


def _payload() -> dict[str, Any]:
    windows_ready = FENCE_NS - 20
    wsl_ready = FENCE_NS - 10
    windows_end = FENCE_NS + dual.LAST_OFFSET_MS * 1_000_000 + 20
    wsl_end = FENCE_NS + dual.LAST_OFFSET_MS * 1_000_000 + 30
    return {
        "schema": dual.SCHEMA,
        "global_run_id": GLOBAL_RUN_ID,
        "run_uuid": RUN_UUID,
        "attempt_uuid": ATTEMPT_UUID,
        "capture_id": CAPTURE_ID,
        "source_kind": "live_raw_collectors",
        "synthetic": False,
        "replayed": False,
        "acceptance_credit": False,
        "completion_credit": "non_credit_only",
        "go": False,
        "completion_marker_created": False,
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
        "cross_domain_raw_comparison": False,
        "contract": {
            "duration_ms": dual.DURATION_MS,
            "cadence_ms": dual.CADENCE_MS,
            "sample_count": dual.SAMPLE_COUNT,
            "first_offset_ms": 0,
            "last_offset_ms": dual.LAST_OFFSET_MS,
            "max_end_after_fence_ms": dual.DURATION_MS,
            "stdout_limit_bytes": dual.STDOUT_LIMIT_BYTES,
            "stderr_limit_bytes": dual.STDERR_LIMIT_BYTES,
        },
        "event_log": [
            {
                "sequence": 0,
                "event": "ready",
                "domain": "windows_host",
                "fence_id": None,
                "parent_monotonic_ns": windows_ready,
            },
            {
                "sequence": 1,
                "event": "ready",
                "domain": "wsl_ubuntu",
                "fence_id": None,
                "parent_monotonic_ns": wsl_ready,
            },
            {
                "sequence": 2,
                "event": "start_fence",
                "domain": None,
                "fence_id": FENCE_ID,
                "parent_monotonic_ns": FENCE_NS,
            },
            {
                "sequence": 3,
                "event": "complete",
                "domain": "windows_host",
                "fence_id": FENCE_ID,
                "parent_monotonic_ns": windows_end,
            },
            {
                "sequence": 4,
                "event": "complete",
                "domain": "wsl_ubuntu",
                "fence_id": FENCE_ID,
                "parent_monotonic_ns": wsl_end,
            },
        ],
        "domains": {
            "windows_host": _domain(
                "windows_host", ready_ns=windows_ready, ended_ns=windows_end, sha="a" * 64
            ),
            "wsl_ubuntu": _domain("wsl_ubuntu", ready_ns=wsl_ready, ended_ns=wsl_end, sha="b" * 64),
        },
    }


def _expectation() -> dual._DualClockExpectationForTest:
    payload = _payload()
    return dual._DualClockExpectationForTest(
        global_run_id=GLOBAL_RUN_ID,
        run_uuid=RUN_UUID,
        attempt_uuid=ATTEMPT_UUID,
        capture_id=CAPTURE_ID,
        domain_binding_sha256s={
            name: dual._domain_binding_sha256(payload["domains"][name]) for name in dual.DOMAINS
        },
    )


def _validate(
    payload: dict[str, Any], *, seen_capture_ids: tuple[str, ...] = ()
) -> dual.DualClockDecision:
    return dual._validate_dual_clock_qualification_for_test(
        payload,
        expected=_expectation(),
        seen_capture_ids=seen_capture_ids,
    )


def test_exact_dual_capture_is_qualified_non_credit_only() -> None:
    decision = _validate(_payload()).to_dict()

    assert decision["status"] == "qualified_non_credit"
    assert decision["acceptance_credit"] is False
    assert decision["completion_credit"] == "non_credit_only"
    assert decision["go"] is False
    assert decision["completion_marker_created"] is False
    assert decision["cross_domain_raw_comparison"] is False


def test_both_ready_must_precede_exactly_one_start_fence() -> None:
    payload = _payload()
    payload["event_log"][1], payload["event_log"][2] = (
        payload["event_log"][2],
        payload["event_log"][1],
    )
    payload["event_log"][1]["sequence"] = 1
    payload["event_log"][2]["sequence"] = 2

    with pytest.raises(dual.R7S5DualClockError, match="start_fence_requires_both_ready_once"):
        _validate(payload)

    machine = dual.DualCollectorStateMachine()
    for sequence, event in enumerate(_payload()["event_log"][:3]):
        machine.apply(event, sequence)
    duplicate = {
        "sequence": 3,
        "event": "start_fence",
        "domain": None,
        "fence_id": FENCE_ID,
        "parent_monotonic_ns": FENCE_NS,
    }
    with pytest.raises(dual.R7S5DualClockError, match="start_fence_requires_both_ready_once"):
        machine.apply(duplicate, 3)


@pytest.mark.parametrize(
    "mutation",
    [
        "synthetic",
        "replayed_flag",
        "credit",
        "cross_domain_compare",
        "automatic_retry_numeric_bool",
        "missing_offset",
        "offset_swap",
        "sample_count_numeric_bool",
        "over_180000ms",
        "short_window",
        "late_start_short_collection",
        "duplicate_sequence",
        "sample_before_dispatch_bracket",
        "sample_at_180000ms",
        "raw_artifact_repin",
        "interpreter_path_swap",
        "wall_clock_backward",
        "containment_swap",
        "stdout_overflow",
        "stdout_undrained",
        "stderr_over_limit",
        "residual_pid",
        "domain_swap",
        "clock_swap",
        "run_swap",
    ],
)
def test_dual_protocol_mutations_fail_closed(mutation: str) -> None:
    payload = _payload()
    windows = payload["domains"]["windows_host"]
    if mutation == "synthetic":
        payload["synthetic"] = True
    elif mutation == "replayed_flag":
        payload["replayed"] = True
    elif mutation == "credit":
        payload["acceptance_credit"] = True
    elif mutation == "cross_domain_compare":
        payload["cross_domain_raw_comparison"] = True
    elif mutation == "automatic_retry_numeric_bool":
        payload["automatic_retry_count"] = False
    elif mutation == "missing_offset":
        windows["sample_offsets_ms"].pop()
    elif mutation == "offset_swap":
        windows["sample_offsets_ms"][10], windows["sample_offsets_ms"][11] = (
            windows["sample_offsets_ms"][11],
            windows["sample_offsets_ms"][10],
        )
    elif mutation == "sample_count_numeric_bool":
        windows["sample_count"] = True
    elif mutation == "over_180000ms":
        windows["ended_parent_monotonic_ns"] = FENCE_NS + 180_000_000_001
        payload["event_log"][3]["parent_monotonic_ns"] = windows["ended_parent_monotonic_ns"]
    elif mutation == "short_window":
        windows["ended_parent_monotonic_ns"] = FENCE_NS + 179_899_999_999
        payload["event_log"][3]["parent_monotonic_ns"] = windows["ended_parent_monotonic_ns"]
    elif mutation == "late_start_short_collection":
        windows["started_parent_monotonic_ns"] = FENCE_NS + 179_899_000_000
    elif mutation == "duplicate_sequence":
        windows["sample_sequences"][10] = windows["sample_sequences"][9]
    elif mutation == "sample_before_dispatch_bracket":
        windows["sample_parent_monotonic_ns"][10] -= dual.CADENCE_MS * 1_000_000
    elif mutation == "sample_at_180000ms":
        windows["sample_parent_monotonic_ns"][-1] = (
            windows["started_parent_monotonic_ns"] + dual.DURATION_MS * 1_000_000
        )
        windows["last_sample_parent_monotonic_ns"] = windows["sample_parent_monotonic_ns"][-1]
    elif mutation == "raw_artifact_repin":
        windows["raw_artifact_sha256"] = "e" * 64
    elif mutation == "interpreter_path_swap":
        windows["interpreter_path"] = "/usr/bin/python3"
    elif mutation == "wall_clock_backward":
        windows["sample_wall_time_ns"][10] = windows["sample_wall_time_ns"][9] - 1
    elif mutation == "containment_swap":
        windows["containment_kind"] = "linux_process_group"
    elif mutation == "stdout_overflow":
        windows["stdout"]["overflowed"] = True
    elif mutation == "stdout_undrained":
        windows["stdout"]["drained"] = False
    elif mutation == "stderr_over_limit":
        windows["stderr"]["observed_bytes"] = dual.STDERR_LIMIT_BYTES + 1
        windows["stderr"]["captured_bytes"] = dual.STDERR_LIMIT_BYTES
    elif mutation == "residual_pid":
        windows["residual_state"] = "nonzero"
        windows["residual_pids"] = [9876]
    elif mutation == "domain_swap":
        payload["domains"]["windows_host"], payload["domains"]["wsl_ubuntu"] = (
            payload["domains"]["wsl_ubuntu"],
            payload["domains"]["windows_host"],
        )
    elif mutation == "clock_swap":
        windows["clock_domain"] = dual.CLOCK_DOMAINS["wsl_ubuntu"]
    elif mutation == "run_swap":
        windows["run_uuid"] = "44444444-4444-4444-8444-444444444444"

    with pytest.raises(dual.R7S5DualClockError):
        _validate(payload)


def test_capture_id_replay_is_rejected() -> None:
    with pytest.raises(dual.R7S5DualClockError, match="capture_replay"):
        _validate(_payload(), seen_capture_ids=(CAPTURE_ID,))


def test_public_validator_is_closed_without_external_authority() -> None:
    with pytest.raises(
        dual.R7S5DualClockError, match="external_qualification_authority_unconfigured"
    ):
        dual.validate_dual_clock_qualification(_payload())


def test_contract_has_separate_clocks_and_no_live_or_success_path() -> None:
    contract = dual.dual_clock_contract()

    assert contract["clock_domains_are_separate"] is True
    assert contract["cross_domain_raw_comparison"] is False
    assert contract["sample_offsets_ms"] == [0, 179900]
    assert contract["completion_credit"] == "non_credit_only"
    assert contract["success_or_completion_marker_allowed"] is False
    assert contract["live_calls_implemented"] is False
    assert contract["raw_sample_sequences_and_parent_monotonic_timestamps_required"] is True
    assert contract["dispatch_ack_start_first_last_complete_exit_drain_required"] is True
    assert contract["collector_raw_git_interpreter_and_launch_chain_provenance_required"] is True
    assert contract["public_validator_external_authority_required_and_unconfigured"] is True
    assert contract["private_syntax_validation_test_seam_only"] is True
    assert (
        contract["collector_liveness_or_raw_sample_authenticity_verified_by_this_module"] is False
    )
    assert not hasattr(dual, "subprocess")


def test_unknown_field_and_event_clock_regression_are_rejected() -> None:
    unknown = _payload()
    unknown["production_entry_enabled"] = True
    with pytest.raises(dual.R7S5DualClockError, match="dual_clock_fields_mismatch"):
        _validate(unknown)

    regressed = copy.deepcopy(_payload())
    regressed["event_log"][1]["parent_monotonic_ns"] = (
        regressed["event_log"][0]["parent_monotonic_ns"] - 1
    )
    with pytest.raises(dual.R7S5DualClockError, match="event_parent_clock_regressed"):
        _validate(regressed)
