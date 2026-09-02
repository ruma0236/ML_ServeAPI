from __future__ import annotations

from typing import Any

import pytest

from evm.scale_validation.phase_b2_r7s5_windows_wsl import (
    _QualificationExpectationForTest as QualificationExpectation,
    _validate_windows_job_qualification_for_test as validate_windows_job_qualification,
    _validate_wsl_process_group_qualification_for_test as validate_wsl_process_group_qualification,
    WINDOWS_EVENT_ORDER,
    WINDOWS_SCHEMA,
    WSL_EVENT_ORDER,
    WSL_SCHEMA,
    source_contract,
    validate_windows_job_qualification as public_validate_windows_job_qualification,
    validate_wsl_process_group_qualification as public_validate_wsl_process_group_qualification,
)


GLOBAL_RUN_ID = "b85da76d-8276-4ec5-811c-8681a87f6bca"
WINDOWS_DOMAIN_RUN_ID = "fb9bbaea-f0ea-4a6b-8c3b-983f2459cd50"
WINDOWS_RUN_UUID = "2a88d916-b476-41e0-b497-e404a09400a4"
WSL_DOMAIN_RUN_ID = "d7b5f598-1fc0-40c2-931b-ebfae99c3f14"
WSL_RUN_UUID = "2de24575-f3e7-45c4-85f4-7836eb800959"
ATTEMPT_UUID = "7987142a-f007-4a0a-91c5-6617f6518491"
COLLECTOR_SHA = "10" * 32
RECEIPT_SHA = "20" * 32
RESERVATION_SHA = "30" * 32
JOB_ID = "job-file-id:20260902:abcdef"


def _image_identity() -> dict[str, Any]:
    return {
        "final_path": r"C:\trusted\python.exe",
        "volume_serial_number": 20260902,
        "file_id_hex": "12" * 16,
        "sha256": "34" * 32,
        "owner_sid": "S-1-5-32-544",
        "security_descriptor_sha256": "45" * 32,
        "dacl_present": True,
        "dacl_protected": True,
        "reparse_tag": 0,
        "file_type": 1,
    }


def _ancestor_identity() -> dict[str, Any]:
    return {
        "final_path": r"C:\trusted",
        "volume_serial_number": 20260902,
        "file_id_hex": "56" * 16,
        "owner_sid": "S-1-5-32-544",
        "security_descriptor_sha256": "78" * 32,
        "dacl_present": True,
        "dacl_protected": True,
        "reparse_tag": 0,
        "file_type": 1,
        "directory_attribute": True,
    }


def _expectation(domain: str) -> QualificationExpectation:
    windows = domain == "windows"
    return QualificationExpectation(
        global_run_id=GLOBAL_RUN_ID,
        domain_run_id=WINDOWS_DOMAIN_RUN_ID if windows else WSL_DOMAIN_RUN_ID,
        run_uuid=WINDOWS_RUN_UUID if windows else WSL_RUN_UUID,
        attempt_uuid=ATTEMPT_UUID,
        collector_sha256=COLLECTOR_SHA,
        receipt_sha256=RECEIPT_SHA,
        reservation_sha256=RESERVATION_SHA,
        job_identity=JOB_ID if windows else None,
        executable_identity=_image_identity() if windows else None,
        ancestor_directory_identity=_ancestor_identity() if windows else None,
    )


def _common(schema: str, domain: str, domain_run_id: str, run_uuid: str) -> dict[str, Any]:
    return {
        "schema": schema,
        "domain": domain,
        "global_run_id": GLOBAL_RUN_ID,
        "domain_run_id": domain_run_id,
        "run_uuid": run_uuid,
        "attempt_uuid": ATTEMPT_UUID,
        "collector_sha256": COLLECTOR_SHA,
        "receipt_sha256": RECEIPT_SHA,
        "reservation_sha256": RESERVATION_SHA,
        "retry_count": 0,
        "automatic_retry_count": 0,
        "terminate_process_calls": 0,
        "terminate_job_calls": 0,
        "force_kill_calls": 0,
        "success_marker_count": 0,
        "completion_marker_count": 0,
        "service_lifecycle_calls": 0,
        "credit": "non_credit",
        "production_go_enabled": False,
        "go_evidence_eligible": False,
    }


def _events(names: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {
            "sequence": sequence,
            "event": name,
            "monotonic_ns": 1_000_000 + sequence * 1_000_000_000,
        }
        for sequence, name in enumerate(names, start=1)
    ]


def _windows() -> dict[str, Any]:
    value = _common(WINDOWS_SCHEMA, "windows", WINDOWS_DOMAIN_RUN_ID, WINDOWS_RUN_UUID)
    value.update(
        {
            "scenario": "root_child_grandchild_reparent_stdio_breakaway",
            "create_suspended": True,
            "job_assigned_before_resume": True,
            "breakaway_allowed": False,
            "breakaway_attempt_count": 1,
            "breakaway_denied_count": 1,
            "leased_executable_identity": _image_identity(),
            "suspended_image_identity": _image_identity(),
            "image_queried_while_suspended": True,
            "leased_ancestor_directory_identity": _ancestor_identity(),
            "resume_ancestor_directory_identity": _ancestor_identity(),
            "job_identity": JOB_ID,
            "processes": [
                {
                    "role": "root",
                    "pid": 4100,
                    "creation_time_ns": 100,
                    "parent_pid": 3000,
                    "parent_creation_time_ns": 50,
                    "job_identity": JOB_ID,
                },
                {
                    "role": "child",
                    "pid": 4200,
                    "creation_time_ns": 200,
                    "parent_pid": 4100,
                    "parent_creation_time_ns": 100,
                    "job_identity": JOB_ID,
                },
                {
                    "role": "grandchild",
                    "pid": 4300,
                    "creation_time_ns": 300,
                    "parent_pid": 4200,
                    "parent_creation_time_ns": 200,
                    "job_identity": JOB_ID,
                },
            ],
            "events": _events(WINDOWS_EVENT_ORDER),
            "root_exited_before_descendants": True,
            "descendant_reparent_observed": True,
            "stdout_drained": True,
            "stderr_drained": True,
            "stdio_descendant_late_close_observed": True,
            "active_process_count": 0,
            "residual_pids": [],
            "stable_zero_observations": 2,
            "completion_accounting_reconciled": True,
            "safe_for_followup": True,
        }
    )
    return value


def _wsl() -> dict[str, Any]:
    value = _common(WSL_SCHEMA, "wsl", WSL_DOMAIN_RUN_ID, WSL_RUN_UUID)
    value.update(
        {
            "scenario": "launcher_exit_linux_process_residual",
            "launch_count": 1,
            "setsid_process_group": True,
            "root_linux_pid": 9001,
            "root_start_time_ticks": 123456,
            "process_group_id": 9001,
            "launcher_exit_observed": True,
            "ack_residual_observed_after_launcher_exit": True,
            "ack_residual_processes": [
                {
                    "pid": 9002,
                    "start_time_ticks": 123457,
                    "run_uuid": WSL_RUN_UUID,
                    "process_group_id": 9001,
                }
            ],
            "events": _events(WSL_EVENT_ORDER),
            "stable_zero_observations": 2,
            "final_uuid_process_count": 0,
            "final_process_group_count": 0,
            "final_proc_observation": "exact_zero",
            "stdout_drained": True,
            "stderr_drained": True,
            "wsl_shutdown_calls": 0,
            "docker_calls": 0,
            "runtime_probe_calls": 0,
            "safe_for_followup": True,
        }
    )
    return value


def test_exact_windows_transcript_is_qualified_non_credit_only() -> None:
    result = validate_windows_job_qualification(_windows(), expected=_expectation("windows"))
    assert result.status == "qualified_non_credit"
    assert result.errors == ()
    assert result.safe_for_followup is True
    assert result.production_go_enabled is False
    assert result.go_evidence_eligible is False


@pytest.mark.parametrize(
    ("mutator", "error"),
    [
        (
            lambda value: value["suspended_image_identity"].__setitem__("sha256", "99" * 32),
            "suspended_image_identity_mismatch",
        ),
        (
            lambda value: value["leased_executable_identity"].__setitem__("sha256", "z" * 64),
            "leased_executable_sha_invalid",
        ),
        (
            lambda value: value.__setitem__("leased_ancestor_directory_identity", {}),
            "leased_ancestor_identity_keys_not_exact",
        ),
        (lambda value: value.__setitem__("job_identity", None), "job_identity_invalid"),
        (
            lambda value: value.__setitem__("breakaway_attempt_count", True),
            "breakaway_attempt_count_mismatch",
        ),
        (
            lambda value: value.__setitem__("active_process_count", False),
            "active_process_count_nonzero_or_invalid",
        ),
        (lambda value: value.__setitem__("residual_pids", [4300]), "residual_pid_present"),
        (lambda value: value.__setitem__("stdout_drained", False), "stdout_not_drained"),
    ],
)
def test_windows_identity_count_and_residual_mutations_fail_closed(
    mutator: Any, error: str
) -> None:
    value = _windows()
    mutator(value)
    result = validate_windows_job_qualification(value, expected=_expectation("windows"))
    assert result.status == "manual_intervention_required"
    assert any(error in item for item in result.errors)
    assert result.safe_for_followup is False


def test_windows_duplicate_extra_bool_sequence_and_nonmonotonic_events_are_rejected() -> None:
    mutations = []
    duplicate = _windows()
    duplicate["events"].append(
        {"sequence": 13, "event": WINDOWS_EVENT_ORDER[-1], "monotonic_ns": 2_000_000}
    )
    mutations.append(duplicate)
    bool_sequence = _windows()
    bool_sequence["events"][0]["sequence"] = True
    mutations.append(bool_sequence)
    nonmonotonic = _windows()
    nonmonotonic["events"][4]["monotonic_ns"] = nonmonotonic["events"][3]["monotonic_ns"]
    mutations.append(nonmonotonic)
    for value in mutations:
        result = validate_windows_job_qualification(value, expected=_expectation("windows"))
        assert result.status == "manual_intervention_required"
        assert any("event_" in error for error in result.errors)


def test_windows_same_pid_parent_creation_swap_and_creation_reorder_are_rejected() -> None:
    same_pid = _windows()
    same_pid["processes"][2]["pid"] = same_pid["processes"][1]["pid"]
    result = validate_windows_job_qualification(same_pid, expected=_expectation("windows"))
    assert "overlapping_process_pid_not_unique" in result.errors

    parent_swap = _windows()
    parent_swap["processes"][2]["parent_creation_time_ns"] = 199
    result = validate_windows_job_qualification(parent_swap, expected=_expectation("windows"))
    assert "grandchild_parent_creation_identity_mismatch" in result.errors

    reordered = _windows()
    reordered["processes"][2]["creation_time_ns"] = 150
    result = validate_windows_job_qualification(reordered, expected=_expectation("windows"))
    assert "process_creation_order_invalid" in result.errors


def test_windows_self_repin_unknown_nan_and_expectation_mutation_are_rejected() -> None:
    self_repinned = _windows()
    self_repinned["global_run_id"] = "a4584ebd-b0ac-47e7-a10c-8b5bca99f637"
    result = validate_windows_job_qualification(self_repinned, expected=_expectation("windows"))
    assert "global_run_id_expectation_mismatch" in result.errors

    noncanonical = _windows()
    noncanonical["unknown"] = float("nan")
    result = validate_windows_job_qualification(noncanonical, expected=_expectation("windows"))
    assert "windows_transcript_keys_not_exact" in result.errors
    assert "transcript_not_canonical_json" in result.errors

    invalid_expected = _expectation("windows")
    object.__setattr__(invalid_expected, "collector_sha256", "z" * 64)
    result = validate_windows_job_qualification(_windows(), expected=invalid_expected)
    assert "expected_collector_sha256_invalid" in result.errors


def test_exact_wsl_transcript_is_qualified_non_credit_only() -> None:
    result = validate_wsl_process_group_qualification(_wsl(), expected=_expectation("wsl"))
    assert result.status == "qualified_non_credit"
    assert result.errors == ()
    assert result.safe_for_followup is True
    assert result.go_evidence_eligible is False


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("launch_count", True, "launch_count_must_be_exact_one"),
        ("launcher_exit_observed", False, "launcher_exit_unproven"),
        ("final_uuid_process_count", False, "final_uuid_process_count_must_be_exact_zero"),
        ("final_process_group_count", 1, "final_process_group_count_must_be_exact_zero"),
        ("wsl_shutdown_calls", 1, "wsl_shutdown_calls_must_be_exact_zero"),
        ("docker_calls", 1, "docker_calls_must_be_exact_zero"),
        ("runtime_probe_calls", 1, "runtime_probe_calls_must_be_exact_zero"),
    ],
)
def test_wsl_boolean_count_residual_and_forbidden_call_mutations_fail_closed(
    field: str, value: Any, error: str
) -> None:
    transcript = _wsl()
    transcript[field] = value
    result = validate_wsl_process_group_qualification(transcript, expected=_expectation("wsl"))
    assert result.status == "manual_intervention_required"
    assert error in result.errors


def test_wsl_duplicate_residual_self_repin_and_event_mutations_are_rejected() -> None:
    duplicate = _wsl()
    duplicate["ack_residual_processes"].append(dict(duplicate["ack_residual_processes"][0]))
    result = validate_wsl_process_group_qualification(duplicate, expected=_expectation("wsl"))
    assert "ack_residual_duplicate_identity" in result.errors

    self_repinned = _wsl()
    replacement = "a4584ebd-b0ac-47e7-a10c-8b5bca99f637"
    self_repinned["run_uuid"] = replacement
    self_repinned["ack_residual_processes"][0]["run_uuid"] = replacement
    result = validate_wsl_process_group_qualification(self_repinned, expected=_expectation("wsl"))
    assert "run_uuid_expectation_mismatch" in result.errors
    assert "ack_residual_run_uuid_mismatch" in result.errors

    events = _wsl()
    events["events"][2]["sequence"] = events["events"][1]["sequence"]
    result = validate_wsl_process_group_qualification(events, expected=_expectation("wsl"))
    assert "wsl_event_sequence_gap_duplicate_or_extra" in result.errors


def test_invalid_transcript_type_fails_closed_without_launch_or_retry() -> None:
    windows = validate_windows_job_qualification(  # type: ignore[arg-type]
        [], expected=_expectation("windows")
    )
    wsl = validate_wsl_process_group_qualification(  # type: ignore[arg-type]
        "bad", expected=_expectation("wsl")
    )
    assert windows.errors == ("transcript_mapping_required",)
    assert wsl.errors == ("transcript_mapping_required",)
    assert windows.retry_count == wsl.retry_count == 0
    assert windows.safe_for_followup is wsl.safe_for_followup is False


def test_public_validators_fail_closed_without_external_authority() -> None:
    windows = public_validate_windows_job_qualification(_windows())
    wsl = public_validate_wsl_process_group_qualification(_wsl())
    assert windows.status == wsl.status == "manual_intervention_required"
    assert windows.errors == wsl.errors == ("external_qualification_authority_unconfigured",)
    assert windows.safe_for_followup is wsl.safe_for_followup is False


@pytest.mark.parametrize("domain", ["windows", "wsl"])
def test_stable_zero_interval_and_120_second_deadline_are_enforced(domain: str) -> None:
    value = _windows() if domain == "windows" else _wsl()
    expectation = _expectation(domain)
    validator = (
        validate_windows_job_qualification
        if domain == "windows"
        else validate_wsl_process_group_qualification
    )
    zero_1_name = (
        "active_process_zero_observation_1" if domain == "windows" else "stable_zero_observation_1"
    )
    zero_2_name = (
        "active_process_zero_observation_2" if domain == "windows" else "stable_zero_observation_2"
    )
    wait_name = "root_exited" if domain == "windows" else "ack_residual_observed"
    times = {item["event"]: item for item in value["events"]}
    times[zero_2_name]["monotonic_ns"] = times[zero_1_name]["monotonic_ns"] + 1
    result = validator(value, expected=expectation)
    assert any("stable_zero_interval_too_short" in item for item in result.errors)

    value = _windows() if domain == "windows" else _wsl()
    times = {item["event"]: item for item in value["events"]}
    times[zero_2_name]["monotonic_ns"] = times[wait_name]["monotonic_ns"] + 120_000_000_001
    following = False
    for item in value["events"]:
        if item["event"] == zero_2_name:
            following = True
            continue
        if following:
            item["monotonic_ns"] = times[zero_2_name]["monotonic_ns"] + item["sequence"]
    result = validator(value, expected=expectation)
    assert any("residual_wait_exceeds_120s" in item for item in result.errors)


def test_source_contract_preserves_live_and_authority_blockers() -> None:
    contract = source_contract()
    assert contract["transcript_only_no_process_launch"] is True
    assert contract["public_validator_external_authority_required_and_unconfigured"] is True
    assert contract["private_syntax_validation_test_seam_only"] is True
    assert contract["exact_top_level_and_nested_schema_required"] is True
    assert contract["canonical_nonfinite_rejected"] is True
    assert contract["root_child_grandchild_parent_creation_identity_required"] is True
    assert contract["event_sequence_timestamp_and_no_duplicate_required"] is True
    assert contract["actual_windows_qualification_executed"] is False
    assert contract["actual_wsl_qualification_executed"] is False
    assert contract["external_execution_authority_configured"] is False
    assert contract["same_token_hostile_admin_protected"] is False
    assert contract["production_go_enabled"] is False
    assert contract["go_evidence_eligible"] is False
