"""Race-resistant process containment for the X1 Phase B2 r7 harness.

The Windows runner creates the root process suspended and atomically assigns it
to a new Job Object through ``PROC_THREAD_ATTRIBUTE_JOB_LIST``.  The process is
resumed only after membership and the absence of Job limit flags have been
read back.  A timeout is deliberately a latch, not a request to terminate the
Job: the runner performs a bounded residual wait and reports any live members
for manual intervention.

The module intentionally contains no process-termination path.  Closing the
Job handle is harmless because no terminating Job limit is configured.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence


RUN_UUID_ENV = "EVM_PHASE_B2_RUN_UUID"
JOB_CAPABILITY_HANDLE_ENV = "EVM_PHASE_B2_JOB_CAPABILITY_HANDLE"
JOB_CAPABILITY_NONCE_ENV = "EVM_PHASE_B2_JOB_CAPABILITY_NONCE"
JOB_CAPABILITY_COMMITMENT_ENV = "EVM_PHASE_B2_JOB_CAPABILITY_COMMITMENT"
JOB_CAPABILITY_NONCE_BYTES = 32
DEFAULT_MAX_STREAM_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_COMPLETION_EVENTS_PER_DRAIN = 256
DEFAULT_MAX_ACCOUNTING_COHERENCE_ATTEMPTS = 8
DEFAULT_MAX_JOB_EVENTS = 16_384
DEFAULT_MAX_PROCESS_IDENTITIES = 4_096
DEFAULT_MAX_ACCOUNTING_SNAPSHOTS = 4_096
DEFAULT_MAX_ERROR_RECORDS = 1_024
DEFAULT_MAX_WSL_SCAN_PAYLOAD_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_WSL_SCAN_RECORDS = 4_096
DEFAULT_MAX_WSL_PROCESSES_EXAMINED = 131_072
# Job completion messages for a console host can arrive and become stale in
# substantially less than the former 25 ms default.  A missed PID remains a
# fail-closed identity-coverage error, but the runner must service the kernel
# queue promptly enough for ordinary short-lived descendants to be observed.
DEFAULT_PROCESS_POLL_INTERVAL_SECONDS = 0.001
JOB_CAPABILITY_QUERY_ACCESS = 0x00020004  # READ_CONTROL | JOB_OBJECT_QUERY
_JOB_CAPABILITY_DOMAIN = b"evm.phase-b2.windows-job-capability.v1\0"
_JOB_CAPABILITY_REDACTION = "<redacted-job-capability-nonce>"


class ProcessContainmentError(RuntimeError):
    """Raised when kernel-backed containment cannot be established safely."""


class _EvidenceLimitExceeded(ProcessContainmentError):
    """Raised once bounded process evidence cannot accept another record."""


class _PreKernelCreateGateError(ProcessContainmentError):
    """Raised by the last cancel/budget gate before the kernel create call."""


class _KernelCreateError(ProcessContainmentError):
    """Raised after a passed pre-kernel gate when CreateProcessW itself fails."""


class _KernelCreateAftermathError(_KernelCreateError):
    """Raised when the kernel may have created a child before control was interrupted."""


class ProcessContainmentFailure(ProcessContainmentError):
    """Fail-closed evidence for a containment setup or observation failure.

    A caller must treat this exception exactly like an unsafe outcome: no
    follow-up probe is permitted.  ``child_created`` distinguishes a setup
    failure that happened before ``CreateProcessW`` from a failure where a
    suspended or resumed process may still exist.  No process-control action is
    attempted while constructing this evidence.
    """

    def __init__(
        self,
        message: str,
        *,
        name: str,
        stage: str,
        run_uuid: str,
        root_pid: int | None,
        child_created: bool,
        job_membership_verified: bool,
        root_resumed: bool,
        residual_pids: Sequence[int],
        stdout: str = "",
        stderr: str = "",
        stdout_drained: bool = False,
        stderr_drained: bool = False,
        events: Sequence[JobEvent] = (),
        identities: Sequence[ProcessIdentity] = (),
        accounting: Sequence[JobAccountingSnapshot] = (),
        errors: Sequence[str] = (),
        cause_type: str = "ProcessContainmentError",
        executable_identity: Mapping[str, Any] | None = None,
        stdout_total_bytes: int = 0,
        stderr_total_bytes: int = 0,
        stdout_capture_overflow: bool = False,
        stderr_capture_overflow: bool = False,
        stream_capture_limit_bytes: int = DEFAULT_MAX_STREAM_BYTES,
        stream_cleanup: Mapping[str, Any] | None = None,
        timed_out: bool = False,
        cancelled: bool = False,
        started_at_utc: str | None = None,
        ended_at_utc: str | None = None,
        duration_seconds: float = 0.0,
        restore_deadline_seconds: float | None = None,
        restore_deadline_exhausted: bool = False,
    ) -> None:
        super().__init__(message)
        self.name = name
        self.stage = stage
        self.run_uuid = run_uuid
        self.root_pid = root_pid
        self.child_created = child_created
        self.no_child_created = not child_created
        self.job_membership_verified = job_membership_verified
        self.root_resumed = root_resumed
        self.timed_out = timed_out
        self.cancelled = cancelled
        self.started_at_utc = started_at_utc
        self.ended_at_utc = ended_at_utc
        self.duration_seconds = duration_seconds
        self.restore_deadline_seconds = restore_deadline_seconds
        self.restore_deadline_exhausted = restore_deadline_exhausted
        self.return_code = None
        self.manual_intervention_required = child_created
        self.residual_pids = tuple(sorted({int(pid) for pid in residual_pids if int(pid) > 0}))
        self.stdout = stdout
        self.stderr = stderr
        self.stdout_drained = stdout_drained
        self.stderr_drained = stderr_drained
        self.streams_drained = stdout_drained and stderr_drained
        self.events = tuple(events)
        self.identities = tuple(identities)
        self.accounting = tuple(accounting)
        self.errors = tuple(errors)
        self.cause_type = cause_type
        self.executable_identity = dict(executable_identity or {})
        self.stdout_total_bytes = stdout_total_bytes
        self.stderr_total_bytes = stderr_total_bytes
        self.stdout_capture_overflow = stdout_capture_overflow
        self.stderr_capture_overflow = stderr_capture_overflow
        self.stream_capture_limit_bytes = stream_capture_limit_bytes
        self.stream_cleanup = dict(stream_cleanup or {})
        self.safe_for_followup = False
        self.forced_termination_attempts = 0

    @property
    def process_evidence(self) -> dict[str, Any]:
        return {
            "root_pid": self.root_pid,
            "child_created": self.child_created,
            "no_child_created": self.no_child_created,
            "job_membership_verified": self.job_membership_verified,
            "root_resumed": self.root_resumed,
            "residual_pids": list(self.residual_pids),
            "stdout_drained": self.stdout_drained,
            "stderr_drained": self.stderr_drained,
            "events": [asdict(value) for value in self.events],
            "identities": [asdict(value) for value in self.identities],
            "accounting": [asdict(value) for value in self.accounting],
            "executable_identity": self.executable_identity,
            "stdout_total_bytes": self.stdout_total_bytes,
            "stderr_total_bytes": self.stderr_total_bytes,
            "stdout_capture_overflow": self.stdout_capture_overflow,
            "stderr_capture_overflow": self.stderr_capture_overflow,
            "stream_capture_limit_bytes": self.stream_capture_limit_bytes,
            "stream_cleanup": self.stream_cleanup,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "cause_type": self.cause_type,
            "name": self.name,
            "stage": self.stage,
            "run_uuid": self.run_uuid,
            "started_at_utc": self.started_at_utc,
            "ended_at_utc": self.ended_at_utc,
            "duration_seconds": self.duration_seconds,
            "restore_deadline_seconds": self.restore_deadline_seconds,
            "restore_deadline_exhausted": self.restore_deadline_exhausted,
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
            "return_code": self.return_code,
            "manual_intervention_required": self.manual_intervention_required,
            "safe_for_followup": self.safe_for_followup,
            "forced_termination_attempts": self.forced_termination_attempts,
            "residual_pids": list(self.residual_pids),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "streams_drained": self.streams_drained,
            "errors": list(self.errors),
            "process_evidence": self.process_evidence,
        }


@dataclass(frozen=True)
class TimeoutContract:
    """Timeouts shared with the r7 restore-only orchestrator."""

    kubectl_timeout_seconds: float = 8.0
    wrapper_timeout_seconds: float = 15.0
    restore_deadline_seconds: float = 600.0
    residual_repoll_seconds: float = 120.0
    stream_drain_seconds: float = 5.0

    def __post_init__(self) -> None:
        values = {
            "kubectl_timeout_seconds": self.kubectl_timeout_seconds,
            "wrapper_timeout_seconds": self.wrapper_timeout_seconds,
            "restore_deadline_seconds": self.restore_deadline_seconds,
            "residual_repoll_seconds": self.residual_repoll_seconds,
            "stream_drain_seconds": self.stream_drain_seconds,
        }
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            for value in values.values()
        ):
            raise ValueError(f"timeout values must be positive: {values}")
        if not (
            self.kubectl_timeout_seconds
            < self.wrapper_timeout_seconds
            < self.restore_deadline_seconds
        ):
            raise ValueError(
                "timeout order must be kubectl_timeout_seconds < "
                "wrapper_timeout_seconds < restore_deadline_seconds"
            )


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    ppid: int | None
    creation_time_ns: int
    creation_time_utc: str
    image: str
    run_uuid: str
    observed_sequence: int

    @property
    def stable_key(self) -> tuple[int, int]:
        """A PID-reuse-safe process key."""

        return (self.pid, self.creation_time_ns)


@dataclass(frozen=True)
class JobEvent:
    sequence: int
    event: str
    monotonic_ns: int
    timestamp_utc: str
    pid: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JobAccountingSnapshot:
    sequence: int
    monotonic_ns: int
    timestamp_utc: str
    total_processes: int
    active_processes: int
    total_terminated_processes: int
    active_pids: tuple[int, ...]


@dataclass(frozen=True)
class ProcessOutcome:
    name: str
    run_uuid: str
    command: tuple[str, ...]
    started_at_utc: str
    ended_at_utc: str
    duration_seconds: float
    timed_out: bool
    cancelled: bool
    return_code: int | None
    manual_intervention_required: bool
    residual_pids: tuple[int, ...]
    stdout: str
    stderr: str
    stdout_drained: bool
    stderr_drained: bool
    streams_drained: bool
    active_process_zero: bool
    final_active_process_count: int
    identity_coverage_complete: bool
    safe_for_followup: bool
    forced_termination_attempts: int
    job_limit_flags: int
    identities: tuple[ProcessIdentity, ...]
    events: tuple[JobEvent, ...]
    accounting: tuple[JobAccountingSnapshot, ...]
    errors: tuple[str, ...] = ()
    executable_identity: dict[str, Any] = field(default_factory=dict)
    stdout_total_bytes: int = 0
    stderr_total_bytes: int = 0
    stdout_capture_overflow: bool = False
    stderr_capture_overflow: bool = False
    stream_capture_limit_bytes: int = DEFAULT_MAX_STREAM_BYTES
    stream_cleanup: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-serialisable evidence object."""

        return asdict(self)


def identity_coverage_complete(total_processes: int, identities: Sequence[ProcessIdentity]) -> bool:
    """Require exact coverage using PID plus creation time, never PID alone."""

    stable_keys = {identity.stable_key for identity in identities}
    observed_sequences = {identity.observed_sequence for identity in identities}
    normalized_run_uuids: set[str] = set()
    for identity in identities:
        try:
            parsed_run_uuid = uuid.UUID(identity.run_uuid)
            normalized_run_uuid = str(parsed_run_uuid)
            created_at = datetime.fromisoformat(identity.creation_time_utc)
        except (ValueError, TypeError):
            return False
        if (
            parsed_run_uuid.version != 4
            or identity.run_uuid != normalized_run_uuid
            or created_at.tzinfo is None
        ):
            return False
        normalized_run_uuids.add(normalized_run_uuid)
    return (
        total_processes >= 1
        and len(stable_keys) == total_processes
        and len(observed_sequences) == total_processes
        and len(normalized_run_uuids) == 1
        and all(
            identity.pid > 0
            and identity.ppid is not None
            and identity.ppid >= 0
            and identity.creation_time_ns > 0
            and bool(identity.image.strip())
            and identity.observed_sequence > 0
            for identity in identities
        )
    )


class _BoundedStreamCapture:
    """Drain a stream fully while retaining at most a fixed number of bytes."""

    def __init__(self, maximum_bytes: int) -> None:
        if isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int):
            raise ValueError("maximum stream bytes must be an integer")
        if maximum_bytes <= 0:
            raise ValueError("maximum stream bytes must be positive")
        self.maximum_bytes = maximum_bytes
        self._captured = bytearray()
        self._total_bytes = 0
        self._overflow = False
        self._lock = threading.Lock()

    def extend(self, value: bytes | bytearray | memoryview) -> None:
        raw = bytes(value)
        with self._lock:
            self._total_bytes += len(raw)
            remaining = self.maximum_bytes - len(self._captured)
            if remaining > 0:
                self._captured.extend(raw[:remaining])
            if len(raw) > remaining:
                self._overflow = True

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    @property
    def overflow(self) -> bool:
        with self._lock:
            return self._overflow

    def __bytes__(self) -> bytes:
        with self._lock:
            return bytes(self._captured)


@dataclass(frozen=True)
class LinuxProcStat:
    pid: int
    comm: str
    state: str
    ppid: int
    pgrp: int
    session: int
    start_time_ticks: int


@dataclass(frozen=True)
class WslProcessIdentity:
    pid: int
    ppid: int
    pgrp: int
    session: int
    start_time_ticks: int
    boot_id: str
    run_uuid_match: bool
    process_group_match: bool
    cmdline_sha256: str | None
    environ_readable: bool = True
    cmdline_readable: bool = True

    @property
    def stable_key(self) -> tuple[str, int, int]:
        return (self.boot_id, self.pid, self.start_time_ticks)


def parse_linux_proc_stat(text: str) -> LinuxProcStat:
    """Parse ``/proc/<pid>/stat`` without being confused by ')' in comm."""

    left = text.find("(")
    right = text.rfind(")")
    if left <= 0 or right <= left:
        raise ValueError("invalid /proc stat record")
    pid = int(text[:left].strip())
    comm = text[left + 1 : right]
    fields = text[right + 1 :].strip().split()
    # fields starts at kernel stat field 3 (state); starttime is field 22.
    if len(fields) < 20:
        raise ValueError("truncated /proc stat record")
    return LinuxProcStat(
        pid=pid,
        comm=comm,
        state=fields[0],
        ppid=int(fields[1]),
        pgrp=int(fields[2]),
        session=int(fields[3]),
        start_time_ticks=int(fields[19]),
    )


def _normalise_uuid(value: str | uuid.UUID) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"invalid run UUID: {value!r}") from exc


def _normalise_job_capability_nonce(value: bytes | bytearray | memoryview | str) -> bytes:
    if isinstance(value, str):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ProcessContainmentError("job capability nonce encoding is not canonical")
        raw = bytes.fromhex(value)
    elif isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
    else:
        raise ProcessContainmentError("job capability nonce must be bytes or lower-hex text")
    if len(raw) != JOB_CAPABILITY_NONCE_BYTES:
        raise ProcessContainmentError("job capability nonce must contain exactly 32 bytes")
    return raw


def job_capability_commitment(
    nonce: bytes | bytearray | memoryview | str,
    run_uuid: str | uuid.UUID,
) -> str:
    """Return the domain-separated public commitment for a private nonce.

    The caller may publish this digest.  The raw nonce must remain confined to
    the one child environment and is deliberately absent from process outcome
    objects and Job event details.
    """

    normalized_uuid = _normalise_uuid(run_uuid)
    raw = _normalise_job_capability_nonce(nonce)
    return hashlib.sha256(
        _JOB_CAPABILITY_DOMAIN + normalized_uuid.encode("ascii") + b"\0" + raw
    ).hexdigest()


def _fresh_job_capability_nonce() -> bytearray:
    raw = os.urandom(JOB_CAPABILITY_NONCE_BYTES)
    if not isinstance(raw, bytes) or len(raw) != JOB_CAPABILITY_NONCE_BYTES:
        raise ProcessContainmentError("OS nonce generator returned an invalid capability nonce")
    return bytearray(raw)


def _clear_capability_environment(environment: MutableMapping[str, str]) -> None:
    protected = {
        JOB_CAPABILITY_HANDLE_ENV,
        JOB_CAPABILITY_NONCE_ENV,
        JOB_CAPABILITY_COMMITMENT_ENV,
    }
    for key in tuple(environment):
        if str(key).upper() in protected:
            del environment[key]


def _consume_environment_value(environment: MutableMapping[str, str], name: str) -> str:
    matches = [key for key in tuple(environment) if str(key).upper() == name]
    values = [str(environment[key]) for key in matches]
    for key in matches:
        del environment[key]
    if len(values) != 1:
        raise ProcessContainmentError(f"job capability environment field invalid: {name}")
    return values[0]


def _redact_job_capability_nonce(value: str, nonce_hex: str) -> str:
    return value.replace(nonce_hex, _JOB_CAPABILITY_REDACTION).replace(
        nonce_hex.upper(), _JOB_CAPABILITY_REDACTION
    )


def _validated_executable_identity(value: str | os.PathLike[str]) -> dict[str, Any]:
    raw_path = os.fspath(value)
    if not isinstance(raw_path, str) or not raw_path or "\0" in raw_path:
        raise ProcessContainmentError("application path is invalid")
    if not os.path.isabs(raw_path):
        raise ProcessContainmentError("application path must be absolute")
    absolute = os.path.normpath(os.path.abspath(raw_path))
    chain: list[str] = []
    cursor = absolute
    while True:
        chain.append(cursor)
        parent = os.path.dirname(cursor)
        if parent == cursor:
            break
        cursor = parent
    leaf_status: os.stat_result | None = None
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for item in reversed(chain):
        try:
            measured = os.lstat(item)
        except OSError as exc:
            raise ProcessContainmentError(f"application path component unreadable: {item}") from exc
        attributes = int(getattr(measured, "st_file_attributes", 0))
        if stat.S_ISLNK(measured.st_mode) or attributes & reparse_flag:
            raise ProcessContainmentError(f"application reparse component forbidden: {item}")
        if item == absolute:
            leaf_status = measured
    if leaf_status is None or not stat.S_ISREG(leaf_status.st_mode):
        raise ProcessContainmentError("application must be a regular readable file")

    descriptor = os.open(absolute, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        opened_status = os.fstat(descriptor)
        identity_before = (
            int(opened_status.st_dev),
            int(opened_status.st_ino),
            int(opened_status.st_size),
            int(opened_status.st_mtime_ns),
        )
        path_identity = (
            int(leaf_status.st_dev),
            int(leaf_status.st_ino),
            int(leaf_status.st_size),
            int(leaf_status.st_mtime_ns),
        )
        if not stat.S_ISREG(opened_status.st_mode) or identity_before != path_identity:
            raise ProcessContainmentError("application identity changed while opening")
        digest = hashlib.sha256()
        measured_bytes = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            measured_bytes += len(chunk)
        opened_after = os.fstat(descriptor)
        identity_after = (
            int(opened_after.st_dev),
            int(opened_after.st_ino),
            int(opened_after.st_size),
            int(opened_after.st_mtime_ns),
        )
        if identity_after != identity_before or measured_bytes != int(opened_status.st_size):
            raise ProcessContainmentError("application identity changed while reading")
    finally:
        os.close(descriptor)
    return {
        "path": absolute,
        "sha256": digest.hexdigest(),
        "bytes": measured_bytes,
        "device": identity_before[0],
        "file_id": identity_before[1],
    }


class WslResidualProtocol:
    """Build and validate the WSL UUID/process-group residual protocol.

    The scanner emits only process metadata and a command-line digest.  It
    never emits environment values or raw command lines.  UUID inheritance is
    primary; boot-id/process-group/start-time matching is the conservative
    fallback for descendants that have rewritten their environment.
    """

    def __init__(
        self,
        run_uuid: str | uuid.UUID,
        *,
        root_process_group: int | None = None,
        root_start_time_ticks: int | None = None,
        boot_id: str | None = None,
    ) -> None:
        self.run_uuid = _normalise_uuid(run_uuid)
        if root_process_group is not None and (
            type(root_process_group) is not int or root_process_group <= 0
        ):
            raise ValueError("root_process_group must be a positive integer")
        if root_start_time_ticks is not None and (
            type(root_start_time_ticks) is not int or root_start_time_ticks <= 0
        ):
            raise ValueError("root_start_time_ticks must be a positive integer")
        self.root_process_group = root_process_group
        self.root_start_time_ticks = root_start_time_ticks
        if boot_id:
            normalized_boot_id = str(uuid.UUID(boot_id.strip()))
            if normalized_boot_id != boot_id.strip():
                raise ValueError("boot_id must be canonical UUID text")
            self.boot_id = normalized_boot_id
        else:
            self.boot_id = None
        self.scan_nonce = str(uuid.uuid4())
        self._scan_payload_consumed = False

    def launch_command(self, distribution: str, command: Sequence[str]) -> tuple[str, ...]:
        if not distribution or not command:
            raise ValueError("distribution and command are required")
        return (
            "wsl.exe",
            "--distribution",
            distribution,
            "--exec",
            "env",
            f"{RUN_UUID_ENV}={self.run_uuid}",
            "setsid",
            "--fork",
            "--wait",
            *(str(item) for item in command),
        )

    def record_from_proc(
        self,
        *,
        stat_text: str,
        environ: bytes,
        cmdline: bytes,
        boot_id: str,
    ) -> WslProcessIdentity:
        stat = parse_linux_proc_stat(stat_text)
        expected = f"{RUN_UUID_ENV}={self.run_uuid}".encode()
        env_entries = {entry for entry in environ.split(b"\0") if entry}
        uuid_match = expected in env_entries
        group_match = bool(
            self.boot_id
            and boot_id.strip() == self.boot_id
            and self.root_process_group is not None
            and stat.pgrp == self.root_process_group
            and self.root_start_time_ticks is not None
            and stat.start_time_ticks >= self.root_start_time_ticks
        )
        return WslProcessIdentity(
            pid=stat.pid,
            ppid=stat.ppid,
            pgrp=stat.pgrp,
            session=stat.session,
            start_time_ticks=stat.start_time_ticks,
            boot_id=boot_id.strip(),
            run_uuid_match=uuid_match,
            process_group_match=group_match,
            cmdline_sha256=hashlib.sha256(cmdline).hexdigest(),
        )

    @staticmethod
    def is_residual(record: WslProcessIdentity) -> bool:
        return record.run_uuid_match or record.process_group_match

    @staticmethod
    def scanner_python_source() -> str:
        """Return the self-contained, read-only Linux ``/proc`` scanner."""

        return r"""import hashlib
import json
import os
import pathlib
import sys

run_uuid, expected_pgrp, expected_start, expected_boot, scan_nonce = sys.argv[1:]
expected_pgrp_i = int(expected_pgrp) if expected_pgrp else None
expected_start_i = int(expected_start) if expected_start else None
boot = pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()
needle = ("EVM_PHASE_B2_RUN_UUID=" + run_uuid).encode()
records = []
processes_examined = 0
resource_limit_exceeded = False
vanished_during_scan = 0
stat_parse_errors = 0
unreadable_stat = 0
unreadable_environ = 0
unreadable_cmdline = 0
for proc in pathlib.Path("/proc").iterdir():
    if not proc.name.isdigit():
        continue
    if processes_examined >= 131072:
        resource_limit_exceeded = True
        break
    processes_examined += 1
    try:
        raw = (proc / "stat").read_text()
        right = raw.rfind(")")
        left = raw.find("(")
        if left <= 0 or right <= left:
            stat_parse_errors += 1
            continue
        pid = int(raw[:left].strip())
        fields = raw[right + 1:].strip().split()
        if len(fields) < 20:
            stat_parse_errors += 1
            continue
        ppid, pgrp, session, start = (
            int(fields[1]), int(fields[2]), int(fields[3]), int(fields[19])
        )
        group_match = bool(
            expected_boot and boot == expected_boot
            and expected_pgrp_i is not None and pgrp == expected_pgrp_i
            and expected_start_i is not None and start >= expected_start_i
        )
        environ_readable = True
        try:
            environ = (proc / "environ").read_bytes().split(b"\0")
        except (PermissionError, OSError):
            environ = []
            environ_readable = False
            unreadable_environ += 1
        uuid_match = needle in environ
        if uuid_match or group_match:
            if len(records) >= 4096:
                resource_limit_exceeded = True
                break
            cmdline_readable = True
            try:
                cmdline = (proc / "cmdline").read_bytes()
            except (PermissionError, OSError):
                cmdline = None
                cmdline_readable = False
                unreadable_cmdline += 1
            records.append({
                "pid": pid, "ppid": ppid, "pgrp": pgrp, "session": session,
                "start_time_ticks": start, "boot_id": boot,
                "run_uuid_match": uuid_match,
                "process_group_match": group_match,
                "cmdline_sha256": (
                    hashlib.sha256(cmdline).hexdigest() if cmdline is not None else None
                ),
                "environ_readable": environ_readable,
                "cmdline_readable": cmdline_readable,
            })
    except (FileNotFoundError, ProcessLookupError):
        vanished_during_scan += 1
        continue
    except PermissionError:
        unreadable_stat += 1
        continue
    except (OSError, ValueError):
        stat_parse_errors += 1
        continue
result = {
    "schema": "evm.phase-b2.wsl-residual-scan.v2",
    "run_uuid": run_uuid,
    "scan_nonce": scan_nonce,
    "boot_id": boot,
    "expected_process_group": expected_pgrp_i,
    "expected_start_time_ticks": expected_start_i,
    "expected_boot_id": expected_boot or None,
    "scan_complete": (
        stat_parse_errors == 0 and unreadable_stat == 0 and unreadable_environ == 0
        and unreadable_cmdline == 0 and not resource_limit_exceeded
    ),
    "resource_limit_exceeded": resource_limit_exceeded,
    "processes_examined": processes_examined,
    "vanished_during_scan": vanished_during_scan,
    "stat_parse_errors": stat_parse_errors,
    "unreadable_stat": unreadable_stat,
    "unreadable_environ": unreadable_environ,
    "unreadable_cmdline": unreadable_cmdline,
    "records": sorted(records, key=lambda row: (row["pid"], row["start_time_ticks"])),
}
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
"""

    def scan_command(self, distribution: str) -> tuple[str, ...]:
        if not distribution:
            raise ValueError("distribution is required")
        # Every dispatched scan invalidates any earlier response, including a
        # response from a prior poll in the same run/protocol instance.
        self.scan_nonce = str(uuid.uuid4())
        self._scan_payload_consumed = False
        return (
            "wsl.exe",
            "--distribution",
            distribution,
            "--exec",
            "/usr/bin/python3",
            "-I",
            "-S",
            "-P",
            "-B",
            "-c",
            self.scanner_python_source(),
            self.run_uuid,
            "" if self.root_process_group is None else str(self.root_process_group),
            "" if self.root_start_time_ticks is None else str(self.root_start_time_ticks),
            self.boot_id or "",
            self.scan_nonce,
        )

    def parse_scan_json(self, payload: str) -> tuple[WslProcessIdentity, ...]:
        if self._scan_payload_consumed:
            raise ValueError("WSL residual scan response was already consumed")
        if not isinstance(payload, str) or len(payload.encode("utf-8")) > (
            DEFAULT_MAX_WSL_SCAN_PAYLOAD_BYTES
        ):
            raise ValueError("WSL residual scan payload exceeds bounded size")
        result = json.loads(payload)
        result_keys = {
            "schema",
            "run_uuid",
            "scan_nonce",
            "boot_id",
            "expected_process_group",
            "expected_start_time_ticks",
            "expected_boot_id",
            "scan_complete",
            "resource_limit_exceeded",
            "processes_examined",
            "vanished_during_scan",
            "stat_parse_errors",
            "unreadable_stat",
            "unreadable_environ",
            "unreadable_cmdline",
            "records",
        }
        if not isinstance(result, dict) or set(result) != result_keys:
            raise ValueError("unexpected WSL residual scan envelope")
        if result["schema"] != "evm.phase-b2.wsl-residual-scan.v2":
            raise ValueError("unexpected WSL residual scan schema")
        if result["run_uuid"] != self.run_uuid or result["scan_nonce"] != self.scan_nonce:
            raise ValueError("WSL residual scan run identity or nonce mismatch")
        if (
            self.boot_id is None
            or self.root_process_group is None
            or self.root_start_time_ticks is None
        ):
            raise ValueError("WSL residual scan guard is missing boot/group/start identity")
        if (
            result["expected_process_group"] != self.root_process_group
            or result["expected_start_time_ticks"] != self.root_start_time_ticks
            or result["expected_boot_id"] != self.boot_id
        ):
            raise ValueError("WSL residual scan expected identity mismatch")
        boot_id = result["boot_id"]
        try:
            normalized_boot_id = str(uuid.UUID(boot_id)) if isinstance(boot_id, str) else None
        except ValueError as exc:
            raise ValueError("WSL residual scan boot ID must be canonical UUID text") from exc
        if normalized_boot_id != boot_id:
            raise ValueError("WSL residual scan boot ID must be canonical UUID text")
        if boot_id != self.boot_id:
            raise ValueError("WSL residual scan boot ID changed")
        if type(result["scan_complete"]) is not bool:
            raise ValueError("WSL residual scan completeness must be boolean")
        if type(result["resource_limit_exceeded"]) is not bool:
            raise ValueError("WSL residual scan resource-limit flag must be boolean")
        count_fields = (
            "processes_examined",
            "vanished_during_scan",
            "stat_parse_errors",
            "unreadable_stat",
            "unreadable_environ",
            "unreadable_cmdline",
        )
        if any(type(result[field]) is not int or result[field] < 0 for field in count_fields):
            raise ValueError("WSL residual scan counters must be non-negative integers")
        if (
            result["scan_complete"] is not True
            or result["resource_limit_exceeded"] is not False
            or any(
                result[field] != 0
                for field in (
                    "stat_parse_errors",
                    "unreadable_stat",
                    "unreadable_environ",
                    "unreadable_cmdline",
                )
            )
        ):
            raise ValueError("WSL residual scan is incomplete")
        rows = result["records"]
        if (
            not isinstance(rows, list)
            or result["processes_examined"] <= 0
            or result["processes_examined"] > DEFAULT_MAX_WSL_PROCESSES_EXAMINED
            or result["processes_examined"] < len(rows)
            or len(rows) > DEFAULT_MAX_WSL_SCAN_RECORDS
        ):
            raise ValueError("invalid WSL residual record count")
        required = {
            "pid",
            "ppid",
            "pgrp",
            "session",
            "start_time_ticks",
            "boot_id",
            "run_uuid_match",
            "process_group_match",
            "cmdline_sha256",
            "environ_readable",
            "cmdline_readable",
        }
        records: list[WslProcessIdentity] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != required:
                raise ValueError("unexpected WSL residual scan schema")
            numeric_fields = ("pid", "ppid", "pgrp", "session", "start_time_ticks")
            if any(type(row[field]) is not int for field in numeric_fields):
                raise ValueError("invalid WSL residual numeric metadata")
            if (
                row["pid"] <= 0
                or row["ppid"] < 0
                or row["pgrp"] < 0
                or row["session"] < 0
                or row["start_time_ticks"] <= 0
            ):
                raise ValueError("WSL residual numeric metadata outside valid range")
            if row["boot_id"] != boot_id:
                raise ValueError("WSL residual record boot ID mismatch")
            if (
                type(row["run_uuid_match"]) is not bool
                or type(row["process_group_match"]) is not bool
            ):
                raise ValueError("invalid WSL residual match flags")
            expected_group_match = bool(
                row["boot_id"] == self.boot_id
                and row["pgrp"] == self.root_process_group
                and row["start_time_ticks"] >= self.root_start_time_ticks
            )
            if row["process_group_match"] is not expected_group_match:
                raise ValueError("WSL residual process-group identity mismatch")
            if not row["run_uuid_match"] and not row["process_group_match"]:
                raise ValueError("non-matching process emitted by WSL residual scanner")
            environ_readable = row["environ_readable"]
            cmdline_readable = row["cmdline_readable"]
            if type(environ_readable) is not bool or type(cmdline_readable) is not bool:
                raise ValueError("invalid WSL residual metadata readability")
            digest_value = row["cmdline_sha256"]
            if digest_value is not None and not isinstance(digest_value, str):
                raise ValueError("invalid command-line digest type")
            digest = digest_value
            if cmdline_readable and not (digest and re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ValueError("invalid command-line digest")
            if not cmdline_readable and digest is not None:
                raise ValueError("unreadable command line must not have a digest")
            records.append(
                WslProcessIdentity(
                    pid=row["pid"],
                    ppid=row["ppid"],
                    pgrp=row["pgrp"],
                    session=row["session"],
                    start_time_ticks=row["start_time_ticks"],
                    boot_id=row["boot_id"],
                    run_uuid_match=row["run_uuid_match"],
                    process_group_match=row["process_group_match"],
                    cmdline_sha256=digest,
                    environ_readable=environ_readable,
                    cmdline_readable=cmdline_readable,
                )
            )
        self._scan_payload_consumed = True
        return tuple(records)


if sys.platform == "win32":
    from ctypes import wintypes

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [
            ("StartupInfo", _STARTUPINFOW),
            ("lpAttributeList", wintypes.LPVOID),
        ]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class _JOB_BASIC_ACCOUNTING(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _JOB_BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOB_ASSOCIATE_COMPLETION_PORT(ctypes.Structure):
        _fields_ = [("CompletionKey", wintypes.LPVOID), ("CompletionPort", wintypes.HANDLE)]

    class _PROCESS_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("Reserved1", wintypes.LPVOID),
            ("PebBaseAddress", wintypes.LPVOID),
            ("Reserved2", wintypes.LPVOID * 2),
            ("UniqueProcessId", ctypes.c_size_t),
            ("InheritedFromUniqueProcessId", ctypes.c_size_t),
        ]


class _WindowsJobApi:
    _JOB_ACCOUNTING = 1
    _JOB_LIMIT = 2
    _JOB_PID_LIST = 3
    _JOB_COMPLETION_PORT = 7

    _JOB_MESSAGE_ACTIVE_ZERO = 4
    _JOB_MESSAGE_NEW_PROCESS = 6
    _JOB_MESSAGE_EXIT_PROCESS = 7
    _JOB_MESSAGE_ABNORMAL_EXIT = 8

    _CREATE_SUSPENDED = 0x00000004
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    _CREATE_NO_WINDOW = 0x08000000
    _STARTF_USESTDHANDLES = 0x00000100
    _PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
    _PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D

    _HANDLE_FLAG_INHERIT = 0x00000001
    _GENERIC_READ = 0x80000000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_READ_ATTRIBUTES = 0x00000080
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x00001000
    _SYNCHRONIZE = 0x00100000
    _THREAD_TERMINATE = 0x0001
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 258
    _STILL_ACTIVE = 259
    _ERROR_BROKEN_PIPE = 109
    _ERROR_MORE_DATA = 234
    _ERROR_INSUFFICIENT_BUFFER = 122
    _ERROR_NOT_FOUND = 1168
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise ProcessContainmentError("Windows Job Objects require Windows")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.ntdll = ctypes.WinDLL("ntdll")
        self.last_application_identity: dict[str, Any] = {}
        self._declare_signatures()

    @staticmethod
    def _error(label: str) -> ProcessContainmentError:
        code = ctypes.get_last_error()
        return ProcessContainmentError(f"{label} failed with Win32 error {code}")

    def _declare_signatures(self) -> None:
        k32 = self.kernel32
        k32.CreateJobObjectW.argtypes = [ctypes.POINTER(_SECURITY_ATTRIBUTES), wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateIoCompletionPort.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.c_size_t,
            wintypes.DWORD,
        ]
        k32.CreateIoCompletionPort.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k32.QueryInformationJobObject.restype = wintypes.BOOL
        k32.InitializeProcThreadAttributeList.argtypes = [
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        k32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        k32.UpdateProcThreadAttribute.argtypes = [
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.c_size_t,
            wintypes.LPVOID,
            ctypes.c_size_t,
            wintypes.LPVOID,
            wintypes.LPVOID,
        ]
        k32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        k32.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
        k32.DeleteProcThreadAttributeList.restype = None
        k32.CreatePipe.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(_SECURITY_ATTRIBUTES),
            wintypes.DWORD,
        ]
        k32.CreatePipe.restype = wintypes.BOOL
        k32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
        k32.SetHandleInformation.restype = wintypes.BOOL
        k32.GetCurrentProcess.argtypes = []
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.DuplicateHandle.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        k32.DuplicateHandle.restype = wintypes.BOOL
        k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(_SECURITY_ATTRIBUTES),
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.POINTER(_SECURITY_ATTRIBUTES),
            ctypes.POINTER(_SECURITY_ATTRIBUTES),
            wintypes.BOOL,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPCWSTR,
            ctypes.POINTER(_STARTUPINFOW),
            ctypes.POINTER(_PROCESS_INFORMATION),
        ]
        k32.CreateProcessW.restype = wintypes.BOOL
        k32.IsProcessInJob.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.BOOL),
        ]
        k32.IsProcessInJob.restype = wintypes.BOOL
        k32.ResumeThread.argtypes = [wintypes.HANDLE]
        k32.ResumeThread.restype = wintypes.DWORD
        k32.GetQueuedCompletionStatus.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(wintypes.LPVOID),
            wintypes.DWORD,
        ]
        k32.GetQueuedCompletionStatus.restype = wintypes.BOOL
        k32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenThread.restype = wintypes.HANDLE
        k32.CancelSynchronousIo.argtypes = [wintypes.HANDLE]
        k32.CancelSynchronousIo.restype = wintypes.BOOL
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
        ]
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        k32.ReadFile.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        self.ntdll.NtQueryInformationProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.ULONG,
            ctypes.POINTER(wintypes.ULONG),
        ]
        self.ntdll.NtQueryInformationProcess.restype = ctypes.c_long

    def close(self, handle: int | None) -> None:
        if handle and handle != self._INVALID_HANDLE_VALUE:
            self.kernel32.CloseHandle(handle)

    def create_job_and_completion_port(self) -> tuple[int, int]:
        job = self.kernel32.CreateJobObjectW(None, None)
        if not job:
            raise self._error("CreateJobObjectW")
        completion = self.kernel32.CreateIoCompletionPort(
            ctypes.c_void_p(self._INVALID_HANDLE_VALUE), None, 0, 1
        )
        if not completion:
            self.close(job)
            raise self._error("CreateIoCompletionPort")
        association = _JOB_ASSOCIATE_COMPLETION_PORT(
            CompletionKey=ctypes.c_void_p(1), CompletionPort=completion
        )
        if not self.kernel32.SetInformationJobObject(
            job,
            self._JOB_COMPLETION_PORT,
            ctypes.byref(association),
            ctypes.sizeof(association),
        ):
            self.close(completion)
            self.close(job)
            raise self._error("SetInformationJobObject(completion port)")
        return job, completion

    def create_pipe(self) -> tuple[int, int]:
        security = _SECURITY_ATTRIBUTES(
            nLength=ctypes.sizeof(_SECURITY_ATTRIBUTES),
            lpSecurityDescriptor=None,
            bInheritHandle=True,
        )
        read_handle = wintypes.HANDLE()
        write_handle = wintypes.HANDLE()
        if not self.kernel32.CreatePipe(
            ctypes.byref(read_handle), ctypes.byref(write_handle), ctypes.byref(security), 0
        ):
            raise self._error("CreatePipe")
        if not self.kernel32.SetHandleInformation(read_handle, self._HANDLE_FLAG_INHERIT, 0):
            self.close(read_handle.value)
            self.close(write_handle.value)
            raise self._error("SetHandleInformation(pipe read)")
        return int(read_handle.value), int(write_handle.value)

    def open_inheritable_null(self) -> int:
        security = _SECURITY_ATTRIBUTES(
            nLength=ctypes.sizeof(_SECURITY_ATTRIBUTES),
            lpSecurityDescriptor=None,
            bInheritHandle=True,
        )
        handle = self.kernel32.CreateFileW(
            "NUL",
            self._GENERIC_READ,
            self._FILE_SHARE_READ | self._FILE_SHARE_WRITE,
            ctypes.byref(security),
            self._OPEN_EXISTING,
            self._FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if not handle or handle == self._INVALID_HANDLE_VALUE:
            raise self._error("CreateFileW(NUL)")
        return int(handle)

    def open_executable_lock(self, path: str) -> int:
        """Deny write/delete sharing from before hashing through CreateProcessW."""

        handle = self.kernel32.CreateFileW(
            path,
            self._GENERIC_READ,
            self._FILE_SHARE_READ,
            None,
            self._OPEN_EXISTING,
            self._FILE_ATTRIBUTE_NORMAL | self._FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if not handle or handle == self._INVALID_HANDLE_VALUE:
            raise self._error("CreateFileW(executable lock)")
        return int(handle)

    def open_directory_rename_lock(self, path: str) -> int:
        """Open one ancestor directory without delete sharing."""

        handle = self.kernel32.CreateFileW(
            path,
            self._FILE_READ_ATTRIBUTES,
            self._FILE_SHARE_READ | self._FILE_SHARE_WRITE,
            None,
            self._OPEN_EXISTING,
            self._FILE_FLAG_BACKUP_SEMANTICS | self._FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if not handle or handle == self._INVALID_HANDLE_VALUE:
            raise self._error("CreateFileW(executable ancestor lock)")
        return int(handle)

    def open_executable_path_locks(self, path: str) -> tuple[tuple[str, int], ...]:
        """Lock every renameable ancestor and the executable leaf."""

        absolute = Path(os.path.normpath(os.path.abspath(path)))
        ancestors: list[Path] = []
        cursor = absolute.parent
        while cursor.parent != cursor:
            ancestors.append(cursor)
            cursor = cursor.parent
        opened: list[tuple[str, int]] = []
        try:
            for ancestor in reversed(ancestors):
                opened.append((str(ancestor), self.open_directory_rename_lock(str(ancestor))))
            opened.append((str(absolute), self.open_executable_lock(str(absolute))))
            return tuple(opened)
        except BaseException:
            for _locked_path, handle in reversed(opened):
                self.close(handle)
            raise

    def cancel_synchronous_reader_io(self, native_thread_id: int) -> dict[str, Any]:
        """Cancel only a reader thread's blocking I/O; never control its child."""

        result: dict[str, Any] = {
            "native_thread_id": native_thread_id,
            "cancel_attempted": True,
            "cancel_succeeded": False,
            "no_pending_io": False,
            "error_code": None,
        }
        thread_handle = self.kernel32.OpenThread(self._THREAD_TERMINATE, False, native_thread_id)
        if not thread_handle:
            result["error_code"] = int(ctypes.get_last_error())
            return result
        try:
            if self.kernel32.CancelSynchronousIo(thread_handle):
                result["cancel_succeeded"] = True
                return result
            error_code = int(ctypes.get_last_error())
            result["error_code"] = error_code
            if error_code == self._ERROR_NOT_FOUND:
                result["no_pending_io"] = True
            return result
        finally:
            self.close(int(thread_handle))

    def duplicate_inheritable_job_capability(
        self,
        job: int,
        *,
        desired_access: int = JOB_CAPABILITY_QUERY_ACCESS,
    ) -> int:
        if (
            isinstance(desired_access, bool)
            or desired_access != JOB_CAPABILITY_QUERY_ACCESS
            or not isinstance(job, int)
            or job <= 0
        ):
            raise ProcessContainmentError("job capability access must be exact query-only access")
        duplicate = wintypes.HANDLE()
        current = self.kernel32.GetCurrentProcess()
        if not self.kernel32.DuplicateHandle(
            current,
            job,
            current,
            ctypes.byref(duplicate),
            desired_access,
            True,
            0,
        ):
            raise self._error("DuplicateHandle(job capability)")
        if not duplicate.value:
            raise ProcessContainmentError("job capability duplicate returned an invalid handle")
        return int(duplicate.value)

    def clear_handle_inherit(self, handle: int) -> None:
        if not self.kernel32.SetHandleInformation(handle, self._HANDLE_FLAG_INHERIT, 0):
            raise self._error("SetHandleInformation(job capability)")

    def initialise_attributes(
        self, job: int, inherited_handles: Sequence[int]
    ) -> tuple[ctypes.Array[Any], wintypes.LPVOID, Any, Any]:
        size = ctypes.c_size_t()
        self.kernel32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
        if ctypes.get_last_error() != self._ERROR_INSUFFICIENT_BUFFER or not size.value:
            raise self._error("InitializeProcThreadAttributeList(size)")
        storage = ctypes.create_string_buffer(size.value)
        attribute_list = ctypes.cast(storage, wintypes.LPVOID)
        if not self.kernel32.InitializeProcThreadAttributeList(
            attribute_list, 2, 0, ctypes.byref(size)
        ):
            raise self._error("InitializeProcThreadAttributeList")

        job_array_type = wintypes.HANDLE * 1
        job_array = job_array_type(job)
        if not self.kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            self._PROC_THREAD_ATTRIBUTE_JOB_LIST,
            ctypes.cast(job_array, wintypes.LPVOID),
            ctypes.sizeof(job_array),
            None,
            None,
        ):
            self.kernel32.DeleteProcThreadAttributeList(attribute_list)
            raise self._error("UpdateProcThreadAttribute(Job list)")

        handle_array_type = wintypes.HANDLE * len(inherited_handles)
        handle_array = handle_array_type(*inherited_handles)
        if not self.kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            self._PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.cast(handle_array, wintypes.LPVOID),
            ctypes.sizeof(handle_array),
            None,
            None,
        ):
            self.kernel32.DeleteProcThreadAttributeList(attribute_list)
            raise self._error("UpdateProcThreadAttribute(handle list)")
        return storage, attribute_list, job_array, handle_array

    def create_suspended_process(
        self,
        *,
        command: Sequence[str],
        cwd: str | None,
        environment: Mapping[str, str],
        job: int,
        stdin_handle: int,
        stdout_handle: int,
        stderr_handle: int,
        job_capability_nonce: bytes | bytearray | memoryview,
        job_capability_run_uuid: str,
        create_no_window: bool,
        expected_executable_sha256: str | None = None,
        pre_kernel_create_gate: Any | None = None,
    ) -> _PROCESS_INFORMATION:
        self.last_process_information: dict[str, int] = {}
        self.pending_process_information: _PROCESS_INFORMATION | None = None
        normalized_nonce = _normalise_job_capability_nonce(job_capability_nonce)
        normalized_uuid = _normalise_uuid(job_capability_run_uuid)
        capability_environment = dict(environment)
        _clear_capability_environment(capability_environment)
        job_capability_handle = self.duplicate_inheritable_job_capability(job)
        try:
            capability_environment[JOB_CAPABILITY_HANDLE_ENV] = str(job_capability_handle)
            capability_environment[JOB_CAPABILITY_NONCE_ENV] = normalized_nonce.hex()
            capability_environment[JOB_CAPABILITY_COMMITMENT_ENV] = job_capability_commitment(
                normalized_nonce, normalized_uuid
            )
            inherited_handles = (
                stdin_handle,
                stdout_handle,
                stderr_handle,
                job_capability_handle,
            )
            if any(
                isinstance(handle, bool) or not isinstance(handle, int) or handle <= 0
                for handle in inherited_handles
            ) or len(set(inherited_handles)) != len(inherited_handles):
                raise ProcessContainmentError("inherited handle roles must be positive and unique")
            _storage, attributes, _jobs, _handles = self.initialise_attributes(
                job, inherited_handles
            )
            try:
                startup = _STARTUPINFOEXW()
                startup.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
                startup.StartupInfo.dwFlags = self._STARTF_USESTDHANDLES
                startup.StartupInfo.hStdInput = stdin_handle
                startup.StartupInfo.hStdOutput = stdout_handle
                startup.StartupInfo.hStdError = stderr_handle
                startup.lpAttributeList = attributes
                info = _PROCESS_INFORMATION()
                command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(command)))
                env_pairs = [f"{key}={value}" for key, value in capability_environment.items()]
                env_block = ctypes.create_unicode_buffer(
                    "\0".join(sorted(env_pairs, key=str.casefold)) + "\0\0"
                )
                flags = (
                    self._CREATE_SUSPENDED
                    | self._CREATE_UNICODE_ENVIRONMENT
                    | self._EXTENDED_STARTUPINFO_PRESENT
                )
                if create_no_window:
                    flags |= self._CREATE_NO_WINDOW
                raw_application_path = os.fspath(command[0])
                if (
                    not isinstance(raw_application_path, str)
                    or not raw_application_path
                    or "\0" in raw_application_path
                    or not os.path.isabs(raw_application_path)
                ):
                    raise ProcessContainmentError(
                        "application path must be a non-empty absolute path"
                    )
                application_path = os.path.normpath(os.path.abspath(raw_application_path))
                executable_path_locks = self.open_executable_path_locks(application_path)
                try:
                    # This is deliberately the last filesystem measurement
                    # before the kernel launch call.  The no-write/no-delete
                    # shared handle remains open across CreateProcessW, closing
                    # the path replacement window after the digest is pinned.
                    application_identity = _validated_executable_identity(application_path)
                    self.last_application_identity = {
                        **application_identity,
                        "expected_sha256": expected_executable_sha256,
                        "pin_required": expected_executable_sha256 is not None,
                        "pin_match": (
                            application_identity["sha256"] == expected_executable_sha256
                            if expected_executable_sha256 is not None
                            else None
                        ),
                        "measurement_scope": "immediately_before_CreateProcessW",
                        "handle_lock_held_through_create": True,
                        "handle_lock_share_mode": "FILE_SHARE_READ_only",
                        "handle_lock_inheritable": False,
                        "ancestor_directory_locks_held_through_create": True,
                        "ancestor_directory_lock_count": len(executable_path_locks) - 1,
                        "ancestor_directory_lock_share_mode": ("FILE_SHARE_READ_WRITE_no_delete"),
                        "path_lock_scope": "all_nonroot_ancestors_and_leaf",
                        "pre_kernel_create_gate_required": pre_kernel_create_gate is not None,
                        "pre_kernel_create_gate_passed": False,
                    }
                    if expected_executable_sha256 is not None:
                        if not re.fullmatch(r"[0-9a-f]{64}", expected_executable_sha256):
                            raise ProcessContainmentError(
                                "expected executable SHA-256 must be canonical lower hex"
                            )
                        if application_identity["sha256"] != expected_executable_sha256:
                            raise ProcessContainmentError(
                                "executable SHA-256 changed before CreateProcessW"
                            )
                    startup_pointer = ctypes.cast(
                        ctypes.byref(startup), ctypes.POINTER(_STARTUPINFOW)
                    )
                    if pre_kernel_create_gate is not None:
                        # All capability, attribute-list, ancestor/leaf lock and
                        # full-file SHA work is complete.  Keep this callback as
                        # the last user-mode gate before the kernel create call.
                        pre_kernel_create_gate()
                        self.last_application_identity["pre_kernel_create_gate_passed"] = True
                    # Publish the zeroed structure to the caller before entering
                    # the kernel.  If an asynchronous BaseException lands at any
                    # later Python bytecode boundary, the caller can still read
                    # fields that CreateProcessW populated through this object.
                    self.pending_process_information = info
                    try:
                        created = self.kernel32.CreateProcessW(
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
                        )
                    except BaseException as exc:
                        if int(info.dwProcessId or 0) > 0 or int(info.hProcess or 0) > 0:
                            self.last_process_information = {
                                "process_handle": int(info.hProcess or 0),
                                "thread_handle": int(info.hThread or 0),
                                "pid": int(info.dwProcessId or 0),
                                "thread_id": int(info.dwThreadId or 0),
                            }
                            raise _KernelCreateAftermathError(
                                "CreateProcessW returned process information before interruption"
                            ) from exc
                        raise
                    if not created:
                        if int(info.dwProcessId or 0) > 0 or int(info.hProcess or 0) > 0:
                            self.last_process_information = {
                                "process_handle": int(info.hProcess or 0),
                                "thread_handle": int(info.hThread or 0),
                                "pid": int(info.dwProcessId or 0),
                                "thread_id": int(info.dwThreadId or 0),
                            }
                            raise _KernelCreateAftermathError(
                                "CreateProcessW reported failure with process information"
                            )
                        raise _KernelCreateError(str(self._error("CreateProcessW")))
                    self.last_process_information = {
                        "process_handle": int(info.hProcess or 0),
                        "thread_handle": int(info.hThread or 0),
                        "pid": int(info.dwProcessId or 0),
                        "thread_id": int(info.dwThreadId or 0),
                    }
                    return info
                finally:
                    for _locked_path, handle in reversed(executable_path_locks):
                        self.close(handle)
            finally:
                self.kernel32.DeleteProcThreadAttributeList(attributes)
        finally:
            _clear_capability_environment(capability_environment)
            self.close(job_capability_handle)

    def is_process_in_job(self, process: int, job: int | None) -> bool:
        result = wintypes.BOOL()
        if not self.kernel32.IsProcessInJob(process, job, ctypes.byref(result)):
            raise self._error("IsProcessInJob")
        return bool(result.value)

    def resume(self, thread: int) -> None:
        previous = self.kernel32.ResumeThread(thread)
        if previous == 0xFFFFFFFF:
            raise self._error("ResumeThread")

    def query_accounting(self, job: int | None) -> _JOB_BASIC_ACCOUNTING:
        value = _JOB_BASIC_ACCOUNTING()
        if not self.kernel32.QueryInformationJobObject(
            job, self._JOB_ACCOUNTING, ctypes.byref(value), ctypes.sizeof(value), None
        ):
            raise self._error("QueryInformationJobObject(accounting)")
        return value

    def query_limit_flags(self, job: int | None) -> int:
        value = _JOB_BASIC_LIMIT()
        if not self.kernel32.QueryInformationJobObject(
            job, self._JOB_LIMIT, ctypes.byref(value), ctypes.sizeof(value), None
        ):
            raise self._error("QueryInformationJobObject(limits)")
        return int(value.LimitFlags)

    def query_process_id_list(self, job: int | None) -> tuple[int, tuple[int, ...]]:
        size = 4096
        while size <= 1024 * 1024:
            buffer = ctypes.create_string_buffer(size)
            returned = wintypes.DWORD()
            ok = self.kernel32.QueryInformationJobObject(
                job, self._JOB_PID_LIST, buffer, size, ctypes.byref(returned)
            )
            if ok:
                assigned = wintypes.DWORD.from_buffer_copy(buffer.raw[0:4]).value
                listed = wintypes.DWORD.from_buffer_copy(buffer.raw[4:8]).value
                offset = 8
                step = ctypes.sizeof(ctypes.c_size_t)
                capacity = (size - offset) // step
                if listed > capacity or assigned < listed:
                    raise ProcessContainmentError("Job process id list count is invalid")
                required = offset + int(listed) * step
                if returned.value != required:
                    raise ProcessContainmentError(
                        "Job process id list returned-length mismatch: "
                        f"expected={required} actual={returned.value}"
                    )
                pids = tuple(
                    int(ctypes.c_size_t.from_buffer_copy(buffer.raw[offset + i * step :]).value)
                    for i in range(int(listed))
                )
                if any(pid <= 0 for pid in pids) or len(set(pids)) != len(pids):
                    raise ProcessContainmentError("Job process id list identity is invalid")
                return int(assigned), pids
            if ctypes.get_last_error() != self._ERROR_MORE_DATA:
                raise self._error("QueryInformationJobObject(process id list)")
            size *= 2
        raise ProcessContainmentError("Job process id list exceeded evidence buffer limit")

    def query_active_pids(self, job: int) -> tuple[int, ...]:
        # Preserve the established runner parser.  Capability consumption uses
        # query_process_id_list() above for the stricter assigned-count and
        # returned-length validation, without changing residual-wait behavior.
        size = 4096
        while size <= 1024 * 1024:
            buffer = ctypes.create_string_buffer(size)
            returned = wintypes.DWORD()
            ok = self.kernel32.QueryInformationJobObject(
                job, self._JOB_PID_LIST, buffer, size, ctypes.byref(returned)
            )
            if ok:
                listed = wintypes.DWORD.from_buffer_copy(buffer.raw[4:8]).value
                offset = 8
                step = ctypes.sizeof(ctypes.c_size_t)
                return tuple(
                    int(ctypes.c_size_t.from_buffer_copy(buffer.raw[offset + i * step :]).value)
                    for i in range(int(listed))
                )
            if ctypes.get_last_error() != self._ERROR_MORE_DATA:
                raise self._error("QueryInformationJobObject(process id list)")
            size *= 2
        raise ProcessContainmentError("Job process id list exceeded evidence buffer limit")

    def current_job_snapshot(self, job: int | None) -> dict[str, Any]:
        current_process = self.kernel32.GetCurrentProcess()
        accounting = self.query_accounting(job)
        assigned, pids = self.query_process_id_list(job)
        return {
            "is_process_in_job": self.is_process_in_job(current_process, job),
            "limit_flags": self.query_limit_flags(job),
            "active_processes": int(accounting.ActiveProcesses),
            "total_processes": int(accounting.TotalProcesses),
            "terminated_processes": int(accounting.TotalTerminatedProcesses),
            "assigned_processes": assigned,
            "process_ids": list(sorted(pids)),
        }

    def _completion_events(
        self,
        completion: int,
        *,
        first_wait_milliseconds: int,
    ) -> list[tuple[int, int | None]]:
        events: list[tuple[int, int | None]] = []
        first = True
        while len(events) < DEFAULT_MAX_COMPLETION_EVENTS_PER_DRAIN:
            message = wintypes.DWORD()
            key = ctypes.c_size_t()
            overlapped = wintypes.LPVOID()
            ok = self.kernel32.GetQueuedCompletionStatus(
                completion,
                ctypes.byref(message),
                ctypes.byref(key),
                ctypes.byref(overlapped),
                first_wait_milliseconds if first else 0,
            )
            first = False
            if not ok:
                if ctypes.get_last_error() == self._WAIT_TIMEOUT:
                    break
                raise self._error("GetQueuedCompletionStatus")
            pid = int(overlapped.value) if overlapped.value else None
            events.append((int(message.value), pid))
        return events

    def completion_events(self, completion: int) -> list[tuple[int, int | None]]:
        return self._completion_events(completion, first_wait_milliseconds=0)

    def wait_completion_events(
        self, completion: int, wait_milliseconds: int
    ) -> list[tuple[int, int | None]]:
        if isinstance(wait_milliseconds, bool) or not isinstance(wait_milliseconds, int):
            raise ValueError("completion wait milliseconds must be an integer")
        if wait_milliseconds < 0:
            raise ValueError("completion wait milliseconds must not be negative")
        return self._completion_events(
            completion,
            first_wait_milliseconds=wait_milliseconds,
        )

    def open_process(self, pid: int) -> int | None:
        handle = self.kernel32.OpenProcess(
            self._PROCESS_QUERY_LIMITED_INFORMATION | self._SYNCHRONIZE, False, pid
        )
        return int(handle) if handle else None

    def process_identity(
        self,
        process: int,
        *,
        pid: int,
        fallback_ppid: int | None,
        run_uuid: str,
        observed_sequence: int,
    ) -> ProcessIdentity:
        creation = _FILETIME()
        exit_time = _FILETIME()
        kernel = _FILETIME()
        user = _FILETIME()
        if not self.kernel32.GetProcessTimes(
            process,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise self._error("GetProcessTimes")
        windows_ticks = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        creation_ns = (windows_ticks - 116_444_736_000_000_000) * 100

        basic = _PROCESS_BASIC_INFORMATION()
        returned = wintypes.ULONG()
        status = self.ntdll.NtQueryInformationProcess(
            process,
            0,
            ctypes.byref(basic),
            ctypes.sizeof(basic),
            ctypes.byref(returned),
        )
        ppid = int(basic.InheritedFromUniqueProcessId) if status == 0 else fallback_ppid

        image_buffer = ctypes.create_unicode_buffer(32768)
        image_size = wintypes.DWORD(len(image_buffer))
        image = ""
        if self.kernel32.QueryFullProcessImageNameW(
            process, 0, image_buffer, ctypes.byref(image_size)
        ):
            image = image_buffer.value
        creation_utc = datetime.fromtimestamp(creation_ns / 1_000_000_000, UTC).isoformat()
        return ProcessIdentity(
            pid=pid,
            ppid=ppid,
            creation_time_ns=creation_ns,
            creation_time_utc=creation_utc,
            image=image,
            run_uuid=run_uuid,
            observed_sequence=observed_sequence,
        )

    def process_is_active(self, process: int) -> bool:
        return self.kernel32.WaitForSingleObject(process, 0) == self._WAIT_TIMEOUT

    def exit_code(self, process: int) -> int | None:
        code = wintypes.DWORD()
        if not self.kernel32.GetExitCodeProcess(process, ctypes.byref(code)):
            return None
        return None if code.value == self._STILL_ACTIVE else int(code.value)

    def read_pipe(self, read_handle: int, sink: bytearray, drained: threading.Event) -> None:
        try:
            while True:
                buffer = ctypes.create_string_buffer(65536)
                read = wintypes.DWORD()
                ok = self.kernel32.ReadFile(
                    read_handle,
                    buffer,
                    len(buffer),
                    ctypes.byref(read),
                    None,
                )
                if read.value:
                    sink.extend(buffer.raw[: read.value])
                if not ok:
                    if ctypes.get_last_error() == self._ERROR_BROKEN_PIPE:
                        drained.set()
                    return
                if read.value == 0:
                    drained.set()
                    return
        finally:
            self.close(read_handle)


def _validated_job_capability_snapshot(
    value: Mapping[str, Any], *, current_pid: int, label: str
) -> dict[str, Any]:
    expected_keys = {
        "is_process_in_job",
        "limit_flags",
        "active_processes",
        "total_processes",
        "terminated_processes",
        "assigned_processes",
        "process_ids",
    }
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        raise ProcessContainmentError(f"{label} Job snapshot schema mismatch")
    scalar_names = expected_keys - {"is_process_in_job", "process_ids"}
    if value["is_process_in_job"] is not True or any(
        isinstance(value[name], bool) or not isinstance(value[name], int) for name in scalar_names
    ):
        raise ProcessContainmentError(f"{label} Job snapshot type or membership mismatch")
    process_ids = value["process_ids"]
    if (
        not isinstance(process_ids, Sequence)
        or isinstance(process_ids, (str, bytes))
        or any(isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 for pid in process_ids)
        or list(process_ids) != sorted(set(process_ids))
    ):
        raise ProcessContainmentError(f"{label} Job process-id list is invalid")
    normalized = {
        "is_process_in_job": True,
        **{name: int(value[name]) for name in scalar_names},
        "process_ids": list(process_ids),
    }
    if any(normalized[name] < 0 for name in scalar_names):
        raise ProcessContainmentError(f"{label} Job snapshot contains a negative count")
    if not (
        normalized["limit_flags"] == 0
        and normalized["active_processes"] >= 1
        and normalized["total_processes"] >= normalized["active_processes"]
        and normalized["terminated_processes"] <= normalized["total_processes"]
        and normalized["assigned_processes"] == normalized["active_processes"]
        and len(normalized["process_ids"]) == normalized["active_processes"]
        and current_pid in normalized["process_ids"]
    ):
        raise ProcessContainmentError(
            f"{label} Job is not the no-limit current-process Job:"
            f"active={normalized['active_processes']}:"
            f"total={normalized['total_processes']}:"
            f"terminated={normalized['terminated_processes']}:"
            f"assigned={normalized['assigned_processes']}:"
            f"pids={normalized['process_ids']!r}:"
            f"limit_flags={normalized['limit_flags']}"
        )
    return normalized


def consume_inherited_job_capability(
    *,
    environment: MutableMapping[str, str] | None = None,
    api: Any | None = None,
) -> dict[str, Any]:
    """Consume and verify the one-shot inherited Job capability.

    Capability fields are deleted from the supplied environment before any
    kernel query.  The returned evidence includes only the nonce commitment;
    the raw nonce and its textual encoding are never returned.
    """

    if sys.platform != "win32":
        raise ProcessContainmentError("inherited Job capability requires Windows")
    child_environment = os.environ if environment is None else environment
    try:
        handle_text = _consume_environment_value(child_environment, JOB_CAPABILITY_HANDLE_ENV)
        if not re.fullmatch(r"[1-9][0-9]*", handle_text):
            raise ProcessContainmentError("job capability handle encoding is invalid")
        handle = int(handle_text)
        if handle > (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1:
            raise ProcessContainmentError("job capability handle is outside pointer range")
    except BaseException:
        _clear_capability_environment(child_environment)
        raise

    # Establish close ownership immediately after the handle encoding becomes
    # trustworthy.  Later capability-field extraction is deliberately inside
    # the handle-owned region so a missing/duplicate nonce or commitment cannot
    # orphan an inherited Job handle after the environment is scrubbed.
    try:
        runtime_api = _WindowsJobApi() if api is None else api
    except BaseException as api_exc:
        # Ownership begins as soon as the inherited handle has a validated
        # positive integer encoding.  Even API construction failure must not
        # leave the capability live for the child lifetime.
        try:
            fallback_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            fallback_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            fallback_kernel32.CloseHandle.restype = wintypes.BOOL
            closed = fallback_kernel32.CloseHandle(wintypes.HANDLE(handle))
        except BaseException as close_exc:
            raise ProcessContainmentError(
                "Job capability API initialization and fallback handle close failed"
            ) from close_exc
        if not closed:
            raise ProcessContainmentError(
                "Job capability API initialization failed and fallback handle close returned false"
            ) from api_exc
        raise
    nonce = bytearray()
    try:
        try:
            nonce_text = _consume_environment_value(child_environment, JOB_CAPABILITY_NONCE_ENV)
            expected_commitment = _consume_environment_value(
                child_environment, JOB_CAPABILITY_COMMITMENT_ENV
            )
        finally:
            _clear_capability_environment(child_environment)
        run_uuid_matches = [
            str(value)
            for key, value in child_environment.items()
            if str(key).upper() == RUN_UUID_ENV
        ]
        if len(run_uuid_matches) != 1:
            raise ProcessContainmentError("job capability run UUID environment is invalid")
        normalized_uuid = _normalise_uuid(run_uuid_matches[0])
        nonce.extend(_normalise_job_capability_nonce(nonce_text))
        commitment = job_capability_commitment(nonce, normalized_uuid)
        if expected_commitment != commitment:
            raise ProcessContainmentError("job capability nonce commitment mismatch")
        runtime_api.clear_handle_inherit(handle)
        current_pid = os.getpid()
        explicit = _validated_job_capability_snapshot(
            runtime_api.current_job_snapshot(handle),
            current_pid=current_pid,
            label="explicit",
        )
        implicit = _validated_job_capability_snapshot(
            runtime_api.current_job_snapshot(None),
            current_pid=current_pid,
            label="implicit",
        )
        if explicit != implicit:
            raise ProcessContainmentError(
                "explicit inherited Job does not match the current implicit Job"
            )
        return {
            "schema": "evm.phase-b2.windows-job-capability-consumption.v1",
            "run_uuid": normalized_uuid,
            "pid": current_pid,
            "requested_access": JOB_CAPABILITY_QUERY_ACCESS,
            "nonce_commitment": commitment,
            "explicit_job": explicit,
            "implicit_job": implicit,
            "snapshots_equal": True,
            "environment_consumed": True,
            "raw_nonce_recorded": False,
        }
    finally:
        nonce[:] = b"\0" * len(nonce)
        runtime_api.close(handle)


class WindowsJobProcessRunner:
    """Run one command inside a no-kill Windows Job Object containment gate."""

    def __init__(
        self,
        contract: TimeoutContract | None = None,
        *,
        clock: Any = time.monotonic,
        sleep: Any = time.sleep,
        utc_clock: Any | None = None,
    ) -> None:
        self.contract = contract or TimeoutContract()
        self._clock = clock
        self._sleep = sleep
        self._utc_clock = utc_clock or (lambda: datetime.now(UTC))

    def run(
        self,
        command: Sequence[str | os.PathLike[str]],
        name: str = "bounded-child",
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        *,
        poll_interval_seconds: float = DEFAULT_PROCESS_POLL_INTERVAL_SECONDS,
        cancel_event: Any | None = None,
        run_uuid: str | uuid.UUID | None = None,
        create_no_window: bool = True,
        expected_executable_sha256: str | None = None,
        max_stream_bytes: int = DEFAULT_MAX_STREAM_BYTES,
        max_job_events: int = DEFAULT_MAX_JOB_EVENTS,
        max_process_identities: int = DEFAULT_MAX_PROCESS_IDENTITIES,
        max_accounting_snapshots: int = DEFAULT_MAX_ACCOUNTING_SNAPSHOTS,
        max_error_records: int = DEFAULT_MAX_ERROR_RECORDS,
    ) -> ProcessOutcome:
        if sys.platform != "win32":
            raise ProcessContainmentError("WindowsJobProcessRunner requires Windows")
        if isinstance(command, (str, bytes)) or not command:
            raise ValueError("command must be a non-empty argument sequence")
        if not name:
            raise ValueError("name is required")
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or not math.isfinite(poll_interval_seconds)
            or poll_interval_seconds <= 0
        ):
            raise ValueError("poll_interval_seconds must be positive")
        if isinstance(max_stream_bytes, bool) or not isinstance(max_stream_bytes, int):
            raise ValueError("max_stream_bytes must be an integer")
        if max_stream_bytes <= 0:
            raise ValueError("max_stream_bytes must be positive")
        evidence_limits = {
            "max_job_events": max_job_events,
            "max_process_identities": max_process_identities,
            "max_accounting_snapshots": max_accounting_snapshots,
            "max_error_records": max_error_records,
        }
        if any(type(value) is not int or value <= 0 for value in evidence_limits.values()):
            raise ValueError(
                f"process evidence limits must be positive integers: {evidence_limits}"
            )
        if expected_executable_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", expected_executable_sha256
        ):
            raise ValueError("expected_executable_sha256 must be canonical lower hex")

        command_tuple = tuple(os.fspath(item) for item in command)
        execution_uuid = _normalise_uuid(run_uuid or uuid.uuid4())
        start_monotonic = self._clock()
        overall_deadline = start_monotonic + self.contract.restore_deadline_seconds
        cleanup_reserve_seconds = min(
            0.25,
            self.contract.stream_drain_seconds,
            (self.contract.restore_deadline_seconds - self.contract.wrapper_timeout_seconds) / 2.0,
        )
        post_process_reserve_seconds = self.contract.stream_drain_seconds + cleanup_reserve_seconds
        active_work_deadline = overall_deadline - post_process_reserve_seconds
        started_at = self._utc_clock().isoformat()
        child_env = dict(os.environ if env is None else env)
        _clear_capability_environment(child_env)
        for key in tuple(child_env):
            if key.upper() == RUN_UUID_ENV:
                del child_env[key]
        child_env[RUN_UUID_ENV] = execution_uuid
        child_env = {str(key): str(value) for key, value in child_env.items()}

        if cancel_event is not None and cancel_event.is_set():
            ended_at = self._utc_clock().isoformat()
            raise ProcessContainmentFailure(
                "containment cancelled before child creation",
                name=name,
                stage="pre_create_cancel_gate",
                run_uuid=execution_uuid,
                root_pid=None,
                child_created=False,
                job_membership_verified=False,
                root_resumed=False,
                residual_pids=(),
                errors=("cancelled_before_child_creation",),
                cause_type="CancellationRequested",
                stream_capture_limit_bytes=max_stream_bytes,
                cancelled=True,
                started_at_utc=started_at,
                ended_at_utc=ended_at,
                duration_seconds=max(0.0, self._clock() - start_monotonic),
                restore_deadline_seconds=self.contract.restore_deadline_seconds,
                restore_deadline_exhausted=False,
            )

        try:
            api = _WindowsJobApi()
        except BaseException as exc:
            ended_at = self._utc_clock().isoformat()
            elapsed = max(0.0, self._clock() - start_monotonic)
            deadline_exhausted = self._clock() >= overall_deadline
            raise ProcessContainmentFailure(
                f"containment failed before child creation: {exc}",
                name=name,
                stage="windows_api_initialization",
                run_uuid=execution_uuid,
                root_pid=None,
                child_created=False,
                job_membership_verified=False,
                root_resumed=False,
                residual_pids=(),
                errors=(str(exc),),
                cause_type=type(exc).__name__,
                stream_capture_limit_bytes=max_stream_bytes,
                timed_out=deadline_exhausted,
                started_at_utc=started_at,
                ended_at_utc=ended_at,
                duration_seconds=elapsed,
                restore_deadline_seconds=self.contract.restore_deadline_seconds,
                restore_deadline_exhausted=deadline_exhausted,
            ) from exc
        events: list[JobEvent] = []
        snapshots: list[JobAccountingSnapshot] = []
        identities: dict[tuple[int, int], ProcessIdentity] = {}
        process_handles: dict[tuple[int, int], int] = {}
        errors: list[str] = []
        error_set: set[str] = set()
        terminal_aux_errors: list[str] = []
        sequence = 0
        evidence_limit_error: str | None = None

        def timestamp() -> tuple[int, str]:
            return (time.monotonic_ns(), self._utc_clock().isoformat())

        def record_error(message: str) -> None:
            nonlocal evidence_limit_error, manual
            normalized = str(message)
            if normalized in error_set:
                return
            if len(errors) >= max_error_records:
                evidence_limit_error = f"error_record_limit_exceeded:{max_error_records}"
                manual = True
                raise _EvidenceLimitExceeded(evidence_limit_error)
            errors.append(normalized)
            error_set.add(normalized)

        def record_terminal_error(message: str) -> None:
            """Retain a small no-throw fallback while constructing terminal evidence."""

            normalized = str(message)
            if normalized not in terminal_aux_errors and len(terminal_aux_errors) < 8:
                terminal_aux_errors.append(normalized)

        def add_event(
            event: str, pid: int | None = None, details: Mapping[str, Any] | None = None
        ) -> int:
            nonlocal evidence_limit_error, manual, sequence
            if len(events) >= max_job_events:
                if evidence_limit_error is None:
                    evidence_limit_error = f"job_event_limit_exceeded:{max_job_events}"
                    record_error(evidence_limit_error)
                    manual = True
                raise _EvidenceLimitExceeded(evidence_limit_error)
            sequence += 1
            monotonic_ns, utc_value = timestamp()
            events.append(
                JobEvent(
                    sequence=sequence,
                    event=event,
                    monotonic_ns=monotonic_ns,
                    timestamp_utc=utc_value,
                    pid=pid,
                    details=dict(details or {}),
                )
            )
            return sequence

        job: int | None = None
        completion: int | None = None
        stdin_handle: int | None = None
        stdout_read: int | None = None
        stdout_write: int | None = None
        stderr_read: int | None = None
        stderr_write: int | None = None
        root_process: int | None = None
        root_thread: int | None = None
        root_pid: int | None = None
        stdout_data = _BoundedStreamCapture(max_stream_bytes)
        stderr_data = _BoundedStreamCapture(max_stream_bytes)
        stdout_drained_event = threading.Event()
        stderr_drained_event = threading.Event()
        stdout_reader_exited_event = threading.Event()
        stderr_reader_exited_event = threading.Event()
        stdout_thread: threading.Thread | None = None
        stderr_thread: threading.Thread | None = None
        stdout_thread_started = False
        stderr_thread_started = False
        timed_out = False
        cancelled = False
        manual = False
        stage = "pre_create_setup"
        child_created = False
        membership_verified = False
        root_resumed = False
        final_accounting: _JOB_BASIC_ACCOUNTING | None = None
        final_pids: tuple[int, ...] = ()
        job_limit_flags = -1
        capability_nonce = bytearray()
        capability_nonce_hex = ""
        executable_identity: dict[str, Any] = {}
        pre_kernel_gate_evidence: dict[str, Any] = {
            "pre_kernel_create_gate_required": True,
            "pre_kernel_create_gate_passed": False,
            "pre_kernel_create_gate_invocations": 0,
            "pre_kernel_remaining_seconds": None,
            "pre_kernel_required_seconds": (
                self.contract.wrapper_timeout_seconds
                + self.contract.residual_repoll_seconds
                + post_process_reserve_seconds
            ),
        }
        pre_kernel_wrapper_deadline: float | None = None
        stream_cleanup: dict[str, Any] = {
            "schema": "evm.phase-b2.stream-reader-cleanup.v1",
            "reason": "readers_not_started",
            "read_handle_owner": "reader_thread",
            "bounded_by_restore_deadline": True,
            "readers": [],
            "all_reader_threads_exited": True,
            "forced_termination_attempts": 0,
        }

        def decoded_stream(value: _BoundedStreamCapture) -> str:
            decoded = bytes(value).decode("utf-8", errors="replace")
            if capability_nonce_hex:
                return _redact_job_capability_nonce(decoded, capability_nonce_hex)
            return decoded

        def require_overall_budget() -> None:
            if self._clock() >= overall_deadline:
                raise ProcessContainmentError(f"restore deadline exhausted before stage {stage}")

        def require_precreate_budget_and_cancel() -> None:
            """Last fail-closed gate immediately before the kernel child create."""

            nonlocal cancelled, manual, pre_kernel_wrapper_deadline, stage, timed_out
            pre_kernel_gate_evidence["pre_kernel_create_gate_invocations"] += 1
            if pre_kernel_gate_evidence["pre_kernel_create_gate_invocations"] != 1:
                manual = True
                raise _PreKernelCreateGateError("pre-kernel create gate invoked more than once")
            stage = "pre_kernel_create_gate"
            now = self._clock()
            remaining = overall_deadline - now
            pre_kernel_gate_evidence["pre_kernel_remaining_seconds"] = remaining
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                manual = True
                raise _PreKernelCreateGateError("cancellation requested before child creation")
            required = float(pre_kernel_gate_evidence["pre_kernel_required_seconds"])
            if remaining < required:
                timed_out = True
                manual = True
                raise _PreKernelCreateGateError(
                    "insufficient restore budget immediately before child creation: "
                    f"remaining={remaining:.9f} required={required:.9f}"
                )
            pre_kernel_wrapper_deadline = now + self.contract.wrapper_timeout_seconds
            pre_kernel_gate_evidence["pre_kernel_create_gate_passed"] = True

        def drain_reader(
            read_handle: int,
            sink: _BoundedStreamCapture,
            drained_event: threading.Event,
            exited_event: threading.Event,
        ) -> None:
            try:
                api.read_pipe(read_handle, sink, drained_event)
            finally:
                exited_event.set()

        def capture(pid: int, supplied_handle: int | None = None) -> bool:
            nonlocal manual
            for key, handle in process_handles.items():
                if key[0] == pid and api.process_is_active(handle):
                    return True
            process_handle = supplied_handle or api.open_process(pid)
            owns_new_handle = supplied_handle is None
            if not process_handle:
                manual = True
                record_error(f"identity_unavailable_pid={pid}")
                return False
            try:
                if not api.is_process_in_job(process_handle, int(job)):
                    manual = True
                    record_error(f"identity_not_in_job_pid={pid}")
                    return False
                # Measure the stable kernel identity before committing an event.  A
                # repeated observation of the same process must not create an extra
                # identity_observed event that has no matching identity record.
                identity_sequence = sequence + 1
                identity = api.process_identity(
                    process_handle,
                    pid=pid,
                    fallback_ppid=os.getpid() if pid == root_pid else None,
                    run_uuid=execution_uuid,
                    observed_sequence=identity_sequence,
                )
                if identity.stable_key in identities:
                    return True
                if len(identities) >= max_process_identities:
                    raise _EvidenceLimitExceeded(
                        f"process_identity_limit_exceeded:{max_process_identities}"
                    )
                if add_event("identity_observed", pid) != identity_sequence:
                    raise ProcessContainmentError("identity_event_sequence_commit_mismatch")
                identities[identity.stable_key] = identity
                process_handles[identity.stable_key] = process_handle
                owns_new_handle = False
                return True
            except _EvidenceLimitExceeded:
                raise
            except ProcessContainmentError as exc:
                manual = True
                record_error(str(exc))
                return False
            finally:
                if owns_new_handle:
                    api.close(process_handle)

        def sample_accounting(force: bool = False) -> _JOB_BASIC_ACCOUNTING:
            nonlocal manual
            accounting: _JOB_BASIC_ACCOUNTING | None = None
            active_pids: tuple[int, ...] = ()
            for attempt in range(1, DEFAULT_MAX_ACCOUNTING_COHERENCE_ATTEMPTS + 1):
                require_overall_budget()
                accounting = api.query_accounting(int(job))
                active_pids = api.query_active_pids(int(job))
                if int(accounting.ActiveProcesses) == len(active_pids):
                    break
                add_event(
                    "accounting_observation_incoherent",
                    details={
                        "attempt": attempt,
                        "active_processes": int(accounting.ActiveProcesses),
                        "active_pid_count": len(active_pids),
                    },
                )
            else:
                manual = True
                record_error("job_accounting_active_pid_snapshot_unstable")
                raise ProcessContainmentError("job accounting active PID snapshot unstable")
            assert accounting is not None
            for pid in active_pids:
                capture(pid, root_process if pid == root_pid else None)
            state = (
                int(accounting.TotalProcesses),
                int(accounting.ActiveProcesses),
                int(accounting.TotalTerminatedProcesses),
                active_pids,
            )
            prior = None
            if snapshots:
                previous = snapshots[-1]
                prior = (
                    previous.total_processes,
                    previous.active_processes,
                    previous.total_terminated_processes,
                    previous.active_pids,
                )
            if force or state != prior:
                if len(snapshots) >= max_accounting_snapshots:
                    raise _EvidenceLimitExceeded(
                        f"accounting_snapshot_limit_exceeded:{max_accounting_snapshots}"
                    )
                nonlocal sequence
                sequence += 1
                monotonic_ns, utc_value = timestamp()
                snapshots.append(
                    JobAccountingSnapshot(
                        sequence=sequence,
                        monotonic_ns=monotonic_ns,
                        timestamp_utc=utc_value,
                        total_processes=state[0],
                        active_processes=state[1],
                        total_terminated_processes=state[2],
                        active_pids=state[3],
                    )
                )
            return accounting

        completion_names = {
            api._JOB_MESSAGE_ACTIVE_ZERO: "job_active_process_zero",
            api._JOB_MESSAGE_NEW_PROCESS: "job_new_process",
            api._JOB_MESSAGE_EXIT_PROCESS: "job_exit_process",
            api._JOB_MESSAGE_ABNORMAL_EXIT: "job_abnormal_exit_process",
        }

        def process_completion(records: Sequence[tuple[int, int | None]]) -> int:
            nonlocal timed_out, cancelled, manual
            observed = 0
            for message, pid in records:
                if observed >= DEFAULT_MAX_COMPLETION_EVENTS_PER_DRAIN:
                    manual = True
                    if "completion_event_batch_limit_reached" not in errors:
                        record_error("completion_event_batch_limit_reached")
                        add_event(
                            "completion_event_batch_limit_reached",
                            details={
                                "maximum_events_per_drain": (
                                    DEFAULT_MAX_COMPLETION_EVENTS_PER_DRAIN
                                ),
                                "forced_termination_attempts": 0,
                            },
                        )
                    break
                if self._clock() >= active_work_deadline:
                    timed_out = True
                    manual = True
                    if "completion_drain_restore_deadline_reached" not in errors:
                        record_error("completion_drain_restore_deadline_reached")
                        add_event(
                            "completion_drain_restore_deadline_reached",
                            details={
                                "restore_deadline_seconds": (
                                    self.contract.restore_deadline_seconds
                                ),
                                "forced_termination_attempts": 0,
                            },
                        )
                    break
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    manual = True
                    if "completion_drain_cancel_latched" not in errors:
                        record_error("completion_drain_cancel_latched")
                        add_event(
                            "completion_drain_cancel_latched",
                            details={"forced_termination_attempts": 0},
                        )
                    break
                add_event(completion_names.get(message, f"job_message_{message}"), pid)
                if message == api._JOB_MESSAGE_NEW_PROCESS and pid:
                    capture(pid, root_process if pid == root_pid else None)
                observed += 1
            if observed >= DEFAULT_MAX_COMPLETION_EVENTS_PER_DRAIN:
                manual = True
                if "completion_event_batch_limit_reached" not in errors:
                    record_error("completion_event_batch_limit_reached")
                    add_event(
                        "completion_event_batch_limit_reached",
                        details={
                            "maximum_events_per_drain": DEFAULT_MAX_COMPLETION_EVENTS_PER_DRAIN,
                            "forced_termination_attempts": 0,
                        },
                    )
            return observed

        def drain_completion() -> int:
            return process_completion(api.completion_events(int(completion)))

        def wait_for_completion_or_budget(sleep_budget: float) -> None:
            wait_method = getattr(api, "wait_completion_events", None)
            if not callable(wait_method) or sleep_budget < 0.001:
                self._sleep(sleep_budget)
                return
            wait_milliseconds = max(1, int(sleep_budget * 1_000))
            wait_started = self._clock()
            observed = process_completion(wait_method(int(completion), wait_milliseconds))
            elapsed = max(0.0, self._clock() - wait_started)
            # A fake/testing API may return immediately without advancing the
            # injected clock.  Preserve the same bounded pacing in that case;
            # real GetQueuedCompletionStatus already consumed this budget.
            if observed == 0 and elapsed < sleep_budget:
                self._sleep(sleep_budget - elapsed)

        def thread_start_observed(
            thread: threading.Thread | None,
            exited_event: threading.Event,
        ) -> bool:
            """Conservatively detect a native reader even if start() raised."""

            if thread is None:
                return False
            try:
                return bool(
                    exited_event.is_set()
                    or getattr(thread, "ident", None) is not None
                    or thread.is_alive()
                )
            except BaseException:
                # An uninspectable start result must never permit the parent
                # to close a handle that a native reader may still own.
                return True

        def cleanup_stream_readers(reason: str, deadline: float) -> dict[str, Any]:
            reader_states = (
                (
                    "stdout",
                    stdout_thread,
                    stdout_thread_started,
                    stdout_drained_event,
                    stdout_reader_exited_event,
                ),
                (
                    "stderr",
                    stderr_thread,
                    stderr_thread_started,
                    stderr_drained_event,
                    stderr_reader_exited_event,
                ),
            )
            records: list[dict[str, Any]] = []
            for label, thread, thread_started, drained_event, exited_event in reader_states:
                observed_started = thread_started or thread_start_observed(thread, exited_event)
                record: dict[str, Any] = {
                    "stream": label,
                    "started": observed_started,
                    "native_thread_id": thread.native_id if thread is not None else None,
                    "drained_before_cleanup": drained_event.is_set(),
                    "exited_before_cleanup": exited_event.is_set(),
                    "cancel_attempted": False,
                    "cancel_succeeded": False,
                    "no_pending_io": False,
                    "cancel_error_code": None,
                    "exited_after_cleanup": exited_event.is_set(),
                    "thread_alive_after_cleanup": bool(thread and thread.is_alive()),
                    "read_handle_close_scope": (
                        "reader_read_pipe_finally"
                        if observed_started
                        else "parent_runner_finally_if_allocated"
                    ),
                    "bounded_join_timeout_seconds": 0.0,
                }
                if (
                    thread is not None
                    and observed_started
                    and thread.is_alive()
                    and not drained_event.is_set()
                    and thread.native_id is not None
                ):
                    record["cancel_attempted"] = True
                    cancel_method = getattr(api, "cancel_synchronous_reader_io", None)
                    if callable(cancel_method):
                        try:
                            cancel_result = cancel_method(int(thread.native_id))
                            record["cancel_succeeded"] = (
                                cancel_result.get("cancel_succeeded") is True
                            )
                            record["no_pending_io"] = cancel_result.get("no_pending_io") is True
                            record["cancel_error_code"] = cancel_result.get("error_code")
                        except Exception as cancel_exc:
                            record["cancel_error_code"] = (
                                f"{type(cancel_exc).__name__}:{cancel_exc}"
                            )
                    else:
                        record["cancel_error_code"] = "cancel_api_unavailable"
                records.append(record)

            for record, (
                _label,
                thread,
                _thread_started,
                _drained_event,
                exited_event,
            ) in zip(records, reader_states, strict=True):
                if thread is not None and record["started"] and thread.is_alive():
                    join_timeout = min(0.25, max(0.0, deadline - self._clock()))
                    record["bounded_join_timeout_seconds"] = join_timeout
                    thread.join(timeout=join_timeout)
                record["exited_after_cleanup"] = exited_event.is_set()
                record["thread_alive_after_cleanup"] = bool(thread and thread.is_alive())

            all_exited = all(
                not record["started"]
                or (
                    record["exited_after_cleanup"] is True
                    and record["thread_alive_after_cleanup"] is False
                )
                for record in records
            )
            result = {
                "schema": "evm.phase-b2.stream-reader-cleanup.v1",
                "reason": reason,
                "read_handle_owner": "reader_thread",
                "bounded_by_restore_deadline": True,
                "readers": records,
                "all_reader_threads_exited": all_exited,
                "forced_termination_attempts": 0,
            }
            try:
                add_event(
                    (
                        "stream_reader_cleanup_completed"
                        if all_exited
                        else "stream_reader_cleanup_failed"
                    ),
                    details={
                        "reason": reason,
                        "all_reader_threads_exited": all_exited,
                        "forced_termination_attempts": 0,
                    },
                )
            except Exception as event_exc:
                record_terminal_error(
                    f"stream_reader_cleanup_event_failed:{type(event_exc).__name__}:{event_exc}"
                )
            return result

        try:
            stage = "create_job_capability_nonce"
            require_overall_budget()
            capability_nonce = _fresh_job_capability_nonce()
            capability_nonce_hex = capability_nonce.hex()
            stage = "create_job"
            require_overall_budget()
            job, completion = api.create_job_and_completion_port()
            add_event("job_created", details={"run_uuid": execution_uuid})
            stage = "create_stdio"
            require_overall_budget()
            stdout_read, stdout_write = api.create_pipe()
            stderr_read, stderr_write = api.create_pipe()
            stdin_handle = api.open_inheritable_null()
            stage = "create_suspended_root"
            info = api.create_suspended_process(
                command=command_tuple,
                cwd=os.fspath(Path(cwd).resolve()) if cwd is not None else None,
                environment=child_env,
                job=job,
                stdin_handle=stdin_handle,
                stdout_handle=stdout_write,
                stderr_handle=stderr_write,
                job_capability_nonce=capability_nonce,
                job_capability_run_uuid=execution_uuid,
                create_no_window=create_no_window,
                expected_executable_sha256=expected_executable_sha256,
                pre_kernel_create_gate=require_precreate_budget_and_cancel,
            )
            root_process = int(info.hProcess)
            root_thread = int(info.hThread)
            root_pid = int(info.dwProcessId)
            child_created = True
            executable_identity = {
                **dict(getattr(api, "last_application_identity", {})),
                **pre_kernel_gate_evidence,
            }
            if (
                pre_kernel_gate_evidence["pre_kernel_create_gate_passed"] is not True
                or pre_kernel_gate_evidence["pre_kernel_create_gate_invocations"] != 1
                or pre_kernel_wrapper_deadline is None
            ):
                raise ProcessContainmentError(
                    "pre-kernel create gate was not enforced exactly once"
                )
            if expected_executable_sha256 is not None and (
                executable_identity.get("sha256") != expected_executable_sha256
                or executable_identity.get("expected_sha256") != expected_executable_sha256
                or executable_identity.get("pin_match") is not True
                or executable_identity.get("measurement_scope")
                != "immediately_before_CreateProcessW"
                or executable_identity.get("handle_lock_held_through_create") is not True
                or executable_identity.get("handle_lock_share_mode") != "FILE_SHARE_READ_only"
                or executable_identity.get("handle_lock_inheritable") is not False
                or executable_identity.get("ancestor_directory_locks_held_through_create")
                is not True
                or type(executable_identity.get("ancestor_directory_lock_count")) is not int
                or executable_identity["ancestor_directory_lock_count"] < 0
                or executable_identity.get("ancestor_directory_lock_share_mode")
                != "FILE_SHARE_READ_WRITE_no_delete"
                or executable_identity.get("path_lock_scope") != "all_nonroot_ancestors_and_leaf"
            ):
                raise ProcessContainmentError(
                    "executable launch identity evidence missing or inconsistent"
                )
            stage = "verify_suspended_root"
            require_overall_budget()
            add_event("root_created_suspended", root_pid)

            # Parent copies must close before drain can become authoritative.
            api.close(stdin_handle)
            stdin_handle = None
            api.close(stdout_write)
            stdout_write = None
            api.close(stderr_write)
            stderr_write = None

            if not api.is_process_in_job(root_process, job):
                raise ProcessContainmentError(
                    f"suspended root pid {root_pid} is not assigned to the Job"
                )
            job_limit_flags = api.query_limit_flags(job)
            if job_limit_flags != 0:
                raise ProcessContainmentError(
                    f"Job limit flags must be zero, observed {job_limit_flags}"
                )
            initial = api.query_accounting(job)
            if initial.ActiveProcesses != 1 or initial.TotalProcesses != 1:
                raise ProcessContainmentError(
                    "suspended-root Job accounting mismatch: "
                    f"total={initial.TotalProcesses} active={initial.ActiveProcesses}"
                )
            membership_verified = True
            add_event(
                "job_membership_verified",
                root_pid,
                {"active_processes": 1, "job_limit_flags": job_limit_flags},
            )
            stage = "capture_suspended_root_identity"
            require_overall_budget()
            if not capture(root_pid, root_process):
                raise ProcessContainmentError(
                    f"suspended root pid {root_pid} identity could not be captured"
                )

            stage = "start_stream_drains"
            require_overall_budget()
            stdout_thread = threading.Thread(
                target=drain_reader,
                args=(
                    stdout_read,
                    stdout_data,
                    stdout_drained_event,
                    stdout_reader_exited_event,
                ),
                name=f"{name}-stdout-drain",
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=drain_reader,
                args=(
                    stderr_read,
                    stderr_data,
                    stderr_drained_event,
                    stderr_reader_exited_event,
                ),
                name=f"{name}-stderr-drain",
                daemon=True,
            )
            try:
                stdout_thread.start()
            finally:
                if thread_start_observed(stdout_thread, stdout_reader_exited_event):
                    stdout_thread_started = True
                    stdout_read = None
            try:
                stderr_thread.start()
            finally:
                if thread_start_observed(stderr_thread, stderr_reader_exited_event):
                    stderr_thread_started = True
                    stderr_read = None

            stage = "resume_verified_root"
            assert pre_kernel_wrapper_deadline is not None
            wrapper_deadline = min(pre_kernel_wrapper_deadline, active_work_deadline)
            resume_gate_now = self._clock()
            resume_cancelled = cancel_event is not None and cancel_event.is_set()
            resume_timed_out = resume_gate_now >= wrapper_deadline
            if resume_cancelled:
                cancelled = True
                manual = True
                add_event(
                    "cancel_latched_before_resume",
                    root_pid,
                    {"forced_termination_attempts": 0},
                )
            if resume_timed_out:
                timed_out = True
                manual = True
                add_event(
                    "wrapper_deadline_latched_before_resume",
                    root_pid,
                    {
                        "wrapper_timeout_seconds": self.contract.wrapper_timeout_seconds,
                        "forced_termination_attempts": 0,
                    },
                )
            if resume_cancelled or resume_timed_out:
                stage = (
                    "resume_cancel_gate"
                    if resume_cancelled and not resume_timed_out
                    else "resume_wrapper_deadline_gate"
                )
                raise ProcessContainmentError(
                    "cancel or wrapper deadline latched before root resume"
                )
            api.resume(root_thread)
            root_resumed = True
            add_event("root_resumed", root_pid)
            api.close(root_thread)
            root_thread = None
            residual_deadline: float | None = None

            stage = "runtime_accounting"
            while True:
                now = self._clock()
                if now >= active_work_deadline:
                    timed_out = True
                    manual = True
                    add_event(
                        "restore_deadline_cleanup_reserve_entered",
                        details={
                            "restore_deadline_seconds": self.contract.restore_deadline_seconds,
                            "cleanup_reserve_seconds": cleanup_reserve_seconds,
                        },
                    )
                    break
                drain_completion()
                try:
                    final_accounting = sample_accounting()
                except ProcessContainmentError as exc:
                    manual = True
                    record_error(str(exc))
                    add_event("job_accounting_error", details={"error": str(exc)})
                    break

                # Accounting is an external observation and may consume the
                # remaining wrapper/active-work budget.  Latch timeout and
                # cancellation state before accepting ActiveProcesses == 0;
                # otherwise a process that exits at the boundary can turn an
                # already-expired or cancelled operation into a false PASS.
                now = self._clock()
                if now >= active_work_deadline:
                    timed_out = True
                    manual = True
                    add_event(
                        "restore_deadline_cleanup_reserve_entered",
                        details={
                            "restore_deadline_seconds": self.contract.restore_deadline_seconds,
                            "cleanup_reserve_seconds": cleanup_reserve_seconds,
                        },
                    )
                    break
                if residual_deadline is None:
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                        manual = True
                        residual_deadline = min(
                            now + self.contract.residual_repoll_seconds,
                            active_work_deadline,
                        )
                        add_event(
                            "cancel_latched",
                            details={
                                "residual_repoll_seconds": self.contract.residual_repoll_seconds
                            },
                        )
                    elif now >= wrapper_deadline:
                        timed_out = True
                        manual = True
                        residual_deadline = min(
                            now + self.contract.residual_repoll_seconds,
                            active_work_deadline,
                        )
                        add_event(
                            "timeout_latched",
                            details={
                                "wrapper_timeout_seconds": self.contract.wrapper_timeout_seconds,
                                "residual_repoll_seconds": self.contract.residual_repoll_seconds,
                            },
                        )
                if int(final_accounting.ActiveProcesses) == 0:
                    add_event("active_process_count_zero")
                    break
                if residual_deadline is not None and now >= residual_deadline:
                    add_event("residual_repoll_exhausted")
                    break
                sleep_budget = min(
                    poll_interval_seconds,
                    max(0.0, active_work_deadline - self._clock()),
                    max(
                        0.0,
                        (residual_deadline or wrapper_deadline) - self._clock(),
                    ),
                )
                if sleep_budget > 0:
                    wait_for_completion_or_budget(sleep_budget)

            drain_completion()
            stage = "final_accounting"
            final_accounting = sample_accounting(force=True)
            final_pids = api.query_active_pids(job)
            if final_pids:
                manual = True
                add_event("residual_processes_observed", details={"pids": list(final_pids)})

            stage = "stream_drain_gate"
            drain_deadline = min(
                self._clock() + self.contract.stream_drain_seconds, overall_deadline
            )
            cleanup_reserve = min(0.1, self.contract.stream_drain_seconds / 2.0)
            natural_drain_deadline = (
                self._clock()
                if final_pids
                else max(self._clock(), drain_deadline - cleanup_reserve)
            )
            while self._clock() < natural_drain_deadline:
                if stdout_drained_event.is_set() and stderr_drained_event.is_set():
                    break
                remaining_drain = max(0.0, natural_drain_deadline - self._clock())
                if remaining_drain > 0:
                    self._sleep(min(poll_interval_seconds, remaining_drain))
            stream_cleanup = cleanup_stream_readers(
                "residual_child" if final_pids else "stream_drain_gate",
                drain_deadline,
            )
            if stream_cleanup["all_reader_threads_exited"] is not True:
                manual = True
                record_error("stream_reader_cleanup_incomplete_within_contract")
            if stdout_drained_event.is_set() and stderr_drained_event.is_set():
                add_event("streams_drained")
            else:
                manual = True
                record_error("stdout_or_stderr_not_drained_within_contract")
                add_event(
                    "stream_drain_incomplete",
                    details={
                        "stdout_drained": stdout_drained_event.is_set(),
                        "stderr_drained": stderr_drained_event.is_set(),
                    },
                )

            if stdout_data.overflow or stderr_data.overflow:
                manual = True
                record_error(
                    "stream_capture_limit_exceeded:"
                    f"max={max_stream_bytes} stdout_total={stdout_data.total_bytes} "
                    f"stderr_total={stderr_data.total_bytes}"
                )
                add_event(
                    "stream_capture_limit_exceeded",
                    details={
                        "maximum_bytes_per_stream": max_stream_bytes,
                        "stdout_total_bytes": stdout_data.total_bytes,
                        "stderr_total_bytes": stderr_data.total_bytes,
                        "stdout_overflow": stdout_data.overflow,
                        "stderr_overflow": stderr_data.overflow,
                    },
                )

            coverage = identity_coverage_complete(
                int(final_accounting.TotalProcesses), tuple(identities.values())
            )
            if not coverage:
                manual = True
                record_error(
                    "identity_coverage_incomplete:"
                    f"total={int(final_accounting.TotalProcesses)} "
                    f"identities={len(identities)}"
                )
                add_event(
                    "identity_coverage_incomplete",
                    details={
                        "job_total_processes": int(final_accounting.TotalProcesses),
                        "unique_identities": len(identities),
                    },
                )

            return_code = api.exit_code(root_process)
            # Decode bounded streams before the last affirmative follow-up
            # decision.  Even bounded decoding can be materially expensive;
            # a deadline/cancel transition during it must be latched rather
            # than returning a stale safe_for_followup=True decision.
            decoded_stdout = decoded_stream(stdout_data)
            decoded_stderr = decoded_stream(stderr_data)
            ended_at = self._utc_clock().isoformat()
            final_gate_now = self._clock()
            if cancel_event is not None and cancel_event.is_set() and not cancelled:
                cancelled = True
                manual = True
                add_event(
                    "cancel_latched_at_final_gate",
                    details={"forced_termination_attempts": 0},
                )
            if final_gate_now >= overall_deadline:
                timed_out = True
                manual = True
                add_event(
                    "restore_deadline_exhausted_at_final_gate",
                    details={
                        "restore_deadline_seconds": self.contract.restore_deadline_seconds,
                        "forced_termination_attempts": 0,
                    },
                )
            duration_seconds = max(0.0, final_gate_now - start_monotonic)
            active_zero = int(final_accounting.ActiveProcesses) == 0
            streams_drained = stdout_drained_event.is_set() and stderr_drained_event.is_set()
            safe = bool(
                active_zero
                and streams_drained
                and coverage
                and not timed_out
                and not cancelled
                and not manual
                and not errors
                and return_code == 0
            )
            stage = "completed"
            return ProcessOutcome(
                name=name,
                run_uuid=execution_uuid,
                command=command_tuple,
                started_at_utc=started_at,
                ended_at_utc=ended_at,
                duration_seconds=duration_seconds,
                timed_out=timed_out,
                cancelled=cancelled,
                return_code=return_code,
                manual_intervention_required=manual,
                residual_pids=tuple(sorted(final_pids)),
                stdout=decoded_stdout,
                stderr=decoded_stderr,
                stdout_drained=stdout_drained_event.is_set(),
                stderr_drained=stderr_drained_event.is_set(),
                streams_drained=streams_drained,
                active_process_zero=active_zero,
                final_active_process_count=int(final_accounting.ActiveProcesses),
                identity_coverage_complete=coverage,
                safe_for_followup=safe,
                forced_termination_attempts=0,
                job_limit_flags=job_limit_flags,
                identities=tuple(identities.values()),
                events=tuple(events),
                accounting=tuple(snapshots),
                errors=tuple(errors),
                executable_identity=executable_identity,
                stdout_total_bytes=stdout_data.total_bytes,
                stderr_total_bytes=stderr_data.total_bytes,
                stdout_capture_overflow=stdout_data.overflow,
                stderr_capture_overflow=stderr_data.overflow,
                stream_capture_limit_bytes=max_stream_bytes,
                stream_cleanup=stream_cleanup,
            )
        except BaseException as exc:
            # Re-latch asynchronous terminal state at the first exception
            # boundary.  A cancel/deadline transition that races with the
            # failing operation must remain visible in the failure evidence.
            exception_gate_now = self._clock()
            try:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    manual = True
                    record_terminal_error("cancel_latched_at_exception_boundary")
            except BaseException as cancel_probe_exc:
                manual = True
                record_terminal_error(
                    "cancel_probe_failed_at_exception_boundary:"
                    f"{type(cancel_probe_exc).__name__}:{cancel_probe_exc}"
                )
            exception_wrapper_deadline = (
                min(pre_kernel_wrapper_deadline, active_work_deadline)
                if pre_kernel_wrapper_deadline is not None
                else None
            )
            if (
                exception_wrapper_deadline is not None
                and exception_gate_now >= exception_wrapper_deadline
            ) or exception_gate_now >= overall_deadline:
                timed_out = True
                manual = True
                record_terminal_error("deadline_latched_at_exception_boundary")
            partial_process_information = dict(getattr(api, "last_process_information", {}) or {})
            pending_process_information = getattr(api, "pending_process_information", None)
            if pending_process_information is not None:
                pending_values = {
                    "process_handle": int(pending_process_information.hProcess or 0),
                    "thread_handle": int(pending_process_information.hThread or 0),
                    "pid": int(pending_process_information.dwProcessId or 0),
                    "thread_id": int(pending_process_information.dwThreadId or 0),
                }
                if any(value > 0 for value in pending_values.values()):
                    partial_process_information = pending_values
            kernel_aftermath_latched = not child_created and any(
                type(partial_process_information.get(key)) is int
                and partial_process_information[key] > 0
                for key in ("process_handle", "thread_handle", "pid")
            )
            if kernel_aftermath_latched:
                root_process = int(partial_process_information.get("process_handle", 0)) or None
                root_thread = int(partial_process_information.get("thread_handle", 0)) or None
                root_pid = int(partial_process_information.get("pid", 0)) or None
                child_created = True
                manual = True
            if isinstance(exc, _KernelCreateAftermathError) or kernel_aftermath_latched:
                stage = "kernel_create_aftermath"
            elif isinstance(exc, _KernelCreateError):
                stage = "kernel_create_suspended_root"
            elif isinstance(exc, _PreKernelCreateGateError):
                stage = "pre_kernel_create_gate"
            cleanup_deadline = min(
                overall_deadline,
                self._clock() + self.contract.stream_drain_seconds,
            )
            try:
                stream_cleanup = cleanup_stream_readers(
                    f"containment_failure:{stage}", cleanup_deadline
                )
            except BaseException as cleanup_exc:
                stream_cleanup = {
                    "schema": "evm.phase-b2.stream-reader-cleanup.v1",
                    "reason": f"containment_failure:{stage}",
                    "read_handle_owner": "reader_thread",
                    "bounded_by_restore_deadline": True,
                    "readers": [],
                    "all_reader_threads_exited": False,
                    "cleanup_error": f"{type(cleanup_exc).__name__}:{cleanup_exc}",
                    "forced_termination_attempts": 0,
                }
            if stream_cleanup.get("all_reader_threads_exited") is not True:
                record_terminal_error("stream_reader_cleanup_incomplete_within_contract")
            executable_identity = {
                **dict(getattr(api, "last_application_identity", {})),
                **executable_identity,
                **pre_kernel_gate_evidence,
            }
            failure_errors = [
                *(
                    _redact_job_capability_nonce(value, capability_nonce_hex)
                    if capability_nonce_hex
                    else value
                    for value in errors
                ),
                *terminal_aux_errors,
                _redact_job_capability_nonce(f"{type(exc).__name__}:{exc}", capability_nonce_hex)
                if capability_nonce_hex
                else f"{type(exc).__name__}:{exc}",
            ]
            failure_pids: set[int] = set()
            if child_created and root_pid is not None and not root_resumed:
                # A root that has never been resumed cannot have exited
                # naturally.  Preserve its PID even if Job queries are the
                # operation that failed.
                failure_pids.add(root_pid)
            if child_created and job:
                try:
                    failure_accounting = api.query_accounting(job)
                    queried_pids = api.query_active_pids(job)
                    failure_pids.update(queried_pids)
                    if len(snapshots) < max_accounting_snapshots:
                        sequence += 1
                        monotonic_ns, utc_value = timestamp()
                        snapshots.append(
                            JobAccountingSnapshot(
                                sequence=sequence,
                                monotonic_ns=monotonic_ns,
                                timestamp_utc=utc_value,
                                total_processes=int(failure_accounting.TotalProcesses),
                                active_processes=int(failure_accounting.ActiveProcesses),
                                total_terminated_processes=int(
                                    failure_accounting.TotalTerminatedProcesses
                                ),
                                active_pids=tuple(queried_pids),
                            )
                        )
                    else:
                        failure_errors.append(
                            "failure_accounting_snapshot_suppressed_at_limit:"
                            f"{max_accounting_snapshots}"
                        )
                except BaseException as accounting_exc:
                    failure_errors.append(
                        "failure_evidence_job_query_failed:"
                        f"{type(accounting_exc).__name__}:{accounting_exc}"
                    )
            if child_created and root_pid is not None and root_process and not failure_pids:
                try:
                    if api.process_is_active(root_process):
                        failure_pids.add(root_pid)
                except BaseException as process_exc:
                    failure_errors.append(
                        "failure_evidence_root_query_failed:"
                        f"{type(process_exc).__name__}:{process_exc}"
                    )
            try:
                add_event(
                    "containment_failure",
                    root_pid,
                    {
                        "stage": stage,
                        "child_created": child_created,
                        "job_membership_verified": membership_verified,
                        "root_resumed": root_resumed,
                        "residual_pids": sorted(failure_pids),
                        "forced_termination_attempts": 0,
                    },
                )
            except BaseException as event_exc:
                failure_errors.append(
                    f"failure_event_record_failed:{type(event_exc).__name__}:{event_exc}"
                )
            raise ProcessContainmentFailure(
                f"containment failed at {stage}: {exc}",
                name=name,
                stage=stage,
                run_uuid=execution_uuid,
                root_pid=root_pid,
                child_created=child_created,
                job_membership_verified=membership_verified,
                root_resumed=root_resumed,
                residual_pids=tuple(failure_pids),
                stdout=decoded_stream(stdout_data),
                stderr=decoded_stream(stderr_data),
                stdout_drained=stdout_drained_event.is_set(),
                stderr_drained=stderr_drained_event.is_set(),
                events=tuple(events),
                identities=tuple(identities.values()),
                accounting=tuple(snapshots),
                errors=tuple(failure_errors),
                cause_type=type(exc).__name__,
                executable_identity=executable_identity,
                stdout_total_bytes=stdout_data.total_bytes,
                stderr_total_bytes=stderr_data.total_bytes,
                stdout_capture_overflow=stdout_data.overflow,
                stderr_capture_overflow=stderr_data.overflow,
                stream_capture_limit_bytes=max_stream_bytes,
                stream_cleanup=stream_cleanup,
                timed_out=timed_out or self._clock() >= overall_deadline,
                cancelled=cancelled,
                started_at_utc=started_at,
                ended_at_utc=self._utc_clock().isoformat(),
                duration_seconds=max(0.0, self._clock() - start_monotonic),
                restore_deadline_seconds=self.contract.restore_deadline_seconds,
                restore_deadline_exhausted=self._clock() >= overall_deadline,
            ) from exc
        finally:
            # Handles are evidence resources only.  No process-control operation
            # occurs here; an active process is allowed to finish naturally.
            api.close(stdin_handle)
            api.close(stdout_write)
            api.close(stderr_write)
            api.close(stdout_read)
            api.close(stderr_read)
            api.close(root_thread)
            closed: set[int] = set()
            for handle in process_handles.values():
                if handle not in closed:
                    api.close(handle)
                    closed.add(handle)
            if root_process and root_process not in closed:
                api.close(root_process)
            api.close(completion)
            api.close(job)
            _clear_capability_environment(child_env)
            capability_nonce[:] = b"\0" * len(capability_nonce)


ProcessTimeoutContract = TimeoutContract

PROCESS_CONTAINMENT_CONTRACT: dict[str, Any] = {
    "provider": "windows_job_object",
    "create_suspended": True,
    "assign_before_resume": True,
    "breakaway_allowed": False,
    "kill_on_job_close": False,
    "terminate_job_object_allowed": False,
    "job_accounting_authoritative": True,
    "stdio_drain_before_followup": True,
    "residual_repoll_seconds": 120,
    "force_termination_attempts": 0,
    "wsl_run_uuid_and_process_group": True,
    "wsl_proc_residual_check": True,
}


# This is deliberately separate from PROCESS_CONTAINMENT_CONTRACT.  The latter
# is the stable r7 manifest contract consumed by existing bundle builders.  The
# r7s3 work adds primitives, but no production child entry point currently
# calls consume_inherited_job_capability(), so it must not be presented as an
# enforced production invariant or as GO evidence.
R7S3_JOB_CAPABILITY_PRIMITIVE_CONTRACT: dict[str, Any] = {
    "schema": "evm.phase-b2.pre-r8-r7s3.job-capability-primitive.v1",
    "parent_provisions_private_inherited_job_capability": True,
    "job_capability_access": JOB_CAPABILITY_QUERY_ACCESS,
    "child_consumption_helper_available": True,
    "child_consumption_wired_to_production": False,
    "explicit_current_job_snapshot_equivalence_helper": True,
    "explicit_current_job_snapshot_equivalence_enforced": False,
    "explicit_application_name": True,
    "optional_expected_executable_sha256": True,
    "actual_launch_identity_evidence": True,
    "bounded_stream_capture": True,
    "restore_deadline_bounds_runner_stages": False,
    "pre_kernel_filesystem_setup_hard_deadline_bounded": False,
    "completion_port_blocking_wait": True,
    "completion_event_batch_limit": DEFAULT_MAX_COMPLETION_EVENTS_PER_DRAIN,
    "run_event_limit": DEFAULT_MAX_JOB_EVENTS,
    "run_identity_limit": DEFAULT_MAX_PROCESS_IDENTITIES,
    "run_accounting_snapshot_limit": DEFAULT_MAX_ACCOUNTING_SNAPSHOTS,
    "completion_drain_deadline_and_cancel_checks": True,
    "final_safe_gate_after_bounded_stream_decode": True,
    "reader_start_exception_native_state_cleanup": True,
    "pre_kernel_create_cancel_gate": True,
    "pre_kernel_create_full_budget_gate": True,
    "wsl_scan_exact_schema_and_types": True,
    "wsl_scan_global_readability_fail_closed": True,
    "wsl_residual_scan_resource_caps_enforced": True,
    "accounting_active_pid_coherence_attempt_limit": (DEFAULT_MAX_ACCOUNTING_COHERENCE_ATTEMPTS),
    "duplicate_identity_events_suppressed": True,
    "base_exception_converted_to_containment_failure": False,
    "base_exception_conversion_scope": "post_windows_api_initialization_runner_body",
    "completion_poll_interval_seconds": DEFAULT_PROCESS_POLL_INTERVAL_SECONDS,
    "missed_descendant_identity_fails_closed": True,
    "executable_handle_held_through_create": True,
    "job_capability_consumed_before_workload": False,
    "ambient_ancestor_job_effective_limits_audited": False,
    "residual_job_observer_lease_until_active_zero": False,
    "wsl_kernel_lineage_containment": False,
    "wsl_interpreter_sha256_pinned": False,
    "wsl_scan_nonce_unique_per_poll": True,
    "same_token_hostile_admin_protected": False,
    "go_evidence_eligible": False,
    "external_review_required": True,
}


class R7ProcessContractError(RuntimeError):
    """Raised when staged containment policy differs from the r7 runtime."""


def validate_process_containment_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise R7ProcessContractError("process_containment_mapping_required")
    actual = dict(value)
    if actual != PROCESS_CONTAINMENT_CONTRACT:
        raise R7ProcessContractError("process_containment_contract_mismatch")
    return actual


__all__ = [
    "DEFAULT_MAX_ACCOUNTING_COHERENCE_ATTEMPTS",
    "DEFAULT_MAX_ACCOUNTING_SNAPSHOTS",
    "DEFAULT_MAX_COMPLETION_EVENTS_PER_DRAIN",
    "DEFAULT_MAX_JOB_EVENTS",
    "DEFAULT_MAX_PROCESS_IDENTITIES",
    "DEFAULT_MAX_STREAM_BYTES",
    "DEFAULT_MAX_WSL_PROCESSES_EXAMINED",
    "DEFAULT_MAX_WSL_SCAN_PAYLOAD_BYTES",
    "DEFAULT_MAX_WSL_SCAN_RECORDS",
    "DEFAULT_PROCESS_POLL_INTERVAL_SECONDS",
    "JOB_CAPABILITY_COMMITMENT_ENV",
    "JOB_CAPABILITY_HANDLE_ENV",
    "JOB_CAPABILITY_NONCE_BYTES",
    "JOB_CAPABILITY_NONCE_ENV",
    "JOB_CAPABILITY_QUERY_ACCESS",
    "JobAccountingSnapshot",
    "JobEvent",
    "LinuxProcStat",
    "ProcessContainmentError",
    "ProcessContainmentFailure",
    "ProcessIdentity",
    "ProcessOutcome",
    "ProcessTimeoutContract",
    "PROCESS_CONTAINMENT_CONTRACT",
    "R7S3_JOB_CAPABILITY_PRIMITIVE_CONTRACT",
    "R7ProcessContractError",
    "RUN_UUID_ENV",
    "TimeoutContract",
    "WindowsJobProcessRunner",
    "WslProcessIdentity",
    "WslResidualProtocol",
    "consume_inherited_job_capability",
    "identity_coverage_complete",
    "job_capability_commitment",
    "parse_linux_proc_stat",
    "validate_process_containment_contract",
]
