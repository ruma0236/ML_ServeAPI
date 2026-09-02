"""Strict offline Windows Job and WSL process-group transcript validators.

The validators never launch or terminate a process.  They require an
out-of-band expectation so a self-consistently repinned transcript cannot be
accepted.  Passing is non-credit review evidence only; real execution and
independent authorization remain mandatory before r8.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping


WINDOWS_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.windows-job-qualification.v2"
WSL_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.wsl-process-group-qualification.v2"
WINDOWS_EVENT_ORDER = (
    "root_created_suspended",
    "job_assigned",
    "image_verified",
    "root_resumed",
    "child_created",
    "grandchild_created",
    "root_exited",
    "descendant_reparent_observed",
    "breakaway_attempt_denied",
    "active_process_zero_observation_1",
    "active_process_zero_observation_2",
    "stdout_drained",
    "stderr_drained",
)
WSL_EVENT_ORDER = (
    "launcher_started",
    "launcher_exited",
    "ack_residual_observed",
    "stable_zero_observation_1",
    "stable_zero_observation_2",
    "stdout_drained",
    "stderr_drained",
)
PROCESS_ROLES = ("root", "child", "grandchild")
RESIDUAL_WAIT_NS = 120_000_000_000
STABLE_ZERO_MIN_INTERVAL_NS = 1_000_000_000
HEX32_RE = re.compile(r"[0-9a-f]{32}\Z")
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
SID_RE = re.compile(r"S-\d+(?:-\d+)+\Z")

COMMON_FIELDS = frozenset(
    {
        "schema",
        "domain",
        "global_run_id",
        "domain_run_id",
        "run_uuid",
        "attempt_uuid",
        "collector_sha256",
        "receipt_sha256",
        "reservation_sha256",
        "retry_count",
        "automatic_retry_count",
        "terminate_process_calls",
        "terminate_job_calls",
        "force_kill_calls",
        "success_marker_count",
        "completion_marker_count",
        "service_lifecycle_calls",
        "credit",
        "production_go_enabled",
        "go_evidence_eligible",
    }
)
WINDOWS_FIELDS = COMMON_FIELDS | frozenset(
    {
        "scenario",
        "create_suspended",
        "job_assigned_before_resume",
        "breakaway_allowed",
        "breakaway_attempt_count",
        "breakaway_denied_count",
        "leased_executable_identity",
        "suspended_image_identity",
        "image_queried_while_suspended",
        "leased_ancestor_directory_identity",
        "resume_ancestor_directory_identity",
        "job_identity",
        "processes",
        "events",
        "root_exited_before_descendants",
        "descendant_reparent_observed",
        "stdout_drained",
        "stderr_drained",
        "stdio_descendant_late_close_observed",
        "active_process_count",
        "residual_pids",
        "stable_zero_observations",
        "completion_accounting_reconciled",
        "safe_for_followup",
    }
)
WSL_FIELDS = COMMON_FIELDS | frozenset(
    {
        "scenario",
        "launch_count",
        "setsid_process_group",
        "root_linux_pid",
        "root_start_time_ticks",
        "process_group_id",
        "launcher_exit_observed",
        "ack_residual_observed_after_launcher_exit",
        "ack_residual_processes",
        "events",
        "stable_zero_observations",
        "final_uuid_process_count",
        "final_process_group_count",
        "final_proc_observation",
        "stdout_drained",
        "stderr_drained",
        "wsl_shutdown_calls",
        "docker_calls",
        "runtime_probe_calls",
        "safe_for_followup",
    }
)
IMAGE_FIELDS = frozenset(
    {
        "final_path",
        "volume_serial_number",
        "file_id_hex",
        "sha256",
        "owner_sid",
        "security_descriptor_sha256",
        "dacl_present",
        "dacl_protected",
        "reparse_tag",
        "file_type",
    }
)
ANCESTOR_FIELDS = frozenset(
    {
        "final_path",
        "volume_serial_number",
        "file_id_hex",
        "owner_sid",
        "security_descriptor_sha256",
        "dacl_present",
        "dacl_protected",
        "reparse_tag",
        "file_type",
        "directory_attribute",
    }
)


@dataclass(frozen=True)
class _QualificationExpectationForTest:
    global_run_id: str
    domain_run_id: str
    run_uuid: str
    attempt_uuid: str
    collector_sha256: str
    receipt_sha256: str
    reservation_sha256: str
    job_identity: str | None = None
    executable_identity: Mapping[str, Any] | None = None
    ancestor_directory_identity: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class QualificationValidation:
    domain: str
    status: str
    errors: tuple[str, ...]
    transcript_sha256: str
    manual_intervention_required: bool
    safe_for_followup: bool
    retry_count: int = 0
    forced_termination_attempts: int = 0
    success_marker_created: bool = False
    production_go_enabled: bool = False
    go_evidence_eligible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.qualification-validation.v2",
            "domain": self.domain,
            "status": self.status,
            "errors": list(self.errors),
            "transcript_sha256": self.transcript_sha256,
            "manual_intervention_required": self.manual_intervention_required,
            "safe_for_followup": self.safe_for_followup,
            "retry_count": self.retry_count,
            "forced_termination_attempts": self.forced_termination_attempts,
            "success_marker_created": self.success_marker_created,
            "production_go_enabled": self.production_go_enabled,
            "go_evidence_eligible": self.go_evidence_eligible,
        }


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _exact_uuid(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return str(parsed) == value and parsed.version == 4


def _exact_int(value: object, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _exact_bool(value: object, expected: bool) -> bool:
    return type(value) is bool and value is expected


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _exact_lower_hex(value: object, pattern: re.Pattern[str]) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _result(
    domain: str, transcript: Mapping[str, Any], errors: list[str]
) -> QualificationValidation:
    try:
        transcript_sha256 = hashlib.sha256(_canonical(transcript)).hexdigest()
    except (TypeError, ValueError):
        errors.append("transcript_not_canonical_json")
        transcript_sha256 = "0" * 64
    passed = not errors
    return QualificationValidation(
        domain=domain,
        status="qualified_non_credit" if passed else "manual_intervention_required",
        errors=tuple(sorted(set(errors))),
        transcript_sha256=transcript_sha256,
        manual_intervention_required=not passed,
        safe_for_followup=passed,
    )


def _require_exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], label: str, errors: list[str]
) -> None:
    if set(value) != set(expected):
        errors.append(f"{label}_keys_not_exact")


def _require_common(
    transcript: Mapping[str, Any],
    *,
    schema: str,
    domain: str,
    expected: _QualificationExpectationForTest,
    errors: list[str],
) -> None:
    if type(expected) is not _QualificationExpectationForTest:
        errors.append("private_test_qualification_expectation_required")
        return
    if transcript.get("schema") != schema:
        errors.append("schema_mismatch")
    if transcript.get("domain") != domain:
        errors.append("domain_mismatch")
    for field in ("global_run_id", "domain_run_id", "run_uuid", "attempt_uuid"):
        wanted = getattr(expected, field)
        if not _exact_uuid(wanted):
            errors.append(f"expected_{field}_invalid")
        if transcript.get(field) != wanted:
            errors.append(f"{field}_expectation_mismatch")
    for field in ("collector_sha256", "receipt_sha256", "reservation_sha256"):
        wanted = getattr(expected, field)
        if not _exact_lower_hex(wanted, HEX64_RE):
            errors.append(f"expected_{field}_invalid")
        if transcript.get(field) != wanted:
            errors.append(f"{field}_expectation_mismatch")
    for field in (
        "retry_count",
        "automatic_retry_count",
        "terminate_process_calls",
        "terminate_job_calls",
        "force_kill_calls",
        "success_marker_count",
        "completion_marker_count",
        "service_lifecycle_calls",
    ):
        if not _exact_int(transcript.get(field)) or transcript.get(field) != 0:
            errors.append(f"{field}_must_be_exact_zero")
    if transcript.get("credit") != "non_credit":
        errors.append("credit_must_be_non_credit")
    if not _exact_bool(transcript.get("production_go_enabled"), False):
        errors.append("production_go_must_be_false")
    if not _exact_bool(transcript.get("go_evidence_eligible"), False):
        errors.append("go_evidence_eligible_must_be_false")


def _validate_windows_identity(
    value: object, *, fields: frozenset[str], directory: bool, label: str, errors: list[str]
) -> Mapping[str, Any] | None:
    identity = _mapping(value)
    if identity is None:
        errors.append(f"{label}_identity_missing")
        return None
    _require_exact_keys(identity, fields, f"{label}_identity", errors)
    path = identity.get("final_path")
    if (
        type(path) is not str
        or not path
        or "\x00" in path
        or not ntpath.isabs(path)
        or ".." in ntpath.normpath(path).split("\\")
    ):
        errors.append(f"{label}_path_invalid")
    if not _exact_int(identity.get("volume_serial_number"), minimum=1):
        errors.append(f"{label}_volume_invalid")
    if not _exact_lower_hex(identity.get("file_id_hex"), HEX32_RE):
        errors.append(f"{label}_file_id_invalid")
    if fields is IMAGE_FIELDS and not _exact_lower_hex(identity.get("sha256"), HEX64_RE):
        errors.append(f"{label}_sha_invalid")
    if (
        type(identity.get("owner_sid")) is not str
        or SID_RE.fullmatch(identity["owner_sid"]) is None
    ):
        errors.append(f"{label}_owner_sid_invalid")
    if not _exact_lower_hex(identity.get("security_descriptor_sha256"), HEX64_RE):
        errors.append(f"{label}_security_descriptor_invalid")
    if not _exact_bool(identity.get("dacl_present"), True):
        errors.append(f"{label}_dacl_present_invalid")
    if not _exact_bool(identity.get("dacl_protected"), True):
        errors.append(f"{label}_dacl_protected_invalid")
    if not _exact_int(identity.get("reparse_tag")) or identity.get("reparse_tag") != 0:
        errors.append(f"{label}_reparse_tag_invalid")
    if not _exact_int(identity.get("file_type"), minimum=1) or identity.get("file_type") != 1:
        errors.append(f"{label}_file_type_invalid")
    if directory and not _exact_bool(identity.get("directory_attribute"), True):
        errors.append(f"{label}_directory_attribute_invalid")
    return identity


def _validate_events(
    value: object, *, expected_order: tuple[str, ...], label: str, errors: list[str]
) -> dict[str, int]:
    if type(value) is not list:
        errors.append(f"{label}_event_list_required")
        return {}
    names: list[str] = []
    sequences: list[int] = []
    monotonic_values: list[int] = []
    for item in value:
        event = _mapping(item)
        if event is None:
            errors.append(f"{label}_event_mapping_required")
            continue
        _require_exact_keys(event, frozenset({"sequence", "event", "monotonic_ns"}), label, errors)
        sequence = event.get("sequence")
        monotonic_ns = event.get("monotonic_ns")
        name = event.get("event")
        if not _exact_int(sequence, minimum=1):
            errors.append(f"{label}_event_sequence_invalid")
        else:
            sequences.append(sequence)
        if not _exact_int(monotonic_ns, minimum=1):
            errors.append(f"{label}_event_monotonic_invalid")
        else:
            monotonic_values.append(monotonic_ns)
        if type(name) is not str:
            errors.append(f"{label}_event_name_invalid")
        else:
            names.append(name)
    if sequences != list(range(1, len(expected_order) + 1)):
        errors.append(f"{label}_event_sequence_gap_duplicate_or_extra")
    if names != list(expected_order):
        errors.append(f"{label}_event_order_duplicate_or_extra")
    if len(monotonic_values) != len(expected_order) or any(
        later <= earlier for earlier, later in zip(monotonic_values, monotonic_values[1:])
    ):
        errors.append(f"{label}_event_monotonic_order_invalid")
    if names == list(expected_order) and len(monotonic_values) == len(expected_order):
        return dict(zip(names, monotonic_values, strict=True))
    return {}


def _validate_windows_job_qualification_for_test(
    transcript: Mapping[str, Any], *, expected: _QualificationExpectationForTest
) -> QualificationValidation:
    """Validate a complete adversarial Windows Job transcript fail-closed."""

    if not isinstance(transcript, Mapping):
        return _result("windows", {}, ["transcript_mapping_required"])
    errors: list[str] = []
    _require_exact_keys(transcript, WINDOWS_FIELDS, "windows_transcript", errors)
    _require_common(
        transcript, schema=WINDOWS_SCHEMA, domain="windows", expected=expected, errors=errors
    )
    if transcript.get("scenario") != "root_child_grandchild_reparent_stdio_breakaway":
        errors.append("required_adversarial_scenario_missing")
    for field, wanted, error in (
        ("create_suspended", True, "create_suspended_required"),
        ("job_assigned_before_resume", True, "job_assignment_order_unproven"),
        ("breakaway_allowed", False, "breakaway_must_be_denied"),
        ("image_queried_while_suspended", True, "suspended_image_query_unproven"),
        ("root_exited_before_descendants", True, "root_first_exit_unproven"),
        ("descendant_reparent_observed", True, "descendant_reparent_unproven"),
        ("stdout_drained", True, "stdout_not_drained"),
        ("stderr_drained", True, "stderr_not_drained"),
        ("stdio_descendant_late_close_observed", True, "stdio_late_close_unproven"),
        ("completion_accounting_reconciled", True, "job_accounting_not_reconciled"),
        ("safe_for_followup", True, "safe_for_followup_false"),
    ):
        if not _exact_bool(transcript.get(field), wanted):
            errors.append(error)
    for field in ("breakaway_attempt_count", "breakaway_denied_count"):
        if not _exact_int(transcript.get(field), minimum=1) or transcript.get(field) != 1:
            errors.append(f"{field}_mismatch")

    leased = _validate_windows_identity(
        transcript.get("leased_executable_identity"),
        fields=IMAGE_FIELDS,
        directory=False,
        label="leased_executable",
        errors=errors,
    )
    actual = _validate_windows_identity(
        transcript.get("suspended_image_identity"),
        fields=IMAGE_FIELDS,
        directory=False,
        label="suspended_image",
        errors=errors,
    )
    ancestor = _validate_windows_identity(
        transcript.get("leased_ancestor_directory_identity"),
        fields=ANCESTOR_FIELDS,
        directory=True,
        label="leased_ancestor",
        errors=errors,
    )
    resume_ancestor = _validate_windows_identity(
        transcript.get("resume_ancestor_directory_identity"),
        fields=ANCESTOR_FIELDS,
        directory=True,
        label="resume_ancestor",
        errors=errors,
    )
    if leased is not None and actual is not None and dict(leased) != dict(actual):
        errors.append("suspended_image_identity_mismatch")
    if (
        ancestor is not None
        and resume_ancestor is not None
        and dict(ancestor) != dict(resume_ancestor)
    ):
        errors.append("ancestor_directory_identity_changed")
    if type(expected) is _QualificationExpectationForTest:
        if expected.job_identity is None or transcript.get("job_identity") != expected.job_identity:
            errors.append("job_identity_expectation_mismatch")
        if (
            expected.executable_identity is None
            or leased is None
            or dict(expected.executable_identity) != dict(leased)
        ):
            errors.append("leased_executable_expectation_mismatch")
        if (
            expected.ancestor_directory_identity is None
            or ancestor is None
            or dict(expected.ancestor_directory_identity) != dict(ancestor)
        ):
            errors.append("ancestor_directory_expectation_mismatch")
    job_identity = transcript.get("job_identity")
    if type(job_identity) is not str or not job_identity:
        errors.append("job_identity_invalid")

    processes = transcript.get("processes")
    role_map: dict[str, Mapping[str, Any]] = {}
    if type(processes) is not list:
        errors.append("process_inventory_list_required")
    else:
        process_fields = frozenset(
            {
                "role",
                "pid",
                "creation_time_ns",
                "parent_pid",
                "parent_creation_time_ns",
                "job_identity",
            }
        )
        for item in processes:
            process = _mapping(item)
            if process is None:
                errors.append("process_entry_mapping_required")
                continue
            _require_exact_keys(process, process_fields, "process", errors)
            role = process.get("role")
            if role not in PROCESS_ROLES or role in role_map:
                errors.append("process_role_duplicate_or_invalid")
                continue
            role_map[str(role)] = process
            for field, minimum in (
                ("pid", 1),
                ("creation_time_ns", 1),
                ("parent_pid", 0),
                ("parent_creation_time_ns", 1),
            ):
                if not _exact_int(process.get(field), minimum=minimum):
                    errors.append(f"{role}_{field}_invalid")
            if process.get("job_identity") != job_identity:
                errors.append(f"{role}_job_identity_mismatch")
        if set(role_map) != set(PROCESS_ROLES):
            errors.append("root_child_grandchild_inventory_required")
        else:
            root = role_map["root"]
            child = role_map["child"]
            grandchild = role_map["grandchild"]
            if (child.get("parent_pid"), child.get("parent_creation_time_ns")) != (
                root.get("pid"),
                root.get("creation_time_ns"),
            ):
                errors.append("child_parent_creation_identity_mismatch")
            if (grandchild.get("parent_pid"), grandchild.get("parent_creation_time_ns")) != (
                child.get("pid"),
                child.get("creation_time_ns"),
            ):
                errors.append("grandchild_parent_creation_identity_mismatch")
            pids = [item.get("pid") for item in role_map.values()]
            identities = [
                (item.get("pid"), item.get("creation_time_ns")) for item in role_map.values()
            ]
            if len(set(pids)) != 3:
                errors.append("overlapping_process_pid_not_unique")
            if len(set(identities)) != 3:
                errors.append("pid_creation_identity_not_unique")
            creation_order = [
                root.get("creation_time_ns"),
                child.get("creation_time_ns"),
                grandchild.get("creation_time_ns"),
            ]
            if not all(type(item) is int for item in creation_order) or not (
                creation_order[0] < creation_order[1] < creation_order[2]
            ):
                errors.append("process_creation_order_invalid")

    event_times = _validate_events(
        transcript.get("events"), expected_order=WINDOWS_EVENT_ORDER, label="windows", errors=errors
    )
    if event_times:
        wait_started = event_times["root_exited"]
        zero_1 = event_times["active_process_zero_observation_1"]
        zero_2 = event_times["active_process_zero_observation_2"]
        if zero_2 - zero_1 < STABLE_ZERO_MIN_INTERVAL_NS:
            errors.append("windows_stable_zero_interval_too_short")
        if zero_2 - wait_started > RESIDUAL_WAIT_NS:
            errors.append("windows_residual_wait_exceeds_120s")
    if (
        not _exact_int(transcript.get("active_process_count"))
        or transcript.get("active_process_count") != 0
    ):
        errors.append("active_process_count_nonzero_or_invalid")
    if type(transcript.get("residual_pids")) is not list or transcript.get("residual_pids") != []:
        errors.append("residual_pid_present_or_unknown")
    if transcript.get("stable_zero_observations") != 2:
        errors.append("stable_zero_repoll_must_be_exactly_two")
    return _result("windows", transcript, errors)


def _validate_wsl_process_group_qualification_for_test(
    transcript: Mapping[str, Any], *, expected: _QualificationExpectationForTest
) -> QualificationValidation:
    """Validate launcher-exit residual observation and stable natural drain."""

    if not isinstance(transcript, Mapping):
        return _result("wsl", {}, ["transcript_mapping_required"])
    errors: list[str] = []
    _require_exact_keys(transcript, WSL_FIELDS, "wsl_transcript", errors)
    _require_common(transcript, schema=WSL_SCHEMA, domain="wsl", expected=expected, errors=errors)
    if transcript.get("scenario") != "launcher_exit_linux_process_residual":
        errors.append("required_wsl_scenario_missing")
    if (
        not _exact_int(transcript.get("launch_count"), minimum=1)
        or transcript.get("launch_count") != 1
    ):
        errors.append("launch_count_must_be_exact_one")
    for field, wanted, error in (
        ("setsid_process_group", True, "setsid_process_group_unproven"),
        ("launcher_exit_observed", True, "launcher_exit_unproven"),
        ("ack_residual_observed_after_launcher_exit", True, "post_launcher_residual_unproven"),
        ("stdout_drained", True, "stdout_not_drained"),
        ("stderr_drained", True, "stderr_not_drained"),
        ("safe_for_followup", True, "safe_for_followup_false"),
    ):
        if not _exact_bool(transcript.get(field), wanted):
            errors.append(error)
    root_pid = transcript.get("root_linux_pid")
    root_start = transcript.get("root_start_time_ticks")
    process_group = transcript.get("process_group_id")
    if not _exact_int(root_pid, minimum=1):
        errors.append("root_linux_pid_invalid")
    if not _exact_int(root_start, minimum=1):
        errors.append("root_start_time_invalid")
    if not _exact_int(process_group, minimum=1) or process_group != root_pid:
        errors.append("process_group_identity_mismatch")

    ack = transcript.get("ack_residual_processes")
    residual_identities: list[tuple[object, object]] = []
    if type(ack) is not list or not ack:
        errors.append("ack_residual_inventory_missing")
    else:
        fields = frozenset({"pid", "start_time_ticks", "run_uuid", "process_group_id"})
        for item_value in ack:
            item = _mapping(item_value)
            if item is None:
                errors.append("ack_residual_entry_mapping_required")
                continue
            _require_exact_keys(item, fields, "ack_residual", errors)
            if not _exact_int(item.get("pid"), minimum=1):
                errors.append("ack_residual_pid_invalid")
            if not _exact_int(item.get("start_time_ticks"), minimum=1):
                errors.append("ack_residual_start_time_invalid")
            if item.get("run_uuid") != expected.run_uuid:
                errors.append("ack_residual_run_uuid_mismatch")
            if item.get("process_group_id") != process_group:
                errors.append("ack_residual_process_group_mismatch")
            residual_identities.append((item.get("pid"), item.get("start_time_ticks")))
        if len(set(residual_identities)) != len(residual_identities):
            errors.append("ack_residual_duplicate_identity")

    event_times = _validate_events(
        transcript.get("events"), expected_order=WSL_EVENT_ORDER, label="wsl", errors=errors
    )
    if event_times:
        wait_started = event_times["ack_residual_observed"]
        zero_1 = event_times["stable_zero_observation_1"]
        zero_2 = event_times["stable_zero_observation_2"]
        if zero_2 - zero_1 < STABLE_ZERO_MIN_INTERVAL_NS:
            errors.append("wsl_stable_zero_interval_too_short")
        if zero_2 - wait_started > RESIDUAL_WAIT_NS:
            errors.append("wsl_residual_wait_exceeds_120s")
    if transcript.get("stable_zero_observations") != 2:
        errors.append("stable_zero_repoll_must_be_exactly_two")
    for field in (
        "final_uuid_process_count",
        "final_process_group_count",
        "wsl_shutdown_calls",
        "docker_calls",
        "runtime_probe_calls",
    ):
        if not _exact_int(transcript.get(field)) or transcript.get(field) != 0:
            errors.append(f"{field}_must_be_exact_zero")
    if transcript.get("final_proc_observation") != "exact_zero":
        errors.append("final_proc_observation_not_exact_zero")
    return _result("wsl", transcript, errors)


def validate_windows_job_qualification(
    transcript: Mapping[str, Any],
) -> QualificationValidation:
    """Public fail-closed entry until an external authority adapter exists."""

    del transcript
    return _result("windows", {}, ["external_qualification_authority_unconfigured"])


def validate_wsl_process_group_qualification(
    transcript: Mapping[str, Any],
) -> QualificationValidation:
    """Public fail-closed entry until an external authority adapter exists."""

    del transcript
    return _result("wsl", {}, ["external_qualification_authority_unconfigured"])


def source_contract() -> dict[str, Any]:
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.windows-wsl-validator.v2",
        "transcript_only_no_process_launch": True,
        "public_validator_external_authority_required_and_unconfigured": True,
        "private_syntax_validation_test_seam_only": True,
        "exact_top_level_and_nested_schema_required": True,
        "canonical_nonfinite_rejected": True,
        "windows_create_suspended_and_job_before_resume_required": True,
        "suspended_actual_image_and_ancestor_identity_match_required": True,
        "root_child_grandchild_parent_creation_identity_required": True,
        "event_sequence_timestamp_and_no_duplicate_required": True,
        "stdio_late_close_and_drain_required": True,
        "breakaway_attempt_denial_required": True,
        "wsl_launcher_exit_residual_ack_and_stable_zero_required": True,
        "residual_wait_contract_seconds": 120,
        "stable_zero_minimum_interval_seconds": 1,
        "pid_reuse_disambiguated_by_creation_time": True,
        "forced_termination_calls": 0,
        "retry_count": 0,
        "actual_windows_qualification_executed": False,
        "actual_wsl_qualification_executed": False,
        "external_execution_authority_configured": False,
        "same_token_hostile_admin_protected": False,
        "production_go_enabled": False,
        "go_evidence_eligible": False,
    }


__all__ = [
    "QualificationValidation",
    "WINDOWS_EVENT_ORDER",
    "WINDOWS_SCHEMA",
    "WSL_EVENT_ORDER",
    "WSL_SCHEMA",
    "source_contract",
    "validate_windows_job_qualification",
    "validate_wsl_process_group_qualification",
]
