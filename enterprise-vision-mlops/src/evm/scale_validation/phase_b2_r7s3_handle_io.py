"""Handle-bound Windows I/O primitives for the pre-r8 r7s3 trust boundary.

The older r7s2 harness rejects reparse points before opening a path.  That is a
useful policy check, but it leaves a check/open race.  This module treats an
already-open kernel handle as the authority: metadata, security descriptor,
content, flush, rename, and read-back are all performed through that handle.

The module deliberately does *not* claim protection from a hostile process
running with the same elevated token.  That stronger boundary requires an
external supervisor/principal and an independently controlled evidence store.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import hashlib
import ntpath
import os
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol


HEX64_RE = re.compile(r"[0-9a-f]{64}")
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_TYPE_DISK = 0x0001


class HandleBoundIoError(RuntimeError):
    """Raised when a handle-bound identity or publication invariant fails."""


@dataclass(frozen=True)
class HandleIdentity:
    """Stable evidence read from one open Windows file handle."""

    final_path: str
    volume_serial_number: int
    file_id_hex: str
    size: int
    link_count: int
    attributes: int
    reparse_tag: int
    file_type: int
    owner_sid: str
    security_descriptor_sha256: str
    dacl_present: bool
    dacl_protected: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BoundRead:
    """Bytes and evidence obtained without reopening the display path."""

    raw: bytes
    identity: HandleIdentity
    sha256: str

    @property
    def pin(self) -> dict[str, Any]:
        return {
            "path": self.identity.final_path,
            "sha256": self.sha256,
            "bytes": len(self.raw),
            "volume_serial_number": self.identity.volume_serial_number,
            "file_id_hex": self.identity.file_id_hex,
            "security_descriptor_sha256": self.identity.security_descriptor_sha256,
        }


@dataclass(frozen=True)
class BoundPublication:
    """Evidence for a create-exclusive, flush, rename, and same-handle read-back."""

    final_path: str
    temporary_leaf: str
    sha256: str
    bytes: int
    identity: HandleIdentity
    directory_identity: HandleIdentity

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["identity"] = self.identity.to_dict()
        value["directory_identity"] = self.directory_identity.to_dict()
        return value


class HandleApi(Protocol):
    """Small seam used by adversarial tests and the Win32 implementation."""

    def open_read(self, path: str) -> int: ...

    def open_directory(self, path: str) -> int: ...

    def create_relative_file(self, directory_handle: int, leaf: str) -> int: ...

    def protect_dacl(self, handle: int) -> None: ...

    def identity(self, handle: int) -> HandleIdentity: ...

    def read_all(self, handle: int, expected_size: int) -> bytes: ...

    def write_all(self, handle: int, raw: bytes) -> None: ...

    def flush(self, handle: int) -> None: ...

    def rename_no_replace(self, handle: int, directory_handle: int, leaf: str) -> None: ...

    def close(self, handle: int | None) -> None: ...


def _normal_path(value: str | os.PathLike[str]) -> str:
    raw = os.fspath(value)
    if not raw or "\x00" in raw:
        raise HandleBoundIoError("path_empty_or_nul")
    if not ntpath.isabs(raw):
        raise HandleBoundIoError("absolute_windows_path_required")
    normalized = ntpath.normpath(raw)
    for component in normalized.replace("/", "\\").split("\\"):
        if component and component not in {".", ".."} and component[-1:] in {" ", "."}:
            raise HandleBoundIoError("trailing_dot_or_space_component")
    return normalized


def _comparable_path(value: str) -> str:
    normalized = value
    if normalized.startswith("\\\\?\\UNC\\"):
        normalized = "\\\\" + normalized[8:]
    elif normalized.startswith("\\\\?\\"):
        normalized = normalized[4:]
    return ntpath.normcase(ntpath.normpath(normalized))


def _validate_leaf(leaf: str) -> str:
    if (
        not leaf
        or leaf in {".", ".."}
        or ntpath.basename(leaf) != leaf
        or "/" in leaf
        or "\\" in leaf
        or "\x00" in leaf
        or leaf[-1:] in {" ", "."}
    ):
        raise HandleBoundIoError("invalid_relative_leaf")
    return leaf


def _reject_unsafe_identity(
    identity: HandleIdentity,
    *,
    expected_path: str,
    require_protected_dacl: bool,
) -> None:
    if _comparable_path(identity.final_path) != _comparable_path(expected_path):
        raise HandleBoundIoError("handle_final_path_mismatch")
    if identity.file_type != FILE_TYPE_DISK:
        raise HandleBoundIoError("handle_not_disk_file")
    if identity.attributes & FILE_ATTRIBUTE_DIRECTORY:
        raise HandleBoundIoError("handle_is_directory")
    if identity.reparse_tag != 0:
        raise HandleBoundIoError("handle_reparse_tag_present")
    if identity.size < 0 or identity.link_count != 1:
        raise HandleBoundIoError("handle_size_or_link_count_invalid")
    if identity.volume_serial_number <= 0 or not identity.file_id_hex:
        raise HandleBoundIoError("handle_file_identity_missing")
    if not identity.owner_sid or not identity.dacl_present:
        raise HandleBoundIoError("handle_owner_or_dacl_missing")
    if require_protected_dacl and not identity.dacl_protected:
        raise HandleBoundIoError("handle_dacl_not_protected")
    if HEX64_RE.fullmatch(identity.security_descriptor_sha256) is None:
        raise HandleBoundIoError("handle_security_descriptor_hash_invalid")


def _validate_expected_pin(pin: Mapping[str, Any], result: BoundRead) -> None:
    required = {
        "path",
        "sha256",
        "bytes",
        "volume_serial_number",
        "file_id_hex",
        "security_descriptor_sha256",
    }
    if set(pin) != required:
        raise HandleBoundIoError("expected_pin_fields_mismatch")
    if pin != result.pin:
        raise HandleBoundIoError("expected_pin_mismatch")


def _reject_unsafe_directory_identity(identity: HandleIdentity, *, expected_path: str) -> None:
    if _comparable_path(identity.final_path) != _comparable_path(expected_path):
        raise HandleBoundIoError("directory_handle_final_path_mismatch")
    if identity.file_type != FILE_TYPE_DISK:
        raise HandleBoundIoError("directory_handle_not_disk")
    if not identity.attributes & FILE_ATTRIBUTE_DIRECTORY:
        raise HandleBoundIoError("directory_handle_not_directory")
    if identity.reparse_tag != 0:
        raise HandleBoundIoError("directory_handle_reparse_tag_present")
    if identity.link_count < 1 or identity.volume_serial_number < 0 or not identity.file_id_hex:
        raise HandleBoundIoError("directory_handle_identity_invalid")
    if not identity.owner_sid or not identity.dacl_present:
        raise HandleBoundIoError("directory_handle_owner_or_dacl_missing")
    if HEX64_RE.fullmatch(identity.security_descriptor_sha256) is None:
        raise HandleBoundIoError("directory_handle_security_descriptor_hash_invalid")


def read_bound_file(
    path: str | os.PathLike[str],
    *,
    expected_pin: Mapping[str, Any] | None = None,
    require_protected_dacl: bool = True,
    api: HandleApi | None = None,
) -> BoundRead:
    """Open once, verify identity, read, and verify the same handle again."""

    display_path = _normal_path(path)
    implementation: HandleApi = api or WindowsHandleApi()
    handle: int | None = None
    try:
        handle = implementation.open_read(display_path)
        before = implementation.identity(handle)
        _reject_unsafe_identity(
            before,
            expected_path=display_path,
            require_protected_dacl=require_protected_dacl,
        )
        raw = implementation.read_all(handle, before.size)
        after = implementation.identity(handle)
        if before != after:
            raise HandleBoundIoError("handle_identity_changed_during_read")
        if len(raw) != before.size:
            raise HandleBoundIoError("handle_read_size_mismatch")
        result = BoundRead(raw=raw, identity=before, sha256=hashlib.sha256(raw).hexdigest())
        if expected_pin is not None:
            _validate_expected_pin(expected_pin, result)
        return result
    finally:
        implementation.close(handle)


def publish_bound_no_replace(
    directory: str | os.PathLike[str],
    final_leaf: str,
    raw: bytes,
    *,
    run_uuid: str | uuid.UUID,
    require_protected_dacl: bool = True,
    api: HandleApi | None = None,
) -> BoundPublication:
    """Publish bytes with relative create/rename and same-handle read-back.

    A failed temporary file is intentionally left in place.  Callers must bind
    its file identity and digest into a failure seal; retrying the same run is
    outside this primitive's contract.
    """

    directory_path = _normal_path(directory)
    leaf = _validate_leaf(final_leaf)
    try:
        run_value = str(uuid.UUID(str(run_uuid)))
    except ValueError as exc:
        raise HandleBoundIoError("run_uuid_invalid") from exc
    temporary_leaf = _validate_leaf(f".{leaf}.{run_value}.partial")
    implementation: HandleApi = api or WindowsHandleApi()
    directory_handle: int | None = None
    file_handle: int | None = None
    try:
        directory_handle = implementation.open_directory(directory_path)
        directory_before = implementation.identity(directory_handle)
        _reject_unsafe_directory_identity(directory_before, expected_path=directory_path)
        file_handle = implementation.create_relative_file(directory_handle, temporary_leaf)
        if require_protected_dacl:
            implementation.protect_dacl(file_handle)
        implementation.write_all(file_handle, raw)
        implementation.flush(file_handle)
        before = implementation.identity(file_handle)
        expected_temp = ntpath.join(directory_path, temporary_leaf)
        _reject_unsafe_identity(
            before,
            expected_path=expected_temp,
            require_protected_dacl=require_protected_dacl,
        )
        if before.size != len(raw):
            raise HandleBoundIoError("temporary_size_mismatch")
        readback = implementation.read_all(file_handle, before.size)
        if readback != raw:
            raise HandleBoundIoError("temporary_readback_mismatch")
        implementation.rename_no_replace(file_handle, directory_handle, leaf)
        implementation.flush(file_handle)
        after = implementation.identity(file_handle)
        expected_final = ntpath.join(directory_path, leaf)
        _reject_unsafe_identity(
            after,
            expected_path=expected_final,
            require_protected_dacl=require_protected_dacl,
        )
        if (
            before.volume_serial_number != after.volume_serial_number
            or before.file_id_hex != after.file_id_hex
            or before.security_descriptor_sha256 != after.security_descriptor_sha256
            or after.size != len(raw)
        ):
            raise HandleBoundIoError("published_file_identity_changed")
        final_readback = implementation.read_all(file_handle, after.size)
        if final_readback != raw:
            raise HandleBoundIoError("published_readback_mismatch")
        directory_after = implementation.identity(directory_handle)
        _reject_unsafe_directory_identity(directory_after, expected_path=directory_path)
        if (
            directory_after.volume_serial_number != directory_before.volume_serial_number
            or directory_after.file_id_hex != directory_before.file_id_hex
            or directory_after.link_count != directory_before.link_count
            or directory_after.reparse_tag != directory_before.reparse_tag
            or directory_after.file_type != directory_before.file_type
            or directory_after.owner_sid != directory_before.owner_sid
            or directory_after.security_descriptor_sha256
            != directory_before.security_descriptor_sha256
            or directory_after.dacl_present != directory_before.dacl_present
            or directory_after.dacl_protected != directory_before.dacl_protected
        ):
            changed = sorted(
                name
                for name in directory_before.to_dict()
                if name not in {"attributes", "size"}
                and directory_before.to_dict()[name] != directory_after.to_dict()[name]
            )
            raise HandleBoundIoError(f"directory_handle_identity_changed:{','.join(changed)}")
        return BoundPublication(
            final_path=after.final_path,
            temporary_leaf=temporary_leaf,
            sha256=hashlib.sha256(raw).hexdigest(),
            bytes=len(raw),
            identity=after,
            directory_identity=directory_after,
        )
    finally:
        implementation.close(file_handle)
        implementation.close(directory_handle)


if os.name == "nt":

    class _FILE_ID_128(ctypes.Structure):
        _fields_ = [("identifier", ctypes.c_ubyte * 16)]

    class _FILE_ID_INFO(ctypes.Structure):
        _fields_ = [
            ("volume_serial_number", ctypes.c_ulonglong),
            ("file_id", _FILE_ID_128),
        ]

    class _FILE_STANDARD_INFO(ctypes.Structure):
        _fields_ = [
            ("allocation_size", ctypes.c_longlong),
            ("end_of_file", ctypes.c_longlong),
            ("number_of_links", wintypes.DWORD),
            # Win32 BOOLEAN is one byte.  wintypes.BOOL is a four-byte LONG
            # and would silently give this SDK structure the wrong ABI.
            ("delete_pending", ctypes.c_ubyte),
            ("directory", ctypes.c_ubyte),
        ]

    class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [("file_attributes", wintypes.DWORD), ("reparse_tag", wintypes.DWORD)]

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ushort),
            ("maximum_length", ctypes.c_ushort),
            ("buffer", wintypes.LPWSTR),
        ]

    class _OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.ULONG),
            ("root_directory", wintypes.HANDLE),
            ("object_name", ctypes.POINTER(_UNICODE_STRING)),
            ("attributes", wintypes.ULONG),
            ("security_descriptor", wintypes.LPVOID),
            ("security_quality_of_service", wintypes.LPVOID),
        ]

    class _IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = [("status", ctypes.c_ssize_t), ("information", ctypes.c_size_t)]

    class _FILE_RENAME_INFO(ctypes.Structure):
        _fields_ = [
            ("replace_if_exists", wintypes.BOOL),
            ("root_directory", wintypes.HANDLE),
            ("file_name_length", wintypes.DWORD),
            ("file_name", wintypes.WCHAR * 1),
        ]


class WindowsHandleApi:
    """Win32/NT implementation; constructed only on Windows."""

    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _DELETE = 0x00010000
    _READ_CONTROL = 0x00020000
    _WRITE_DAC = 0x00040000
    _SYNCHRONIZE = 0x00100000
    _FILE_LIST_DIRECTORY = 0x00000001
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _OPEN_EXISTING = 3
    _FILE_CREATE = 2
    _FILE_NON_DIRECTORY_FILE = 0x00000040
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    _FILE_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
    _OBJ_CASE_INSENSITIVE = 0x00000040
    _FILE_ID_INFO_CLASS = 18
    _FILE_STANDARD_INFO_CLASS = 1
    _FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
    _FILE_RENAME_INFORMATION_CLASS = 10
    _OWNER_SECURITY_INFORMATION = 0x00000001
    _GROUP_SECURITY_INFORMATION = 0x00000002
    _DACL_SECURITY_INFORMATION = 0x00000004
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _SE_FILE_OBJECT = 1
    _SE_DACL_PRESENT = 0x0004
    _SE_DACL_PROTECTED = 0x1000
    _FILE_BEGIN = 0
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def __init__(self) -> None:
        if os.name != "nt":
            raise HandleBoundIoError("windows_handle_io_requires_windows")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self.ntdll = ctypes.WinDLL("ntdll")
        self._declare()

    @staticmethod
    def _raise(label: str) -> None:
        raise HandleBoundIoError(f"{label}:win32={ctypes.get_last_error()}")

    def _declare(self) -> None:
        k32 = self.kernel32
        k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        k32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        k32.GetFinalPathNameByHandleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        k32.GetFileType.argtypes = [wintypes.HANDLE]
        k32.GetFileType.restype = wintypes.DWORD
        k32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        k32.ReadFile.restype = wintypes.BOOL
        k32.WriteFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        k32.WriteFile.restype = wintypes.BOOL
        k32.SetFilePointerEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong),
            wintypes.DWORD,
        ]
        k32.SetFilePointerEx.restype = wintypes.BOOL
        k32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        k32.FlushFileBuffers.restype = wintypes.BOOL
        k32.LocalFree.argtypes = [wintypes.HLOCAL]
        k32.LocalFree.restype = wintypes.HLOCAL

        advapi = self.advapi32
        advapi.GetSecurityInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
        ]
        advapi.GetSecurityInfo.restype = wintypes.DWORD
        advapi.SetSecurityInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.LPVOID,
        ]
        advapi.SetSecurityInfo.restype = wintypes.DWORD
        advapi.GetSecurityDescriptorLength.argtypes = [wintypes.LPVOID]
        advapi.GetSecurityDescriptorLength.restype = wintypes.DWORD
        advapi.GetSecurityDescriptorControl.argtypes = [
            wintypes.LPVOID,
            ctypes.POINTER(ctypes.c_ushort),
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
        advapi.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
        advapi.ConvertSidToStringSidW.restype = wintypes.BOOL

        self.ntdll.NtCreateFile.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            ctypes.POINTER(_OBJECT_ATTRIBUTES),
            ctypes.POINTER(_IO_STATUS_BLOCK),
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self.ntdll.NtCreateFile.restype = ctypes.c_long
        self.ntdll.NtSetInformationFile.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_IO_STATUS_BLOCK),
            wintypes.LPVOID,
            wintypes.ULONG,
            ctypes.c_int,
        ]
        self.ntdll.NtSetInformationFile.restype = ctypes.c_long

    def close(self, handle: int | None) -> None:
        if handle and handle != self._INVALID_HANDLE_VALUE:
            self.kernel32.CloseHandle(handle)

    def _create_file(self, path: str, access: int, share: int, flags: int) -> int:
        handle = self.kernel32.CreateFileW(
            path, access, share, None, self._OPEN_EXISTING, flags, None
        )
        if not handle or handle == self._INVALID_HANDLE_VALUE:
            self._raise("CreateFileW")
        return int(handle)

    def open_read(self, path: str) -> int:
        return self._create_file(
            path,
            self._GENERIC_READ | self._READ_CONTROL,
            self._FILE_SHARE_READ,
            self._FILE_FLAG_OPEN_REPARSE_POINT | self._FILE_FLAG_SEQUENTIAL_SCAN,
        )

    def open_directory(self, path: str) -> int:
        return self._create_file(
            path,
            self._FILE_LIST_DIRECTORY | self._READ_CONTROL | self._SYNCHRONIZE,
            self._FILE_SHARE_READ | self._FILE_SHARE_WRITE,
            self._FILE_FLAG_OPEN_REPARSE_POINT | self._FILE_FLAG_BACKUP_SEMANTICS,
        )

    def create_relative_file(self, directory_handle: int, leaf: str) -> int:
        buffer = ctypes.create_unicode_buffer(leaf)
        name = _UNICODE_STRING(
            length=len(leaf.encode("utf-16-le")),
            maximum_length=(len(leaf) + 1) * ctypes.sizeof(wintypes.WCHAR),
            buffer=ctypes.cast(buffer, wintypes.LPWSTR),
        )
        attributes = _OBJECT_ATTRIBUTES(
            length=ctypes.sizeof(_OBJECT_ATTRIBUTES),
            root_directory=directory_handle,
            object_name=ctypes.pointer(name),
            attributes=self._OBJ_CASE_INSENSITIVE,
            security_descriptor=None,
            security_quality_of_service=None,
        )
        status_block = _IO_STATUS_BLOCK()
        handle = wintypes.HANDLE()
        status = self.ntdll.NtCreateFile(
            ctypes.byref(handle),
            self._GENERIC_READ
            | self._GENERIC_WRITE
            | self._DELETE
            | self._READ_CONTROL
            | self._WRITE_DAC
            | self._SYNCHRONIZE,
            ctypes.byref(attributes),
            ctypes.byref(status_block),
            None,
            0,
            self._FILE_SHARE_READ,
            self._FILE_CREATE,
            self._FILE_NON_DIRECTORY_FILE
            | self._FILE_SYNCHRONOUS_IO_NONALERT
            | self._FILE_OPEN_REPARSE_POINT,
            None,
            0,
        )
        if status < 0 or not handle.value:
            raise HandleBoundIoError(f"NtCreateFile:ntstatus=0x{status & 0xFFFFFFFF:08x}")
        return int(handle.value)

    def protect_dacl(self, handle: int) -> None:
        dacl = wintypes.LPVOID()
        descriptor = wintypes.LPVOID()
        status = self.advapi32.GetSecurityInfo(
            handle,
            self._SE_FILE_OBJECT,
            self._DACL_SECURITY_INFORMATION,
            None,
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if status != 0 or not descriptor.value or not dacl.value:
            if descriptor.value:
                self.kernel32.LocalFree(descriptor)
            raise HandleBoundIoError(f"GetSecurityInfo(protect DACL):win32={status}")
        try:
            status = self.advapi32.SetSecurityInfo(
                handle,
                self._SE_FILE_OBJECT,
                self._DACL_SECURITY_INFORMATION | self._PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                dacl,
                None,
            )
            if status != 0:
                raise HandleBoundIoError(f"SetSecurityInfo(protect DACL):win32={status}")
        finally:
            self.kernel32.LocalFree(descriptor)

    def _query(self, handle: int, information_class: int, value: Any) -> None:
        if not self.kernel32.GetFileInformationByHandleEx(
            handle, information_class, ctypes.byref(value), ctypes.sizeof(value)
        ):
            self._raise(f"GetFileInformationByHandleEx({information_class})")

    def _final_path(self, handle: int) -> str:
        length = self.kernel32.GetFinalPathNameByHandleW(handle, None, 0, 0)
        if not length:
            self._raise("GetFinalPathNameByHandleW(size)")
        buffer = ctypes.create_unicode_buffer(length + 1)
        written = self.kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not written or written >= len(buffer):
            self._raise("GetFinalPathNameByHandleW")
        return buffer.value

    def _security(self, handle: int) -> tuple[str, str, bool, bool]:
        owner = wintypes.LPVOID()
        group = wintypes.LPVOID()
        dacl = wintypes.LPVOID()
        descriptor = wintypes.LPVOID()
        status = self.advapi32.GetSecurityInfo(
            handle,
            self._SE_FILE_OBJECT,
            self._OWNER_SECURITY_INFORMATION
            | self._GROUP_SECURITY_INFORMATION
            | self._DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            ctypes.byref(group),
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if status != 0 or not descriptor.value:
            raise HandleBoundIoError(f"GetSecurityInfo:win32={status}")
        try:
            length = int(self.advapi32.GetSecurityDescriptorLength(descriptor))
            if length <= 0:
                raise HandleBoundIoError("security_descriptor_length_invalid")
            raw = ctypes.string_at(descriptor, length)
            control = ctypes.c_ushort()
            revision = wintypes.DWORD()
            if not self.advapi32.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)
            ):
                self._raise("GetSecurityDescriptorControl")
            if not owner.value:
                raise HandleBoundIoError("security_owner_missing")
            sid_text = wintypes.LPWSTR()
            if not self.advapi32.ConvertSidToStringSidW(owner, ctypes.byref(sid_text)):
                self._raise("ConvertSidToStringSidW")
            try:
                owner_sid = sid_text.value
            finally:
                self.kernel32.LocalFree(sid_text)
            return (
                owner_sid,
                hashlib.sha256(raw).hexdigest(),
                bool(control.value & self._SE_DACL_PRESENT and dacl.value),
                bool(control.value & self._SE_DACL_PROTECTED),
            )
        finally:
            self.kernel32.LocalFree(descriptor)

    def identity(self, handle: int) -> HandleIdentity:
        file_id = _FILE_ID_INFO()
        standard = _FILE_STANDARD_INFO()
        tag = _FILE_ATTRIBUTE_TAG_INFO()
        self._query(handle, self._FILE_ID_INFO_CLASS, file_id)
        self._query(handle, self._FILE_STANDARD_INFO_CLASS, standard)
        self._query(handle, self._FILE_ATTRIBUTE_TAG_INFO_CLASS, tag)
        owner, descriptor_sha, dacl_present, dacl_protected = self._security(handle)
        return HandleIdentity(
            final_path=self._final_path(handle),
            volume_serial_number=int(file_id.volume_serial_number),
            file_id_hex=bytes(file_id.file_id.identifier).hex(),
            size=int(standard.end_of_file),
            link_count=int(standard.number_of_links),
            attributes=int(tag.file_attributes),
            reparse_tag=int(tag.reparse_tag),
            file_type=int(self.kernel32.GetFileType(handle)),
            owner_sid=owner,
            security_descriptor_sha256=descriptor_sha,
            dacl_present=dacl_present,
            dacl_protected=dacl_protected,
        )

    def _rewind(self, handle: int) -> None:
        if not self.kernel32.SetFilePointerEx(handle, 0, None, self._FILE_BEGIN):
            self._raise("SetFilePointerEx")

    def read_all(self, handle: int, expected_size: int) -> bytes:
        if expected_size < 0:
            raise HandleBoundIoError("negative_expected_size")
        self._rewind(handle)
        output = bytearray()
        while len(output) < expected_size:
            wanted = min(1024 * 1024, expected_size - len(output))
            buffer = ctypes.create_string_buffer(wanted)
            count = wintypes.DWORD()
            if not self.kernel32.ReadFile(handle, buffer, wanted, ctypes.byref(count), None):
                self._raise("ReadFile")
            if count.value == 0:
                break
            output.extend(buffer.raw[: count.value])
        return bytes(output)

    def write_all(self, handle: int, raw: bytes) -> None:
        offset = 0
        while offset < len(raw):
            chunk = raw[offset : offset + 1024 * 1024]
            buffer = ctypes.create_string_buffer(chunk)
            count = wintypes.DWORD()
            if not self.kernel32.WriteFile(handle, buffer, len(chunk), ctypes.byref(count), None):
                self._raise("WriteFile")
            if count.value <= 0:
                raise HandleBoundIoError("WriteFile_zero_progress")
            offset += int(count.value)

    def flush(self, handle: int) -> None:
        if not self.kernel32.FlushFileBuffers(handle):
            self._raise("FlushFileBuffers")

    def rename_no_replace(self, handle: int, directory_handle: int, leaf: str) -> None:
        encoded = leaf.encode("utf-16-le")
        header_size = _FILE_RENAME_INFO.file_name.offset
        buffer = ctypes.create_string_buffer(header_size + len(encoded))
        info = ctypes.cast(buffer, ctypes.POINTER(_FILE_RENAME_INFO)).contents
        info.replace_if_exists = False
        info.root_directory = directory_handle
        info.file_name_length = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + header_size, encoded, len(encoded))
        status_block = _IO_STATUS_BLOCK()
        status = self.ntdll.NtSetInformationFile(
            handle,
            ctypes.byref(status_block),
            buffer,
            len(buffer),
            self._FILE_RENAME_INFORMATION_CLASS,
        )
        if status < 0:
            raise HandleBoundIoError(
                "NtSetInformationFile(FileRenameInformation,no-replace):"
                f"ntstatus=0x{status & 0xFFFFFFFF:08x}"
            )


def source_contract() -> dict[str, Any]:
    """Machine-readable claims; deliberately excludes same-token immutability."""

    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s3.handle-io.v1",
        "authority": "open_kernel_handle",
        "read_same_handle": True,
        "file_id_and_volume_bound": True,
        "security_descriptor_bound": True,
        "reparse_tag_rejected": True,
        "share_delete_allowed": False,
        "relative_create": "NtCreateFile.RootDirectory",
        "relative_rename": "NtSetInformationFile.RootDirectory",
        "replace_if_exists": False,
        "flush_before_hash": True,
        "created_file_dacl_protected_before_content": True,
        "production_evidence_writer_integrated": False,
        "directory_identity_bound": True,
        "directory_metadata_flushed_after_rename": False,
        "same_token_hostile_admin_protected": False,
        "go_evidence_eligible": False,
    }


__all__ = [
    "BoundPublication",
    "BoundRead",
    "HandleBoundIoError",
    "HandleIdentity",
    "WindowsHandleApi",
    "publish_bound_no_replace",
    "read_bound_file",
    "source_contract",
]
