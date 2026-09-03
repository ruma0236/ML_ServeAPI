"""Prepare one append-only, internal-only r7s7 Windows qualification work order.

This preparer is intentionally not an external approval authority.  It performs
read-only checkout, lineage, source/tool, and directory measurements and then
publishes one immutable work-order candidate.  It never starts the qualification
runner and can never emit a GO or completion marker.
"""

from __future__ import annotations

import argparse
import bisect
import ctypes
import ctypes.wintypes
import hashlib
import importlib
import json
import ntpath
import os
import re
import secrets
import stat
import struct
import sys
import uuid
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol


_BOOTSTRAP_STDLIB_PRELOAD = (ctypes, ctypes.wintypes, re, secrets)


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
    raise RuntimeError("pre_r8_r7s7_preparer_requires_python_i_b_s")
if (
    not sys.pycache_prefix
    or not Path(sys.pycache_prefix).is_absolute()
    or Path(sys.pycache_prefix).exists()
):
    raise RuntimeError("pre_r8_r7s7_preparer_requires_fresh_absolute_pycache_prefix")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
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
PRESERVED_UNTRACKED_SCHEMA = (
    "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification.preserved-untracked-inventory.v1"
)
PRESERVED_UNTRACKED_SCOPE = "all_regular_files_not_in_index_including_git_ignored"
REVIEWER_BLOCKERS = [
    "external_oob_work_order_authority_required",
    "preparer_prelaunch_trusted_pin_unproven",
    "python_runtime_transitive_closure_unproven",
    "same_token_hostile_admin_tamper_resistance_unproven",
]


def _reject_bootstrap_source_import_hazards(source_root: Path) -> None:
    """Reject legacy sourceless modules before adding project ``src`` to sys.path."""

    if not source_root.is_dir():
        raise RuntimeError("pre_r8_r7s7_preparer_source_root_missing")
    for candidate in source_root.iterdir():
        if candidate.name not in {"evm", "__pycache__"}:
            raise RuntimeError("pre_r8_r7s7_preparer_noncanonical_top_level_source_present")
        if candidate.name == "evm" and not candidate.is_dir():
            raise RuntimeError("pre_r8_r7s7_preparer_evm_package_directory_required")
    for directory, names, files in os.walk(source_root, topdown=True, followlinks=False):
        current = Path(directory)
        retained_names: list[str] = []
        for name in names:
            candidate = current / name
            observed = candidate.lstat()
            is_reparse = bool(
                getattr(observed, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            if candidate.is_symlink() or is_reparse:
                raise RuntimeError("pre_r8_r7s7_preparer_source_directory_reparse_present")
            if name != "__pycache__":
                retained_names.append(name)
        names[:] = retained_names
        for name in files:
            candidate = current / name
            observed = candidate.lstat()
            is_reparse = bool(
                getattr(observed, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            if candidate.is_symlink() or is_reparse:
                raise RuntimeError("pre_r8_r7s7_preparer_source_file_reparse_present")
            if candidate.suffix.casefold() in {".pyc", ".pyo"}:
                raise RuntimeError("pre_r8_r7s7_preparer_legacy_sourceless_module_present")


_reject_bootstrap_source_import_hazards(PROJECT_ROOT / "src")
handle_io: Any = None
work_order_gate: Any = None
publish_bound_no_replace_durable: Any = None


PREPARER_RESULT_SCHEMA = (
    "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification-preparer-result.v1"
)
PREPARER_FAILURE_SCHEMA = (
    "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification-preparer-failure.v1"
)
CANONICAL_PARENT_PROVISIONING_SCHEMA = (
    "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-qualification."
    "external-canonical-parent-provisioning.v1"
)
MAX_INDEX_ENTRIES = 100_000
MAX_TRACKED_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_TOTAL_TRACKED_BYTES = 8 * 1024 * 1024 * 1024
MAX_UNTRACKED_ENTRIES = 100_000
MAX_UNTRACKED_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_TOTAL_UNTRACKED_BYTES = 8 * 1024 * 1024 * 1024
MAX_GIT_PACK_BYTES = 4 * 1024 * 1024 * 1024
MAX_GIT_OBJECT_BYTES = 64 * 1024 * 1024
_HEX40 = frozenset("0123456789abcdef")
_HEX64 = frozenset("0123456789abcdef")


class PreparerError(RuntimeError):
    """Fail-closed preparer rejection with mutation-free call counts."""

    def __init__(self, code: str, *, stage: str) -> None:
        super().__init__(code)
        self.code = code
        self.stage = stage
        self.call_counts = {
            "repository_process_creation": 0,
            "qualification_process_creation": 0,
            "work_order_publication": 0,
            "automatic_retry": 0,
            "force_termination": 0,
            "success_marker": 0,
            "completion_marker": 0,
        }


@dataclass(frozen=True, slots=True)
class ExecutablePin:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class PrepareRequest:
    global_run_id: str
    run_uuid: str
    attempt_uuid: str
    expected_commit: str
    expected_tree: str
    expected_preparer_sha256: str
    expected_untracked_count: int
    expected_untracked_bytes: int
    expected_untracked_inventory_sha256: str
    interpreter: ExecutablePin
    powershell: ExecutablePin
    codex: ExecutablePin
    command_processor: ExecutablePin


@dataclass(frozen=True, slots=True)
class RepositoryObservation:
    checkout_root: str
    commit: str
    commit_tree: str
    index_tree: str
    tracked_entry_count: int
    tracked_bytes: int
    tracked_files: tuple[TrackedFileObservation, ...]
    clean: bool
    untracked_examined: bool
    untracked_count: int
    untracked_bytes: int
    untracked_files: tuple[UntrackedFileObservation, ...]
    untracked_inventory_sha256: str
    untracked_import_active_count: int
    child_process_count: int = 0


@dataclass(frozen=True, slots=True)
class TrackedFileObservation:
    relative_path: str
    blob_oid: str
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class UntrackedFileObservation:
    relative_path: str
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class PublicationObservation:
    final_path: str
    sha256: str
    bytes: int
    file_flush_count: int
    directory_flush_count: int
    directory_flush_succeeded: bool
    same_handle_readback: bool
    file_identity_stable_across_rename: bool
    replace_if_exists: bool
    file_identity: dict[str, Any]
    directory_identity: dict[str, Any]
    create_attempt_count: int = 1


class RepositoryInspector(Protocol):
    def inspect(self, start: Path) -> RepositoryObservation: ...


class IdentityInspector(Protocol):
    def file(self, role: str, path: str) -> dict[str, Any]: ...

    def directory(self, role: str, path: str) -> dict[str, Any]: ...


class LineageInspector(Protocol):
    def measure(self) -> dict[str, Any]: ...


class Publisher(Protocol):
    def publish(
        self, *, root: str, leaf: str, raw: bytes, run_uuid: str
    ) -> PublicationObservation: ...


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
        raise PreparerError(f"{label}_uuid4_required", stage="request")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise PreparerError(f"{label}_uuid4_required", stage="request") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise PreparerError(f"{label}_uuid4_required", stage="request")
    return value


def _hex(value: object, length: int, label: str) -> str:
    alphabet = _HEX40 if length == 40 else _HEX64
    if type(value) is not str or len(value) != length or not set(value) <= alphabet:
        raise PreparerError(f"{label}_invalid", stage="request")
    return value


def _normal(path: str | os.PathLike[str]) -> str:
    value = os.fspath(path)
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return ntpath.normcase(ntpath.normpath(value))


def _resolve_git_dir(start: Path) -> tuple[Path, Path]:
    for checkout_root in (start, *start.parents):
        marker = checkout_root / ".git"
        if marker.is_dir():
            return checkout_root, marker
        if marker.is_file():
            raw = marker.read_text(encoding="utf-8").strip()
            if not raw.startswith("gitdir: "):
                raise PreparerError("gitdir_file_invalid", stage="repository")
            git_dir = Path(raw[8:])
            if not git_dir.is_absolute():
                git_dir = (checkout_root / git_dir).resolve()
            if not git_dir.is_dir():
                raise PreparerError("gitdir_missing", stage="repository")
            return checkout_root, git_dir
    raise PreparerError("git_checkout_not_found", stage="repository")


def _read_ref(git_dir: Path, ref: str) -> str:
    loose = git_dir / Path(*ref.split("/"))
    if loose.is_file():
        return loose.read_text(encoding="ascii").strip()
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="ascii").splitlines():
            if not line or line[0] in {"#", "^"}:
                continue
            oid, separator, name = line.partition(" ")
            if separator and name == ref:
                return oid
    raise PreparerError("head_ref_unresolved", stage="repository")


def _head_oid(git_dir: Path) -> str:
    raw = (git_dir / "HEAD").read_text(encoding="ascii").strip()
    oid = _read_ref(git_dir, raw[5:]) if raw.startswith("ref: ") else raw
    if len(oid) != 40 or not set(oid) <= _HEX40:
        raise PreparerError("head_oid_invalid", stage="repository")
    return oid


def _decode_loose_object(git_dir: Path, oid: str, expected_type: bytes) -> bytes | None:
    path = git_dir / "objects" / oid[:2] / oid[2:]
    if not path.is_file():
        return None
    try:
        decoded = zlib.decompress(path.read_bytes())
    except zlib.error as exc:
        raise PreparerError("loose_git_object_invalid", stage="repository") from exc
    header, separator, payload = decoded.partition(b"\0")
    kind, space, size = header.partition(b" ")
    if (
        separator != b"\0"
        or space != b" "
        or kind != expected_type
        or not size.isdigit()
        or int(size) != len(payload)
        or hashlib.sha1(decoded).hexdigest() != oid
    ):
        raise PreparerError("loose_git_object_identity_mismatch", stage="repository")
    return payload


def _packed_object_offset(index_path: Path, oid: bytes) -> tuple[Path, int, bytes, int] | None:
    """Return a v2 pack offset after validating the complete index identity."""

    raw = index_path.read_bytes()
    if len(raw) < 8 + (256 * 4) + 40 or raw[:4] != b"\xfftOc":
        raise PreparerError("git_pack_index_v2_required", stage="repository")
    if struct.unpack(">I", raw[4:8])[0] != 2:
        raise PreparerError("git_pack_index_version_unsupported", stage="repository")
    if hashlib.sha1(raw[:-20]).digest() != raw[-20:]:
        raise PreparerError("git_pack_index_checksum_mismatch", stage="repository")
    fanout = struct.unpack(">256I", raw[8 : 8 + (256 * 4)])
    if any(right < left for left, right in zip(fanout, fanout[1:])):
        raise PreparerError("git_pack_index_fanout_invalid", stage="repository")
    count = fanout[-1]
    if count > MAX_INDEX_ENTRIES:
        raise PreparerError("git_pack_index_count_limit", stage="repository")
    names_start = 8 + (256 * 4)
    crc_start = names_start + (count * 20)
    offsets_start = crc_start + (count * 4)
    base_end = offsets_start + (count * 4)
    if base_end + 40 > len(raw):
        raise PreparerError("git_pack_index_truncated", stage="repository")
    names = [raw[offset : offset + 20] for offset in range(names_start, crc_start, 20)]
    if names != sorted(names) or len(set(names)) != len(names):
        raise PreparerError("git_pack_index_object_names_invalid", stage="repository")
    offset_words = struct.unpack(f">{count}I", raw[offsets_start:base_end]) if count else ()
    large_count = sum(bool(word & 0x80000000) for word in offset_words)
    expected_size = base_end + (large_count * 8) + 40
    if len(raw) != expected_size:
        raise PreparerError("git_pack_index_size_mismatch", stage="repository")
    position = bisect.bisect_left(names, oid)
    if position >= count or names[position] != oid:
        return None
    word = offset_words[position]
    if word & 0x80000000:
        large_index = word & 0x7FFFFFFF
        if large_index >= large_count:
            raise PreparerError("git_pack_large_offset_invalid", stage="repository")
        large_start = base_end + (large_index * 8)
        object_offset = struct.unpack(">Q", raw[large_start : large_start + 8])[0]
    else:
        object_offset = word
    pack_path = index_path.with_suffix(".pack")
    if not pack_path.is_file():
        raise PreparerError("git_pack_file_missing", stage="repository")
    return pack_path, object_offset, raw[-40:-20], count


def _validate_pack_checksum(pack_path: Path, expected_checksum: bytes) -> tuple[int, int]:
    size = pack_path.stat().st_size
    if size < 32 or size > MAX_GIT_PACK_BYTES:
        raise PreparerError("git_pack_size_invalid", stage="repository")
    digest = hashlib.sha1()
    remaining = size - 20
    with pack_path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b"PACK":
            raise PreparerError("git_pack_header_invalid", stage="repository")
        version, count = struct.unpack(">II", header[4:12])
        if version not in {2, 3} or count > MAX_INDEX_ENTRIES:
            raise PreparerError("git_pack_version_or_count_unsupported", stage="repository")
        stream.seek(0)
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise PreparerError("git_pack_truncated", stage="repository")
            digest.update(chunk)
            remaining -= len(chunk)
        trailer = stream.read(20)
        if len(trailer) != 20 or stream.read(1):
            raise PreparerError("git_pack_trailer_invalid", stage="repository")
    if digest.digest() != trailer or trailer != expected_checksum:
        raise PreparerError("git_pack_checksum_mismatch", stage="repository")
    return size, count


def _read_packed_object(
    pack_path: Path,
    *,
    offset: int,
    expected_type: bytes,
    expected_oid: str,
    expected_pack_checksum: bytes,
    expected_object_count: int,
) -> bytes:
    pack_size, pack_object_count = _validate_pack_checksum(pack_path, expected_pack_checksum)
    if pack_object_count != expected_object_count:
        raise PreparerError("git_pack_index_object_count_mismatch", stage="repository")
    if offset < 12 or offset >= pack_size - 20:
        raise PreparerError("git_pack_object_offset_invalid", stage="repository")
    with pack_path.open("rb") as stream:
        stream.seek(offset)
        first = stream.read(1)
        if not first:
            raise PreparerError("git_pack_object_header_truncated", stage="repository")
        byte = first[0]
        object_type = (byte >> 4) & 0x07
        declared_size = byte & 0x0F
        shift = 4
        while byte & 0x80:
            next_byte = stream.read(1)
            if not next_byte or shift > 60:
                raise PreparerError("git_pack_object_header_invalid", stage="repository")
            byte = next_byte[0]
            declared_size |= (byte & 0x7F) << shift
            shift += 7
        expected_type_code = {b"commit": 1, b"tree": 2, b"blob": 3, b"tag": 4}.get(expected_type)
        if object_type != expected_type_code:
            if object_type in {6, 7}:
                raise PreparerError("packed_git_delta_object_unsupported", stage="repository")
            raise PreparerError("git_pack_object_type_mismatch", stage="repository")
        if declared_size > MAX_GIT_OBJECT_BYTES:
            raise PreparerError("git_pack_object_size_limit", stage="repository")
        decoder = zlib.decompressobj()
        payload = bytearray()
        while not decoder.eof:
            chunk = stream.read(64 * 1024)
            if not chunk:
                raise PreparerError("git_pack_object_data_truncated", stage="repository")
            try:
                payload.extend(decoder.decompress(chunk, MAX_GIT_OBJECT_BYTES + 1 - len(payload)))
            except zlib.error as exc:
                raise PreparerError("git_pack_object_zlib_invalid", stage="repository") from exc
            if len(payload) > MAX_GIT_OBJECT_BYTES:
                raise PreparerError("git_pack_object_size_limit", stage="repository")
            if not decoder.eof and decoder.unconsumed_tail:
                raise PreparerError("git_pack_object_size_limit", stage="repository")
        result = bytes(payload)
    if len(result) != declared_size or _git_object_id(expected_type, result).hex() != expected_oid:
        raise PreparerError("git_pack_object_identity_mismatch", stage="repository")
    return result


def _git_object(git_dir: Path, oid: str, expected_type: bytes) -> bytes:
    loose = _decode_loose_object(git_dir, oid, expected_type)
    if loose is not None:
        return loose
    oid_bytes = bytes.fromhex(oid)
    pack_dir = git_dir / "objects" / "pack"
    for index_path in sorted(pack_dir.glob("*.idx")) if pack_dir.is_dir() else ():
        located = _packed_object_offset(index_path, oid_bytes)
        if located is None:
            continue
        pack_path, offset, pack_checksum, object_count = located
        return _read_packed_object(
            pack_path,
            offset=offset,
            expected_type=expected_type,
            expected_oid=oid,
            expected_pack_checksum=pack_checksum,
            expected_object_count=object_count,
        )
    raise PreparerError("required_git_object_unavailable", stage="repository")


def _commit_tree(git_dir: Path, commit: str) -> str:
    payload = _git_object(git_dir, commit, b"commit")
    first = payload.splitlines()[0]
    if not first.startswith(b"tree "):
        raise PreparerError("commit_tree_header_missing", stage="repository")
    tree = first[5:].decode("ascii", errors="strict")
    if len(tree) != 40 or not set(tree) <= _HEX40:
        raise PreparerError("commit_tree_invalid", stage="repository")
    return tree


@dataclass(frozen=True, slots=True)
class _IndexEntry:
    path_raw: bytes
    mode: int
    oid: bytes


def _parse_index(git_dir: Path) -> tuple[_IndexEntry, ...]:
    path = git_dir / "index"
    raw = path.read_bytes()
    if len(raw) < 32 or raw[:4] != b"DIRC":
        raise PreparerError("git_index_invalid", stage="repository")
    if hashlib.sha1(raw[:-20]).digest() != raw[-20:]:
        raise PreparerError("git_index_checksum_mismatch", stage="repository")
    version, count = struct.unpack(">II", raw[4:12])
    if version not in {2, 3} or count > MAX_INDEX_ENTRIES:
        raise PreparerError("git_index_version_or_count_unsupported", stage="repository")
    limit = len(raw) - 20
    offset = 12
    entries: list[_IndexEntry] = []
    for _ in range(count):
        entry_start = offset
        if offset + 62 > limit:
            raise PreparerError("git_index_entry_truncated", stage="repository")
        mode = struct.unpack(">I", raw[offset + 24 : offset + 28])[0]
        oid = raw[offset + 40 : offset + 60]
        flags = struct.unpack(">H", raw[offset + 60 : offset + 62])[0]
        offset += 62
        if flags & 0x4000:
            if version != 3 or offset + 2 > limit:
                raise PreparerError("git_index_extended_flags_invalid", stage="repository")
            extended = struct.unpack(">H", raw[offset : offset + 2])[0]
            offset += 2
            if extended != 0:
                raise PreparerError("git_index_extended_entry_unsupported", stage="repository")
        if (flags >> 12) & 0x3:
            raise PreparerError("git_index_nonzero_stage", stage="repository")
        name_end = raw.find(b"\0", offset, limit)
        if name_end < 0:
            raise PreparerError("git_index_name_unterminated", stage="repository")
        path_raw = raw[offset:name_end]
        declared_length = flags & 0x0FFF
        if declared_length < 0x0FFF and declared_length != len(path_raw):
            raise PreparerError("git_index_name_length_mismatch", stage="repository")
        if (
            not path_raw
            or path_raw.startswith(b"/")
            or b"\\" in path_raw
            or b"\0" in path_raw
            or any(part in {b"", b".", b".."} for part in path_raw.split(b"/"))
        ):
            raise PreparerError("git_index_path_invalid", stage="repository")
        offset = name_end + 1
        offset += (8 - ((offset - entry_start) % 8)) % 8
        if offset > limit:
            raise PreparerError("git_index_padding_invalid", stage="repository")
        entries.append(_IndexEntry(path_raw=path_raw, mode=mode, oid=oid))
    while offset < limit:
        if offset + 8 > limit:
            raise PreparerError("git_index_extension_truncated", stage="repository")
        signature = raw[offset : offset + 4]
        size = struct.unpack(">I", raw[offset + 4 : offset + 8])[0]
        offset += 8
        if offset + size > limit:
            raise PreparerError("git_index_extension_size_invalid", stage="repository")
        if signature[:1].islower():
            raise PreparerError("git_index_mandatory_extension_unsupported", stage="repository")
        offset += size
    if len({entry.path_raw for entry in entries}) != len(entries):
        raise PreparerError("git_index_duplicate_path", stage="repository")
    return tuple(entries)


def _git_object_id(kind: bytes, payload: bytes) -> bytes:
    header = kind + b" " + str(len(payload)).encode("ascii") + b"\0"
    return hashlib.sha1(header + payload).digest()


def _index_tree(entries: tuple[_IndexEntry, ...]) -> str:
    root: dict[bytes, Any] = {}
    for entry in entries:
        components = entry.path_raw.split(b"/")
        node = root
        for component in components[:-1]:
            existing = node.setdefault(component, {})
            if not isinstance(existing, dict):
                raise PreparerError("git_index_file_directory_collision", stage="repository")
            node = existing
        if components[-1] in node:
            raise PreparerError("git_index_tree_collision", stage="repository")
        node[components[-1]] = entry

    def emit(node: dict[bytes, Any]) -> bytes:
        rows: list[tuple[bytes, bytes]] = []
        for name, child in node.items():
            if isinstance(child, dict):
                payload = emit(child)
                row = b"40000 " + name + b"\0" + _git_object_id(b"tree", payload)
                rows.append((name + b"/", row))
            else:
                file_type = child.mode & 0o170000
                if file_type == 0o100000:
                    mode = b"100755" if child.mode & 0o111 else b"100644"
                elif file_type == 0o120000:
                    mode = b"120000"
                else:
                    raise PreparerError("git_index_mode_unsupported", stage="repository")
                rows.append((name, mode + b" " + name + b"\0" + child.oid))
        return b"".join(row for _, row in sorted(rows, key=lambda item: item[0]))

    payload = emit(root)
    return _git_object_id(b"tree", payload).hex()


def _tracked_worktree_clean(
    checkout_root: Path,
    entries: tuple[_IndexEntry, ...],
    *,
    allow_windows_crlf_clean_filter: bool,
) -> tuple[bool, int, tuple[TrackedFileObservation, ...]]:
    total = 0
    observations: list[TrackedFileObservation] = []
    for entry in entries:
        relative = entry.path_raw.decode("utf-8", errors="surrogateescape")
        candidate = checkout_root.joinpath(*relative.split("/"))
        file_type = entry.mode & 0o170000
        try:
            if file_type == 0o120000:
                if not candidate.is_symlink():
                    return False, total, tuple(observations)
                payload = os.readlink(candidate).encode("utf-8", errors="surrogateescape")
            elif file_type == 0o100000:
                observed = candidate.lstat()
                if not stat.S_ISREG(observed.st_mode) or candidate.is_symlink():
                    return False, total, tuple(observations)
                if observed.st_size > MAX_TRACKED_FILE_BYTES:
                    raise PreparerError("tracked_file_size_limit", stage="repository")
                payload = candidate.read_bytes()
            else:
                raise PreparerError("git_index_mode_unsupported", stage="repository")
        except FileNotFoundError:
            return False, total, tuple(observations)
        total += len(payload)
        if total > MAX_TOTAL_TRACKED_BYTES:
            raise PreparerError("tracked_checkout_size_limit", stage="repository")
        observed_oid = _git_object_id(b"blob", payload)
        if observed_oid != entry.oid:
            # A Windows checkout with core.autocrlf=true legitimately stores
            # CRLF bytes while the index/tree stores the clean-filtered LF
            # blob.  Model only that one reversible, content-preserving text
            # conversion.  Binary/NUL data, non-UTF-8 data, bare CR, and any
            # other transform remain fail-closed.  Bound task files retain
            # their exact raw SHA-256/byte count below in addition to the Git
            # blob identity.
            filtered = None
            if allow_windows_crlf_clean_filter and b"\r\n" in payload and b"\0" not in payload:
                try:
                    text = payload.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    text = ""
                without_pairs = text.replace("\r\n", "")
                unsafe_control = any(
                    ord(character) < 32 and character not in "\n\t\f\b"
                    for character in without_pairs
                )
                if text and "\r" not in without_pairs and not unsafe_control:
                    filtered = payload.replace(b"\r\n", b"\n")
            if filtered is None or _git_object_id(b"blob", filtered) != entry.oid:
                return False, total, tuple(observations)
        observations.append(
            TrackedFileObservation(
                relative_path=entry.path_raw.decode("utf-8", errors="strict"),
                blob_oid=entry.oid.hex(),
                sha256=_sha256(payload),
                bytes=len(payload),
            )
        )
    return True, total, tuple(observations)


def _is_reparse(stat_result: os.stat_result) -> bool:
    return bool(
        getattr(stat_result, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _is_import_active_untracked(
    relative_path: str,
    *,
    project_relative_root: str,
) -> bool:
    """Classify paths that could influence the pinned -I/-S project import closure."""

    lowered = relative_path.casefold()
    project = project_relative_root.casefold().rstrip("/")
    if lowered != project and not lowered.startswith(project + "/"):
        return False
    project_local = lowered[len(project) :].lstrip("/")
    parts = project_local.split("/")
    suffix = ntpath.splitext(parts[-1])[1]
    # The exact run-unique, initially absent -X pycache_prefix means source-local
    # __pycache__ entries are never consulted.  They are nevertheless content-
    # pinned below, so preservation drift remains detectable.
    if "__pycache__" in parts and suffix in {".pyc", ".pyo"}:
        return False
    if suffix in {".py", ".pyw", ".pyc", ".pyo", ".pyd", ".pth", ".dll", ".exe"}:
        return True
    protected_roots = ("src/evm/", "scripts/dev/")
    return project_local.startswith(protected_roots)


def _untracked_inventory(
    checkout_root: Path,
    entries: tuple[_IndexEntry, ...],
    *,
    project_root: Path,
) -> tuple[tuple[UntrackedFileObservation, ...], int, int, str]:
    tracked = {
        entry.path_raw.decode("utf-8", errors="strict").replace("\\", "/") for entry in entries
    }
    try:
        project_relative_root = (
            project_root.resolve(strict=True)
            .relative_to(checkout_root.resolve(strict=True))
            .as_posix()
        )
    except (FileNotFoundError, ValueError) as exc:
        raise PreparerError("project_root_not_within_checkout", stage="repository") from exc
    observed: list[UntrackedFileObservation] = []
    import_active_count = 0
    total_bytes = 0
    for directory, names, files in os.walk(checkout_root, topdown=True, followlinks=False):
        current = Path(directory)
        if current == checkout_root:
            names[:] = [name for name in names if name != ".git"]
            files = [name for name in files if name != ".git"]
        for name in names:
            candidate = current / name
            try:
                observed_stat = candidate.lstat()
            except FileNotFoundError as exc:
                raise PreparerError("untracked_directory_changed", stage="repository") from exc
            if candidate.is_symlink() or _is_reparse(observed_stat):
                raise PreparerError("untracked_directory_symlink_or_reparse", stage="repository")
        for name in files:
            candidate = current / name
            relative = candidate.relative_to(checkout_root).as_posix()
            if relative in tracked:
                continue
            try:
                before = candidate.lstat()
                if (
                    not stat.S_ISREG(before.st_mode)
                    or candidate.is_symlink()
                    or _is_reparse(before)
                ):
                    raise PreparerError("untracked_file_not_regular", stage="repository")
                if before.st_size > MAX_UNTRACKED_FILE_BYTES:
                    raise PreparerError("untracked_file_size_limit", stage="repository")
                raw = candidate.read_bytes()
                after = candidate.lstat()
            except FileNotFoundError as exc:
                raise PreparerError("untracked_file_changed", stage="repository") from exc
            stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
            if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
                raise PreparerError("untracked_file_changed", stage="repository")
            if len(raw) != before.st_size:
                raise PreparerError("untracked_file_size_changed", stage="repository")
            total_bytes += len(raw)
            if total_bytes > MAX_TOTAL_UNTRACKED_BYTES:
                raise PreparerError("untracked_inventory_size_limit", stage="repository")
            observed.append(
                UntrackedFileObservation(
                    relative_path=relative,
                    sha256=_sha256(raw),
                    bytes=len(raw),
                )
            )
            import_active_count += int(
                _is_import_active_untracked(
                    relative,
                    project_relative_root=project_relative_root,
                )
            )
            if len(observed) > MAX_UNTRACKED_ENTRIES:
                raise PreparerError("untracked_inventory_count_limit", stage="repository")
    ordered = tuple(sorted(observed, key=lambda item: item.relative_path))
    if len({item.relative_path.casefold() for item in ordered}) != len(ordered):
        raise PreparerError("untracked_inventory_case_collision", stage="repository")
    payload = {
        "schema": PRESERVED_UNTRACKED_SCHEMA,
        "scope": PRESERVED_UNTRACKED_SCOPE,
        "files": [asdict(item) for item in ordered],
        "count": len(ordered),
        "total_bytes": total_bytes,
        "import_active_count": import_active_count,
    }
    return ordered, total_bytes, import_active_count, _sha256(_canonical_json(payload))


class PureGitRepositoryInspector:
    """Read Git metadata directly; it never creates a helper process."""

    def inspect(self, start: Path) -> RepositoryObservation:
        checkout_root, git_dir = _resolve_git_dir(start)
        blockers = (
            git_dir / "index.lock",
            git_dir / "MERGE_HEAD",
            git_dir / "CHERRY_PICK_HEAD",
            git_dir / "rebase-apply",
            git_dir / "rebase-merge",
        )
        if any(path.exists() for path in blockers):
            raise PreparerError("git_operation_or_lock_in_progress", stage="repository")
        config = (git_dir / "config").read_text(encoding="utf-8", errors="strict")
        if "objectformat" in config.lower() and "sha256" in config.lower():
            raise PreparerError("git_sha256_object_format_unsupported", stage="repository")
        commit = _head_oid(git_dir)
        commit_tree = _commit_tree(git_dir, commit)
        entries = _parse_index(git_dir)
        index_tree = _index_tree(entries)
        worktree_clean, total, tracked_files = _tracked_worktree_clean(
            checkout_root,
            entries,
            allow_windows_crlf_clean_filter=os.name == "nt",
        )
        untracked_files, untracked_bytes, import_active_count, untracked_inventory_sha256 = (
            _untracked_inventory(checkout_root, entries, project_root=PROJECT_ROOT)
        )
        return RepositoryObservation(
            checkout_root=str(checkout_root.resolve()),
            commit=commit,
            commit_tree=commit_tree,
            index_tree=index_tree,
            tracked_entry_count=len(entries),
            tracked_bytes=total,
            tracked_files=tracked_files,
            clean=worktree_clean and commit_tree == index_tree,
            untracked_examined=True,
            untracked_count=len(untracked_files),
            untracked_bytes=untracked_bytes,
            untracked_files=untracked_files,
            untracked_inventory_sha256=untracked_inventory_sha256,
            untracked_import_active_count=import_active_count,
        )


def _directory_json(role: str, display_path: str, identity: Any) -> dict[str, Any]:
    if identity.dacl_present is not True or type(identity.dacl_protected) is not bool:
        raise PreparerError(f"{role}_directory_dacl_invalid", stage="identity")
    return {
        "role": role,
        "final_path": display_path,
        "volume_serial_number": identity.volume_serial_number,
        "file_id_hex": identity.file_id_hex,
        "owner_sid": identity.owner_sid,
        "security_descriptor_sha256": identity.security_descriptor_sha256,
        "dacl_present": identity.dacl_present,
        "dacl_protected": identity.dacl_protected,
        "link_count": identity.link_count,
        "reparse_tag": identity.reparse_tag,
        "file_type": identity.file_type,
        "is_directory": bool(identity.attributes & handle_io.FILE_ATTRIBUTE_DIRECTORY),
    }


class Win32IdentityInspector:
    """Measure content and identity through retained no-write/no-delete handles."""

    def __init__(self) -> None:
        self.api: Any = None

    def _api(self) -> Any:
        if handle_io is None:
            raise PreparerError("project_modules_not_verified", stage="preimport")
        if self.api is None:
            self.api = handle_io.WindowsHandleApi()
        return self.api

    def directory(self, role: str, path: str) -> dict[str, Any]:
        display = str(Path(path).resolve(strict=True))
        api = self._api()
        handle: int | None = None
        try:
            handle = api.open_directory(display)
            identity = api.identity(handle)
            handle_io._reject_unsafe_directory_identity(identity, expected_path=display)
            return _directory_json(role, display, identity)
        finally:
            api.close(handle)

    def file(self, role: str, path: str) -> dict[str, Any]:
        display = str(Path(path).resolve(strict=True))
        api = self._api()
        handle: int | None = None
        try:
            handle = api.open_read(display)
            before = api.identity(handle)
            handle_io._reject_unsafe_identity(
                before, expected_path=display, require_protected_dacl=False
            )
            if before.dacl_present is not True or type(before.dacl_protected) is not bool:
                raise PreparerError(f"{role}_dacl_invalid", stage="identity")
            raw = api.read_all(handle, before.size)
            after = api.identity(handle)
            if before != after or len(raw) != before.size:
                raise PreparerError(f"{role}_identity_changed", stage="identity")
            parent_path = str(Path(display).parent.resolve(strict=True))
            parent = self.directory(f"qualification:{role}:parent", parent_path)
            return {
                "role": f"qualification:{role}",
                "final_path": display,
                "volume_serial_number": before.volume_serial_number,
                "file_id_hex": before.file_id_hex,
                "sha256": _sha256(raw),
                "bytes": len(raw),
                "owner_sid": before.owner_sid,
                "security_descriptor_sha256": before.security_descriptor_sha256,
                "dacl_present": before.dacl_present,
                "dacl_protected": before.dacl_protected,
                "link_count": before.link_count,
                "reparse_tag": before.reparse_tag,
                "file_type": before.file_type,
                "creation_time_ns": Path(display).stat().st_ctime_ns,
                "parent_directory_identity": parent,
            }
        finally:
            api.close(handle)


class DirectWin32LineageInspector:
    def measure(self) -> dict[str, Any]:
        import importlib.util

        qualifier_path = PROJECT_ROOT / "scripts/dev/qualify_pre_r8_r7s7_windows.py"
        spec = importlib.util.spec_from_file_location(
            "r7s7_preparer_lineage_qualifier", qualifier_path
        )
        if spec is None or spec.loader is None:
            raise PreparerError("qualifier_lineage_module_unavailable", stage="lineage")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module._measure_live_lineage()


class DurableWorkOrderPublisher:
    def publish(self, *, root: str, leaf: str, raw: bytes, run_uuid: str) -> PublicationObservation:
        published = publish_bound_no_replace_durable(
            root,
            leaf,
            raw,
            run_uuid=run_uuid,
            require_protected_dacl=True,
        )
        return PublicationObservation(
            final_path=published.final_path,
            sha256=published.sha256,
            bytes=published.bytes,
            file_flush_count=published.file_flush_count,
            directory_flush_count=published.directory_flush_count,
            directory_flush_succeeded=published.directory_flush_succeeded,
            same_handle_readback=published.same_handle_readback,
            file_identity_stable_across_rename=(published.file_identity_stable_across_rename),
            replace_if_exists=published.replace_if_exists,
            file_identity=published.identity.to_dict(),
            directory_identity=published.directory_identity.to_dict(),
        )


def _project_paths(request: PrepareRequest) -> dict[str, str]:
    return {
        "interpreter": request.interpreter.path,
        "fixture": str(PROJECT_ROOT / "scripts/dev/pre_r8_r7s7_windows_fixture.py"),
        "qualifier": str(PROJECT_ROOT / "scripts/dev/qualify_pre_r8_r7s7_windows.py"),
        "runner_source": str(PROJECT_ROOT / "src/evm/scale_validation/phase_b2_r7s3_process.py"),
        "powershell": request.powershell.path,
        "codex": request.codex.path,
        "command_processor": request.command_processor.path,
        "trusted_outer": str(
            PROJECT_ROOT / "scripts/dev/invoke_pre_r8_r7s7_windows_qualification.ps1"
        ),
        "work_order_gate": str(
            PROJECT_ROOT / "src/evm/scale_validation/phase_b2_r7s7_qualification_work_order.py"
        ),
        "admission_source": str(
            PROJECT_ROOT / "src/evm/scale_validation/phase_b2_r7s7_admission.py"
        ),
        "r7s3_handle_io_source": str(
            PROJECT_ROOT / "src/evm/scale_validation/phase_b2_r7s3_handle_io.py"
        ),
        "r7s4_handle_io_source": str(
            PROJECT_ROOT / "src/evm/scale_validation/phase_b2_r7s4_handle_io.py"
        ),
        "evm_package_init_source": str(PROJECT_ROOT / "src/evm/__init__.py"),
        "scale_validation_package_init_source": str(
            PROJECT_ROOT / "src/evm/scale_validation/__init__.py"
        ),
        "preparer": str(Path(__file__).resolve()),
    }


_PREIMPORT_PROJECT_ROLES = (
    "work_order_gate",
    "admission_source",
    "r7s3_handle_io_source",
    "r7s4_handle_io_source",
    "evm_package_init_source",
    "scale_validation_package_init_source",
    "preparer",
)


def _validate_preimport_project_sources(
    request: PrepareRequest,
    observation: RepositoryObservation,
    paths: Mapping[str, str],
) -> None:
    tracked = {item.relative_path.replace("\\", "/"): item for item in observation.tracked_files}
    if len(tracked) != len(observation.tracked_files):
        raise PreparerError("tracked_file_inventory_duplicate", stage="preimport")
    checkout_root = Path(observation.checkout_root).resolve(strict=True)
    for role in _PREIMPORT_PROJECT_ROLES:
        candidate = Path(paths[role]).resolve(strict=True)
        try:
            relative = candidate.relative_to(checkout_root).as_posix()
        except ValueError as exc:
            raise PreparerError(f"{role}_not_under_checkout", stage="preimport") from exc
        item = tracked.get(relative)
        if item is None:
            raise PreparerError(f"{role}_not_tracked_before_import", stage="preimport")
        observed = candidate.lstat()
        if (
            candidate.is_symlink()
            or not stat.S_ISREG(observed.st_mode)
            or _is_reparse(observed)
            or observed.st_size != item.bytes
        ):
            raise PreparerError(f"{role}_unsafe_before_import", stage="preimport")
        raw = candidate.read_bytes()
        if len(raw) != item.bytes or _sha256(raw) != item.sha256:
            raise PreparerError(f"{role}_content_changed_before_import", stage="preimport")
        if role == "preparer" and item.sha256 != request.expected_preparer_sha256:
            raise PreparerError("preparer_oob_sha256_mismatch_before_import", stage="preimport")


def _load_verified_project_modules() -> None:
    global handle_io, publish_bound_no_replace_durable, work_order_gate

    if (
        handle_io is not None
        and work_order_gate is not None
        and publish_bound_no_replace_durable is not None
    ):
        return
    module_names = (
        "evm.scale_validation.phase_b2_r7s7_qualification_work_order",
        "evm.scale_validation.phase_b2_r7s7_admission",
        "evm.scale_validation.phase_b2_r7s4_handle_io",
        "evm.scale_validation.phase_b2_r7s3_handle_io",
        "evm.scale_validation",
        "evm",
    )
    for name in module_names:
        sys.modules.pop(name, None)
    source_root = str((PROJECT_ROOT / "src").resolve(strict=True))
    sys.path[:] = [item for item in sys.path if _normal(item) != _normal(source_root)]
    sys.path.insert(0, source_root)
    importlib.invalidate_caches()
    loaded_handle_io = importlib.import_module("evm.scale_validation.phase_b2_r7s3_handle_io")
    loaded_work_order_gate = importlib.import_module(
        "evm.scale_validation.phase_b2_r7s7_qualification_work_order"
    )
    loaded_r7s4 = importlib.import_module("evm.scale_validation.phase_b2_r7s4_handle_io")
    expected_modules = {
        "evm": PROJECT_ROOT / "src/evm/__init__.py",
        "evm.scale_validation": PROJECT_ROOT / "src/evm/scale_validation/__init__.py",
        "evm.scale_validation.phase_b2_r7s3_handle_io": PROJECT_ROOT
        / "src/evm/scale_validation/phase_b2_r7s3_handle_io.py",
        "evm.scale_validation.phase_b2_r7s4_handle_io": PROJECT_ROOT
        / "src/evm/scale_validation/phase_b2_r7s4_handle_io.py",
        "evm.scale_validation.phase_b2_r7s7_admission": PROJECT_ROOT
        / "src/evm/scale_validation/phase_b2_r7s7_admission.py",
        "evm.scale_validation.phase_b2_r7s7_qualification_work_order": PROJECT_ROOT
        / "src/evm/scale_validation/phase_b2_r7s7_qualification_work_order.py",
    }
    for name, expected_path in expected_modules.items():
        module = sys.modules.get(name)
        if module is None or _normal(getattr(module, "__file__", "")) != _normal(expected_path):
            raise PreparerError("project_module_origin_mismatch", stage="preimport")
    if (
        loaded_work_order_gate.CANONICAL_OUTPUT_ROOT != CANONICAL_OUTPUT_ROOT
        or loaded_work_order_gate.CANONICAL_PYCACHE_ROOT != CANONICAL_PYCACHE_ROOT
        or loaded_work_order_gate.CANONICAL_WORK_ORDER_ROOT != CANONICAL_WORK_ORDER_ROOT
        or loaded_work_order_gate.PRESERVED_UNTRACKED_SCHEMA != PRESERVED_UNTRACKED_SCHEMA
        or loaded_work_order_gate.PRESERVED_UNTRACKED_SCOPE != PRESERVED_UNTRACKED_SCOPE
    ):
        raise PreparerError("project_module_constant_mismatch", stage="preimport")
    handle_io = loaded_handle_io
    work_order_gate = loaded_work_order_gate
    publish_bound_no_replace_durable = loaded_r7s4.publish_bound_no_replace_durable


def canonical_parent_provisioning_contract() -> dict[str, Any]:
    return {
        "schema": CANONICAL_PARENT_PROVISIONING_SCHEMA,
        "status_without_verified_preprovisioning": "reviewer_pending",
        "decision_without_verified_preprovisioning": "NO-GO",
        "external_provisioning_required": True,
        "preparer_may_create_or_modify_paths": False,
        "expected_directories": [
            {
                "role": "qualification:output_parent",
                "path": CANONICAL_OUTPUT_ROOT,
            },
            {
                "role": "qualification:pycache_parent",
                "path": CANONICAL_PYCACHE_ROOT,
            },
            {
                "role": "qualification:work_order_parent",
                "path": CANONICAL_WORK_ORDER_ROOT,
            },
        ],
        "required_identity_readback": [
            "final_path",
            "volume_serial_number",
            "file_id_hex",
            "owner_sid",
            "security_descriptor_sha256",
            "dacl_present",
            "dacl_protected",
            "link_count",
            "reparse_tag",
            "file_type",
            "is_directory",
        ],
        "dacl_present_required": True,
        "dacl_protected_required": True,
        "reparse_tag_required": 0,
        "disk_directory_required": True,
        "readback_required_before_publication": True,
        "self_authorization_allowed": False,
    }


def _measure_external_canonical_parent(
    identities: IdentityInspector,
    *,
    role: str,
    path: str,
) -> dict[str, Any]:
    try:
        value = identities.directory(role, path)
    except Exception as exc:
        raise PreparerError(
            "external_canonical_parent_provisioning_required",
            stage="canonical_parent_preflight",
        ) from exc
    required = {
        "role",
        "final_path",
        "volume_serial_number",
        "file_id_hex",
        "owner_sid",
        "security_descriptor_sha256",
        "dacl_present",
        "dacl_protected",
        "link_count",
        "reparse_tag",
        "file_type",
        "is_directory",
    }
    if (
        type(value) is not dict
        or set(value) != required
        or value.get("role") != role
        or _normal(str(value.get("final_path", ""))) != _normal(path)
        or type(value.get("volume_serial_number")) is not int
        or value.get("volume_serial_number", 0) <= 0
        or type(value.get("file_id_hex")) is not str
        or len(value.get("file_id_hex", "")) != 32
        or not set(value.get("file_id_hex", "")) <= _HEX40
        or type(value.get("owner_sid")) is not str
        or not value.get("owner_sid", "").startswith("S-")
        or type(value.get("security_descriptor_sha256")) is not str
        or len(value.get("security_descriptor_sha256", "")) != 64
        or not set(value.get("security_descriptor_sha256", "")) <= _HEX64
        or value.get("dacl_present") is not True
        or value.get("dacl_protected") is not True
        or type(value.get("link_count")) is not int
        or value.get("link_count", 0) < 1
        or value.get("reparse_tag") != 0
        or value.get("file_type") != 1
        or value.get("is_directory") is not True
    ):
        raise PreparerError(
            "external_canonical_parent_provisioning_required",
            stage="canonical_parent_preflight",
        )
    return value


def _validate_request(request: PrepareRequest) -> None:
    if type(request) is not PrepareRequest:
        raise PreparerError("typed_prepare_request_required", stage="request")
    _uuid4(request.global_run_id, "global_run_id")
    _uuid4(request.run_uuid, "run_uuid")
    _uuid4(request.attempt_uuid, "attempt_uuid")
    _hex(request.expected_commit, 40, "expected_commit")
    _hex(request.expected_tree, 40, "expected_tree")
    _hex(request.expected_preparer_sha256, 64, "expected_preparer_sha256")
    if type(request.expected_untracked_count) is not int or request.expected_untracked_count < 0:
        raise PreparerError("expected_untracked_count_invalid", stage="request")
    if type(request.expected_untracked_bytes) is not int or request.expected_untracked_bytes < 0:
        raise PreparerError("expected_untracked_bytes_invalid", stage="request")
    _hex(
        request.expected_untracked_inventory_sha256,
        64,
        "expected_untracked_inventory_sha256",
    )
    for role in ("interpreter", "powershell", "codex", "command_processor"):
        pin = getattr(request, role)
        if type(pin) is not ExecutablePin or not Path(pin.path).is_absolute():
            raise PreparerError(f"{role}_absolute_typed_pin_required", stage="request")
        _hex(pin.sha256, 64, f"{role}_sha256")


def _validate_repository(request: PrepareRequest, observation: RepositoryObservation) -> None:
    if type(observation) is not RepositoryObservation:
        raise PreparerError("typed_repository_observation_required", stage="repository")
    if (
        observation.commit != request.expected_commit
        or observation.commit_tree != request.expected_tree
        or observation.index_tree != request.expected_tree
        or observation.clean is not True
        or observation.child_process_count != 0
        or observation.untracked_examined is not True
        or observation.untracked_count != len(observation.untracked_files)
        or observation.untracked_count != request.expected_untracked_count
        or observation.untracked_bytes != request.expected_untracked_bytes
        or observation.untracked_bytes != sum(item.bytes for item in observation.untracked_files)
        or observation.untracked_inventory_sha256 != request.expected_untracked_inventory_sha256
        or observation.untracked_import_active_count != 0
        or observation.tracked_entry_count != len(observation.tracked_files)
        or _normal(observation.checkout_root) != _normal(PROJECT_ROOT.parent)
    ):
        raise PreparerError("checkout_commit_tree_or_cleanliness_mismatch", stage="repository")


def _preserved_untracked_inventory(observation: RepositoryObservation) -> dict[str, Any]:
    value = {
        "schema": PRESERVED_UNTRACKED_SCHEMA,
        "scope": PRESERVED_UNTRACKED_SCOPE,
        "files": [asdict(item) for item in observation.untracked_files],
        "count": observation.untracked_count,
        "total_bytes": observation.untracked_bytes,
        "import_active_count": observation.untracked_import_active_count,
    }
    if _sha256(_canonical_json(value)) != observation.untracked_inventory_sha256:
        raise PreparerError("untracked_inventory_internal_digest_mismatch", stage="repository")
    value["inventory_sha256"] = observation.untracked_inventory_sha256
    return value


def _validate_project_bindings_are_pinned_tree_blobs(
    observation: RepositoryObservation,
    paths: Mapping[str, str],
    bindings: Mapping[str, Mapping[str, Any]],
) -> None:
    tracked = {item.relative_path.replace("\\", "/"): item for item in observation.tracked_files}
    if len(tracked) != len(observation.tracked_files):
        raise PreparerError("tracked_file_inventory_duplicate", stage="repository")
    external_roles = {"interpreter", "powershell", "codex", "command_processor"}
    for role in work_order_gate.FILE_BINDING_ROLES:
        if role in external_roles:
            continue
        try:
            relative = (
                Path(paths[role])
                .resolve(strict=False)
                .relative_to(Path(observation.checkout_root))
                .as_posix()
            )
        except ValueError as exc:
            raise PreparerError(f"{role}_not_under_checkout", stage="repository") from exc
        tracked_file = tracked.get(relative)
        if tracked_file is None:
            raise PreparerError(f"{role}_not_tracked_in_pinned_tree", stage="repository")
        if (
            tracked_file.sha256 != bindings[role]["sha256"]
            or tracked_file.bytes != bindings[role]["bytes"]
            or len(tracked_file.blob_oid) != 40
            or not set(tracked_file.blob_oid) <= _HEX40
        ):
            raise PreparerError(f"{role}_tracked_blob_content_mismatch", stage="repository")


def _validate_lineage(
    lineage: object,
    *,
    bindings: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if type(lineage) is not dict or set(lineage) != {"python", "powershell", "codex"}:
        raise PreparerError("lineage_keys_not_exact", stage="lineage")
    expected = {
        "python": bindings["interpreter"],
        "powershell": bindings["powershell"],
        "codex": bindings["codex"],
    }
    for role, binding in expected.items():
        record = lineage[role]
        token = record.get("token") if type(record) is dict else None
        if (
            type(record) is not dict
            or _normal(record.get("path", "")) != _normal(binding["final_path"])
            or record.get("image_sha256") != binding["sha256"]
            or type(token) is not dict
            or token.get("administrator") is not True
            or token.get("integrity") not in {"High", "System"}
            or token.get("token_elevation_type") != "Full"
        ):
            raise PreparerError(f"{role}_lineage_or_token_mismatch", stage="lineage")
    if (
        lineage["python"].get("ppid") != lineage["powershell"].get("pid")
        or lineage["powershell"].get("ppid") != lineage["codex"].get("pid")
        or lineage["codex"].get("danger_full_access_flag_present") is not True
        or lineage["codex"].get("approval_never_flag_present") is not True
        or lineage["codex"].get("command_line_persisted") is not False
    ):
        raise PreparerError("trusted_powershell_codex_lineage_required", stage="lineage")
    return {
        "schema": f"{PREPARER_RESULT_SCHEMA}.direct-lineage.v1",
        "direct_win32_measurement": True,
        "python_pid": lineage["python"]["pid"],
        "powershell_pid": lineage["powershell"]["pid"],
        "codex_pid": lineage["codex"]["pid"],
        "session_id": lineage["python"]["session_id"],
        "python_image_sha256": lineage["python"]["image_sha256"],
        "powershell_image_sha256": lineage["powershell"]["image_sha256"],
        "codex_image_sha256": lineage["codex"]["image_sha256"],
        "all_tokens_full_admin_high_or_system": True,
        "codex_danger_full_access": True,
        "codex_approval_never": True,
        "raw_command_line_persisted": False,
    }


def _source_closure(bindings: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    files = [
        {
            "role": role,
            "final_path": bindings[role]["final_path"],
            "sha256": bindings[role]["sha256"],
            "bytes": bindings[role]["bytes"],
            "volume_serial_number": bindings[role]["volume_serial_number"],
            "file_id_hex": bindings[role]["file_id_hex"],
            "security_descriptor_sha256": bindings[role]["security_descriptor_sha256"],
            "creation_time_ns": bindings[role]["creation_time_ns"],
        }
        for role in work_order_gate.SOURCE_CLOSURE_ROLES
    ]
    value = {
        "schema": work_order_gate.SOURCE_CLOSURE_SCHEMA,
        "roles": list(work_order_gate.SOURCE_CLOSURE_ROLES),
        "files": files,
        "count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
    }
    value["inventory_sha256"] = _sha256(_canonical_json(value))
    return value


def _normalized_invocation(
    request: PrepareRequest,
    bindings: Mapping[str, Mapping[str, Any]],
    working_directory_identity: Mapping[str, Any],
    pycache_prefix: str,
) -> dict[str, Any]:
    value = {
        "schema": work_order_gate.INVOCATION_SCHEMA,
        "working_directory_identity": dict(working_directory_identity),
        "argv": [
            bindings["interpreter"]["final_path"],
            "-I",
            "-B",
            "-S",
            "-X",
            f"pycache_prefix={pycache_prefix}",
            bindings["fixture"]["final_path"],
            "--mode",
            "root",
            "--run-uuid",
            request.run_uuid,
            "--pycache-prefix",
            pycache_prefix,
            "--interpreter-sha256",
            bindings["interpreter"]["sha256"],
            "--fixture-sha256",
            bindings["fixture"]["sha256"],
            "--command-processor",
            bindings["command_processor"]["final_path"],
            "--command-processor-sha256",
            bindings["command_processor"]["sha256"],
        ],
        "absolute_path_argument_indexes": [0, 6, 12, 18],
        "pycache_prefix_argument_index": 5,
    }
    value["canonical_sha256"] = _sha256(_canonical_json(value))
    return value


def _expected_work_order_path(request: PrepareRequest) -> str:
    return ntpath.join(
        CANONICAL_WORK_ORDER_ROOT,
        (f"windows-qualification-work-order-{request.run_uuid}-{request.attempt_uuid}.json"),
    )


def _validate_publication(
    publication: PublicationObservation,
    *,
    raw: bytes,
    expected_path: str,
    expected_parent_identity: Mapping[str, Any],
) -> None:
    if (
        type(publication) is not PublicationObservation
        or _normal(publication.final_path) != _normal(expected_path)
        or publication.sha256 != _sha256(raw)
        or publication.bytes != len(raw)
        or publication.file_flush_count < 2
        or publication.directory_flush_count != 1
        or publication.directory_flush_succeeded is not True
        or publication.same_handle_readback is not True
        or publication.file_identity_stable_across_rename is not True
        or publication.replace_if_exists is not False
        or publication.create_attempt_count != 1
        or type(publication.file_identity) is not dict
        or _normal(str(publication.file_identity.get("final_path", ""))) != _normal(expected_path)
    ):
        raise PreparerError("work_order_publication_contract_mismatch", stage="publication")
    observed_directory = publication.directory_identity
    comparable_keys = {
        "volume_serial_number",
        "file_id_hex",
        "owner_sid",
        "security_descriptor_sha256",
        "dacl_present",
        "dacl_protected",
        "link_count",
        "reparse_tag",
        "file_type",
    }
    if (
        type(observed_directory) is not dict
        or not comparable_keys <= set(observed_directory)
        or _normal(str(observed_directory.get("final_path", "")))
        != _normal(str(expected_parent_identity["final_path"]))
        or any(observed_directory[key] != expected_parent_identity[key] for key in comparable_keys)
    ):
        raise PreparerError("publication_parent_identity_mismatch", stage="publication")


def prepare_internal_non_authoritative_once(
    request: PrepareRequest,
    *,
    repository: RepositoryInspector,
    identities: IdentityInspector,
    lineage: LineageInspector,
    publisher: Publisher,
    path_exists: Any = os.path.lexists,
) -> dict[str, Any]:
    """Prepare one candidate; adapters are explicit to support no-live tests."""

    _validate_request(request)
    expected_prefix = ntpath.join(
        CANONICAL_PYCACHE_ROOT,
        f"{request.run_uuid}-{request.attempt_uuid}",
    )
    if _normal(sys.pycache_prefix or "") != _normal(expected_prefix):
        raise PreparerError("preparer_pycache_prefix_run_identity_mismatch", stage="bootstrap")
    work_order_path = _expected_work_order_path(request)
    reservation_paths = (
        work_order_path,
        ntpath.join(CANONICAL_OUTPUT_ROOT, request.run_uuid),
        ntpath.join(
            CANONICAL_OUTPUT_ROOT,
            f"{request.run_uuid}.reservation.json",
        ),
        expected_prefix,
    )
    if any(path_exists(path) for path in reservation_paths):
        raise PreparerError("run_or_work_order_identity_collision", stage="collision")
    observation = repository.inspect(PROJECT_ROOT)
    _validate_repository(request, observation)
    paths = _project_paths(request)
    _validate_preimport_project_sources(request, observation, paths)
    _load_verified_project_modules()
    output_parent_identity = _measure_external_canonical_parent(
        identities,
        role="qualification:output_parent",
        path=CANONICAL_OUTPUT_ROOT,
    )
    pycache_parent_identity = _measure_external_canonical_parent(
        identities,
        role="qualification:pycache_parent",
        path=CANONICAL_PYCACHE_ROOT,
    )
    work_order_parent_identity = _measure_external_canonical_parent(
        identities,
        role="qualification:work_order_parent",
        path=CANONICAL_WORK_ORDER_ROOT,
    )
    bindings = {
        role: identities.file(role, paths[role]) for role in work_order_gate.FILE_BINDING_ROLES
    }
    expected_pins = {
        "interpreter": request.interpreter,
        "powershell": request.powershell,
        "codex": request.codex,
        "command_processor": request.command_processor,
        "preparer": ExecutablePin(paths["preparer"], request.expected_preparer_sha256),
    }
    for role, pin in expected_pins.items():
        if (
            _normal(bindings[role]["final_path"]) != _normal(pin.path)
            or bindings[role]["sha256"] != pin.sha256
        ):
            raise PreparerError(f"{role}_pin_mismatch", stage="identity")
    _validate_project_bindings_are_pinned_tree_blobs(observation, paths, bindings)
    working_directory_identity = identities.directory(
        "qualification:working_directory", str(PROJECT_ROOT)
    )
    lineage_evidence = _validate_lineage(lineage.measure(), bindings=bindings)
    normalized_invocation = _normalized_invocation(
        request,
        bindings,
        working_directory_identity,
        expected_prefix,
    )
    work_order = {
        "schema": work_order_gate.WORK_ORDER_SCHEMA,
        "status": "internal_non_authoritative",
        "decision": "NO-GO",
        "credit": "zero_credit",
        "authority_scope": work_order_gate.AUTHORITY_SCOPE,
        "authority_verified": False,
        "external_authority_verified": False,
        "production_go": False,
        "go_evidence_eligible": False,
        "global_run_id": request.global_run_id,
        "run_uuid": request.run_uuid,
        "attempt_uuid": request.attempt_uuid,
        "commit": request.expected_commit,
        "tree": request.expected_tree,
        "qualification_mode": work_order_gate.QUALIFICATION_MODE,
        "file_bindings": bindings,
        "source_closure": _source_closure(bindings),
        "preserved_untracked_inventory": _preserved_untracked_inventory(observation),
        "normalized_invocation": normalized_invocation,
        "pycache_prefix": expected_prefix,
        "pycache_parent_identity": pycache_parent_identity,
        "pycache_prefix_initially_absent": True,
        "pycache_prefix_postcondition_absent": True,
        "output_root": CANONICAL_OUTPUT_ROOT,
        "output_parent_identity": output_parent_identity,
        "work_order_path": work_order_path,
        "work_order_parent_identity": work_order_parent_identity,
        "same_token_hostile_admin_protected": False,
        "toolchain_runtime_closure_state": "unproven",
        "reviewer_blockers": REVIEWER_BLOCKERS,
    }
    raw = _canonical_json(work_order)
    expectation = work_order_gate.QualificationWorkOrderExpectation(
        work_order_sha256=_sha256(raw),
        global_run_id=request.global_run_id,
        run_uuid=request.run_uuid,
        attempt_uuid=request.attempt_uuid,
        commit=request.expected_commit,
        tree=request.expected_tree,
    )
    work_order_gate.verify_internal_qualification_work_order(raw, expected=expectation)
    if any(path_exists(path) for path in reservation_paths):
        raise PreparerError("run_or_work_order_identity_collision", stage="prepublication")
    try:
        publication = publisher.publish(
            root=CANONICAL_WORK_ORDER_ROOT,
            leaf=ntpath.basename(work_order_path),
            raw=raw,
            run_uuid=request.run_uuid,
        )
    except BaseException as exc:
        error = PreparerError("work_order_publication_failed", stage="publication")
        error.call_counts["work_order_publication"] = 1
        raise error from exc
    _validate_publication(
        publication,
        raw=raw,
        expected_path=work_order_path,
        expected_parent_identity=work_order_parent_identity,
    )
    return {
        "schema": PREPARER_RESULT_SCHEMA,
        "status": "internal_non_authoritative",
        "decision": "NO-GO",
        "credit": "zero_credit",
        "reviewer_pending": True,
        "external_authority_verified": False,
        "production_go": False,
        "go_evidence_eligible": False,
        "same_token_hostile_admin_protected": False,
        "toolchain_runtime_closure_state": "unproven",
        "reviewer_blockers": REVIEWER_BLOCKERS,
        "global_run_id": request.global_run_id,
        "run_uuid": request.run_uuid,
        "attempt_uuid": request.attempt_uuid,
        "commit": request.expected_commit,
        "tree": request.expected_tree,
        "repository": asdict(observation),
        "canonical_parent_preconditions": {
            "verified": True,
            "provisioning_performed_by_preparer": False,
            "contract": canonical_parent_provisioning_contract(),
        },
        "lineage": lineage_evidence,
        "work_order_path": work_order_path,
        "work_order_sha256": _sha256(raw),
        "work_order_bytes": len(raw),
        "publication": asdict(publication),
        "call_counts": {
            "repository_process_creation": 0,
            "qualification_process_creation": 0,
            "work_order_publication": 1,
            "automatic_retry": 0,
            "force_termination": 0,
            "success_marker": 0,
            "completion_marker": 0,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--global-run-id", required=True)
    parser.add_argument("--run-uuid", required=True)
    parser.add_argument("--attempt-uuid", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-tree", required=True)
    parser.add_argument("--expected-preparer-sha256", required=True)
    parser.add_argument("--expected-untracked-count", required=True, type=int)
    parser.add_argument("--expected-untracked-bytes", required=True, type=int)
    parser.add_argument("--expected-untracked-inventory-sha256", required=True)
    for name in ("interpreter", "powershell", "codex", "command-processor"):
        parser.add_argument(f"--{name}", required=True)
        parser.add_argument(f"--{name}-sha256", required=True)
    return parser


def _request(args: argparse.Namespace) -> PrepareRequest:
    return PrepareRequest(
        global_run_id=args.global_run_id,
        run_uuid=args.run_uuid,
        attempt_uuid=args.attempt_uuid,
        expected_commit=args.expected_commit,
        expected_tree=args.expected_tree,
        expected_preparer_sha256=args.expected_preparer_sha256,
        expected_untracked_count=args.expected_untracked_count,
        expected_untracked_bytes=args.expected_untracked_bytes,
        expected_untracked_inventory_sha256=args.expected_untracked_inventory_sha256,
        interpreter=ExecutablePin(args.interpreter, args.interpreter_sha256),
        powershell=ExecutablePin(args.powershell, args.powershell_sha256),
        codex=ExecutablePin(args.codex, args.codex_sha256),
        command_processor=ExecutablePin(args.command_processor, args.command_processor_sha256),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        result = prepare_internal_non_authoritative_once(
            _request(_parser().parse_args(argv)),
            repository=PureGitRepositoryInspector(),
            identities=Win32IdentityInspector(),
            lineage=DirectWin32LineageInspector(),
            publisher=DurableWorkOrderPublisher(),
        )
    except BaseException as exc:
        if isinstance(exc, PreparerError):
            code = exc.code
            stage = exc.stage
            counts = exc.call_counts
        else:
            code = type(exc).__name__
            stage = "unexpected"
            counts = PreparerError("unexpected", stage="unexpected").call_counts
        failure = {
            "schema": PREPARER_FAILURE_SCHEMA,
            "status": "reviewer_pending",
            "decision": "NO-GO",
            "credit": "zero_credit",
            "error": code,
            "stage": stage,
            "external_authority_verified": False,
            "production_go": False,
            "go_evidence_eligible": False,
            "canonical_parent_provisioning": canonical_parent_provisioning_contract(),
            "external_canonical_parent_provisioning_required": (
                code == "external_canonical_parent_provisioning_required"
            ),
            "call_counts": counts,
        }
        sys.stderr.buffer.write(_canonical_json(failure))
        return 2
    sys.stdout.buffer.write(_canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
