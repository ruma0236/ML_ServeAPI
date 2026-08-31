from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from evm.scale_validation.clock_remediation import (
    ClockRemediationThresholds,
    analyze_runtime_clock_samples,
)


FRESH_MODE = "fresh"
FRESH_DURATION_SECONDS = 180
FRESH_CADENCE_MS = 100
FRESH_CADENCE_NS = 100_000_000
FRESH_SAMPLE_COUNT = 1_800
SCHEDULE_LATENESS_LIMIT_NS = FRESH_CADENCE_NS
WINDOWS_DOMAIN = "windows_host"
WSL_DOMAIN = "wsl_ubuntu"

LIFECYCLE_SEQUENCE = (
    "compose_stop",
    "desktop_stop",
    "desktop_start",
    "compose_start",
)
EXPECTED_LIFECYCLE_COUNTS = {
    "compose_stop": 1,
    "desktop_stop": 1,
    "wsl_shutdown": 0,
    "desktop_start": 1,
    "compose_start": 1,
}

REQUIRED_RUNTIME_INVARIANTS = (
    "docker_engine",
    "compose_13_of_13",
    "kubernetes_livez",
    "kubernetes_readyz",
    "node_ready_1_of_1",
    "device_plugin_ready_1_of_1",
    "gpu_capacity_1",
    "gpu_allocatable_1",
    "b0_exact_uid",
    "b0_exact_image",
    "b0_replica_1_of_1",
    "b0_actual_cuda",
    "prometheus_5_of_5",
    "api_health_200",
    "api_ready_200",
    "api_revision_exact",
    "api_runtime_revision_matches",
    "queue_active_zero",
    "queue_leased_zero",
    "queue_outcome_unknown_zero",
    "active_claims_zero",
    "active_jobs_zero",
    "gpu_lease_zero",
    "x1_residue_zero",
    "residual_pid_zero",
)

_RAW_INTEGER_FIELDS = (
    "sequence",
    "raw_before_ns",
    "realtime_unix_ns",
    "raw_after_ns",
    "monotonic_ns",
    "auxiliary_monotonic_ns",
)

__all__ = (
    "EXPECTED_LIFECYCLE_COUNTS",
    "FRESH_CADENCE_MS",
    "FRESH_DURATION_SECONDS",
    "FRESH_SAMPLE_COUNT",
    "LIFECYCLE_SEQUENCE",
    "REQUIRED_RUNTIME_INVARIANTS",
    "ClockCollectionReport",
    "FreshContext",
    "FreshContract",
    "FreshContractError",
    "FreshEligibility",
    "FreshEvidenceExistsError",
    "FreshEvidenceValidationError",
    "FreshExecution",
    "FreshPhaseB2Error",
    "FreshPhaseB2Report",
    "SampleRequest",
    "ScheduleReport",
    "StepResult",
    "evaluate_fresh_eligibility",
    "run_fresh",
    "validate_fresh_execution",
    "write_fresh_evidence",
)


class FreshPhaseB2Error(RuntimeError):
    pass


class FreshContractError(FreshPhaseB2Error):
    pass


class FreshEvidenceExistsError(FreshPhaseB2Error):
    pass


class FreshEvidenceValidationError(FreshPhaseB2Error):
    pass


@dataclass(frozen=True)
class FreshEligibility:
    eligible: bool
    decision: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "decision": self.decision,
            "reasons": list(self.reasons),
        }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _strict_int(value: object, label: str) -> int:
    if type(value) is not int:
        raise FreshContractError(f"{label}_must_be_integer")
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def _jsonl_bytes(values: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(_canonical_json_bytes(dict(value)) for value in values)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _create_new(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise FreshEvidenceExistsError(f"evidence_path_exists:{path}") from exc
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("exclusive evidence write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _new_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise FreshEvidenceExistsError(f"evidence_directory_exists:{path}") from exc


def _validated_run_uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise FreshContractError("run_uuid_invalid") from exc
    canonical = str(parsed)
    if parsed.version != 4 or canonical != str(value).lower():
        raise FreshContractError("run_uuid_must_be_canonical_uuid4")
    return canonical


@dataclass(frozen=True)
class FreshContract:
    duration_seconds: int = FRESH_DURATION_SECONDS
    cadence_ms: int = FRESH_CADENCE_MS
    sample_count: int = FRESH_SAMPLE_COUNT
    required_invariants: tuple[str, ...] = REQUIRED_RUNTIME_INVARIANTS

    @property
    def cadence_ns(self) -> int:
        return self.cadence_ms * 1_000_000

    @property
    def duration_ns(self) -> int:
        return self.duration_seconds * 1_000_000_000

    def validate(self) -> FreshContract:
        if _strict_int(self.duration_seconds, "duration_seconds") != FRESH_DURATION_SECONDS:
            raise FreshContractError("duration_seconds_must_equal_180")
        if _strict_int(self.cadence_ms, "cadence_ms") != FRESH_CADENCE_MS:
            raise FreshContractError("cadence_ms_must_equal_100")
        if _strict_int(self.sample_count, "sample_count") != FRESH_SAMPLE_COUNT:
            raise FreshContractError("sample_count_must_equal_1800")
        if self.duration_ns // self.cadence_ns != self.sample_count:
            raise FreshContractError("duration_cadence_sample_count_mismatch")
        if tuple(self.required_invariants) != REQUIRED_RUNTIME_INVARIANTS:
            raise FreshContractError("required_runtime_invariants_must_not_be_weakened")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration_seconds": self.duration_seconds,
            "cadence_ms": self.cadence_ms,
            "cadence_ns": self.cadence_ns,
            "sample_count": self.sample_count,
            "required_invariants": list(self.required_invariants),
        }


@dataclass(frozen=True)
class FreshContext:
    run_uuid: str
    contract: FreshContract
    started_at: str
    schedule_origin_monotonic_ns: int


@dataclass(frozen=True)
class SampleRequest:
    run_uuid: str
    domain: str
    sequence: int
    target_monotonic_ns: int


@dataclass(frozen=True)
class StepResult:
    name: str
    passed: bool
    timed_out: bool = False
    manual_intervention_required: bool = False
    residual_pids: tuple[int, ...] = ()
    error: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def normalize(cls, name: str, raw: StepResult | Mapping[str, Any] | bool) -> StepResult:
        if isinstance(raw, StepResult):
            if raw.name != name:
                raise FreshContractError(f"step_name_mismatch:{name}:{raw.name}")
            return raw
        if type(raw) is bool:
            return cls(name=name, passed=raw)
        if not isinstance(raw, Mapping) or type(raw.get("passed")) is not bool:
            raise FreshContractError(f"step_result_invalid:{name}")
        residual_values = raw.get("residual_pids", ())
        if not isinstance(residual_values, Sequence) or isinstance(residual_values, (str, bytes)):
            raise FreshContractError(f"step_residual_pids_invalid:{name}")
        residual_pids = tuple(
            sorted({_strict_int(value, f"{name}_residual_pid") for value in residual_values})
        )
        if any(value <= 0 for value in residual_pids):
            raise FreshContractError(f"step_residual_pid_nonpositive:{name}")
        details = raw.get("details", {})
        if not isinstance(details, Mapping):
            raise FreshContractError(f"step_details_invalid:{name}")
        error = raw.get("error")
        if error is not None and not isinstance(error, str):
            raise FreshContractError(f"step_error_invalid:{name}")
        for boolean_name in ("timed_out", "manual_intervention_required"):
            if boolean_name in raw and type(raw[boolean_name]) is not bool:
                raise FreshContractError(f"step_{boolean_name}_invalid:{name}")
        return cls(
            name=name,
            passed=raw["passed"],
            timed_out=raw.get("timed_out", False),
            manual_intervention_required=raw.get("manual_intervention_required", False),
            residual_pids=residual_pids,
            error=error,
            details=dict(details),
        )

    @property
    def clean_pass(self) -> bool:
        return (
            self.passed
            and not self.timed_out
            and not self.manual_intervention_required
            and not self.residual_pids
            and self.error is None
        )

    @property
    def blocking(self) -> bool:
        return self.timed_out or self.manual_intervention_required or bool(self.residual_pids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "timed_out": self.timed_out,
            "manual_intervention_required": self.manual_intervention_required,
            "residual_pids": list(self.residual_pids),
            "error": self.error,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ScheduleReport:
    sample_count: int
    origin_monotonic_ns: int
    window_end_monotonic_ns: int
    observed_end_monotonic_ns: int
    duration_reached: bool
    early_sample_count: int
    lateness_violation_count: int
    monotonic_regression_count: int
    cadence_gap_count: int
    max_lateness_ns: int
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "origin_monotonic_ns": self.origin_monotonic_ns,
            "window_end_monotonic_ns": self.window_end_monotonic_ns,
            "observed_end_monotonic_ns": self.observed_end_monotonic_ns,
            "duration_reached": self.duration_reached,
            "early_sample_count": self.early_sample_count,
            "lateness_violation_count": self.lateness_violation_count,
            "monotonic_regression_count": self.monotonic_regression_count,
            "cadence_gap_count": self.cadence_gap_count,
            "max_lateness_ns": self.max_lateness_ns,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class ClockCollectionReport:
    domain: str
    sample_count: int
    raw_sha256: str
    offset_discontinuity_count: int
    backward_step_count: int
    unclassified_gap_count: int
    bracket_violation_count: int
    sampler_gap_count: int
    passed: bool
    error: str | None
    analysis: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "sample_count": self.sample_count,
            "raw_sha256": self.raw_sha256,
            "offset_discontinuity_count": self.offset_discontinuity_count,
            "backward_step_count": self.backward_step_count,
            "unclassified_gap_count": self.unclassified_gap_count,
            "bracket_violation_count": self.bracket_violation_count,
            "sampler_gap_count": self.sampler_gap_count,
            "passed": self.passed,
            "error": self.error,
            "analysis": dict(self.analysis),
        }


@dataclass(frozen=True)
class FreshPhaseB2Report:
    schema: str
    mode: str
    full_execution: bool
    source_kind: str
    run_uuid: str
    started_at: str
    ended_at: str
    contract: FreshContract
    preflight: StepResult
    lifecycle: tuple[StepResult, ...]
    recovery: StepResult
    windows: ClockCollectionReport
    wsl: ClockCollectionReport
    schedule: ScheduleReport
    call_counts: Mapping[str, int]
    runtime_invariants: Mapping[str, bool]
    residual_pids: tuple[int, ...]
    errors: tuple[str, ...]
    manual_intervention_required: bool
    success_eligible: bool
    decision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "mode": self.mode,
            "full_execution": self.full_execution,
            "source_kind": self.source_kind,
            "run_uuid": self.run_uuid,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "contract": self.contract.to_dict(),
            "preflight": self.preflight.to_dict(),
            "lifecycle": [value.to_dict() for value in self.lifecycle],
            "recovery": self.recovery.to_dict(),
            "windows": self.windows.to_dict(),
            "wsl": self.wsl.to_dict(),
            "schedule": self.schedule.to_dict(),
            "call_counts": dict(self.call_counts),
            "runtime_invariants": dict(self.runtime_invariants),
            "residual_pids": list(self.residual_pids),
            "errors": list(self.errors),
            "manual_intervention_required": self.manual_intervention_required,
            "success_eligible": self.success_eligible,
            "decision": self.decision,
            "eligibility": evaluate_fresh_eligibility(self).to_dict(),
        }


@dataclass(frozen=True)
class FreshExecution:
    report: FreshPhaseB2Report
    windows_samples: tuple[Mapping[str, Any], ...]
    wsl_samples: tuple[Mapping[str, Any], ...]
    schedule_observations: tuple[Mapping[str, int], ...]

    @property
    def success_eligible(self) -> bool:
        return self.report.success_eligible

    @property
    def eligibility(self) -> FreshEligibility:
        return evaluate_fresh_eligibility(self.report)


def _empty_step(name: str, error: str) -> StepResult:
    return StepResult(name=name, passed=False, error=error)


def _empty_clock_report(
    domain: str, samples: Sequence[Mapping[str, Any]], error: str
) -> ClockCollectionReport:
    return ClockCollectionReport(
        domain=domain,
        sample_count=len(samples),
        raw_sha256=_sha256_bytes(_jsonl_bytes(samples)),
        offset_discontinuity_count=-1,
        backward_step_count=-1,
        unclassified_gap_count=-1,
        bracket_violation_count=-1,
        sampler_gap_count=-1,
        passed=False,
        error=error,
        analysis={},
    )


def _normalize_sample(
    raw: Mapping[str, Any],
    request: SampleRequest,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise FreshContractError(f"sample_not_mapping:{request.domain}:{request.sequence}")
    value = dict(raw)
    for field_name in _RAW_INTEGER_FIELDS:
        _strict_int(value.get(field_name), f"{request.domain}_{field_name}")
    if value["domain"] != request.domain:
        raise FreshContractError(
            f"sample_domain_mismatch:{request.domain}:{request.sequence}:{value['domain']}"
        )
    if value["sequence"] != request.sequence:
        raise FreshContractError(
            f"sample_sequence_mismatch:{request.domain}:{request.sequence}:{value['sequence']}"
        )
    supplied_uuid = value.get("run_uuid")
    if supplied_uuid is not None and supplied_uuid != request.run_uuid:
        raise FreshContractError(f"sample_run_uuid_mismatch:{request.domain}:{request.sequence}")
    supplied_target = value.get("scheduled_monotonic_ns")
    if supplied_target is not None and supplied_target != request.target_monotonic_ns:
        raise FreshContractError(f"sample_schedule_mismatch:{request.domain}:{request.sequence}")
    value["run_uuid"] = request.run_uuid
    value["scheduled_monotonic_ns"] = request.target_monotonic_ns
    return value


def _clock_report(
    domain: str,
    samples: Sequence[Mapping[str, Any]],
    contract: FreshContract,
) -> ClockCollectionReport:
    raw_sha256 = _sha256_bytes(_jsonl_bytes(samples))
    try:
        if len(samples) != contract.sample_count:
            raise ValueError(f"expected {contract.sample_count} samples, observed {len(samples)}")
        for sequence, sample in enumerate(samples):
            if sample.get("domain") != domain:
                raise ValueError(f"domain mismatch at sequence {sequence}")
            if sample.get("sequence") != sequence:
                raise ValueError(f"sequence mismatch at sequence {sequence}")
            if sample.get("run_uuid") is None:
                raise ValueError(f"run UUID missing at sequence {sequence}")
        analysis = analyze_runtime_clock_samples(
            samples,
            thresholds=ClockRemediationThresholds(
                sample_count=contract.sample_count,
                cadence_ns=contract.cadence_ns,
            ),
        )
        discontinuities = int(analysis["offset_step_count"])
        backward = int(analysis["backward_wall_step_count"])
        unclassified = int(analysis["unclassified_sampler_gap_count"])
        bracket = int(analysis["bracket_violation_count"])
        gaps = int(analysis["sampler_gap_count"])
        passed = (
            analysis.get("passed") is True
            and discontinuities == 0
            and backward == 0
            and unclassified == 0
            and bracket == 0
            and gaps == 0
        )
        return ClockCollectionReport(
            domain=domain,
            sample_count=len(samples),
            raw_sha256=raw_sha256,
            offset_discontinuity_count=discontinuities,
            backward_step_count=backward,
            unclassified_gap_count=unclassified,
            bracket_violation_count=bracket,
            sampler_gap_count=gaps,
            passed=passed,
            error=None if passed else "clock_acceptance_metric_nonzero",
            analysis=analysis,
        )
    except (FreshContractError, KeyError, TypeError, ValueError) as exc:
        return ClockCollectionReport(
            domain=domain,
            sample_count=len(samples),
            raw_sha256=raw_sha256,
            offset_discontinuity_count=-1,
            backward_step_count=-1,
            unclassified_gap_count=-1,
            bracket_violation_count=-1,
            sampler_gap_count=-1,
            passed=False,
            error=f"clock_analysis_failed:{exc}",
            analysis={},
        )


def _schedule_report(
    observations: Sequence[Mapping[str, int]],
    *,
    origin_ns: int,
    observed_end_ns: int,
    contract: FreshContract,
) -> ScheduleReport:
    early = 0
    late = 0
    regressions = 0
    gaps = 0
    max_lateness = 0
    previous_observed: int | None = None
    for expected_sequence, value in enumerate(observations):
        sequence = _strict_int(value.get("sequence"), "schedule_sequence")
        target = _strict_int(value.get("target_monotonic_ns"), "schedule_target")
        observed = _strict_int(value.get("observed_monotonic_ns"), "schedule_observed")
        expected_target = origin_ns + expected_sequence * contract.cadence_ns
        if sequence != expected_sequence or target != expected_target:
            regressions += 1
        lateness = observed - target
        if lateness < 0:
            early += 1
        else:
            max_lateness = max(max_lateness, lateness)
            if lateness > SCHEDULE_LATENESS_LIMIT_NS:
                late += 1
        if previous_observed is not None:
            delta = observed - previous_observed
            if delta < 0:
                regressions += 1
            elif delta > contract.cadence_ns + SCHEDULE_LATENESS_LIMIT_NS:
                gaps += 1
        previous_observed = observed
    window_end = origin_ns + contract.duration_ns
    reached = observed_end_ns >= window_end
    passed = (
        len(observations) == contract.sample_count
        and reached
        and early == 0
        and late == 0
        and regressions == 0
        and gaps == 0
    )
    return ScheduleReport(
        sample_count=len(observations),
        origin_monotonic_ns=origin_ns,
        window_end_monotonic_ns=window_end,
        observed_end_monotonic_ns=observed_end_ns,
        duration_reached=reached,
        early_sample_count=early,
        lateness_violation_count=late,
        monotonic_regression_count=regressions,
        cadence_gap_count=gaps,
        max_lateness_ns=max_lateness,
        passed=passed,
    )


def _sleep_until(
    target_ns: int,
    *,
    monotonic_ns: Callable[[], int],
    sleep: Callable[[float], None],
) -> int:
    previous = _strict_int(monotonic_ns(), "monotonic_clock")
    for _attempt in range(1_000):
        if previous >= target_ns:
            return previous
        sleep((target_ns - previous) / 1_000_000_000)
        observed = _strict_int(monotonic_ns(), "monotonic_clock")
        if observed < previous:
            raise FreshContractError("scheduler_monotonic_clock_regressed")
        previous = observed
    raise FreshContractError("scheduler_sleep_did_not_reach_target")


def _invoke_step(
    name: str,
    callback: Callable[[FreshContext], StepResult | Mapping[str, Any] | bool],
    context: FreshContext,
) -> StepResult:
    try:
        return StepResult.normalize(name, callback(context))
    except Exception as exc:  # callback is an integration boundary
        return StepResult(
            name=name,
            passed=False,
            manual_intervention_required=True,
            error=f"callback_exception:{type(exc).__name__}:{exc}",
        )


def _expected_success_call_counts(contract: FreshContract) -> dict[str, int]:
    return {
        "preflight": 1,
        # The raw Windows/WSL collection is one Docker-off probe attempt.
        # Individual sampler calls remain separately accounted so a partial
        # or duplicated collection cannot be presented as a single success.
        "docker_off_probe": 1,
        **EXPECTED_LIFECYCLE_COUNTS,
        "windows_sampler": contract.sample_count,
        "wsl_sampler": contract.sample_count,
        "recovery": 1,
        "invariant_probe": 1,
    }


def evaluate_fresh_eligibility(report: FreshPhaseB2Report) -> FreshEligibility:
    expected_calls = _expected_success_call_counts(report.contract)
    lifecycle_by_name = {value.name: value for value in report.lifecycle}
    reasons: list[str] = []
    if report.mode != FRESH_MODE or not report.full_execution:
        reasons.append("not_full_fresh_mode")
    if report.source_kind != "live_raw_collectors":
        reasons.append("not_live_raw_collectors")
    if not report.preflight.clean_pass:
        reasons.append("preflight_not_clean_pass")
    if tuple(lifecycle_by_name) != LIFECYCLE_SEQUENCE or not all(
        value.clean_pass for value in report.lifecycle
    ):
        reasons.append("lifecycle_not_exact_clean_pass")
    if not report.recovery.clean_pass:
        reasons.append("recovery_not_clean_pass")
    if not report.windows.passed or report.windows.sample_count != report.contract.sample_count:
        reasons.append("windows_raw_clock_not_exact_pass")
    if not report.wsl.passed or report.wsl.sample_count != report.contract.sample_count:
        reasons.append("wsl_raw_clock_not_exact_pass")
    if not report.schedule.passed:
        reasons.append("monotonic_schedule_not_pass")
    if dict(report.call_counts) != expected_calls:
        reasons.append("call_counts_not_exact")
    if set(report.runtime_invariants) != set(report.contract.required_invariants) or not all(
        report.runtime_invariants.get(name) is True for name in report.contract.required_invariants
    ):
        reasons.append("runtime_invariants_not_all_true")
    if report.runtime_invariants.get("residual_pid_zero") is not True or report.residual_pids:
        reasons.append("residual_process_not_zero")
    if report.errors:
        reasons.append("execution_errors_present")
    if report.manual_intervention_required:
        reasons.append("manual_intervention_required")
    eligible = not reasons
    return FreshEligibility(
        eligible=eligible,
        decision="phase_b2_pass" if eligible else "zero_credit_failure",
        reasons=tuple(reasons),
    )


def _report_success_eligible(report: FreshPhaseB2Report) -> bool:
    return evaluate_fresh_eligibility(report).eligible


def run_fresh(
    *,
    preflight: Callable[[FreshContext], StepResult | Mapping[str, Any] | bool],
    lifecycle_callbacks: Mapping[
        str, Callable[[FreshContext], StepResult | Mapping[str, Any] | bool]
    ],
    windows_sampler: Callable[[SampleRequest], Mapping[str, Any]],
    wsl_sampler: Callable[[SampleRequest], Mapping[str, Any]],
    recovery: Callable[[FreshContext], StepResult | Mapping[str, Any] | bool],
    invariant_probe: Callable[[FreshContext], Mapping[str, bool]],
    contract: FreshContract | None = None,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    sleep: Callable[[float], None] = time.sleep,
    utc_clock: Callable[[], str] = _utc_now,
    run_uuid_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
) -> FreshExecution:
    """Run one full fresh attempt with injected lifecycle and raw clock samplers.

    This function never invokes Docker, WSL, or PowerShell itself. In particular,
    ``wsl_shutdown`` is not a callback and remains fixed at zero. The caller must
    persist the returned execution with :func:`write_fresh_evidence`.
    """

    frozen_contract = (contract or FreshContract()).validate()
    if set(lifecycle_callbacks) != set(LIFECYCLE_SEQUENCE):
        raise FreshContractError(
            "lifecycle_callbacks_must_be_exactly_compose_stop_desktop_stop_"
            "desktop_start_compose_start"
        )
    if "wsl_shutdown" in lifecycle_callbacks:
        raise FreshContractError("wsl_shutdown_callback_forbidden")

    run_uuid = _validated_run_uuid(run_uuid_factory())
    origin_ns = _strict_int(monotonic_ns(), "schedule_origin_monotonic_ns")
    started_at = utc_clock()
    context = FreshContext(
        run_uuid=run_uuid,
        contract=frozen_contract,
        started_at=started_at,
        schedule_origin_monotonic_ns=origin_ns,
    )
    counts = {name: 0 for name in _expected_success_call_counts(frozen_contract)}
    errors: list[str] = []
    residual_pids: set[int] = set()
    lifecycle: list[StepResult] = []
    windows_samples: list[Mapping[str, Any]] = []
    wsl_samples: list[Mapping[str, Any]] = []
    observations: list[Mapping[str, int]] = []
    manual = False

    counts["preflight"] += 1
    preflight_result = _invoke_step("preflight", preflight, context)
    residual_pids.update(preflight_result.residual_pids)
    manual = preflight_result.blocking
    if not preflight_result.clean_pass:
        errors.append(preflight_result.error or "preflight_failed")

    offline_ready = preflight_result.clean_pass
    if offline_ready:
        for name in LIFECYCLE_SEQUENCE[:2]:
            counts[name] += 1
            result = _invoke_step(name, lifecycle_callbacks[name], context)
            lifecycle.append(result)
            residual_pids.update(result.residual_pids)
            manual = manual or result.blocking
            if not result.clean_pass:
                errors.append(result.error or f"{name}_failed")
                offline_ready = False
                break

    collection_complete = False
    if offline_ready and not manual:
        counts["docker_off_probe"] += 1
        # Preflight and stop latency cannot consume any part of the frozen
        # 180-second offline sampling window.
        origin_ns = _strict_int(monotonic_ns(), "schedule_origin_monotonic_ns")
        context = FreshContext(
            run_uuid=run_uuid,
            contract=frozen_contract,
            started_at=started_at,
            schedule_origin_monotonic_ns=origin_ns,
        )
        try:
            for sequence in range(frozen_contract.sample_count):
                target = origin_ns + sequence * frozen_contract.cadence_ns
                observed = _sleep_until(
                    target,
                    monotonic_ns=monotonic_ns,
                    sleep=sleep,
                )
                observations.append(
                    {
                        "sequence": sequence,
                        "target_monotonic_ns": target,
                        "observed_monotonic_ns": observed,
                    }
                )
                windows_request = SampleRequest(
                    run_uuid=run_uuid,
                    domain=WINDOWS_DOMAIN,
                    sequence=sequence,
                    target_monotonic_ns=target,
                )
                counts["windows_sampler"] += 1
                windows_samples.append(
                    _normalize_sample(windows_sampler(windows_request), windows_request)
                )
                wsl_request = SampleRequest(
                    run_uuid=run_uuid,
                    domain=WSL_DOMAIN,
                    sequence=sequence,
                    target_monotonic_ns=target,
                )
                counts["wsl_sampler"] += 1
                wsl_samples.append(_normalize_sample(wsl_sampler(wsl_request), wsl_request))
            _sleep_until(
                origin_ns + frozen_contract.duration_ns,
                monotonic_ns=monotonic_ns,
                sleep=sleep,
            )
            collection_complete = True
        except Exception as exc:  # sampler/scheduler is an integration boundary
            errors.append(f"raw_collection_failed:{type(exc).__name__}:{exc}")
            manual = True

    observed_end_ns = _strict_int(monotonic_ns(), "observed_end_monotonic_ns")
    windows_report = _clock_report(WINDOWS_DOMAIN, windows_samples, frozen_contract)
    wsl_report = _clock_report(WSL_DOMAIN, wsl_samples, frozen_contract)
    schedule_report = _schedule_report(
        observations,
        origin_ns=origin_ns,
        observed_end_ns=observed_end_ns,
        contract=frozen_contract,
    )
    if collection_complete and not windows_report.passed:
        errors.append(windows_report.error or "windows_clock_failed")
    if collection_complete and not wsl_report.passed:
        errors.append(wsl_report.error or "wsl_clock_failed")
    if collection_complete and not schedule_report.passed:
        errors.append("monotonic_schedule_failed")

    restore_allowed = collection_complete and not manual
    if restore_allowed:
        for name in LIFECYCLE_SEQUENCE[2:]:
            counts[name] += 1
            result = _invoke_step(name, lifecycle_callbacks[name], context)
            lifecycle.append(result)
            residual_pids.update(result.residual_pids)
            manual = manual or result.blocking
            if not result.clean_pass:
                errors.append(result.error or f"{name}_failed")
                restore_allowed = False
                break

    if restore_allowed and not manual:
        counts["recovery"] += 1
        recovery_result = _invoke_step("recovery", recovery, context)
        residual_pids.update(recovery_result.residual_pids)
        manual = manual or recovery_result.blocking
        if not recovery_result.clean_pass:
            errors.append(recovery_result.error or "recovery_failed")
    else:
        recovery_result = _empty_step("recovery", "recovery_not_run")

    runtime_invariants: dict[str, bool] = {
        name: False for name in frozen_contract.required_invariants
    }
    if recovery_result.clean_pass and not manual:
        counts["invariant_probe"] += 1
        try:
            raw_invariants = invariant_probe(context)
            if not isinstance(raw_invariants, Mapping):
                raise FreshContractError("runtime_invariants_not_mapping")
            if set(raw_invariants) != set(frozen_contract.required_invariants):
                raise FreshContractError("runtime_invariant_names_not_exact")
            if any(type(value) is not bool for value in raw_invariants.values()):
                raise FreshContractError("runtime_invariant_values_not_boolean")
            runtime_invariants = dict(raw_invariants)
            failed = [name for name, value in runtime_invariants.items() if not value]
            if failed:
                errors.append(f"runtime_invariants_failed:{','.join(sorted(failed))}")
        except Exception as exc:
            errors.append(f"invariant_probe_failed:{type(exc).__name__}:{exc}")
            manual = True

    if residual_pids:
        runtime_invariants["residual_pid_zero"] = False
    lifecycle_by_name = {value.name: value for value in lifecycle}
    ordered_lifecycle = tuple(
        lifecycle_by_name.get(name, _empty_step(name, "lifecycle_not_run"))
        for name in LIFECYCLE_SEQUENCE
    )
    ended_at = utc_clock()
    provisional = FreshPhaseB2Report(
        schema="s8-v4-x1-phase-b2-r5-fresh-report/v1",
        mode=FRESH_MODE,
        full_execution=True,
        source_kind="live_raw_collectors",
        run_uuid=run_uuid,
        started_at=started_at,
        ended_at=ended_at,
        contract=frozen_contract,
        preflight=preflight_result,
        lifecycle=ordered_lifecycle,
        recovery=recovery_result,
        windows=windows_report,
        wsl=wsl_report,
        schedule=schedule_report,
        call_counts=dict(counts),
        runtime_invariants=runtime_invariants,
        residual_pids=tuple(sorted(residual_pids)),
        errors=tuple(errors),
        manual_intervention_required=manual,
        success_eligible=False,
        decision="zero_credit_failure",
    )
    eligible = _report_success_eligible(provisional)
    report = FreshPhaseB2Report(
        **{
            **provisional.__dict__,
            "success_eligible": eligible,
            "decision": "phase_b2_pass" if eligible else "zero_credit_failure",
        }
    )
    return FreshExecution(
        report=report,
        windows_samples=tuple(dict(value) for value in windows_samples),
        wsl_samples=tuple(dict(value) for value in wsl_samples),
        schedule_observations=tuple(dict(value) for value in observations),
    )


def validate_fresh_execution(execution: FreshExecution) -> None:
    if type(execution) is not FreshExecution:
        raise FreshEvidenceValidationError("fresh_execution_type_required")
    report = execution.report
    if type(report) is not FreshPhaseB2Report:
        raise FreshEvidenceValidationError("fresh_report_type_required")
    report.contract.validate()
    _validated_run_uuid(report.run_uuid)
    for domain, samples in (
        (WINDOWS_DOMAIN, execution.windows_samples),
        (WSL_DOMAIN, execution.wsl_samples),
    ):
        for sequence, sample in enumerate(samples):
            if sample.get("run_uuid") != report.run_uuid:
                raise FreshEvidenceValidationError(f"raw_run_uuid_mismatch:{domain}:{sequence}")
            if sample.get("scheduled_monotonic_ns") != (
                report.schedule.origin_monotonic_ns + sequence * report.contract.cadence_ns
            ):
                raise FreshEvidenceValidationError(f"raw_schedule_mismatch:{domain}:{sequence}")
        recomputed = _clock_report(domain, samples, report.contract)
        recorded = report.windows if domain == WINDOWS_DOMAIN else report.wsl
        if recomputed.to_dict() != recorded.to_dict():
            raise FreshEvidenceValidationError(f"raw_analysis_mismatch:{domain}")
    recomputed_schedule = _schedule_report(
        execution.schedule_observations,
        origin_ns=report.schedule.origin_monotonic_ns,
        observed_end_ns=report.schedule.observed_end_monotonic_ns,
        contract=report.contract,
    )
    if recomputed_schedule.to_dict() != report.schedule.to_dict():
        raise FreshEvidenceValidationError("schedule_analysis_mismatch")
    recomputed_eligibility = _report_success_eligible(report)
    if report.success_eligible != recomputed_eligibility:
        raise FreshEvidenceValidationError("success_eligibility_mismatch")
    expected_decision = "phase_b2_pass" if recomputed_eligibility else "zero_credit_failure"
    if report.decision != expected_decision:
        raise FreshEvidenceValidationError("decision_mismatch")


def _inventory_entry(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def write_fresh_evidence(
    output_directory: Path,
    execution: FreshExecution,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one append-only fresh attempt and create a marker only on true success."""

    validate_fresh_execution(execution)
    output_directory = Path(output_directory)
    _new_directory(output_directory)
    windows_path = output_directory / "windows-raw-samples.jsonl"
    wsl_path = output_directory / "wsl-raw-samples.jsonl"
    schedule_path = output_directory / "monotonic-schedule.jsonl"
    report_path = output_directory / "fresh-report.json"
    _create_new(windows_path, _jsonl_bytes(execution.windows_samples))
    _create_new(wsl_path, _jsonl_bytes(execution.wsl_samples))
    _create_new(schedule_path, _jsonl_bytes(execution.schedule_observations))
    _create_new(report_path, _canonical_json_bytes(execution.report.to_dict()))
    base_files = [
        _inventory_entry(windows_path),
        _inventory_entry(wsl_path),
        _inventory_entry(schedule_path),
        _inventory_entry(report_path),
    ]

    if execution.success_eligible:
        index = {
            "schema": "s8-v4-x1-phase-b2-r5-private-evidence-index/v1",
            "created_at": _utc_now(),
            "run_uuid": execution.report.run_uuid,
            "acceptance_credit": True,
            "is_success_index": True,
            "all_invariants_passed": True,
            "metadata": dict(metadata or {}),
            "files": base_files,
        }
        index_path = output_directory / "private-evidence-index.json"
        _create_new(index_path, _canonical_json_bytes(index))
        marker = {
            "schema": "s8-v4-x1-phase-b2-r5-completion-marker/v1",
            "created_at": _utc_now(),
            "run_uuid": execution.report.run_uuid,
            "phase_b2_pass": True,
            "all_invariants_passed": True,
            "private_evidence_index_sha256": _sha256_file(index_path),
        }
        marker_path = output_directory / "completion-marker.json"
        _create_new(marker_path, _canonical_json_bytes(marker))
        return {
            "decision": "phase_b2_pass",
            "directory": str(output_directory),
            "private_index": str(index_path),
            "private_index_sha256": _sha256_file(index_path),
            "completion_marker": str(marker_path),
            "completion_marker_sha256": _sha256_file(marker_path),
        }

    seal = {
        "schema": "s8-v4-x1-phase-b2-r5-failure-seal/v1",
        "sealed_at": _utc_now(),
        "run_uuid": execution.report.run_uuid,
        "failure_only": True,
        "acceptance_credit": False,
        "is_success_index": False,
        "success_marker_created": False,
        "report_sha256": _sha256_file(report_path),
        "metadata": dict(metadata or {}),
    }
    seal_path = output_directory / "failure-seal.json"
    _create_new(seal_path, _canonical_json_bytes(seal))
    failure_files = [*base_files, _inventory_entry(seal_path)]
    index = {
        "schema": "s8-v4-x1-phase-b2-r5-failure-evidence-index/v1",
        "created_at": _utc_now(),
        "run_uuid": execution.report.run_uuid,
        "failure_only": True,
        "acceptance_credit": False,
        "is_success_index": False,
        "success_marker_created": False,
        "files": failure_files,
    }
    index_path = output_directory / "failure-evidence-index.json"
    _create_new(index_path, _canonical_json_bytes(index))
    return {
        "decision": "zero_credit_failure",
        "directory": str(output_directory),
        "failure_seal": str(seal_path),
        "failure_seal_sha256": _sha256_file(seal_path),
        "failure_index": str(index_path),
        "failure_index_sha256": _sha256_file(index_path),
    }
