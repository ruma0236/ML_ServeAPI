"""Fail-closed contracts and append-only evidence for Phase B2 r7.

R7 is a restore-only reconciliation harness.  It has no fresh Phase B2 API and
performs no Docker, Kubernetes, WSL, or process-control action.  This module
validates immutable work-order inputs and writes one create-exclusive evidence
set after a runner has supplied a report.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from evm.scale_validation.phase_b2_r7_process import (
    PROCESS_CONTAINMENT_CONTRACT,
    R7ProcessContractError,
    validate_process_containment_contract,
)


SCHEMA_VERSION = "evm.s8_v4.x1_phase_b2_r7_restore_work_order.v1"
WORK_ORDER_ID = "s8-v4-x1-phase-b2-r7-restore-only-validation"
LAUNCHER_EVIDENCE_SCHEMA = "s8-v4-x1-phase-b2-r7-launcher-evidence/v1"
PRE_R7_REVISION = "167cb0176cb76b67085e218e89030a832f0f8ff2"
PRESERVED_UNTRACKED_COUNT = 4_244
UNTRACKED_PATH_SET_ENCODING = "ordinal-sorted UTF-8 paths, each NUL-terminated"

RUNTIME_COMPONENTS = (
    "builder",
    "core",
    "process",
    "runner",
    "validator",
    "docker_compose",
)
PARENT_CHECKPOINT_ROLES = (
    "r5_failure_seal",
    "r5_failure_index",
    "r6_compose_rca",
    "r6_failure_seal_amendment",
    "r6_final_index",
    "post_manual_on_readback",
    "post_manual_on_index",
)
PARENT_CHECKPOINT_KINDS = {role: role for role in PARENT_CHECKPOINT_ROLES}

RESTORE_LIFECYCLE_COUNTS = {
    "docker_off_probe": 0,
    "compose_stop": 0,
    "desktop_stop": 0,
    "wsl_shutdown": 0,
    "desktop_start": 0,
    "compose_start": 0,
}
RESTORE_COLLECTOR_COUNTS = {"windows_fresh_collector": 0, "wsl_fresh_collector": 0}
LAUNCHER_COUNTS = {"outer": 1, "bridge": 1, "runner": 1, "automatic_retry": 0}
DOWNSTREAM_COUNTS = {
    "full_stack_3180": 0,
    "q0": 0,
    "calibration_54": 0,
    "matrix_78": 0,
    "integrated_v4": 0,
    "etw": 0,
}

LONG_LIVED_SERVICES = (
    "airflow-postgres",
    "airflow-scheduler",
    "airflow-webserver",
    "api",
    "control-panel",
    "control-plane-postgres",
    "grafana",
    "minio",
    "mlflow",
    "otel-collector",
    "postgres",
    "prometheus",
    "task-queue-worker",
)
ONE_SHOT_SERVICES = ("airflow-init", "minio-create-buckets")
PROMETHEUS_JOBS = (
    "evm-api",
    "evm-b0-production",
    "evm-otel-collector",
    "evm-task-queue-worker",
    "prometheus",
)
DATABASE_INSTANCES = {
    "control_plane": {
        "container_name": "evm-control-plane-postgres",
        "user": "evm_control_plane",
        "database": "evm_control_plane",
    },
    "mlflow": {"container_name": "evm-postgres", "user": "mlflow", "database": "mlflow"},
    "airflow": {
        "container_name": "evm-airflow-postgres",
        "user": "airflow",
        "database": "airflow",
    },
}
MLFLOW_MIGRATION_HEAD = "0584bdc529eb"
AIRFLOW_MIGRATION_HEAD = "5f2621c13b39"

JOB_SCOPE_CONTRACT = {
    "canonical_active_jobs": {
        "sources": ["kubernetes_job_status_active", "manifest_active_job_file_markers"],
        "required_count": 0,
    },
    "historical_observations": {
        "sources": [
            "control_plane_task_entity_statuses",
            "mlflow_running_rows",
            "kubernetes_terminal_failed_objects",
        ],
        "separate_from_canonical_active_jobs": True,
        "unknown_or_unproven_blocks_restore": True,
        "deletion_required": False,
    },
}
HISTORICAL_CLASSIFICATION_SOURCES = (
    "control_plane_task_entity_statuses",
    "mlflow_running_rows",
    "kubernetes_terminal_failed_objects",
)
HISTORICAL_QUERY_TEXTS = {
    "control_plane_task_entity_statuses": (
        "SELECT entity_id,state,"
        "to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'),"
        "to_char(updated_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') "
        "FROM evm_control_plane.entities WHERE entity_kind='task_assignment' "
        "AND state IN ('queued','pending_confirmation','running') "
        "ORDER BY entity_id;"
    ),
    "mlflow_running_rows": (
        "SELECT run_uuid,status,lifecycle_stage,COALESCE(start_time::text,''),"
        "COALESCE(end_time::text,'') FROM runs WHERE status='RUNNING' ORDER BY run_uuid;"
    ),
    "kubernetes_terminal_failed_objects": (
        "kubectl get pods -A --field-selector=status.phase=Failed -o json"
    ),
}
HISTORICAL_QUERY_SHA256 = {
    source: hashlib.sha256(query.encode("utf-8")).hexdigest()
    for source, query in HISTORICAL_QUERY_TEXTS.items()
}
HISTORICAL_DECISION_AUTHORITY = "phase-b2-r7-independent-review"

FULL_SHA1 = re.compile(r"^[0-9a-f]{40}$")
FULL_SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA256_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
MIGRATION_VERSION = re.compile(r"^[0-9]{3}_[a-z0-9_]+$")


class PhaseB2R7Error(RuntimeError):
    """Base exception for an r7 fail-closed decision."""


class R7ContractError(PhaseB2R7Error):
    """Raised when executable or observed state differs from the r7 contract."""


class R7EvidenceExistsError(PhaseB2R7Error):
    """Raised when create-exclusive evidence would overwrite a path."""


class R7SuccessInvariantError(PhaseB2R7Error):
    """Raised when restore-only evidence is requested before all gates pass."""


@dataclass(frozen=True)
class TimeoutContract:
    """Exact nested-command, wrapper, restore, residual, and drain budgets."""

    kubectl_timeout_seconds: float = 8.0
    wrapper_timeout_seconds: float = 15.0
    restore_deadline_seconds: float = 600.0
    residual_repoll_seconds: float = 120.0
    stream_drain_seconds: float = 5.0

    FIELD_NAMES = (
        "kubectl_timeout_seconds",
        "wrapper_timeout_seconds",
        "restore_deadline_seconds",
        "residual_repoll_seconds",
        "stream_drain_seconds",
    )

    def validate(self) -> "TimeoutContract":
        for name in self.FIELD_NAMES:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise R7ContractError(f"{name}_numeric_required")
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise R7ContractError(f"{name}_finite_positive_required")
        if not (
            self.kubectl_timeout_seconds
            < self.wrapper_timeout_seconds
            < self.restore_deadline_seconds
        ):
            raise R7ContractError("timeout_order_requires_kubectl_lt_wrapper_lt_restore_deadline")
        if self.residual_repoll_seconds != 120:
            raise R7ContractError("residual_repoll_must_equal_120_seconds")
        if self.stream_drain_seconds >= self.wrapper_timeout_seconds:
            raise R7ContractError("stream_drain_must_be_less_than_wrapper")
        return self

    def to_dict(self) -> dict[str, float]:
        self.validate()
        return {name: float(getattr(self, name)) for name in self.FIELD_NAMES}

    @classmethod
    def from_mapping(cls, value: Any) -> "TimeoutContract":
        source = _mapping(value, "timeout_contract")
        if set(source) != set(cls.FIELD_NAMES):
            raise R7ContractError("timeout_contract_fields_mismatch")
        if any(isinstance(source[name], bool) for name in cls.FIELD_NAMES):
            raise R7ContractError("timeout_contract_boolean_forbidden")
        try:
            return cls(**{name: float(source[name]) for name in cls.FIELD_NAMES}).validate()
        except (TypeError, ValueError) as exc:
            raise R7ContractError("timeout_contract_numeric_required") from exc


@dataclass
class RestoreDeadline:
    total_seconds: float
    clock: Callable[[], float] = time.monotonic
    started_monotonic: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.total_seconds)) or float(self.total_seconds) <= 0:
            raise R7ContractError("restore_deadline_finite_positive_required")
        if self.started_monotonic is None:
            self.started_monotonic = float(self.clock())

    @property
    def remaining_seconds(self) -> float:
        assert self.started_monotonic is not None
        return max(
            0.0,
            self.started_monotonic + float(self.total_seconds) - float(self.clock()),
        )

    def can_launch(self, required_seconds: float) -> bool:
        if not math.isfinite(float(required_seconds)) or float(required_seconds) <= 0:
            raise R7ContractError("probe_required_seconds_finite_positive_required")
        return self.remaining_seconds >= float(required_seconds)

    def assert_can_launch(self, required_seconds: float) -> None:
        if not self.can_launch(required_seconds):
            raise R7ContractError(
                "restore_budget_prevents_new_probe:"
                f"remaining={self.remaining_seconds:.6f}:required={float(required_seconds):.6f}"
            )


@dataclass(frozen=True)
class RestoreCheckpoint:
    source: str
    historical_call_counts: Mapping[str, int]
    previous_attempt_failed: bool = True

    def permits(self, operation: str) -> bool:
        blocked = {
            *RESTORE_LIFECYCLE_COUNTS,
            *RESTORE_COLLECTOR_COUNTS,
            *DOWNSTREAM_COUNTS,
        }
        return operation not in blocked

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "historical_call_counts": dict(self.historical_call_counts),
            "previous_attempt_failed": self.previous_attempt_failed,
            "restore_only_blocked_calls": list(RESTORE_LIFECYCLE_COUNTS),
        }


class RestoreStage(str, Enum):
    DOCKER_ENGINE = "docker_engine"
    COMPOSE = "compose"
    KUBERNETES_API = "kubernetes_api"
    NODE_DEVICE_PLUGIN_GPU = "node_device_plugin_gpu"
    B0_IDENTITY_CUDA = "b0_exact_identity_actual_cuda"
    PROMETHEUS = "prometheus"
    API_RELEASE_IDENTITY = "api_release_identity"
    QUEUE_JOBS_LEASE_RESIDUE = "queue_jobs_lease_residue"


RESTORE_STAGE_ORDER = tuple(RestoreStage)
R7_REQUIRED_INVARIANTS = (
    "docker_engine",
    "compose_healthy",
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
    "active_jobs_zero",
    "active_claims_zero",
    "gpu_lease_zero",
    "x1_residue_zero",
    "compose_exact_13_running",
    "compose_healthchecks_healthy",
    "compose_container_identity_stable",
    "compose_restart_delta_zero",
    "compose_stability_duration_met",
    "compose_one_shots_classified",
    "postgres_3_of_3_connected",
    "postgres_3_of_3_not_in_recovery",
    "control_plane_migrations_exact",
    "mlflow_migration_head_exact",
    "airflow_migration_head_exact",
    "api_container_image_exact",
    "worker_container_image_exact",
    "api_image_revision_exact",
    "api_image_attestation_exact",
    "canonical_active_scope_exact",
    "historical_control_plane_tasks_classified",
    "historical_mlflow_running_classified",
    "historical_failed_pods_classified",
    "windows_global_residual_zero",
    "wsl_global_residual_zero",
)


@dataclass(frozen=True)
class ProbeResult:
    passed: bool
    retryable: bool = False
    last_error: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    residual_pids: tuple[int, ...] = ()
    manual_intervention_required: bool = False
    invariants: Mapping[str, bool] = field(default_factory=dict)

    @classmethod
    def normalize(cls, raw: Any) -> "ProbeResult":
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, bool):
            return cls(passed=raw, last_error=None if raw else "probe_false")
        if not isinstance(raw, Mapping):
            raise TypeError(f"probe_result_mapping_required:{type(raw).__name__}")
        invariants_raw = raw.get("invariants", {})
        if not isinstance(invariants_raw, Mapping):
            raise TypeError("probe_invariants_mapping_required")
        invariants = {str(name): value is True for name, value in invariants_raw.items()}
        passed = raw.get("passed", raw.get("ok", False)) is True and all(invariants.values())
        error = raw.get("last_error", raw.get("error"))
        return cls(
            passed=passed,
            # Preserve the observation for evidence, but the r7 harness never
            # interprets it as authority to launch a second probe.
            retryable=raw.get("retryable") is True,
            last_error=None if error is None else str(error),
            details={
                str(key): value
                for key, value in raw.items()
                if key
                not in {
                    "passed",
                    "ok",
                    "retryable",
                    "last_error",
                    "error",
                    "residual_pids",
                    "manual_intervention_required",
                    "invariants",
                }
            },
            residual_pids=tuple(sorted({int(pid) for pid in raw.get("residual_pids", ())})),
            manual_intervention_required=raw.get("manual_intervention_required") is True,
            invariants=invariants,
        )


@dataclass(frozen=True)
class RestoreReport:
    mode: str
    started_at: str
    ended_at: str
    duration_seconds: float
    expected_revision: str | None
    passed: bool
    manual_intervention_required: bool
    deadline_exceeded: bool
    last_error: str | None
    stages: list[Any]
    call_counts: Mapping[str, int]
    residual_pids: tuple[int, ...]
    checkpoint: Mapping[str, Any]
    success_invariants: Mapping[str, bool]
    required_invariants: tuple[str, ...] = ()
    decision: str | None = None

    def to_dict(self) -> dict[str, Any]:
        stage_values = [
            stage.to_dict() if hasattr(stage, "to_dict") else dict(stage) for stage in self.stages
        ]
        return {
            "schema": "s8-v4-x1-phase-b2-r7-restore-report/v1",
            "mode": self.mode,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "expected_revision": self.expected_revision,
            "passed": self.passed,
            "overall_pass": self.passed,
            "manual_intervention_required": self.manual_intervention_required,
            "deadline_exceeded": self.deadline_exceeded,
            "last_error": self.last_error,
            "stages": stage_values,
            "call_counts": dict(self.call_counts),
            "residual_pids": list(self.residual_pids),
            "checkpoint": dict(self.checkpoint),
            "success_invariants": dict(self.success_invariants),
            "required_invariants": list(self.required_invariants),
            "decision": self.decision,
        }


class ReconcileRestoreHarness:
    """Single-attempt, read-only r7 restore reconciliation state machine."""

    def __init__(
        self,
        *,
        contract: TimeoutContract | None = None,
        probes: Mapping[str | RestoreStage, Callable[[RestoreDeadline], Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], str] | None = None,
        expected_revision: str | None = None,
        required_invariants: Sequence[str] | None = None,
        max_probe_attempts: int = 1,
    ) -> None:
        self.contract = (contract or TimeoutContract()).validate()
        self.probes = {
            key.value if isinstance(key, RestoreStage) else str(key): probe
            for key, probe in (probes or {}).items()
        }
        self.clock = clock
        self.utc_clock = utc_clock or utc_now
        self.expected_revision = expected_revision
        normalized_invariants = (
            R7_REQUIRED_INVARIANTS
            if required_invariants is None
            else tuple(str(item) for item in required_invariants)
        )
        if normalized_invariants != R7_REQUIRED_INVARIANTS:
            raise R7ContractError("r7_required_invariant_set_mismatch")
        self.required_invariants = R7_REQUIRED_INVARIANTS
        if max_probe_attempts != 1 or isinstance(max_probe_attempts, bool):
            raise R7ContractError("r7_probe_max_attempts_must_equal_1")
        self.max_probe_attempts = 1

    def run_restore_only(self, checkpoint: RestoreCheckpoint) -> RestoreReport:
        if not isinstance(checkpoint, RestoreCheckpoint):
            raise TypeError("restore_checkpoint_required")
        started_at = self.utc_clock()
        started = float(self.clock())
        deadline = RestoreDeadline(
            self.contract.restore_deadline_seconds,
            clock=self.clock,
            started_monotonic=started,
        )
        stages: list[dict[str, Any]] = []
        invariants = {name: False for name in self.required_invariants}
        invariants.update({stage.value: False for stage in RESTORE_STAGE_ORDER})
        residual_pids: set[int] = set()
        last_error: str | None = None
        unsafe_latch = False

        for stage in RESTORE_STAGE_ORDER:
            stage_started_at = self.utc_clock()
            stage_started = float(self.clock())
            probe = self.probes.get(stage.value)
            attempts = 0
            if probe is None:
                result = ProbeResult(passed=False, last_error=f"probe_missing:{stage.value}")
            elif deadline.remaining_seconds <= 0:
                result = ProbeResult(
                    passed=False,
                    last_error=f"restore_deadline_exhausted_before_probe:{stage.value}",
                    manual_intervention_required=True,
                )
            else:
                try:
                    attempts = 1
                    result = ProbeResult.normalize(probe(deadline))
                except Exception as exc:
                    result = ProbeResult(
                        passed=False,
                        last_error=f"probe_exception:{stage.value}:{type(exc).__name__}:{exc}",
                        manual_intervention_required=True,
                    )
            stage_ended = float(self.clock())
            invariants[stage.value] = result.passed
            invariants.update(result.invariants)
            residual_pids.update(result.residual_pids)
            if not result.passed and last_error is None:
                last_error = result.last_error or f"restore_stage_failed:{stage.value}"
            stages.append(
                {
                    "stage": stage.value,
                    "started_at": stage_started_at,
                    "ended_at": self.utc_clock(),
                    "duration_seconds": max(0.0, stage_ended - stage_started),
                    "attempts": attempts,
                    "max_attempts": 1,
                    "passed": result.passed,
                    "retryable_ignored": result.retryable,
                    "last_error": result.last_error,
                    "manual_intervention_required": result.manual_intervention_required,
                    "residual_pids": list(result.residual_pids),
                    "invariants": dict(result.invariants),
                    "details": dict(result.details),
                    "deadline_remaining_seconds": deadline.remaining_seconds,
                }
            )
            unsafe_latch = bool(
                result.manual_intervention_required
                or result.residual_pids
                or deadline.remaining_seconds <= 0
            )
            if unsafe_latch:
                break

        required_ok = all(invariants.get(name) is True for name in self.required_invariants)
        all_stages = len(stages) == len(RESTORE_STAGE_ORDER) and all(
            bool(stage["passed"]) for stage in stages
        )
        passed = bool(
            all_stages
            and required_ok
            and not unsafe_latch
            and not residual_pids
            and last_error is None
        )
        if not passed and last_error is None:
            last_error = "restore_invariants_incomplete"
        ended = float(self.clock())
        return RestoreReport(
            mode="restore-only",
            started_at=started_at,
            ended_at=self.utc_clock(),
            duration_seconds=max(0.0, ended - started),
            expected_revision=self.expected_revision,
            passed=passed,
            manual_intervention_required=not passed,
            deadline_exceeded=deadline.remaining_seconds <= 0,
            last_error=last_error,
            stages=stages,
            call_counts=dict(RESTORE_LIFECYCLE_COUNTS),
            residual_pids=tuple(sorted(residual_pids)),
            checkpoint=checkpoint.to_dict(),
            success_invariants=invariants,
            required_invariants=self.required_invariants,
            decision="restore_only_pass" if passed else "manual_intervention_required",
        )


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_snapshot(path: Path) -> tuple[Any, str]:
    """Parse and hash one immutable byte snapshot from ``path``.

    Reading and hashing through separate opens would permit a path replacement
    between the SHA check and JSON parsing.  Callers bind both decisions to the
    same bytes instead.
    """

    raw = Path(path).read_bytes()
    measured_sha256 = hashlib.sha256(raw).hexdigest()
    return json.loads(raw.decode("utf-8-sig")), measured_sha256


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise R7ContractError(f"{label}_mapping_required")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise R7ContractError(f"{label}_sequence_required")
    return value


def _nonempty(value: Any, label: str) -> str:
    normalized = str(value)
    if not normalized.strip():
        raise R7ContractError(f"{label}_nonempty_required")
    return normalized


def _full_sha1(value: Any, label: str) -> str:
    normalized = str(value).lower()
    if FULL_SHA1.fullmatch(normalized) is None:
        raise R7ContractError(f"{label}_full_sha1_required")
    return normalized


def _full_sha256(value: Any, label: str) -> str:
    normalized = str(value).lower()
    if FULL_SHA256.fullmatch(normalized) is None:
        raise R7ContractError(f"{label}_full_sha256_required")
    return normalized


def _sha256_id(value: Any, label: str) -> str:
    normalized = str(value).lower()
    if SHA256_ID.fullmatch(normalized) is None:
        raise R7ContractError(f"{label}_sha256_id_required")
    return normalized


def _uuid(value: Any, label: str) -> str:
    normalized = str(value).lower()
    try:
        parsed = uuid.UUID(normalized)
    except (ValueError, AttributeError) as exc:
        raise R7ContractError(f"{label}_uuid_required") from exc
    if str(parsed) != normalized:
        raise R7ContractError(f"{label}_canonical_uuid_required")
    return normalized


def _exact_counts(value: Any, expected: Mapping[str, int], label: str) -> dict[str, int]:
    source = _mapping(value, label)
    try:
        actual = {str(key): int(raw) for key, raw in source.items()}
    except (TypeError, ValueError) as exc:
        raise R7ContractError(f"{label}_integer_counts_required") from exc
    if any(isinstance(raw, bool) for raw in source.values()) or actual != dict(expected):
        raise R7ContractError(f"{label}_exact_counts_required:{actual}")
    return actual


def _resolved_outside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return True
    return False


def git_head_blob_oid(repository_root: Path, path: Path) -> str:
    root_result = subprocess.run(
        ["git", "-C", str(Path(repository_root).resolve()), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if root_result.returncode != 0:
        raise R7ContractError("runtime_git_toplevel_read_failed")
    git_root = Path(root_result.stdout.strip()).resolve()
    try:
        relative = Path(path).resolve().relative_to(git_root).as_posix()
    except ValueError as exc:
        raise R7ContractError("runtime_path_outside_git_repository") from exc
    result = subprocess.run(
        ["git", "-C", str(git_root), "rev-parse", f"HEAD:{relative}"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    value = result.stdout.strip().lower()
    if result.returncode != 0 or FULL_SHA1.fullmatch(value) is None:
        raise R7ContractError(f"runtime_git_blob_read_failed:{relative}")
    return value


@dataclass(frozen=True)
class LifecycleTimeoutContract:
    compose_internal_seconds: float = 120.0
    compose_wrapper_seconds: float = 150.0
    desktop_internal_seconds: float = 300.0
    desktop_wrapper_seconds: float = 330.0
    sampler_internal_seconds: float = 180.0
    sampler_wrapper_seconds: float = 210.0
    attempt_deadline_seconds: float = 1200.0

    FIELD_NAMES = (
        "compose_internal_seconds",
        "compose_wrapper_seconds",
        "desktop_internal_seconds",
        "desktop_wrapper_seconds",
        "sampler_internal_seconds",
        "sampler_wrapper_seconds",
        "attempt_deadline_seconds",
    )

    def validate(self) -> "LifecycleTimeoutContract":
        for name in self.FIELD_NAMES:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise R7ContractError(f"{name}_numeric_required")
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise R7ContractError(f"{name}_finite_positive_required")
        if not self.compose_internal_seconds < self.compose_wrapper_seconds:
            raise R7ContractError("compose_internal_must_be_less_than_wrapper")
        if not self.desktop_internal_seconds < self.desktop_wrapper_seconds:
            raise R7ContractError("desktop_internal_must_be_less_than_wrapper")
        if not self.sampler_internal_seconds < self.sampler_wrapper_seconds:
            raise R7ContractError("sampler_internal_must_be_less_than_wrapper")
        if (
            max(
                self.compose_wrapper_seconds,
                self.desktop_wrapper_seconds,
                self.sampler_wrapper_seconds,
            )
            >= self.attempt_deadline_seconds
        ):
            raise R7ContractError("lifecycle_wrapper_must_be_less_than_attempt_deadline")
        return self

    def to_dict(self) -> dict[str, float]:
        self.validate()
        return {name: float(getattr(self, name)) for name in self.FIELD_NAMES}

    @classmethod
    def from_mapping(cls, value: Any) -> "LifecycleTimeoutContract":
        source = _mapping(value, "lifecycle_timeout_contract")
        if set(source) != set(cls.FIELD_NAMES):
            raise R7ContractError("lifecycle_timeout_contract_fields_mismatch")
        if any(isinstance(source[name], bool) for name in cls.FIELD_NAMES):
            raise R7ContractError("lifecycle_timeout_contract_boolean_forbidden")
        try:
            return cls(**{name: float(source[name]) for name in cls.FIELD_NAMES}).validate()
        except (TypeError, ValueError) as exc:
            raise R7ContractError("lifecycle_timeout_contract_numeric_required") from exc


def validate_runtime_pins(
    manifest: Mapping[str, Any], repository_root: Path
) -> dict[str, dict[str, Any]]:
    runtime = _mapping(manifest.get("runtime"), "runtime")
    if set(runtime) != set(RUNTIME_COMPONENTS):
        raise R7ContractError("runtime_component_role_set_mismatch")
    root = Path(repository_root).resolve()
    measured: dict[str, dict[str, Any]] = {}
    paths: set[Path] = set()
    for name in RUNTIME_COMPONENTS:
        component = _mapping(runtime[name], f"runtime_{name}")
        if set(component) != {"path", "sha256", "blob_oid", "bytes"}:
            raise R7ContractError(f"runtime_{name}_fields_mismatch")
        path = Path(_nonempty(component["path"], f"runtime_{name}_path")).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise R7ContractError(f"runtime_{name}_path_outside_repository") from exc
        if path in paths:
            raise R7ContractError("runtime_component_paths_must_be_distinct")
        paths.add(path)
        if not path.is_file():
            raise R7ContractError(f"runtime_{name}_file_missing:{path}")
        expected_sha = _full_sha256(component["sha256"], f"runtime_{name}")
        expected_blob = _full_sha1(component["blob_oid"], f"runtime_{name}_blob")
        if (
            isinstance(component["bytes"], bool)
            or not isinstance(component["bytes"], int)
            or component["bytes"] < 1
        ):
            raise R7ContractError(f"runtime_{name}_positive_bytes_required")
        actual_sha = sha256_file(path)
        actual_blob = git_head_blob_oid(root, path)
        actual_bytes = path.stat().st_size
        if actual_sha != expected_sha:
            raise R7ContractError(f"runtime_{name}_sha256_mismatch")
        if actual_blob != expected_blob:
            raise R7ContractError(f"runtime_{name}_blob_oid_mismatch")
        if actual_bytes != component["bytes"]:
            raise R7ContractError(f"runtime_{name}_bytes_mismatch")
        measured[name] = {
            "path": str(path),
            "sha256": actual_sha,
            "blob_oid": actual_blob,
            "bytes": actual_bytes,
        }
    return measured


def _validate_parent_entries(
    value: Any,
    *,
    bundle_directory: Path | None = None,
    output_directory: Path | None = None,
) -> dict[str, dict[str, Any]]:
    entries = _sequence(value, "parent_checkpoints")
    if len(entries) != len(PARENT_CHECKPOINT_ROLES):
        raise R7ContractError("parent_checkpoint_count_mismatch")
    normalized: dict[str, dict[str, Any]] = {}
    paths: set[Path] = set()
    for raw in entries:
        entry = _mapping(raw, "parent_checkpoint")
        required = {"role", "kind", "path", "sha256", "immutable", "must_not_execute"}
        if set(entry) != required:
            raise R7ContractError("parent_checkpoint_fields_mismatch")
        role = str(entry["role"])
        if role not in PARENT_CHECKPOINT_KINDS or role in normalized:
            raise R7ContractError("parent_checkpoint_role_set_mismatch")
        if entry["kind"] != PARENT_CHECKPOINT_KINDS[role]:
            raise R7ContractError(f"parent_checkpoint_kind_mismatch:{role}")
        if entry["immutable"] is not True or entry["must_not_execute"] is not True:
            raise R7ContractError(f"parent_checkpoint_immutable_no_execute_required:{role}")
        path = Path(_nonempty(entry["path"], f"parent_checkpoint_{role}_path")).resolve()
        if path in paths:
            raise R7ContractError("parent_checkpoint_paths_must_be_distinct")
        paths.add(path)
        if bundle_directory is not None and not _resolved_outside(path, bundle_directory):
            raise R7ContractError(f"parent_checkpoint_inside_bundle:{role}")
        if output_directory is not None and not _resolved_outside(path, output_directory):
            raise R7ContractError(f"parent_checkpoint_inside_output:{role}")
        normalized[role] = {
            "role": role,
            "kind": str(entry["kind"]),
            "path": str(path),
            "sha256": _full_sha256(entry["sha256"], f"parent_checkpoint_{role}"),
            "immutable": True,
            "must_not_execute": True,
        }
    if set(normalized) != set(PARENT_CHECKPOINT_ROLES):
        raise R7ContractError("parent_checkpoint_role_set_mismatch")
    return normalized


def _validate_compose(value: Any) -> dict[str, Any]:
    compose = _mapping(value, "expected_state_compose")
    required = {
        "project_name",
        "config_path",
        "config_sha256",
        "long_lived_services",
        "one_shot_services",
        "service_pins",
        "stability",
    }
    if set(compose) != required:
        raise R7ContractError("expected_state_compose_fields_mismatch")
    _nonempty(compose["project_name"], "compose_project_name")
    _nonempty(compose["config_path"], "compose_config_path")
    _full_sha256(compose["config_sha256"], "compose_config")
    if tuple(_sequence(compose["long_lived_services"], "long_lived_services")) != (
        LONG_LIVED_SERVICES
    ):
        raise R7ContractError("compose_long_lived_services_mismatch")
    if tuple(_sequence(compose["one_shot_services"], "one_shot_services")) != ONE_SHOT_SERVICES:
        raise R7ContractError("compose_one_shot_services_mismatch")
    pins = _mapping(compose["service_pins"], "compose_service_pins")
    if set(pins) != set(LONG_LIVED_SERVICES):
        raise R7ContractError("compose_service_pin_role_set_mismatch")
    container_ids: set[str] = set()
    for service in LONG_LIVED_SERVICES:
        pin = _mapping(pins[service], f"compose_service_pin_{service}")
        if set(pin) != {"container_name", "container_id", "image_id", "healthcheck_expected"}:
            raise R7ContractError(f"compose_service_pin_fields_mismatch:{service}")
        _nonempty(pin["container_name"], f"compose_{service}_container_name")
        container_id = _full_sha256(pin["container_id"], f"compose_{service}_container_id")
        if container_id in container_ids:
            raise R7ContractError("compose_container_ids_must_be_distinct")
        container_ids.add(container_id)
        _sha256_id(pin["image_id"], f"compose_{service}_image_id")
        if not isinstance(pin["healthcheck_expected"], bool):
            raise R7ContractError(f"compose_{service}_healthcheck_boolean_required")
    stability = _mapping(compose["stability"], "compose_stability")
    expected_stability = {
        "duration_seconds": 300,
        "interval_seconds": 5,
        "samples": 61,
        "restart_delta": 0,
    }
    if dict(stability) != expected_stability:
        raise R7ContractError("compose_stability_contract_mismatch")
    return dict(compose)


def _validate_api(
    value: Any, revision: str, tree: str, compose: Mapping[str, Any]
) -> dict[str, Any]:
    api = _mapping(value, "expected_state_api")
    required = {
        "base_url",
        "api_container_name",
        "worker_container_name",
        "image_id",
        "source_revision",
        "source_tree",
        "image_attestation",
    }
    if set(api) != required:
        raise R7ContractError("expected_state_api_fields_mismatch")
    _nonempty(api["base_url"], "api_base_url")
    if api["api_container_name"] != "evm-api":
        raise R7ContractError("api_container_name_mismatch")
    if api["worker_container_name"] != "evm-task-queue-worker":
        raise R7ContractError("worker_container_name_mismatch")
    image_id = _sha256_id(api["image_id"], "api_image_id")
    if _full_sha1(api["source_revision"], "api_source_revision") != revision:
        raise R7ContractError("api_source_revision_mismatch")
    if _full_sha1(api["source_tree"], "api_source_tree") != tree:
        raise R7ContractError("api_source_tree_mismatch")
    attestation = _mapping(api["image_attestation"], "api_image_attestation")
    if set(attestation) != {"path", "sha256"}:
        raise R7ContractError("api_image_attestation_fields_mismatch")
    _nonempty(attestation["path"], "api_image_attestation_path")
    _full_sha256(attestation["sha256"], "api_image_attestation")
    service_pins = _mapping(compose["service_pins"], "compose_service_pins")
    for service in ("api", "task-queue-worker"):
        pin = _mapping(service_pins[service], f"compose_service_pin_{service}")
        if str(pin["image_id"]).lower() != image_id:
            raise R7ContractError(f"api_shared_image_id_mismatch:{service}")
    return dict(api)


def _validate_database(value: Any) -> dict[str, Any]:
    database = _mapping(value, "expected_state_database")
    required = {
        "instances",
        "control_plane_schema_versions",
        "mlflow_migration_head",
        "airflow_migration_head",
    }
    if set(database) != required:
        raise R7ContractError("expected_state_database_fields_mismatch")
    instances = _mapping(database["instances"], "database_instances")
    if {key: dict(_mapping(raw, f"database_instance_{key}")) for key, raw in instances.items()} != (
        DATABASE_INSTANCES
    ):
        raise R7ContractError("database_instance_contract_mismatch")
    versions = tuple(
        str(item)
        for item in _sequence(
            database["control_plane_schema_versions"], "control_plane_schema_versions"
        )
    )
    if not versions or len(set(versions)) != len(versions):
        raise R7ContractError("control_plane_schema_versions_nonempty_unique_required")
    if versions != tuple(sorted(versions)) or any(
        MIGRATION_VERSION.fullmatch(item) is None for item in versions
    ):
        raise R7ContractError("control_plane_schema_versions_canonical_order_required")
    if database["mlflow_migration_head"] != MLFLOW_MIGRATION_HEAD:
        raise R7ContractError("mlflow_migration_head_mismatch")
    if database["airflow_migration_head"] != AIRFLOW_MIGRATION_HEAD:
        raise R7ContractError("airflow_migration_head_mismatch")
    return dict(database)


def _validate_kubernetes(value: Any) -> dict[str, Any]:
    kubernetes = _mapping(value, "expected_state_kubernetes")
    if set(kubernetes) != {
        "allowed_historical_failed_pods",
        "health_confirmation_samples",
        "residual_selectors",
    }:
        raise R7ContractError("expected_state_kubernetes_fields_mismatch")
    if kubernetes["health_confirmation_samples"] != 2 or isinstance(
        kubernetes["health_confirmation_samples"], bool
    ):
        raise R7ContractError("kubernetes_health_confirmation_samples_must_equal_2")
    pods = _sequence(kubernetes["allowed_historical_failed_pods"], "historical_failed_pods")
    if len(pods) != 11:
        raise R7ContractError("historical_failed_pod_count_must_equal_11")
    pod_uids: set[str] = set()
    names: set[str] = set()
    for raw in pods:
        pod = _mapping(raw, "historical_failed_pod")
        if set(pod) != {"uid", "namespace", "name", "reason", "owner_uid"}:
            raise R7ContractError("historical_failed_pod_fields_mismatch")
        uid = _uuid(pod["uid"], "historical_failed_pod_uid")
        owner_uid = _uuid(pod["owner_uid"], "historical_failed_pod_owner_uid")
        name = str(pod["name"])
        if uid in pod_uids or name in names:
            raise R7ContractError("historical_failed_pods_must_be_unique")
        pod_uids.add(uid)
        names.add(name)
        if pod["namespace"] != "evm-production":
            raise R7ContractError("historical_failed_pod_namespace_mismatch")
        if not name.startswith("evm-b0-production-"):
            raise R7ContractError("historical_failed_pod_name_mismatch")
        if pod["reason"] != "UnexpectedAdmissionError":
            raise R7ContractError("historical_failed_pod_reason_mismatch")
        if not owner_uid:
            raise R7ContractError("historical_failed_pod_owner_required")
    selectors = tuple(_sequence(kubernetes["residual_selectors"], "residual_selectors"))
    if selectors != ("evm.openai.local/scenario=s8-v4-x1",):
        raise R7ContractError("kubernetes_residual_selectors_mismatch")
    return dict(kubernetes)


def _attestation_time(value: Any, source: str) -> str:
    captured_at = _nonempty(value, f"historical_attestation_captured_at:{source}")
    if not captured_at.endswith("Z"):
        raise R7ContractError(f"historical_attestation_utc_timestamp_required:{source}")
    try:
        parsed = datetime.fromisoformat(captured_at[:-1] + "+00:00")
    except ValueError as exc:
        raise R7ContractError(f"historical_attestation_timestamp_invalid:{source}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise R7ContractError(f"historical_attestation_utc_timestamp_required:{source}")
    return captured_at


def _validate_historical_attestation(
    *,
    source: str,
    manifest_item: Mapping[str, Any],
    attestation: Mapping[str, Any],
    attestation_path: Path,
    expected_kubernetes_uids: frozenset[str],
    proof_paths: set[Path],
) -> None:
    required = {
        "source",
        "captured_at",
        "query_sha256",
        "counts",
        "classification",
        "records",
    }
    if set(attestation) != required or attestation["source"] != source:
        raise R7ContractError(f"historical_attestation_source_or_fields_mismatch:{source}")
    _attestation_time(attestation["captured_at"], source)
    query_sha = _full_sha256(attestation["query_sha256"], f"historical_attestation_query:{source}")
    if query_sha != HISTORICAL_QUERY_SHA256[source]:
        raise R7ContractError(f"historical_attestation_canonical_query_mismatch:{source}")
    count_names = (
        "observed_count",
        "executing_count",
        "historical_count",
        "unproven_count",
    )
    raw_counts = _mapping(attestation["counts"], f"historical_attestation_counts:{source}")
    if set(raw_counts) != set(count_names):
        raise R7ContractError(f"historical_attestation_count_fields_mismatch:{source}")
    counts: dict[str, int] = {}
    for name in count_names:
        raw = raw_counts[name]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise R7ContractError(f"historical_attestation_count_invalid:{source}:{name}")
        counts[name] = raw
        if raw != manifest_item[name]:
            raise R7ContractError(f"historical_attestation_manifest_count_mismatch:{source}:{name}")
    if attestation["classification"] != manifest_item["classification"]:
        raise R7ContractError(f"historical_attestation_classification_mismatch:{source}")

    records = _sequence(attestation["records"], f"historical_attestation_records:{source}")
    if len(records) != counts["observed_count"]:
        raise R7ContractError(f"historical_attestation_record_count_mismatch:{source}")
    identities: set[str] = set()
    kubernetes_uids: set[str] = set()
    derived = {"executing": 0, "historical_nonexecuting": 0, "unproven": 0}
    proof_count_names = (
        "active_job_count",
        "active_claim_count",
        "active_lease_count",
        "outcome_unknown_count",
    )
    for raw_record in records:
        record = _mapping(raw_record, f"historical_attestation_record:{source}")
        if set(record) != {
            "identity",
            "observed_state",
            "classification",
            "execution_proof",
        }:
            raise R7ContractError(f"historical_attestation_record_fields_mismatch:{source}")
        identity = _mapping(record["identity"], f"historical_attestation_identity:{source}")
        if source == "control_plane_task_entity_statuses":
            if set(identity) != {"entity_id", "created_at", "updated_at"}:
                raise R7ContractError(
                    "historical_attestation_control_plane_identity_fields_mismatch"
                )
            _nonempty(identity["entity_id"], "historical_attestation_control_plane_entity_id")
            _nonempty(identity["created_at"], "historical_attestation_control_plane_created_at")
            _nonempty(identity["updated_at"], "historical_attestation_control_plane_updated_at")
        elif source == "mlflow_running_rows":
            if set(identity) != {"run_id", "lifecycle_stage", "start_time", "end_time"}:
                raise R7ContractError("historical_attestation_mlflow_identity_fields_mismatch")
            for name in ("run_id", "lifecycle_stage", "start_time", "end_time"):
                if not isinstance(identity[name], str):
                    raise R7ContractError(f"historical_attestation_mlflow_{name}_string_required")
                _nonempty(identity[name], f"historical_attestation_mlflow_{name}")
        else:
            if set(identity) != {"uid", "namespace", "name", "owner_uid", "reason"}:
                raise R7ContractError("historical_attestation_kubernetes_identity_fields_mismatch")
            uid = _uuid(identity["uid"], "historical_attestation_kubernetes_uid")
            _uuid(identity["owner_uid"], "historical_attestation_kubernetes_owner_uid")
            for name in ("namespace", "name", "reason"):
                _nonempty(identity[name], f"historical_attestation_kubernetes_{name}")
            kubernetes_uids.add(uid)
        stable_identity = json.dumps(
            dict(identity),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if stable_identity in identities:
            raise R7ContractError(f"historical_attestation_record_identity_duplicate:{source}")
        identities.add(stable_identity)
        _nonempty(record["observed_state"], f"historical_attestation_observed_state:{source}")
        classification = str(record["classification"])
        if classification not in derived:
            raise R7ContractError(f"historical_attestation_record_classification_invalid:{source}")
        proof = _mapping(
            record["execution_proof"], f"historical_attestation_execution_proof:{source}"
        )
        if set(proof) != {"inactivity_proven", *proof_count_names, "evidence"}:
            raise R7ContractError(
                f"historical_attestation_execution_proof_fields_mismatch:{source}"
            )
        if not isinstance(proof["inactivity_proven"], bool):
            raise R7ContractError(
                f"historical_attestation_inactivity_proven_boolean_required:{source}"
            )
        proof_counts: dict[str, int] = {}
        for name in proof_count_names:
            raw = proof[name]
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                raise R7ContractError(f"historical_attestation_proof_count_invalid:{source}:{name}")
            proof_counts[name] = raw
        active_links = sum(proof_counts.values())
        evidence = _mapping(proof["evidence"], f"historical_attestation_proof_evidence:{source}")
        if set(evidence) != {"path", "sha256"}:
            raise R7ContractError(f"historical_attestation_proof_evidence_fields_mismatch:{source}")
        evidence_path = Path(
            _nonempty(evidence["path"], f"historical_attestation_proof_evidence_path:{source}")
        ).resolve()
        evidence_sha = _full_sha256(
            evidence["sha256"], f"historical_attestation_proof_evidence:{source}"
        )
        if evidence_path == attestation_path or evidence_path in proof_paths:
            raise R7ContractError("historical_attestation_proof_paths_must_be_distinct")
        proof_paths.add(evidence_path)
        if not evidence_path.is_file():
            raise R7ContractError(f"historical_attestation_proof_file_missing:{source}")
        try:
            proof_payload, measured_evidence_sha = _read_json_snapshot(evidence_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise R7ContractError(f"historical_attestation_proof_json_invalid:{source}") from exc
        if measured_evidence_sha != evidence_sha:
            raise R7ContractError(f"historical_attestation_proof_sha256_mismatch:{source}")
        proof_payload = _mapping(proof_payload, f"historical_attestation_proof_payload:{source}")
        required_proof_payload = {
            "source",
            "identity",
            "observed_state",
            "captured_at",
            "query_sha256",
            *proof_count_names,
            "inactivity_decision",
            "decision_authority",
        }
        if set(proof_payload) != required_proof_payload:
            raise R7ContractError(f"historical_attestation_proof_payload_fields_mismatch:{source}")
        if proof_payload["source"] != source:
            raise R7ContractError(f"historical_attestation_proof_source_mismatch:{source}")
        if dict(_mapping(proof_payload["identity"], "proof_identity")) != dict(identity):
            raise R7ContractError(f"historical_attestation_proof_identity_mismatch:{source}")
        if proof_payload["observed_state"] != record["observed_state"]:
            raise R7ContractError(f"historical_attestation_proof_observed_state_mismatch:{source}")
        _attestation_time(proof_payload["captured_at"], f"proof:{source}")
        if proof_payload["query_sha256"] != attestation["query_sha256"]:
            raise R7ContractError(f"historical_attestation_proof_query_mismatch:{source}")
        for name in proof_count_names:
            if proof_payload[name] != proof_counts[name]:
                raise R7ContractError(
                    f"historical_attestation_proof_count_mismatch:{source}:{name}"
                )
        expected_decision = (
            "executing"
            if active_links
            else "proven_inactive"
            if proof["inactivity_proven"] is True
            else "unproven"
        )
        if proof_payload["inactivity_decision"] != expected_decision:
            raise R7ContractError(f"historical_attestation_proof_decision_mismatch:{source}")
        if proof_payload["decision_authority"] != HISTORICAL_DECISION_AUTHORITY:
            raise R7ContractError(
                f"historical_attestation_proof_decision_authority_mismatch:{source}"
            )
        if classification == "historical_nonexecuting" and (
            proof["inactivity_proven"] is not True or active_links != 0
        ):
            raise R7ContractError(f"historical_attestation_inactivity_proof_required:{source}")
        if classification == "unproven" and (
            proof["inactivity_proven"] is not False or active_links != 0
        ):
            raise R7ContractError(f"historical_attestation_unproven_record_mismatch:{source}")
        if classification == "executing" and (
            active_links == 0 or proof["inactivity_proven"] is not False
        ):
            raise R7ContractError(f"historical_attestation_execution_proof_required:{source}")
        derived[classification] += 1

    if derived["executing"] != counts["executing_count"]:
        raise R7ContractError(f"historical_attestation_executing_records_mismatch:{source}")
    if derived["historical_nonexecuting"] != counts["historical_count"]:
        raise R7ContractError(f"historical_attestation_historical_records_mismatch:{source}")
    if derived["unproven"] != counts["unproven_count"]:
        raise R7ContractError(f"historical_attestation_unproven_records_mismatch:{source}")
    if source == "kubernetes_terminal_failed_objects" and kubernetes_uids != set(
        expected_kubernetes_uids
    ):
        raise R7ContractError("historical_attestation_kubernetes_identity_set_mismatch")


def _validate_job_scope(
    value: Any,
    *,
    verify_attestations: bool = False,
    expected_kubernetes_uids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    contract = _mapping(value, "job_scope_contract")
    required = {
        "canonical_active_jobs",
        "historical_observations",
        "historical_classifications",
    }
    if set(contract) != required:
        raise R7ContractError("job_scope_contract_fields_mismatch")
    for name in ("canonical_active_jobs", "historical_observations"):
        if dict(_mapping(contract[name], f"job_scope_{name}")) != JOB_SCOPE_CONTRACT[name]:
            raise R7ContractError(f"job_scope_{name}_mismatch")
    raw_classifications = _sequence(
        contract["historical_classifications"], "historical_classifications"
    )
    if len(raw_classifications) != len(HISTORICAL_CLASSIFICATION_SOURCES):
        raise R7ContractError("historical_classification_count_mismatch")
    normalized: list[dict[str, Any]] = []
    attestation_paths: set[Path] = set()
    proof_paths: set[Path] = set()
    for expected_source, raw in zip(
        HISTORICAL_CLASSIFICATION_SOURCES, raw_classifications, strict=True
    ):
        item = _mapping(raw, "historical_classification")
        required_item = {
            "source",
            "observed_count",
            "executing_count",
            "historical_count",
            "unproven_count",
            "classification",
            "attestation",
        }
        if set(item) != required_item or item["source"] != expected_source:
            raise R7ContractError("historical_classification_source_or_fields_mismatch")
        counts: dict[str, int] = {}
        for field_name in (
            "observed_count",
            "executing_count",
            "historical_count",
            "unproven_count",
        ):
            raw_count = item[field_name]
            if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
                raise R7ContractError(
                    f"historical_classification_nonnegative_integer_required:{expected_source}"
                )
            counts[field_name] = raw_count
        if counts["observed_count"] != (
            counts["executing_count"] + counts["historical_count"] + counts["unproven_count"]
        ):
            raise R7ContractError(f"historical_classification_count_sum_mismatch:{expected_source}")
        classification = str(item["classification"])
        expected_classification = (
            "unproven"
            if counts["unproven_count"]
            else "executing"
            if counts["executing_count"]
            else "historical_nonexecuting"
        )
        if classification != expected_classification:
            raise R7ContractError(f"historical_classification_label_mismatch:{expected_source}")
        if (
            expected_source == "kubernetes_terminal_failed_objects"
            and counts["observed_count"] != 11
        ):
            raise R7ContractError("historical_failed_pod_classification_count_mismatch")
        attestation = _mapping(item["attestation"], "historical_classification_attestation")
        if set(attestation) != {"path", "sha256"}:
            raise R7ContractError("historical_classification_attestation_fields_mismatch")
        attestation_value = {
            "path": _nonempty(attestation["path"], "historical_attestation_path"),
            "sha256": _full_sha256(attestation["sha256"], "historical_attestation"),
        }
        attestation_path = Path(attestation_value["path"]).resolve()
        if attestation_path in attestation_paths:
            raise R7ContractError("historical_attestation_paths_must_be_distinct")
        attestation_paths.add(attestation_path)
        if verify_attestations:
            if not attestation_path.is_file():
                raise R7ContractError(f"historical_attestation_file_missing:{expected_source}")
            try:
                payload, measured_attestation_sha = _read_json_snapshot(attestation_path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise R7ContractError(
                    f"historical_attestation_json_invalid:{expected_source}"
                ) from exc
            if measured_attestation_sha != attestation_value["sha256"]:
                raise R7ContractError(f"historical_attestation_sha256_mismatch:{expected_source}")
            _validate_historical_attestation(
                source=expected_source,
                manifest_item=item,
                attestation=_mapping(payload, f"historical_attestation:{expected_source}"),
                attestation_path=attestation_path,
                expected_kubernetes_uids=expected_kubernetes_uids,
                proof_paths=proof_paths,
            )
        normalized.append(
            {
                "source": expected_source,
                **counts,
                "classification": classification,
                "attestation": attestation_value,
            }
        )
    if proof_paths & attestation_paths:
        raise R7ContractError("historical_attestation_and_proof_paths_must_be_distinct")
    return {
        "canonical_active_jobs": dict(JOB_SCOPE_CONTRACT["canonical_active_jobs"]),
        "historical_observations": dict(JOB_SCOPE_CONTRACT["historical_observations"]),
        "historical_classifications": normalized,
    }


def _validate_expected_state(value: Any, revision: str, tree: str) -> dict[str, Any]:
    state = _mapping(value, "expected_state")
    required = {
        "compose",
        "api",
        "database",
        "kubernetes",
        "compose_services",
        "api_base_url",
        "b0",
        "prometheus_jobs",
        "prometheus_targets_url",
        "gpu_lease_path",
        "active_job_roots",
        "active_claim_roots",
        "x1_residue_paths",
        "x1_docker_name_filter",
        "x1_ports",
        "x1_kubernetes_selectors",
    }
    if set(state) != required:
        raise R7ContractError("expected_state_fields_mismatch")
    compose = _validate_compose(state["compose"])
    api = _validate_api(state["api"], revision, tree, compose)
    database = _validate_database(state["database"])
    kubernetes = _validate_kubernetes(state["kubernetes"])
    if tuple(_sequence(state["compose_services"], "compose_services")) != LONG_LIVED_SERVICES:
        raise R7ContractError("compose_services_mismatch")
    if state["api_base_url"] != api["base_url"]:
        raise R7ContractError("api_base_url_projection_mismatch")
    b0 = _mapping(state["b0"], "expected_state_b0")
    _uuid(b0.get("uid"), "expected_b0_uid")
    image = _nonempty(b0.get("image"), "expected_b0_image")
    if "@sha256:" not in image:
        raise R7ContractError("expected_b0_digest_pinned_image_required")
    if tuple(_sequence(state["prometheus_jobs"], "prometheus_jobs")) != PROMETHEUS_JOBS:
        raise R7ContractError("prometheus_jobs_mismatch")
    _nonempty(state["prometheus_targets_url"], "prometheus_targets_url")
    _nonempty(state["gpu_lease_path"], "gpu_lease_path")
    _sequence(state["active_job_roots"], "active_job_roots")
    _sequence(state["active_claim_roots"], "active_claim_roots")
    residue_paths = _sequence(state["x1_residue_paths"], "x1_residue_paths")
    if len(residue_paths) != 2 or any(not str(item) for item in residue_paths):
        raise R7ContractError("x1_residue_paths_exact_two_required")
    if state["x1_docker_name_filter"] != "name=evm-x1":
        raise R7ContractError("x1_docker_name_filter_mismatch")
    if tuple(_sequence(state["x1_ports"], "x1_ports")) != (31120, 31121, 31122):
        raise R7ContractError("x1_ports_mismatch")
    selectors = tuple(_sequence(state["x1_kubernetes_selectors"], "x1_selectors"))
    if selectors != tuple(kubernetes["residual_selectors"]):
        raise R7ContractError("x1_selector_projection_mismatch")
    return {
        "compose": compose,
        "api": api,
        "database": database,
        "kubernetes": kubernetes,
    }


def validate_r7_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_revision: str,
    mode: str = "restore-only",
    repository_root: Path | None = None,
    runtime_timeout: TimeoutContract | None = None,
    lifecycle_timeout: LifecycleTimeoutContract | None = None,
    expected_untracked_path_set_sha256: str | None = None,
    verify_attestations: bool | None = None,
) -> dict[str, Any]:
    """Validate a restore-only r7 manifest against executable defaults."""

    required_top_level = {
        "schema_version",
        "work_order_id",
        "bundle_id",
        "execution_mode",
        "created_at",
        "canonical_revision",
        "canonical_tree",
        "bundle",
        "repository",
        "parent_checkpoints",
        "output",
        "timeout_contract",
        "lifecycle_timeout_contract",
        "process_containment",
        "probe_max_attempts",
        "call_contract",
        "expected_state",
        "job_scope_contract",
        "etw_contract",
        "evidence",
        "runtime",
    }
    if set(manifest) != required_top_level:
        raise R7ContractError("r7_restore_manifest_top_level_fields_mismatch")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise R7ContractError("r7_restore_manifest_schema_required")
    if manifest.get("work_order_id") != WORK_ORDER_ID:
        raise R7ContractError("r7_restore_work_order_id_mismatch")
    bundle_id = _nonempty(manifest.get("bundle_id"), "r7_bundle_id")
    if "r7" not in bundle_id.lower():
        raise R7ContractError("r7_bundle_identity_required")
    _attestation_time(manifest.get("created_at"), "manifest")
    if mode != "restore-only" or manifest.get("execution_mode") != "restore-only":
        raise R7ContractError("r7_restore_only_mode_required")
    revision = _full_sha1(manifest.get("canonical_revision"), "canonical_revision")
    expected = _full_sha1(expected_revision, "expected_revision")
    if revision != expected:
        raise R7ContractError("manifest_canonical_revision_mismatch")
    if revision == PRE_R7_REVISION:
        raise R7ContractError("pre_r7_revision_pin_reuse_forbidden")
    tree = _full_sha1(manifest.get("canonical_tree"), "canonical_tree")

    executable_timeout = (runtime_timeout or TimeoutContract()).validate()
    promised_timeout = TimeoutContract.from_mapping(
        _mapping(manifest.get("timeout_contract"), "timeout_contract")
    )
    if promised_timeout.to_dict() != executable_timeout.to_dict():
        raise R7ContractError("manifest_runtime_timeout_contract_mismatch")
    executable_lifecycle = (lifecycle_timeout or LifecycleTimeoutContract()).validate()
    promised_lifecycle = LifecycleTimeoutContract.from_mapping(
        manifest.get("lifecycle_timeout_contract")
    )
    if promised_lifecycle.to_dict() != executable_lifecycle.to_dict():
        raise R7ContractError("manifest_runtime_lifecycle_timeout_mismatch")
    try:
        containment = validate_process_containment_contract(manifest.get("process_containment"))
    except R7ProcessContractError as exc:
        raise R7ContractError(str(exc)) from exc

    if manifest.get("probe_max_attempts") != 1 or isinstance(
        manifest.get("probe_max_attempts"), bool
    ):
        raise R7ContractError("probe_max_attempts_must_equal_1")
    calls = _mapping(manifest.get("call_contract"), "call_contract")
    if set(calls) != {"restore-only", "launcher", "collectors", "downstream"}:
        raise R7ContractError("call_contract_sections_mismatch")
    _exact_counts(calls["restore-only"], RESTORE_LIFECYCLE_COUNTS, "restore_call_contract")
    launcher = _exact_counts(calls["launcher"], LAUNCHER_COUNTS, "launcher_call_contract")
    _exact_counts(calls["collectors"], RESTORE_COLLECTOR_COUNTS, "collector_call_contract")
    _exact_counts(calls["downstream"], DOWNSTREAM_COUNTS, "downstream_call_contract")

    repository = _mapping(manifest.get("repository"), "repository")
    expected_repository_fields = {
        "preserved_untracked_count",
        "untracked_path_set_sha256",
        "untracked_path_set_encoding",
        "tracked_changes",
    }
    if set(repository) != expected_repository_fields:
        raise R7ContractError("repository_contract_fields_mismatch")
    if repository["preserved_untracked_count"] != PRESERVED_UNTRACKED_COUNT or isinstance(
        repository["preserved_untracked_count"], bool
    ):
        raise R7ContractError("preserved_untracked_count_mismatch")
    untracked_digest = _full_sha256(repository["untracked_path_set_sha256"], "untracked_path_set")
    if expected_untracked_path_set_sha256 is not None and untracked_digest != _full_sha256(
        expected_untracked_path_set_sha256, "expected_untracked_path_set"
    ):
        raise R7ContractError("untracked_path_set_sha256_mismatch")
    if repository["untracked_path_set_encoding"] != UNTRACKED_PATH_SET_ENCODING:
        raise R7ContractError("untracked_path_set_encoding_mismatch")
    if repository["tracked_changes"] != 0 or isinstance(repository["tracked_changes"], bool):
        raise R7ContractError("tracked_changes_must_equal_zero")

    expected_state = _validate_expected_state(manifest.get("expected_state"), revision, tree)
    verify_attestation_files = (
        repository_root is not None if verify_attestations is None else bool(verify_attestations)
    )
    kubernetes_uids = frozenset(
        str(item["uid"]) for item in expected_state["kubernetes"]["allowed_historical_failed_pods"]
    )
    job_scope = _validate_job_scope(
        manifest.get("job_scope_contract"),
        verify_attestations=verify_attestation_files,
        expected_kubernetes_uids=kubernetes_uids,
    )

    output = _mapping(manifest.get("output"), "output")
    if set(output) != {"path", "must_not_exist_before_runner", "write_mode"}:
        raise R7ContractError("output_contract_fields_mismatch")
    if output.get("write_mode") != "create-exclusive":
        raise R7ContractError("output_create_exclusive_required")
    if output.get("must_not_exist_before_runner") is not True:
        raise R7ContractError("output_must_not_exist_before_runner_required")
    output_path = Path(_nonempty(output.get("path"), "output_path")).resolve()
    bundle = _mapping(manifest.get("bundle"), "bundle")
    if set(bundle) != {"path"}:
        raise R7ContractError("bundle_contract_fields_mismatch")
    bundle_path = Path(_nonempty(bundle.get("path"), "bundle_path")).resolve()
    parents = _validate_parent_entries(
        manifest.get("parent_checkpoints"),
        bundle_directory=bundle_path,
        output_directory=output_path,
    )

    evidence = _mapping(manifest.get("evidence"), "evidence")
    required_evidence = {
        "write_mode": "create-exclusive",
        "failure_creates_completion_marker": False,
        "restore_only_creates_completion_marker": False,
        "failure_index_is_not_success_index": True,
        "success_requires_all_invariants": True,
    }
    if dict(evidence) != required_evidence:
        raise R7ContractError("evidence_contract_mismatch")

    etw = _mapping(manifest.get("etw_contract"), "etw_contract")
    if set(etw) != {
        "decision",
        "amendment_path",
        "amendment_sha256",
        "fresh_capture_required_for_phase_b2_go",
        "fresh_invocations",
    }:
        raise R7ContractError("etw_contract_fields_mismatch")
    if etw["decision"] != (
        "existing_pinned_etw_evidence_is_admissible;fresh_capture_not_a_phase_b2_go_invariant"
    ):
        raise R7ContractError("etw_contract_decision_mismatch")
    _nonempty(etw["amendment_path"], "etw_amendment_path")
    _full_sha256(etw["amendment_sha256"], "etw_amendment")
    if (
        etw["fresh_capture_required_for_phase_b2_go"] is not False
        or etw["fresh_invocations"] != 0
        or isinstance(etw["fresh_invocations"], bool)
    ):
        raise R7ContractError("etw_fresh_capture_must_remain_zero")
    runtime = None
    if repository_root is not None:
        runtime = validate_runtime_pins(manifest, repository_root)
    else:
        runtime_contract = _mapping(manifest.get("runtime"), "runtime")
        if set(runtime_contract) != set(RUNTIME_COMPONENTS):
            raise R7ContractError("runtime_component_role_set_mismatch")
        for name in RUNTIME_COMPONENTS:
            component = _mapping(runtime_contract[name], f"runtime_{name}")
            if set(component) != {"path", "sha256", "blob_oid", "bytes"}:
                raise R7ContractError(f"runtime_{name}_fields_mismatch")
            _nonempty(component["path"], f"runtime_{name}_path")
            _full_sha256(component.get("sha256"), f"runtime_{name}")
            _full_sha1(component.get("blob_oid"), f"runtime_{name}_blob")
            if (
                isinstance(component["bytes"], bool)
                or not isinstance(component["bytes"], int)
                or component["bytes"] < 1
            ):
                raise R7ContractError(f"runtime_{name}_positive_bytes_required")

    return {
        "revision": revision,
        "tree": tree,
        "mode": "restore-only",
        "timeout_contract": executable_timeout.to_dict(),
        "lifecycle_timeout_contract": executable_lifecycle.to_dict(),
        "process_containment": containment,
        "launcher_calls": launcher,
        "untracked_path_set_sha256": untracked_digest,
        "parents": parents,
        "job_scope_contract": job_scope,
        "expected_state": expected_state,
        "runtime": runtime,
    }


def read_parent_checkpoints(
    manifest_parent_entries: Any,
) -> tuple[dict[str, dict[str, Any]], RestoreCheckpoint]:
    """Read and hash seven immutable parents without executing any of them."""

    entries = _validate_parent_entries(manifest_parent_entries)
    payloads: dict[str, dict[str, Any]] = {}
    for role in PARENT_CHECKPOINT_ROLES:
        entry = entries[role]
        path = Path(entry["path"])
        if not path.is_file():
            raise R7ContractError(f"parent_checkpoint_file_missing:{role}:{path}")
        try:
            payload, measured_parent_sha = _read_json_snapshot(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise R7ContractError(f"parent_checkpoint_json_invalid:{role}") from exc
        if measured_parent_sha != entry["sha256"]:
            raise R7ContractError(f"parent_checkpoint_sha256_mismatch:{role}")
        if not isinstance(payload, dict):
            raise R7ContractError(f"parent_checkpoint_object_required:{role}")
        if payload.get("acceptance_credit") is True:
            raise R7ContractError(f"parent_checkpoint_credit_forbidden:{role}")
        if (
            payload.get("success_marker_created") is True
            or payload.get("completion_marker_created") is True
        ):
            raise R7ContractError(f"parent_checkpoint_success_marker_forbidden:{role}")
        if payload.get("phase_b2_executed") is True:
            raise R7ContractError(f"parent_checkpoint_phase_b2_execution_forbidden:{role}")
        payloads[role] = payload

    r5_seal = payloads["r5_failure_seal"]
    if r5_seal.get("failure_only") is not True or r5_seal.get("acceptance_credit") is not False:
        raise R7ContractError("r5_failure_seal_semantics_required")
    r5_index = payloads["r5_failure_index"]
    if r5_index.get("failure_only") is not True or r5_index.get("acceptance_credit") is not False:
        raise R7ContractError("r5_failure_index_semantics_required")
    report_value = r5_seal.get("report")
    if not isinstance(report_value, Mapping):
        metadata = r5_seal.get("metadata")
        report_value = metadata.get("report") if isinstance(metadata, Mapping) else None
    report = _mapping(report_value, "r5_failure_seal_report")
    historical_counts = _exact_counts(
        report.get("call_counts"), RESTORE_LIFECYCLE_COUNTS, "r5_historical_call_counts"
    )
    checkpoint = RestoreCheckpoint(
        source="r7_seven_parent_checkpoint_set",
        historical_call_counts=historical_counts,
        previous_attempt_failed=True,
    )
    return payloads, checkpoint


def decode_launcher_evidence(encoded: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
    try:
        raw = base64.b64decode(encoded, validate=True)
        value = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R7ContractError("launcher_evidence_base64_json_invalid") from exc
    if not isinstance(value, dict):
        raise R7ContractError("launcher_evidence_object_required")
    required_fields = {
        "schema",
        "token_evidence",
        "sha_chain",
        "git",
        "run_id",
        "mode",
        "invocation_counts",
    }
    if set(value) != required_fields:
        raise R7ContractError("launcher_evidence_top_level_fields_mismatch")
    if value.get("schema") != LAUNCHER_EVIDENCE_SCHEMA:
        raise R7ContractError("launcher_evidence_schema_mismatch")
    if value.get("run_id") != manifest.get("bundle_id"):
        raise R7ContractError("launcher_evidence_run_id_mismatch")
    if value.get("mode") != "restore-only":
        raise R7ContractError("launcher_evidence_mode_mismatch")
    token = _mapping(value.get("token_evidence"), "launcher_token_evidence")
    if token.get("administrator") is not True:
        raise R7ContractError("launcher_administrator_token_required")
    integrity = str(token.get("integrity", "")).lower()
    if integrity not in {"high", "system"}:
        raise R7ContractError("launcher_high_or_system_integrity_required")
    if str(token.get("token_elevation_type", "")).lower() != "full":
        raise R7ContractError("launcher_full_token_required")
    chain = _mapping(value.get("sha_chain"), "launcher_sha_chain")
    required_chain = {"outer", "bridge", "manifest", *RUNTIME_COMPONENTS, *PARENT_CHECKPOINT_ROLES}
    if set(chain) != required_chain:
        raise R7ContractError("launcher_sha_chain_role_set_mismatch")
    for name in required_chain:
        _full_sha256(chain[name], f"launcher_sha_chain_{name}")
    runtime = _mapping(manifest.get("runtime"), "runtime")
    for name in RUNTIME_COMPONENTS:
        component = _mapping(runtime.get(name), f"runtime_{name}")
        if str(chain[name]).lower() != str(component.get("sha256", "")).lower():
            raise R7ContractError(f"launcher_runtime_sha_chain_mismatch:{name}")
    parents = _validate_parent_entries(manifest.get("parent_checkpoints"))
    for role in PARENT_CHECKPOINT_ROLES:
        if str(chain[role]).lower() != parents[role]["sha256"]:
            raise R7ContractError(f"launcher_parent_sha_chain_mismatch:{role}")
    value["invocation_counts"] = _exact_counts(
        value.get("invocation_counts"), LAUNCHER_COUNTS, "launcher_evidence_invocation_counts"
    )
    return value


def r7_restore_report(report: RestoreReport, run_id: str) -> dict[str, Any]:
    if report.mode != "restore-only":
        raise R7ContractError("restore_only_report_mode_required")
    _exact_counts(report.call_counts, RESTORE_LIFECYCLE_COUNTS, "restore_report_call_counts")
    value = report.to_dict()
    value.update(
        {
            "schema": "s8-v4-x1-phase-b2-r7-restore-report/v1",
            "run_id": _nonempty(run_id, "run_id"),
            "restore_only_pass": bool(report.passed),
            "acceptance_credit": False,
            "phase_b2_executed": False,
            "completion_marker_created": False,
            "process_containment": "windows_job_object",
        }
    )
    return value


def _file_identity(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


class EvidenceWriter:
    """One create-exclusive evidence directory for one r7 restore attempt."""

    def __init__(self, output_directory: Path) -> None:
        self.root = Path(output_directory).resolve()
        try:
            self.root.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise R7EvidenceExistsError(f"evidence_directory_exists:{self.root}") from exc

    @staticmethod
    def _publish_source_leaf(name: str) -> str:
        return f".{name}.publish-source"

    @classmethod
    def _planned_publish_source(cls, name: str, payload: bytes) -> dict[str, Any]:
        return {
            "path": cls._publish_source_leaf(name),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    def write_bytes(self, name: str, payload: bytes) -> dict[str, Any]:
        if Path(name).name != name or name in {".", ".."}:
            raise ValueError("evidence_leaf_name_required")
        if not isinstance(payload, bytes):
            raise TypeError("evidence_payload_bytes_required")
        path = self.root / name
        if path.exists():
            raise R7EvidenceExistsError(f"evidence_path_exists:{path}")
        publish_source = self.root / self._publish_source_leaf(name)
        if publish_source.exists():
            raise R7EvidenceExistsError(f"evidence_publish_source_exists:{publish_source}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(publish_source, flags, 0o600)
        except FileExistsError as exc:
            raise R7EvidenceExistsError(f"evidence_publish_source_exists:{publish_source}") from exc
        try:
            view = memoryview(payload)
            offset = 0
            while offset < len(view):
                written = os.write(descriptor, view[offset:])
                if written <= 0:
                    raise OSError("exclusive evidence write made no progress")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            # Hard-link publication is an atomic create-new operation: it
            # cannot replace an existing leaf, and the final name never
            # exposes a partially written payload.
            os.link(publish_source, path)
        except FileExistsError as exc:
            raise R7EvidenceExistsError(f"evidence_path_exists:{path}") from exc
        if not os.path.samefile(publish_source, path):
            raise OSError("evidence_publish_source_identity_mismatch")
        return _file_identity(path, self.root)

    def write_json(self, name: str, value: Any) -> dict[str, Any]:
        return self.write_bytes(name, canonical_json_bytes(value))

    def inventory(self, *, exclude: Sequence[str] = ()) -> list[dict[str, Any]]:
        excluded = set(exclude)
        return [
            _file_identity(path, self.root)
            for path in sorted(self.root.iterdir(), key=lambda item: item.name)
            if path.is_file() and path.name not in excluded
        ]

    def seal_failure(
        self,
        report: Mapping[str, Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        prior = self.inventory(exclude=("failure-seal.json", "failure-evidence-index.json"))
        seal = {
            "schema": "s8-v4-x1-phase-b2-r7-failure-seal/v1",
            "sealed_at": utc_now(),
            "failure_only": True,
            "decision": "manual_intervention_required",
            "acceptance_credit": False,
            "success_marker_created": False,
            "completion_marker_created": False,
            "phase_b2_executed": False,
            "report": dict(report),
            "metadata": dict(metadata or {}),
            "prior_files": prior,
        }
        # The seal is the final create-exclusive commit record.  Publish the
        # index first with the exact planned seal identity so a crash can leave
        # only an explicitly uncommitted draft, never a committed seal lacking
        # its index.
        seal_payload = canonical_json_bytes(seal)
        planned_seal = {
            "path": "failure-seal.json",
            "bytes": len(seal_payload),
            "sha256": hashlib.sha256(seal_payload).hexdigest(),
        }
        planned_seal_source = self._planned_publish_source("failure-seal.json", seal_payload)
        index = {
            "schema": "s8-v4-x1-phase-b2-r7-failure-evidence-index/v1",
            "created_at": utc_now(),
            "failure_only": True,
            "is_success_index": False,
            "acceptance_credit": False,
            "completion_marker_created": False,
            "phase_b2_executed": False,
            "publication_state": "pending_until_commit_record_exists",
            "commit_record": planned_seal,
            "files": [*prior, planned_seal_source, planned_seal],
        }
        index_file = self.write_json("failure-evidence-index.json", index)
        seal_file = self.write_bytes("failure-seal.json", seal_payload)
        if seal_file != planned_seal:
            raise OSError("failure seal identity differs from planned commit record")
        seal_source_file = _file_identity(
            self.root / self._publish_source_leaf("failure-seal.json"), self.root
        )
        if seal_source_file != planned_seal_source:
            raise OSError("failure seal publish source differs from planned identity")
        return {"failure_seal": seal_file, "failure_index": index_file}

    def seal_restore_only(
        self,
        report: Mapping[str, Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if report.get("restore_only_pass") is not True or report.get("passed") is not True:
            raise R7SuccessInvariantError("passing_restore_only_report_required")
        if report.get("phase_b2_executed") is not False:
            raise R7SuccessInvariantError("restore_only_phase_b2_execution_forbidden")
        if report.get("acceptance_credit") is not False:
            raise R7SuccessInvariantError("restore_only_acceptance_credit_forbidden")
        if report.get("completion_marker_created") is not False:
            raise R7SuccessInvariantError("restore_only_completion_marker_forbidden")
        if report.get("manual_intervention_required") is not False:
            raise R7SuccessInvariantError("restore_only_manual_intervention_forbidden")
        if report.get("residual_pids"):
            raise R7SuccessInvariantError("restore_only_residual_process_forbidden")
        if report.get("mode") != "restore-only":
            raise R7SuccessInvariantError("restore_only_report_mode_required")
        if report.get("decision") != "restore_only_pass":
            raise R7SuccessInvariantError("restore_only_pass_decision_required")
        if report.get("deadline_exceeded") is not False:
            raise R7SuccessInvariantError("restore_only_deadline_must_not_be_exceeded")
        _exact_counts(report.get("call_counts"), RESTORE_LIFECYCLE_COUNTS, "report_call_counts")
        required = tuple(str(item) for item in report.get("required_invariants", ()))
        invariants = _mapping(report.get("success_invariants"), "success_invariants")
        if required != R7_REQUIRED_INVARIANTS:
            raise R7SuccessInvariantError("restore_only_required_invariant_set_mismatch")
        expected_invariant_names = {
            *R7_REQUIRED_INVARIANTS,
            *(stage.value for stage in RESTORE_STAGE_ORDER),
        }
        if set(invariants) != expected_invariant_names:
            raise R7SuccessInvariantError("restore_only_success_invariant_fields_mismatch")
        if any(invariants.get(name) is not True for name in expected_invariant_names):
            raise R7SuccessInvariantError("restore_only_all_required_invariants_required")
        stages = _sequence(report.get("stages"), "restore_stages")
        if len(stages) != len(RESTORE_STAGE_ORDER):
            raise R7SuccessInvariantError("restore_only_stage_count_mismatch")
        stage_fields = {
            "stage",
            "started_at",
            "ended_at",
            "duration_seconds",
            "attempts",
            "max_attempts",
            "passed",
            "retryable_ignored",
            "last_error",
            "manual_intervention_required",
            "residual_pids",
            "invariants",
            "details",
            "deadline_remaining_seconds",
        }
        for expected_stage, raw_stage in zip(RESTORE_STAGE_ORDER, stages, strict=True):
            stage = _mapping(raw_stage, "restore_stage")
            if set(stage) != stage_fields or stage["stage"] != expected_stage.value:
                raise R7SuccessInvariantError("restore_only_stage_schema_or_order_mismatch")
            if (
                stage["passed"] is not True
                or stage["attempts"] != 1
                or isinstance(stage["attempts"], bool)
                or stage["max_attempts"] != 1
                or isinstance(stage["max_attempts"], bool)
                or stage["manual_intervention_required"] is not False
                or stage["residual_pids"]
                or stage["last_error"] is not None
            ):
                raise R7SuccessInvariantError(
                    f"restore_only_stage_not_passing:{expected_stage.value}"
                )
            if not isinstance(stage["retryable_ignored"], bool):
                raise R7SuccessInvariantError(
                    "restore_only_stage_retryable_evidence_boolean_required"
                )
            _nonempty(stage["started_at"], "restore_stage_started_at")
            _nonempty(stage["ended_at"], "restore_stage_ended_at")
            for name in ("duration_seconds", "deadline_remaining_seconds"):
                value = stage[name]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or float(value) < 0
                ):
                    raise R7SuccessInvariantError(f"restore_only_stage_{name}_invalid")
            _mapping(stage["invariants"], "restore_stage_invariants")
            _mapping(stage["details"], "restore_stage_details")
        report_payload = canonical_json_bytes(dict(report))
        report_file = self.write_bytes("restore-only-report.json", report_payload)
        report_source_file = self._planned_publish_source(
            "restore-only-report.json", report_payload
        )
        index = {
            "schema": "s8-v4-x1-phase-b2-r7-restore-only-index/v1",
            "created_at": utc_now(),
            "restore_only_pass": True,
            "acceptance_credit": False,
            "is_phase_b2_success_index": False,
            "completion_marker_created": False,
            "phase_b2_executed": False,
            "metadata": dict(metadata or {}),
            "files": [report_source_file, report_file],
        }
        index_file = self.write_json("restore-only-index.json", index)
        return {"restore_only_report": report_file, "restore_only_index": index_file}


__all__ = [
    "AIRFLOW_MIGRATION_HEAD",
    "DATABASE_INSTANCES",
    "DOWNSTREAM_COUNTS",
    "EvidenceWriter",
    "HISTORICAL_DECISION_AUTHORITY",
    "HISTORICAL_QUERY_SHA256",
    "HISTORICAL_QUERY_TEXTS",
    "JOB_SCOPE_CONTRACT",
    "LAUNCHER_COUNTS",
    "LONG_LIVED_SERVICES",
    "LifecycleTimeoutContract",
    "MLFLOW_MIGRATION_HEAD",
    "ONE_SHOT_SERVICES",
    "PARENT_CHECKPOINT_KINDS",
    "PARENT_CHECKPOINT_ROLES",
    "PROCESS_CONTAINMENT_CONTRACT",
    "PRESERVED_UNTRACKED_COUNT",
    "ProbeResult",
    "R7ContractError",
    "R7EvidenceExistsError",
    "R7_REQUIRED_INVARIANTS",
    "R7SuccessInvariantError",
    "RESTORE_COLLECTOR_COUNTS",
    "RESTORE_LIFECYCLE_COUNTS",
    "RESTORE_STAGE_ORDER",
    "RUNTIME_COMPONENTS",
    "ReconcileRestoreHarness",
    "RestoreCheckpoint",
    "RestoreDeadline",
    "RestoreReport",
    "RestoreStage",
    "SCHEMA_VERSION",
    "TimeoutContract",
    "UNTRACKED_PATH_SET_ENCODING",
    "WORK_ORDER_ID",
    "decode_launcher_evidence",
    "git_head_blob_oid",
    "r7_restore_report",
    "read_parent_checkpoints",
    "sha256_file",
    "validate_r7_manifest",
    "validate_runtime_pins",
]
