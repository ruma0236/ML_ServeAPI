"""Fail-closed candidate for the r7s7 Windows non-credit qualification.

The public entry point is intentionally disabled until an external authority
receipt adapter is provisioned.  The private test entry exercises the exact
one-shot ordering with an explicitly internal, non-authoritative root approval.
It can never emit production, r8, or Phase B2 success evidence.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import sys
import uuid
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePath
from typing import Any, Callable, NoReturn, Protocol

_REQUIRED_BOOTSTRAP_FLAGS = {
    "isolated": 1,
    "no_user_site": 1,
    "no_site": 1,
    "dont_write_bytecode": 1,
}
if any(
    getattr(sys.flags, name, None) != expected
    for name, expected in _REQUIRED_BOOTSTRAP_FLAGS.items()
):
    raise RuntimeError("pre_r8_r7s7_qualifier_requires_python_i_b_s")
if (
    not sys.pycache_prefix
    or not Path(sys.pycache_prefix).is_absolute()
    or Path(sys.pycache_prefix).exists()
):
    raise RuntimeError("pre_r8_r7s7_qualifier_requires_fresh_absolute_pycache_prefix")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from evm.scale_validation import phase_b2_r7s3_handle_io as handle_io_module  # noqa: E402
from evm.scale_validation import phase_b2_r7s3_process as process_module  # noqa: E402
from evm.scale_validation import (  # noqa: E402
    phase_b2_r7s7_qualification_work_order as qualification_work_order,
)
from evm.scale_validation.phase_b2_r7s3_process import (  # noqa: E402
    JOB_CAPABILITY_QUERY_ACCESS,
    JobAccountingSnapshot,
    ProcessContainmentFailure,
    ProcessOutcome,
    TimeoutContract,
    WindowsJobProcessRunner,
    identity_coverage_complete,
)
from evm.scale_validation.phase_b2_r7s4_handle_io import (  # noqa: E402
    DurablePublicationError,
    WindowsHandleApi,
    publish_bound_no_replace_durable,
)

INTERNAL_APPROVAL_SCHEMA = (
    "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification-internal-approval.v1"
)
INTERNAL_EVIDENCE_SCHEMA = (
    "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification-internal-evidence.v1"
)
FAILURE_SEAL_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification-failure-seal.v1"
EMERGENCY_SEAL_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification-emergency-seal.v1"
PRE_RESERVATION_FAILURE_SEAL_SCHEMA = (
    "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-pre-reservation-failure-seal.v1"
)
PRE_RESERVATION_EMERGENCY_SEAL_SCHEMA = (
    "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-pre-reservation-emergency-seal.v1"
)
FIXTURE_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-fixture-observation.v1"
RESERVATION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-reservation.v1"
RUN_DIRECTORY_ANCHOR_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-run-directory-anchor.v1"
PUBLIC_EXTERNAL_AUTHORITY_CONFIGURED = False
PUBLIC_PROCESS_CREATION_ENABLED = False
_HEX64 = frozenset("0123456789abcdef")


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _uuid4(value: object, label: str) -> str:
    if type(value) is not str:
        raise WindowsQualificationError(f"{label}_uuid4_required", stage="preflight")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise WindowsQualificationError(f"{label}_uuid4_required", stage="preflight") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise WindowsQualificationError(f"{label}_uuid4_required", stage="preflight")
    return value


def _hex64(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 64 or not set(value) <= _HEX64:
        raise WindowsQualificationError(f"{label}_sha256_required", stage="preflight")
    return value


@dataclass(frozen=True, slots=True)
class InternalRootApproval:
    schema: str
    run_uuid: str
    attempt_uuid: str
    root_pid: int
    administrator: bool
    integrity: str
    token_elevation_type: str
    powershell_parent_observed: bool
    approve_exactly_once: bool
    internal_non_authoritative: bool
    production_go: bool = False


@dataclass(frozen=True, slots=True)
class QualificationConfig:
    run_uuid: str
    attempt_uuid: str
    interpreter: str
    interpreter_sha256: str
    fixture: str
    fixture_sha256: str
    qualifier: str
    qualifier_sha256: str
    runner_source: str
    runner_source_sha256: str
    powershell: str
    powershell_sha256: str
    codex: str
    codex_sha256: str
    command_processor: str
    command_processor_sha256: str
    pycache_prefix: str
    output_root: str


@dataclass(frozen=True, slots=True)
class Publication:
    path: str
    sha256: str
    bytes: int
    atomic_rename_no_replace: bool
    file_fsync: bool
    directory_fsync: bool
    same_handle_readback: bool
    file_identity_stable_across_rename: bool
    file_identity: dict[str, Any]
    directory_identity: dict[str, Any]
    create_attempt_count: int


@dataclass(frozen=True, slots=True)
class QualificationCallCounts:
    reservation: int = 0
    process_creation_requested: int = 0
    process_creation: int = 0
    child_created_observed: bool | None = None
    runner_invocation: int = 0
    automatic_retry: int = 0
    followup_probe: int = 0
    force_termination: int = 0
    evidence_publication: int = 0
    failure_seal_publication: int = 0
    emergency_seal_publication: int = 0
    success_marker: int = 0
    completion_marker: int = 0


class WindowsQualificationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        stage: str,
        classification: str = "failed",
        counts: QualificationCallCounts | None = None,
        failure_publication: Publication | None = None,
        emergency_publication: Publication | None = None,
        publication_observation: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.classification = classification
        self.counts = counts or QualificationCallCounts()
        self.failure_publication = failure_publication
        self.emergency_publication = emergency_publication
        self.observation = publication_observation


class Runner(Protocol):
    def run(self, command: list[str], **kwargs: Any) -> ProcessOutcome: ...


class Store(Protocol):
    def reserve_once(self, run_uuid: str, attempt_uuid: str) -> Publication: ...

    def publish_evidence_once(self, run_uuid: str, raw: bytes) -> Publication: ...

    def publish_failure_once(self, run_uuid: str, raw: bytes) -> Publication: ...

    def publish_emergency_once(self, run_uuid: str, raw: bytes) -> Publication: ...

    def publish_pre_reservation_failure_once(
        self, run_uuid: str, attempt_uuid: str, raw: bytes
    ) -> Publication: ...

    def publish_pre_reservation_emergency_once(
        self, run_uuid: str, attempt_uuid: str, raw: bytes
    ) -> Publication: ...


class FileQualificationStore:
    """Handle-bound append-only store using the r7s6 publication primitive."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).resolve()
        self._run_directory_identities: dict[str, dict[str, Any]] = {}
        self._attempt_uuids: dict[str, str] = {}

    @staticmethod
    def _directory_identity(path: Path) -> dict[str, Any]:
        api = WindowsHandleApi()
        handle: int | None = None
        try:
            handle = api.open_directory(str(path))
            identity = api.identity(handle)
            handle_io_module._reject_unsafe_directory_identity(
                identity,
                expected_path=str(path),
            )
            return identity.to_dict()
        finally:
            api.close(handle)

    @staticmethod
    def _directory_identity_continuity_key(value: dict[str, Any]) -> tuple[Any, ...]:
        return (
            os.path.normcase(str(value.get("final_path", ""))),
            value.get("volume_serial_number"),
            value.get("file_id_hex"),
            value.get("reparse_tag"),
            value.get("file_type"),
            value.get("owner_sid"),
            value.get("security_descriptor_sha256"),
            value.get("dacl_present"),
            value.get("dacl_protected"),
        )

    @staticmethod
    def _as_publication(published: Any, raw: bytes) -> Publication:
        if (
            published.sha256 != _sha256(raw)
            or published.bytes != len(raw)
            or published.replace_if_exists
            or not published.same_handle_readback
            or not published.file_identity_stable_across_rename
            or published.file_flush_count < 1
            or published.directory_flush_count != 1
            or not published.directory_flush_succeeded
        ):
            raise WindowsQualificationError(
                "durable_publication_contract_mismatch", stage="publication"
            )
        return Publication(
            path=published.final_path,
            sha256=published.sha256,
            bytes=published.bytes,
            atomic_rename_no_replace=True,
            file_fsync=published.file_flush_count >= 1,
            directory_fsync=True,
            same_handle_readback=published.same_handle_readback,
            file_identity_stable_across_rename=published.file_identity_stable_across_rename,
            file_identity=published.identity.to_dict(),
            directory_identity=published.directory_identity.to_dict(),
            create_attempt_count=1,
        )

    def _publish(self, run_dir: Path, leaf: str, raw: bytes, run_uuid: str) -> Publication:
        if PurePath(leaf).name != leaf or leaf not in {
            "windows-qualification-evidence.json",
            "windows-qualification-failure-seal.json",
            "windows-qualification-emergency-seal.json",
        }:
            raise WindowsQualificationError("publication_leaf_forbidden", stage="publication")
        expected_directory_identity = self._run_directory_identities.get(run_uuid)
        if expected_directory_identity is None:
            raise WindowsQualificationError(
                "run_directory_identity_anchor_missing", stage="publication"
            )
        if self._directory_identity_continuity_key(
            self._directory_identity(run_dir)
        ) != self._directory_identity_continuity_key(expected_directory_identity):
            raise WindowsQualificationError(
                "run_directory_identity_changed_before_publication", stage="publication"
            )
        published = publish_bound_no_replace_durable(
            run_dir,
            leaf,
            raw,
            run_uuid=run_uuid,
        )
        publication = self._as_publication(published, raw)
        if self._directory_identity_continuity_key(
            publication.directory_identity
        ) != self._directory_identity_continuity_key(expected_directory_identity):
            raise WindowsQualificationError(
                "run_directory_identity_changed_during_publication", stage="publication"
            )
        return publication

    def reserve_once(self, run_uuid: str, attempt_uuid: str) -> Publication:
        run_uuid = _uuid4(run_uuid, "reservation_run")
        attempt_uuid = _uuid4(attempt_uuid, "reservation_attempt")
        self.root.mkdir(parents=True, exist_ok=True)
        run_dir = self.root / run_uuid
        reservation_leaf = f"{run_uuid}.reservation.json"
        reservation_path = self.root / reservation_leaf
        if os.path.lexists(reservation_path) or os.path.lexists(run_dir):
            raise WindowsQualificationError("run_uuid_already_reserved", stage="reservation")
        reservation_raw = _canonical_json(
            {
                "schema": RESERVATION_SCHEMA,
                "run_uuid": run_uuid,
                "attempt_uuid": attempt_uuid,
                "one_shot": True,
                "replace_existing": False,
                "production_go": False,
            }
        )
        try:
            reservation = publish_bound_no_replace_durable(
                self.root,
                reservation_leaf,
                reservation_raw,
                run_uuid=run_uuid,
            )
        except FileExistsError as exc:
            raise WindowsQualificationError(
                "run_uuid_already_reserved", stage="reservation"
            ) from exc
        except DurablePublicationError as exc:
            message = (
                "run_uuid_already_reserved"
                if os.path.lexists(reservation_path)
                else "reservation_publication_failed"
            )
            raise WindowsQualificationError(
                message,
                stage="reservation",
                publication_observation=exc.observation,
            ) from exc
        try:
            run_dir.mkdir()
        except FileExistsError as exc:
            raise WindowsQualificationError(
                "reserved_run_directory_collision", stage="reservation"
            ) from exc
        directory_before_anchor = self._directory_identity(run_dir)
        anchor_raw = _canonical_json(
            {
                "schema": RUN_DIRECTORY_ANCHOR_SCHEMA,
                "run_uuid": run_uuid,
                "attempt_uuid": attempt_uuid,
                "identity_continuity": "verify_before_and_after_each_publication",
                "run_directory_identity": directory_before_anchor,
                "replace_existing": False,
                "production_go": False,
            }
        )
        anchor = self._as_publication(
            publish_bound_no_replace_durable(
                run_dir,
                "windows-qualification-run-directory-anchor.json",
                anchor_raw,
                run_uuid=run_uuid,
            ),
            anchor_raw,
        )
        if self._directory_identity_continuity_key(
            anchor.directory_identity
        ) != self._directory_identity_continuity_key(directory_before_anchor):
            raise WindowsQualificationError(
                "run_directory_identity_changed_during_anchor", stage="reservation"
            )
        self._run_directory_identities[run_uuid] = anchor.directory_identity
        self._attempt_uuids[run_uuid] = attempt_uuid
        return self._as_publication(reservation, reservation_raw)

    def publish_evidence_once(self, run_uuid: str, raw: bytes) -> Publication:
        return self._publish(
            self.root / _uuid4(run_uuid, "evidence_run"),
            "windows-qualification-evidence.json",
            raw,
            run_uuid,
        )

    def publish_failure_once(self, run_uuid: str, raw: bytes) -> Publication:
        return self._publish(
            self.root / _uuid4(run_uuid, "failure_run"),
            "windows-qualification-failure-seal.json",
            raw,
            run_uuid,
        )

    def publish_emergency_once(self, run_uuid: str, raw: bytes) -> Publication:
        attempt_uuid = self._attempt_uuids.get(run_uuid)
        if attempt_uuid is None:
            raise WindowsQualificationError(
                "emergency_attempt_identity_missing", stage="publication"
            )
        return self._publish_parent_control(
            run_uuid,
            attempt_uuid,
            scope="post-reservation",
            kind="emergency",
            raw=raw,
        )

    def _publish_parent_control(
        self,
        run_uuid: str,
        attempt_uuid: str,
        *,
        scope: str,
        kind: str,
        raw: bytes,
    ) -> Publication:
        run_uuid = _uuid4(run_uuid, "parent_control_run")
        attempt_uuid = _uuid4(attempt_uuid, "parent_control_attempt")
        if scope not in {"pre-reservation", "post-reservation"} or kind not in {
            "failure",
            "emergency",
        }:
            raise WindowsQualificationError("parent_control_kind_forbidden", stage="publication")
        self.root.mkdir(parents=True, exist_ok=True)
        leaf = f"{run_uuid}.{attempt_uuid}.{scope}-{kind}-seal.json"
        published = publish_bound_no_replace_durable(
            self.root,
            leaf,
            raw,
            run_uuid=run_uuid,
        )
        return self._as_publication(published, raw)

    def publish_pre_reservation_failure_once(
        self, run_uuid: str, attempt_uuid: str, raw: bytes
    ) -> Publication:
        return self._publish_parent_control(
            run_uuid,
            attempt_uuid,
            scope="pre-reservation",
            kind="failure",
            raw=raw,
        )

    def publish_pre_reservation_emergency_once(
        self, run_uuid: str, attempt_uuid: str, raw: bytes
    ) -> Publication:
        return self._publish_parent_control(
            run_uuid,
            attempt_uuid,
            scope="pre-reservation",
            kind="emergency",
            raw=raw,
        )


class _ProcessBasicInformation(ctypes.Structure):
    _fields_ = [
        ("reserved1", ctypes.c_void_p),
        ("peb_base", ctypes.c_void_p),
        ("reserved2_0", ctypes.c_void_p),
        ("reserved2_1", ctypes.c_void_p),
        ("unique_pid", ctypes.c_size_t),
        ("inherited_pid", ctypes.c_size_t),
    ]


class _UnicodeString(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.USHORT),
        ("maximum_length", wintypes.USHORT),
        ("buffer", ctypes.c_void_p),
    ]


class _FileTime(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


def _win32_error(label: str) -> WindowsQualificationError:
    return WindowsQualificationError(
        f"{label}_win32_error:{ctypes.get_last_error()}", stage="live_lineage"
    )


def _filetime_iso(value: _FileTime) -> str:
    ticks = (int(value.high) << 32) | int(value.low)
    return (
        (datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=ticks // 10))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _measure_process_live(pid: int) -> dict[str, Any]:
    """Read identity, redacted command metadata, and token directly via Win32."""

    if os.name != "nt" or type(pid) is not int or pid <= 0:
        raise WindowsQualificationError("windows_live_process_required", stage="live_lineage")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.ProcessIdToSessionId.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    advapi32.CreateWellKnownSid.argtypes = [
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.CreateWellKnownSid.restype = wintypes.BOOL
    advapi32.DuplicateToken.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.DuplicateToken.restype = wintypes.BOOL
    advapi32.CheckTokenMembership.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.CheckTokenMembership.restype = wintypes.BOOL
    ntdll.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    ]
    ntdll.NtQueryInformationProcess.restype = ctypes.c_long
    process_handle = kernel32.OpenProcess(0x1000, False, pid)
    if not process_handle:
        raise _win32_error("open_process")
    token_handle = ctypes.c_void_p()
    duplicate_token = ctypes.c_void_p()
    try:
        basic = _ProcessBasicInformation()
        returned = wintypes.ULONG()
        if (
            ntdll.NtQueryInformationProcess(
                process_handle,
                0,
                ctypes.byref(basic),
                ctypes.sizeof(basic),
                ctypes.byref(returned),
            )
            != 0
        ):
            raise WindowsQualificationError("process_ppid_query_failed", stage="live_lineage")
        path_buffer = ctypes.create_unicode_buffer(32_768)
        path_size = wintypes.DWORD(len(path_buffer))
        if not kernel32.QueryFullProcessImageNameW(
            process_handle, 0, path_buffer, ctypes.byref(path_size)
        ):
            raise _win32_error("process_path_query")
        created = _FileTime()
        exited = _FileTime()
        kernel = _FileTime()
        user = _FileTime()
        if not kernel32.GetProcessTimes(
            process_handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise _win32_error("process_creation_time_query")

        required = wintypes.ULONG()
        ntdll.NtQueryInformationProcess(process_handle, 60, None, 0, ctypes.byref(required))
        if required.value < ctypes.sizeof(_UnicodeString) or required.value > 1_048_576:
            raise WindowsQualificationError(
                "process_command_line_size_invalid", stage="live_lineage"
            )
        command_buffer = ctypes.create_string_buffer(required.value)
        if (
            ntdll.NtQueryInformationProcess(
                process_handle,
                60,
                command_buffer,
                required.value,
                ctypes.byref(required),
            )
            != 0
        ):
            raise WindowsQualificationError(
                "process_command_line_query_failed", stage="live_lineage"
            )
        command_value = ctypes.cast(command_buffer, ctypes.POINTER(_UnicodeString)).contents
        if command_value.length % 2 or command_value.length > command_value.maximum_length:
            raise WindowsQualificationError("process_command_line_invalid", stage="live_lineage")
        command_line = ctypes.wstring_at(command_value.buffer, command_value.length // 2)

        if not advapi32.OpenProcessToken(process_handle, 0x000A, ctypes.byref(token_handle)):
            raise _win32_error("open_process_token")
        elevation = wintypes.DWORD()
        token_elevated = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token_handle,
            18,
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(returned),
        ) or not advapi32.GetTokenInformation(
            token_handle,
            20,
            ctypes.byref(token_elevated),
            ctypes.sizeof(token_elevated),
            ctypes.byref(returned),
        ):
            raise _win32_error("token_elevation_query")
        integrity_size = wintypes.DWORD()
        advapi32.GetTokenInformation(token_handle, 25, None, 0, ctypes.byref(integrity_size))
        if not integrity_size.value:
            raise _win32_error("token_integrity_size_query")
        integrity_buffer = ctypes.create_string_buffer(integrity_size.value)
        if not advapi32.GetTokenInformation(
            token_handle,
            25,
            integrity_buffer,
            integrity_size.value,
            ctypes.byref(integrity_size),
        ):
            raise _win32_error("token_integrity_query")

        class _SidAndAttributes(ctypes.Structure):
            _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]

        label = ctypes.cast(integrity_buffer, ctypes.POINTER(_SidAndAttributes)).contents
        count = advapi32.GetSidSubAuthorityCount(label.sid).contents.value
        integrity_rid = advapi32.GetSidSubAuthority(label.sid, count - 1).contents.value
        integrity = {0x3000: "High", 0x4000: "System"}.get(
            integrity_rid,
            "Other",
        )
        sid_size = wintypes.DWORD(68)
        admin_sid = ctypes.create_string_buffer(sid_size.value)
        if not advapi32.CreateWellKnownSid(26, None, admin_sid, ctypes.byref(sid_size)):
            raise _win32_error("administrator_sid_create")
        if not advapi32.DuplicateToken(token_handle, 2, ctypes.byref(duplicate_token)):
            raise _win32_error("token_duplicate")
        administrator_member = wintypes.BOOL()
        if not advapi32.CheckTokenMembership(
            duplicate_token,
            admin_sid,
            ctypes.byref(administrator_member),
        ):
            raise _win32_error("administrator_membership_query")
        session_id = wintypes.DWORD()
        if not kernel32.ProcessIdToSessionId(pid, ctypes.byref(session_id)):
            raise _win32_error("process_session_query")
        image_path = Path(path_buffer.value).resolve(strict=True)
        return {
            "pid": pid,
            "ppid": int(basic.inherited_pid),
            "session_id": int(session_id.value),
            "creation_time_utc": _filetime_iso(created),
            "path": str(image_path),
            "image_sha256": _sha256(image_path.read_bytes()),
            "command_line_sha256": _sha256(command_line.encode("utf-8")),
            "danger_full_access_flag_present": bool(
                re.search(
                    r"(?i)(?:^|\s)(?:-s|--sandbox)(?:\s+|=)[\"']?danger-full-access", command_line
                )
            ),
            "approval_never_flag_present": bool(
                re.search(
                    r"(?i)(?:^|\s)(?:-a|--ask-for-approval|--approval-policy)(?:\s+|=)[\"']?never",
                    command_line,
                )
            ),
            "command_line_persisted": False,
            "token": {
                "administrator": bool(
                    administrator_member.value
                    and token_elevated.value == 1
                    and integrity_rid >= 0x3000
                ),
                "administrator_group_member": bool(administrator_member.value),
                "integrity": integrity,
                "integrity_rid": int(integrity_rid),
                "token_elevation_type": {1: "Default", 2: "Full", 3: "Limited"}.get(
                    int(elevation.value), "Unknown"
                ),
                "token_elevation_value": int(elevation.value),
            },
            "measurement": "win32_direct_no_child_process",
        }
    finally:
        if duplicate_token:
            kernel32.CloseHandle(duplicate_token)
        if token_handle:
            kernel32.CloseHandle(token_handle)
        kernel32.CloseHandle(process_handle)


def _validate_live_lineage(value: dict[str, Any]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"python", "powershell", "codex"}:
        raise WindowsQualificationError("live_lineage_keys_not_exact", stage="live_lineage")
    runtime = value["python"]
    powershell = value["powershell"]
    codex = value["codex"]
    record_keys = {
        "pid",
        "ppid",
        "session_id",
        "creation_time_utc",
        "path",
        "image_sha256",
        "command_line_sha256",
        "danger_full_access_flag_present",
        "approval_never_flag_present",
        "command_line_persisted",
        "token",
        "measurement",
    }
    token_keys = {
        "administrator",
        "administrator_group_member",
        "integrity",
        "integrity_rid",
        "token_elevation_type",
        "token_elevation_value",
    }
    if any(type(record) is not dict or set(record) != record_keys for record in value.values()):
        raise WindowsQualificationError("live_lineage_record_keys_not_exact", stage="live_lineage")
    if runtime.get("pid") != os.getpid() or runtime.get("ppid") != powershell.get("pid"):
        raise WindowsQualificationError("python_powershell_lineage_mismatch", stage="live_lineage")
    if powershell.get("ppid") != codex.get("pid"):
        raise WindowsQualificationError("powershell_codex_lineage_mismatch", stage="live_lineage")
    if Path(str(powershell.get("path", ""))).name.lower() not in {"powershell.exe", "pwsh.exe"}:
        raise WindowsQualificationError("powershell_parent_required", stage="live_lineage")
    if Path(str(codex.get("path", ""))).name.lower() != "codex.exe":
        raise WindowsQualificationError("codex_ancestor_required", stage="live_lineage")
    sessions: set[int] = set()
    creation: list[datetime] = []
    for label, record in (("python", runtime), ("powershell", powershell), ("codex", codex)):
        token = record.get("token")
        if (
            type(token) is not dict
            or set(token) != token_keys
            or token.get("administrator") is not True
            or token.get("administrator_group_member") is not True
            or token.get("integrity") not in {"High", "System"}
            or token.get("token_elevation_type") != "Full"
            or token.get("token_elevation_value") != 2
        ):
            raise WindowsQualificationError(f"{label}_token_not_full_admin", stage="live_lineage")
        sessions.add(record.get("session_id"))
        try:
            created = datetime.fromisoformat(
                str(record.get("creation_time_utc", "")).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise WindowsQualificationError(
                f"{label}_creation_time_invalid", stage="live_lineage"
            ) from exc
        if created.tzinfo is None:
            raise WindowsQualificationError(f"{label}_creation_time_invalid", stage="live_lineage")
        creation.append(created)
    if len(sessions) != 1 or creation != [*reversed(sorted(creation))]:
        # Input order is Python (newest), PowerShell, Codex (oldest).
        raise WindowsQualificationError(
            "lineage_session_or_creation_order_mismatch", stage="live_lineage"
        )
    if (
        codex.get("danger_full_access_flag_present") is not True
        or codex.get("approval_never_flag_present") is not True
        or codex.get("command_line_persisted") is not False
    ):
        raise WindowsQualificationError(
            "codex_command_policy_readback_mismatch", stage="live_lineage"
        )
    return value


def _validate_live_lineage_pins(
    config: QualificationConfig, value: dict[str, Any]
) -> dict[str, Any]:
    lineage = _validate_live_lineage(value)
    expected = {
        "python": (config.interpreter, config.interpreter_sha256),
        "powershell": (config.powershell, config.powershell_sha256),
        "codex": (config.codex, config.codex_sha256),
    }
    for label, (expected_path, expected_sha256) in expected.items():
        record = lineage[label]
        if (
            os.path.normcase(str(Path(record["path"]).resolve()))
            != os.path.normcase(str(Path(expected_path).resolve()))
            or record["image_sha256"] != expected_sha256
        ):
            raise WindowsQualificationError(
                f"{label}_path_or_sha_pin_mismatch", stage="live_lineage"
            )
    return lineage


def _measure_live_lineage() -> dict[str, Any]:
    runtime = _measure_process_live(os.getpid())
    powershell = _measure_process_live(runtime["ppid"])
    codex = _measure_process_live(powershell["ppid"])
    return _validate_live_lineage({"python": runtime, "powershell": powershell, "codex": codex})


def _validate_approval(value: InternalRootApproval, config: QualificationConfig) -> None:
    if type(value) is not InternalRootApproval:
        raise WindowsQualificationError("typed_internal_root_approval_required", stage="approval")
    if (
        value.schema != INTERNAL_APPROVAL_SCHEMA
        or value.run_uuid != config.run_uuid
        or value.attempt_uuid != config.attempt_uuid
        or type(value.root_pid) is not int
        or value.root_pid != os.getpid()
        or value.administrator is not True
        or value.integrity not in {"High", "System"}
        or value.token_elevation_type != "Full"
        or value.powershell_parent_observed is not True
        or value.approve_exactly_once is not True
        or value.internal_non_authoritative is not True
        or value.production_go is not False
    ):
        raise WindowsQualificationError("internal_root_approval_mismatch", stage="approval")


def _validate_config(config: QualificationConfig) -> tuple[Path, Path]:
    if type(config) is not QualificationConfig:
        raise WindowsQualificationError("typed_configuration_required", stage="preflight")
    _uuid4(config.run_uuid, "run")
    _uuid4(config.attempt_uuid, "attempt")
    pin_names = (
        "interpreter",
        "fixture",
        "qualifier",
        "runner_source",
        "powershell",
        "codex",
        "command_processor",
    )
    resolved: dict[str, Path] = {}
    for name in pin_names:
        candidate = Path(getattr(config, name))
        if not candidate.is_absolute():
            raise WindowsQualificationError(f"absolute_{name}_required", stage="preflight")
        candidate = candidate.resolve(strict=True)
        expected_sha256 = _hex64(getattr(config, f"{name}_sha256"), name)
        if _sha256(candidate.read_bytes()) != expected_sha256:
            raise WindowsQualificationError(f"{name}_sha256_mismatch", stage="preflight")
        resolved[name] = candidate
    if not Path(config.output_root).is_absolute():
        raise WindowsQualificationError("absolute_output_root_required", stage="preflight")
    pycache_prefix = Path(config.pycache_prefix)
    if not pycache_prefix.is_absolute():
        raise WindowsQualificationError("absolute_pycache_prefix_required", stage="preflight")
    if pycache_prefix.exists():
        raise WindowsQualificationError("pycache_prefix_initially_present", stage="preflight")
    return resolved["interpreter"], resolved["fixture"]


def _validate_runtime_pycache_prefix(config: QualificationConfig) -> None:
    if (
        not sys.pycache_prefix
        or os.path.normcase(str(Path(sys.pycache_prefix)))
        != os.path.normcase(str(Path(config.pycache_prefix)))
        or not sys.dont_write_bytecode
        or Path(config.pycache_prefix).exists()
    ):
        raise WindowsQualificationError(
            "isolated_runtime_pycache_prefix_mismatch",
            stage="bootstrap",
            counts=QualificationCallCounts(),
        )


def _validate_internal_runtime_bootstrap(
    config: QualificationConfig,
    measured_lineage: dict[str, Any],
    *,
    flags: Any = sys.flags,
) -> dict[str, Any]:
    _validate_config(config)
    if any(
        getattr(flags, name, None) != value for name, value in _REQUIRED_BOOTSTRAP_FLAGS.items()
    ):
        raise WindowsQualificationError("isolated_i_b_s_bootstrap_required", stage="bootstrap")
    qualifier_origin = Path(__file__).resolve(strict=True)
    runner_origin = Path(process_module.__file__).resolve(strict=True)
    if (
        qualifier_origin != Path(config.qualifier).resolve(strict=True)
        or runner_origin != Path(config.runner_source).resolve(strict=True)
        or not qualifier_origin.is_relative_to(PROJECT_ROOT)
        or not runner_origin.is_relative_to(PROJECT_ROOT)
    ):
        raise WindowsQualificationError("isolated_module_origin_mismatch", stage="bootstrap")
    return _validate_live_lineage_pins(config, measured_lineage)


def _parse_fixture_stdout(stdout: str, run_uuid: str) -> dict[str, Any]:
    if type(stdout) is not str or "\r" in stdout or not stdout.endswith("\n"):
        raise WindowsQualificationError("fixture_stdout_not_canonical", stage="observation")
    lines = stdout.splitlines()
    if len(lines) != 1:
        raise WindowsQualificationError("fixture_stdout_record_count_mismatch", stage="observation")
    try:
        value = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise WindowsQualificationError("fixture_stdout_invalid_json", stage="observation") from exc
    if type(value) is not dict or _canonical_json(value).decode() != stdout:
        raise WindowsQualificationError("fixture_stdout_not_canonical", stage="observation")
    expected_keys = {
        "breakaway",
        "capability",
        "pids",
        "pycache",
        "run_uuid",
        "schema",
        "stdio",
        "timing_contract",
        "tool_pins",
    }
    if set(value) != expected_keys or value["schema"] != FIXTURE_SCHEMA:
        raise WindowsQualificationError("fixture_schema_or_keys_mismatch", stage="observation")
    if value["run_uuid"] != run_uuid:
        raise WindowsQualificationError("fixture_run_uuid_mismatch", stage="observation")
    return value


def _event_sequence(outcome: ProcessOutcome, event: str, pid: int | None = None) -> int:
    matches = [
        item.sequence
        for item in outcome.events
        if item.event == event and (pid is None or item.pid == pid)
    ]
    if len(matches) != 1:
        raise WindowsQualificationError(
            f"required_event_multiplicity_mismatch:{event}:{pid}:{len(matches)}",
            stage="observation",
            classification="unproven",
        )
    return min(matches)


def _validate_zero_snapshots(accounting: tuple[JobAccountingSnapshot, ...]) -> None:
    zero_indexes = [
        index
        for index, item in enumerate(accounting)
        if item.active_processes == 0 and not item.active_pids
    ]
    if (
        len(accounting) < 2
        or zero_indexes != [len(accounting) - 2, len(accounting) - 1]
        or accounting[-2].sequence >= accounting[-1].sequence
        or accounting[-2].monotonic_ns >= accounting[-1].monotonic_ns
    ):
        raise WindowsQualificationError(
            "stable_zero_twice_unproven",
            stage="observation",
            classification="unproven",
        )


def _validate_outcome(outcome: ProcessOutcome, config: QualificationConfig) -> dict[str, Any]:
    if type(outcome) is not ProcessOutcome:
        raise WindowsQualificationError("typed_process_outcome_required", stage="observation")
    if (
        outcome.run_uuid != config.run_uuid
        or outcome.timed_out
        or outcome.cancelled
        or outcome.return_code != 0
        or outcome.manual_intervention_required
        or outcome.residual_pids
        or not outcome.stdout_drained
        or not outcome.stderr_drained
        or not outcome.streams_drained
        or not outcome.active_process_zero
        or outcome.final_active_process_count != 0
        or not outcome.identity_coverage_complete
        or not outcome.safe_for_followup
        or outcome.forced_termination_attempts != 0
        or outcome.job_limit_flags != 0
        or outcome.errors
        or outcome.stdout_capture_overflow
        or outcome.stderr_capture_overflow
    ):
        manual_latch = (
            outcome.manual_intervention_required
            or outcome.timed_out
            or outcome.cancelled
            or bool(outcome.residual_pids)
            or not outcome.streams_drained
            or not outcome.active_process_zero
            or outcome.final_active_process_count != 0
            or not outcome.safe_for_followup
        )
        raise WindowsQualificationError(
            "process_outcome_not_clean",
            stage="observation",
            classification=("manual_intervention_required" if manual_latch else "failed"),
        )

    fixture = _parse_fixture_stdout(outcome.stdout, config.run_uuid)
    pycache = fixture["pycache"]
    if (
        pycache
        != {
            "prefix": config.pycache_prefix,
            "initially_absent": True,
            "absent_before_root_exit": True,
            "dont_write_bytecode": True,
        }
        or Path(config.pycache_prefix).exists()
    ):
        raise WindowsQualificationError(
            "pycache_prefix_postcondition_unproven", stage="observation"
        )
    capability = fixture["capability"]
    capability_keys = {
        "environment_consumed",
        "explicit_job",
        "implicit_job",
        "nonce_commitment",
        "pid",
        "raw_nonce_recorded",
        "requested_access",
        "run_uuid",
        "schema",
        "snapshots_equal",
    }
    if (
        type(capability) is not dict
        or set(capability) != capability_keys
        or capability.get("schema") != "evm.phase-b2.windows-job-capability-consumption.v1"
        or capability.get("run_uuid") != config.run_uuid
        or type(capability.get("pid")) is not int
        or capability.get("pid", 0) <= 0
        or len(str(capability.get("nonce_commitment", ""))) != 64
        or not set(str(capability.get("nonce_commitment", ""))) <= _HEX64
        or capability.get("requested_access") != JOB_CAPABILITY_QUERY_ACCESS
        or capability.get("snapshots_equal") is not True
        or capability.get("environment_consumed") is not True
        or capability.get("raw_nonce_recorded") is not False
        or capability.get("explicit_job") != capability.get("implicit_job")
        or capability.get("explicit_job", {}).get("is_process_in_job") is not True
        or capability.get("explicit_job", {}).get("limit_flags") != 0
    ):
        raise WindowsQualificationError(
            "explicit_implicit_query_only_job_mismatch", stage="observation"
        )
    pids = fixture["pids"]
    required_pid_roles = {"root", "child", "grandchild", "closed_stdio", "console_child"}
    if type(pids) is not dict or set(pids) != required_pid_roles:
        raise WindowsQualificationError("fixture_pid_roles_mismatch", stage="observation")
    if (
        any(type(pid) is not int or pid <= 0 for pid in pids.values())
        or len(set(pids.values())) != 5
    ):
        raise WindowsQualificationError("fixture_pid_identity_invalid", stage="observation")
    identity_by_pid = {identity.pid: identity for identity in outcome.identities}
    if any(pid not in identity_by_pid for pid in pids.values()):
        raise WindowsQualificationError(
            "required_process_identity_unobserved",
            stage="observation",
            classification="unproven",
        )
    if identity_by_pid[pids["child"]].ppid != pids["root"]:
        raise WindowsQualificationError("child_lineage_mismatch", stage="observation")
    if identity_by_pid[pids["grandchild"]].ppid != pids["child"]:
        raise WindowsQualificationError("grandchild_lineage_mismatch", stage="observation")
    if capability["pid"] != pids["root"]:
        raise WindowsQualificationError("capability_root_pid_mismatch", stage="observation")
    if fixture["stdio"] != {"closed_stdio_child": True, "full_drain_required": True}:
        raise WindowsQualificationError("closed_stdio_contract_mismatch", stage="observation")
    if fixture["timing_contract"] != {
        "descendant_hold_seconds": 2.5,
        "child_handoff_seconds": 0.15,
        "minimum_descendant_margin_seconds": 2.35,
    }:
        raise WindowsQualificationError("fixture_timing_contract_mismatch", stage="observation")
    tool_pins = fixture["tool_pins"]
    expected_tool_pins = {
        "interpreter": {
            "path": str(Path(config.interpreter).resolve()),
            "sha256": config.interpreter_sha256,
        },
        "fixture": {
            "path": str(Path(config.fixture).resolve()),
            "sha256": config.fixture_sha256,
        },
        "command_processor": {
            "path": str(Path(config.command_processor).resolve()),
            "sha256": config.command_processor_sha256,
        },
    }
    if tool_pins != expected_tool_pins:
        raise WindowsQualificationError("fixture_tool_pin_mismatch", stage="observation")
    breakaway = fixture["breakaway"]
    if type(breakaway) is not dict or breakaway.get("attempted") is not True:
        raise WindowsQualificationError("breakaway_attempt_unproven", stage="observation")
    if (
        breakaway.get("denied") is not True
        or breakaway.get("error_code") != 5
        or breakaway.get("spawned_pid") is not None
    ):
        raise WindowsQualificationError("breakaway_denial_unproven", stage="observation")

    child_exit = _event_sequence(outcome, "job_exit_process", pids["child"])
    root_exit = _event_sequence(outcome, "job_exit_process", pids["root"])
    if child_exit >= root_exit:
        raise WindowsQualificationError(
            "child_exit_before_root_exit_unproven",
            stage="observation",
            classification="unproven",
        )
    for role in ("grandchild", "closed_stdio"):
        if _event_sequence(outcome, "job_exit_process", pids[role]) <= root_exit:
            raise WindowsQualificationError(
                f"root_exit_reparent_order_unproven:{role}",
                stage="observation",
                classification="unproven",
            )
    conhost = [
        identity
        for identity in outcome.identities
        if PurePath(identity.image.replace("\\", "/")).name.lower() == "conhost.exe"
    ]
    if not conhost:
        raise WindowsQualificationError(
            "conhost_descendant_unobserved",
            stage="observation",
            classification="unproven",
        )
    if len(conhost) != 1:
        raise WindowsQualificationError(
            f"conhost_descendant_multiplicity_mismatch:{len(conhost)}",
            stage="observation",
            classification="unproven",
        )
    _event_sequence(outcome, "job_exit_process", pids["console_child"])
    _event_sequence(outcome, "job_exit_process", conhost[0].pid)
    final_total = outcome.accounting[-1].total_processes if outcome.accounting else 0
    if not identity_coverage_complete(final_total, outcome.identities):
        raise WindowsQualificationError(
            "process_identity_coverage_unproven",
            stage="observation",
            classification="unproven",
        )
    all_sequences = [item.sequence for item in outcome.events] + [
        item.sequence for item in outcome.accounting
    ]
    if len(set(all_sequences)) != len(all_sequences) or any(value <= 0 for value in all_sequences):
        raise WindowsQualificationError("process_event_sequence_invalid", stage="observation")
    _validate_zero_snapshots(outcome.accounting)
    created = _event_sequence(outcome, "root_created_suspended", pids["root"])
    resumed = _event_sequence(outcome, "root_resumed", pids["root"])
    active_zero = _event_sequence(outcome, "active_process_count_zero")
    streams_drained = _event_sequence(outcome, "streams_drained")
    if not created < resumed < root_exit < active_zero < streams_drained:
        raise WindowsQualificationError("process_event_order_invalid", stage="observation")
    return {
        "child_grandchild_observed": True,
        "root_exit_before_descendant_exit_observed": True,
        "closed_stdio_residual_branch_observed": True,
        "breakaway_denied_observed": True,
        "conhost_descendant_observed": True,
        "explicit_implicit_query_only_job_snapshots_equal": True,
        "stable_zero_snapshot_count": 2,
        "streams_fully_drained": True,
        "residual_process_count": 0,
        "pycache_prefix_initially_and_post_run_absent": True,
    }


def _counts(**overrides: Any) -> QualificationCallCounts:
    values = asdict(QualificationCallCounts())
    values.update(overrides)
    return QualificationCallCounts(**values)


def _publication_failure_details(exc: BaseException) -> dict[str, Any] | None:
    observation = getattr(exc, "observation", None)
    if observation is None or not hasattr(observation, "to_dict"):
        return None
    value = observation.to_dict()
    return {
        "stage": value.get("stage"),
        "temporary_leaf": value.get("temporary_leaf"),
        "intended_final_path": value.get("intended_final_path"),
        "rename_completed": value.get("rename_completed"),
        "observation_status": value.get("observation_status"),
        "current_sha256": value.get("current_sha256"),
        "current_bytes": value.get("current_bytes"),
        "expected_sha256": value.get("expected_sha256"),
        "expected_bytes": value.get("expected_bytes"),
        "current_identity": value.get("current_identity"),
        "partial_preserved_unmodified": True,
        "cleanup_attempted": False,
        "retry_allowed": False,
    }


def _failure_raw(
    config: QualificationConfig,
    exc: BaseException,
    counts: QualificationCallCounts,
    outcome: ProcessOutcome | None,
) -> bytes:
    if isinstance(exc, WindowsQualificationError):
        stage = exc.stage
        classification = exc.classification
    elif isinstance(exc, ProcessContainmentFailure):
        stage = exc.stage
        classification = (
            "manual_intervention_required"
            if (
                exc.manual_intervention_required
                or exc.residual_pids
                or exc.timed_out
                or exc.cancelled
            )
            else "failed"
        )
    else:
        stage = "unexpected"
        classification = "failed"
    return _canonical_json(
        {
            "schema": FAILURE_SEAL_SCHEMA,
            "authority": "internal_non_authoritative",
            "status": classification,
            "decision": "NO-GO",
            "credit": "zero_credit",
            "reviewer_pending": True,
            "run_uuid": config.run_uuid,
            "attempt_uuid": config.attempt_uuid,
            "failed_stage": stage,
            "error": f"{type(exc).__name__}:{exc}",
            "partial_artifact": _publication_failure_details(exc),
            "process_outcome": None if outcome is None else outcome.to_dict(),
            "process_containment_failure": (
                exc.to_dict() if isinstance(exc, ProcessContainmentFailure) else None
            ),
            "call_counts": asdict(counts),
            "automatic_retry_count": 0,
            "followup_probe_count": 0,
            "force_termination_count": 0,
            "success_marker_count": 0,
            "completion_marker_count": 0,
            "production_go": False,
        }
    )


def _emergency_raw(
    config: QualificationConfig,
    original_exc: BaseException,
    seal_exc: BaseException,
    counts: QualificationCallCounts,
) -> bytes:
    return _canonical_json(
        {
            "schema": EMERGENCY_SEAL_SCHEMA,
            "authority": "internal_non_authoritative",
            "status": "manual_intervention_required",
            "decision": "NO-GO",
            "credit": "zero_credit",
            "reviewer_pending": True,
            "run_uuid": config.run_uuid,
            "attempt_uuid": config.attempt_uuid,
            "original_error": f"{type(original_exc).__name__}:{original_exc}",
            "original_partial_artifact": _publication_failure_details(original_exc),
            "failure_seal_error": f"{type(seal_exc).__name__}:{seal_exc}",
            "failure_seal_partial_artifact": _publication_failure_details(seal_exc),
            "call_counts": asdict(counts),
            "automatic_retry_count": 0,
            "followup_probe_count": 0,
            "force_termination_count": 0,
            "success_marker_count": 0,
            "completion_marker_count": 0,
            "cleanup_attempted": False,
            "production_go": False,
        }
    )


def _reservation_namespace_observation(exc: BaseException) -> dict[str, Any]:
    partial = _publication_failure_details(exc)
    collision = str(exc) in {
        "run_uuid_already_reserved",
        "reserved_run_directory_collision",
    }
    if partial is None:
        publication_state = "not_started"
    elif partial.get("rename_completed") is True:
        publication_state = "final_renamed"
    elif partial.get("current_sha256") is not None:
        publication_state = "partial"
    else:
        publication_state = "not_started"
    return {
        "publication_state": publication_state,
        "existing_namespace_collision_observed": collision,
        "reservation_parent_namespace_touched": publication_state != "not_started",
        "existing_run_namespace_touched": (
            False if collision or publication_state == "not_started" else "unproven"
        ),
        "observation_source": (
            "durable_publication_same_handle_observation"
            if partial is not None
            else "pre_write_collision_or_exception_observation"
        ),
    }


def _pre_reservation_failure_raw(
    config: QualificationConfig,
    exc: BaseException,
    counts: QualificationCallCounts,
) -> bytes:
    stage = exc.stage if isinstance(exc, WindowsQualificationError) else "reservation"
    namespace_observation = _reservation_namespace_observation(exc)
    return _canonical_json(
        {
            "schema": PRE_RESERVATION_FAILURE_SEAL_SCHEMA,
            "authority": "internal_non_authoritative",
            "seal_scope": "parent_sibling_outside_existing_run_namespace",
            "status": "failed",
            "decision": "NO-GO",
            "credit": "zero_credit",
            "reviewer_pending": True,
            "run_uuid": config.run_uuid,
            "attempt_uuid": config.attempt_uuid,
            "failed_stage": stage,
            "error": f"{type(exc).__name__}:{exc}",
            "reservation_partial_artifact": _publication_failure_details(exc),
            "namespace_touch_observation": namespace_observation,
            "existing_run_namespace_touched": namespace_observation[
                "existing_run_namespace_touched"
            ],
            "existing_run_artifact_overwrite_attempted": False,
            "process_outcome": None,
            "call_counts": asdict(counts),
            "automatic_retry_count": 0,
            "followup_probe_count": 0,
            "force_termination_count": 0,
            "success_marker_count": 0,
            "completion_marker_count": 0,
            "production_go": False,
        }
    )


def _pre_reservation_emergency_raw(
    config: QualificationConfig,
    reservation_exc: BaseException,
    seal_exc: BaseException,
    counts: QualificationCallCounts,
) -> bytes:
    namespace_observation = _reservation_namespace_observation(reservation_exc)
    return _canonical_json(
        {
            "schema": PRE_RESERVATION_EMERGENCY_SEAL_SCHEMA,
            "authority": "internal_non_authoritative",
            "seal_scope": "parent_sibling_outside_existing_run_namespace",
            "status": "manual_intervention_required",
            "decision": "NO-GO",
            "credit": "zero_credit",
            "reviewer_pending": True,
            "run_uuid": config.run_uuid,
            "attempt_uuid": config.attempt_uuid,
            "reservation_error": f"{type(reservation_exc).__name__}:{reservation_exc}",
            "reservation_partial_artifact": _publication_failure_details(reservation_exc),
            "failure_seal_error": f"{type(seal_exc).__name__}:{seal_exc}",
            "failure_seal_partial_artifact": _publication_failure_details(seal_exc),
            "namespace_touch_observation": namespace_observation,
            "existing_run_namespace_touched": namespace_observation[
                "existing_run_namespace_touched"
            ],
            "existing_run_artifact_overwrite_attempted": False,
            "call_counts": asdict(counts),
            "automatic_retry_count": 0,
            "followup_probe_count": 0,
            "force_termination_count": 0,
            "success_marker_count": 0,
            "completion_marker_count": 0,
            "cleanup_attempted": False,
            "production_go": False,
        }
    )


def _seal_pre_reservation_failure(
    config: QualificationConfig,
    store: Store,
    exc: BaseException,
    counts: QualificationCallCounts,
) -> NoReturn:
    failure_counts = _counts(**{**asdict(counts), "failure_seal_publication": 1})
    try:
        publication = store.publish_pre_reservation_failure_once(
            config.run_uuid,
            config.attempt_uuid,
            _pre_reservation_failure_raw(config, exc, failure_counts),
        )
    except BaseException as seal_exc:
        emergency_counts = _counts(**{**asdict(failure_counts), "emergency_seal_publication": 1})
        try:
            emergency = store.publish_pre_reservation_emergency_once(
                config.run_uuid,
                config.attempt_uuid,
                _pre_reservation_emergency_raw(config, exc, seal_exc, emergency_counts),
            )
        except BaseException as emergency_exc:
            raise WindowsQualificationError(
                "pre_reservation_emergency_seal_publication_failed:"
                f"{type(emergency_exc).__name__}:{emergency_exc}",
                stage="pre_reservation_emergency_seal",
                counts=emergency_counts,
                publication_observation=getattr(emergency_exc, "observation", None),
            ) from emergency_exc
        raise WindowsQualificationError(
            f"pre_reservation_failure_seal_publication_failed:{type(seal_exc).__name__}:{seal_exc}",
            stage="pre_reservation_failure_seal",
            counts=emergency_counts,
            emergency_publication=emergency,
            publication_observation=getattr(seal_exc, "observation", None),
        ) from seal_exc
    if isinstance(exc, WindowsQualificationError):
        raise WindowsQualificationError(
            str(exc),
            stage=exc.stage,
            classification=exc.classification,
            counts=failure_counts,
            failure_publication=publication,
            publication_observation=getattr(exc, "observation", None),
        ) from exc
    raise WindowsQualificationError(
        f"reservation_failed:{type(exc).__name__}:{exc}",
        stage="reservation",
        counts=failure_counts,
        failure_publication=publication,
        publication_observation=getattr(exc, "observation", None),
    ) from exc


def _qualify_windows_non_credit_for_test(
    config: QualificationConfig,
    approval: InternalRootApproval,
    *,
    store: Store,
    runner_factory: Callable[[TimeoutContract], Runner] = WindowsJobProcessRunner,
    measured_lineage: dict[str, Any] | None = None,
    verified_work_order: (
        qualification_work_order.VerifiedInternalQualificationWorkOrder | None
    ) = None,
) -> dict[str, Any]:
    """Run one internal-only qualification after explicit root approval."""

    interpreter, fixture = _validate_config(config)
    _validate_approval(approval, config)
    counts = _counts(reservation=1)
    try:
        store.reserve_once(config.run_uuid, config.attempt_uuid)
    except BaseException as exc:
        _seal_pre_reservation_failure(config, store, exc, counts)
    outcome: ProcessOutcome | None = None
    try:
        command = [
            str(interpreter),
            "-I",
            "-B",
            "-S",
            "-X",
            f"pycache_prefix={config.pycache_prefix}",
            str(fixture),
            "--mode",
            "root",
            "--run-uuid",
            config.run_uuid,
            "--pycache-prefix",
            config.pycache_prefix,
            "--interpreter-sha256",
            config.interpreter_sha256,
            "--fixture-sha256",
            config.fixture_sha256,
            "--command-processor",
            config.command_processor,
            "--command-processor-sha256",
            config.command_processor_sha256,
        ]
        runner = runner_factory(
            TimeoutContract(
                kubectl_timeout_seconds=1,
                wrapper_timeout_seconds=15,
                restore_deadline_seconds=150,
                residual_repoll_seconds=120,
                stream_drain_seconds=5,
            )
        )
        counts = _counts(
            reservation=1,
            process_creation_requested=1,
            process_creation=0,
            child_created_observed=None,
            runner_invocation=1,
        )
        outcome = runner.run(
            command,
            name=f"pre-r8-r7s7-windows-{config.run_uuid}",
            cwd=str(PROJECT_ROOT),
            env={},
            poll_interval_seconds=0.001,
            run_uuid=config.run_uuid,
            create_no_window=False,
            expected_executable_sha256=config.interpreter_sha256,
        )
        child_created = type(outcome) is ProcessOutcome and any(
            event.event == "root_created_suspended" for event in outcome.events
        )
        counts = _counts(
            **{
                **asdict(counts),
                "process_creation": int(child_created),
                "child_created_observed": child_created,
            }
        )
        observations = _validate_outcome(outcome, config)
        counts = _counts(**{**asdict(counts), "evidence_publication": 1})
        payload = {
            "schema": INTERNAL_EVIDENCE_SCHEMA,
            "authority": "internal_non_authoritative",
            "qualification": "windows_non_credit",
            "status": "internally_observed",
            "decision": "NO-GO",
            "credit": "zero_credit",
            "reviewer_pending": True,
            "r8_or_phase_b2_completion": False,
            "run_uuid": config.run_uuid,
            "attempt_uuid": config.attempt_uuid,
            "interpreter_sha256": config.interpreter_sha256,
            "fixture_sha256": config.fixture_sha256,
            "pycache_prefix": config.pycache_prefix,
            "pycache_prefix_initially_and_post_run_absent": True,
            "tool_pins": {
                "qualifier": {
                    "path": config.qualifier,
                    "sha256": config.qualifier_sha256,
                },
                "runner_source": {
                    "path": config.runner_source,
                    "sha256": config.runner_source_sha256,
                },
                "powershell": {
                    "path": config.powershell,
                    "sha256": config.powershell_sha256,
                },
                "codex": {"path": config.codex, "sha256": config.codex_sha256},
                "command_processor": {
                    "path": config.command_processor,
                    "sha256": config.command_processor_sha256,
                },
            },
            "root_authority_measurement": (
                measured_lineage
                if measured_lineage is not None
                else {
                    "scope": "test_only_caller_supplied_typed_approval",
                    "live_win32_measurement": False,
                    "production_go": False,
                }
            ),
            "internal_work_order_binding": (
                None if verified_work_order is None else verified_work_order.to_dict()
            ),
            "observations": observations,
            "process_outcome": outcome.to_dict(),
            "call_counts": asdict(counts),
            "automatic_retry_count": 0,
            "followup_probe_count": 0,
            "force_termination_count": 0,
            "success_marker_count": 0,
            "completion_marker_count": 0,
            "production_go": False,
        }
        raw = _canonical_json(payload)
        publication = store.publish_evidence_once(config.run_uuid, raw)
        return {
            "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-internal-result.v1",
            "status": "internal_non_authoritative",
            "decision": "NO-GO",
            "credit": "zero_credit",
            "reviewer_pending": True,
            "publication": asdict(publication),
            "call_counts": payload["call_counts"],
            "production_go": False,
        }
    except BaseException as exc:
        if isinstance(exc, ProcessContainmentFailure):
            counts = _counts(
                **{
                    **asdict(counts),
                    "process_creation": int(exc.child_created),
                    "child_created_observed": exc.child_created,
                }
            )
        failure_counts = _counts(**{**asdict(counts), "failure_seal_publication": 1})
        try:
            publication = store.publish_failure_once(
                config.run_uuid, _failure_raw(config, exc, failure_counts, outcome)
            )
        except BaseException as seal_exc:
            emergency_counts = _counts(
                **{
                    **asdict(failure_counts),
                    "emergency_seal_publication": 1,
                }
            )
            try:
                emergency = store.publish_emergency_once(
                    config.run_uuid,
                    _emergency_raw(config, exc, seal_exc, emergency_counts),
                )
            except BaseException as emergency_exc:
                raise WindowsQualificationError(
                    "emergency_seal_publication_failed:"
                    f"{type(emergency_exc).__name__}:{emergency_exc}",
                    stage="emergency_seal",
                    counts=emergency_counts,
                ) from emergency_exc
            raise WindowsQualificationError(
                f"failure_seal_publication_failed:{type(seal_exc).__name__}:{seal_exc}",
                stage="failure_seal",
                counts=emergency_counts,
                emergency_publication=emergency,
            ) from seal_exc
        if isinstance(exc, WindowsQualificationError):
            raise WindowsQualificationError(
                str(exc),
                stage=exc.stage,
                classification=exc.classification,
                counts=failure_counts,
                failure_publication=publication,
            ) from exc
        raise WindowsQualificationError(
            f"qualification_failed:{type(exc).__name__}:{exc}",
            stage=(exc.stage if isinstance(exc, ProcessContainmentFailure) else "unexpected"),
            classification=(
                "manual_intervention_required"
                if isinstance(exc, ProcessContainmentFailure)
                and (
                    exc.manual_intervention_required
                    or exc.residual_pids
                    or exc.timed_out
                    or exc.cancelled
                )
                else "failed"
            ),
            counts=failure_counts,
            failure_publication=publication,
        ) from exc


def _approval_from_measured_lineage(
    config: QualificationConfig, measured: dict[str, Any]
) -> InternalRootApproval:
    lineage = _validate_live_lineage(measured)
    runtime = lineage["python"]
    return InternalRootApproval(
        schema=INTERNAL_APPROVAL_SCHEMA,
        run_uuid=config.run_uuid,
        attempt_uuid=config.attempt_uuid,
        root_pid=runtime["pid"],
        administrator=runtime["token"]["administrator"],
        integrity=runtime["token"]["integrity"],
        token_elevation_type=runtime["token"]["token_elevation_type"],
        powershell_parent_observed=True,
        approve_exactly_once=True,
        internal_non_authoritative=True,
        production_go=False,
    )


def _run_internal_non_authoritative_once_for_test(
    config: QualificationConfig,
    *,
    lineage_probe: Callable[[], dict[str, Any]],
    runner_factory: Callable[[TimeoutContract], Runner],
    store: Store,
) -> dict[str, Any]:
    """Private seam proving that live lineage precedes reservation and launch."""

    measured = _validate_internal_runtime_bootstrap(config, lineage_probe())
    return _qualify_windows_non_credit_for_test(
        config,
        _approval_from_measured_lineage(config, measured),
        store=store,
        runner_factory=runner_factory,
        measured_lineage=measured,
    )


def run_internal_non_authoritative_once(
    config: QualificationConfig,
    *,
    work_order: (qualification_work_order.VerifiedInternalQualificationWorkOrder | None) = None,
) -> dict[str, Any]:
    """Measure the actual Win32 lineage, then run one non-authoritative candidate.

    No caller-supplied administrator, integrity, parent, command-policy, or
    approval boolean is accepted.  This entry is intentionally absent from the
    CLI and still returns NO-GO/zero-credit evidence.  A controlling root may
    call this function exactly once from the trusted isolated ``python -I -B
    -S`` outer after the complete regression gate.  A sentinel-sealed typed
    work-order token, whose immutable projection exactly matches ``config``,
    is required before lineage measurement, reservation, or process creation.
    """

    if type(config) is not QualificationConfig:
        raise WindowsQualificationError(
            "typed_configuration_required",
            stage="internal_work_order",
            counts=QualificationCallCounts(),
        )
    try:
        verified = qualification_work_order.require_verified_qualification_work_order(
            work_order,
            config=config,
        )
    except qualification_work_order.QualificationWorkOrderError as exc:
        raise WindowsQualificationError(
            exc.code,
            stage="internal_work_order",
            counts=QualificationCallCounts(),
        ) from exc
    if verified.order.toolchain_runtime_closure_state != "verified":
        raise WindowsQualificationError(
            "toolchain_runtime_closure_unproven",
            stage="internal_work_order",
            classification="unproven",
            counts=QualificationCallCounts(),
        )
    _validate_runtime_pycache_prefix(config)
    measured = _validate_internal_runtime_bootstrap(config, _measure_live_lineage())
    return _qualify_windows_non_credit_for_test(
        config,
        _approval_from_measured_lineage(config, measured),
        store=FileQualificationStore(config.output_root),
        runner_factory=WindowsJobProcessRunner,
        measured_lineage=measured,
        verified_work_order=verified,
    )


def qualify_windows_non_credit(*_args: Any, **_kwargs: Any) -> NoReturn:
    """Public production-shaped entry; closed before runner construction."""

    if not PUBLIC_EXTERNAL_AUTHORITY_CONFIGURED:
        raise WindowsQualificationError(
            "external_authority_receipt_adapter_unprovisioned",
            stage="root_gate",
            counts=QualificationCallCounts(),
        )
    if not PUBLIC_PROCESS_CREATION_ENABLED:
        raise WindowsQualificationError(
            "public_process_creation_disabled",
            stage="root_gate",
            counts=QualificationCallCounts(),
        )
    raise WindowsQualificationError(
        "public_qualification_adapter_not_implemented",
        stage="root_gate",
        counts=QualificationCallCounts(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authority-receipt", type=Path)
    parser.parse_args(argv)
    try:
        qualify_windows_non_credit()
    except WindowsQualificationError as exc:
        print(
            _canonical_json(
                {
                    "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-rejection.v1",
                    "status": "reviewer_pending",
                    "decision": "NO-GO",
                    "credit": "zero_credit",
                    "error": str(exc),
                    "stage": exc.stage,
                    "call_counts": asdict(exc.counts),
                    "production_go": False,
                }
            ).decode(),
            end="",
            file=sys.stderr,
        )
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
