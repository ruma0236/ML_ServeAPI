"""Fail-closed, offline reviewer-candidate admission model for pre-r8 r7s7.

The public entry point is deliberately unwired.  It cannot accept adapters,
paths, commands, clocks, or authority keys and stops before reading candidate
material or creating a process.  The private ``_for_test`` entry models the
required ordering with typed test doubles.  Its terminal result is explicitly
``internal_non_authoritative`` and is never production or Phase B2 evidence.

This module does not launch, resume, terminate, or inspect a live process.  It
does not call Docker, WSL, ETW, Kubernetes, or a service endpoint.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import re
import secrets
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import PureWindowsPath
from typing import Any, Protocol, Sequence


WORK_ORDER_SCHEMA = "evm.s8-v4.x1.phase-b2.r7s7.reviewer-work-order.v1"
INTERNAL_APPROVAL_SCHEMA = (
    "evm.s8-v4.x1.phase-b2.r7s7.internal-non-authoritative.approval-receipt.v1"
)
INTERNAL_RESERVATION_SCHEMA = "evm.s8-v4.x1.phase-b2.r7s7.internal-non-authoritative.reservation.v1"
INTERNAL_PLAN_SCHEMA = "evm.s8-v4.x1.phase-b2.r7s7.internal-non-authoritative.suspended-plan.v1"
INTERNAL_JOB_SCHEMA = "evm.s8-v4.x1.phase-b2.r7s7.internal-non-authoritative.query-only-job.v1"
INTERNAL_PUBLICATION_SCHEMA = "evm.s8-v4.x1.phase-b2.r7s7.internal-non-authoritative.publication.v1"
INVOCATION_SCHEMA = "evm.s8-v4.x1.phase-b2.r7s7.normalized-invocation.v1"
INTERNAL_RESULT_SCHEMA = "evm.s8-v4.x1.phase-b2.r7s7.internal-non-authoritative.admission-result.v1"
INTERNAL_FAILURE_SCHEMA = (
    "evm.s8-v4.x1.phase-b2.r7s7.internal-non-authoritative.admission-failure.v1"
)

CANONICAL_RESERVATION_ROOT = (
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation"
    r"\private\s8-v4\x1-clock-phase-b2\pre-r8-r7s7-review-reservations"
)
CANONICAL_EVIDENCE_ROOT = (
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation"
    r"\private\s8-v4\x1-clock-phase-b2\pre-r8-r7s7-review-evidence"
)

PRODUCTION_EXTERNAL_AUTHORITY_CONFIGURED = False
PRODUCTION_CROSS_PROCESS_RESERVATION_CONFIGURED = False
PRODUCTION_WORM_EVIDENCE_ADAPTER_CONFIGURED = False
PRODUCTION_PROCESS_CREATION_ENABLED = False
PRODUCTION_ENTRY_ENABLED = False

HEX32_RE = re.compile(r"[0-9a-f]{32}\Z")
HEX40_RE = re.compile(r"[0-9a-f]{40}\Z")
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
SID_RE = re.compile(r"S-\d+(?:-\d+)+\Z")
FILE_TYPE_DISK = 1

ORDERED_STAGES = (
    "work_order_digest_bound",
    "external_approval_verified",
    "receipt_replay_checked",
    "cross_process_reservation_acquired",
    "source_tool_handles_bound",
    "suspended_admin_root_plan_validated",
    "query_only_job_snapshots_equal",
    "launch_nonce_cleared",
    "source_tool_handles_revalidated",
    "atomic_no_replace_evidence_published",
)


def canonical_json_bytes(value: Any) -> bytes:
    """Return strict deterministic UTF-8 JSON with one LF."""

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


def _exact_uuid4(value: object, label: str) -> str:
    if type(value) is not str:
        raise _StageFailure(f"{label}_uuid4_invalid", "work_order")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise _StageFailure(f"{label}_uuid4_invalid", "work_order") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise _StageFailure(f"{label}_uuid4_not_canonical", "work_order")
    return value


def _exact_hex(value: object, pattern: re.Pattern[str], label: str, stage: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise _StageFailure(f"{label}_invalid", stage)
    return value


def _exact_nonzero_hex(value: object, pattern: re.Pattern[str], label: str, stage: str) -> str:
    result = _exact_hex(value, pattern, label, stage)
    if not any(character != "0" for character in result):
        raise _StageFailure(f"{label}_zero_identity_invalid", stage)
    return result


def _exact_positive_int(value: object, label: str, stage: str) -> int:
    if type(value) is not int or value <= 0:
        raise _StageFailure(f"{label}_positive_int_required", stage)
    return value


def _safe_id(value: object, label: str, stage: str) -> str:
    if type(value) is not str or SAFE_ID_RE.fullmatch(value) is None:
        raise _StageFailure(f"{label}_invalid", stage)
    return value


def _normal_windows_path(value: object, label: str, stage: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise _StageFailure(f"{label}_invalid", stage)
    path = PureWindowsPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise _StageFailure(f"{label}_not_absolute", stage)
    normalized = ntpath.normpath(value)
    for component in normalized.replace("/", "\\").split("\\"):
        if component and component not in {".", ".."} and component[-1:] in {" ", "."}:
            raise _StageFailure(f"{label}_ambiguous_component", stage)
    return ntpath.normcase(normalized)


def _strict_leaf(value: object, label: str, stage: str) -> str:
    if (
        type(value) is not str
        or not value
        or value in {".", ".."}
        or "\x00" in value
        or "/" in value
        or "\\" in value
        or ntpath.basename(value) != value
        or value[-1:] in {" ", "."}
    ):
        raise _StageFailure(f"{label}_invalid", stage)
    return value


def _strict_json_mapping(raw: bytes, label: str) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or b"\r" in raw or not raw.endswith(b"\n"):
        raise _StageFailure(f"{label}_canonical_bytes_required", "work_order")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise _StageFailure(f"{label}_duplicate_key:{key}", "work_order")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _StageFailure(f"{label}_invalid_json", "work_order") from exc
    if type(value) is not dict or canonical_json_bytes(value) != raw:
        raise _StageFailure(f"{label}_not_strict_canonical_json", "work_order")
    return value


@dataclass(frozen=True, slots=True)
class ReviewerExpectation:
    work_order_sha256: str
    global_run_id: str
    run_uuid: str
    attempt_uuid: str
    commit: str
    tree: str
    candidate_sha256: str


@dataclass(frozen=True, slots=True)
class DirectoryIdentity:
    role: str
    final_path: str
    volume_serial_number: int
    file_id_hex: str
    owner_sid: str
    security_descriptor_sha256: str
    dacl_present: bool
    dacl_protected: bool
    link_count: int
    reparse_tag: int
    file_type: int
    is_directory: bool


@dataclass(frozen=True, slots=True)
class HandleIdentity:
    role: str
    final_path: str
    volume_serial_number: int
    file_id_hex: str
    sha256: str
    bytes: int
    owner_sid: str
    security_descriptor_sha256: str
    dacl_present: bool
    dacl_protected: bool
    link_count: int
    reparse_tag: int
    file_type: int
    creation_time_ns: int
    parent_directory_identity: DirectoryIdentity


@dataclass(frozen=True, slots=True)
class NormalizedInvocation:
    schema: str
    working_directory: str
    argv: tuple[str, ...]
    absolute_path_argument_indexes: tuple[int, ...]
    canonical_sha256: str


@dataclass(frozen=True, slots=True)
class RenameIdentityEvidence:
    temporary_leaf: str
    file_handle: int
    before_identity: HandleIdentity
    after_identity: HandleIdentity
    same_file_handle_across_rename: bool
    rename_no_replace: bool


@dataclass(frozen=True, slots=True)
class WorkOrder:
    global_run_id: str
    run_uuid: str
    attempt_uuid: str
    commit: str
    tree: str
    candidate_sha256: str
    execution_mode: str
    source_identity: HandleIdentity
    tool_identities: tuple[HandleIdentity, ...]
    normalized_invocation: NormalizedInvocation
    reservation_directory_identity: DirectoryIdentity
    evidence_directory_identity: DirectoryIdentity
    raw_sha256: str


@dataclass(frozen=True, slots=True)
class VerifiedApprovalReceipt:
    schema: str
    authority_scope: str
    receipt_sha256: str
    approval_id: str
    work_order_sha256: str
    global_run_id: str
    run_uuid: str
    attempt_uuid: str
    commit: str
    tree: str
    candidate_sha256: str
    externally_verified: bool
    approve_exact_reviewer_candidate_once: bool
    production_go: bool = False


@dataclass(frozen=True, slots=True)
class ReservationAcquisition:
    schema: str
    collision_key_sha256: str
    root_path: str
    final_path: str
    leaf: str
    handle: int
    record_sha256: str
    create_no_replace: bool
    replace_if_exists: bool
    cross_process_visible: bool
    same_handle_readback: bool
    handle_retained: bool
    directory_handle: int
    directory_identity_before: DirectoryIdentity
    directory_identity_after: DirectoryIdentity
    directory_handle_retained: bool
    same_directory_handle_across_rename: bool
    rename_identity: RenameIdentityEvidence
    path_fallback_count: int
    production_go: bool = False


@dataclass(frozen=True, slots=True)
class BoundIdentityAcquisition:
    handle_ids: tuple[int, ...]
    directory_handle_ids: tuple[int, ...]
    source_identity: HandleIdentity
    tool_identities: tuple[HandleIdentity, ...]
    same_handle_readback: bool
    same_directory_handle_readback: bool
    handles_retained: bool
    directory_handles_retained: bool
    snapshot_sha256: str


@dataclass(frozen=True, slots=True)
class SuspendedAdminRootPlan:
    schema: str
    plan_id: str
    work_order_sha256: str
    receipt_sha256: str
    reservation_key_sha256: str
    identity_snapshot_sha256: str
    job_identity: str
    normalized_invocation: NormalizedInvocation
    command_sha256: str
    create_suspended: bool
    administrator_required: bool
    integrity_required: str
    elevation_type_required: str
    process_created: bool
    root_resumed: bool
    launch_nonce: bytearray
    production_go: bool = False


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    job_identity: str
    active_process_count: int
    total_process_count: int
    assigned_process_id_list: tuple[int, ...]
    accounting_sequence: int


@dataclass(frozen=True, slots=True)
class QueryOnlyJobEvidence:
    schema: str
    plan_id: str
    job_identity: str
    access_rights: str
    query_only: bool
    can_assign: bool
    can_set_limits: bool
    can_terminate: bool
    explicit_snapshot: JobSnapshot
    implicit_snapshot: JobSnapshot
    explicit_query_count: int
    implicit_query_count: int
    production_go: bool = False


@dataclass(frozen=True, slots=True)
class AtomicEvidencePublication:
    schema: str
    root_path: str
    final_path: str
    leaf: str
    sha256: str
    bytes: int
    create_attempt_count: int
    atomic_rename: bool
    create_no_replace: bool
    replace_if_exists: bool
    same_handle_readback: bool
    directory_handle: int
    directory_identity_before: DirectoryIdentity
    directory_identity_after: DirectoryIdentity
    directory_handle_retained: bool
    same_directory_handle_across_rename: bool
    rename_identity: RenameIdentityEvidence
    file_flush_count: int
    directory_flush_count: int
    directory_flush_succeeded: bool
    worm_append_only: bool
    path_fallback_count: int
    success_marker_count: int
    completion_marker_count: int
    production_go: bool = False


@dataclass(frozen=True, slots=True)
class AdmissionCallCounts:
    work_order_validation: int = 0
    receipt_verification: int = 0
    receipt_replay_check: int = 0
    reservation_acquire: int = 0
    reservation_readback: int = 0
    identity_bind: int = 0
    plan_build: int = 0
    job_capability_query: int = 0
    job_explicit_snapshot_query: int = 0
    job_implicit_snapshot_query: int = 0
    nonce_clear: int = 0
    identity_readback: int = 0
    evidence_publication: int = 0
    reservation_close: int = 0
    identity_close: int = 0
    process_creation: int = 0
    process_resume: int = 0
    automatic_retry: int = 0
    force_termination: int = 0
    success_marker: int = 0
    completion_marker: int = 0
    path_fallback: int = 0
    docker: int = 0
    wsl: int = 0
    etw: int = 0
    r8: int = 0


class _MutableCounts:
    def __init__(self) -> None:
        for field in AdmissionCallCounts.__dataclass_fields__:
            setattr(self, field, 0)

    def snapshot(self) -> AdmissionCallCounts:
        return AdmissionCallCounts(
            **{field: getattr(self, field) for field in AdmissionCallCounts.__dataclass_fields__}
        )


class _StageFailure(RuntimeError):
    def __init__(self, code: str, stage: str) -> None:
        super().__init__(code)
        self.code = code
        self.stage = stage


class R7S7AdmissionError(RuntimeError):
    """Terminal, non-authoritative and fail-closed reviewer-pending result."""

    status = "reviewer_pending"
    decision = "NO-GO"
    credit = "zero_credit"
    production_go = False
    automatic_retry_allowed = False
    force_termination_allowed = False

    def __init__(
        self,
        code: str,
        *,
        stage: str,
        counts: AdmissionCallCounts,
        completed_stages: Sequence[str] = (),
    ) -> None:
        super().__init__(code)
        self.code = code
        self.stage = stage
        self.counts = counts
        self.completed_stages = tuple(completed_stages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": INTERNAL_FAILURE_SCHEMA,
            "status": self.status,
            "decision": self.decision,
            "credit": self.credit,
            "code": self.code,
            "stage": self.stage,
            "completed_stages": list(self.completed_stages),
            "counts": asdict(self.counts),
            "launch_nonce_present": False,
            "automatic_retry_allowed": False,
            "force_termination_allowed": False,
            "success_marker_created": False,
            "completion_marker_created": False,
            "production_go": False,
        }


@dataclass(frozen=True, slots=True)
class InternalReviewerCandidateResult:
    work_order_sha256: str
    receipt_sha256: str
    collision_key_sha256: str
    identity_snapshot_sha256: str
    normalized_invocation_sha256: str
    plan_sha256: str
    job_snapshot_sha256: str
    publication: AtomicEvidencePublication
    completed_stages: tuple[str, ...]
    counts: AdmissionCallCounts
    reservation_closed: bool
    identity_handles_closed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": INTERNAL_RESULT_SCHEMA,
            "status": "internal_non_authoritative",
            "decision": "reviewer_pending",
            "credit": "zero_credit",
            "work_order_sha256": self.work_order_sha256,
            "receipt_sha256": self.receipt_sha256,
            "collision_key_sha256": self.collision_key_sha256,
            "identity_snapshot_sha256": self.identity_snapshot_sha256,
            "normalized_invocation_sha256": self.normalized_invocation_sha256,
            "plan_sha256": self.plan_sha256,
            "job_snapshot_sha256": self.job_snapshot_sha256,
            "publication": asdict(self.publication),
            "completed_stages": list(self.completed_stages),
            "counts": asdict(self.counts),
            "reservation_closed": self.reservation_closed,
            "identity_handles_closed": self.identity_handles_closed,
            "launch_nonce_present": False,
            "automatic_retry_allowed": False,
            "force_termination_allowed": False,
            "success_marker_created": False,
            "completion_marker_created": False,
            "ready_for_production_closure": False,
            "production_go": False,
        }


class ApprovalVerifierForTest(Protocol):
    def verify_for_test(
        self, receipt_raw: bytes, *, work_order_sha256: str
    ) -> VerifiedApprovalReceipt: ...


class ReservationAdapterForTest(Protocol):
    def acquire_once_for_test(
        self, *, collision_key_sha256: str, record_raw: bytes
    ) -> ReservationAcquisition: ...

    def read_same_handles_for_test(
        self, handle: int, directory_handle: int
    ) -> ReservationAcquisition: ...

    def close_for_test(self, handle: int, directory_handle: int) -> None: ...


class IdentityBinderForTest(Protocol):
    def bind_for_test(self, order: WorkOrder) -> BoundIdentityAcquisition: ...

    def read_same_handles_for_test(
        self,
        handle_ids: tuple[int, ...],
        directory_handle_ids: tuple[int, ...],
    ) -> BoundIdentityAcquisition: ...

    def close_for_test(
        self,
        handle_ids: tuple[int, ...],
        directory_handle_ids: tuple[int, ...],
    ) -> None: ...


class PlanBuilderForTest(Protocol):
    def build_for_test(
        self,
        *,
        order: WorkOrder,
        receipt: VerifiedApprovalReceipt,
        reservation: ReservationAcquisition,
        identities: BoundIdentityAcquisition,
        launch_nonce: bytearray,
    ) -> SuspendedAdminRootPlan: ...


class QueryOnlyJobAdapterForTest(Protocol):
    def query_for_test(self, plan: SuspendedAdminRootPlan) -> QueryOnlyJobEvidence: ...


class AtomicEvidenceWriterForTest(Protocol):
    def publish_no_replace_for_test(
        self, *, root_path: str, final_leaf: str, raw: bytes
    ) -> AtomicEvidencePublication: ...


def _directory_identity(
    value: object,
    *,
    expected_role: str,
    expected_path: str | None,
    stage: str,
) -> DirectoryIdentity:
    if type(value) is not DirectoryIdentity:
        raise _StageFailure(f"{expected_role}_typed_directory_identity_required", stage)
    assert isinstance(value, DirectoryIdentity)
    if value.role != expected_role:
        raise _StageFailure(f"{expected_role}_directory_role_mismatch", stage)
    observed_path = _normal_windows_path(value.final_path, f"{expected_role}_directory_path", stage)
    if expected_path is not None and observed_path != _normal_windows_path(
        expected_path, f"{expected_role}_expected_directory_path", stage
    ):
        raise _StageFailure(f"{expected_role}_directory_path_mismatch", stage)
    _exact_positive_int(value.volume_serial_number, f"{expected_role}_directory_volume", stage)
    _exact_nonzero_hex(value.file_id_hex, HEX32_RE, f"{expected_role}_directory_file_id", stage)
    if type(value.owner_sid) is not str or SID_RE.fullmatch(value.owner_sid) is None:
        raise _StageFailure(f"{expected_role}_directory_owner_sid_invalid", stage)
    _exact_hex(
        value.security_descriptor_sha256,
        HEX64_RE,
        f"{expected_role}_directory_security_descriptor_sha256",
        stage,
    )
    if value.dacl_present is not True or value.dacl_protected is not True:
        raise _StageFailure(f"{expected_role}_directory_dacl_not_protected", stage)
    if type(value.link_count) is not int or value.link_count < 1:
        raise _StageFailure(f"{expected_role}_directory_link_count_invalid", stage)
    if type(value.reparse_tag) is not int or value.reparse_tag != 0:
        raise _StageFailure(f"{expected_role}_directory_reparse_present", stage)
    if type(value.file_type) is not int or value.file_type != FILE_TYPE_DISK:
        raise _StageFailure(f"{expected_role}_directory_not_disk", stage)
    if value.is_directory is not True:
        raise _StageFailure(f"{expected_role}_not_directory", stage)
    return value


def _directory_from_json(
    value: object,
    *,
    expected_role: str,
    expected_path: str | None = None,
) -> DirectoryIdentity:
    if type(value) is not dict or set(value) != set(DirectoryIdentity.__dataclass_fields__):
        raise _StageFailure(f"{expected_role}_directory_identity_keys_not_exact", "work_order")
    try:
        identity = DirectoryIdentity(**value)
    except TypeError as exc:
        raise _StageFailure(f"{expected_role}_directory_identity_invalid", "work_order") from exc
    return _directory_identity(
        identity,
        expected_role=expected_role,
        expected_path=expected_path,
        stage="work_order",
    )


def _handle_identity(value: object, *, expected_role: str, stage: str) -> HandleIdentity:
    if type(value) is not HandleIdentity:
        raise _StageFailure(f"{expected_role}_typed_handle_identity_required", stage)
    assert isinstance(value, HandleIdentity)
    if value.role != expected_role:
        raise _StageFailure(f"{expected_role}_role_mismatch", stage)
    _normal_windows_path(value.final_path, f"{expected_role}_path", stage)
    _exact_positive_int(value.volume_serial_number, f"{expected_role}_volume", stage)
    _exact_nonzero_hex(value.file_id_hex, HEX32_RE, f"{expected_role}_file_id", stage)
    _exact_hex(value.sha256, HEX64_RE, f"{expected_role}_sha256", stage)
    if type(value.bytes) is not int or value.bytes < 0:
        raise _StageFailure(f"{expected_role}_bytes_invalid", stage)
    if type(value.owner_sid) is not str or SID_RE.fullmatch(value.owner_sid) is None:
        raise _StageFailure(f"{expected_role}_owner_sid_invalid", stage)
    _exact_hex(
        value.security_descriptor_sha256,
        HEX64_RE,
        f"{expected_role}_security_descriptor_sha256",
        stage,
    )
    if value.dacl_present is not True or value.dacl_protected is not True:
        raise _StageFailure(f"{expected_role}_dacl_not_protected", stage)
    if type(value.link_count) is not int or value.link_count != 1:
        raise _StageFailure(f"{expected_role}_link_count_invalid", stage)
    if type(value.reparse_tag) is not int or value.reparse_tag != 0:
        raise _StageFailure(f"{expected_role}_reparse_present", stage)
    if type(value.file_type) is not int or value.file_type != FILE_TYPE_DISK:
        raise _StageFailure(f"{expected_role}_not_disk_file", stage)
    _exact_positive_int(value.creation_time_ns, f"{expected_role}_creation_time_ns", stage)
    parent = _directory_identity(
        value.parent_directory_identity,
        expected_role=f"{expected_role}:parent",
        expected_path=ntpath.dirname(value.final_path),
        stage=stage,
    )
    if parent.volume_serial_number != value.volume_serial_number:
        raise _StageFailure(f"{expected_role}_parent_volume_mismatch", stage)
    return value


def _handle_from_json(value: object, *, expected_role: str) -> HandleIdentity:
    if type(value) is not dict:
        raise _StageFailure(f"{expected_role}_identity_mapping_required", "work_order")
    expected_keys = set(HandleIdentity.__dataclass_fields__)
    if set(value) != expected_keys:
        raise _StageFailure(f"{expected_role}_identity_keys_not_exact", "work_order")
    try:
        fields = dict(value)
        fields["parent_directory_identity"] = _directory_from_json(
            fields["parent_directory_identity"],
            expected_role=f"{expected_role}:parent",
            expected_path=ntpath.dirname(fields["final_path"]),
        )
        identity = HandleIdentity(**fields)
    except TypeError as exc:
        raise _StageFailure(f"{expected_role}_identity_invalid", "work_order") from exc
    return _handle_identity(identity, expected_role=expected_role, stage="work_order")


def _invocation_digest_payload(value: NormalizedInvocation) -> dict[str, Any]:
    return {
        "schema": value.schema,
        "working_directory": value.working_directory,
        "argv": list(value.argv),
        "absolute_path_argument_indexes": list(value.absolute_path_argument_indexes),
    }


def _validate_normalized_invocation(
    value: object,
    *,
    source: HandleIdentity,
    tools: tuple[HandleIdentity, ...],
    stage: str,
) -> NormalizedInvocation:
    if type(value) is not NormalizedInvocation:
        raise _StageFailure("typed_normalized_invocation_required", stage)
    assert isinstance(value, NormalizedInvocation)
    if value.schema != INVOCATION_SCHEMA:
        raise _StageFailure("normalized_invocation_schema_mismatch", stage)
    working_directory = _normal_windows_path(
        value.working_directory, "invocation_working_directory", stage
    )
    if value.working_directory != ntpath.normpath(value.working_directory):
        raise _StageFailure("invocation_working_directory_not_normalized", stage)
    if type(value.argv) is not tuple or not value.argv:
        raise _StageFailure("normalized_invocation_argv_required", stage)
    for index, argument in enumerate(value.argv):
        if (
            type(argument) is not str
            or not argument
            or "\x00" in argument
            or "\r" in argument
            or "\n" in argument
        ):
            raise _StageFailure(f"normalized_invocation_argument_invalid:{index}", stage)
    indexes = value.absolute_path_argument_indexes
    if (
        type(indexes) is not tuple
        or not indexes
        or indexes[0] != 0
        or any(type(index) is not int or index < 0 or index >= len(value.argv) for index in indexes)
        or tuple(sorted(set(indexes))) != indexes
    ):
        raise _StageFailure("invocation_absolute_path_indexes_invalid", stage)
    normalized_arguments: list[str | None] = [None] * len(value.argv)
    for index in indexes:
        argument = value.argv[index]
        normalized_arguments[index] = _normal_windows_path(
            argument,
            f"invocation_absolute_path_argument:{index}",
            stage,
        )
        if argument != ntpath.normpath(argument):
            raise _StageFailure(f"invocation_path_argument_not_normalized:{index}", stage)
    for index, argument in enumerate(value.argv):
        if index in indexes:
            continue
        if PureWindowsPath(argument).is_absolute() or "/" in argument or "\\" in argument:
            raise _StageFailure(f"invocation_unclassified_path_argument:{index}", stage)
    executable = normalized_arguments[0]
    assert executable is not None
    tool_paths = {
        _normal_windows_path(item.final_path, f"invocation_tool:{index}", stage)
        for index, item in enumerate(tools)
    }
    if executable not in tool_paths:
        raise _StageFailure("invocation_executable_not_handle_bound", stage)
    bound_paths = (
        _normal_windows_path(source.final_path, "invocation_source", stage),
        *(
            _normal_windows_path(item.final_path, f"invocation_tool:{index}", stage)
            for index, item in enumerate(tools)
        ),
    )
    bound_parent_paths = {
        _normal_windows_path(
            item.parent_directory_identity.final_path,
            f"invocation_bound_parent:{index}",
            stage,
        )
        for index, item in enumerate((source, *tools))
    }
    if working_directory not in bound_parent_paths:
        raise _StageFailure("invocation_working_directory_not_handle_bound", stage)
    for bound_path in bound_paths:
        if normalized_arguments.count(bound_path) != 1:
            raise _StageFailure("invocation_bound_path_count_mismatch", stage)
    expected_sha256 = _sha256(canonical_json_bytes(_invocation_digest_payload(value)))
    if value.canonical_sha256 != expected_sha256:
        raise _StageFailure("normalized_invocation_sha256_mismatch", stage)
    return value


def _invocation_from_json(
    value: object,
    *,
    source: HandleIdentity,
    tools: tuple[HandleIdentity, ...],
) -> NormalizedInvocation:
    if type(value) is not dict or set(value) != set(NormalizedInvocation.__dataclass_fields__):
        raise _StageFailure("normalized_invocation_keys_not_exact", "work_order")
    fields = dict(value)
    if (
        type(fields["argv"]) is not list
        or type(fields["absolute_path_argument_indexes"]) is not list
    ):
        raise _StageFailure("normalized_invocation_argv_list_required", "work_order")
    fields["argv"] = tuple(fields["argv"])
    fields["absolute_path_argument_indexes"] = tuple(fields["absolute_path_argument_indexes"])
    try:
        invocation = NormalizedInvocation(**fields)
    except TypeError as exc:
        raise _StageFailure("normalized_invocation_invalid", "work_order") from exc
    return _validate_normalized_invocation(
        invocation,
        source=source,
        tools=tools,
        stage="work_order",
    )


def _validate_rename_identity(
    value: object,
    *,
    expected_role: str,
    expected_directory: DirectoryIdentity,
    expected_temporary_leaf: str,
    expected_final_path: str,
    expected_sha256: str,
    expected_bytes: int,
    expected_file_handle: int,
    stage: str,
) -> RenameIdentityEvidence:
    if type(value) is not RenameIdentityEvidence:
        raise _StageFailure(f"{expected_role}_typed_rename_identity_required", stage)
    assert isinstance(value, RenameIdentityEvidence)
    temporary_leaf = _strict_leaf(value.temporary_leaf, f"{expected_role}_temporary_leaf", stage)
    if temporary_leaf != expected_temporary_leaf:
        raise _StageFailure(f"{expected_role}_temporary_leaf_mismatch", stage)
    if (
        type(value.file_handle) is not int
        or value.file_handle <= 0
        or value.file_handle != expected_file_handle
    ):
        raise _StageFailure(f"{expected_role}_rename_handle_mismatch", stage)
    before = _handle_identity(value.before_identity, expected_role=expected_role, stage=stage)
    after = _handle_identity(value.after_identity, expected_role=expected_role, stage=stage)
    expected_before_path = ntpath.join(expected_directory.final_path, temporary_leaf)
    if _normal_windows_path(before.final_path, f"{expected_role}_before_path", stage) != (
        _normal_windows_path(expected_before_path, f"{expected_role}_expected_before_path", stage)
    ):
        raise _StageFailure(f"{expected_role}_before_path_mismatch", stage)
    if _normal_windows_path(after.final_path, f"{expected_role}_after_path", stage) != (
        _normal_windows_path(expected_final_path, f"{expected_role}_expected_after_path", stage)
    ):
        raise _StageFailure(f"{expected_role}_after_path_mismatch", stage)
    if (
        before.parent_directory_identity != expected_directory
        or after.parent_directory_identity != expected_directory
    ):
        raise _StageFailure(f"{expected_role}_parent_directory_identity_mismatch", stage)
    if (
        before.sha256 != expected_sha256
        or after.sha256 != expected_sha256
        or before.bytes != expected_bytes
        or after.bytes != expected_bytes
    ):
        raise _StageFailure(f"{expected_role}_content_binding_mismatch", stage)
    if replace(before, final_path=after.final_path) != after:
        raise _StageFailure(f"{expected_role}_file_identity_changed_across_rename", stage)
    if value.same_file_handle_across_rename is not True or value.rename_no_replace is not True:
        raise _StageFailure(f"{expected_role}_rename_contract_mismatch", stage)
    return value


def _validate_expectation(value: object) -> ReviewerExpectation:
    if type(value) is not ReviewerExpectation:
        raise _StageFailure("typed_reviewer_expectation_required", "work_order")
    assert isinstance(value, ReviewerExpectation)
    _exact_hex(value.work_order_sha256, HEX64_RE, "expected_work_order_sha256", "work_order")
    _exact_uuid4(value.global_run_id, "expected_global_run_id")
    _exact_uuid4(value.run_uuid, "expected_run_uuid")
    _exact_uuid4(value.attempt_uuid, "expected_attempt_uuid")
    _exact_hex(value.commit, HEX40_RE, "expected_commit", "work_order")
    _exact_hex(value.tree, HEX40_RE, "expected_tree", "work_order")
    _exact_hex(value.candidate_sha256, HEX64_RE, "expected_candidate_sha256", "work_order")
    return value


def _parse_work_order(raw: bytes, expectation: ReviewerExpectation) -> WorkOrder:
    expected = _validate_expectation(expectation)
    raw_sha = _sha256(raw) if type(raw) is bytes else ""
    if raw_sha != expected.work_order_sha256:
        raise _StageFailure("work_order_oob_digest_mismatch", "work_order")
    value = _strict_json_mapping(raw, "work_order")
    required = {
        "schema",
        "global_run_id",
        "run_uuid",
        "attempt_uuid",
        "commit",
        "tree",
        "candidate_sha256",
        "execution_mode",
        "source_identity",
        "tool_identities",
        "normalized_invocation",
        "reservation_directory_identity",
        "evidence_directory_identity",
    }
    if set(value) != required:
        raise _StageFailure("work_order_keys_not_exact", "work_order")
    if value["schema"] != WORK_ORDER_SCHEMA:
        raise _StageFailure("work_order_schema_mismatch", "work_order")
    if value["execution_mode"] != "offline_reviewer_candidate":
        raise _StageFailure("work_order_execution_mode_mismatch", "work_order")
    for name in ("global_run_id", "run_uuid", "attempt_uuid"):
        observed = _exact_uuid4(value[name], name)
        if observed != getattr(expected, name):
            raise _StageFailure(f"work_order_{name}_mismatch", "work_order")
    for name, pattern in (
        ("commit", HEX40_RE),
        ("tree", HEX40_RE),
        ("candidate_sha256", HEX64_RE),
    ):
        observed = _exact_hex(value[name], pattern, f"work_order_{name}", "work_order")
        if observed != getattr(expected, name):
            raise _StageFailure(f"work_order_{name}_mismatch", "work_order")
    source = _handle_from_json(value["source_identity"], expected_role="source")
    tool_values = value["tool_identities"]
    if type(tool_values) is not list or not tool_values:
        raise _StageFailure("work_order_tool_identities_required", "work_order")
    tools = tuple(
        _handle_from_json(item, expected_role=f"tool:{index}")
        for index, item in enumerate(tool_values)
    )
    invocation = _invocation_from_json(
        value["normalized_invocation"],
        source=source,
        tools=tools,
    )
    reservation_directory = _directory_from_json(
        value["reservation_directory_identity"],
        expected_role="reservation_marker:parent",
        expected_path=CANONICAL_RESERVATION_ROOT,
    )
    evidence_directory = _directory_from_json(
        value["evidence_directory_identity"],
        expected_role="evidence_record:parent",
        expected_path=CANONICAL_EVIDENCE_ROOT,
    )
    if (
        reservation_directory.volume_serial_number,
        reservation_directory.file_id_hex,
    ) == (
        evidence_directory.volume_serial_number,
        evidence_directory.file_id_hex,
    ):
        raise _StageFailure("work_order_directory_identity_collision", "work_order")
    paths = [source.final_path, *(item.final_path for item in tools)]
    if len({ntpath.normcase(ntpath.normpath(item)) for item in paths}) != len(paths):
        raise _StageFailure("work_order_identity_path_collision", "work_order")
    return WorkOrder(
        global_run_id=value["global_run_id"],
        run_uuid=value["run_uuid"],
        attempt_uuid=value["attempt_uuid"],
        commit=value["commit"],
        tree=value["tree"],
        candidate_sha256=value["candidate_sha256"],
        execution_mode=value["execution_mode"],
        source_identity=source,
        tool_identities=tools,
        normalized_invocation=invocation,
        reservation_directory_identity=reservation_directory,
        evidence_directory_identity=evidence_directory,
        raw_sha256=raw_sha,
    )


def _validate_receipt(
    value: object, *, receipt_raw: bytes, order: WorkOrder
) -> VerifiedApprovalReceipt:
    if type(value) is not VerifiedApprovalReceipt:
        raise _StageFailure("typed_verified_approval_receipt_required", "receipt")
    assert isinstance(value, VerifiedApprovalReceipt)
    if type(receipt_raw) is not bytes or not receipt_raw:
        raise _StageFailure("approval_receipt_bytes_required", "receipt")
    exact = {
        "schema": INTERNAL_APPROVAL_SCHEMA,
        "authority_scope": "internal_non_authoritative_test_double",
        "receipt_sha256": _sha256(receipt_raw),
        "work_order_sha256": order.raw_sha256,
        "global_run_id": order.global_run_id,
        "run_uuid": order.run_uuid,
        "attempt_uuid": order.attempt_uuid,
        "commit": order.commit,
        "tree": order.tree,
        "candidate_sha256": order.candidate_sha256,
        "externally_verified": True,
        "approve_exact_reviewer_candidate_once": True,
        "production_go": False,
    }
    for name, wanted in exact.items():
        if getattr(value, name) != wanted:
            raise _StageFailure(f"approval_receipt_binding_mismatch:{name}", "receipt")
    _safe_id(value.approval_id, "approval_id", "receipt")
    return value


def _execution_collision_key(order: WorkOrder) -> str:
    return _sha256(
        canonical_json_bytes(
            {
                "schema": "evm.s8-v4.x1.phase-b2.r7s7.execution-collision-key.v1",
                "work_order_sha256": order.raw_sha256,
                "global_run_id": order.global_run_id,
                "run_uuid": order.run_uuid,
                "attempt_uuid": order.attempt_uuid,
                "commit": order.commit,
                "tree": order.tree,
                "candidate_sha256": order.candidate_sha256,
            }
        )
    )


def _reservation_leaf(collision_key: str) -> str:
    return f"r7s7-review-{collision_key}.reservation.json"


def _validate_reservation(
    value: object,
    *,
    collision_key: str,
    record_raw: bytes,
    order: WorkOrder,
) -> ReservationAcquisition:
    if type(value) is not ReservationAcquisition:
        raise _StageFailure("typed_reservation_acquisition_required", "reservation")
    assert isinstance(value, ReservationAcquisition)
    leaf = _reservation_leaf(collision_key)
    expected_final = ntpath.join(CANONICAL_RESERVATION_ROOT, leaf)
    directory_before = _directory_identity(
        value.directory_identity_before,
        expected_role="reservation_marker:parent",
        expected_path=CANONICAL_RESERVATION_ROOT,
        stage="reservation",
    )
    directory_after = _directory_identity(
        value.directory_identity_after,
        expected_role="reservation_marker:parent",
        expected_path=CANONICAL_RESERVATION_ROOT,
        stage="reservation",
    )
    _validate_rename_identity(
        value.rename_identity,
        expected_role="reservation_marker",
        expected_directory=order.reservation_directory_identity,
        expected_temporary_leaf=f".{leaf}.{order.run_uuid}.partial",
        expected_final_path=expected_final,
        expected_sha256=_sha256(record_raw),
        expected_bytes=len(record_raw),
        expected_file_handle=value.handle,
        stage="reservation",
    )
    if (
        value.schema != INTERNAL_RESERVATION_SCHEMA
        or value.collision_key_sha256 != collision_key
        or _normal_windows_path(value.root_path, "reservation_root", "reservation")
        != _normal_windows_path(
            CANONICAL_RESERVATION_ROOT, "canonical_reservation_root", "reservation"
        )
        or _normal_windows_path(value.final_path, "reservation_final", "reservation")
        != _normal_windows_path(expected_final, "expected_reservation_final", "reservation")
        or value.leaf != leaf
        or type(value.handle) is not int
        or value.handle <= 0
        or value.record_sha256 != _sha256(record_raw)
        or value.create_no_replace is not True
        or value.replace_if_exists is not False
        or value.cross_process_visible is not True
        or value.same_handle_readback is not True
        or value.handle_retained is not True
        or type(value.directory_handle) is not int
        or value.directory_handle <= 0
        or value.directory_handle == value.handle
        or directory_before != order.reservation_directory_identity
        or directory_after != order.reservation_directory_identity
        or value.directory_handle_retained is not True
        or value.same_directory_handle_across_rename is not True
        or type(value.path_fallback_count) is not int
        or value.path_fallback_count != 0
        or value.production_go is not False
    ):
        raise _StageFailure("reservation_contract_mismatch", "reservation")
    return value


def _identity_snapshot_payload(value: BoundIdentityAcquisition) -> dict[str, Any]:
    return {
        "handle_ids": list(value.handle_ids),
        "directory_handle_ids": list(value.directory_handle_ids),
        "source_identity": asdict(value.source_identity),
        "tool_identities": [asdict(item) for item in value.tool_identities],
        "same_handle_readback": value.same_handle_readback,
        "same_directory_handle_readback": value.same_directory_handle_readback,
        "handles_retained": value.handles_retained,
        "directory_handles_retained": value.directory_handles_retained,
    }


def _identity_snapshot_sha(value: BoundIdentityAcquisition) -> str:
    return _sha256(canonical_json_bytes(_identity_snapshot_payload(value)))


def _validate_bound_identities(
    value: object, *, order: WorkOrder, stage: str
) -> BoundIdentityAcquisition:
    if type(value) is not BoundIdentityAcquisition:
        raise _StageFailure("typed_bound_identity_acquisition_required", stage)
    assert isinstance(value, BoundIdentityAcquisition)
    expected_count = 1 + len(order.tool_identities)
    if (
        type(value.handle_ids) is not tuple
        or len(value.handle_ids) != expected_count
        or any(type(item) is not int or item <= 0 for item in value.handle_ids)
        or len(set(value.handle_ids)) != expected_count
        or type(value.directory_handle_ids) is not tuple
        or len(value.directory_handle_ids) != expected_count
        or any(type(item) is not int or item <= 0 for item in value.directory_handle_ids)
        or len(set(value.directory_handle_ids)) != expected_count
        or set(value.handle_ids) & set(value.directory_handle_ids)
        or value.same_handle_readback is not True
        or value.same_directory_handle_readback is not True
        or value.handles_retained is not True
        or value.directory_handles_retained is not True
    ):
        raise _StageFailure("identity_handle_contract_mismatch", stage)
    source = _handle_identity(value.source_identity, expected_role="source", stage=stage)
    tools = tuple(
        _handle_identity(item, expected_role=f"tool:{index}", stage=stage)
        for index, item in enumerate(value.tool_identities)
    )
    if source != order.source_identity or tools != order.tool_identities:
        raise _StageFailure("source_or_tool_identity_drift", stage)
    actual_sha = _identity_snapshot_sha(value)
    if value.snapshot_sha256 != actual_sha:
        raise _StageFailure("identity_snapshot_sha256_mismatch", stage)
    return value


def _plan_payload(plan: SuspendedAdminRootPlan) -> dict[str, Any]:
    return {
        "schema": plan.schema,
        "plan_id": plan.plan_id,
        "work_order_sha256": plan.work_order_sha256,
        "receipt_sha256": plan.receipt_sha256,
        "reservation_key_sha256": plan.reservation_key_sha256,
        "identity_snapshot_sha256": plan.identity_snapshot_sha256,
        "job_identity": plan.job_identity,
        "normalized_invocation": asdict(plan.normalized_invocation),
        "command_sha256": plan.command_sha256,
        "create_suspended": plan.create_suspended,
        "administrator_required": plan.administrator_required,
        "integrity_required": plan.integrity_required,
        "elevation_type_required": plan.elevation_type_required,
        "process_created": plan.process_created,
        "root_resumed": plan.root_resumed,
        "launch_nonce_redacted": True,
        "production_go": plan.production_go,
    }


def _validate_plan(
    value: object,
    *,
    order: WorkOrder,
    receipt: VerifiedApprovalReceipt,
    reservation: ReservationAcquisition,
    identities: BoundIdentityAcquisition,
    expected_launch_nonce: bytearray,
) -> SuspendedAdminRootPlan:
    if type(value) is not SuspendedAdminRootPlan:
        raise _StageFailure("typed_suspended_admin_root_plan_required", "plan")
    assert isinstance(value, SuspendedAdminRootPlan)
    try:
        plan_uuid = str(uuid.UUID(value.plan_id))
    except (ValueError, AttributeError) as exc:
        raise _StageFailure("plan_id_uuid4_invalid", "plan") from exc
    if (
        plan_uuid != value.plan_id
        or uuid.UUID(value.plan_id).version != 4
        or value.schema != INTERNAL_PLAN_SCHEMA
        or value.work_order_sha256 != order.raw_sha256
        or value.receipt_sha256 != receipt.receipt_sha256
        or value.reservation_key_sha256 != reservation.collision_key_sha256
        or value.identity_snapshot_sha256 != identities.snapshot_sha256
        or type(value.job_identity) is not str
        or not value.job_identity
        or value.normalized_invocation != order.normalized_invocation
        or value.command_sha256 != order.normalized_invocation.canonical_sha256
        or value.create_suspended is not True
        or value.administrator_required is not True
        or value.integrity_required not in {"High", "System"}
        or value.elevation_type_required != "Full"
        or value.process_created is not False
        or value.root_resumed is not False
        or type(value.launch_nonce) is not bytearray
        or value.launch_nonce is not expected_launch_nonce
        or len(value.launch_nonce) != 32
        or not any(value.launch_nonce)
        or value.production_go is not False
    ):
        raise _StageFailure("suspended_admin_root_plan_mismatch", "plan")
    _validate_normalized_invocation(
        value.normalized_invocation,
        source=order.source_identity,
        tools=order.tool_identities,
        stage="plan",
    )
    canonical_json_bytes(_plan_payload(value))
    return value


def _validate_job_evidence(value: object, *, plan: SuspendedAdminRootPlan) -> QueryOnlyJobEvidence:
    if type(value) is not QueryOnlyJobEvidence:
        raise _StageFailure("typed_query_only_job_evidence_required", "job")
    assert isinstance(value, QueryOnlyJobEvidence)
    explicit = value.explicit_snapshot
    implicit = value.implicit_snapshot
    if type(explicit) is not JobSnapshot or type(implicit) is not JobSnapshot:
        raise _StageFailure("typed_job_snapshots_required", "job")
    if (
        value.schema != INTERNAL_JOB_SCHEMA
        or value.plan_id != plan.plan_id
        or value.job_identity != plan.job_identity
        or value.access_rights != "JOB_OBJECT_QUERY"
        or value.query_only is not True
        or value.can_assign is not False
        or value.can_set_limits is not False
        or value.can_terminate is not False
        or type(value.explicit_query_count) is not int
        or value.explicit_query_count != 1
        or type(value.implicit_query_count) is not int
        or value.implicit_query_count != 1
        or value.production_go is not False
    ):
        raise _StageFailure("query_only_job_capability_mismatch", "job")
    for label, snapshot in (("explicit", explicit), ("implicit", implicit)):
        if (
            snapshot.job_identity != plan.job_identity
            or type(snapshot.active_process_count) is not int
            or snapshot.active_process_count != 0
            or type(snapshot.total_process_count) is not int
            or snapshot.total_process_count != 0
            or type(snapshot.assigned_process_id_list) is not tuple
            or snapshot.assigned_process_id_list != ()
            or type(snapshot.accounting_sequence) is not int
            or snapshot.accounting_sequence <= 0
        ):
            raise _StageFailure(f"{label}_job_snapshot_mismatch", "job")
    if explicit != implicit:
        raise _StageFailure("explicit_implicit_job_snapshot_mismatch", "job")
    return value


def _clear_nonce_buffer(launch_nonce: object) -> None:
    if type(launch_nonce) is not bytearray or len(launch_nonce) != 32:
        raise _StageFailure("launch_nonce_buffer_invalid", "nonce_clear")
    for index in range(len(launch_nonce)):
        launch_nonce[index] = 0
    if any(launch_nonce):
        raise _StageFailure("launch_nonce_clear_failed", "nonce_clear")


def _clear_nonce(plan: SuspendedAdminRootPlan) -> None:
    _clear_nonce_buffer(plan.launch_nonce)


def _publication_leaf(collision_key: str) -> str:
    return f"r7s7-review-{collision_key}.internal-non-authoritative.json"


def _validate_publication(
    value: object, *, raw: bytes, collision_key: str, order: WorkOrder
) -> AtomicEvidencePublication:
    if type(value) is not AtomicEvidencePublication:
        raise _StageFailure("typed_atomic_evidence_publication_required", "evidence")
    assert isinstance(value, AtomicEvidencePublication)
    leaf = _publication_leaf(collision_key)
    expected_final = ntpath.join(CANONICAL_EVIDENCE_ROOT, leaf)
    directory_before = _directory_identity(
        value.directory_identity_before,
        expected_role="evidence_record:parent",
        expected_path=CANONICAL_EVIDENCE_ROOT,
        stage="evidence",
    )
    directory_after = _directory_identity(
        value.directory_identity_after,
        expected_role="evidence_record:parent",
        expected_path=CANONICAL_EVIDENCE_ROOT,
        stage="evidence",
    )
    _validate_rename_identity(
        value.rename_identity,
        expected_role="evidence_record",
        expected_directory=order.evidence_directory_identity,
        expected_temporary_leaf=f".{leaf}.{order.run_uuid}.partial",
        expected_final_path=expected_final,
        expected_sha256=_sha256(raw),
        expected_bytes=len(raw),
        expected_file_handle=value.rename_identity.file_handle,
        stage="evidence",
    )
    if (
        value.schema != INTERNAL_PUBLICATION_SCHEMA
        or _normal_windows_path(value.root_path, "evidence_root", "evidence")
        != _normal_windows_path(CANONICAL_EVIDENCE_ROOT, "canonical_evidence_root", "evidence")
        or _normal_windows_path(value.final_path, "evidence_final", "evidence")
        != _normal_windows_path(expected_final, "expected_evidence_final", "evidence")
        or value.leaf != leaf
        or value.sha256 != _sha256(raw)
        or type(value.bytes) is not int
        or value.bytes != len(raw)
        or type(value.create_attempt_count) is not int
        or value.create_attempt_count != 1
        or value.atomic_rename is not True
        or value.create_no_replace is not True
        or value.replace_if_exists is not False
        or value.same_handle_readback is not True
        or type(value.directory_handle) is not int
        or value.directory_handle <= 0
        or value.directory_handle == value.rename_identity.file_handle
        or directory_before != order.evidence_directory_identity
        or directory_after != order.evidence_directory_identity
        or value.directory_handle_retained is not True
        or value.same_directory_handle_across_rename is not True
        or type(value.file_flush_count) is not int
        or value.file_flush_count != 2
        or type(value.directory_flush_count) is not int
        or value.directory_flush_count != 1
        or value.directory_flush_succeeded is not True
        or value.worm_append_only is not True
        or type(value.path_fallback_count) is not int
        or value.path_fallback_count != 0
        or type(value.success_marker_count) is not int
        or value.success_marker_count != 0
        or type(value.completion_marker_count) is not int
        or value.completion_marker_count != 0
        or value.production_go is not False
    ):
        raise _StageFailure("atomic_no_replace_evidence_contract_mismatch", "evidence")
    return value


def _record_failure(exc: BaseException, fallback: str, stage: str) -> _StageFailure:
    if isinstance(exc, _StageFailure):
        return exc
    if isinstance(exc, FileExistsError):
        return _StageFailure("cross_process_reservation_collision", stage)
    code = getattr(exc, "code", None)
    return _StageFailure(code if type(code) is str and code else fallback, stage)


def _admit_reviewer_candidate_for_test(
    work_order_raw: bytes,
    approval_receipt_raw: bytes | None,
    *,
    expected: ReviewerExpectation,
    verifier: ApprovalVerifierForTest,
    reservation_adapter: ReservationAdapterForTest,
    identity_binder: IdentityBinderForTest,
    plan_builder: PlanBuilderForTest,
    job_adapter: QueryOnlyJobAdapterForTest,
    evidence_writer: AtomicEvidenceWriterForTest,
    seen_receipt_sha256s: Sequence[str] = (),
) -> InternalReviewerCandidateResult:
    """Exercise the offline ordering with internal, non-authoritative doubles."""

    counts = _MutableCounts()
    completed: list[str] = []
    reservation: ReservationAcquisition | None = None
    identities: BoundIdentityAcquisition | None = None
    plan: SuspendedAdminRootPlan | None = None
    pending: _StageFailure | None = None
    publication: AtomicEvidencePublication | None = None
    reservation_closed = False
    identities_closed = False
    order: WorkOrder | None = None
    receipt: VerifiedApprovalReceipt | None = None
    collision_key: str | None = None
    plan_sha: str | None = None
    job_sha: str | None = None
    owned_launch_nonce: bytearray | None = None

    try:
        counts.work_order_validation += 1
        order = _parse_work_order(work_order_raw, expected)
        completed.append(ORDERED_STAGES[0])

        if type(approval_receipt_raw) is not bytes or not approval_receipt_raw:
            raise _StageFailure("approval_receipt_absent", "receipt")
        counts.receipt_verification += 1
        try:
            receipt = _validate_receipt(
                verifier.verify_for_test(approval_receipt_raw, work_order_sha256=order.raw_sha256),
                receipt_raw=approval_receipt_raw,
                order=order,
            )
        except Exception as exc:
            raise _record_failure(exc, "approval_receipt_verification_failed", "receipt") from exc
        completed.append(ORDERED_STAGES[1])

        counts.receipt_replay_check += 1
        if receipt.receipt_sha256 in seen_receipt_sha256s:
            raise _StageFailure("approval_receipt_replay", "receipt_replay")
        completed.append(ORDERED_STAGES[2])

        collision_key = _execution_collision_key(order)
        reservation_record = canonical_json_bytes(
            {
                "schema": INTERNAL_RESERVATION_SCHEMA,
                "status": "internal_non_authoritative",
                "work_order_sha256": order.raw_sha256,
                "receipt_sha256": receipt.receipt_sha256,
                "collision_key_sha256": collision_key,
                "reservation_directory_identity": asdict(order.reservation_directory_identity),
                "production_go": False,
            }
        )
        counts.reservation_acquire += 1
        try:
            reservation = _validate_reservation(
                reservation_adapter.acquire_once_for_test(
                    collision_key_sha256=collision_key,
                    record_raw=reservation_record,
                ),
                collision_key=collision_key,
                record_raw=reservation_record,
                order=order,
            )
        except Exception as exc:
            raise _record_failure(exc, "cross_process_reservation_failed", "reservation") from exc
        completed.append(ORDERED_STAGES[3])

        counts.identity_bind += 1
        try:
            identities = _validate_bound_identities(
                identity_binder.bind_for_test(order), order=order, stage="identity_bind"
            )
        except Exception as exc:
            raise _record_failure(exc, "source_tool_identity_bind_failed", "identity_bind") from exc
        completed.append(ORDERED_STAGES[4])

        counts.plan_build += 1
        try:
            owned_launch_nonce = bytearray(secrets.token_bytes(32))
            if len(owned_launch_nonce) != 32 or not any(owned_launch_nonce):
                raise _StageFailure("owned_launch_nonce_generation_failed", "plan")
        except _StageFailure:
            raise
        except Exception as exc:
            raise _record_failure(exc, "owned_launch_nonce_generation_failed", "plan") from exc
        try:
            candidate_plan = plan_builder.build_for_test(
                order=order,
                receipt=receipt,
                reservation=reservation,
                identities=identities,
                launch_nonce=owned_launch_nonce,
            )
            # Retain a typed plan before validating its bindings so its nonce is
            # still cleared when a fail-closed validation rejects the plan.
            if type(candidate_plan) is SuspendedAdminRootPlan:
                plan = candidate_plan
            plan = _validate_plan(
                candidate_plan,
                order=order,
                receipt=receipt,
                reservation=reservation,
                identities=identities,
                expected_launch_nonce=owned_launch_nonce,
            )
        except Exception as exc:
            raise _record_failure(exc, "suspended_admin_root_plan_failed", "plan") from exc
        plan_sha = _sha256(canonical_json_bytes(_plan_payload(plan)))
        completed.append(ORDERED_STAGES[5])

        counts.job_capability_query += 1
        counts.job_explicit_snapshot_query += 1
        counts.job_implicit_snapshot_query += 1
        try:
            job = _validate_job_evidence(job_adapter.query_for_test(plan), plan=plan)
            _validate_plan(
                plan,
                order=order,
                receipt=receipt,
                reservation=reservation,
                identities=identities,
                expected_launch_nonce=owned_launch_nonce,
            )
            if _sha256(canonical_json_bytes(_plan_payload(plan))) != plan_sha:
                raise _StageFailure("suspended_admin_root_plan_changed", "job")
        except Exception as exc:
            raise _record_failure(exc, "query_only_job_validation_failed", "job") from exc
        job_sha = _sha256(canonical_json_bytes(asdict(job)))
        completed.append(ORDERED_STAGES[6])

        _clear_nonce(plan)
        counts.nonce_clear += 1
        completed.append(ORDERED_STAGES[7])

        counts.identity_readback += 1
        try:
            readback = _validate_bound_identities(
                identity_binder.read_same_handles_for_test(
                    identities.handle_ids,
                    identities.directory_handle_ids,
                ),
                order=order,
                stage="identity_readback",
            )
        except Exception as exc:
            raise _record_failure(
                exc, "source_tool_identity_readback_failed", "identity_readback"
            ) from exc
        if readback != identities:
            raise _StageFailure("source_tool_handle_snapshot_changed", "identity_readback")
        completed.append(ORDERED_STAGES[8])

        counts.reservation_readback += 1
        try:
            reservation_readback = _validate_reservation(
                reservation_adapter.read_same_handles_for_test(
                    reservation.handle,
                    reservation.directory_handle,
                ),
                collision_key=collision_key,
                record_raw=reservation_record,
                order=order,
            )
        except Exception as exc:
            raise _record_failure(
                exc,
                "cross_process_reservation_readback_failed",
                "reservation_readback",
            ) from exc
        if reservation_readback != reservation:
            raise _StageFailure(
                "cross_process_reservation_identity_changed",
                "reservation_readback",
            )

        payload = {
            "schema": INTERNAL_RESULT_SCHEMA,
            "status": "internal_non_authoritative",
            "decision": "reviewer_pending",
            "credit": "zero_credit",
            "work_order_sha256": order.raw_sha256,
            "receipt_sha256": receipt.receipt_sha256,
            "collision_key_sha256": collision_key,
            "identity_snapshot_sha256": identities.snapshot_sha256,
            "normalized_invocation_sha256": order.normalized_invocation.canonical_sha256,
            "plan_sha256": plan_sha,
            "job_snapshot_sha256": job_sha,
            "reservation_directory_identity": asdict(order.reservation_directory_identity),
            "evidence_directory_identity": asdict(order.evidence_directory_identity),
            "completed_prepublication_stages": list(completed),
            "launch_nonce_present": False,
            "process_creation_calls": 0,
            "process_resume_calls": 0,
            "automatic_retry_count": 0,
            "force_termination_count": 0,
            "success_marker_count": 0,
            "completion_marker_count": 0,
            "path_fallback_count": 0,
            "production_go": False,
        }
        evidence_raw = canonical_json_bytes(payload)
        counts.evidence_publication += 1
        try:
            publication = _validate_publication(
                evidence_writer.publish_no_replace_for_test(
                    root_path=CANONICAL_EVIDENCE_ROOT,
                    final_leaf=_publication_leaf(collision_key),
                    raw=evidence_raw,
                ),
                raw=evidence_raw,
                collision_key=collision_key,
                order=order,
            )
        except Exception as exc:
            raise _record_failure(exc, "atomic_no_replace_evidence_failed", "evidence") from exc

        counts.reservation_readback += 1
        try:
            final_reservation_readback = _validate_reservation(
                reservation_adapter.read_same_handles_for_test(
                    reservation.handle,
                    reservation.directory_handle,
                ),
                collision_key=collision_key,
                record_raw=reservation_record,
                order=order,
            )
        except Exception as exc:
            raise _record_failure(
                exc,
                "cross_process_reservation_final_readback_failed",
                "reservation_readback",
            ) from exc
        if final_reservation_readback != reservation:
            raise _StageFailure(
                "cross_process_reservation_identity_changed",
                "reservation_readback",
            )
        completed.append(ORDERED_STAGES[9])
    except _StageFailure as exc:
        pending = exc
    finally:
        nonce_buffers: list[bytearray] = []
        if owned_launch_nonce is not None:
            nonce_buffers.append(owned_launch_nonce)
        if (
            plan is not None
            and type(plan.launch_nonce) is bytearray
            and all(plan.launch_nonce is not item for item in nonce_buffers)
        ):
            nonce_buffers.append(plan.launch_nonce)
        if plan is not None and type(plan.launch_nonce) is not bytearray:
            pending = _StageFailure("launch_nonce_reference_changed", "nonce_clear")
        for nonce_buffer in nonce_buffers:
            if not any(nonce_buffer):
                continue
            try:
                _clear_nonce_buffer(nonce_buffer)
                counts.nonce_clear += 1
            except _StageFailure as exc:
                pending = exc
        if identities is not None:
            counts.identity_close += 1
            try:
                identity_binder.close_for_test(
                    identities.handle_ids,
                    identities.directory_handle_ids,
                )
                identities_closed = True
            except Exception:
                pending = _StageFailure("identity_handle_close_ambiguous", "identity_close")
        if reservation is not None:
            counts.reservation_close += 1
            try:
                reservation_adapter.close_for_test(
                    reservation.handle,
                    reservation.directory_handle,
                )
                reservation_closed = True
            except Exception:
                pending = _StageFailure("reservation_handle_close_ambiguous", "reservation_close")

    if pending is not None:
        raise R7S7AdmissionError(
            pending.code,
            stage=pending.stage,
            counts=counts.snapshot(),
            completed_stages=completed,
        )

    assert order is not None
    assert receipt is not None
    assert collision_key is not None
    assert identities is not None
    assert plan is not None and not any(plan.launch_nonce)
    assert plan_sha is not None
    assert job_sha is not None
    assert publication is not None
    final_counts = counts.snapshot()
    if any(
        getattr(final_counts, field) != 0
        for field in (
            "process_creation",
            "process_resume",
            "automatic_retry",
            "force_termination",
            "success_marker",
            "completion_marker",
            "path_fallback",
            "docker",
            "wsl",
            "etw",
            "r8",
        )
    ):
        raise R7S7AdmissionError(
            "forbidden_call_count_nonzero",
            stage="terminal",
            counts=final_counts,
            completed_stages=completed,
        )
    return InternalReviewerCandidateResult(
        work_order_sha256=order.raw_sha256,
        receipt_sha256=receipt.receipt_sha256,
        collision_key_sha256=collision_key,
        identity_snapshot_sha256=identities.snapshot_sha256,
        normalized_invocation_sha256=order.normalized_invocation.canonical_sha256,
        plan_sha256=plan_sha,
        job_snapshot_sha256=job_sha,
        publication=publication,
        completed_stages=tuple(completed),
        counts=final_counts,
        reservation_closed=reservation_closed,
        identity_handles_closed=identities_closed,
    )


def admit_reviewer_candidate(
    work_order_raw: bytes,
    approval_receipt_raw: bytes | None,
) -> InternalReviewerCandidateResult:
    """Public non-injectable gate, intentionally closed before process creation."""

    del work_order_raw, approval_receipt_raw
    counts = AdmissionCallCounts()
    if not PRODUCTION_EXTERNAL_AUTHORITY_CONFIGURED:
        raise R7S7AdmissionError(
            "production_external_authority_unconfigured",
            stage="root_gate",
            counts=counts,
        )
    if not PRODUCTION_CROSS_PROCESS_RESERVATION_CONFIGURED:
        raise R7S7AdmissionError(
            "production_cross_process_reservation_unconfigured",
            stage="root_gate",
            counts=counts,
        )
    if not PRODUCTION_WORM_EVIDENCE_ADAPTER_CONFIGURED:
        raise R7S7AdmissionError(
            "production_worm_evidence_adapter_unconfigured",
            stage="root_gate",
            counts=counts,
        )
    raise R7S7AdmissionError(
        "production_reviewer_candidate_wiring_not_implemented",
        stage="root_gate",
        counts=counts,
    )


def admission_contract() -> dict[str, Any]:
    return {
        "schema": "evm.s8-v4.x1.phase-b2.r7s7.reviewer-candidate-contract.v1",
        "ordered_stages": list(ORDERED_STAGES),
        "public_dependency_injection_allowed": False,
        "public_path_or_command_argument_allowed": False,
        "production_external_authority_configured": PRODUCTION_EXTERNAL_AUTHORITY_CONFIGURED,
        "production_cross_process_reservation_configured": (
            PRODUCTION_CROSS_PROCESS_RESERVATION_CONFIGURED
        ),
        "production_worm_evidence_adapter_configured": (
            PRODUCTION_WORM_EVIDENCE_ADAPTER_CONFIGURED
        ),
        "production_process_creation_enabled": PRODUCTION_PROCESS_CREATION_ENABLED,
        "production_entry_enabled": PRODUCTION_ENTRY_ENABLED,
        "test_result_schema": INTERNAL_RESULT_SCHEMA,
        "test_result_authority": "internal_non_authoritative",
        "query_only_job_required": True,
        "explicit_implicit_job_snapshot_equality_required": True,
        "source_tool_same_handle_revalidation_required": True,
        "source_tool_parent_directory_identity_required": True,
        "source_tool_parent_directory_handles_retained_and_revalidated": True,
        "normalized_invocation_oob_pinned_and_handle_bound": True,
        "suspended_plan_invocation_object_and_sha_exact_match_required": True,
        "reservation_and_evidence_directory_identity_oob_pinned": True,
        "reservation_same_handle_readback_before_and_after_evidence": True,
        "file_identity_stable_across_no_replace_rename_required": True,
        "directory_identity_stable_across_no_replace_rename_required": True,
        "orchestrator_owned_launch_nonce_required": True,
        "launch_nonce_zeroed_before_evidence": True,
        "atomic_no_replace_worm_evidence_required": True,
        "automatic_retry_allowed": False,
        "force_termination_allowed": False,
        "success_or_completion_marker_allowed": False,
        "path_fallback_allowed": False,
        "same_token_hostile_admin_protected": False,
        "production_worm_required_for_replay_marker_protection": True,
        "live_process_calls_implemented": False,
        "docker_wsl_etw_r8_calls_implemented": False,
        "decision": "NO-GO",
        "status": "reviewer_pending",
        "production_go": False,
    }


__all__ = [
    "AdmissionCallCounts",
    "AtomicEvidencePublication",
    "BoundIdentityAcquisition",
    "CANONICAL_EVIDENCE_ROOT",
    "CANONICAL_RESERVATION_ROOT",
    "DirectoryIdentity",
    "HandleIdentity",
    "INTERNAL_APPROVAL_SCHEMA",
    "INTERNAL_JOB_SCHEMA",
    "INTERNAL_PLAN_SCHEMA",
    "INTERNAL_PUBLICATION_SCHEMA",
    "INTERNAL_RESERVATION_SCHEMA",
    "INTERNAL_RESULT_SCHEMA",
    "INVOCATION_SCHEMA",
    "InternalReviewerCandidateResult",
    "JobSnapshot",
    "NormalizedInvocation",
    "ORDERED_STAGES",
    "PRODUCTION_ENTRY_ENABLED",
    "PRODUCTION_PROCESS_CREATION_ENABLED",
    "QueryOnlyJobEvidence",
    "R7S7AdmissionError",
    "ReservationAcquisition",
    "RenameIdentityEvidence",
    "ReviewerExpectation",
    "SuspendedAdminRootPlan",
    "VerifiedApprovalReceipt",
    "WORK_ORDER_SCHEMA",
    "admission_contract",
    "admit_reviewer_candidate",
    "canonical_json_bytes",
]
