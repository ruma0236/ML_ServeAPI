"""Fail-closed Windows handle I/O for the pre-r8 r7s4 review gate.

This version extends the r7s3 primitive with an explicit directory-handle
flush after every successful no-replace rename.  A successful flush is a
required local observation, not proof that a filesystem, controller, or disk
will survive power loss.  The module also makes no same-token administrator
tamper-resistance claim.
"""

from __future__ import annotations

import hashlib
import ntpath
import os
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from evm.scale_validation import phase_b2_r7s3_handle_io as r7s3


HandleBoundIoError = r7s3.HandleBoundIoError
HandleIdentity = r7s3.HandleIdentity
_SAFE_WINDOWS_LEAF_RE = re.compile(r"[A-Za-z0-9._-]{1,180}\Z")
_WINDOWS_RESERVED_BASENAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


def validate_strict_windows_leaf(value: str) -> str:
    """Reject ADS, traversal, device names, controls, and ambiguous leaf syntax."""

    if (
        not isinstance(value, str)
        or value in {"", ".", ".."}
        or ntpath.basename(value) != value
        or _SAFE_WINDOWS_LEAF_RE.fullmatch(value) is None
        or value.endswith((" ", "."))
    ):
        raise HandleBoundIoError("strict_windows_leaf_invalid")
    device_basename = value.split(".", 1)[0].upper()
    if device_basename in _WINDOWS_RESERVED_BASENAMES:
        raise HandleBoundIoError("strict_windows_leaf_reserved_device")
    return value


class DurableHandleApi(r7s3.HandleApi, Protocol):
    """The r7s3 API plus a distinct directory metadata flush operation."""

    def flush_directory(self, handle: int) -> None: ...


@dataclass(frozen=True)
class DurableBoundPublication:
    """Same-handle publication evidence including the directory flush."""

    final_path: str
    temporary_leaf: str
    sha256: str
    bytes: int
    identity: HandleIdentity
    directory_identity: HandleIdentity
    file_flush_count: int
    directory_flush_count: int
    directory_flush_succeeded: bool
    replace_if_exists: bool = False
    same_handle_readback: bool = True
    file_identity_stable_across_rename: bool = True
    power_loss_durability_proven: bool = False
    same_token_hostile_admin_protected: bool = False
    go_evidence_eligible: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["identity"] = self.identity.to_dict()
        value["directory_identity"] = self.directory_identity.to_dict()
        return value


@dataclass(frozen=True)
class PublicationFailureObservation:
    """Immutable best-effort observation made through the still-open file handle."""

    stage: str
    temporary_leaf: str
    intended_final_path: str
    rename_completed: bool
    observation_status: str
    current_identity: HandleIdentity | None
    current_sha256: str | None
    current_bytes: int | None
    expected_sha256: str
    expected_bytes: int
    observation_error_type: str | None
    manual_intervention_required: bool = True
    retry_allowed: bool = False
    cleanup_attempted: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["current_identity"] = (
            self.current_identity.to_dict() if self.current_identity is not None else None
        )
        return value


class DurablePublicationError(HandleBoundIoError):
    """Publication failed; observation is never treated as successful evidence."""

    def __init__(self, message: str, observation: PublicationFailureObservation) -> None:
        super().__init__(message)
        self.observation = observation
        self.manual_intervention_required = True
        self.retry_allowed = False


def _directory_identity_changed(before: HandleIdentity, after: HandleIdentity) -> list[str]:
    """Return security or object-identity changes, excluding volatile fields."""

    before_value = before.to_dict()
    after_value = after.to_dict()
    return sorted(
        name
        for name in before_value
        if name not in {"attributes", "size"} and before_value[name] != after_value[name]
    )


def _failure_observation(
    implementation: DurableHandleApi,
    file_handle: int | None,
    *,
    stage: str,
    temporary_leaf: str,
    intended_final_path: str,
    rename_completed: bool,
    expected_raw: bytes,
) -> PublicationFailureObservation:
    identity: HandleIdentity | None = None
    current_sha256: str | None = None
    current_bytes: int | None = None
    observation_error_type: str | None = None
    status = "unknown_no_open_file_handle"
    if file_handle is not None:
        try:
            identity = implementation.identity(file_handle)
            observed_raw = implementation.read_all(file_handle, identity.size)
            if len(observed_raw) != identity.size:
                raise HandleBoundIoError("failure_observation_size_mismatch")
            current_sha256 = hashlib.sha256(observed_raw).hexdigest()
            current_bytes = len(observed_raw)
            status = "same_handle_observed"
        except Exception as exc:
            identity = None
            current_sha256 = None
            current_bytes = None
            observation_error_type = f"{type(exc).__module__}.{type(exc).__qualname__}"
            status = "unknown_observation_failed"
    return PublicationFailureObservation(
        stage=stage,
        temporary_leaf=temporary_leaf,
        intended_final_path=intended_final_path,
        rename_completed=rename_completed,
        observation_status=status,
        current_identity=identity,
        current_sha256=current_sha256,
        current_bytes=current_bytes,
        expected_sha256=hashlib.sha256(expected_raw).hexdigest(),
        expected_bytes=len(expected_raw),
        observation_error_type=observation_error_type,
    )


def publish_bound_no_replace_durable(
    directory: str | os.PathLike[str],
    final_leaf: str,
    raw: bytes,
    *,
    run_uuid: str | uuid.UUID,
    require_protected_dacl: bool = True,
    api: DurableHandleApi | None = None,
) -> DurableBoundPublication:
    """Create, flush, rename, directory-flush, and read back through handles.

    There is one create attempt and one rename attempt.  On any error the
    partial or final file is intentionally preserved and no cleanup, retry, or
    process-control action is attempted.
    """

    if not isinstance(raw, bytes):
        raise TypeError("publication_payload_bytes_required")
    directory_path = r7s3._normal_path(directory)
    leaf = validate_strict_windows_leaf(final_leaf)
    try:
        run_value = str(uuid.UUID(str(run_uuid)))
    except ValueError as exc:
        raise HandleBoundIoError("run_uuid_invalid") from exc
    temporary_leaf = validate_strict_windows_leaf(f".{leaf}.{run_value}.partial")
    implementation: DurableHandleApi = api or WindowsHandleApi()
    directory_handle: int | None = None
    file_handle: int | None = None
    stage = "open_directory"
    rename_completed = False
    intended_final_path = ntpath.join(directory_path, leaf)
    try:
        directory_handle = implementation.open_directory(directory_path)
        stage = "bind_directory_identity"
        directory_before = implementation.identity(directory_handle)
        r7s3._reject_unsafe_directory_identity(
            directory_before,
            expected_path=directory_path,
        )
        stage = "create_relative_temporary"
        file_handle = implementation.create_relative_file(directory_handle, temporary_leaf)
        if require_protected_dacl:
            stage = "protect_temporary_dacl"
            implementation.protect_dacl(file_handle)
        stage = "write_temporary"
        implementation.write_all(file_handle, raw)
        stage = "flush_temporary"
        implementation.flush(file_handle)

        stage = "verify_temporary_identity"
        temporary_identity = implementation.identity(file_handle)
        expected_temporary = ntpath.join(directory_path, temporary_leaf)
        r7s3._reject_unsafe_identity(
            temporary_identity,
            expected_path=expected_temporary,
            require_protected_dacl=require_protected_dacl,
        )
        if temporary_identity.size != len(raw):
            raise HandleBoundIoError("temporary_size_mismatch")
        stage = "readback_temporary"
        if implementation.read_all(file_handle, temporary_identity.size) != raw:
            raise HandleBoundIoError("temporary_readback_mismatch")

        stage = "rename_no_replace"
        implementation.rename_no_replace(file_handle, directory_handle, leaf)
        rename_completed = True
        stage = "flush_renamed_file"
        implementation.flush(file_handle)
        stage = "flush_directory"
        implementation.flush_directory(directory_handle)

        stage = "verify_final_identity"
        final_identity = implementation.identity(file_handle)
        r7s3._reject_unsafe_identity(
            final_identity,
            expected_path=intended_final_path,
            require_protected_dacl=require_protected_dacl,
        )
        if (
            temporary_identity.volume_serial_number != final_identity.volume_serial_number
            or temporary_identity.file_id_hex != final_identity.file_id_hex
            or temporary_identity.security_descriptor_sha256
            != final_identity.security_descriptor_sha256
            or final_identity.size != len(raw)
        ):
            raise HandleBoundIoError("published_file_identity_changed")
        stage = "readback_final"
        if implementation.read_all(file_handle, final_identity.size) != raw:
            raise HandleBoundIoError("published_readback_mismatch")

        stage = "verify_directory_identity"
        directory_after = implementation.identity(directory_handle)
        r7s3._reject_unsafe_directory_identity(
            directory_after,
            expected_path=directory_path,
        )
        changed = _directory_identity_changed(directory_before, directory_after)
        if changed:
            raise HandleBoundIoError(f"directory_handle_identity_changed:{','.join(changed)}")

        return DurableBoundPublication(
            final_path=final_identity.final_path,
            temporary_leaf=temporary_leaf,
            sha256=hashlib.sha256(raw).hexdigest(),
            bytes=len(raw),
            identity=final_identity,
            directory_identity=directory_after,
            file_flush_count=2,
            directory_flush_count=1,
            directory_flush_succeeded=True,
        )
    except Exception as exc:
        if isinstance(exc, DurablePublicationError):
            raise
        observation = _failure_observation(
            implementation,
            file_handle,
            stage=stage,
            temporary_leaf=temporary_leaf,
            intended_final_path=intended_final_path,
            rename_completed=rename_completed,
            expected_raw=raw,
        )
        raise DurablePublicationError(
            f"durable_publication_failed:{stage}",
            observation,
        ) from exc
    finally:
        implementation.close(file_handle)
        implementation.close(directory_handle)


class WindowsHandleApi(r7s3.WindowsHandleApi):
    """Windows API with write access on the directory used for its flush."""

    def open_directory(self, path: str) -> int:
        return self._create_file(
            path,
            self._GENERIC_WRITE
            | self._FILE_LIST_DIRECTORY
            | self._READ_CONTROL
            | self._SYNCHRONIZE,
            self._FILE_SHARE_READ | self._FILE_SHARE_WRITE,
            self._FILE_FLAG_OPEN_REPARSE_POINT | self._FILE_FLAG_BACKUP_SEMANTICS,
        )

    def flush_directory(self, handle: int) -> None:
        self.flush(handle)


def source_contract() -> dict[str, Any]:
    """Machine-readable boundary; success remains review-only and non-GO."""

    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s4.handle-io.v1",
        "parent_contract": r7s3.source_contract()["schema"],
        "authority": "open_kernel_handle",
        "relative_create": "NtCreateFile.RootDirectory",
        "relative_rename": "NtSetInformationFile.RootDirectory",
        "replace_if_exists": False,
        "strict_windows_leaf_validation": True,
        "ads_and_reserved_device_names_rejected": True,
        "same_handle_readback": True,
        "file_flush_before_rename": True,
        "file_flush_after_rename": True,
        "directory_handle_flush_after_rename": True,
        "directory_flush_success_required": True,
        "post_rename_failure_same_handle_observation": True,
        "unknown_failure_observation_requires_manual_intervention": True,
        "retry_or_cleanup_after_failure": False,
        "output_directory_parent_metadata_flushed": False,
        "power_loss_durability_proven": False,
        "same_token_hostile_admin_protected": False,
        "legacy_evidence_writers_modified": False,
        "ubuntu_ci_executes_windows_handle_tests": False,
        "separate_local_windows_evidence_required": True,
        "production_go_enabled": False,
        "go_evidence_eligible": False,
    }


__all__ = [
    "DurableBoundPublication",
    "DurableHandleApi",
    "DurablePublicationError",
    "HandleBoundIoError",
    "HandleIdentity",
    "PublicationFailureObservation",
    "WindowsHandleApi",
    "publish_bound_no_replace_durable",
    "source_contract",
    "validate_strict_windows_leaf",
]
