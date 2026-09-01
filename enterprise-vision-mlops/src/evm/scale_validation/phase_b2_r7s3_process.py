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
JOB_CAPABILITY_QUERY_ACCESS = 0x00020004  # READ_CONTROL | JOB_OBJECT_QUERY
_JOB_CAPABILITY_DOMAIN = b"evm.phase-b2.windows-job-capability.v1\0"
_JOB_CAPABILITY_REDACTION = "<redacted-job-capability-nonce>"


class ProcessContainmentError(RuntimeError):
    """Raised when kernel-backed containment cannot be established safely."""


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
        self.timed_out = False
        self.cancelled = False
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
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "cause_type": self.cause_type,
            "name": self.name,
            "stage": self.stage,
            "run_uuid": self.run_uuid,
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
        if any(not isinstance(value, (int, float)) or value <= 0 for value in values.values()):
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

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-serialisable evidence object."""

        return asdict(self)


def identity_coverage_complete(total_processes: int, identities: Sequence[ProcessIdentity]) -> bool:
    """Require exact coverage using PID plus creation time, never PID alone."""

    stable_keys = {identity.stable_key for identity in identities}
    return (
        total_processes >= 1
        and len(stable_keys) == total_processes
        and all(
            identity.pid > 0
            and identity.ppid is not None
            and identity.creation_time_ns > 0
            and bool(identity.creation_time_utc)
            for identity in identities
        )
    )


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
    cmdline_sha256: str

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
        self.root_process_group = root_process_group
        self.root_start_time_ticks = root_start_time_ticks
        self.boot_id = boot_id.strip() if boot_id else None

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

run_uuid, expected_pgrp, expected_start, expected_boot = sys.argv[1:]
expected_pgrp_i = int(expected_pgrp) if expected_pgrp else None
expected_start_i = int(expected_start) if expected_start else None
boot = pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()
needle = ("EVM_PHASE_B2_RUN_UUID=" + run_uuid).encode()
records = []
for proc in pathlib.Path("/proc").iterdir():
    if not proc.name.isdigit():
        continue
    try:
        raw = (proc / "stat").read_text()
        right = raw.rfind(")")
        left = raw.find("(")
        if left <= 0 or right <= left:
            continue
        pid = int(raw[:left].strip())
        fields = raw[right + 1:].strip().split()
        if len(fields) < 20:
            continue
        ppid, pgrp, session, start = (
            int(fields[1]), int(fields[2]), int(fields[3]), int(fields[19])
        )
        environ = (proc / "environ").read_bytes().split(b"\0")
        cmdline = (proc / "cmdline").read_bytes()
        uuid_match = needle in environ
        group_match = bool(
            expected_boot and boot == expected_boot
            and expected_pgrp_i is not None and pgrp == expected_pgrp_i
            and expected_start_i is not None and start >= expected_start_i
        )
        if uuid_match or group_match:
            records.append({
                "pid": pid, "ppid": ppid, "pgrp": pgrp, "session": session,
                "start_time_ticks": start, "boot_id": boot,
                "run_uuid_match": uuid_match,
                "process_group_match": group_match,
                "cmdline_sha256": hashlib.sha256(cmdline).hexdigest(),
            })
    except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
        continue
print(json.dumps(sorted(records, key=lambda row: (row["pid"], row["start_time_ticks"])),
                 sort_keys=True, separators=(",", ":")))
"""

    def scan_command(self, distribution: str) -> tuple[str, ...]:
        if not distribution:
            raise ValueError("distribution is required")
        return (
            "wsl.exe",
            "--distribution",
            distribution,
            "--exec",
            "python3",
            "-c",
            self.scanner_python_source(),
            self.run_uuid,
            "" if self.root_process_group is None else str(self.root_process_group),
            "" if self.root_start_time_ticks is None else str(self.root_start_time_ticks),
            self.boot_id or "",
        )

    @staticmethod
    def parse_scan_json(payload: str) -> tuple[WslProcessIdentity, ...]:
        rows = json.loads(payload)
        if not isinstance(rows, list):
            raise ValueError("WSL residual scan result must be a list")
        allowed = {
            "pid",
            "ppid",
            "pgrp",
            "session",
            "start_time_ticks",
            "boot_id",
            "run_uuid_match",
            "process_group_match",
            "cmdline_sha256",
        }
        records: list[WslProcessIdentity] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != allowed:
                raise ValueError("unexpected WSL residual scan schema")
            digest = str(row["cmdline_sha256"])
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("invalid command-line digest")
            records.append(
                WslProcessIdentity(
                    pid=int(row["pid"]),
                    ppid=int(row["ppid"]),
                    pgrp=int(row["pgrp"]),
                    session=int(row["session"]),
                    start_time_ticks=int(row["start_time_ticks"]),
                    boot_id=str(row["boot_id"]),
                    run_uuid_match=bool(row["run_uuid_match"]),
                    process_group_match=bool(row["process_group_match"]),
                    cmdline_sha256=digest,
                )
            )
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
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x00001000
    _SYNCHRONIZE = 0x00100000
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 258
    _STILL_ACTIVE = 259
    _ERROR_BROKEN_PIPE = 109
    _ERROR_MORE_DATA = 234
    _ERROR_INSUFFICIENT_BUFFER = 122
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise ProcessContainmentError("Windows Job Objects require Windows")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.ntdll = ctypes.WinDLL("ntdll")
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
    ) -> _PROCESS_INFORMATION:
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
            application_identity = _validated_executable_identity(command[0])
            application_path = str(application_identity["path"])
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
                startup_pointer = ctypes.cast(ctypes.byref(startup), ctypes.POINTER(_STARTUPINFOW))
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
                    raise self._error("CreateProcessW")
                return info
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

    def completion_events(self, completion: int) -> list[tuple[int, int | None]]:
        events: list[tuple[int, int | None]] = []
        while True:
            message = wintypes.DWORD()
            key = ctypes.c_size_t()
            overlapped = wintypes.LPVOID()
            ok = self.kernel32.GetQueuedCompletionStatus(
                completion,
                ctypes.byref(message),
                ctypes.byref(key),
                ctypes.byref(overlapped),
                0,
            )
            if not ok:
                if ctypes.get_last_error() == self._WAIT_TIMEOUT:
                    break
                raise self._error("GetQueuedCompletionStatus")
            pid = int(overlapped.value) if overlapped.value else None
            events.append((int(message.value), pid))
        return events

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
        nonce_text = _consume_environment_value(child_environment, JOB_CAPABILITY_NONCE_ENV)
        expected_commitment = _consume_environment_value(
            child_environment, JOB_CAPABILITY_COMMITMENT_ENV
        )
    finally:
        _clear_capability_environment(child_environment)

    if not re.fullmatch(r"[1-9][0-9]*", handle_text):
        raise ProcessContainmentError("job capability handle encoding is invalid")
    handle = int(handle_text)
    if handle > (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1:
        raise ProcessContainmentError("job capability handle is outside pointer range")
    run_uuid_matches = [
        str(value) for key, value in child_environment.items() if str(key).upper() == RUN_UUID_ENV
    ]
    if len(run_uuid_matches) != 1:
        raise ProcessContainmentError("job capability run UUID environment is invalid")
    normalized_uuid = _normalise_uuid(run_uuid_matches[0])
    nonce = bytearray(_normalise_job_capability_nonce(nonce_text))
    try:
        commitment = job_capability_commitment(nonce, normalized_uuid)
        if expected_commitment != commitment:
            raise ProcessContainmentError("job capability nonce commitment mismatch")
        runtime_api = _WindowsJobApi() if api is None else api
        try:
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
        finally:
            runtime_api.close(handle)
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
        poll_interval_seconds: float = 0.025,
        cancel_event: Any | None = None,
        run_uuid: str | uuid.UUID | None = None,
        create_no_window: bool = True,
    ) -> ProcessOutcome:
        if sys.platform != "win32":
            raise ProcessContainmentError("WindowsJobProcessRunner requires Windows")
        if isinstance(command, (str, bytes)) or not command:
            raise ValueError("command must be a non-empty argument sequence")
        if not name:
            raise ValueError("name is required")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")

        command_tuple = tuple(os.fspath(item) for item in command)
        execution_uuid = _normalise_uuid(run_uuid or uuid.uuid4())
        child_env = dict(os.environ if env is None else env)
        _clear_capability_environment(child_env)
        for key in tuple(child_env):
            if key.upper() == RUN_UUID_ENV:
                del child_env[key]
        child_env[RUN_UUID_ENV] = execution_uuid
        child_env = {str(key): str(value) for key, value in child_env.items()}

        try:
            api = _WindowsJobApi()
        except Exception as exc:
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
            ) from exc
        events: list[JobEvent] = []
        snapshots: list[JobAccountingSnapshot] = []
        identities: dict[tuple[int, int], ProcessIdentity] = {}
        process_handles: dict[tuple[int, int], int] = {}
        errors: list[str] = []
        sequence = 0

        def timestamp() -> tuple[int, str]:
            return (time.monotonic_ns(), self._utc_clock().isoformat())

        def add_event(
            event: str, pid: int | None = None, details: Mapping[str, Any] | None = None
        ) -> int:
            nonlocal sequence
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
        stdout_data = bytearray()
        stderr_data = bytearray()
        stdout_drained_event = threading.Event()
        stderr_drained_event = threading.Event()
        stdout_thread: threading.Thread | None = None
        stderr_thread: threading.Thread | None = None
        start_monotonic = self._clock()
        started_at = self._utc_clock().isoformat()
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

        def decoded_stream(value: bytearray) -> str:
            decoded = bytes(value).decode("utf-8", errors="replace")
            if capability_nonce_hex:
                return _redact_job_capability_nonce(decoded, capability_nonce_hex)
            return decoded

        def capture(pid: int, supplied_handle: int | None = None) -> bool:
            nonlocal manual
            for key, handle in process_handles.items():
                if key[0] == pid and api.process_is_active(handle):
                    return True
            process_handle = supplied_handle or api.open_process(pid)
            owns_new_handle = supplied_handle is None
            if not process_handle:
                errors.append(f"identity_unavailable_pid={pid}")
                return False
            try:
                if not api.is_process_in_job(process_handle, int(job)):
                    errors.append(f"identity_not_in_job_pid={pid}")
                    return False
                identity_sequence = add_event("identity_observed", pid)
                identity = api.process_identity(
                    process_handle,
                    pid=pid,
                    fallback_ppid=os.getpid() if pid == root_pid else None,
                    run_uuid=execution_uuid,
                    observed_sequence=identity_sequence,
                )
                if identity.stable_key in identities:
                    return True
                identities[identity.stable_key] = identity
                process_handles[identity.stable_key] = process_handle
                owns_new_handle = False
                return True
            except ProcessContainmentError as exc:
                manual = True
                errors.append(str(exc))
                return False
            finally:
                if owns_new_handle:
                    api.close(process_handle)

        def sample_accounting(force: bool = False) -> _JOB_BASIC_ACCOUNTING:
            accounting = api.query_accounting(int(job))
            active_pids = api.query_active_pids(int(job))
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

        def drain_completion() -> None:
            for message, pid in api.completion_events(int(completion)):
                add_event(completion_names.get(message, f"job_message_{message}"), pid)
                if message == api._JOB_MESSAGE_NEW_PROCESS and pid:
                    capture(pid, root_process if pid == root_pid else None)

        try:
            stage = "create_job_capability_nonce"
            capability_nonce = _fresh_job_capability_nonce()
            capability_nonce_hex = capability_nonce.hex()
            stage = "create_job"
            job, completion = api.create_job_and_completion_port()
            add_event("job_created", details={"run_uuid": execution_uuid})
            stage = "create_stdio"
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
            )
            root_process = int(info.hProcess)
            root_thread = int(info.hThread)
            root_pid = int(info.dwProcessId)
            child_created = True
            stage = "verify_suspended_root"
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
            if not capture(root_pid, root_process):
                raise ProcessContainmentError(
                    f"suspended root pid {root_pid} identity could not be captured"
                )

            stage = "start_stream_drains"
            stdout_thread = threading.Thread(
                target=api.read_pipe,
                args=(stdout_read, stdout_data, stdout_drained_event),
                name=f"{name}-stdout-drain",
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=api.read_pipe,
                args=(stderr_read, stderr_data, stderr_drained_event),
                name=f"{name}-stderr-drain",
                daemon=True,
            )
            stdout_thread.start()
            stderr_thread.start()
            stdout_read = None  # reader owns the handle
            stderr_read = None

            stage = "resume_verified_root"
            api.resume(root_thread)
            root_resumed = True
            add_event("root_resumed", root_pid)
            api.close(root_thread)
            root_thread = None
            start_monotonic = self._clock()
            wrapper_deadline = start_monotonic + self.contract.wrapper_timeout_seconds
            residual_deadline: float | None = None

            stage = "runtime_accounting"
            while True:
                drain_completion()
                try:
                    final_accounting = sample_accounting()
                except ProcessContainmentError as exc:
                    manual = True
                    errors.append(str(exc))
                    add_event("job_accounting_error", details={"error": str(exc)})
                    break
                if int(final_accounting.ActiveProcesses) == 0:
                    add_event("active_process_count_zero")
                    break

                now = self._clock()
                if residual_deadline is None:
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                        manual = True
                        residual_deadline = now + self.contract.residual_repoll_seconds
                        add_event(
                            "cancel_latched",
                            details={
                                "residual_repoll_seconds": self.contract.residual_repoll_seconds
                            },
                        )
                    elif now >= wrapper_deadline:
                        timed_out = True
                        manual = True
                        residual_deadline = now + self.contract.residual_repoll_seconds
                        add_event(
                            "timeout_latched",
                            details={
                                "wrapper_timeout_seconds": self.contract.wrapper_timeout_seconds,
                                "residual_repoll_seconds": self.contract.residual_repoll_seconds,
                            },
                        )
                elif now >= residual_deadline:
                    add_event("residual_repoll_exhausted")
                    break
                self._sleep(poll_interval_seconds)

            drain_completion()
            stage = "final_accounting"
            final_accounting = sample_accounting(force=True)
            final_pids = api.query_active_pids(job)
            if final_pids:
                manual = True
                add_event("residual_processes_observed", details={"pids": list(final_pids)})

            stage = "stream_drain_gate"
            drain_deadline = self._clock() + self.contract.stream_drain_seconds
            while self._clock() < drain_deadline:
                if stdout_drained_event.is_set() and stderr_drained_event.is_set():
                    break
                self._sleep(min(poll_interval_seconds, max(0.001, drain_deadline - self._clock())))
            if stdout_thread is not None:
                stdout_thread.join(timeout=0)
            if stderr_thread is not None:
                stderr_thread.join(timeout=0)
            if stdout_drained_event.is_set() and stderr_drained_event.is_set():
                add_event("streams_drained")
            else:
                manual = True
                errors.append("stdout_or_stderr_not_drained_within_contract")
                add_event(
                    "stream_drain_incomplete",
                    details={
                        "stdout_drained": stdout_drained_event.is_set(),
                        "stderr_drained": stderr_drained_event.is_set(),
                    },
                )

            coverage = identity_coverage_complete(
                int(final_accounting.TotalProcesses), tuple(identities.values())
            )
            if not coverage:
                manual = True
                errors.append(
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
            active_zero = int(final_accounting.ActiveProcesses) == 0
            streams_drained = stdout_drained_event.is_set() and stderr_drained_event.is_set()
            safe = bool(
                active_zero
                and streams_drained
                and coverage
                and not timed_out
                and not cancelled
                and not manual
                and return_code == 0
            )
            ended_at = self._utc_clock().isoformat()
            stage = "completed"
            return ProcessOutcome(
                name=name,
                run_uuid=execution_uuid,
                command=command_tuple,
                started_at_utc=started_at,
                ended_at_utc=ended_at,
                duration_seconds=max(0.0, self._clock() - start_monotonic),
                timed_out=timed_out,
                cancelled=cancelled,
                return_code=return_code,
                manual_intervention_required=manual,
                residual_pids=tuple(sorted(final_pids)),
                stdout=decoded_stream(stdout_data),
                stderr=decoded_stream(stderr_data),
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
            )
        except Exception as exc:
            failure_errors = [
                *(
                    _redact_job_capability_nonce(value, capability_nonce_hex)
                    if capability_nonce_hex
                    else value
                    for value in errors
                ),
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
                except Exception as accounting_exc:
                    failure_errors.append(
                        "failure_evidence_job_query_failed:"
                        f"{type(accounting_exc).__name__}:{accounting_exc}"
                    )
            if child_created and root_pid is not None and root_process and not failure_pids:
                try:
                    if api.process_is_active(root_process):
                        failure_pids.add(root_pid)
                except Exception as process_exc:
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
            except Exception as event_exc:
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
    # The image is measured and lpApplicationName is explicit, but the current
    # runner closes its measurement descriptor before CreateProcessW.
    "executable_handle_held_through_create": False,
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
