"""Canonical internal work-order gate for the r7s7 Windows qualification.

This module is deliberately non-authoritative.  It binds a work-order digest
supplied independently by a trusted outer to exact source/tool identities and
an exact Job-root invocation.  It neither verifies an external approval nor
creates a process.  A verified token is therefore only an admission prerequisite
for the non-credit qualification and can never establish production GO.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from evm.scale_validation import phase_b2_r7s7_admission as admission


WORK_ORDER_SCHEMA = (
    "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification."
    "internal-non-authoritative-work-order.v1"
)
INVOCATION_SCHEMA = (
    "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification.normalized-invocation.v1"
)
AUTHORITY_SCOPE = "trusted_outer_internal_non_authoritative"
QUALIFICATION_MODE = "windows_job_non_credit"
SOURCE_CLOSURE_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification.source-closure.v1"
PRESERVED_UNTRACKED_SCHEMA = (
    "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification.preserved-untracked-inventory.v1"
)
PRESERVED_UNTRACKED_SCOPE = "all_regular_files_not_in_index_including_git_ignored"
CANONICAL_OUTPUT_ROOT = (
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation"
    r"\private\s8-v4\x1-clock-phase-b2\pre-r8-r7s7-windows-qualification"
)
CANONICAL_PYCACHE_ROOT = (
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation"
    r"\private\s8-v4\x1-clock-phase-b2\pre-r8-r7s7-windows-qualification-pycache"
)
CANONICAL_WORK_ORDER_ROOT = (
    r"F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\scale_validation"
    r"\private\s8-v4\x1-clock-phase-b2\pre-r8-r7s7-windows-qualification-work-orders"
)
FILE_BINDING_ROLES = (
    "interpreter",
    "fixture",
    "qualifier",
    "runner_source",
    "powershell",
    "codex",
    "command_processor",
    "trusted_outer",
    "work_order_gate",
    "admission_source",
    "r7s3_handle_io_source",
    "r7s4_handle_io_source",
    "evm_package_init_source",
    "scale_validation_package_init_source",
    "preparer",
)
SOURCE_CLOSURE_ROLES = (
    "trusted_outer",
    "fixture",
    "qualifier",
    "runner_source",
    "work_order_gate",
    "admission_source",
    "r7s3_handle_io_source",
    "r7s4_handle_io_source",
    "evm_package_init_source",
    "scale_validation_package_init_source",
    "preparer",
)
CONFIG_FIELDS = (
    "run_uuid",
    "attempt_uuid",
    "interpreter",
    "interpreter_sha256",
    "fixture",
    "fixture_sha256",
    "qualifier",
    "qualifier_sha256",
    "runner_source",
    "runner_source_sha256",
    "powershell",
    "powershell_sha256",
    "codex",
    "codex_sha256",
    "command_processor",
    "command_processor_sha256",
    "pycache_prefix",
    "output_root",
)
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SID = re.compile(r"S-\d+(?:-\d+)+\Z")
_VERIFICATION_CAPABILITY = object()


class QualificationWorkOrderError(RuntimeError):
    """Fail-closed pre-process work-order rejection."""

    status = "reviewer_pending"
    decision = "NO-GO"
    credit = "zero_credit"
    production_go = False
    external_authority_verified = False
    go_evidence_eligible = False

    def __init__(self, code: str, *, stage: str = "internal_work_order") -> None:
        super().__init__(code)
        self.code = code
        self.stage = stage
        self.call_counts = {
            "lineage_probe": 0,
            "reservation": 0,
            "process_creation": 0,
            "runner_invocation": 0,
            "automatic_retry": 0,
            "force_termination": 0,
            "success_marker": 0,
            "completion_marker": 0,
        }


@dataclass(frozen=True, slots=True)
class QualificationWorkOrderExpectation:
    work_order_sha256: str
    global_run_id: str
    run_uuid: str
    attempt_uuid: str
    commit: str
    tree: str


@dataclass(frozen=True, slots=True)
class InternalDirectoryIdentity:
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
class InternalHandleIdentity:
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
    parent_directory_identity: InternalDirectoryIdentity


@dataclass(frozen=True, slots=True)
class QualificationInvocation:
    schema: str
    working_directory_identity: InternalDirectoryIdentity
    argv: tuple[str, ...]
    absolute_path_argument_indexes: tuple[int, ...]
    pycache_prefix_argument_index: int
    canonical_sha256: str


@dataclass(frozen=True, slots=True)
class SourceClosureEntry:
    role: str
    final_path: str
    sha256: str
    bytes: int
    volume_serial_number: int
    file_id_hex: str
    security_descriptor_sha256: str
    creation_time_ns: int


@dataclass(frozen=True, slots=True)
class SourceClosureInventory:
    schema: str
    roles: tuple[str, ...]
    files: tuple[SourceClosureEntry, ...]
    count: int
    total_bytes: int
    inventory_sha256: str


@dataclass(frozen=True, slots=True)
class PreservedUntrackedEntry:
    relative_path: str
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class PreservedUntrackedInventory:
    schema: str
    scope: str
    files: tuple[PreservedUntrackedEntry, ...]
    count: int
    total_bytes: int
    import_active_count: int
    inventory_sha256: str


@dataclass(frozen=True, slots=True)
class InternalQualificationWorkOrder:
    global_run_id: str
    run_uuid: str
    attempt_uuid: str
    commit: str
    tree: str
    file_bindings: tuple[tuple[str, InternalHandleIdentity], ...]
    source_closure: SourceClosureInventory
    preserved_untracked_inventory: PreservedUntrackedInventory
    normalized_invocation: QualificationInvocation
    pycache_prefix: str
    pycache_parent_identity: InternalDirectoryIdentity
    pycache_prefix_initially_absent: bool
    pycache_prefix_postcondition_absent: bool
    output_root: str
    output_parent_identity: InternalDirectoryIdentity
    work_order_path: str
    work_order_parent_identity: InternalDirectoryIdentity
    same_token_hostile_admin_protected: bool
    toolchain_runtime_closure_state: str
    reviewer_blockers: tuple[str, ...]
    raw_sha256: str


@dataclass(frozen=True, slots=True)
class VerifiedInternalQualificationWorkOrder:
    order: InternalQualificationWorkOrder
    verification_scope: str = "internal_non_authoritative_digest_and_identity_contract"
    external_authority_verified: bool = False
    production_go: bool = False
    go_evidence_eligible: bool = False
    _capability: object = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": f"{WORK_ORDER_SCHEMA}.verified-token.v1",
            "status": "internal_non_authoritative",
            "decision": "NO-GO",
            "credit": "zero_credit",
            "verification_scope": self.verification_scope,
            "work_order_sha256": self.order.raw_sha256,
            "global_run_id": self.order.global_run_id,
            "run_uuid": self.order.run_uuid,
            "attempt_uuid": self.order.attempt_uuid,
            "commit": self.order.commit,
            "tree": self.order.tree,
            "normalized_invocation_sha256": (self.order.normalized_invocation.canonical_sha256),
            "source_closure_inventory_sha256": (self.order.source_closure.inventory_sha256),
            "source_closure_count": self.order.source_closure.count,
            "preserved_untracked_inventory_sha256": (
                self.order.preserved_untracked_inventory.inventory_sha256
            ),
            "preserved_untracked_count": self.order.preserved_untracked_inventory.count,
            "pycache_prefix": self.order.pycache_prefix,
            "pycache_prefix_initially_absent": (self.order.pycache_prefix_initially_absent),
            "pycache_prefix_postcondition_absent": (self.order.pycache_prefix_postcondition_absent),
            "output_root": self.order.output_root,
            "work_order_path": self.order.work_order_path,
            "same_token_hostile_admin_protected": (self.order.same_token_hostile_admin_protected),
            "toolchain_runtime_closure_state": (self.order.toolchain_runtime_closure_state),
            "reviewer_blockers": list(self.order.reviewer_blockers),
            "external_authority_verified": False,
            "production_go": False,
            "go_evidence_eligible": False,
            "process_creation_count": 0,
        }


def canonical_json_bytes(value: Any) -> bytes:
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
        raise QualificationWorkOrderError(f"{label}_uuid4_required")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise QualificationWorkOrderError(f"{label}_uuid4_required") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise QualificationWorkOrderError(f"{label}_uuid4_required")
    return value


def _hex(value: object, pattern: re.Pattern[str], label: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise QualificationWorkOrderError(f"{label}_invalid")
    return value


def _canonical_mapping(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or b"\r" in raw or not raw.endswith(b"\n"):
        raise QualificationWorkOrderError("work_order_canonical_bytes_required")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise QualificationWorkOrderError(f"work_order_duplicate_key:{key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationWorkOrderError("work_order_invalid_json") from exc
    if type(value) is not dict or canonical_json_bytes(value) != raw:
        raise QualificationWorkOrderError("work_order_not_canonical_json")
    return value


def _validate_expectation(value: object) -> QualificationWorkOrderExpectation:
    if type(value) is not QualificationWorkOrderExpectation:
        raise QualificationWorkOrderError("typed_work_order_expectation_required")
    assert isinstance(value, QualificationWorkOrderExpectation)
    _hex(value.work_order_sha256, _HEX64, "expected_work_order_sha256")
    _uuid4(value.global_run_id, "expected_global_run_id")
    _uuid4(value.run_uuid, "expected_run_uuid")
    _uuid4(value.attempt_uuid, "expected_attempt_uuid")
    _hex(value.commit, _HEX40, "expected_commit")
    _hex(value.tree, _HEX40, "expected_tree")
    return value


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise QualificationWorkOrderError(f"{label}_positive_integer_required")
    return value


def _directory_from_json(
    value: object,
    *,
    expected_role: str,
    expected_path: str | None = None,
) -> InternalDirectoryIdentity:
    if type(value) is not dict or set(value) != set(InternalDirectoryIdentity.__dataclass_fields__):
        raise QualificationWorkOrderError(f"{expected_role}_directory_keys_not_exact")
    try:
        identity = InternalDirectoryIdentity(**value)
    except TypeError as exc:
        raise QualificationWorkOrderError(f"{expected_role}_directory_identity_invalid") from exc
    if identity.role != expected_role:
        raise QualificationWorkOrderError(f"{expected_role}_directory_role_mismatch")
    observed_path = admission._normal_windows_path(
        identity.final_path, f"{expected_role}_directory_path", "work_order"
    )
    if expected_path is not None and observed_path != admission._normal_windows_path(
        expected_path, f"{expected_role}_expected_directory_path", "work_order"
    ):
        raise QualificationWorkOrderError(f"{expected_role}_directory_path_mismatch")
    _positive_int(identity.volume_serial_number, f"{expected_role}_directory_volume")
    _hex(identity.file_id_hex, _HEX32, f"{expected_role}_directory_file_id")
    if type(identity.owner_sid) is not str or _SID.fullmatch(identity.owner_sid) is None:
        raise QualificationWorkOrderError(f"{expected_role}_directory_owner_sid_invalid")
    _hex(
        identity.security_descriptor_sha256,
        _HEX64,
        f"{expected_role}_directory_security_descriptor_sha256",
    )
    if identity.dacl_present is not True or type(identity.dacl_protected) is not bool:
        raise QualificationWorkOrderError(f"{expected_role}_directory_dacl_invalid")
    if type(identity.link_count) is not int or identity.link_count < 1:
        raise QualificationWorkOrderError(f"{expected_role}_directory_link_count_invalid")
    if type(identity.reparse_tag) is not int or identity.reparse_tag != 0:
        raise QualificationWorkOrderError(f"{expected_role}_directory_reparse_present")
    if type(identity.file_type) is not int or identity.file_type != 1:
        raise QualificationWorkOrderError(f"{expected_role}_directory_not_disk")
    if identity.is_directory is not True:
        raise QualificationWorkOrderError(f"{expected_role}_not_directory")
    return identity


def _handle_from_json(value: object, *, expected_role: str) -> InternalHandleIdentity:
    if type(value) is not dict or set(value) != set(InternalHandleIdentity.__dataclass_fields__):
        raise QualificationWorkOrderError(f"{expected_role}_identity_keys_not_exact")
    fields = dict(value)
    fields["parent_directory_identity"] = _directory_from_json(
        fields["parent_directory_identity"],
        expected_role=f"{expected_role}:parent",
        expected_path=ntpath.dirname(fields["final_path"]),
    )
    try:
        identity = InternalHandleIdentity(**fields)
    except TypeError as exc:
        raise QualificationWorkOrderError(f"{expected_role}_identity_invalid") from exc
    if identity.role != expected_role:
        raise QualificationWorkOrderError(f"{expected_role}_role_mismatch")
    admission._normal_windows_path(identity.final_path, f"{expected_role}_path", "work_order")
    _positive_int(identity.volume_serial_number, f"{expected_role}_volume")
    _hex(identity.file_id_hex, _HEX32, f"{expected_role}_file_id")
    _hex(identity.sha256, _HEX64, f"{expected_role}_sha256")
    if type(identity.bytes) is not int or identity.bytes < 0:
        raise QualificationWorkOrderError(f"{expected_role}_bytes_invalid")
    if type(identity.owner_sid) is not str or _SID.fullmatch(identity.owner_sid) is None:
        raise QualificationWorkOrderError(f"{expected_role}_owner_sid_invalid")
    _hex(
        identity.security_descriptor_sha256,
        _HEX64,
        f"{expected_role}_security_descriptor_sha256",
    )
    if identity.dacl_present is not True or type(identity.dacl_protected) is not bool:
        raise QualificationWorkOrderError(f"{expected_role}_dacl_invalid")
    if type(identity.link_count) is not int or identity.link_count != 1:
        raise QualificationWorkOrderError(f"{expected_role}_link_count_invalid")
    if type(identity.reparse_tag) is not int or identity.reparse_tag != 0:
        raise QualificationWorkOrderError(f"{expected_role}_reparse_present")
    if type(identity.file_type) is not int or identity.file_type != 1:
        raise QualificationWorkOrderError(f"{expected_role}_not_disk_file")
    _positive_int(identity.creation_time_ns, f"{expected_role}_creation_time_ns")
    if identity.parent_directory_identity.volume_serial_number != identity.volume_serial_number:
        raise QualificationWorkOrderError(f"{expected_role}_parent_volume_mismatch")
    return identity


def _parse_file_bindings(value: object) -> dict[str, InternalHandleIdentity]:
    if type(value) is not dict or set(value) != set(FILE_BINDING_ROLES):
        raise QualificationWorkOrderError("file_binding_roles_not_exact")
    result: dict[str, InternalHandleIdentity] = {}
    try:
        for role in FILE_BINDING_ROLES:
            result[role] = _handle_from_json(value[role], expected_role=f"qualification:{role}")
    except Exception as exc:
        code = getattr(exc, "code", "file_binding_identity_invalid")
        raise QualificationWorkOrderError(str(code)) from exc
    normalized_paths = [
        admission._normal_windows_path(item.final_path, f"file_binding:{role}", "work_order")
        for role, item in result.items()
    ]
    if len(set(normalized_paths)) != len(normalized_paths):
        raise QualificationWorkOrderError("file_binding_path_collision")
    file_identity_keys = {(item.volume_serial_number, item.file_id_hex) for item in result.values()}
    if len(file_identity_keys) != len(result):
        raise QualificationWorkOrderError("file_binding_identity_collision")
    expected_basenames = {
        "fixture": "pre_r8_r7s7_windows_fixture.py",
        "qualifier": "qualify_pre_r8_r7s7_windows.py",
        "runner_source": "phase_b2_r7s3_process.py",
        "trusted_outer": "invoke_pre_r8_r7s7_windows_qualification.ps1",
        "work_order_gate": "phase_b2_r7s7_qualification_work_order.py",
        "admission_source": "phase_b2_r7s7_admission.py",
        "r7s3_handle_io_source": "phase_b2_r7s3_handle_io.py",
        "r7s4_handle_io_source": "phase_b2_r7s4_handle_io.py",
        "evm_package_init_source": "__init__.py",
        "scale_validation_package_init_source": "__init__.py",
        "preparer": "prepare_pre_r8_r7s7_windows_qualification.py",
    }
    for role, basename in expected_basenames.items():
        if ntpath.basename(result[role].final_path).lower() != basename.lower():
            raise QualificationWorkOrderError(f"{role}_basename_mismatch")
    if ntpath.basename(result["interpreter"].final_path).lower() not in {
        "python.exe",
        "python3.exe",
    }:
        raise QualificationWorkOrderError("interpreter_basename_mismatch")
    if ntpath.basename(result["powershell"].final_path).lower() not in {
        "powershell.exe",
        "pwsh.exe",
    }:
        raise QualificationWorkOrderError("powershell_basename_mismatch")
    if ntpath.basename(result["codex"].final_path).lower() != "codex.exe":
        raise QualificationWorkOrderError("codex_basename_mismatch")
    if ntpath.basename(result["command_processor"].final_path).lower() != "cmd.exe":
        raise QualificationWorkOrderError("command_processor_basename_mismatch")
    return result


def _source_closure_entry(role: str, binding: InternalHandleIdentity) -> SourceClosureEntry:
    return SourceClosureEntry(
        role=role,
        final_path=binding.final_path,
        sha256=binding.sha256,
        bytes=binding.bytes,
        volume_serial_number=binding.volume_serial_number,
        file_id_hex=binding.file_id_hex,
        security_descriptor_sha256=binding.security_descriptor_sha256,
        creation_time_ns=binding.creation_time_ns,
    )


def _source_closure_payload(value: SourceClosureInventory) -> dict[str, Any]:
    return {
        "schema": value.schema,
        "roles": list(value.roles),
        "files": [asdict(item) for item in value.files],
        "count": value.count,
        "total_bytes": value.total_bytes,
    }


def _parse_source_closure(
    value: object,
    *,
    bindings: Mapping[str, InternalHandleIdentity],
) -> SourceClosureInventory:
    if type(value) is not dict or set(value) != set(SourceClosureInventory.__dataclass_fields__):
        raise QualificationWorkOrderError("source_closure_keys_not_exact")
    if type(value["roles"]) is not list or type(value["files"]) is not list:
        raise QualificationWorkOrderError("source_closure_lists_required")
    expected_files = tuple(
        _source_closure_entry(role, bindings[role]) for role in SOURCE_CLOSURE_ROLES
    )
    closure = SourceClosureInventory(
        schema=value["schema"],
        roles=tuple(value["roles"]),
        files=expected_files,
        count=value["count"],
        total_bytes=value["total_bytes"],
        inventory_sha256=value["inventory_sha256"],
    )
    if closure.schema != SOURCE_CLOSURE_SCHEMA:
        raise QualificationWorkOrderError("source_closure_schema_mismatch")
    if closure.roles != SOURCE_CLOSURE_ROLES:
        raise QualificationWorkOrderError("source_closure_roles_not_exact")
    if canonical_json_bytes(value["files"]) != canonical_json_bytes(
        [asdict(item) for item in expected_files]
    ):
        raise QualificationWorkOrderError("source_closure_files_not_exact")
    if type(closure.count) is not int or closure.count != len(expected_files):
        raise QualificationWorkOrderError("source_closure_count_mismatch")
    expected_total = sum(item.bytes for item in expected_files)
    if type(closure.total_bytes) is not int or closure.total_bytes != expected_total:
        raise QualificationWorkOrderError("source_closure_total_bytes_mismatch")
    _hex(closure.inventory_sha256, _HEX64, "source_closure_inventory_sha256")
    expected_sha256 = _sha256(canonical_json_bytes(_source_closure_payload(closure)))
    if closure.inventory_sha256 != expected_sha256:
        raise QualificationWorkOrderError("source_closure_inventory_sha256_mismatch")
    return closure


def _preserved_untracked_payload(value: PreservedUntrackedInventory) -> dict[str, Any]:
    return {
        "schema": value.schema,
        "scope": value.scope,
        "files": [asdict(item) for item in value.files],
        "count": value.count,
        "total_bytes": value.total_bytes,
        "import_active_count": value.import_active_count,
    }


def _parse_preserved_untracked_inventory(value: object) -> PreservedUntrackedInventory:
    if type(value) is not dict or set(value) != set(
        PreservedUntrackedInventory.__dataclass_fields__
    ):
        raise QualificationWorkOrderError("preserved_untracked_inventory_keys_not_exact")
    if type(value["files"]) is not list or len(value["files"]) > 100_000:
        raise QualificationWorkOrderError("preserved_untracked_files_list_invalid")
    entries: list[PreservedUntrackedEntry] = []
    prior_path: str | None = None
    normalized_paths: set[str] = set()
    for item in value["files"]:
        if type(item) is not dict or set(item) != set(PreservedUntrackedEntry.__dataclass_fields__):
            raise QualificationWorkOrderError("preserved_untracked_entry_keys_not_exact")
        relative_path = item["relative_path"]
        if (
            type(relative_path) is not str
            or not relative_path
            or "\\" in relative_path
            or relative_path.startswith("/")
            or ntpath.isabs(relative_path)
            or any(ord(character) < 32 for character in relative_path)
            or any(part in {"", ".", ".."} for part in relative_path.split("/"))
            or (prior_path is not None and relative_path <= prior_path)
            or relative_path.casefold() in normalized_paths
        ):
            raise QualificationWorkOrderError("preserved_untracked_path_not_canonical_sorted")
        sha256 = _hex(item["sha256"], _HEX64, "preserved_untracked_sha256")
        if type(item["bytes"]) is not int or item["bytes"] < 0:
            raise QualificationWorkOrderError("preserved_untracked_bytes_invalid")
        entries.append(
            PreservedUntrackedEntry(
                relative_path=relative_path,
                sha256=sha256,
                bytes=item["bytes"],
            )
        )
        normalized_paths.add(relative_path.casefold())
        prior_path = relative_path
    inventory = PreservedUntrackedInventory(
        schema=value["schema"],
        scope=value["scope"],
        files=tuple(entries),
        count=value["count"],
        total_bytes=value["total_bytes"],
        import_active_count=value["import_active_count"],
        inventory_sha256=value["inventory_sha256"],
    )
    if inventory.schema != PRESERVED_UNTRACKED_SCHEMA:
        raise QualificationWorkOrderError("preserved_untracked_schema_mismatch")
    if inventory.scope != PRESERVED_UNTRACKED_SCOPE:
        raise QualificationWorkOrderError("preserved_untracked_scope_mismatch")
    if type(inventory.count) is not int or inventory.count != len(entries):
        raise QualificationWorkOrderError("preserved_untracked_count_mismatch")
    if type(inventory.total_bytes) is not int or inventory.total_bytes != sum(
        item.bytes for item in entries
    ):
        raise QualificationWorkOrderError("preserved_untracked_total_bytes_mismatch")
    if type(inventory.import_active_count) is not int or inventory.import_active_count != 0:
        raise QualificationWorkOrderError("preserved_untracked_import_active_files_present")
    _hex(inventory.inventory_sha256, _HEX64, "preserved_untracked_inventory_sha256")
    expected_digest = _sha256(canonical_json_bytes(_preserved_untracked_payload(inventory)))
    if inventory.inventory_sha256 != expected_digest:
        raise QualificationWorkOrderError("preserved_untracked_inventory_sha256_mismatch")
    return inventory


def _invocation_payload(value: QualificationInvocation) -> dict[str, Any]:
    return {
        "schema": value.schema,
        "working_directory_identity": asdict(value.working_directory_identity),
        "argv": list(value.argv),
        "absolute_path_argument_indexes": list(value.absolute_path_argument_indexes),
        "pycache_prefix_argument_index": value.pycache_prefix_argument_index,
    }


def _expected_argv(
    bindings: Mapping[str, InternalHandleIdentity],
    run_uuid: str,
    pycache_prefix: str,
) -> tuple[str, ...]:
    return (
        bindings["interpreter"].final_path,
        "-I",
        "-B",
        "-S",
        "-X",
        f"pycache_prefix={pycache_prefix}",
        bindings["fixture"].final_path,
        "--mode",
        "root",
        "--run-uuid",
        run_uuid,
        "--pycache-prefix",
        pycache_prefix,
        "--interpreter-sha256",
        bindings["interpreter"].sha256,
        "--fixture-sha256",
        bindings["fixture"].sha256,
        "--command-processor",
        bindings["command_processor"].final_path,
        "--command-processor-sha256",
        bindings["command_processor"].sha256,
    )


def _parse_invocation(
    value: object,
    *,
    bindings: Mapping[str, InternalHandleIdentity],
    run_uuid: str,
    pycache_prefix: str,
) -> QualificationInvocation:
    expected_keys = set(QualificationInvocation.__dataclass_fields__)
    if type(value) is not dict or set(value) != expected_keys:
        raise QualificationWorkOrderError("normalized_invocation_keys_not_exact")
    if type(value["argv"]) is not list or type(value["absolute_path_argument_indexes"]) is not list:
        raise QualificationWorkOrderError("normalized_invocation_lists_required")
    if any(type(index) is not int for index in value["absolute_path_argument_indexes"]):
        raise QualificationWorkOrderError("normalized_invocation_path_indexes_must_be_integers")
    if type(value["pycache_prefix_argument_index"]) is not int:
        raise QualificationWorkOrderError(
            "normalized_invocation_pycache_prefix_index_must_be_integer"
        )
    try:
        working_directory = _directory_from_json(
            value["working_directory_identity"],
            expected_role="qualification:working_directory",
        )
    except Exception as exc:
        raise QualificationWorkOrderError("working_directory_identity_invalid") from exc
    invocation = QualificationInvocation(
        schema=value["schema"],
        working_directory_identity=working_directory,
        argv=tuple(value["argv"]),
        absolute_path_argument_indexes=tuple(value["absolute_path_argument_indexes"]),
        pycache_prefix_argument_index=value["pycache_prefix_argument_index"],
        canonical_sha256=value["canonical_sha256"],
    )
    if invocation.schema != INVOCATION_SCHEMA:
        raise QualificationWorkOrderError("normalized_invocation_schema_mismatch")
    if invocation.argv != _expected_argv(bindings, run_uuid, pycache_prefix):
        raise QualificationWorkOrderError("normalized_invocation_argv_mismatch")
    if invocation.absolute_path_argument_indexes != (0, 6, 12, 18):
        raise QualificationWorkOrderError("normalized_invocation_path_indexes_mismatch")
    if invocation.pycache_prefix_argument_index != 5:
        raise QualificationWorkOrderError("normalized_invocation_pycache_prefix_index_mismatch")
    if invocation.argv[invocation.pycache_prefix_argument_index] != (
        f"pycache_prefix={pycache_prefix}"
    ):
        raise QualificationWorkOrderError("normalized_invocation_pycache_prefix_mismatch")
    for index in invocation.absolute_path_argument_indexes:
        argument = invocation.argv[index]
        try:
            admission._normal_windows_path(
                argument, f"normalized_invocation_path:{index}", "work_order"
            )
        except Exception as exc:
            raise QualificationWorkOrderError(
                f"normalized_invocation_absolute_path_invalid:{index}"
            ) from exc
        if argument != ntpath.normpath(argument):
            raise QualificationWorkOrderError(f"normalized_invocation_path_not_normalized:{index}")
    expected_sha256 = _sha256(canonical_json_bytes(_invocation_payload(invocation)))
    if invocation.canonical_sha256 != expected_sha256:
        raise QualificationWorkOrderError("normalized_invocation_sha256_mismatch")
    return invocation


def _is_within(child: str, parent: str) -> bool:
    child_normal = admission._normal_windows_path(child, "project_child", "work_order")
    parent_normal = admission._normal_windows_path(parent, "project_parent", "work_order")
    try:
        return ntpath.commonpath((child_normal, parent_normal)) == parent_normal
    except ValueError:
        return False


def _validate_directory_identity_graph(
    identities: tuple[InternalDirectoryIdentity, ...],
) -> None:
    by_path: dict[str, tuple[Any, ...]] = {}
    identity_to_path: dict[tuple[int, str], str] = {}
    for identity in identities:
        path = admission._normal_windows_path(
            identity.final_path, "directory_graph_path", "work_order"
        )
        fingerprint = (
            identity.volume_serial_number,
            identity.file_id_hex,
            identity.owner_sid,
            identity.security_descriptor_sha256,
            identity.dacl_present,
            identity.dacl_protected,
            identity.link_count,
            identity.reparse_tag,
            identity.file_type,
            identity.is_directory,
        )
        prior = by_path.setdefault(path, fingerprint)
        if prior != fingerprint:
            raise QualificationWorkOrderError("directory_same_path_identity_mismatch")
        identity_key = (identity.volume_serial_number, identity.file_id_hex)
        prior_path = identity_to_path.setdefault(identity_key, path)
        if prior_path != path:
            raise QualificationWorkOrderError("directory_identity_path_collision")


def _expected_pycache_prefix(run_uuid: str, attempt_uuid: str) -> str:
    return ntpath.join(CANONICAL_PYCACHE_ROOT, f"{run_uuid}-{attempt_uuid}")


def _expected_work_order_path(run_uuid: str, attempt_uuid: str) -> str:
    return ntpath.join(
        CANONICAL_WORK_ORDER_ROOT,
        f"windows-qualification-work-order-{run_uuid}-{attempt_uuid}.json",
    )


def verify_internal_qualification_work_order(
    raw: bytes,
    *,
    expected: QualificationWorkOrderExpectation,
) -> VerifiedInternalQualificationWorkOrder:
    """Verify an independently pinned internal work order without live calls."""

    expectation = _validate_expectation(expected)
    raw_sha256 = _sha256(raw) if type(raw) is bytes else ""
    if raw_sha256 != expectation.work_order_sha256:
        raise QualificationWorkOrderError("work_order_oob_digest_mismatch")
    value = _canonical_mapping(raw)
    required = {
        "schema",
        "status",
        "decision",
        "credit",
        "authority_scope",
        "authority_verified",
        "external_authority_verified",
        "production_go",
        "go_evidence_eligible",
        "global_run_id",
        "run_uuid",
        "attempt_uuid",
        "commit",
        "tree",
        "qualification_mode",
        "file_bindings",
        "source_closure",
        "preserved_untracked_inventory",
        "normalized_invocation",
        "pycache_prefix",
        "pycache_parent_identity",
        "pycache_prefix_initially_absent",
        "pycache_prefix_postcondition_absent",
        "output_root",
        "output_parent_identity",
        "work_order_path",
        "work_order_parent_identity",
        "same_token_hostile_admin_protected",
        "toolchain_runtime_closure_state",
        "reviewer_blockers",
    }
    if set(value) != required:
        raise QualificationWorkOrderError("work_order_keys_not_exact")
    if (
        value["schema"] != WORK_ORDER_SCHEMA
        or value["status"] != "internal_non_authoritative"
        or value["decision"] != "NO-GO"
        or value["credit"] != "zero_credit"
        or value["authority_scope"] != AUTHORITY_SCOPE
        or value["authority_verified"] is not False
        or value["external_authority_verified"] is not False
        or value["production_go"] is not False
        or value["go_evidence_eligible"] is not False
        or value["qualification_mode"] != QUALIFICATION_MODE
    ):
        raise QualificationWorkOrderError("work_order_non_authoritative_contract_mismatch")
    for name in ("global_run_id", "run_uuid", "attempt_uuid"):
        if _uuid4(value[name], name) != getattr(expectation, name):
            raise QualificationWorkOrderError(f"work_order_{name}_mismatch")
    for name in ("commit", "tree"):
        if _hex(value[name], _HEX40, name) != getattr(expectation, name):
            raise QualificationWorkOrderError(f"work_order_{name}_mismatch")
    bindings = _parse_file_bindings(value["file_bindings"])
    source_closure = _parse_source_closure(value["source_closure"], bindings=bindings)
    preserved_untracked_inventory = _parse_preserved_untracked_inventory(
        value["preserved_untracked_inventory"]
    )
    pycache_prefix = value["pycache_prefix"]
    expected_pycache_prefix = _expected_pycache_prefix(value["run_uuid"], value["attempt_uuid"])
    if (
        type(pycache_prefix) is not str
        or pycache_prefix != expected_pycache_prefix
        or admission._normal_windows_path(pycache_prefix, "pycache_prefix", "work_order")
        != admission._normal_windows_path(
            expected_pycache_prefix, "expected_pycache_prefix", "work_order"
        )
    ):
        raise QualificationWorkOrderError("pycache_prefix_not_canonical_run_unique")
    try:
        pycache_parent = _directory_from_json(
            value["pycache_parent_identity"],
            expected_role="qualification:pycache_parent",
            expected_path=CANONICAL_PYCACHE_ROOT,
        )
    except Exception as exc:
        raise QualificationWorkOrderError("pycache_parent_identity_invalid") from exc
    if (
        value["pycache_prefix_initially_absent"] is not True
        or value["pycache_prefix_postcondition_absent"] is not True
    ):
        raise QualificationWorkOrderError("pycache_prefix_absence_contract_required")
    invocation = _parse_invocation(
        value["normalized_invocation"],
        bindings=bindings,
        run_uuid=value["run_uuid"],
        pycache_prefix=pycache_prefix,
    )
    try:
        output_parent = _directory_from_json(
            value["output_parent_identity"],
            expected_role="qualification:output_parent",
            expected_path=CANONICAL_OUTPUT_ROOT,
        )
    except Exception as exc:
        raise QualificationWorkOrderError("output_parent_identity_invalid") from exc
    output_root = value["output_root"]
    if (
        type(output_root) is not str
        or admission._normal_windows_path(output_root, "output_root", "work_order")
        != admission._normal_windows_path(
            CANONICAL_OUTPUT_ROOT, "canonical_output_root", "work_order"
        )
        or output_parent.final_path.lower() != CANONICAL_OUTPUT_ROOT.lower()
    ):
        raise QualificationWorkOrderError("output_root_not_canonical")
    expected_work_order_path = _expected_work_order_path(value["run_uuid"], value["attempt_uuid"])
    if type(value["work_order_path"]) is not str or admission._normal_windows_path(
        value["work_order_path"], "work_order_path", "work_order"
    ) != admission._normal_windows_path(
        expected_work_order_path, "expected_work_order_path", "work_order"
    ):
        raise QualificationWorkOrderError("work_order_path_not_canonical_run_unique")
    try:
        work_order_parent = _directory_from_json(
            value["work_order_parent_identity"],
            expected_role="qualification:work_order_parent",
            expected_path=CANONICAL_WORK_ORDER_ROOT,
        )
    except Exception as exc:
        raise QualificationWorkOrderError("work_order_parent_identity_invalid") from exc
    _validate_directory_identity_graph(
        (
            invocation.working_directory_identity,
            pycache_parent,
            output_parent,
            work_order_parent,
            *(item.parent_directory_identity for item in bindings.values()),
        )
    )
    if value["same_token_hostile_admin_protected"] is not False:
        raise QualificationWorkOrderError(
            "internal_qualification_same_token_hostile_admin_protection_must_be_false"
        )
    if value["toolchain_runtime_closure_state"] != "unproven":
        raise QualificationWorkOrderError("toolchain_runtime_closure_state_not_unproven")
    expected_reviewer_blockers = [
        "external_oob_work_order_authority_required",
        "preparer_prelaunch_trusted_pin_unproven",
        "python_runtime_transitive_closure_unproven",
        "same_token_hostile_admin_tamper_resistance_unproven",
    ]
    if value["reviewer_blockers"] != expected_reviewer_blockers:
        raise QualificationWorkOrderError("reviewer_blockers_not_exact")
    project_root = invocation.working_directory_identity.final_path
    expected_project_paths = {
        "trusted_outer": r"scripts\dev\invoke_pre_r8_r7s7_windows_qualification.ps1",
        "fixture": r"scripts\dev\pre_r8_r7s7_windows_fixture.py",
        "qualifier": r"scripts\dev\qualify_pre_r8_r7s7_windows.py",
        "runner_source": r"src\evm\scale_validation\phase_b2_r7s3_process.py",
        "work_order_gate": (r"src\evm\scale_validation\phase_b2_r7s7_qualification_work_order.py"),
        "admission_source": (r"src\evm\scale_validation\phase_b2_r7s7_admission.py"),
        "r7s3_handle_io_source": (r"src\evm\scale_validation\phase_b2_r7s3_handle_io.py"),
        "r7s4_handle_io_source": (r"src\evm\scale_validation\phase_b2_r7s4_handle_io.py"),
        "evm_package_init_source": r"src\evm\__init__.py",
        "scale_validation_package_init_source": (r"src\evm\scale_validation\__init__.py"),
        "preparer": r"scripts\dev\prepare_pre_r8_r7s7_windows_qualification.py",
    }
    for role, relative_path in expected_project_paths.items():
        expected_path = ntpath.normpath(ntpath.join(project_root, relative_path))
        if not _is_within(
            bindings[role].final_path, project_root
        ) or admission._normal_windows_path(
            bindings[role].final_path, f"{role}_path", "work_order"
        ) != admission._normal_windows_path(expected_path, f"expected_{role}_path", "work_order"):
            raise QualificationWorkOrderError(f"{role}_project_path_mismatch")
    order = InternalQualificationWorkOrder(
        global_run_id=value["global_run_id"],
        run_uuid=value["run_uuid"],
        attempt_uuid=value["attempt_uuid"],
        commit=value["commit"],
        tree=value["tree"],
        file_bindings=tuple((role, bindings[role]) for role in FILE_BINDING_ROLES),
        source_closure=source_closure,
        preserved_untracked_inventory=preserved_untracked_inventory,
        normalized_invocation=invocation,
        pycache_prefix=pycache_prefix,
        pycache_parent_identity=pycache_parent,
        pycache_prefix_initially_absent=True,
        pycache_prefix_postcondition_absent=True,
        output_root=CANONICAL_OUTPUT_ROOT,
        output_parent_identity=output_parent,
        work_order_path=expected_work_order_path,
        work_order_parent_identity=work_order_parent,
        same_token_hostile_admin_protected=False,
        toolchain_runtime_closure_state="unproven",
        reviewer_blockers=tuple(expected_reviewer_blockers),
        raw_sha256=raw_sha256,
    )
    return VerifiedInternalQualificationWorkOrder(
        order=order,
        _capability=_VERIFICATION_CAPABILITY,
    )


def _require_token(value: object) -> VerifiedInternalQualificationWorkOrder:
    if (
        type(value) is not VerifiedInternalQualificationWorkOrder
        or value._capability is not _VERIFICATION_CAPABILITY
        or value.external_authority_verified is not False
        or value.production_go is not False
        or value.go_evidence_eligible is not False
    ):
        raise QualificationWorkOrderError("verified_internal_work_order_required")
    assert isinstance(value, VerifiedInternalQualificationWorkOrder)
    return value


def qualification_config_projection(
    token: VerifiedInternalQualificationWorkOrder,
) -> dict[str, str]:
    verified = _require_token(token)
    bindings = dict(verified.order.file_bindings)
    return {
        "run_uuid": verified.order.run_uuid,
        "attempt_uuid": verified.order.attempt_uuid,
        "interpreter": bindings["interpreter"].final_path,
        "interpreter_sha256": bindings["interpreter"].sha256,
        "fixture": bindings["fixture"].final_path,
        "fixture_sha256": bindings["fixture"].sha256,
        "qualifier": bindings["qualifier"].final_path,
        "qualifier_sha256": bindings["qualifier"].sha256,
        "runner_source": bindings["runner_source"].final_path,
        "runner_source_sha256": bindings["runner_source"].sha256,
        "powershell": bindings["powershell"].final_path,
        "powershell_sha256": bindings["powershell"].sha256,
        "codex": bindings["codex"].final_path,
        "codex_sha256": bindings["codex"].sha256,
        "command_processor": bindings["command_processor"].final_path,
        "command_processor_sha256": bindings["command_processor"].sha256,
        "pycache_prefix": verified.order.pycache_prefix,
        "output_root": verified.order.output_root,
    }


def require_verified_qualification_work_order(
    token: VerifiedInternalQualificationWorkOrder | None,
    *,
    config: Mapping[str, str] | object,
) -> VerifiedInternalQualificationWorkOrder:
    """Require an exact token/config match before any lineage or process call."""

    verified = _require_token(token)
    expected = qualification_config_projection(verified)
    if isinstance(config, Mapping):
        observed = dict(config)
    else:
        try:
            observed = {name: getattr(config, name) for name in CONFIG_FIELDS}
        except AttributeError as exc:
            raise QualificationWorkOrderError("qualification_config_fields_missing") from exc
    if set(observed) != set(CONFIG_FIELDS) or observed != expected:
        raise QualificationWorkOrderError("qualification_config_work_order_mismatch")
    return verified


def work_order_contract() -> dict[str, Any]:
    return {
        "schema": f"{WORK_ORDER_SCHEMA}.contract.v1",
        "authority_scope": AUTHORITY_SCOPE,
        "external_authority_replaced": False,
        "production_go_enabled": False,
        "same_token_hostile_admin_protected": False,
        "toolchain_runtime_closure_state": "unproven",
        "reviewer_blockers": [
            "external_oob_work_order_authority_required",
            "preparer_prelaunch_trusted_pin_unproven",
            "python_runtime_transitive_closure_unproven",
            "same_token_hostile_admin_tamper_resistance_unproven",
        ],
        "go_evidence_eligible": False,
        "process_calls_implemented": False,
        "canonical_output_root": CANONICAL_OUTPUT_ROOT,
        "canonical_pycache_root": CANONICAL_PYCACHE_ROOT,
        "canonical_work_order_root": CANONICAL_WORK_ORDER_ROOT,
        "external_canonical_parent_provisioning_required": True,
        "canonical_parent_paths_required_exact": [
            CANONICAL_OUTPUT_ROOT,
            CANONICAL_PYCACHE_ROOT,
            CANONICAL_WORK_ORDER_ROOT,
        ],
        "canonical_parent_protected_dacl_and_identity_readback_required": True,
        "candidate_may_self_provision_canonical_parents": False,
        "exact_file_binding_roles": list(FILE_BINDING_ROLES),
        "exact_source_closure_roles": list(SOURCE_CLOSURE_ROLES),
        "source_closure_inventory_count_and_sha256_required": True,
        "preserved_untracked_exact_path_content_inventory_required": True,
        "preserved_untracked_import_active_count_required": 0,
        "normalized_invocation_exact_match_required": True,
        "run_unique_pycache_prefix_initially_and_post_run_absent_required": True,
        "trusted_outer_independent_digest_required": True,
        "verified_token_required_before_lineage_reservation_or_process": True,
        "automatic_retry_allowed": False,
        "success_or_completion_marker_allowed": False,
    }


__all__ = [
    "AUTHORITY_SCOPE",
    "CANONICAL_OUTPUT_ROOT",
    "CANONICAL_PYCACHE_ROOT",
    "CANONICAL_WORK_ORDER_ROOT",
    "CONFIG_FIELDS",
    "FILE_BINDING_ROLES",
    "INVOCATION_SCHEMA",
    "InternalDirectoryIdentity",
    "InternalHandleIdentity",
    "InternalQualificationWorkOrder",
    "QUALIFICATION_MODE",
    "PRESERVED_UNTRACKED_SCHEMA",
    "PRESERVED_UNTRACKED_SCOPE",
    "PreservedUntrackedEntry",
    "PreservedUntrackedInventory",
    "QualificationInvocation",
    "QualificationWorkOrderError",
    "QualificationWorkOrderExpectation",
    "SOURCE_CLOSURE_ROLES",
    "SOURCE_CLOSURE_SCHEMA",
    "SourceClosureEntry",
    "SourceClosureInventory",
    "VerifiedInternalQualificationWorkOrder",
    "WORK_ORDER_SCHEMA",
    "canonical_json_bytes",
    "qualification_config_projection",
    "require_verified_qualification_work_order",
    "verify_internal_qualification_work_order",
    "work_order_contract",
]
