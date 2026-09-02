"""Fail-closed r7s5 local admission reservation.

The production entry deliberately has no caller-selected root or backend.  The
required independently provisioned backend and pinned root identity do not yet
exist, so production always stops before I/O.  A private test seam exercises the
same validation and lease lifetime without claiming multi-host or hostile-admin
protection.
"""

from __future__ import annotations

import hashlib
import ntpath
import re
import threading
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from evm.scale_validation.phase_b2_r7s4_authority import (
    VerifiedExternalReceipt,
    canonical_json_bytes,
    require_verified_external_receipt,
)


RESERVATION_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.admission-reservation.v1"
CANONICAL_RESERVATION_ROOT = (
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation"
    r"\private\s8-v4\x1-clock-phase-b2\pre-r8-r7s5-admission-reservations"
)
PRODUCTION_RESERVATION_BACKEND_CONFIGURED = False
PRODUCTION_ROOT_IDENTITY_PIN_CONFIGURED = False
PRODUCTION_ENTRY_ENABLED = False
MULTI_HOST_GLOBAL_ONE_SHOT_PROVIDED = False
SAME_TOKEN_HOSTILE_ADMIN_PROTECTED = False
POWER_LOSS_DURABILITY_PROVEN = False
HEX32_RE = re.compile(r"[0-9a-f]{32}")
HEX64_RE = re.compile(r"[0-9a-f]{64}")
SID_RE = re.compile(r"S-\d+(?:-\d+)+")
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,191}")
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_TYPE_DISK = 1


class R7S5ReservationError(RuntimeError):
    """A reservation is absent, ambiguous, replayed, or no longer bound."""

    manual_intervention_required = True
    automatic_retry_allowed = False
    downstream_calls_allowed = False

    def __init__(self, code: str, *, stage: str) -> None:
        super().__init__(code)
        self.code = code
        self.stage = stage


class ReservationCollisionError(R7S5ReservationError):
    """The immutable execution identity was already reserved."""


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    global_run_id: str
    domain_run_id: str
    domain: str
    run_uuid: str
    attempt_uuid: str
    execution_mode: str


@dataclass(frozen=True, slots=True)
class ReservationRootExpectation:
    """Provisioned identity of the one fixed reservation directory."""

    final_path: str
    volume_serial_number: int
    file_id_hex: str
    owner_sid: str
    security_descriptor_sha256: str
    dacl_present: bool = True
    dacl_protected: bool = True


@dataclass(frozen=True, slots=True)
class HandleIdentitySnapshot:
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


@dataclass(frozen=True, slots=True)
class ReservationBackendAcquisition:
    """Typed result returned while both the root and reservation handles are open."""

    handle: int
    final_path: str
    temporary_leaf: str
    raw: bytes
    sha256: str
    bytes: int
    identity: HandleIdentitySnapshot
    root_identity: HandleIdentitySnapshot
    file_flush_count: int
    directory_flush_count: int
    directory_flush_succeeded: bool
    create_no_replace: bool
    replace_if_exists: bool
    same_handle_readback: bool
    file_identity_stable_across_rename: bool
    handle_retained: bool
    power_loss_durability_proven: bool = False
    same_token_hostile_admin_protected: bool = False
    multi_host_global_one_shot_provided: bool = False
    production_go: bool = False


@dataclass(frozen=True, slots=True)
class ReservationBackendReadback:
    handle: int
    raw: bytes
    sha256: str
    identity: HandleIdentitySnapshot
    root_identity: HandleIdentitySnapshot


class ReservationBackend(Protocol):
    """Future fixed-root backend; only fake implementations are injected in tests."""

    def acquire_no_replace(
        self,
        *,
        root_path: str,
        final_leaf: str,
        raw: bytes,
        run_uuid: str,
    ) -> ReservationBackendAcquisition: ...

    def read_same_handle(
        self, handle: int, *, expected_size: int
    ) -> ReservationBackendReadback: ...

    def close(self, handle: int) -> None: ...


@dataclass(frozen=True, slots=True)
class ReservationEvidence:
    execution_identity_sha256: str
    marker_leaf: str
    final_path: str
    raw_sha256: str
    raw_bytes: int
    file_identity: HandleIdentitySnapshot
    root_identity: HandleIdentitySnapshot
    handle_retained_until_admission_terminal: bool
    local_at_most_once_only: bool = True
    multi_host_global_one_shot_provided: bool = False
    same_token_hostile_admin_protected: bool = False
    production_go: bool = False


def _normal_path(value: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise R7S5ReservationError("windows_path_invalid", stage="preflight")
    return ntpath.normcase(ntpath.normpath(value))


def _exact_positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise R7S5ReservationError(f"{label}_positive_int_required", stage="readback")
    return value


def _canonical_uuid(value: object, label: str) -> str:
    if type(value) is not str:
        raise R7S5ReservationError(f"{label}_uuid_invalid", stage="preflight")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise R7S5ReservationError(f"{label}_uuid_invalid", stage="preflight") from exc
    if str(parsed) != value or parsed.version != 4:
        raise R7S5ReservationError(f"{label}_uuid_invalid", stage="preflight")
    return value


def _validated_execution_identity(value: object) -> ExecutionIdentity:
    if type(value) is not ExecutionIdentity:
        raise R7S5ReservationError("typed_execution_identity_required", stage="preflight")
    assert isinstance(value, ExecutionIdentity)
    for name in ("global_run_id", "domain_run_id", "execution_mode"):
        item = getattr(value, name)
        if type(item) is not str or SAFE_ID_RE.fullmatch(item) is None:
            raise R7S5ReservationError(f"execution_identity_invalid:{name}", stage="preflight")
    if type(value.domain) is not str or value.domain not in {"windows", "wsl"}:
        raise R7S5ReservationError("execution_identity_invalid:domain", stage="preflight")
    _canonical_uuid(value.run_uuid, "run")
    _canonical_uuid(value.attempt_uuid, "attempt")
    return value


def execution_identity_sha256(value: ExecutionIdentity) -> str:
    identity = _validated_execution_identity(value)
    return hashlib.sha256(canonical_json_bytes(asdict(identity))).hexdigest()


def _validate_root_expectation(value: object) -> ReservationRootExpectation:
    if type(value) is not ReservationRootExpectation:
        raise R7S5ReservationError("typed_root_expectation_required", stage="preflight")
    assert isinstance(value, ReservationRootExpectation)
    if _normal_path(value.final_path) != _normal_path(CANONICAL_RESERVATION_ROOT):
        raise R7S5ReservationError("alternate_reservation_root_rejected", stage="preflight")
    _exact_positive_int(value.volume_serial_number, "root_volume_serial_number")
    if HEX32_RE.fullmatch(value.file_id_hex) is None:
        raise R7S5ReservationError("root_file_id_invalid", stage="preflight")
    if SID_RE.fullmatch(value.owner_sid) is None:
        raise R7S5ReservationError("root_owner_sid_invalid", stage="preflight")
    if HEX64_RE.fullmatch(value.security_descriptor_sha256) is None:
        raise R7S5ReservationError("root_security_descriptor_sha256_invalid", stage="preflight")
    if value.dacl_present is not True or value.dacl_protected is not True:
        raise R7S5ReservationError("root_dacl_contract_invalid", stage="preflight")
    return value


def _validate_identity(
    value: object,
    *,
    expected_path: str,
    expected_size: int,
    is_directory: bool,
    stage: str,
) -> HandleIdentitySnapshot:
    if type(value) is not HandleIdentitySnapshot:
        raise R7S5ReservationError("typed_handle_identity_required", stage=stage)
    assert isinstance(value, HandleIdentitySnapshot)
    if _normal_path(value.final_path) != _normal_path(expected_path):
        raise R7S5ReservationError("handle_identity_path_mismatch", stage=stage)
    _exact_positive_int(value.volume_serial_number, "handle_volume_serial_number")
    if HEX32_RE.fullmatch(value.file_id_hex) is None:
        raise R7S5ReservationError("handle_file_id_invalid", stage=stage)
    if type(value.size) is not int or value.size != expected_size:
        raise R7S5ReservationError("handle_size_mismatch", stage=stage)
    if type(value.link_count) is not int or value.link_count != 1:
        raise R7S5ReservationError("handle_link_count_invalid", stage=stage)
    if type(value.attributes) is not int:
        raise R7S5ReservationError("handle_attributes_invalid", stage=stage)
    directory_bit = bool(value.attributes & FILE_ATTRIBUTE_DIRECTORY)
    if directory_bit is not is_directory:
        raise R7S5ReservationError("handle_directory_attribute_mismatch", stage=stage)
    if type(value.reparse_tag) is not int or value.reparse_tag != 0:
        raise R7S5ReservationError("handle_reparse_tag_present", stage=stage)
    if type(value.file_type) is not int or value.file_type != FILE_TYPE_DISK:
        raise R7S5ReservationError("handle_not_disk_file", stage=stage)
    if SID_RE.fullmatch(value.owner_sid) is None:
        raise R7S5ReservationError("handle_owner_sid_invalid", stage=stage)
    if HEX64_RE.fullmatch(value.security_descriptor_sha256) is None:
        raise R7S5ReservationError("handle_security_descriptor_sha256_invalid", stage=stage)
    if value.dacl_present is not True or value.dacl_protected is not True:
        raise R7S5ReservationError("handle_dacl_contract_invalid", stage=stage)
    return value


def _validate_root_identity(
    identity: object,
    expected: ReservationRootExpectation,
    *,
    stage: str,
) -> HandleIdentitySnapshot:
    result = _validate_identity(
        identity,
        expected_path=expected.final_path,
        expected_size=0,
        is_directory=True,
        stage=stage,
    )
    exact = {
        "volume_serial_number": expected.volume_serial_number,
        "file_id_hex": expected.file_id_hex,
        "owner_sid": expected.owner_sid,
        "security_descriptor_sha256": expected.security_descriptor_sha256,
        "dacl_present": expected.dacl_present,
        "dacl_protected": expected.dacl_protected,
    }
    for name, wanted in exact.items():
        if getattr(result, name) != wanted:
            raise R7S5ReservationError(f"root_identity_mismatch:{name}", stage=stage)
    return result


def _bind_capability(capability: object, identity: ExecutionIdentity) -> VerifiedExternalReceipt:
    verified = require_verified_external_receipt(capability)
    for name, wanted in asdict(identity).items():
        if getattr(verified, name) != wanted:
            raise R7S5ReservationError(
                f"receipt_execution_identity_mismatch:{name}", stage="preflight"
            )
    return verified


def _record_raw(
    identity: ExecutionIdentity,
    capability: VerifiedExternalReceipt,
    identity_sha: str,
) -> bytes:
    return canonical_json_bytes(
        {
            "schema": RESERVATION_SCHEMA,
            "status": "reserved_for_local_admission",
            "execution_identity": asdict(identity),
            "execution_identity_sha256": identity_sha,
            "approval_instance": {
                "approval_id": capability.approval_id,
                "approval_request_id": capability.approval_request_id,
                "approval_request_sha256": capability.approval_request_sha256,
                "receipt_sha256": capability.receipt_sha256,
                "subject_sha256": capability.subject_sha256,
                "verifier_identity": capability.verifier_identity,
            },
            "claims": {
                "local_at_most_once_only": True,
                "multi_host_global_one_shot_provided": False,
                "power_loss_durability_proven": False,
                "production_go": False,
                "same_token_hostile_admin_protected": False,
            },
        }
    )


def _validate_acquisition(
    value: object,
    *,
    expected_raw: bytes,
    expected_leaf: str,
    identity: ExecutionIdentity,
    root: ReservationRootExpectation,
) -> ReservationBackendAcquisition:
    if type(value) is not ReservationBackendAcquisition:
        raise R7S5ReservationError("typed_reservation_acquisition_required", stage="publication")
    assert isinstance(value, ReservationBackendAcquisition)
    expected_path = ntpath.join(CANONICAL_RESERVATION_ROOT, expected_leaf)
    expected_temp = f".{expected_leaf}.{identity.run_uuid}.partial"
    if type(value.handle) is not int or value.handle <= 0:
        raise R7S5ReservationError("reservation_handle_invalid", stage="publication")
    if _normal_path(value.final_path) != _normal_path(expected_path):
        raise R7S5ReservationError("reservation_final_path_mismatch", stage="publication")
    if value.temporary_leaf != expected_temp:
        raise R7S5ReservationError("reservation_temporary_leaf_mismatch", stage="publication")
    expected_sha = hashlib.sha256(expected_raw).hexdigest()
    if (
        type(value.raw) is not bytes
        or value.raw != expected_raw
        or value.sha256 != expected_sha
        or type(value.bytes) is not int
        or value.bytes != len(expected_raw)
    ):
        raise R7S5ReservationError("reservation_raw_sha_bytes_mismatch", stage="publication")
    if (
        type(value.file_flush_count) is not int
        or value.file_flush_count != 2
        or type(value.directory_flush_count) is not int
        or value.directory_flush_count != 1
        or value.directory_flush_succeeded is not True
        or value.create_no_replace is not True
        or value.replace_if_exists is not False
        or value.same_handle_readback is not True
        or value.file_identity_stable_across_rename is not True
        or value.handle_retained is not True
        or value.power_loss_durability_proven is not False
        or value.same_token_hostile_admin_protected is not False
        or value.multi_host_global_one_shot_provided is not False
        or value.production_go is not False
    ):
        raise R7S5ReservationError("reservation_publication_contract_mismatch", stage="publication")
    root_identity = _validate_root_identity(value.root_identity, root, stage="publication")
    file_identity = _validate_identity(
        value.identity,
        expected_path=expected_path,
        expected_size=len(expected_raw),
        is_directory=False,
        stage="publication",
    )
    if file_identity.volume_serial_number != root_identity.volume_serial_number:
        raise R7S5ReservationError("reservation_cross_volume_identity", stage="publication")
    if file_identity.file_id_hex == root_identity.file_id_hex:
        raise R7S5ReservationError("reservation_file_root_identity_collision", stage="publication")
    return value


def _validate_readback(
    value: object,
    *,
    acquisition: ReservationBackendAcquisition,
    root: ReservationRootExpectation,
) -> ReservationBackendReadback:
    if type(value) is not ReservationBackendReadback:
        raise R7S5ReservationError("typed_reservation_readback_required", stage="readback")
    assert isinstance(value, ReservationBackendReadback)
    if type(value.handle) is not int or value.handle != acquisition.handle:
        raise R7S5ReservationError("reservation_readback_handle_mismatch", stage="readback")
    if (
        type(value.raw) is not bytes
        or value.raw != acquisition.raw
        or value.sha256 != acquisition.sha256
        or hashlib.sha256(value.raw).hexdigest() != value.sha256
    ):
        raise R7S5ReservationError("reservation_readback_raw_sha_mismatch", stage="readback")
    file_identity = _validate_identity(
        value.identity,
        expected_path=acquisition.final_path,
        expected_size=acquisition.bytes,
        is_directory=False,
        stage="readback",
    )
    root_identity = _validate_root_identity(value.root_identity, root, stage="readback")
    if file_identity != acquisition.identity or root_identity != acquisition.root_identity:
        raise R7S5ReservationError("reservation_identity_changed_while_open", stage="readback")
    if file_identity.volume_serial_number != root_identity.volume_serial_number:
        raise R7S5ReservationError("reservation_readback_cross_volume", stage="readback")
    return value


class ReservationLease:
    """An open reservation handle retained until admission reaches a terminal state."""

    def __init__(
        self,
        *,
        backend: ReservationBackend,
        acquisition: ReservationBackendAcquisition,
        root_expectation: ReservationRootExpectation,
        evidence: ReservationEvidence,
    ) -> None:
        self._backend = backend
        self._acquisition = acquisition
        self._root_expectation = root_expectation
        self._lock = threading.Lock()
        self._active = True
        self._close_count = 0
        self._readback_count = 1
        self.evidence = evidence

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def close_count(self) -> int:
        with self._lock:
            return self._close_count

    @property
    def readback_count(self) -> int:
        with self._lock:
            return self._readback_count

    def assert_active(self) -> ReservationBackendReadback:
        with self._lock:
            if not self._active:
                raise R7S5ReservationError("reservation_lease_not_active", stage="readback")
            result = self._backend.read_same_handle(
                self._acquisition.handle, expected_size=self._acquisition.bytes
            )
            validated = _validate_readback(
                result,
                acquisition=self._acquisition,
                root=self._root_expectation,
            )
            self._readback_count += 1
            return validated

    def close(self) -> None:
        with self._lock:
            if not self._active:
                raise R7S5ReservationError("reservation_lease_close_reentry", stage="close")
            try:
                self._backend.close(self._acquisition.handle)
            except Exception as exc:
                self._active = False
                self._close_count += 1
                raise R7S5ReservationError(
                    "reservation_handle_close_ambiguous", stage="close"
                ) from exc
            self._active = False
            self._close_count += 1


def _acquire_reservation_for_test(
    capability: object,
    *,
    execution_identity: ExecutionIdentity,
    root_expectation: ReservationRootExpectation,
    backend: ReservationBackend,
) -> ReservationLease:
    """Private dependency-injection seam; never a production entry."""

    identity = _validated_execution_identity(execution_identity)
    root = _validate_root_expectation(root_expectation)
    verified = _bind_capability(capability, identity)
    identity_sha = execution_identity_sha256(identity)
    raw = _record_raw(identity, verified, identity_sha)
    leaf = f"r7s5-execution-{identity_sha}.reservation.json"
    acquisition: ReservationBackendAcquisition | None = None
    try:
        acquisition = backend.acquire_no_replace(
            root_path=CANONICAL_RESERVATION_ROOT,
            final_leaf=leaf,
            raw=raw,
            run_uuid=identity.run_uuid,
        )
    except FileExistsError as exc:
        raise ReservationCollisionError(
            "reservation_execution_identity_collision", stage="publication"
        ) from exc
    except Exception as exc:
        raise R7S5ReservationError(
            "reservation_publication_ambiguous", stage="publication"
        ) from exc
    try:
        validated = _validate_acquisition(
            acquisition,
            expected_raw=raw,
            expected_leaf=leaf,
            identity=identity,
            root=root,
        )
        readback = backend.read_same_handle(validated.handle, expected_size=validated.bytes)
        _validate_readback(readback, acquisition=validated, root=root)
    except Exception:
        if type(acquisition) is ReservationBackendAcquisition and type(acquisition.handle) is int:
            try:
                backend.close(acquisition.handle)
            except Exception:
                pass
        raise
    evidence = ReservationEvidence(
        execution_identity_sha256=identity_sha,
        marker_leaf=leaf,
        final_path=validated.final_path,
        raw_sha256=validated.sha256,
        raw_bytes=validated.bytes,
        file_identity=validated.identity,
        root_identity=validated.root_identity,
        handle_retained_until_admission_terminal=True,
    )
    return ReservationLease(
        backend=backend,
        acquisition=validated,
        root_expectation=root,
        evidence=evidence,
    )


def acquire_production_reservation(
    capability: object,
    *,
    execution_identity: ExecutionIdentity,
) -> ReservationLease:
    """Fixed-root production API; intentionally closed until provisioning exists."""

    del capability, execution_identity
    if not PRODUCTION_RESERVATION_BACKEND_CONFIGURED:
        raise R7S5ReservationError("production_reservation_backend_unconfigured", stage="gate")
    if not PRODUCTION_ROOT_IDENTITY_PIN_CONFIGURED:
        raise R7S5ReservationError("production_root_identity_pin_unconfigured", stage="gate")
    raise R7S5ReservationError("production_reservation_wiring_not_implemented", stage="gate")


def reservation_contract() -> dict[str, Any]:
    return {
        "schema": "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.reservation-contract.v1",
        "canonical_root": CANONICAL_RESERVATION_ROOT,
        "caller_selectable_production_root": False,
        "caller_injectable_production_backend": False,
        "production_reservation_backend_configured": PRODUCTION_RESERVATION_BACKEND_CONFIGURED,
        "production_root_identity_pin_configured": PRODUCTION_ROOT_IDENTITY_PIN_CONFIGURED,
        "production_entry_enabled": PRODUCTION_ENTRY_ENABLED,
        "handle_retained_until_admission_terminal": True,
        "root_identity_and_security_descriptor_readback_required": True,
        "execution_collision_key_scope": [
            "global_run_id",
            "domain_run_id",
            "domain",
            "run_uuid",
            "attempt_uuid",
            "execution_mode",
        ],
        "execution_collision_key_excludes_request_subject_receipt_approval": True,
        "create_no_replace_required": True,
        "automatic_retry_allowed": False,
        "power_loss_durability_proven": POWER_LOSS_DURABILITY_PROVEN,
        "same_token_hostile_admin_protected": SAME_TOKEN_HOSTILE_ADMIN_PROTECTED,
        "multi_host_global_one_shot_provided": MULTI_HOST_GLOBAL_ONE_SHOT_PROVIDED,
        "local_at_most_once_only": True,
        "production_go": False,
    }


__all__ = [
    "CANONICAL_RESERVATION_ROOT",
    "ExecutionIdentity",
    "HandleIdentitySnapshot",
    "PRODUCTION_ENTRY_ENABLED",
    "PRODUCTION_RESERVATION_BACKEND_CONFIGURED",
    "PRODUCTION_ROOT_IDENTITY_PIN_CONFIGURED",
    "R7S5ReservationError",
    "RESERVATION_SCHEMA",
    "ReservationBackendAcquisition",
    "ReservationBackendReadback",
    "ReservationCollisionError",
    "ReservationEvidence",
    "ReservationLease",
    "ReservationRootExpectation",
    "acquire_production_reservation",
    "execution_identity_sha256",
    "reservation_contract",
]
