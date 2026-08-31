"""Fail-closed restore primitives for the S8-V4/X1 Phase B2 r4 harness.

This module deliberately contains no Docker Desktop lifecycle operation and no
Kubernetes mutation.  It provides the bounded process, deadline, checkpoint,
state-machine, and append-only evidence rules used by the separately sealed r4
launchers.  Keeping those rules in a normal Python module makes the promises in
the launch manifest directly unit-testable.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence

import psutil


class PhaseB2R4Error(RuntimeError):
    """Base class for fail-closed r4 harness errors."""


class ContractValidationError(PhaseB2R4Error):
    """Raised when the sealed and executable timeout contracts differ."""


class EvidenceExistsError(PhaseB2R4Error):
    """Raised when append-only evidence would overwrite an existing path."""


class SuccessInvariantError(PhaseB2R4Error):
    """Raised when success evidence is requested before all gates pass."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _finite_positive(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractValidationError(f"{name}_must_be_numeric")
    if not math.isfinite(float(value)) or float(value) <= 0:
        raise ContractValidationError(f"{name}_must_be_finite_positive")


@dataclass(frozen=True)
class TimeoutContract:
    """Timeout values that are pinned identically in manifest and runtime."""

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
            _finite_positive(name, getattr(self, name))
        if not (
            self.kubectl_timeout_seconds
            < self.wrapper_timeout_seconds
            < self.restore_deadline_seconds
        ):
            raise ContractValidationError(
                "timeout_order_requires_kubectl_lt_wrapper_lt_restore_deadline"
            )
        if self.residual_repoll_seconds != 120:
            raise ContractValidationError("residual_repoll_must_equal_120_seconds")
        if self.stream_drain_seconds >= self.wrapper_timeout_seconds:
            raise ContractValidationError("stream_drain_must_be_less_than_wrapper")
        return self

    def to_dict(self) -> dict[str, float]:
        self.validate()
        return {name: float(getattr(self, name)) for name in self.FIELD_NAMES}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TimeoutContract":
        if not isinstance(value, Mapping):
            raise ContractValidationError("timeout_contract_object_required")
        missing = [name for name in cls.FIELD_NAMES if name not in value]
        extra = sorted(set(value) - set(cls.FIELD_NAMES))
        if missing:
            raise ContractValidationError(f"timeout_contract_missing:{','.join(missing)}")
        if extra:
            raise ContractValidationError(f"timeout_contract_extra:{','.join(extra)}")
        if any(isinstance(value[name], bool) for name in cls.FIELD_NAMES):
            raise ContractValidationError("timeout_contract_boolean_value_forbidden")
        try:
            contract = cls(**{name: float(value[name]) for name in cls.FIELD_NAMES})
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("timeout_contract_numeric_values_required") from exc
        return contract.validate()


def _timeout_mapping(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    direct = manifest.get("timeout_contract")
    if isinstance(direct, Mapping):
        return direct
    runtime = manifest.get("runtime")
    if isinstance(runtime, Mapping) and isinstance(runtime.get("timeout_contract"), Mapping):
        return runtime["timeout_contract"]
    raise ContractValidationError("manifest_timeout_contract_missing")


def validate_manifest_runtime_contract(
    manifest: Mapping[str, Any], contract: TimeoutContract
) -> dict[str, float]:
    """Reject staging if a manifest promise differs from executable settings."""

    contract.validate()
    promised = TimeoutContract.from_mapping(_timeout_mapping(manifest))
    actual = contract.to_dict()
    if promised.to_dict() != actual:
        differences = [
            name
            for name in TimeoutContract.FIELD_NAMES
            if promised.to_dict()[name] != actual[name]
        ]
        raise ContractValidationError(f"manifest_runtime_timeout_mismatch:{','.join(differences)}")

    # A launcher may redundantly pin a second copy.  If present it must also be
    # byte-for-value identical; it can never silently override the main copy.
    runtime = manifest.get("runtime")
    if isinstance(runtime, Mapping) and "timeout_contract" in runtime:
        runtime_value = TimeoutContract.from_mapping(runtime["timeout_contract"])
        if runtime_value.to_dict() != actual:
            raise ContractValidationError("manifest_nested_runtime_timeout_mismatch")
    return actual


@dataclass
class RestoreDeadline:
    """A monotonic, injectable restore budget shared by every state stage."""

    total_seconds: float
    clock: Callable[[], float] = time.monotonic
    started_monotonic: float | None = None

    def __post_init__(self) -> None:
        _finite_positive("restore_deadline_seconds", self.total_seconds)
        if self.started_monotonic is None:
            self.started_monotonic = float(self.clock())

    @property
    def expires_monotonic(self) -> float:
        assert self.started_monotonic is not None
        return self.started_monotonic + float(self.total_seconds)

    @property
    def elapsed_seconds(self) -> float:
        assert self.started_monotonic is not None
        return max(0.0, float(self.clock()) - self.started_monotonic)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.expires_monotonic - float(self.clock()))

    def can_launch(self, required_seconds: float) -> bool:
        _finite_positive("probe_required_seconds", required_seconds)
        return self.remaining_seconds >= float(required_seconds)

    def assert_can_launch(self, required_seconds: float) -> None:
        if not self.can_launch(required_seconds):
            raise PhaseB2R4Error(
                "restore_budget_prevents_new_probe:"
                f"remaining={self.remaining_seconds:.6f}:required={float(required_seconds):.6f}"
            )


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    ppid: int | None
    creation_time: float | None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResidualObservation:
    elapsed_seconds: float
    identities: tuple[ProcessIdentity, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "elapsed_seconds": self.elapsed_seconds,
            "identities": [item.to_dict() for item in self.identities],
        }


@dataclass(frozen=True)
class ResidualClassification:
    residual_pids: tuple[int, ...]
    naturally_exited: bool
    streams_drained: bool
    manual_intervention_required: bool
    observed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _identity_from_value(value: Any) -> ProcessIdentity:
    if isinstance(value, ProcessIdentity):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return ProcessIdentity(pid=value, ppid=None, creation_time=None)
    if isinstance(value, Mapping):
        return ProcessIdentity(
            pid=int(value["pid"]),
            ppid=None if value.get("ppid") is None else int(value["ppid"]),
            creation_time=(
                None if value.get("creation_time") is None else float(value["creation_time"])
            ),
            name=None if value.get("name") is None else str(value["name"]),
        )
    raise TypeError(f"unsupported_process_identity:{type(value).__name__}")


def classify_residual_process(
    initial_pid: int,
    observations: Sequence[Mapping[str, Any] | ResidualObservation],
    streams_drained: bool,
    contract: TimeoutContract,
) -> ResidualClassification:
    """Pure classifier used by tests and by the process runner's final gate."""

    contract.validate()
    normalized: list[ResidualObservation] = []
    for raw in observations:
        if isinstance(raw, ResidualObservation):
            normalized.append(raw)
            continue
        elapsed = float(raw.get("elapsed_seconds", raw.get("elapsed", 0.0)))
        identities_raw = raw.get("identities", raw.get("processes", raw.get("pids", ())))
        normalized.append(
            ResidualObservation(
                elapsed_seconds=elapsed,
                identities=tuple(_identity_from_value(item) for item in identities_raw),
            )
        )
    normalized.sort(key=lambda item: item.elapsed_seconds)
    last = normalized[-1] if normalized else ResidualObservation(0.0, ())
    residual = tuple(sorted({item.pid for item in last.identities}))
    exhausted = bool(residual) and last.elapsed_seconds >= contract.residual_repoll_seconds
    manual = exhausted or not streams_drained
    naturally_exited = not residual and any(
        initial_pid in {item.pid for item in observation.identities}
        for observation in normalized[:-1]
    )
    return ResidualClassification(
        residual_pids=residual,
        naturally_exited=naturally_exited,
        streams_drained=bool(streams_drained),
        manual_intervention_required=manual,
        observed_seconds=last.elapsed_seconds,
    )


def classify_residual_observations(
    *,
    pid: int,
    ppid: int | None,
    creation_time: float | None,
    descendants: list[dict[str, Any]],
    timed_out: bool,
    observations: list[dict[str, Any]],
    streams_drained: bool,
    contract: TimeoutContract,
) -> "ProcessOutcome":
    """Build a deterministic outcome from simulated residual observations.

    ``alive_pids`` is intentionally the only required observation payload.  The
    helper lets adversarial timeout tests cover the full 120-second contract
    without waiting on wall-clock time or spawning a process.
    """

    contract.validate()
    normalized: list[dict[str, Any]] = []
    for raw in observations:
        elapsed = float(raw["elapsed_seconds"])
        alive_pids = sorted({int(value) for value in raw.get("alive_pids", ())})
        normalized.append({"elapsed_seconds": elapsed, "alive_pids": alive_pids})
    normalized.sort(key=lambda item: item["elapsed_seconds"])
    final = normalized[-1] if normalized else {"elapsed_seconds": 0.0, "alive_pids": []}
    residual_pids = tuple(final["alive_pids"])
    residual_window_exhausted = (
        bool(residual_pids)
        and final["elapsed_seconds"] >= contract.residual_repoll_seconds
    )
    natural_exit = bool(timed_out and not residual_pids)
    manual = residual_window_exhausted or not streams_drained
    return ProcessOutcome(
        name="residual-classification",
        command=(),
        pid=int(pid),
        ppid=ppid,
        creation_time=creation_time,
        descendants=tuple(dict(item) for item in descendants),
        started_at="",
        ended_at="",
        duration_seconds=max(0.0, float(final["elapsed_seconds"])),
        return_code=None,
        timed_out=bool(timed_out),
        residual_pids=residual_pids,
        residual_observations=tuple(dict(item) for item in normalized),
        stdout="",
        stderr="",
        stdout_drained=bool(streams_drained),
        stderr_drained=bool(streams_drained),
        streams_drained=bool(streams_drained),
        natural_exit_after_timeout=natural_exit,
        manual_intervention_required=manual,
        wrapper_timeout_seconds=contract.wrapper_timeout_seconds,
        residual_repoll_seconds=contract.residual_repoll_seconds,
        forced_termination_attempts=0,
    )


@dataclass
class ProcessOutcome:
    name: str
    command: tuple[str, ...]
    pid: int
    ppid: int | None
    creation_time: float | None
    descendants: tuple[dict[str, Any], ...]
    started_at: str
    ended_at: str
    duration_seconds: float
    return_code: int | None
    timed_out: bool
    residual_pids: tuple[int, ...]
    residual_observations: tuple[dict[str, Any], ...]
    stdout: str
    stderr: str
    stdout_drained: bool
    stderr_drained: bool
    streams_drained: bool
    natural_exit_after_timeout: bool
    manual_intervention_required: bool
    wrapper_timeout_seconds: float
    residual_repoll_seconds: float
    forced_termination_attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["command"] = list(self.command)
        value["residual_pids"] = list(self.residual_pids)
        return value


class _PsutilInspector:
    def identity(self, pid: int) -> ProcessIdentity | None:
        try:
            process = psutil.Process(pid)
            return ProcessIdentity(
                pid=process.pid,
                ppid=process.ppid(),
                creation_time=process.create_time(),
                name=process.name(),
            )
        except psutil.AccessDenied as exc:
            raise PhaseB2R4Error(f"process_identity_access_denied:{pid}") from exc
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return None

    def descendants(self, pid: int) -> list[ProcessIdentity]:
        try:
            children = psutil.Process(pid).children(recursive=True)
        except psutil.AccessDenied as exc:
            raise PhaseB2R4Error(f"process_tree_access_denied:{pid}") from exc
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return []
        values: list[ProcessIdentity] = []
        for child in children:
            try:
                values.append(
                    ProcessIdentity(
                        pid=child.pid,
                        ppid=child.ppid(),
                        creation_time=child.create_time(),
                        name=child.name(),
                    )
                )
            except psutil.AccessDenied as exc:
                raise PhaseB2R4Error(
                    f"process_descendant_identity_access_denied:{child.pid}"
                ) from exc
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
        return values

    def same_process_alive(self, identity: ProcessIdentity) -> bool:
        current = self.identity(identity.pid)
        if current is None:
            return False
        if identity.creation_time is None or current.creation_time is None:
            return True
        return abs(identity.creation_time - current.creation_time) < 0.001


class BoundedProcessRunner:
    """Run one child without forced termination and audit its whole process tree."""

    def __init__(
        self,
        contract: TimeoutContract | None = None,
        *,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        process_inspector: Any | None = None,
        stream_joiner: Callable[[Sequence[threading.Thread], float], bool] | None = None,
        utc_clock: Callable[[], str] = utc_now,
    ) -> None:
        self.contract = (contract or TimeoutContract()).validate()
        self.popen_factory = popen_factory
        self.clock = clock
        self.sleep = sleep
        self.process_inspector = process_inspector or _PsutilInspector()
        self.stream_joiner = stream_joiner
        self.utc_clock = utc_clock

    def _identity(self, pid: int) -> ProcessIdentity | None:
        inspector = self.process_inspector
        if hasattr(inspector, "identity"):
            return inspector.identity(pid)
        return None

    def _descendants(self, pid: int) -> list[ProcessIdentity]:
        inspector = self.process_inspector
        if hasattr(inspector, "descendants"):
            return list(inspector.descendants(pid))
        return []

    def _same_alive(self, identity: ProcessIdentity) -> bool:
        inspector = self.process_inspector
        if hasattr(inspector, "same_process_alive"):
            return bool(inspector.same_process_alive(identity))
        return False

    def _observe(
        self,
        root: ProcessIdentity,
        known: MutableMapping[tuple[int, float | None], ProcessIdentity],
        observer: Callable[[ProcessIdentity, tuple[ProcessIdentity, ...]], Iterable[Any]] | None,
    ) -> tuple[ProcessIdentity, ...]:
        for child in self._descendants(root.pid):
            known[(child.pid, child.creation_time)] = child
        if observer is not None:
            raw = observer(root, tuple(known.values()))
            live = tuple(_identity_from_value(item) for item in raw)
            for item in live:
                known[(item.pid, item.creation_time)] = item
            return tuple(sorted(live, key=lambda item: item.pid))
        live = [item for item in known.values() if self._same_alive(item)]
        return tuple(sorted(live, key=lambda item: item.pid))

    @staticmethod
    def _drain(stream: Any, chunks: list[str], errors: list[str]) -> None:
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    return
                chunks.append(str(chunk))
        except Exception as exc:  # the outcome must record incomplete pipe evidence
            errors.append(f"{type(exc).__name__}:{exc}")

    def _join_streams(self, threads: Sequence[threading.Thread], timeout: float) -> bool:
        if self.stream_joiner is not None:
            return bool(self.stream_joiner(threads, timeout))
        deadline = time.monotonic() + timeout
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        return all(not thread.is_alive() for thread in threads)

    def run(
        self,
        command: Sequence[str],
        *,
        name: str = "bounded-child",
        wrapper_timeout: float | None = None,
        residual_repoll: float | None = None,
        poll_interval: float = 0.25,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        residual_observer: (
            Callable[[ProcessIdentity, tuple[ProcessIdentity, ...]], Iterable[Any]] | None
        ) = None,
    ) -> ProcessOutcome:
        if not command or any(not isinstance(item, str) or not item for item in command):
            raise ValueError("non_empty_string_command_required")
        _finite_positive("poll_interval", poll_interval)
        wrapper = (
            self.contract.wrapper_timeout_seconds
            if wrapper_timeout is None
            else float(wrapper_timeout)
        )
        residual = (
            self.contract.residual_repoll_seconds
            if residual_repoll is None
            else float(residual_repoll)
        )
        if wrapper != self.contract.wrapper_timeout_seconds:
            raise ContractValidationError("runtime_wrapper_timeout_differs_from_contract")
        if residual != self.contract.residual_repoll_seconds:
            raise ContractValidationError("runtime_residual_repoll_differs_from_contract")

        started_at = self.utc_clock()
        started = float(self.clock())
        child = self.popen_factory(
            list(command),
            cwd=None if cwd is None else os.fspath(cwd),
            env=None if env is None else dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
        )
        fallback = ProcessIdentity(pid=int(child.pid), ppid=os.getpid(), creation_time=None)
        root = self._identity(int(child.pid)) or fallback
        known: dict[tuple[int, float | None], ProcessIdentity] = {
            (root.pid, root.creation_time): root
        }
        initial_descendants = self._descendants(root.pid)
        for item in initial_descendants:
            known[(item.pid, item.creation_time)] = item

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        stdout_errors: list[str] = []
        stderr_errors: list[str] = []
        stdout_thread = threading.Thread(
            target=self._drain,
            args=(child.stdout, stdout_chunks, stdout_errors),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._drain,
            args=(child.stderr, stderr_chunks, stderr_errors),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        wrapper_deadline = started + wrapper
        while child.poll() is None and float(self.clock()) < wrapper_deadline:
            self._observe(root, known, residual_observer)
            self.sleep(min(float(poll_interval), max(0.0, wrapper_deadline - float(self.clock()))))
        timed_out = child.poll() is None

        observations: list[ResidualObservation] = []
        if timed_out:
            residual_started = float(self.clock())
            residual_deadline = residual_started + residual
            while True:
                live = self._observe(root, known, residual_observer)
                elapsed = min(residual, max(0.0, float(self.clock()) - residual_started))
                observations.append(ResidualObservation(elapsed, live))
                if not live or float(self.clock()) >= residual_deadline:
                    break
                self.sleep(
                    min(float(poll_interval), max(0.0, residual_deadline - float(self.clock())))
                )
        else:
            live = self._observe(root, known, residual_observer)
            observations.append(ResidualObservation(0.0, live))

        joined = self._join_streams(
            (stdout_thread, stderr_thread), self.contract.stream_drain_seconds
        )
        stdout_drained = not stdout_thread.is_alive() and not stdout_errors
        stderr_drained = not stderr_thread.is_alive() and not stderr_errors
        streams_drained = joined and stdout_drained and stderr_drained
        last_live = observations[-1].identities if observations else ()
        residual_pids = tuple(sorted({item.pid for item in last_live}))
        natural_exit = timed_out and not residual_pids and child.poll() is not None
        manual = bool(residual_pids) or not streams_drained
        ended = float(self.clock())
        return ProcessOutcome(
            name=name,
            command=tuple(command),
            pid=root.pid,
            ppid=root.ppid,
            creation_time=root.creation_time,
            descendants=tuple(item.to_dict() for item in known.values() if item.pid != root.pid),
            started_at=started_at,
            ended_at=self.utc_clock(),
            duration_seconds=max(0.0, ended - started),
            return_code=child.poll(),
            timed_out=timed_out,
            residual_pids=residual_pids,
            residual_observations=tuple(item.to_dict() for item in observations),
            stdout="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
            stdout_drained=stdout_drained,
            stderr_drained=stderr_drained,
            streams_drained=streams_drained,
            natural_exit_after_timeout=natural_exit,
            manual_intervention_required=manual,
            wrapper_timeout_seconds=wrapper,
            residual_repoll_seconds=residual,
            forced_termination_attempts=0,
        )


DISRUPTIVE_CALL_NAMES = (
    "docker_off_probe",
    "compose_stop",
    "desktop_stop",
    "wsl_shutdown",
    "desktop_start",
    "compose_start",
)


def _count_from_aliases(value: Mapping[str, Any], aliases: Sequence[str]) -> int:
    for alias in aliases:
        if alias in value:
            count = int(value[alias])
            if count < 0:
                raise ValueError(f"negative_call_count:{alias}")
            return count
    return 0


@dataclass(frozen=True)
class RestoreCheckpoint:
    source: str
    historical_call_counts: Mapping[str, int]
    previous_attempt_failed: bool = True

    @classmethod
    def from_r3_call_counts(cls, value: Mapping[str, Any]) -> "RestoreCheckpoint":
        if not isinstance(value, Mapping):
            raise TypeError("r3_call_counts_mapping_required")
        aliases = {
            "docker_off_probe": ("docker_off_probe", "probe", "probe_count"),
            "compose_stop": ("compose_stop", "compose_stop_count"),
            "desktop_stop": ("desktop_stop", "docker_desktop_stop"),
            "wsl_shutdown": ("wsl_shutdown", "wsl_shutdown_count"),
            "desktop_start": ("desktop_start", "docker_desktop_start"),
            "compose_start": ("compose_start", "compose_start_count"),
        }
        counts = {name: _count_from_aliases(value, names) for name, names in aliases.items()}
        unexpected = {name: count for name, count in counts.items() if count != 1}
        if unexpected:
            raise ValueError(f"r3_disruptive_call_counts_not_exact_once:{unexpected}")
        return cls(source="r3_failure_checkpoint", historical_call_counts=counts)

    def permits(self, operation: str) -> bool:
        # Restore-only is intentionally reconciliation-only.  Historical values
        # are evidence, never a reason to repeat a disruptive operation.
        return operation not in DISRUPTIVE_CALL_NAMES

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "previous_attempt_failed": self.previous_attempt_failed,
            "historical_call_counts": dict(self.historical_call_counts),
            "restore_only_blocked_calls": list(DISRUPTIVE_CALL_NAMES),
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
        passed = bool(raw.get("passed", raw.get("ok", False)))
        residual = tuple(sorted({int(pid) for pid in raw.get("residual_pids", ())}))
        invariants_raw = raw.get("invariants", {})
        if not isinstance(invariants_raw, Mapping):
            raise TypeError("probe_invariants_mapping_required")
        invariants = {str(name): bool(value) for name, value in invariants_raw.items()}
        if any(not value for value in invariants.values()):
            passed = False
        error = raw.get("last_error", raw.get("error"))
        retryable = bool(raw.get("retryable", False))
        if error and "eof" in str(error).lower():
            retryable = True
        reserved = {
            "passed",
            "ok",
            "retryable",
            "last_error",
            "error",
            "residual_pids",
            "manual_intervention_required",
            "invariants",
        }
        details = {str(key): value for key, value in raw.items() if key not in reserved}
        return cls(
            passed=passed,
            retryable=retryable,
            last_error=None if error is None else str(error),
            details=details,
            residual_pids=residual,
            manual_intervention_required=bool(raw.get("manual_intervention_required", False)),
            invariants=invariants,
        )


@dataclass
class RestoreStageEvidence:
    stage: str
    started_at: str
    ended_at: str
    duration_seconds: float
    restore_deadline_monotonic: float
    remaining_at_start_seconds: float
    remaining_at_end_seconds: float
    probe_launches: int
    passed: bool
    last_error: str | None
    attempts: list[dict[str, Any]]
    details: Mapping[str, Any]
    residual_pids: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["residual_pids"] = list(self.residual_pids)
        return value


@dataclass
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
    stages: list[RestoreStageEvidence]
    call_counts: Mapping[str, int]
    residual_pids: tuple[int, ...]
    checkpoint: Mapping[str, Any]
    success_invariants: Mapping[str, bool]
    required_invariants: tuple[str, ...] = ()
    decision: str | None = None

    @property
    def overall_pass(self) -> bool:
        return self.passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "s8-v4-x1-phase-b2-r4-restore-report/v1",
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
            "stages": [stage.to_dict() for stage in self.stages],
            "call_counts": dict(self.call_counts),
            "residual_pids": list(self.residual_pids),
            "checkpoint": dict(self.checkpoint),
            "success_invariants": dict(self.success_invariants),
            "required_invariants": list(self.required_invariants),
            "decision": self.decision,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


def validate_release_readiness(
    ready_status: int, payload: Mapping[str, Any], expected_revision: str
) -> dict[str, bool]:
    """Validate both readiness and the release identity returned by the API."""

    if not expected_revision or len(expected_revision) != 40:
        raise ValueError("full_expected_revision_required")
    invariants = {
        "api_ready_200": int(ready_status) == 200,
        "api_revision_exact": str(payload.get("runtime_source_commit", ""))
        == expected_revision,
        "api_runtime_revision_matches": payload.get("runtime_revision_matches") is True,
    }
    return invariants


class RestoreHarness:
    """Checkpointed, read-only restore reconciliation state machine."""

    def __init__(
        self,
        *,
        contract: TimeoutContract | None = None,
        probes: Mapping[str | RestoreStage, Callable[[RestoreDeadline], Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        utc_clock: Callable[[], str] = utc_now,
        retry_interval_seconds: float = 1.0,
        expected_revision: str | None = None,
        process_runner: BoundedProcessRunner | None = None,
        required_invariants: Sequence[str] = (),
        max_probe_attempts: int = 3,
    ) -> None:
        self.contract = (contract or TimeoutContract()).validate()
        self.probes = {
            (key.value if isinstance(key, RestoreStage) else str(key)): value
            for key, value in (probes or {}).items()
        }
        self.clock = clock
        self.sleep = sleep
        self.utc_clock = utc_clock
        _finite_positive("retry_interval_seconds", retry_interval_seconds)
        self.retry_interval_seconds = float(retry_interval_seconds)
        self.expected_revision = expected_revision
        self.process_runner = process_runner
        self.required_invariants = tuple(str(item) for item in required_invariants)
        if isinstance(max_probe_attempts, bool) or int(max_probe_attempts) < 1:
            raise ValueError("max_probe_attempts_must_be_positive")
        self.max_probe_attempts = int(max_probe_attempts)

    @property
    def probe_launch_budget_seconds(self) -> float:
        """Worst-case budget reserved before a new child probe is launched."""

        return (
            self.contract.wrapper_timeout_seconds
            + self.contract.residual_repoll_seconds
            + self.contract.stream_drain_seconds
        )

    def _run_stage(
        self, stage: RestoreStage, deadline: RestoreDeadline
    ) -> tuple[RestoreStageEvidence, ProbeResult]:
        started_at = self.utc_clock()
        started = float(self.clock())
        remaining_start = deadline.remaining_seconds
        attempts: list[dict[str, Any]] = []
        latest = ProbeResult(False, last_error="probe_not_launched")
        launches = 0
        probe = self.probes.get(stage.value)
        if probe is None:
            latest = ProbeResult(False, last_error=f"probe_missing:{stage.value}")
        else:
            while True:
                remaining_before = deadline.remaining_seconds
                if not deadline.can_launch(self.probe_launch_budget_seconds):
                    latest = ProbeResult(
                        False,
                        last_error="restore_budget_prevents_new_probe",
                        manual_intervention_required=True,
                    )
                    break
                attempt_started = float(self.clock())
                attempt_started_at = self.utc_clock()
                launches += 1
                try:
                    latest = ProbeResult.normalize(probe(deadline))
                except Exception as exc:  # fail-closed evidence, not control flow leakage
                    message = f"{type(exc).__name__}:{exc}"
                    latest = ProbeResult(
                        False,
                        retryable="eof" in message.lower(),
                        last_error=message,
                    )
                attempts.append(
                    {
                        "attempt": launches,
                        "started_at": attempt_started_at,
                        "ended_at": self.utc_clock(),
                        "duration_seconds": max(0.0, float(self.clock()) - attempt_started),
                        "remaining_before_seconds": remaining_before,
                        "remaining_after_seconds": deadline.remaining_seconds,
                        "passed": latest.passed,
                        "retryable": latest.retryable,
                        "last_error": latest.last_error,
                        "details": dict(latest.details),
                        "residual_pids": list(latest.residual_pids),
                        "invariants": dict(latest.invariants),
                    }
                )
                if latest.passed or latest.manual_intervention_required or latest.residual_pids:
                    break
                if not latest.retryable:
                    break
                if launches >= self.max_probe_attempts:
                    latest = ProbeResult(
                        False,
                        last_error=f"probe_retry_limit_exhausted:{latest.last_error or stage.value}",
                        manual_intervention_required=True,
                    )
                    break
                remaining_after = deadline.remaining_seconds
                required = self.probe_launch_budget_seconds + self.retry_interval_seconds
                if remaining_after < required:
                    latest = ProbeResult(
                        False,
                        last_error="restore_budget_prevents_retry_probe",
                        manual_intervention_required=True,
                    )
                    break
                self.sleep(min(self.retry_interval_seconds, remaining_after))

        ended = float(self.clock())
        evidence = RestoreStageEvidence(
            stage=stage.value,
            started_at=started_at,
            ended_at=self.utc_clock(),
            duration_seconds=max(0.0, ended - started),
            restore_deadline_monotonic=deadline.expires_monotonic,
            remaining_at_start_seconds=remaining_start,
            remaining_at_end_seconds=deadline.remaining_seconds,
            probe_launches=launches,
            passed=latest.passed,
            last_error=latest.last_error,
            attempts=attempts,
            details=dict(latest.details),
            residual_pids=latest.residual_pids,
        )
        return evidence, latest

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
        call_counts = {name: 0 for name in DISRUPTIVE_CALL_NAMES}
        stages: list[RestoreStageEvidence] = []
        invariants: dict[str, bool] = {}
        residual_pids: set[int] = set()
        manual = False
        last_error: str | None = None

        for stage in RESTORE_STAGE_ORDER:
            evidence, result = self._run_stage(stage, deadline)
            stages.append(evidence)
            invariants[stage.value] = result.passed
            invariants.update(result.invariants)
            residual_pids.update(result.residual_pids)
            if result.residual_pids or result.manual_intervention_required:
                manual = True
            if not result.passed:
                manual = True
                last_error = result.last_error or f"restore_stage_failed:{stage.value}"
                break

        required_ok = all(invariants.get(name) is True for name in self.required_invariants)
        all_stages = len(stages) == len(RESTORE_STAGE_ORDER) and all(
            stage.passed for stage in stages
        )
        deadline_exceeded = deadline.remaining_seconds <= 0 or (
            last_error is not None and "budget" in last_error
        )
        if deadline_exceeded or residual_pids:
            manual = True
        passed = all_stages and required_ok and not manual and not residual_pids
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
            manual_intervention_required=manual,
            deadline_exceeded=deadline_exceeded,
            last_error=last_error,
            stages=stages,
            call_counts=call_counts,
            residual_pids=tuple(sorted(residual_pids)),
            checkpoint=checkpoint.to_dict(),
            success_invariants=invariants,
            required_invariants=self.required_invariants,
            decision="restore_only_pass" if passed else "manual_intervention_required",
        )


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
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_sha256(value: str, label: str) -> str:
    normalized = str(value).lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ContractValidationError(f"{label}_sha256_invalid")
    return normalized


def validate_sha_chain(
    *,
    outer_path: Path,
    expected_outer_sha256: str,
    bridge_path: Path,
    expected_bridge_sha256: str,
    manifest_path: Path,
    expected_manifest_sha256: str,
) -> dict[str, str]:
    """Verify the outer, bridge, and manifest pins immediately before launch."""

    pins = {
        "outer": (Path(outer_path), _validated_sha256(expected_outer_sha256, "outer")),
        "bridge": (Path(bridge_path), _validated_sha256(expected_bridge_sha256, "bridge")),
        "manifest": (
            Path(manifest_path),
            _validated_sha256(expected_manifest_sha256, "manifest"),
        ),
    }
    measured: dict[str, str] = {}
    for name, (path, expected) in pins.items():
        if not path.is_file():
            raise ContractValidationError(f"sha_chain_file_missing:{name}:{path}")
        actual = sha256_file(path)
        if actual != expected:
            raise ContractValidationError(f"sha_chain_mismatch:{name}:{expected}:{actual}")
        measured[name] = actual
    return measured


def _create_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise EvidenceExistsError(f"evidence_path_exists:{path}") from exc
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _new_evidence_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise EvidenceExistsError(f"evidence_directory_exists:{path}") from exc


def create_failure_evidence(
    output_directory: Path,
    report: RestoreReport | Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a failure-only seal/index; never create a success-named file."""

    _new_evidence_directory(output_directory)
    report_value = report.to_dict() if isinstance(report, RestoreReport) else dict(report)
    seal = {
        "schema": "s8-v4-x1-phase-b2-r4-failure-seal/v1",
        "sealed_at": utc_now(),
        "failure_only": True,
        "acceptance_credit": False,
        "is_success_index": False,
        "success_marker_created": False,
        "report": report_value,
        "metadata": dict(metadata or {}),
    }
    seal_path = output_directory / "failure-seal.json"
    _create_new(seal_path, canonical_json_bytes(seal))
    index = {
        "schema": "s8-v4-x1-phase-b2-r4-failure-evidence-index/v1",
        "created_at": utc_now(),
        "failure_only": True,
        "acceptance_credit": False,
        "is_success_index": False,
        "success_marker_created": False,
        "files": [
            {
                "path": seal_path.name,
                "bytes": seal_path.stat().st_size,
                "sha256": sha256_file(seal_path),
            }
        ],
    }
    index_path = output_directory / "failure-evidence-index.json"
    _create_new(index_path, canonical_json_bytes(index))
    return {
        "directory": str(output_directory),
        "failure_seal": str(seal_path),
        "failure_seal_sha256": sha256_file(seal_path),
        "failure_index": str(index_path),
        "failure_index_sha256": sha256_file(index_path),
    }


def create_restore_only_evidence(
    output_directory: Path,
    report: RestoreReport,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Seal a passing restore-only reconciliation without a completion marker."""

    if report.mode != "restore-only" or not report.passed:
        raise SuccessInvariantError("restore_only_evidence_requires_passing_restore_report")
    _new_evidence_directory(output_directory)
    report_path = output_directory / "restore-only-report.json"
    _create_new(report_path, canonical_json_bytes(report.to_dict()))
    index = {
        "schema": "s8-v4-x1-phase-b2-r4-restore-only-index/v1",
        "created_at": utc_now(),
        "acceptance_credit": False,
        "is_phase_b2_success_index": False,
        "completion_marker_created": False,
        "metadata": dict(metadata or {}),
        "files": [
            {
                "path": report_path.name,
                "bytes": report_path.stat().st_size,
                "sha256": sha256_file(report_path),
            }
        ],
    }
    index_path = output_directory / "restore-only-index.json"
    _create_new(index_path, canonical_json_bytes(index))
    return {
        "directory": str(output_directory),
        "restore_only_report": str(report_path),
        "restore_only_report_sha256": sha256_file(report_path),
        "restore_only_index": str(index_path),
        "restore_only_index_sha256": sha256_file(index_path),
    }


def _success_ready(report: RestoreReport) -> bool:
    if report.mode != "phase-b2" or report.decision != "phase_b2_pass":
        return False
    if not report.passed or report.manual_intervention_required or report.residual_pids:
        return False
    if len(report.stages) != len(RESTORE_STAGE_ORDER) or any(
        not stage.passed for stage in report.stages
    ):
        return False
    if any(report.call_counts.get(name, -1) != 1 for name in DISRUPTIVE_CALL_NAMES):
        return False
    return all(report.success_invariants.get(name) is True for name in report.required_invariants)


def create_success_evidence(
    output_directory: Path,
    report: RestoreReport,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the success index/marker only after every report invariant passes."""

    if not _success_ready(report):
        raise SuccessInvariantError("success_evidence_requires_all_invariants")
    _new_evidence_directory(output_directory)
    report_path = output_directory / "restore-report.json"
    _create_new(report_path, canonical_json_bytes(report.to_dict()))
    index = {
        "schema": "s8-v4-x1-phase-b2-r4-private-evidence-index/v1",
        "created_at": utc_now(),
        "acceptance_credit": True,
        "is_success_index": True,
        "all_invariants_passed": True,
        "metadata": dict(metadata or {}),
        "files": [
            {
                "path": report_path.name,
                "bytes": report_path.stat().st_size,
                "sha256": sha256_file(report_path),
            }
        ],
    }
    index_path = output_directory / "private-evidence-index.json"
    _create_new(index_path, canonical_json_bytes(index))
    marker = {
        "schema": "s8-v4-x1-phase-b2-r4-completion-marker/v1",
        "created_at": utc_now(),
        "phase_b2_pass": True,
        "all_invariants_passed": True,
        "private_evidence_index_sha256": sha256_file(index_path),
    }
    marker_path = output_directory / "completion-marker.json"
    _create_new(marker_path, canonical_json_bytes(marker))
    return {
        "directory": str(output_directory),
        "private_index": str(index_path),
        "private_index_sha256": sha256_file(index_path),
        "completion_marker": str(marker_path),
        "completion_marker_sha256": sha256_file(marker_path),
    }
