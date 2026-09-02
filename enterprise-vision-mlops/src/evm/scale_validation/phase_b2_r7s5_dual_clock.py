"""Pure r7s5 dual-clock protocol validator.

No collector is launched here.  The validator proves only a non-credit
qualification envelope produced by two separately-clocked collectors.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.dual-clock-qualification.v1"
DOMAINS = ("windows_host", "wsl_ubuntu")
CLOCK_DOMAINS = {
    "windows_host": "windows_query_performance_counter",
    "wsl_ubuntu": "linux_clock_monotonic_raw",
}
DURATION_MS = 180_000
CADENCE_MS = 100
SAMPLE_COUNT = 1_800
LAST_OFFSET_MS = 179_900
STDOUT_LIMIT_BYTES = 4 * 1024 * 1024
STDERR_LIMIT_BYTES = 256 * 1024
HEX64_RE = re.compile(r"[0-9a-f]{64}")
HEX40_RE = re.compile(r"[0-9a-f]{40}")


class R7S5DualClockError(ValueError):
    """Raised when dual-collector provenance or timing is ambiguous."""


@dataclass(frozen=True, slots=True)
class _DualClockExpectationForTest:
    global_run_id: str
    run_uuid: str
    attempt_uuid: str
    capture_id: str
    domain_binding_sha256s: Mapping[str, str]


def _canonical(value: object) -> bytes:
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


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise R7S5DualClockError(f"{label}_object_required")
    return dict(value)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise R7S5DualClockError(f"{label}_fields_mismatch")


def _strict_int(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise R7S5DualClockError(f"{label}_integer_invalid")
    return value


def _strict_bool(value: object, expected: bool, label: str) -> None:
    if type(value) is not bool or value is not expected:
        raise R7S5DualClockError(f"{label}_must_be_{str(expected).lower()}")


def _uuid4(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise R7S5DualClockError(f"{label}_uuid4_invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise R7S5DualClockError(f"{label}_uuid4_invalid") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise R7S5DualClockError(f"{label}_uuid4_not_canonical")
    return value


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or HEX64_RE.fullmatch(value) is None:
        raise R7S5DualClockError(f"{label}_sha256_invalid")
    return value


@dataclass(slots=True)
class DualCollectorStateMachine:
    """Enforce both READY transitions before one shared start fence."""

    ready: set[str] = field(default_factory=set)
    completed: set[str] = field(default_factory=set)
    fence_id: str | None = None
    fence_timestamp_ns: int | None = None
    last_timestamp_ns: int | None = None

    def apply(self, value: object, expected_sequence: int) -> None:
        event = _mapping(value, f"event_{expected_sequence}")
        _exact_keys(
            event,
            {"sequence", "event", "domain", "fence_id", "parent_monotonic_ns"},
            f"event_{expected_sequence}",
        )
        if _strict_int(event["sequence"], "event_sequence") != expected_sequence:
            raise R7S5DualClockError("event_sequence_not_contiguous")
        timestamp = _strict_int(event["parent_monotonic_ns"], "event_parent_monotonic_ns")
        if self.last_timestamp_ns is not None and timestamp < self.last_timestamp_ns:
            raise R7S5DualClockError("event_parent_clock_regressed")
        self.last_timestamp_ns = timestamp
        kind = event["event"]
        domain = event["domain"]
        fence = event["fence_id"]
        if kind == "ready":
            if domain not in DOMAINS or fence is not None or self.fence_id is not None:
                raise R7S5DualClockError("ready_transition_invalid")
            if domain in self.ready:
                raise R7S5DualClockError("ready_transition_duplicate")
            self.ready.add(domain)
            return
        if kind == "start_fence":
            if domain is not None or not isinstance(fence, str) or not fence:
                raise R7S5DualClockError("start_fence_transition_invalid")
            if self.ready != set(DOMAINS) or self.fence_id is not None:
                raise R7S5DualClockError("start_fence_requires_both_ready_once")
            self.fence_id = fence
            self.fence_timestamp_ns = timestamp
            return
        if kind == "complete":
            if (
                domain not in DOMAINS
                or self.fence_id is None
                or fence != self.fence_id
                or domain in self.completed
            ):
                raise R7S5DualClockError("complete_transition_invalid")
            self.completed.add(domain)
            return
        raise R7S5DualClockError("event_kind_invalid")

    def finish(self) -> None:
        if self.ready != set(DOMAINS):
            raise R7S5DualClockError("both_collectors_not_ready")
        if self.fence_id is None or self.fence_timestamp_ns is None:
            raise R7S5DualClockError("single_start_fence_missing")
        if self.completed != set(DOMAINS):
            raise R7S5DualClockError("both_collectors_not_complete")


def _validate_stream(value: object, *, label: str, expected_limit: int) -> None:
    stream = _mapping(value, label)
    _exact_keys(
        stream,
        {"limit_bytes", "observed_bytes", "captured_bytes", "overflowed", "drained"},
        label,
    )
    limit = _strict_int(stream["limit_bytes"], f"{label}_limit", minimum=1)
    if limit != expected_limit:
        raise R7S5DualClockError(f"{label}_limit_mismatch")
    observed = _strict_int(stream["observed_bytes"], f"{label}_observed")
    captured = _strict_int(stream["captured_bytes"], f"{label}_captured")
    if observed > limit or captured != observed:
        raise R7S5DualClockError(f"{label}_bounded_capture_mismatch")
    _strict_bool(stream["overflowed"], False, f"{label}_overflowed")
    _strict_bool(stream["drained"], True, f"{label}_drained")


_DOMAIN_FIELDS = {
    "domain",
    "clock_domain",
    "global_run_id",
    "domain_run_id",
    "run_uuid",
    "attempt_uuid",
    "capture_id",
    "fence_id",
    "collector_sha256",
    "raw_artifact_sha256",
    "raw_artifact_bytes",
    "git_commit",
    "git_tree",
    "launcher_sha256",
    "outer_sha256",
    "bridge_sha256",
    "runner_sha256",
    "interpreter_sha256",
    "interpreter_path",
    "interpreter_version",
    "wsl_distro",
    "invocation_arguments",
    "root_pid",
    "root_creation_identity",
    "containment_kind",
    "containment_identity",
    "wall_clock_basis",
    "monotonic_clock_basis",
    "dispatch_requested_parent_monotonic_ns",
    "acknowledged_parent_monotonic_ns",
    "ready_parent_monotonic_ns",
    "started_parent_monotonic_ns",
    "first_sample_parent_monotonic_ns",
    "last_sample_parent_monotonic_ns",
    "ended_parent_monotonic_ns",
    "process_exited_parent_monotonic_ns",
    "stream_drained_parent_monotonic_ns",
    "sample_count",
    "sample_sequences",
    "sample_offsets_ms",
    "sample_parent_monotonic_ns",
    "sample_wall_time_ns",
    "discontinuity_count",
    "backward_step_count",
    "unclassified_gap_count",
    "bracket_violation_count",
    "stdout",
    "stderr",
    "residual_state",
    "residual_pids",
}

_DOMAIN_BINDING_FIELDS = (
    "domain",
    "clock_domain",
    "global_run_id",
    "domain_run_id",
    "run_uuid",
    "attempt_uuid",
    "capture_id",
    "collector_sha256",
    "raw_artifact_sha256",
    "raw_artifact_bytes",
    "git_commit",
    "git_tree",
    "launcher_sha256",
    "outer_sha256",
    "bridge_sha256",
    "runner_sha256",
    "interpreter_sha256",
    "interpreter_path",
    "interpreter_version",
    "wsl_distro",
    "invocation_arguments",
    "root_pid",
    "root_creation_identity",
    "containment_kind",
    "containment_identity",
    "wall_clock_basis",
    "monotonic_clock_basis",
)


def _domain_binding_sha256(value: Mapping[str, Any]) -> str:
    projection = {field: value[field] for field in _DOMAIN_BINDING_FIELDS}
    return hashlib.sha256(_canonical(projection)).hexdigest()


def _validate_domain(
    name: str,
    value: object,
    *,
    run_uuid: str,
    attempt_uuid: str,
    capture_id: str,
    global_run_id: str,
    expected_binding_sha256: str,
    machine: DualCollectorStateMachine,
    event_log: Sequence[Mapping[str, Any]],
) -> None:
    domain = _mapping(value, f"domain_{name}")
    _exact_keys(domain, _DOMAIN_FIELDS, f"domain_{name}")
    if domain["domain"] != name or domain["clock_domain"] != CLOCK_DOMAINS[name]:
        raise R7S5DualClockError(f"domain_identity_or_clock_swapped:{name}")
    _uuid4(domain["domain_run_id"], f"{name}_domain_run")
    if (
        domain["global_run_id"] != global_run_id
        or domain["run_uuid"] != run_uuid
        or domain["attempt_uuid"] != attempt_uuid
        or domain["capture_id"] != capture_id
        or domain["fence_id"] != machine.fence_id
    ):
        raise R7S5DualClockError(f"domain_binding_mismatch:{name}")
    for sha_field in (
        "collector_sha256",
        "raw_artifact_sha256",
        "launcher_sha256",
        "outer_sha256",
        "bridge_sha256",
        "runner_sha256",
        "interpreter_sha256",
    ):
        _sha256(domain[sha_field], f"{name}_{sha_field}")
    for git_field in ("git_commit", "git_tree"):
        if not isinstance(domain[git_field], str) or HEX40_RE.fullmatch(domain[git_field]) is None:
            raise R7S5DualClockError(f"{name}_{git_field}_invalid")
    _strict_int(domain["raw_artifact_bytes"], f"{name}_raw_artifact_bytes", minimum=1)
    interpreter_path = domain["interpreter_path"]
    if (
        not isinstance(interpreter_path, str)
        or not interpreter_path
        or "\x00" in interpreter_path
        or (name == "windows_host" and not ntpath.isabs(interpreter_path))
        or (name == "wsl_ubuntu" and not interpreter_path.startswith("/"))
    ):
        raise R7S5DualClockError(f"{name}_interpreter_path_invalid")
    if not isinstance(domain["interpreter_version"], str) or not domain["interpreter_version"]:
        raise R7S5DualClockError(f"{name}_interpreter_version_invalid")
    expected_distro = None if name == "windows_host" else "Ubuntu"
    if domain["wsl_distro"] != expected_distro:
        raise R7S5DualClockError(f"{name}_wsl_distro_invalid")
    arguments = domain["invocation_arguments"]
    if (
        not isinstance(arguments, list)
        or not arguments
        or any(not isinstance(item, str) or not item or "\x00" in item for item in arguments)
    ):
        raise R7S5DualClockError(f"{name}_invocation_arguments_invalid")
    _strict_int(domain["root_pid"], f"{name}_root_pid", minimum=1)
    for identity_field in ("root_creation_identity", "containment_identity"):
        if not isinstance(domain[identity_field], str) or not domain[identity_field]:
            raise R7S5DualClockError(f"{name}_{identity_field}_invalid")
    expected_containment = "windows_job_object" if name == "windows_host" else "linux_process_group"
    if domain["containment_kind"] != expected_containment:
        raise R7S5DualClockError(f"{name}_containment_kind_invalid")
    if domain["wall_clock_basis"] != "utc_system_clock_observational_only":
        raise R7S5DualClockError(f"{name}_wall_clock_basis_invalid")
    if domain["monotonic_clock_basis"] != CLOCK_DOMAINS[name]:
        raise R7S5DualClockError(f"{name}_monotonic_clock_basis_invalid")
    _sha256(expected_binding_sha256, f"{name}_expected_binding")
    if _domain_binding_sha256(domain) != expected_binding_sha256:
        raise R7S5DualClockError(f"{name}_external_binding_mismatch")

    dispatch_ns = _strict_int(
        domain["dispatch_requested_parent_monotonic_ns"], f"{name}_dispatch_ns"
    )
    acknowledged_ns = _strict_int(
        domain["acknowledged_parent_monotonic_ns"], f"{name}_acknowledged_ns"
    )
    ready_ns = _strict_int(domain["ready_parent_monotonic_ns"], f"{name}_ready_ns")
    started_ns = _strict_int(domain["started_parent_monotonic_ns"], f"{name}_started_ns")
    first_sample_ns = _strict_int(
        domain["first_sample_parent_monotonic_ns"], f"{name}_first_sample_ns"
    )
    last_sample_ns = _strict_int(
        domain["last_sample_parent_monotonic_ns"], f"{name}_last_sample_ns"
    )
    ended_ns = _strict_int(domain["ended_parent_monotonic_ns"], f"{name}_ended_ns")
    process_exited_ns = _strict_int(
        domain["process_exited_parent_monotonic_ns"], f"{name}_process_exited_ns"
    )
    stream_drained_ns = _strict_int(
        domain["stream_drained_parent_monotonic_ns"], f"{name}_stream_drained_ns"
    )
    assert machine.fence_timestamp_ns is not None
    if not (
        dispatch_ns
        <= acknowledged_ns
        == ready_ns
        <= machine.fence_timestamp_ns
        <= started_ns
        <= first_sample_ns
        <= last_sample_ns
        <= ended_ns
        <= process_exited_ns
        <= stream_drained_ns
    ):
        raise R7S5DualClockError(f"domain_fence_timing_invalid:{name}")
    collection_duration_ns = ended_ns - started_ns
    if collection_duration_ns < LAST_OFFSET_MS * 1_000_000:
        raise R7S5DualClockError(f"domain_collection_duration_too_short:{name}")
    if collection_duration_ns > DURATION_MS * 1_000_000:
        raise R7S5DualClockError(f"domain_collection_duration_exceeds_180000ms:{name}")
    duration_from_fence_ns = ended_ns - machine.fence_timestamp_ns
    if duration_from_fence_ns < LAST_OFFSET_MS * 1_000_000:
        raise R7S5DualClockError(f"domain_window_too_short:{name}")
    if duration_from_fence_ns > DURATION_MS * 1_000_000:
        raise R7S5DualClockError(f"domain_window_exceeds_180000ms:{name}")
    if _strict_int(domain["sample_count"], f"{name}_sample_count") != SAMPLE_COUNT:
        raise R7S5DualClockError(f"domain_sample_count_mismatch:{name}")
    sequences = domain["sample_sequences"]
    if sequences != list(range(SAMPLE_COUNT)) or any(type(item) is not int for item in sequences):
        raise R7S5DualClockError(f"domain_sample_sequence_not_exact:{name}")
    offsets = domain["sample_offsets_ms"]
    if not isinstance(offsets, list) or any(type(item) is not int for item in offsets):
        raise R7S5DualClockError(f"domain_offsets_invalid:{name}")
    if offsets != list(range(0, DURATION_MS, CADENCE_MS)):
        raise R7S5DualClockError(f"domain_offsets_not_exact:{name}")
    sample_times = domain["sample_parent_monotonic_ns"]
    if (
        not isinstance(sample_times, list)
        or len(sample_times) != SAMPLE_COUNT
        or any(type(item) is not int for item in sample_times)
    ):
        raise R7S5DualClockError(f"domain_sample_timestamps_invalid:{name}")
    for index, (offset_ms, timestamp_ns) in enumerate(zip(offsets, sample_times, strict=True)):
        lower = started_ns + offset_ms * 1_000_000
        upper = lower + CADENCE_MS * 1_000_000
        if timestamp_ns < lower or timestamp_ns >= upper:
            raise R7S5DualClockError(f"domain_sample_dispatch_bracket_invalid:{name}:{index}")
    if (
        first_sample_ns != sample_times[0]
        or last_sample_ns != sample_times[-1]
        or any(later <= earlier for earlier, later in zip(sample_times, sample_times[1:]))
    ):
        raise R7S5DualClockError(f"domain_sample_timestamp_order_or_boundary_invalid:{name}")
    if last_sample_ns >= started_ns + DURATION_MS * 1_000_000:
        raise R7S5DualClockError(f"domain_sample_after_strict_180s:{name}")
    wall_times = domain["sample_wall_time_ns"]
    if (
        not isinstance(wall_times, list)
        or len(wall_times) != SAMPLE_COUNT
        or any(type(item) is not int or item <= 0 for item in wall_times)
        or any(later < earlier for earlier, later in zip(wall_times, wall_times[1:]))
    ):
        raise R7S5DualClockError(f"domain_wall_sample_backward_or_invalid:{name}")
    for metric in (
        "discontinuity_count",
        "backward_step_count",
        "unclassified_gap_count",
        "bracket_violation_count",
    ):
        if _strict_int(domain[metric], f"{name}_{metric}") != 0:
            raise R7S5DualClockError(f"domain_{metric}_nonzero:{name}")
    _validate_stream(domain["stdout"], label=f"{name}_stdout", expected_limit=STDOUT_LIMIT_BYTES)
    _validate_stream(domain["stderr"], label=f"{name}_stderr", expected_limit=STDERR_LIMIT_BYTES)
    if domain["residual_state"] != "zero" or domain["residual_pids"] != []:
        raise R7S5DualClockError(f"domain_residual_not_zero:{name}")

    ready_events = [row for row in event_log if row["event"] == "ready" and row["domain"] == name]
    complete_events = [
        row for row in event_log if row["event"] == "complete" and row["domain"] == name
    ]
    if (
        len(ready_events) != 1
        or ready_events[0]["parent_monotonic_ns"] != ready_ns
        or len(complete_events) != 1
        or complete_events[0]["parent_monotonic_ns"] != ended_ns
    ):
        raise R7S5DualClockError(f"domain_event_timing_mismatch:{name}")


@dataclass(frozen=True, slots=True)
class DualClockDecision:
    capture_id: str
    fence_id: str
    qualification_sha256s: tuple[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "qualified_non_credit",
            "capture_id": self.capture_id,
            "fence_id": self.fence_id,
            "collector_sha256s": list(self.qualification_sha256s),
            "acceptance_credit": False,
            "completion_credit": "non_credit_only",
            "go": False,
            "completion_marker_created": False,
            "automatic_retry_allowed": False,
            "cross_domain_raw_comparison": False,
        }


_TOP_FIELDS = {
    "schema",
    "global_run_id",
    "run_uuid",
    "attempt_uuid",
    "capture_id",
    "source_kind",
    "synthetic",
    "replayed",
    "acceptance_credit",
    "completion_credit",
    "go",
    "completion_marker_created",
    "automatic_retry_count",
    "forced_termination_attempts",
    "cross_domain_raw_comparison",
    "contract",
    "event_log",
    "domains",
}


def _validate_dual_clock_qualification_for_test(
    value: object,
    *,
    expected: _DualClockExpectationForTest,
    seen_capture_ids: Sequence[str] = (),
) -> DualClockDecision:
    if type(expected) is not _DualClockExpectationForTest:
        raise R7S5DualClockError("private_test_expectation_required")
    raw = _mapping(value, "dual_clock")
    _exact_keys(raw, _TOP_FIELDS, "dual_clock")
    if raw["schema"] != SCHEMA or raw["source_kind"] != "live_raw_collectors":
        raise R7S5DualClockError("dual_clock_schema_or_source_mismatch")
    global_run_id = _uuid4(raw["global_run_id"], "global_run")
    run_uuid = _uuid4(raw["run_uuid"], "run")
    attempt_uuid = _uuid4(raw["attempt_uuid"], "attempt")
    capture_id = _uuid4(raw["capture_id"], "capture")
    if (
        global_run_id != expected.global_run_id
        or run_uuid != expected.run_uuid
        or attempt_uuid != expected.attempt_uuid
        or capture_id != expected.capture_id
    ):
        raise R7S5DualClockError("external_capture_identity_binding_mismatch")
    if set(expected.domain_binding_sha256s) != set(DOMAINS):
        raise R7S5DualClockError("external_domain_binding_set_mismatch")
    if capture_id in seen_capture_ids:
        raise R7S5DualClockError("capture_replay")
    _strict_bool(raw["synthetic"], False, "synthetic")
    _strict_bool(raw["replayed"], False, "replayed")
    _strict_bool(raw["acceptance_credit"], False, "acceptance_credit")
    if raw["completion_credit"] != "non_credit_only":
        raise R7S5DualClockError("completion_credit_mismatch")
    _strict_bool(raw["go"], False, "go")
    _strict_bool(raw["completion_marker_created"], False, "completion_marker_created")
    if _strict_int(raw["automatic_retry_count"], "automatic_retry_count") != 0:
        raise R7S5DualClockError("automatic_retry_forbidden")
    if _strict_int(raw["forced_termination_attempts"], "forced_termination_attempts") != 0:
        raise R7S5DualClockError("forced_termination_forbidden")
    _strict_bool(raw["cross_domain_raw_comparison"], False, "cross_domain_raw_comparison")

    contract = _mapping(raw["contract"], "contract")
    expected_contract = {
        "duration_ms": DURATION_MS,
        "cadence_ms": CADENCE_MS,
        "sample_count": SAMPLE_COUNT,
        "first_offset_ms": 0,
        "last_offset_ms": LAST_OFFSET_MS,
        "max_end_after_fence_ms": DURATION_MS,
        "stdout_limit_bytes": STDOUT_LIMIT_BYTES,
        "stderr_limit_bytes": STDERR_LIMIT_BYTES,
    }
    if contract != expected_contract:
        raise R7S5DualClockError("frozen_contract_mismatch")

    event_log_raw = raw["event_log"]
    if not isinstance(event_log_raw, list):
        raise R7S5DualClockError("event_log_list_required")
    machine = DualCollectorStateMachine()
    event_log: list[Mapping[str, Any]] = []
    for sequence, value_event in enumerate(event_log_raw):
        machine.apply(value_event, sequence)
        event_log.append(_mapping(value_event, f"event_{sequence}"))
    machine.finish()

    domains = _mapping(raw["domains"], "domains")
    _exact_keys(domains, set(DOMAINS), "domains")
    for name in DOMAINS:
        _validate_domain(
            name,
            domains[name],
            run_uuid=run_uuid,
            attempt_uuid=attempt_uuid,
            capture_id=capture_id,
            global_run_id=global_run_id,
            expected_binding_sha256=expected.domain_binding_sha256s[name],
            machine=machine,
            event_log=event_log,
        )
    assert machine.fence_id is not None
    return DualClockDecision(
        capture_id=capture_id,
        fence_id=machine.fence_id,
        qualification_sha256s=tuple(domains[name]["collector_sha256"] for name in DOMAINS),
    )


def validate_dual_clock_qualification(value: object) -> DualClockDecision:
    """Public entry is closed until an external qualification authority exists."""

    del value
    raise R7S5DualClockError("external_qualification_authority_unconfigured")


def dual_clock_contract() -> dict[str, Any]:
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.dual-clock-contract.v1",
        "both_ready_before_single_start_fence": True,
        "domains": list(DOMAINS),
        "clock_domains_are_separate": True,
        "cross_domain_raw_comparison": False,
        "duration_ms": DURATION_MS,
        "cadence_ms": CADENCE_MS,
        "sample_count_per_domain": SAMPLE_COUNT,
        "sample_offsets_ms": [0, LAST_OFFSET_MS],
        "raw_sample_sequences_and_parent_monotonic_timestamps_required": True,
        "dispatch_ack_start_first_last_complete_exit_drain_required": True,
        "collector_raw_git_interpreter_and_launch_chain_provenance_required": True,
        "bounded_stream_drain_required": True,
        "residual_pid_zero_required": True,
        "synthetic_or_replay_allowed": False,
        "completion_credit": "non_credit_only",
        "success_or_completion_marker_allowed": False,
        "live_calls_implemented": False,
        "collector_liveness_or_raw_sample_authenticity_verified_by_this_module": False,
        "public_validator_external_authority_required_and_unconfigured": True,
        "private_syntax_validation_test_seam_only": True,
        "capture_replay_scope": "private_test_seam_only_caller_supplied_seen_capture_ids",
    }


__all__ = (
    "CADENCE_MS",
    "DURATION_MS",
    "DualClockDecision",
    "DualCollectorStateMachine",
    "R7S5DualClockError",
    "SAMPLE_COUNT",
    "dual_clock_contract",
    "validate_dual_clock_qualification",
)
