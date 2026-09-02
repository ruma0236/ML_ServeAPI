"""Local r7s4 Windows bootstrap admission for a future versioned runner.

This module is deliberately not wired into any r3-r7s3 bundle or production
entry point.  It provides one bounded building block: an isolated, pinned
Python bootstrap consumes a query-only Job capability, creates the requested
payload suspended, proves that payload membership through the retained
capability, and waits for a parent approval byte before resuming the payload.

No process-wide termination primitive or Job close limit is used.  If an
admission fails after payload creation, the payload remains suspended and the
parent outcome is a manual-intervention latch with residual PID evidence.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
import types
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Protocol, Sequence


def _load_r7s3_process() -> types.ModuleType:
    """Load the predecessor under isolated ``python -I -S`` execution."""

    try:
        from evm.scale_validation import phase_b2_r7s3_process

        return phase_b2_r7s3_process
    except ModuleNotFoundError:
        path = Path(__file__).with_name("phase_b2_r7s3_process.py")
        name = "_evm_phase_b2_r7s3_process_for_r7s4"
        existing = sys.modules.get(name)
        if existing is not None:
            return existing
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None:
            raise RuntimeError("r7s3_process_import_spec_unavailable")
        module = types.ModuleType(name)
        module.__file__ = str(path)
        module.__package__ = ""
        module.__spec__ = spec
        sys.modules[name] = module
        try:
            # Compile the pinned source explicitly.  SourceFileLoader may read a
            # pre-existing pyc even under -B; that would expand the bootstrap
            # TCB beyond the parent-held source lease.
            code = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
            exec(code, module.__dict__)
        except Exception:
            sys.modules.pop(name, None)
            raise
        return module


r7s3 = _load_r7s3_process()

ACK_HANDLE_ENV = "EVM_PHASE_B2_R7S4_ACK_HANDLE"
CONTROL_HANDLE_ENV = "EVM_PHASE_B2_R7S4_CONTROL_HANDLE"
ADMISSION_ID_ENV = "EVM_PHASE_B2_R7S4_ADMISSION_ID"
PAYLOAD_ENVELOPE_ENV = "EVM_PHASE_B2_R7S4_PAYLOAD_ENVELOPE"
COMMAND_DIGEST_ENV = "EVM_PHASE_B2_R7S4_COMMAND_SHA256"
BOOTSTRAP_SHA256_ENV = "EVM_PHASE_B2_R7S4_BOOTSTRAP_SHA256"
BOOTSTRAP_SOURCE_IDENTITY_ENV = "EVM_PHASE_B2_R7S4_BOOTSTRAP_SOURCE_IDENTITY"
BOOTSTRAP_R7S3_IDENTITY_ENV = "EVM_PHASE_B2_R7S4_BOOTSTRAP_R7S3_IDENTITY"
ACK_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.bootstrap-ack.v1"
OUTCOME_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.process-outcome.v1"
APPROVAL_BYTE = b"A"
MAX_ACK_BYTES = 256 * 1024
DEFAULT_STREAM_CAPTURE_BYTES = 1024 * 1024
HEX64_RE = re.compile(r"[0-9a-f]{64}")

_CAPABILITY_NAMES = {
    r7s3.JOB_CAPABILITY_HANDLE_ENV,
    r7s3.JOB_CAPABILITY_NONCE_ENV,
    r7s3.JOB_CAPABILITY_COMMITMENT_ENV,
}
_BOOTSTRAP_NAMES = {
    ACK_HANDLE_ENV,
    CONTROL_HANDLE_ENV,
    ADMISSION_ID_ENV,
    PAYLOAD_ENVELOPE_ENV,
    COMMAND_DIGEST_ENV,
    BOOTSTRAP_SHA256_ENV,
    BOOTSTRAP_SOURCE_IDENTITY_ENV,
    BOOTSTRAP_R7S3_IDENTITY_ENV,
}
_PRIVATE_ENVIRONMENT_NAMES = _CAPABILITY_NAMES | _BOOTSTRAP_NAMES


class R7S4ProcessError(RuntimeError):
    """Raised when local bootstrap admission fails closed."""


class BootstrapApi(Protocol):
    def clear_handle_inherit(self, handle: int) -> None: ...

    def current_job_snapshot(self, job: int | None) -> Mapping[str, Any]: ...

    def is_process_in_job(self, process: int, job: int | None) -> bool: ...

    def create_payload_suspended(
        self,
        *,
        command: Sequence[str],
        cwd: str | None,
        environment: Mapping[str, str],
        create_no_window: bool,
    ) -> Any: ...

    def write_ack(self, handle: int, payload: bytes) -> None: ...

    def read_approval(self, handle: int) -> bytes: ...

    def resume(self, thread: int) -> None: ...

    def wait_payload(self, process: int) -> int: ...

    def close(self, handle: int | None) -> None: ...

    def open_executable_lease(self, path: str) -> ExecutableLease: ...

    def verify_executable_path_binding(self, identity: Mapping[str, Any]) -> None: ...


class ParentApi(Protocol):
    def create_job_and_completion_port(self) -> tuple[int, int]: ...

    def create_pipe(self) -> tuple[int, int]: ...

    def create_control_pipe(self) -> tuple[int, int]: ...

    def open_inheritable_null(self) -> int: ...

    def create_bootstrap_suspended(
        self,
        *,
        command: Sequence[str],
        cwd: str | None,
        environment: Mapping[str, str],
        job: int,
        stdin_handle: int,
        stdout_handle: int,
        stderr_handle: int,
        ack_handle: int,
        control_handle: int,
        nonce: bytes | bytearray | memoryview,
        run_uuid: str,
        admission_id: str,
        payload_envelope: str,
        command_sha256: str,
        bootstrap_sha256: str,
        bootstrap_source_identity: Mapping[str, Any],
        bootstrap_r7s3_identity: Mapping[str, Any],
        create_no_window: bool,
    ) -> Any: ...

    def is_process_in_job(self, process: int, job: int | None) -> bool: ...

    def member_job_snapshot(self, job: int, process: int) -> Mapping[str, Any]: ...

    def resume(self, thread: int) -> None: ...

    def read_pipe(self, read_handle: int, sink: bytearray, drained: threading.Event) -> None: ...

    def read_bounded_pipe(
        self, read_handle: int, sink: bytearray, drained: threading.Event, maximum: int
    ) -> None: ...

    def read_bounded_discarding_pipe(
        self,
        read_handle: int,
        sink: bytearray,
        drained: threading.Event,
        state: StreamCaptureState,
    ) -> None: ...

    def write_approval(self, handle: int) -> None: ...

    def query_active_pids(self, job: int) -> tuple[int, ...]: ...

    def job_accounting_snapshot(self, job: int) -> Mapping[str, Any]: ...

    def exit_code(self, process: int) -> int | None: ...

    def open_process(self, pid: int) -> int | None: ...

    def process_is_active(self, process: int) -> bool: ...

    def process_identity(self, process: int, **kwargs: Any) -> Any: ...

    def completion_events(self, completion: int) -> list[tuple[int, int | None]]: ...

    def close(self, handle: int | None) -> None: ...

    def open_executable_lease(self, path: str) -> ExecutableLease: ...

    def verify_executable_path_binding(self, identity: Mapping[str, Any]) -> None: ...


@dataclass(frozen=True)
class BootstrapAckExpectation:
    run_uuid: str
    admission_id: str
    nonce_commitment: str
    command_sha256: str
    bootstrap_sha256: str
    bootstrap_source_identity: Mapping[str, Any]
    bootstrap_r7s3_identity: Mapping[str, Any]
    bootstrap_pid: int


@dataclass(frozen=True)
class BootstrapOutcome:
    schema: str
    name: str
    run_uuid: str
    admission_id: str
    command: tuple[str, ...]
    command_sha256: str
    bootstrap_sha256: str
    bootstrap_source_identity: Mapping[str, Any] | None
    bootstrap_r7s3_identity: Mapping[str, Any] | None
    bootstrap_executable_identity: Mapping[str, Any] | None
    payload_executable_identity: Mapping[str, Any] | None
    bootstrap_pid: int | None
    payload_pid: int | None
    ack: Mapping[str, Any] | None
    ack_valid: bool
    payload_resume_authorized: bool
    return_code: int | None
    timed_out: bool
    manual_intervention_required: bool
    residual_pids: tuple[int, ...]
    stdout: str
    stderr: str
    stdout_captured_sha256: str
    stderr_captured_sha256: str
    stdout_bytes_observed: int
    stderr_bytes_observed: int
    stdout_capture_overflow: bool
    stderr_capture_overflow: bool
    stream_capture_limit_bytes: int
    output_redaction_policy: str
    stdout_drained: bool
    stderr_drained: bool
    ack_drained: bool
    stdout_drained_monotonic: float | None
    stderr_drained_monotonic: float | None
    ack_drained_monotonic: float | None
    stream_drain_within_deadline: bool
    ack_overflow: bool
    ack_timeout: bool
    active_process_zero: bool
    active_pid_query_succeeded: bool
    residual_state: str
    completion_accounting_reconciled: bool
    completion_event_sequence_complete: bool
    stable_zero_observations: int
    final_job_accounting: Mapping[str, Any] | None
    observed_process_identity_count: int
    observation_continuity: str
    observation_id: str | None
    process_identities: tuple[Mapping[str, Any], ...]
    process_events: tuple[Mapping[str, Any], ...]
    safe_for_followup: bool
    forced_termination_attempts: int
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SuspendedProcess:
    hProcess: int
    hThread: int
    dwProcessId: int
    dwThreadId: int
    executable_identity: Mapping[str, Any]


@dataclass
class ExecutableLease:
    """Measured executable handle held across the path-based CreateProcess call."""

    handle: int
    identity: dict[str, Any]
    api: Any
    closed: bool = False

    def verify_path_binding(self) -> None:
        if self.closed:
            raise R7S4ProcessError("executable_lease_already_closed")
        self.api.verify_executable_path_binding(self.identity)

    def close(self) -> None:
        if not self.closed:
            self.api.close(self.handle)
            self.closed = True

    def __enter__(self) -> ExecutableLease:
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()


@dataclass
class StreamCaptureState:
    limit_bytes: int
    bytes_observed: int = 0
    overflowed: bool = False
    read_error: str | None = None
    completed_monotonic: float | None = None


class _TimestampedDrainSignal:
    """Publish a drain timestamp before making completion visible to the runner."""

    def __init__(self, event: threading.Event, state: StreamCaptureState, clock: Any) -> None:
        self._event = event
        self._state = state
        self._clock = clock
        self._lock = threading.Lock()

    def set(self) -> None:
        with self._lock:
            if self._state.completed_monotonic is None:
                self._state.completed_monotonic = float(self._clock())
            self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()


def _append_bounded_capture(sink: bytearray, state: StreamCaptureState, raw: bytes) -> None:
    if not isinstance(raw, bytes):
        raise R7S4ProcessError("stream_capture_chunk_must_be_bytes")
    state.bytes_observed += len(raw)
    remaining = max(0, state.limit_bytes - len(sink))
    if remaining:
        sink.extend(raw[:remaining])
    if len(raw) > remaining:
        state.overflowed = True


@dataclass
class ResidualObservationLease:
    observation_id: str
    run_uuid: str
    api: Any
    job: int
    completion: int | None
    root_process: int | None
    bootstrap_source_lease: ExecutableLease | None
    bootstrap_r7s3_lease: ExecutableLease | None
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def observe(self) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                return {
                    "observation_id": self.observation_id,
                    "run_uuid": self.run_uuid,
                    "query_succeeded": False,
                    "active_process_zero": False,
                    "residual_state": "unknown",
                    "residual_pids": [],
                    "error_type": "observation_closed",
                }
            try:
                pids = tuple(sorted(self.api.query_active_pids(self.job)))
            except Exception as exc:
                return {
                    "observation_id": self.observation_id,
                    "run_uuid": self.run_uuid,
                    "query_succeeded": False,
                    "active_process_zero": False,
                    "residual_state": "unknown",
                    "residual_pids": [],
                    "error_type": type(exc).__name__,
                }
            return {
                "observation_id": self.observation_id,
                "run_uuid": self.run_uuid,
                "query_succeeded": True,
                "active_process_zero": not pids,
                "residual_state": "zero" if not pids else "nonzero",
                "residual_pids": list(pids),
                "error_type": None,
            }

    def close_if_zero(self) -> bool:
        with self._lock:
            if self._closed:
                return True
            try:
                pids = tuple(sorted(self.api.query_active_pids(self.job)))
            except Exception:
                return False
            if pids:
                return False
            root_process = self.root_process
            completion = self.completion
            job = self.job
            source_lease = self.bootstrap_source_lease
            r7s3_lease = self.bootstrap_r7s3_lease
            self.root_process = None
            self.completion = None
            self.job = 0
            self.bootstrap_source_lease = None
            self.bootstrap_r7s3_lease = None
            self._closed = True
            self.api.close(root_process)
            self.api.close(completion)
            self.api.close(job)
            if source_lease is not None:
                source_lease.close()
            if r7s3_lease is not None:
                r7s3_lease.close()
            return True


class LocalResidualObservationRegistry:
    """Process-local handle retention for manual, read-only residual observation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._leases: dict[str, ResidualObservationLease] = {}

    def retain(
        self,
        *,
        run_uuid: str,
        api: Any,
        job: int,
        completion: int | None,
        root_process: int | None,
        bootstrap_source_lease: ExecutableLease | None,
        bootstrap_r7s3_lease: ExecutableLease | None,
    ) -> str:
        observation_id = str(uuid.uuid4())
        lease = ResidualObservationLease(
            observation_id=observation_id,
            run_uuid=run_uuid,
            api=api,
            job=job,
            completion=completion,
            root_process=root_process,
            bootstrap_source_lease=bootstrap_source_lease,
            bootstrap_r7s3_lease=bootstrap_r7s3_lease,
        )
        with self._lock:
            self._leases[observation_id] = lease
        return observation_id

    def observe(self, observation_id: str) -> dict[str, Any]:
        with self._lock:
            lease = self._leases.get(observation_id)
        if lease is None:
            raise R7S4ProcessError("residual_observation_id_unknown")
        return lease.observe()

    def release_if_zero(self, observation_id: str) -> bool:
        with self._lock:
            lease = self._leases.get(observation_id)
        if lease is None or not lease.close_if_zero():
            return False
        with self._lock:
            if self._leases.get(observation_id) is lease:
                self._leases.pop(observation_id, None)
        return True


RESIDUAL_OBSERVATIONS = LocalResidualObservationRegistry()


@dataclass
class JobCapabilityLease:
    """A consumed capability retained only through payload admission."""

    handle: int
    run_uuid: str
    nonce_commitment: str
    explicit_job: dict[str, Any]
    null_job_observation: dict[str, Any]
    api: BootstrapApi
    closed: bool = False

    def payload_snapshots(
        self, process: int, payload_pid: int
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.closed:
            raise R7S4ProcessError("job_capability_lease_already_closed")
        if not self.api.is_process_in_job(process, self.handle):
            raise R7S4ProcessError("payload_not_in_explicit_job")
        current_pid = os.getpid()
        explicit = r7s3._validated_job_capability_snapshot(
            self.api.current_job_snapshot(self.handle),
            current_pid=current_pid,
            label="r7s4_explicit_after_payload_create",
        )
        null_observation = _observed_current_job_snapshot(
            self.api.current_job_snapshot(None),
            current_pid=current_pid,
            label="r7s4_null_job_after_payload_create",
        )
        if payload_pid not in explicit["process_ids"]:
            raise R7S4ProcessError("payload_pid_absent_from_job_snapshot")
        return explicit, null_observation

    def close(self) -> None:
        if not self.closed:
            self.api.close(self.handle)
            self.closed = True

    def __enter__(self) -> JobCapabilityLease:
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()


def _normal_uuid(value: str | uuid.UUID, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise R7S4ProcessError(f"{label}_invalid") from exc


def _lower_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64_RE.fullmatch(value) is None:
        raise R7S4ProcessError(f"{label}_invalid")
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise R7S4ProcessError("canonical_json_serialization_failed") from exc
    return (encoded + "\n").encode("utf-8")


def _strict_json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    if not raw or not raw.endswith(b"\n") or b"\r" in raw or raw.startswith(b"\xef\xbb\xbf"):
        raise R7S4ProcessError(f"{label}_not_canonical_utf8_lf")

    def pairs(value: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in value:
            if key in result:
                raise R7S4ProcessError(f"{label}_duplicate_key")
            result[key] = item
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda item: (_ for _ in ()).throw(
                R7S4ProcessError(f"{label}_nonfinite:{item}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R7S4ProcessError(f"{label}_json_invalid") from exc
    if not isinstance(value, dict) or _canonical_json_bytes(value) != raw:
        raise R7S4ProcessError(f"{label}_not_canonical")
    return value


def _normalize_command(command: Sequence[str | os.PathLike[str]]) -> tuple[str, ...]:
    if isinstance(command, (str, bytes)) or not command:
        raise R7S4ProcessError("payload_command_must_be_nonempty_sequence")
    values = tuple(os.fspath(item) for item in command)
    if any(not isinstance(item, str) or not item or "\0" in item for item in values):
        raise R7S4ProcessError("payload_command_argument_invalid")
    if not os.path.isabs(values[0]):
        raise R7S4ProcessError("payload_application_must_be_absolute")
    executable = os.path.normpath(os.path.abspath(values[0]))
    return (executable, *values[1:])


def normalized_command_digest(command: Sequence[str | os.PathLike[str]]) -> str:
    normalized = _normalize_command(command)
    payload = _canonical_json_bytes({"argv": list(normalized)})
    return hashlib.sha256(b"evm.phase-b2.r7s4.command.v1\0" + payload).hexdigest()


def _casefold_matches(environment: Mapping[str, str], name: str) -> list[str]:
    return [key for key in environment if str(key).upper() == name]


def _consume_fields(
    environment: MutableMapping[str, str], names: set[str], label: str
) -> dict[str, str]:
    captured: dict[str, str] = {}
    invalid: list[str] = []
    for name in sorted(names):
        matches = _casefold_matches(environment, name)
        values = [str(environment[key]) for key in matches]
        for key in matches:
            del environment[key]
        if len(values) != 1:
            invalid.append(name)
        else:
            captured[name] = values[0]
    if invalid:
        raise R7S4ProcessError(f"{label}_environment_fields_invalid:{','.join(invalid)}")
    return captured


def _clear_private_environment(environment: MutableMapping[str, str]) -> None:
    for key in tuple(environment):
        if str(key).upper() in _PRIVATE_ENVIRONMENT_NAMES:
            del environment[key]


def _single_run_uuid(environment: Mapping[str, str]) -> str:
    values = [str(environment[key]) for key in _casefold_matches(environment, r7s3.RUN_UUID_ENV)]
    if len(values) != 1:
        raise R7S4ProcessError("run_uuid_environment_invalid")
    return _normal_uuid(values[0], "run_uuid")


def _parse_handle(value: str, label: str) -> int:
    if re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise R7S4ProcessError(f"{label}_encoding_invalid")
    handle = int(value)
    if handle > (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1:
        raise R7S4ProcessError(f"{label}_outside_pointer_range")
    return handle


def _decode_payload_envelope(value: str) -> tuple[tuple[str, ...], str | None, bool]:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", value) is None:
        raise R7S4ProcessError("payload_envelope_encoding_invalid")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise R7S4ProcessError("payload_envelope_base64_invalid") from exc
    payload = _strict_json_bytes(raw, "payload_envelope")
    if set(payload) != {"argv", "create_no_window", "cwd"}:
        raise R7S4ProcessError("payload_envelope_fields_invalid")
    argv = payload["argv"]
    if not isinstance(argv, list):
        raise R7S4ProcessError("payload_envelope_argv_invalid")
    command = _normalize_command(argv)
    cwd = payload["cwd"]
    if cwd is not None and (not isinstance(cwd, str) or not os.path.isabs(cwd) or "\0" in cwd):
        raise R7S4ProcessError("payload_envelope_cwd_invalid")
    create_no_window = payload["create_no_window"]
    if type(create_no_window) is not bool:
        raise R7S4ProcessError("payload_envelope_create_no_window_invalid")
    return (
        command,
        os.path.normpath(os.path.abspath(cwd)) if cwd else None,
        create_no_window,
    )


def _encode_source_identity(value: Mapping[str, Any]) -> str:
    identity = _validated_executable_lease_identity(value, label="bootstrap_source_identity")
    return base64.b64encode(_canonical_json_bytes(identity)).decode("ascii")


def _decode_source_identity(value: str) -> dict[str, Any]:
    if re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", value) is None:
        raise R7S4ProcessError("bootstrap_source_identity_encoding_invalid")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise R7S4ProcessError("bootstrap_source_identity_base64_invalid") from exc
    return _validated_executable_lease_identity(
        _strict_json_bytes(raw, "bootstrap_source_identity"),
        label="bootstrap_source_identity",
    )


def _encode_payload_envelope(
    command: Sequence[str | os.PathLike[str]],
    cwd: str | os.PathLike[str] | None,
    create_no_window: bool,
) -> str:
    normalized = _normalize_command(command)
    cwd_value = None if cwd is None else os.path.normpath(os.path.abspath(os.fspath(cwd)))
    raw = _canonical_json_bytes(
        {
            "argv": list(normalized),
            "create_no_window": bool(create_no_window),
            "cwd": cwd_value,
        }
    )
    return base64.b64encode(raw).decode("ascii")


def _observed_current_job_snapshot(
    value: Mapping[str, Any], *, current_pid: int, label: str
) -> dict[str, Any]:
    """Validate a NULL/current-Job observation without claiming Job identity.

    Under nested Jobs, ``QueryInformationJobObject(NULL, ...)`` can select an
    outer Job.  It is therefore observation-only; the inherited query-only
    capability remains the explicit authority for admission.
    """

    fields = {
        "is_process_in_job",
        "limit_flags",
        "active_processes",
        "total_processes",
        "terminated_processes",
        "assigned_processes",
        "process_ids",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise R7S4ProcessError(f"{label}_fields_invalid")
    result = dict(value)
    if result["is_process_in_job"] is not True:
        raise R7S4ProcessError(f"{label}_current_process_not_in_selected_job")
    for name in (
        "limit_flags",
        "active_processes",
        "total_processes",
        "terminated_processes",
        "assigned_processes",
    ):
        if type(result[name]) is not int or result[name] < 0:
            raise R7S4ProcessError(f"{label}_{name}_invalid")
    pids = result["process_ids"]
    if (
        not isinstance(pids, list)
        or any(type(pid) is not int or pid <= 0 for pid in pids)
        or pids != sorted(set(pids))
        or current_pid not in pids
        or result["active_processes"] != len(pids)
        or result["assigned_processes"] != len(pids)
        or result["total_processes"] < result["active_processes"]
    ):
        raise R7S4ProcessError(f"{label}_accounting_or_pid_list_invalid")
    return result


def _validated_job_accounting_snapshot(value: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    """Validate explicit-Job accounting without using ambient Job identity."""

    fields = {
        "limit_flags",
        "active_processes",
        "total_processes",
        "terminated_processes",
        "assigned_processes",
        "process_ids",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise R7S4ProcessError(f"{label}_fields_invalid")
    result = dict(value)
    for name in (
        "limit_flags",
        "active_processes",
        "total_processes",
        "terminated_processes",
        "assigned_processes",
    ):
        if type(result[name]) is not int or result[name] < 0:
            raise R7S4ProcessError(f"{label}_{name}_invalid")
    pids = result["process_ids"]
    if (
        not isinstance(pids, list)
        or any(type(pid) is not int or pid <= 0 for pid in pids)
        or pids != sorted(set(pids))
        or result["active_processes"] != len(pids)
        or result["assigned_processes"] != len(pids)
        or result["total_processes"] < result["active_processes"]
    ):
        raise R7S4ProcessError(f"{label}_accounting_or_pid_list_invalid")
    if result["limit_flags"] != 0:
        raise R7S4ProcessError(f"{label}_dangerous_limit_flags")
    return result


def acquire_job_capability_lease(
    *,
    environment: MutableMapping[str, str] | None = None,
    api: BootstrapApi | None = None,
) -> JobCapabilityLease:
    """Consume capability environment and retain its handle until admission ends."""

    if sys.platform != "win32" and api is None:
        raise R7S4ProcessError("job_capability_lease_requires_windows")
    child_environment = os.environ if environment is None else environment
    try:
        fields = _consume_fields(child_environment, _CAPABILITY_NAMES, "job_capability")
    finally:
        for key in tuple(child_environment):
            if str(key).upper() in _CAPABILITY_NAMES:
                del child_environment[key]
    handle = _parse_handle(fields[r7s3.JOB_CAPABILITY_HANDLE_ENV], "job_capability_handle")
    runtime_api: BootstrapApi = api if api is not None else _BootstrapWindowsApi()
    nonce = bytearray()
    keep_handle = False
    try:
        runtime_api.clear_handle_inherit(handle)
        run_uuid = _single_run_uuid(child_environment)
        nonce = bytearray(
            r7s3._normalise_job_capability_nonce(fields[r7s3.JOB_CAPABILITY_NONCE_ENV])
        )
        commitment = r7s3.job_capability_commitment(nonce, run_uuid)
        if fields[r7s3.JOB_CAPABILITY_COMMITMENT_ENV] != commitment:
            raise R7S4ProcessError("job_capability_commitment_mismatch")
        current_pid = os.getpid()
        explicit = r7s3._validated_job_capability_snapshot(
            runtime_api.current_job_snapshot(handle),
            current_pid=current_pid,
            label="r7s4_explicit_before_payload_create",
        )
        null_observation = _observed_current_job_snapshot(
            runtime_api.current_job_snapshot(None),
            current_pid=current_pid,
            label="r7s4_null_job_before_payload_create",
        )
        keep_handle = True
        return JobCapabilityLease(
            handle=handle,
            run_uuid=run_uuid,
            nonce_commitment=commitment,
            explicit_job=explicit,
            null_job_observation=null_observation,
            api=runtime_api,
        )
    finally:
        nonce[:] = b"\0" * len(nonce)
        if not keep_handle:
            runtime_api.close(handle)


_ACK_FIELDS = {
    "schema",
    "run_uuid",
    "admission_id",
    "nonce_commitment",
    "command_sha256",
    "bootstrap_sha256",
    "bootstrap_source_identity",
    "bootstrap_r7s3_identity",
    "bootstrap_pid",
    "payload_pid",
    "payload_executable_identity",
    "explicit_job",
    "null_job_observation",
    "null_job_matches_explicit",
    "payload_in_explicit_job",
    "environment_consumed",
    "raw_nonce_recorded",
}


def validate_bootstrap_ack(
    value: Mapping[str, Any],
    expectation: BootstrapAckExpectation,
    *,
    consumed_admission_ids: set[str],
) -> dict[str, Any]:
    """Validate one ACK and atomically consume its local admission identifier."""

    if not isinstance(value, Mapping) or set(value) != _ACK_FIELDS:
        raise R7S4ProcessError("bootstrap_ack_fields_invalid")
    ack = json.loads(json.dumps(dict(value), allow_nan=False))
    expected_scalars = {
        "schema": ACK_SCHEMA,
        "run_uuid": expectation.run_uuid,
        "admission_id": expectation.admission_id,
        "nonce_commitment": expectation.nonce_commitment,
        "command_sha256": expectation.command_sha256,
        "bootstrap_sha256": expectation.bootstrap_sha256,
        "bootstrap_source_identity": dict(expectation.bootstrap_source_identity),
        "bootstrap_r7s3_identity": dict(expectation.bootstrap_r7s3_identity),
        "bootstrap_pid": expectation.bootstrap_pid,
        "payload_in_explicit_job": True,
        "null_job_matches_explicit": value.get("explicit_job") == value.get("null_job_observation"),
        "environment_consumed": True,
        "raw_nonce_recorded": False,
    }
    for key, expected in expected_scalars.items():
        if ack.get(key) != expected:
            raise R7S4ProcessError(f"bootstrap_ack_{key}_mismatch")
    payload_pid = ack.get("payload_pid")
    if type(payload_pid) is not int or payload_pid <= 0 or payload_pid == expectation.bootstrap_pid:
        raise R7S4ProcessError("bootstrap_ack_payload_pid_invalid")
    ack["payload_executable_identity"] = _validated_executable_lease_identity(
        ack["payload_executable_identity"],
        label="bootstrap_ack_payload_executable_identity",
    )
    snapshot = ack["explicit_job"]
    if not isinstance(snapshot, Mapping) or set(snapshot) != {
        "is_process_in_job",
        "limit_flags",
        "active_processes",
        "total_processes",
        "terminated_processes",
        "assigned_processes",
        "process_ids",
    }:
        raise R7S4ProcessError("bootstrap_ack_job_snapshot_fields_invalid")
    pids = snapshot.get("process_ids")
    if (
        snapshot.get("is_process_in_job") is not True
        or snapshot.get("limit_flags") != 0
        or not isinstance(pids, list)
        or pids != sorted(set(pids))
        or expectation.bootstrap_pid not in pids
        or payload_pid not in pids
        or snapshot.get("active_processes") != len(pids)
        or snapshot.get("assigned_processes") != len(pids)
    ):
        raise R7S4ProcessError("bootstrap_ack_job_snapshot_invalid")
    _observed_current_job_snapshot(
        ack["null_job_observation"],
        current_pid=expectation.bootstrap_pid,
        label="bootstrap_ack_null_job_observation",
    )
    if expectation.admission_id in consumed_admission_ids:
        raise R7S4ProcessError("bootstrap_ack_replay")
    consumed_admission_ids.add(expectation.admission_id)
    return ack


def run_bootstrap_child(
    *,
    environment: MutableMapping[str, str] | None = None,
    api: BootstrapApi | None = None,
) -> int:
    """Run the isolated child admission protocol and return payload exit code."""

    if sys.platform != "win32" and api is None:
        raise R7S4ProcessError("bootstrap_child_requires_windows")
    child_environment = os.environ if environment is None else environment
    runtime_api: BootstrapApi = api if api is not None else _BootstrapWindowsApi()
    lease: JobCapabilityLease | None = None
    ack_handle: int | None = None
    control_handle: int | None = None
    payload_process: int | None = None
    payload_thread: int | None = None
    bootstrap_source_lease: ExecutableLease | None = None
    bootstrap_r7s3_lease: ExecutableLease | None = None
    try:
        lease = acquire_job_capability_lease(environment=child_environment, api=runtime_api)
        try:
            fields = _consume_fields(child_environment, _BOOTSTRAP_NAMES, "r7s4_bootstrap")
        finally:
            _clear_private_environment(child_environment)
        if _single_run_uuid(child_environment) != lease.run_uuid:
            raise R7S4ProcessError("bootstrap_run_uuid_changed_after_capability_consumption")
        ack_handle = _parse_handle(fields[ACK_HANDLE_ENV], "bootstrap_ack_handle")
        control_handle = _parse_handle(fields[CONTROL_HANDLE_ENV], "bootstrap_control_handle")
        if ack_handle == control_handle or lease.handle in {ack_handle, control_handle}:
            raise R7S4ProcessError("bootstrap_private_handle_roles_not_unique")
        runtime_api.clear_handle_inherit(ack_handle)
        runtime_api.clear_handle_inherit(control_handle)
        admission_id = _normal_uuid(fields[ADMISSION_ID_ENV], "admission_id")
        expected_command_sha = _lower_sha256(fields[COMMAND_DIGEST_ENV], "command_sha256")
        expected_bootstrap_sha = _lower_sha256(fields[BOOTSTRAP_SHA256_ENV], "bootstrap_sha256")
        expected_source_identity = _decode_source_identity(fields[BOOTSTRAP_SOURCE_IDENTITY_ENV])
        bootstrap_source_lease = runtime_api.open_executable_lease(
            os.path.normpath(os.path.abspath(__file__))
        )
        bootstrap_source_lease.verify_path_binding()
        actual_source_identity = _validated_executable_lease_identity(
            bootstrap_source_lease.identity, label="executed_bootstrap_source_identity"
        )
        if actual_source_identity != expected_source_identity:
            raise R7S4ProcessError("bootstrap_source_identity_mismatch")
        if actual_source_identity["sha256"] != expected_bootstrap_sha:
            raise R7S4ProcessError("bootstrap_source_sha256_mismatch")
        expected_r7s3_identity = _decode_source_identity(fields[BOOTSTRAP_R7S3_IDENTITY_ENV])
        expected_r7s3_path = Path(__file__).with_name("phase_b2_r7s3_process.py").resolve()
        loaded_r7s3_path = Path(str(r7s3.__file__)).resolve()
        if loaded_r7s3_path != expected_r7s3_path:
            raise R7S4ProcessError("bootstrap_r7s3_loaded_origin_mismatch")
        bootstrap_r7s3_lease = runtime_api.open_executable_lease(str(expected_r7s3_path))
        bootstrap_r7s3_lease.verify_path_binding()
        actual_r7s3_identity = _validated_executable_lease_identity(
            bootstrap_r7s3_lease.identity,
            label="executed_bootstrap_r7s3_identity",
        )
        if actual_r7s3_identity != expected_r7s3_identity:
            raise R7S4ProcessError("bootstrap_r7s3_identity_mismatch")
        command, cwd, create_no_window = _decode_payload_envelope(fields[PAYLOAD_ENVELOPE_ENV])
        if normalized_command_digest(command) != expected_command_sha:
            raise R7S4ProcessError("bootstrap_command_sha256_mismatch")
        info = runtime_api.create_payload_suspended(
            command=command,
            cwd=cwd,
            environment={str(key): str(value) for key, value in child_environment.items()},
            create_no_window=create_no_window,
        )
        payload_process = int(info.hProcess)
        payload_thread = int(info.hThread)
        payload_pid = int(info.dwProcessId)
        if payload_process <= 0 or payload_thread <= 0 or payload_pid <= 0:
            raise R7S4ProcessError("payload_process_information_invalid")
        payload_executable_identity = _validated_executable_lease_identity(
            info.executable_identity, label="payload_executable_identity"
        )
        admitted_snapshot, null_observation = lease.payload_snapshots(payload_process, payload_pid)
        ack = {
            "schema": ACK_SCHEMA,
            "run_uuid": lease.run_uuid,
            "admission_id": admission_id,
            "nonce_commitment": lease.nonce_commitment,
            "command_sha256": expected_command_sha,
            "bootstrap_sha256": expected_bootstrap_sha,
            "bootstrap_source_identity": actual_source_identity,
            "bootstrap_r7s3_identity": actual_r7s3_identity,
            "bootstrap_pid": os.getpid(),
            "payload_pid": payload_pid,
            "payload_executable_identity": payload_executable_identity,
            "explicit_job": admitted_snapshot,
            "null_job_observation": null_observation,
            "null_job_matches_explicit": admitted_snapshot == null_observation,
            "payload_in_explicit_job": True,
            "environment_consumed": True,
            "raw_nonce_recorded": False,
        }
        ack_raw = _canonical_json_bytes(ack)
        if len(ack_raw) > MAX_ACK_BYTES:
            raise R7S4ProcessError("bootstrap_ack_exceeds_bound")
        runtime_api.write_ack(ack_handle, ack_raw)
        bootstrap_source_lease.close()
        bootstrap_source_lease = None
        bootstrap_r7s3_lease.close()
        bootstrap_r7s3_lease = None
        runtime_api.close(ack_handle)
        ack_handle = None
        if runtime_api.read_approval(control_handle) != APPROVAL_BYTE:
            raise R7S4ProcessError("bootstrap_parent_approval_missing_or_invalid")
        runtime_api.close(control_handle)
        control_handle = None
        runtime_api.resume(payload_thread)
        runtime_api.close(payload_thread)
        payload_thread = None
        return runtime_api.wait_payload(payload_process)
    finally:
        # Closing observation handles does not change process execution state.
        # A suspended, unapproved payload intentionally remains residual.
        runtime_api.close(payload_thread)
        runtime_api.close(payload_process)
        runtime_api.close(ack_handle)
        runtime_api.close(control_handle)
        if lease is not None:
            lease.close()
        if bootstrap_source_lease is not None:
            bootstrap_source_lease.close()
        if bootstrap_r7s3_lease is not None:
            bootstrap_r7s3_lease.close()
        _clear_private_environment(child_environment)


_EXECUTABLE_IDENTITY_FIELDS = {
    "path",
    "final_path",
    "sha256",
    "bytes",
    "volume_serial",
    "file_id",
    "file_attributes",
    "write_sharing_allowed",
    "delete_sharing_allowed",
    "lease_held_through_create",
}


def _validated_executable_lease_identity(value: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _EXECUTABLE_IDENTITY_FIELDS:
        raise R7S4ProcessError(f"{label}_fields_invalid")
    identity = dict(value)
    for name in ("path", "final_path"):
        path = identity[name]
        if not isinstance(path, str) or not os.path.isabs(path) or "\0" in path:
            raise R7S4ProcessError(f"{label}_{name}_invalid")
    _lower_sha256(identity["sha256"], f"{label}_sha256")
    for name in ("bytes", "volume_serial", "file_id", "file_attributes"):
        if type(identity[name]) is not int or identity[name] < 0:
            raise R7S4ProcessError(f"{label}_{name}_invalid")
    if identity["write_sharing_allowed"] is not False:
        raise R7S4ProcessError(f"{label}_write_sharing_not_denied")
    if identity["delete_sharing_allowed"] is not False:
        raise R7S4ProcessError(f"{label}_delete_sharing_not_denied")
    if identity["lease_held_through_create"] is not True:
        raise R7S4ProcessError(f"{label}_lease_not_held_through_create")
    return identity


if sys.platform == "win32":

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", r7s3.wintypes.DWORD),
            ("ftCreationTime", r7s3.wintypes.FILETIME),
            ("ftLastAccessTime", r7s3.wintypes.FILETIME),
            ("ftLastWriteTime", r7s3.wintypes.FILETIME),
            ("dwVolumeSerialNumber", r7s3.wintypes.DWORD),
            ("nFileSizeHigh", r7s3.wintypes.DWORD),
            ("nFileSizeLow", r7s3.wintypes.DWORD),
            ("nNumberOfLinks", r7s3.wintypes.DWORD),
            ("nFileIndexHigh", r7s3.wintypes.DWORD),
            ("nFileIndexLow", r7s3.wintypes.DWORD),
        ]


def _declare_executable_lease_signatures(kernel32: Any) -> None:
    if sys.platform != "win32":
        return
    wintypes = r7s3.wintypes
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.SetFilePointerEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    ]
    kernel32.SetFilePointerEx.restype = wintypes.BOOL


def _normal_final_windows_path(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normpath(os.path.abspath(value))


def _open_executable_lease(api: Any, value: str) -> ExecutableLease:
    """Open, measure, and retain a no-write/no-delete-share executable handle."""

    if sys.platform != "win32":
        raise R7S4ProcessError("executable_lease_requires_windows")
    wintypes = r7s3.wintypes
    preliminary = r7s3._validated_executable_identity(value)
    path = str(preliminary["path"])
    # FILE_SHARE_READ only: write and delete/rename sharing are intentionally absent.
    handle = api.kernel32.CreateFileW(
        path,
        api._GENERIC_READ,
        api._FILE_SHARE_READ,
        None,
        api._OPEN_EXISTING,
        api._FILE_ATTRIBUTE_NORMAL | 0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
        None,
    )
    if not handle or handle == api._INVALID_HANDLE_VALUE:
        raise api._error("CreateFileW(executable lease)")
    lease_handle = int(handle)
    try:
        information = _BY_HANDLE_FILE_INFORMATION()
        if not api.kernel32.GetFileInformationByHandle(lease_handle, ctypes.byref(information)):
            raise api._error("GetFileInformationByHandle(executable lease)")
        attributes = int(information.dwFileAttributes)
        if attributes & 0x10 or attributes & 0x400:
            raise R7S4ProcessError("executable_lease_directory_or_reparse_forbidden")
        file_id = (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow)
        size = (int(information.nFileSizeHigh) << 32) | int(information.nFileSizeLow)
        if file_id != int(preliminary["file_id"]) or size != int(preliminary["bytes"]):
            raise R7S4ProcessError("executable_identity_changed_before_lease")
        position = ctypes.c_longlong()
        if not api.kernel32.SetFilePointerEx(lease_handle, 0, ctypes.byref(position), 0):
            raise api._error("SetFilePointerEx(executable lease)")
        digest = hashlib.sha256()
        measured = 0
        while True:
            buffer = ctypes.create_string_buffer(1024 * 1024)
            count = wintypes.DWORD()
            if not api.kernel32.ReadFile(
                lease_handle, buffer, len(buffer), ctypes.byref(count), None
            ):
                raise api._error("ReadFile(executable lease)")
            if count.value == 0:
                break
            chunk = buffer.raw[: count.value]
            digest.update(chunk)
            measured += len(chunk)
        if measured != size or digest.hexdigest() != preliminary["sha256"]:
            raise R7S4ProcessError("executable_identity_changed_during_lease_measurement")
        capacity = 32768
        path_buffer = ctypes.create_unicode_buffer(capacity)
        length = api.kernel32.GetFinalPathNameByHandleW(lease_handle, path_buffer, capacity, 0)
        if not length or length >= capacity:
            raise api._error("GetFinalPathNameByHandleW(executable lease)")
        identity = _validated_executable_lease_identity(
            {
                "path": path,
                "final_path": _normal_final_windows_path(path_buffer.value),
                "sha256": digest.hexdigest(),
                "bytes": measured,
                "volume_serial": int(information.dwVolumeSerialNumber),
                "file_id": file_id,
                "file_attributes": attributes,
                "write_sharing_allowed": False,
                "delete_sharing_allowed": False,
                "lease_held_through_create": True,
            },
            label="executable_lease_identity",
        )
        api.verify_executable_path_binding(identity)
        return ExecutableLease(handle=lease_handle, identity=identity, api=api)
    except Exception:
        api.close(lease_handle)
        raise


def _verify_executable_path_binding(identity: Mapping[str, Any]) -> None:
    validated = _validated_executable_lease_identity(identity, label="executable_lease_binding")
    try:
        current = os.lstat(validated["path"])
    except OSError as exc:
        raise R7S4ProcessError("executable_lease_path_unreadable") from exc
    attributes = int(getattr(current, "st_file_attributes", 0))
    if (
        int(current.st_ino) != validated["file_id"]
        or int(current.st_size) != validated["bytes"]
        or attributes & 0x400
    ):
        raise R7S4ProcessError("executable_lease_path_binding_changed")


def _create_with_executable_lease(api: Any, path: str, creator: Any) -> SuspendedProcess:
    """Call CreateProcess while the same measured executable lease remains open."""

    with api.open_executable_lease(path) as lease:
        lease.verify_path_binding()
        info = creator(str(lease.identity["path"]))
        identity = dict(lease.identity)
    return SuspendedProcess(
        hProcess=int(info.hProcess),
        hThread=int(info.hThread),
        dwProcessId=int(info.dwProcessId),
        dwThreadId=int(getattr(info, "dwThreadId", 0)),
        executable_identity=identity,
    )


class _BootstrapWindowsApi(r7s3._WindowsJobApi):
    """Win32 API used by the isolated bootstrap, never by an arbitrary payload."""

    _STD_INPUT_HANDLE = -10
    _STD_OUTPUT_HANDLE = -11
    _STD_ERROR_HANDLE = -12
    _INFINITE = 0xFFFFFFFF

    def _declare_signatures(self) -> None:
        super()._declare_signatures()
        _declare_executable_lease_signatures(self.kernel32)
        wintypes = r7s3.wintypes
        self.kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        self.kernel32.GetStdHandle.restype = wintypes.HANDLE
        self.kernel32.WriteFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self.kernel32.WriteFile.restype = wintypes.BOOL

    def open_executable_lease(self, path: str) -> ExecutableLease:
        return _open_executable_lease(self, path)

    def verify_executable_path_binding(self, identity: Mapping[str, Any]) -> None:
        _verify_executable_path_binding(identity)

    def _handle_only_attributes(
        self, inherited_handles: Sequence[int]
    ) -> tuple[ctypes.Array[Any], Any, Any]:
        wintypes = r7s3.wintypes
        if (
            not inherited_handles
            or any(
                isinstance(handle, bool) or not isinstance(handle, int) or handle <= 0
                for handle in inherited_handles
            )
            or len(set(inherited_handles)) != len(inherited_handles)
        ):
            raise R7S4ProcessError("payload_inherited_handle_roles_invalid")
        size = ctypes.c_size_t()
        self.kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        if ctypes.get_last_error() != self._ERROR_INSUFFICIENT_BUFFER or not size.value:
            raise self._error("InitializeProcThreadAttributeList(payload size)")
        storage = ctypes.create_string_buffer(size.value)
        attributes = ctypes.cast(storage, wintypes.LPVOID)
        if not self.kernel32.InitializeProcThreadAttributeList(
            attributes, 1, 0, ctypes.byref(size)
        ):
            raise self._error("InitializeProcThreadAttributeList(payload)")
        array_type = wintypes.HANDLE * len(inherited_handles)
        handles = array_type(*inherited_handles)
        if not self.kernel32.UpdateProcThreadAttribute(
            attributes,
            0,
            self._PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.cast(handles, wintypes.LPVOID),
            ctypes.sizeof(handles),
            None,
            None,
        ):
            self.kernel32.DeleteProcThreadAttributeList(attributes)
            raise self._error("UpdateProcThreadAttribute(payload handle list)")
        return storage, attributes, handles

    def create_payload_suspended(
        self,
        *,
        command: Sequence[str],
        cwd: str | None,
        environment: Mapping[str, str],
        create_no_window: bool,
    ) -> Any:
        """Create a payload suspended by automatic nested-Job inheritance.

        No Job-list attribute is supplied.  Windows therefore associates the
        child with every inherited non-breakaway Job before its first
        instruction.  The exact handle list contains standard I/O only.
        """

        command_tuple = _normalize_command(command)
        std_handles = tuple(
            int(self.kernel32.GetStdHandle(value))
            for value in (
                self._STD_INPUT_HANDLE,
                self._STD_OUTPUT_HANDLE,
                self._STD_ERROR_HANDLE,
            )
        )
        if any(handle <= 0 or handle == self._INVALID_HANDLE_VALUE for handle in std_handles):
            raise R7S4ProcessError("bootstrap_standard_handle_invalid")
        payload_environment = {str(key): str(value) for key, value in environment.items()}
        _clear_private_environment(payload_environment)
        _storage, attributes, _handles = self._handle_only_attributes(std_handles)
        try:
            startup = r7s3._STARTUPINFOEXW()
            startup.StartupInfo.cb = ctypes.sizeof(r7s3._STARTUPINFOEXW)
            startup.StartupInfo.dwFlags = self._STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = std_handles[0]
            startup.StartupInfo.hStdOutput = std_handles[1]
            startup.StartupInfo.hStdError = std_handles[2]
            startup.lpAttributeList = attributes
            info = r7s3._PROCESS_INFORMATION()
            command_line = ctypes.create_unicode_buffer(
                subprocess.list2cmdline(list(command_tuple))
            )
            pairs = [f"{key}={value}" for key, value in payload_environment.items()]
            env_block = ctypes.create_unicode_buffer(
                "\0".join(sorted(pairs, key=str.casefold)) + "\0\0"
            )
            flags = (
                self._CREATE_SUSPENDED
                | self._CREATE_UNICODE_ENVIRONMENT
                | self._EXTENDED_STARTUPINFO_PRESENT
            )
            if create_no_window:
                flags |= self._CREATE_NO_WINDOW
            startup_pointer = ctypes.cast(ctypes.byref(startup), ctypes.POINTER(r7s3._STARTUPINFOW))

            def create(application_path: str) -> Any:
                if not self.kernel32.CreateProcessW(
                    application_path,
                    command_line,
                    None,
                    None,
                    True,
                    flags,
                    env_block,
                    cwd,
                    startup_pointer,
                    ctypes.byref(info),
                ):
                    raise self._error("CreateProcessW(payload)")
                return info

            return _create_with_executable_lease(self, command_tuple[0], create)
        finally:
            self.kernel32.DeleteProcThreadAttributeList(attributes)

    def write_ack(self, handle: int, payload: bytes) -> None:
        wintypes = r7s3.wintypes
        offset = 0
        while offset < len(payload):
            written = wintypes.DWORD()
            chunk = payload[offset : offset + 65536]
            buffer = ctypes.create_string_buffer(chunk)
            if not self.kernel32.WriteFile(handle, buffer, len(chunk), ctypes.byref(written), None):
                raise self._error("WriteFile(bootstrap ack)")
            if written.value <= 0:
                raise R7S4ProcessError("bootstrap_ack_write_made_no_progress")
            offset += int(written.value)

    def read_approval(self, handle: int) -> bytes:
        wintypes = r7s3.wintypes
        value = ctypes.create_string_buffer(2)
        read = wintypes.DWORD()
        if not self.kernel32.ReadFile(handle, value, 2, ctypes.byref(read), None):
            if ctypes.get_last_error() == self._ERROR_BROKEN_PIPE:
                return b""
            raise self._error("ReadFile(parent approval)")
        return value.raw[: read.value]

    def wait_payload(self, process: int) -> int:
        result = self.kernel32.WaitForSingleObject(process, self._INFINITE)
        if result != self._WAIT_OBJECT_0:
            raise self._error("WaitForSingleObject(payload)")
        value = self.exit_code(process)
        if value is None:
            raise R7S4ProcessError("payload_exit_code_unavailable")
        return value


class _ParentWindowsApi(r7s3._WindowsJobApi):
    """Parent-side Win32 adapter for one bootstrap admission."""

    def _declare_signatures(self) -> None:
        super()._declare_signatures()
        _declare_executable_lease_signatures(self.kernel32)
        wintypes = r7s3.wintypes
        self.kernel32.WriteFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self.kernel32.WriteFile.restype = wintypes.BOOL

    def open_executable_lease(self, path: str) -> ExecutableLease:
        return _open_executable_lease(self, path)

    def verify_executable_path_binding(self, identity: Mapping[str, Any]) -> None:
        _verify_executable_path_binding(identity)

    def create_control_pipe(self) -> tuple[int, int]:
        wintypes = r7s3.wintypes
        security = r7s3._SECURITY_ATTRIBUTES(
            nLength=ctypes.sizeof(r7s3._SECURITY_ATTRIBUTES),
            lpSecurityDescriptor=None,
            bInheritHandle=True,
        )
        read_handle = wintypes.HANDLE()
        write_handle = wintypes.HANDLE()
        if not self.kernel32.CreatePipe(
            ctypes.byref(read_handle),
            ctypes.byref(write_handle),
            ctypes.byref(security),
            0,
        ):
            raise self._error("CreatePipe(control)")
        if not self.kernel32.SetHandleInformation(write_handle, self._HANDLE_FLAG_INHERIT, 0):
            self.close(read_handle.value)
            self.close(write_handle.value)
            raise self._error("SetHandleInformation(control write)")
        return int(read_handle.value), int(write_handle.value)

    def create_bootstrap_suspended(
        self,
        *,
        command: Sequence[str],
        cwd: str | None,
        environment: Mapping[str, str],
        job: int,
        stdin_handle: int,
        stdout_handle: int,
        stderr_handle: int,
        ack_handle: int,
        control_handle: int,
        nonce: bytes | bytearray | memoryview,
        run_uuid: str,
        admission_id: str,
        payload_envelope: str,
        command_sha256: str,
        bootstrap_sha256: str,
        bootstrap_source_identity: Mapping[str, Any],
        bootstrap_r7s3_identity: Mapping[str, Any],
        create_no_window: bool,
    ) -> Any:
        normalized_nonce = r7s3._normalise_job_capability_nonce(nonce)
        normalized_run_uuid = _normal_uuid(run_uuid, "run_uuid")
        bootstrap_environment = {str(key): str(value) for key, value in environment.items()}
        _clear_private_environment(bootstrap_environment)
        for key in tuple(bootstrap_environment):
            if key.upper() == r7s3.RUN_UUID_ENV:
                del bootstrap_environment[key]
        capability = self.duplicate_inheritable_job_capability(job)
        try:
            bootstrap_environment.update(
                {
                    r7s3.RUN_UUID_ENV: normalized_run_uuid,
                    r7s3.JOB_CAPABILITY_HANDLE_ENV: str(capability),
                    r7s3.JOB_CAPABILITY_NONCE_ENV: normalized_nonce.hex(),
                    r7s3.JOB_CAPABILITY_COMMITMENT_ENV: r7s3.job_capability_commitment(
                        normalized_nonce, normalized_run_uuid
                    ),
                    ACK_HANDLE_ENV: str(ack_handle),
                    CONTROL_HANDLE_ENV: str(control_handle),
                    ADMISSION_ID_ENV: _normal_uuid(admission_id, "admission_id"),
                    PAYLOAD_ENVELOPE_ENV: payload_envelope,
                    COMMAND_DIGEST_ENV: _lower_sha256(command_sha256, "command_sha256"),
                    BOOTSTRAP_SHA256_ENV: _lower_sha256(bootstrap_sha256, "bootstrap_sha256"),
                    BOOTSTRAP_SOURCE_IDENTITY_ENV: _encode_source_identity(
                        bootstrap_source_identity
                    ),
                    BOOTSTRAP_R7S3_IDENTITY_ENV: _encode_source_identity(bootstrap_r7s3_identity),
                }
            )
            inherited = (
                stdin_handle,
                stdout_handle,
                stderr_handle,
                capability,
                ack_handle,
                control_handle,
            )
            if any(
                isinstance(handle, bool) or not isinstance(handle, int) or handle <= 0
                for handle in inherited
            ) or len(set(inherited)) != len(inherited):
                raise R7S4ProcessError("bootstrap_inherited_handle_roles_invalid")
            _storage, attributes, _jobs, _handles = self.initialise_attributes(job, inherited)
            try:
                startup = r7s3._STARTUPINFOEXW()
                startup.StartupInfo.cb = ctypes.sizeof(r7s3._STARTUPINFOEXW)
                startup.StartupInfo.dwFlags = self._STARTF_USESTDHANDLES
                startup.StartupInfo.hStdInput = stdin_handle
                startup.StartupInfo.hStdOutput = stdout_handle
                startup.StartupInfo.hStdError = stderr_handle
                startup.lpAttributeList = attributes
                info = r7s3._PROCESS_INFORMATION()
                command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(command)))
                pairs = [f"{key}={value}" for key, value in bootstrap_environment.items()]
                env_block = ctypes.create_unicode_buffer(
                    "\0".join(sorted(pairs, key=str.casefold)) + "\0\0"
                )
                flags = (
                    self._CREATE_SUSPENDED
                    | self._CREATE_UNICODE_ENVIRONMENT
                    | self._EXTENDED_STARTUPINFO_PRESENT
                )
                if create_no_window:
                    flags |= self._CREATE_NO_WINDOW
                pointer = ctypes.cast(ctypes.byref(startup), ctypes.POINTER(r7s3._STARTUPINFOW))

                def create(application_path: str) -> Any:
                    if not self.kernel32.CreateProcessW(
                        application_path,
                        command_line,
                        None,
                        None,
                        True,
                        flags,
                        env_block,
                        cwd,
                        pointer,
                        ctypes.byref(info),
                    ):
                        raise self._error("CreateProcessW(bootstrap)")
                    return info

                return _create_with_executable_lease(self, str(command[0]), create)
            finally:
                self.kernel32.DeleteProcThreadAttributeList(attributes)
        finally:
            _clear_private_environment(bootstrap_environment)
            self.close(capability)

    def member_job_snapshot(self, job: int, process: int) -> Mapping[str, Any]:
        value = dict(self.current_job_snapshot(job))
        value["is_process_in_job"] = self.is_process_in_job(process, job)
        return value

    def job_accounting_snapshot(self, job: int) -> Mapping[str, Any]:
        accounting = self.query_accounting(job)
        assigned, pids = self.query_process_id_list(job)
        return {
            "limit_flags": self.query_limit_flags(job),
            "active_processes": int(accounting.ActiveProcesses),
            "total_processes": int(accounting.TotalProcesses),
            "terminated_processes": int(accounting.TotalTerminatedProcesses),
            "assigned_processes": assigned,
            "process_ids": list(sorted(pids)),
        }

    def read_bounded_pipe(
        self, read_handle: int, sink: bytearray, drained: threading.Event, maximum: int
    ) -> None:
        self.read_bounded_discarding_pipe(
            read_handle,
            sink,
            drained,
            StreamCaptureState(limit_bytes=maximum),
        )

    def read_bounded_discarding_pipe(
        self,
        read_handle: int,
        sink: bytearray,
        drained: threading.Event,
        state: StreamCaptureState,
    ) -> None:
        wintypes = r7s3.wintypes
        try:
            while True:
                buffer = ctypes.create_string_buffer(65536)
                read = wintypes.DWORD()
                ok = self.kernel32.ReadFile(
                    read_handle, buffer, len(buffer), ctypes.byref(read), None
                )
                if read.value:
                    raw = buffer.raw[: read.value]
                    _append_bounded_capture(sink, state, raw)
                if not ok:
                    if ctypes.get_last_error() == self._ERROR_BROKEN_PIPE:
                        drained.set()
                    else:
                        state.read_error = f"win32_error_{ctypes.get_last_error()}"
                    return
                if read.value == 0:
                    drained.set()
                    return
        finally:
            self.close(read_handle)

    def write_approval(self, handle: int) -> None:
        wintypes = r7s3.wintypes
        written = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(APPROVAL_BYTE)
        if not self.kernel32.WriteFile(
            handle, buffer, len(APPROVAL_BYTE), ctypes.byref(written), None
        ) or written.value != len(APPROVAL_BYTE):
            raise self._error("WriteFile(parent approval)")


def _fresh_nonce() -> bytearray:
    return bytearray(os.urandom(r7s3.JOB_CAPABILITY_NONCE_BYTES))


def _source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


R7S4_LOCAL_PROCESS_CONTRACT: dict[str, Any] = {
    "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.local-process-contract.v1",
    "bootstrap_create_suspended": True,
    "payload_create_suspended": True,
    "query_only_capability": True,
    "capability_lease_through_payload_admission": True,
    "payload_automatic_nested_job_inheritance": True,
    "explicit_capability_job_is_admission_authority": True,
    "null_job_snapshot_used_as_identity": False,
    "outer_job_observed_without_identity_claim": True,
    "dedicated_parent_ack": True,
    "payload_ack_and_capability_inheritance": False,
    "bootstrap_executable_lease_through_create": True,
    "payload_executable_lease_through_create": True,
    "bootstrap_source_lease_through_ack": True,
    "bootstrap_executed_source_identity_in_ack": True,
    "bootstrap_r7s3_dependency_lease_through_ack": True,
    "bootstrap_r7s3_source_loaded_without_pyc": True,
    # The interpreter and stdlib/import machinery remain outside this module's
    # source-level pin set, so no complete transitive Python TCB claim is made.
    "bootstrap_transitive_python_tcb_pinned": False,
    "executable_write_delete_sharing_denied": True,
    # CreateProcessW remains path-based; same-token hostile-admin elimination
    # requires authority outside this bounded local module.
    "path_based_createprocess_toctou_eliminated": False,
    "bounded_stream_capture_with_discard_drain": True,
    "active_pid_query_unknown_is_manual": True,
    "completion_queue_stable_zero_reconciliation": True,
    "job_total_process_identity_reconciliation": True,
    "residual_observation_process_local_registry": True,
    "raw_command_and_stream_content_in_default_outcome": False,
    "terminate_process_calls": 0,
    "terminate_job_calls": 0,
    "kill_on_job_close": False,
    "production_fresh_wired": False,
    "go_evidence_eligible": False,
    "external_review_required": True,
}


class WindowsBootstrapProcessRunner:
    """Run one local payload through the pinned two-process admission gate."""

    def __init__(
        self,
        expected_bootstrap_sha256: str,
        expected_r7s3_sha256: str,
        contract: Any | None = None,
        *,
        api_factory: Any = _ParentWindowsApi,
        clock: Any = time.monotonic,
        sleep: Any = time.sleep,
        stream_capture_limit_bytes: int = DEFAULT_STREAM_CAPTURE_BYTES,
        observation_registry: LocalResidualObservationRegistry = RESIDUAL_OBSERVATIONS,
    ) -> None:
        self.expected_bootstrap_sha256 = _lower_sha256(
            expected_bootstrap_sha256, "expected_bootstrap_sha256"
        )
        self.expected_r7s3_sha256 = _lower_sha256(expected_r7s3_sha256, "expected_r7s3_sha256")
        self.contract = contract or r7s3.TimeoutContract()
        self._api_factory = api_factory
        self._clock = clock
        self._sleep = sleep
        if type(stream_capture_limit_bytes) is not int or stream_capture_limit_bytes <= 0:
            raise R7S4ProcessError("stream_capture_limit_bytes_invalid")
        self._stream_capture_limit_bytes = stream_capture_limit_bytes
        self._observation_registry = observation_registry
        self._consumed_admission_ids: set[str] = set()

    @staticmethod
    def _bootstrap_command() -> tuple[str, ...]:
        # Avoid a venv redirector becoming the Job root: the ACK must be issued
        # by the exact process returned by CreateProcessW, not a later Python
        # child spawned by an interpreter launcher shim.
        executable = os.path.normpath(
            os.path.abspath(getattr(sys, "_base_executable", sys.executable))
        )
        source = os.path.normpath(os.path.abspath(__file__))
        return (executable, "-I", "-S", "-B", source, "--bootstrap")

    def run(
        self,
        command: Sequence[str | os.PathLike[str]],
        *,
        name: str = "r7s4-local-payload",
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        run_uuid: str | uuid.UUID | None = None,
        create_no_window: bool = True,
        poll_interval_seconds: float = 0.025,
    ) -> BootstrapOutcome:
        if self._api_factory is _ParentWindowsApi and sys.platform != "win32":
            raise R7S4ProcessError("WindowsBootstrapProcessRunner_requires_windows")
        if not name or poll_interval_seconds <= 0:
            raise R7S4ProcessError("runner_name_or_poll_interval_invalid")
        command_tuple = _normalize_command(command)
        execution_uuid = _normal_uuid(run_uuid or uuid.uuid4(), "run_uuid")
        admission_id = str(uuid.uuid4())
        command_sha = normalized_command_digest(command_tuple)
        bootstrap_sha = self.expected_bootstrap_sha256
        cwd_value = None if cwd is None else os.path.normpath(os.path.abspath(os.fspath(cwd)))
        payload_envelope = _encode_payload_envelope(command_tuple, cwd_value, create_no_window)
        child_environment = {
            str(key): str(value) for key, value in (os.environ if env is None else env).items()
        }
        _clear_private_environment(child_environment)
        for key in tuple(child_environment):
            if key.upper() == r7s3.RUN_UUID_ENV:
                del child_environment[key]

        api: ParentApi = self._api_factory()
        nonce = _fresh_nonce()
        commitment = r7s3.job_capability_commitment(nonce, execution_uuid)
        errors: list[str] = []
        ack: dict[str, Any] | None = None
        ack_valid = False
        authorized = False
        timed_out = False
        ack_timeout = False
        ack_overflow = False
        stream_drain_within_deadline = False
        manual = False
        residual_pids: tuple[int, ...] = ()
        active_pid_query_succeeded = False
        active_zero = False
        residual_state = "unknown"
        completion_accounting_reconciled = False
        completion_event_sequence_complete = False
        stable_zero_observations = 0
        final_job_accounting: dict[str, Any] | None = None
        first_zero_observed_at: float | None = None
        observation_id: str | None = None
        observation_continuity = "not_required"
        return_code: int | None = None
        sequence = 0
        process_events: list[dict[str, Any]] = []
        identities: dict[tuple[int, int], dict[str, Any]] = {}

        job: int | None = None
        completion: int | None = None
        stdin_handle: int | None = None
        stdout_read: int | None = None
        stdout_write: int | None = None
        stderr_read: int | None = None
        stderr_write: int | None = None
        ack_read: int | None = None
        ack_write: int | None = None
        control_read: int | None = None
        control_write: int | None = None
        root_process: int | None = None
        root_thread: int | None = None
        root_pid: int | None = None
        payload_process: int | None = None
        bootstrap_executable_identity: dict[str, Any] | None = None
        bootstrap_source_identity: dict[str, Any] | None = None
        bootstrap_source_lease: ExecutableLease | None = None
        bootstrap_r7s3_identity: dict[str, Any] | None = None
        bootstrap_r7s3_lease: ExecutableLease | None = None

        stdout_data = bytearray()
        stderr_data = bytearray()
        ack_data = bytearray()
        stdout_state = StreamCaptureState(self._stream_capture_limit_bytes)
        stderr_state = StreamCaptureState(self._stream_capture_limit_bytes)
        ack_state = StreamCaptureState(MAX_ACK_BYTES)
        stdout_drained = threading.Event()
        stderr_drained = threading.Event()
        ack_drained = threading.Event()
        stdout_drain_signal = _TimestampedDrainSignal(stdout_drained, stdout_state, self._clock)
        stderr_drain_signal = _TimestampedDrainSignal(stderr_drained, stderr_state, self._clock)
        ack_drain_signal = _TimestampedDrainSignal(ack_drained, ack_state, self._clock)
        stdout_thread: threading.Thread | None = None
        stderr_thread: threading.Thread | None = None
        ack_thread: threading.Thread | None = None

        def add_event(event: str, pid: int | None = None, **details: Any) -> int:
            nonlocal sequence
            sequence += 1
            process_events.append(
                {
                    "sequence": sequence,
                    "event": event,
                    "monotonic_ns": time.monotonic_ns(),
                    "pid": pid,
                    "details": dict(details),
                }
            )
            return sequence

        def capture_identity(
            pid: int,
            *,
            supplied_handle: int | None = None,
            fallback_ppid: int | None = None,
        ) -> None:
            handle = supplied_handle or api.open_process(pid)
            owns = supplied_handle is None
            if not handle:
                errors.append(f"process_identity_open_failed:pid={pid}")
                return
            try:
                if not api.is_process_in_job(handle, job):
                    errors.append(f"process_identity_not_in_explicit_job:pid={pid}")
                    return
                identity_sequence = add_event("process_identity_observed", pid)
                identity = api.process_identity(
                    handle,
                    pid=pid,
                    fallback_ppid=fallback_ppid,
                    run_uuid=execution_uuid,
                    observed_sequence=identity_sequence,
                )
                key = (int(identity.pid), int(identity.creation_time_ns))
                identities[key] = {
                    "pid": int(identity.pid),
                    "ppid": (int(identity.ppid) if identity.ppid is not None else None),
                    "creation_time_ns": int(identity.creation_time_ns),
                    "creation_time_utc": str(identity.creation_time_utc),
                    "image_sha256": hashlib.sha256(str(identity.image).encode("utf-8")).hexdigest(),
                    "run_uuid": str(identity.run_uuid),
                    "observed_sequence": int(identity.observed_sequence),
                }
            except Exception as exc:
                errors.append(f"process_identity_failed:pid={pid}:{type(exc).__name__}")
            finally:
                if owns:
                    api.close(handle)

        def drain_job_events() -> tuple[int, bool]:
            if completion is None:
                return 0, False
            try:
                observed = api.completion_events(completion)
            except Exception as exc:
                errors.append(f"completion_event_query_failed:{type(exc).__name__}")
                return 0, False
            for message, pid in observed:
                event_name = {
                    getattr(api, "_JOB_MESSAGE_ACTIVE_ZERO", 4): "job_active_zero",
                    getattr(api, "_JOB_MESSAGE_NEW_PROCESS", 6): "job_new_process",
                    getattr(api, "_JOB_MESSAGE_EXIT_PROCESS", 7): "job_exit_process",
                    getattr(api, "_JOB_MESSAGE_ABNORMAL_EXIT", 8): "job_abnormal_exit",
                }.get(message, f"job_message_{message}")
                add_event(event_name, pid)
                if message == getattr(api, "_JOB_MESSAGE_NEW_PROCESS", 6) and pid:
                    new_instance_count = sum(
                        1
                        for item in process_events
                        if item["event"] == "job_new_process" and item["pid"] == pid
                    )
                    known_instance_count = sum(1 for known_pid, _ in identities if known_pid == pid)
                    if known_instance_count < new_instance_count:
                        capture_identity(pid, fallback_ppid=None)
            return len(observed), True

        try:
            job, completion = api.create_job_and_completion_port()
            stdout_read, stdout_write = api.create_pipe()
            stderr_read, stderr_write = api.create_pipe()
            ack_read, ack_write = api.create_pipe()
            control_read, control_write = api.create_control_pipe()
            stdin_handle = api.open_inheritable_null()
            bootstrap_source_lease = api.open_executable_lease(
                os.path.normpath(os.path.abspath(__file__))
            )
            bootstrap_source_lease.verify_path_binding()
            bootstrap_source_identity = _validated_executable_lease_identity(
                bootstrap_source_lease.identity,
                label="parent_bootstrap_source_identity",
            )
            if bootstrap_source_identity["sha256"] != bootstrap_sha:
                raise R7S4ProcessError("bootstrap_source_sha256_mismatch_before_create")
            bootstrap_r7s3_lease = api.open_executable_lease(
                str(Path(__file__).with_name("phase_b2_r7s3_process.py").resolve())
            )
            bootstrap_r7s3_lease.verify_path_binding()
            bootstrap_r7s3_identity = _validated_executable_lease_identity(
                bootstrap_r7s3_lease.identity,
                label="parent_bootstrap_r7s3_identity",
            )
            if bootstrap_r7s3_identity["sha256"] != self.expected_r7s3_sha256:
                raise R7S4ProcessError("bootstrap_r7s3_sha256_mismatch_before_create")
            info = api.create_bootstrap_suspended(
                command=self._bootstrap_command(),
                cwd=cwd_value,
                environment=child_environment,
                job=job,
                stdin_handle=stdin_handle,
                stdout_handle=stdout_write,
                stderr_handle=stderr_write,
                ack_handle=ack_write,
                control_handle=control_read,
                nonce=nonce,
                run_uuid=execution_uuid,
                admission_id=admission_id,
                payload_envelope=payload_envelope,
                command_sha256=command_sha,
                bootstrap_sha256=bootstrap_sha,
                bootstrap_source_identity=bootstrap_source_identity,
                bootstrap_r7s3_identity=bootstrap_r7s3_identity,
                create_no_window=create_no_window,
            )
            root_process = int(info.hProcess)
            root_thread = int(info.hThread)
            root_pid = int(info.dwProcessId)
            bootstrap_executable_identity = _validated_executable_lease_identity(
                info.executable_identity, label="bootstrap_executable_identity"
            )
            if root_process <= 0 or root_thread <= 0 or root_pid <= 0:
                raise R7S4ProcessError("bootstrap_process_information_invalid")
            add_event("bootstrap_created_suspended", root_pid)

            for handle_name in (
                "stdin_handle",
                "stdout_write",
                "stderr_write",
                "ack_write",
                "control_read",
            ):
                handle = locals()[handle_name]
                api.close(handle)
                if handle_name == "stdin_handle":
                    stdin_handle = None
                elif handle_name == "stdout_write":
                    stdout_write = None
                elif handle_name == "stderr_write":
                    stderr_write = None
                elif handle_name == "ack_write":
                    ack_write = None
                else:
                    control_read = None

            if not api.is_process_in_job(root_process, job):
                raise R7S4ProcessError("suspended_bootstrap_not_in_job")
            initial = r7s3._validated_job_capability_snapshot(
                api.member_job_snapshot(job, root_process),
                current_pid=root_pid,
                label="r7s4_parent_initial_job",
            )
            if initial["active_processes"] != 1 or initial["assigned_processes"] != 1:
                raise R7S4ProcessError("suspended_bootstrap_accounting_not_exactly_one")
            add_event("bootstrap_explicit_job_membership_verified", root_pid)
            capture_identity(root_pid, supplied_handle=root_process, fallback_ppid=os.getpid())

            stdout_thread = threading.Thread(
                target=api.read_bounded_discarding_pipe,
                args=(stdout_read, stdout_data, stdout_drain_signal, stdout_state),
                name=f"{name}-stdout",
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=api.read_bounded_discarding_pipe,
                args=(stderr_read, stderr_data, stderr_drain_signal, stderr_state),
                name=f"{name}-stderr",
                daemon=True,
            )
            ack_thread = threading.Thread(
                target=api.read_bounded_discarding_pipe,
                args=(ack_read, ack_data, ack_drain_signal, ack_state),
                name=f"{name}-admission-ack",
                daemon=True,
            )
            stdout_thread.start()
            stdout_read = None
            stderr_thread.start()
            stderr_read = None
            ack_thread.start()
            ack_read = None

            start = self._clock()
            wrapper_deadline = start + self.contract.wrapper_timeout_seconds
            api.resume(root_thread)
            add_event("bootstrap_resumed", root_pid)
            api.close(root_thread)
            root_thread = None
            while (
                not ack_drained.is_set()
                and not ack_state.overflowed
                and self._clock() < wrapper_deadline
            ):
                self._sleep(poll_interval_seconds)
            ack_completed_on_time = (
                ack_drained.is_set()
                and ack_state.completed_monotonic is not None
                and ack_state.completed_monotonic <= wrapper_deadline
            )
            if ack_state.overflowed:
                ack_overflow = True
                manual = True
                errors.append("bootstrap_ack_overflow")
                add_event("bootstrap_ack_overflow", root_pid)
            elif not ack_completed_on_time:
                timed_out = True
                ack_timeout = True
                manual = True
                if ack_drained.is_set():
                    errors.append("bootstrap_ack_completed_after_wrapper_deadline")
                    add_event(
                        "bootstrap_ack_late",
                        root_pid,
                        completed_monotonic=ack_state.completed_monotonic,
                        deadline=wrapper_deadline,
                    )
                else:
                    errors.append("bootstrap_ack_not_drained_before_wrapper_deadline")
                    add_event("bootstrap_ack_timeout", root_pid)
            else:
                try:
                    parsed = _strict_json_bytes(bytes(ack_data), "bootstrap_ack")
                    expectation = BootstrapAckExpectation(
                        run_uuid=execution_uuid,
                        admission_id=admission_id,
                        nonce_commitment=commitment,
                        command_sha256=command_sha,
                        bootstrap_sha256=bootstrap_sha,
                        bootstrap_source_identity=bootstrap_source_identity,
                        bootstrap_r7s3_identity=bootstrap_r7s3_identity,
                        bootstrap_pid=root_pid,
                    )
                    ack = validate_bootstrap_ack(
                        parsed,
                        expectation,
                        consumed_admission_ids=self._consumed_admission_ids,
                    )
                    payload_pid = int(ack["payload_pid"])
                    payload_process = api.open_process(payload_pid)
                    if not payload_process:
                        raise R7S4ProcessError("parent_cannot_open_suspended_payload")
                    if not api.is_process_in_job(payload_process, job):
                        raise R7S4ProcessError("parent_payload_not_in_explicit_job")
                    capture_identity(
                        payload_pid,
                        supplied_handle=payload_process,
                        fallback_ppid=root_pid,
                    )
                    parent_snapshot = r7s3._validated_job_capability_snapshot(
                        api.member_job_snapshot(job, root_process),
                        current_pid=root_pid,
                        label="r7s4_parent_admission_job",
                    )
                    if parent_snapshot != ack["explicit_job"]:
                        raise R7S4ProcessError("parent_and_bootstrap_job_snapshots_differ")
                    api.write_approval(control_write)
                    bootstrap_source_lease.close()
                    bootstrap_source_lease = None
                    bootstrap_r7s3_lease.close()
                    bootstrap_r7s3_lease = None
                    authorized = True
                    ack_valid = True
                    add_event("payload_resume_authorized", payload_pid)
                except Exception as exc:
                    manual = True
                    errors.append(f"bootstrap_ack_rejected:{type(exc).__name__}")
                    add_event("bootstrap_ack_rejected", root_pid, error=type(exc).__name__)

            # Closing without the approval byte makes the bootstrap fail closed.
            api.close(control_write)
            control_write = None

            residual_deadline: float | None = None
            reconciliation_deadline: float | None = None
            required_stable_zero_observations = 2
            last_reconciliation_reason = "not_started"
            while True:
                events_before_zero, completion_query_ok = drain_job_events()
                if not completion_query_ok:
                    manual = True
                    last_reconciliation_reason = "completion_query_failed"
                    break
                try:
                    residual_pids = tuple(sorted(api.query_active_pids(job)))
                    active_pid_query_succeeded = True
                    residual_state = "zero" if not residual_pids else "nonzero"
                except Exception as exc:
                    residual_pids = ()
                    active_pid_query_succeeded = False
                    active_zero = False
                    residual_state = "unknown"
                    manual = True
                    errors.append(f"active_pid_query_failed:{type(exc).__name__}")
                    add_event("active_pid_query_failed", error=type(exc).__name__)
                    last_reconciliation_reason = "active_pid_query_failed"
                    break
                if residual_pids:
                    stable_zero_observations = 0
                    final_job_accounting = None
                    reconciliation_deadline = None
                    now = self._clock()
                    if residual_deadline is None:
                        if manual or now >= wrapper_deadline:
                            if now >= wrapper_deadline:
                                timed_out = True
                            manual = True
                            residual_deadline = now + self.contract.residual_repoll_seconds
                    elif now >= residual_deadline:
                        last_reconciliation_reason = "residual_deadline_elapsed"
                        break
                    self._sleep(poll_interval_seconds)
                    continue

                zero_observed_now = self._clock()
                if first_zero_observed_at is None:
                    first_zero_observed_at = zero_observed_now
                    if first_zero_observed_at > wrapper_deadline:
                        timed_out = True
                        manual = True
                        errors.append("job_zero_first_observed_after_wrapper_deadline")
                        add_event(
                            "job_zero_observed_late",
                            observed_monotonic=first_zero_observed_at,
                            wrapper_deadline=wrapper_deadline,
                        )
                    if residual_deadline is not None and first_zero_observed_at > residual_deadline:
                        manual = True
                        errors.append("job_zero_first_observed_after_residual_deadline")

                if reconciliation_deadline is None:
                    reconciliation_window = max(
                        float(self.contract.stream_drain_seconds),
                        poll_interval_seconds * (required_stable_zero_observations + 1),
                    )
                    reconciliation_deadline = self._clock() + reconciliation_window

                events_after_zero, completion_query_ok = drain_job_events()
                if not completion_query_ok:
                    manual = True
                    last_reconciliation_reason = "completion_query_failed"
                    break
                try:
                    pids_after_drain = tuple(sorted(api.query_active_pids(job)))
                    accounting_before_final_drain = _validated_job_accounting_snapshot(
                        api.job_accounting_snapshot(job),
                        label="r7s4_final_job_accounting_before_drain",
                    )
                except Exception as exc:
                    residual_pids = ()
                    active_pid_query_succeeded = False
                    active_zero = False
                    residual_state = "unknown"
                    manual = True
                    errors.append(f"final_job_accounting_query_failed:{type(exc).__name__}")
                    add_event("final_job_accounting_query_failed", error=type(exc).__name__)
                    last_reconciliation_reason = "final_accounting_query_failed"
                    break

                events_after_accounting, completion_query_ok = drain_job_events()
                if not completion_query_ok:
                    manual = True
                    last_reconciliation_reason = "completion_query_failed"
                    break
                try:
                    pids_after_final_drain = tuple(sorted(api.query_active_pids(job)))
                    accounting_after_final_drain = _validated_job_accounting_snapshot(
                        api.job_accounting_snapshot(job),
                        label="r7s4_final_job_accounting_after_drain",
                    )
                except Exception as exc:
                    residual_pids = ()
                    active_pid_query_succeeded = False
                    active_zero = False
                    residual_state = "unknown"
                    manual = True
                    errors.append(f"final_job_accounting_query_failed:{type(exc).__name__}")
                    add_event("final_job_accounting_query_failed", error=type(exc).__name__)
                    last_reconciliation_reason = "final_accounting_query_failed"
                    break

                residual_pids = pids_after_final_drain or pids_after_drain
                if residual_pids:
                    residual_state = "nonzero"
                    stable_zero_observations = 0
                    final_job_accounting = None
                    reconciliation_deadline = None
                    self._sleep(poll_interval_seconds)
                    continue

                final_job_accounting = accounting_after_final_drain
                total_processes = int(final_job_accounting["total_processes"])
                new_process_events = sum(
                    item["event"] == "job_new_process" for item in process_events
                )
                exit_process_events = sum(
                    item["event"] in {"job_exit_process", "job_abnormal_exit"}
                    for item in process_events
                )
                active_zero_event_seen = any(
                    item["event"] == "job_active_zero" for item in process_events
                )
                quiet_iteration = (
                    events_before_zero == 0
                    and events_after_zero == 0
                    and events_after_accounting == 0
                )
                accounting_stable = (
                    accounting_before_final_drain == accounting_after_final_drain
                    and final_job_accounting["active_processes"] == 0
                    and final_job_accounting["assigned_processes"] == 0
                    and final_job_accounting["process_ids"] == []
                )
                identities_reconciled = len(identities) == total_processes
                events_reconciled = (
                    new_process_events == total_processes
                    and exit_process_events == total_processes
                    and active_zero_event_seen
                )
                if (
                    quiet_iteration
                    and accounting_stable
                    and identities_reconciled
                    and events_reconciled
                ):
                    stable_zero_observations += 1
                    last_reconciliation_reason = "stable_zero_candidate"
                else:
                    stable_zero_observations = 0
                    last_reconciliation_reason = "completion_or_accounting_not_stable"

                if stable_zero_observations >= required_stable_zero_observations:
                    active_zero = True
                    residual_state = "zero"
                    completion_accounting_reconciled = True
                    completion_event_sequence_complete = True
                    add_event(
                        "job_active_process_zero_reconciled",
                        stable_observations=stable_zero_observations,
                        total_processes=total_processes,
                    )
                    break
                if self._clock() >= reconciliation_deadline:
                    manual = True
                    if not identities_reconciled:
                        errors.append("completion_identity_accounting_mismatch")
                    if not events_reconciled:
                        errors.append("completion_event_accounting_mismatch")
                    if not accounting_stable or not quiet_iteration:
                        errors.append("completion_queue_not_stably_drained")
                    add_event(
                        "completion_reconciliation_deadline_elapsed",
                        reason=last_reconciliation_reason,
                    )
                    break
                self._sleep(poll_interval_seconds)
            if residual_pids:
                manual = True
                errors.append("residual_job_processes_after_bounded_repoll")
                add_event("residual_processes_observed", pids=list(residual_pids))
            return_code = api.exit_code(root_process)

            drain_deadline = self._clock() + self.contract.stream_drain_seconds
            while self._clock() < drain_deadline:
                if stdout_drained.is_set() and stderr_drained.is_set() and ack_drained.is_set():
                    break
                self._sleep(poll_interval_seconds)
            stdout_drained_on_time = (
                stdout_drained.is_set()
                and stdout_state.completed_monotonic is not None
                and stdout_state.completed_monotonic <= drain_deadline
            )
            stderr_drained_on_time = (
                stderr_drained.is_set()
                and stderr_state.completed_monotonic is not None
                and stderr_state.completed_monotonic <= drain_deadline
            )
            ack_drained_on_time = (
                ack_drained.is_set()
                and ack_state.completed_monotonic is not None
                and ack_state.completed_monotonic <= wrapper_deadline
            )
            stream_drain_within_deadline = (
                stdout_drained_on_time and stderr_drained_on_time and ack_drained_on_time
            )
            if not stdout_drained_on_time or not stderr_drained_on_time:
                manual = True
                errors.append("payload_streams_not_timely_drained_within_contract")
                if active_zero:
                    observation_continuity = "unobservable_manual_latch"
            if not ack_drained_on_time:
                manual = True
                errors.append("bootstrap_ack_stream_not_timely_drained_within_contract")
                if active_zero:
                    observation_continuity = "unobservable_manual_latch"
            if stdout_state.overflowed or stderr_state.overflowed:
                manual = True
                errors.append("stdout_or_stderr_capture_overflow")
        except Exception as exc:
            manual = root_pid is not None
            errors.append(f"runner_failure:{type(exc).__name__}")
            if control_write is not None:
                api.close(control_write)
                control_write = None
            if job is not None:
                try:
                    residual_pids = tuple(sorted(api.query_active_pids(job)))
                    active_pid_query_succeeded = True
                    active_zero = not residual_pids
                    residual_state = "zero" if active_zero else "nonzero"
                except Exception as query_exc:
                    residual_pids = ()
                    active_pid_query_succeeded = False
                    active_zero = False
                    residual_state = "unknown"
                    errors.append(f"residual_query_failed:{type(query_exc).__name__}")
        finally:
            nonce[:] = b"\0" * len(nonce)
            needs_observer = (
                root_pid is not None
                and job is not None
                and (not active_pid_query_succeeded or not active_zero)
            )
            if needs_observer:
                try:
                    observation_id = self._observation_registry.retain(
                        run_uuid=execution_uuid,
                        api=api,
                        job=job,
                        completion=completion,
                        root_process=root_process,
                        bootstrap_source_lease=bootstrap_source_lease,
                        bootstrap_r7s3_lease=bootstrap_r7s3_lease,
                    )
                    observation_continuity = "process_local_handle_registry"
                    job = None
                    completion = None
                    root_process = None
                    bootstrap_source_lease = None
                    bootstrap_r7s3_lease = None
                except Exception as observation_exc:
                    observation_continuity = "unobservable_manual_latch"
                    errors.append(
                        f"residual_observation_retention_failed:{type(observation_exc).__name__}"
                    )
            if bootstrap_source_lease is not None:
                bootstrap_source_lease.close()
                bootstrap_source_lease = None
            if bootstrap_r7s3_lease is not None:
                bootstrap_r7s3_lease.close()
                bootstrap_r7s3_lease = None
            for handle in (
                stdin_handle,
                stdout_write,
                stderr_write,
                ack_write,
                control_read,
                control_write,
                root_thread,
                payload_process,
                root_process,
                completion,
                job,
                stdout_read,
                stderr_read,
                ack_read,
            ):
                api.close(handle)

        drained = stdout_drained.is_set() and stderr_drained.is_set() and ack_drained.is_set()
        safe = (
            authorized
            and ack_valid
            and return_code == 0
            and active_zero
            and active_pid_query_succeeded
            and drained
            and stream_drain_within_deadline
            and completion_accounting_reconciled
            and completion_event_sequence_complete
            and not timed_out
            and not manual
            and not errors
        )
        return BootstrapOutcome(
            schema=OUTCOME_SCHEMA,
            name=name,
            run_uuid=execution_uuid,
            admission_id=admission_id,
            command=(),
            command_sha256=command_sha,
            bootstrap_sha256=bootstrap_sha,
            bootstrap_source_identity=bootstrap_source_identity,
            bootstrap_r7s3_identity=bootstrap_r7s3_identity,
            bootstrap_executable_identity=bootstrap_executable_identity,
            payload_executable_identity=(
                dict(ack["payload_executable_identity"]) if ack is not None else None
            ),
            bootstrap_pid=root_pid,
            payload_pid=int(ack["payload_pid"]) if ack is not None else None,
            ack=ack,
            ack_valid=ack_valid,
            payload_resume_authorized=authorized,
            return_code=return_code,
            timed_out=timed_out,
            manual_intervention_required=manual or not safe,
            residual_pids=residual_pids,
            stdout="",
            stderr="",
            stdout_captured_sha256=hashlib.sha256(bytes(stdout_data)).hexdigest(),
            stderr_captured_sha256=hashlib.sha256(bytes(stderr_data)).hexdigest(),
            stdout_bytes_observed=stdout_state.bytes_observed,
            stderr_bytes_observed=stderr_state.bytes_observed,
            stdout_capture_overflow=stdout_state.overflowed,
            stderr_capture_overflow=stderr_state.overflowed,
            stream_capture_limit_bytes=self._stream_capture_limit_bytes,
            output_redaction_policy="raw_command_and_stream_content_omitted",
            stdout_drained=stdout_drained.is_set(),
            stderr_drained=stderr_drained.is_set(),
            ack_drained=ack_drained.is_set(),
            stdout_drained_monotonic=stdout_state.completed_monotonic,
            stderr_drained_monotonic=stderr_state.completed_monotonic,
            ack_drained_monotonic=ack_state.completed_monotonic,
            stream_drain_within_deadline=stream_drain_within_deadline,
            ack_overflow=ack_overflow,
            ack_timeout=ack_timeout,
            active_process_zero=active_zero,
            active_pid_query_succeeded=active_pid_query_succeeded,
            residual_state=residual_state,
            completion_accounting_reconciled=completion_accounting_reconciled,
            completion_event_sequence_complete=completion_event_sequence_complete,
            stable_zero_observations=stable_zero_observations,
            final_job_accounting=final_job_accounting,
            observed_process_identity_count=len(identities),
            observation_continuity=observation_continuity,
            observation_id=observation_id,
            process_identities=tuple(
                sorted(identities.values(), key=lambda item: item["observed_sequence"])
            ),
            process_events=tuple(process_events),
            safe_for_followup=safe,
            forced_termination_attempts=0,
            errors=tuple(errors),
        )


def _bootstrap_main() -> int:
    try:
        return run_bootstrap_child()
    except Exception as exc:
        # Error types/messages contain protocol labels only; private environment
        # values and the raw capability nonce are never rendered.
        print(f"r7s4_bootstrap_failed:{type(exc).__name__}", file=sys.stderr)
        return 125


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="r7s4 local process bootstrap")
    parser.add_argument("--bootstrap", action="store_true")
    args = parser.parse_args(argv)
    if not args.bootstrap:
        parser.error("this versioned module only exposes --bootstrap")
    return _bootstrap_main()


__all__ = [
    "ACK_HANDLE_ENV",
    "ACK_SCHEMA",
    "ADMISSION_ID_ENV",
    "BOOTSTRAP_R7S3_IDENTITY_ENV",
    "BOOTSTRAP_SHA256_ENV",
    "BOOTSTRAP_SOURCE_IDENTITY_ENV",
    "BootstrapAckExpectation",
    "BootstrapOutcome",
    "COMMAND_DIGEST_ENV",
    "CONTROL_HANDLE_ENV",
    "ExecutableLease",
    "JobCapabilityLease",
    "LocalResidualObservationRegistry",
    "OUTCOME_SCHEMA",
    "PAYLOAD_ENVELOPE_ENV",
    "R7S4_LOCAL_PROCESS_CONTRACT",
    "R7S4ProcessError",
    "RESIDUAL_OBSERVATIONS",
    "ResidualObservationLease",
    "StreamCaptureState",
    "SuspendedProcess",
    "WindowsBootstrapProcessRunner",
    "acquire_job_capability_lease",
    "main",
    "normalized_command_digest",
    "run_bootstrap_child",
    "validate_bootstrap_ack",
]


if __name__ == "__main__":
    raise SystemExit(main())
