from __future__ import annotations

import ctypes
import os
from dataclasses import replace
from pathlib import Path

import pytest

from evm.scale_validation import phase_b2_r7s3_handle_io as handle_io
from evm.scale_validation.phase_b2_r7s3_handle_io import (
    HandleBoundIoError,
    HandleIdentity,
    publish_bound_no_replace,
    read_bound_file,
    source_contract,
)


RUN_UUID = "97e44cb4-1ea6-4a39-96fb-46e4d5e77257"
SOURCE_PATH = r"C:\trusted\source.py"
DIRECTORY = r"C:\trusted\evidence"


def _identity(path: str, raw: bytes = b"payload\n") -> HandleIdentity:
    return HandleIdentity(
        final_path=path,
        volume_serial_number=123456,
        file_id_hex="11" * 16,
        size=len(raw),
        link_count=1,
        attributes=0x80,
        reparse_tag=0,
        file_type=1,
        owner_sid="S-1-5-32-544",
        security_descriptor_sha256="22" * 32,
        dacl_present=True,
        dacl_protected=True,
    )


def _directory_identity(path: str = DIRECTORY) -> HandleIdentity:
    return replace(
        _identity(path, b""),
        attributes=0x10,
        dacl_protected=False,
        link_count=1,
    )


class FakeApi:
    def __init__(
        self,
        *,
        raw: bytes = b"payload\n",
        identities: list[HandleIdentity] | None = None,
        directory_identity: HandleIdentity | None = None,
        create_error: Exception | None = None,
        protect_error: Exception | None = None,
        rename_error: Exception | None = None,
    ) -> None:
        self.raw = raw
        self.identities = list(identities or [_identity(SOURCE_PATH, raw)] * 2)
        self.directory_identity = directory_identity or _directory_identity()
        self.create_error = create_error
        self.protect_error = protect_error
        self.rename_error = rename_error
        self.calls: list[tuple[object, ...]] = []
        self.written = b""

    def open_read(self, path: str) -> int:
        self.calls.append(("open_read", path))
        return 10

    def open_directory(self, path: str) -> int:
        self.calls.append(("open_directory", path))
        return 20

    def create_relative_file(self, directory_handle: int, leaf: str) -> int:
        self.calls.append(("create_relative_file", directory_handle, leaf))
        if self.create_error:
            raise self.create_error
        return 30

    def protect_dacl(self, handle: int) -> None:
        self.calls.append(("protect_dacl", handle))
        if self.protect_error:
            raise self.protect_error

    def identity(self, handle: int) -> HandleIdentity:
        self.calls.append(("identity", handle))
        if handle == 20:
            return self.directory_identity
        if len(self.identities) > 1:
            return self.identities.pop(0)
        return self.identities[0]

    def read_all(self, handle: int, expected_size: int) -> bytes:
        self.calls.append(("read_all", handle, expected_size))
        return self.written if self.written else self.raw

    def write_all(self, handle: int, raw: bytes) -> None:
        self.calls.append(("write_all", handle, raw))
        self.written = raw

    def flush(self, handle: int) -> None:
        self.calls.append(("flush", handle))

    def rename_no_replace(self, handle: int, directory_handle: int, leaf: str) -> None:
        self.calls.append(("rename_no_replace", handle, directory_handle, leaf))
        if self.rename_error:
            raise self.rename_error

    def close(self, handle: int | None) -> None:
        self.calls.append(("close", handle))


def test_bound_read_uses_one_handle_for_identity_content_and_readback() -> None:
    api = FakeApi()

    result = read_bound_file(SOURCE_PATH, api=api)

    assert result.raw == b"payload\n"
    assert result.pin == {
        "path": SOURCE_PATH,
        "sha256": "d4e4877bac978b7952f0d544fc52ebff5411d351d129f1f056fa43f11da9af2b",
        "bytes": 8,
        "volume_serial_number": 123456,
        "file_id_hex": "11" * 16,
        "security_descriptor_sha256": "22" * 32,
    }
    assert [call[0] for call in api.calls] == [
        "open_read",
        "identity",
        "read_all",
        "identity",
        "close",
    ]
    assert {call[1] for call in api.calls if call[0] in {"identity", "read_all"}} == {10}


def test_expected_pin_is_exact_and_cannot_be_self_consistently_relaxed() -> None:
    api = FakeApi()
    result = read_bound_file(SOURCE_PATH, api=api)
    pin = dict(result.pin)
    pin["sha256"] = "33" * 32

    with pytest.raises(HandleBoundIoError, match="expected_pin_mismatch"):
        read_bound_file(SOURCE_PATH, expected_pin=pin, api=FakeApi())

    pin["unexpected"] = False
    with pytest.raises(HandleBoundIoError, match="expected_pin_fields_mismatch"):
        read_bound_file(SOURCE_PATH, expected_pin=pin, api=FakeApi())


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ({"reparse_tag": 0xA000000C}, "handle_reparse_tag_present"),
        ({"link_count": 2}, "handle_size_or_link_count_invalid"),
        ({"dacl_present": False}, "handle_owner_or_dacl_missing"),
        ({"dacl_protected": False}, "handle_dacl_not_protected"),
        ({"file_type": 3}, "handle_not_disk_file"),
        ({"final_path": r"C:\attacker\source.py"}, "handle_final_path_mismatch"),
    ],
)
def test_unsafe_handle_identity_is_rejected(mutation: dict[str, object], error: str) -> None:
    identity = replace(_identity(SOURCE_PATH), **mutation)
    with pytest.raises(HandleBoundIoError, match=error):
        read_bound_file(SOURCE_PATH, api=FakeApi(identities=[identity]))


def test_identity_swap_while_reading_is_rejected() -> None:
    first = _identity(SOURCE_PATH)
    swapped = replace(first, file_id_hex="44" * 16)
    with pytest.raises(HandleBoundIoError, match="handle_identity_changed_during_read"):
        read_bound_file(SOURCE_PATH, api=FakeApi(identities=[first, swapped]))


def test_publication_is_relative_exclusive_flushes_and_reads_same_file_handle() -> None:
    raw = b'{"status":"failure"}\n'
    partial = _identity(
        rf"{DIRECTORY}\.failure.json.{RUN_UUID}.partial",
        raw,
    )
    final = replace(partial, final_path=rf"{DIRECTORY}\failure.json")
    api = FakeApi(raw=raw, identities=[partial, final])

    result = publish_bound_no_replace(
        DIRECTORY,
        "failure.json",
        raw,
        run_uuid=RUN_UUID,
        api=api,
    )

    assert result.final_path == rf"{DIRECTORY}\failure.json"
    assert result.identity.file_id_hex == partial.file_id_hex
    assert [call[0] for call in api.calls] == [
        "open_directory",
        "identity",
        "create_relative_file",
        "protect_dacl",
        "write_all",
        "flush",
        "identity",
        "read_all",
        "rename_no_replace",
        "flush",
        "identity",
        "read_all",
        "identity",
        "close",
        "close",
    ]
    create = next(call for call in api.calls if call[0] == "create_relative_file")
    rename = next(call for call in api.calls if call[0] == "rename_no_replace")
    assert create[1] == rename[2] == 20
    assert rename[3] == "failure.json"
    assert all(call[1] == 30 for call in api.calls if call[0] in {"write_all", "read_all"})


def test_preexisting_partial_or_final_name_is_never_retried_or_replaced() -> None:
    api = FakeApi(create_error=FileExistsError("exclusive create collision"))
    with pytest.raises(FileExistsError, match="exclusive create collision"):
        publish_bound_no_replace(DIRECTORY, "failure.json", b"x", run_uuid=RUN_UUID, api=api)
    assert sum(call[0] == "create_relative_file" for call in api.calls) == 1
    assert sum(call[0] == "rename_no_replace" for call in api.calls) == 0


def test_directory_reparse_handle_is_rejected_before_create() -> None:
    directory = replace(_directory_identity(), reparse_tag=0xA000000C)
    api = FakeApi(directory_identity=directory)
    with pytest.raises(HandleBoundIoError, match="directory_handle_reparse_tag_present"):
        publish_bound_no_replace(DIRECTORY, "failure.json", b"x", run_uuid=RUN_UUID, api=api)
    assert sum(call[0] == "create_relative_file" for call in api.calls) == 0


def test_dacl_protection_failure_occurs_before_content_write() -> None:
    api = FakeApi(protect_error=PermissionError("protect denied"))
    with pytest.raises(PermissionError, match="protect denied"):
        publish_bound_no_replace(DIRECTORY, "failure.json", b"secret", run_uuid=RUN_UUID, api=api)
    assert sum(call[0] == "protect_dacl" for call in api.calls) == 1
    assert sum(call[0] == "write_all" for call in api.calls) == 0


def test_rename_failure_is_fail_closed_without_cleanup_or_retry() -> None:
    raw = b"payload\n"
    partial = _identity(rf"{DIRECTORY}\.failure.json.{RUN_UUID}.partial", raw)
    api = FakeApi(raw=raw, identities=[partial], rename_error=PermissionError("rename denied"))
    with pytest.raises(PermissionError, match="rename denied"):
        publish_bound_no_replace(DIRECTORY, "failure.json", raw, run_uuid=RUN_UUID, api=api)
    assert sum(call[0] == "rename_no_replace" for call in api.calls) == 1
    assert not any(call[0] in {"unlink", "remove", "replace"} for call in api.calls)


def test_module_contract_does_not_overclaim_same_token_admin_protection() -> None:
    contract = source_contract()
    assert contract["authority"] == "open_kernel_handle"
    assert contract["relative_create"] == "NtCreateFile.RootDirectory"
    assert contract["relative_rename"] == "NtSetInformationFile.RootDirectory"
    assert contract["replace_if_exists"] is False
    assert contract["created_file_dacl_protected_before_content"] is True
    assert contract["production_evidence_writer_integrated"] is False
    assert contract["directory_identity_bound"] is True
    assert contract["directory_metadata_flushed_after_rename"] is False
    assert contract["same_token_hostile_admin_protected"] is False
    assert contract["go_evidence_eligible"] is False


@pytest.mark.skipif(os.name != "nt", reason="requires Windows SDK ABI")
def test_file_standard_info_matches_windows_boolean_layout() -> None:
    structure = handle_io._FILE_STANDARD_INFO
    assert ctypes.sizeof(structure) == 24
    assert structure.number_of_links.offset == 16
    assert structure.delete_pending.offset == 20
    assert structure.directory.offset == 21


@pytest.mark.skipif(os.name != "nt", reason="requires Windows handle APIs")
def test_real_windows_relative_create_rename_and_same_handle_readback(tmp_path: Path) -> None:
    raw = b'{"schema":"r7s3-real-handle-smoke"}\n'

    publication = publish_bound_no_replace(
        tmp_path,
        "sealed.json",
        raw,
        run_uuid=RUN_UUID,
    )

    final = tmp_path / "sealed.json"
    assert final.read_bytes() == raw
    assert publication.bytes == len(raw)
    assert publication.identity.file_id_hex
    assert publication.identity.reparse_tag == 0
    assert publication.identity.dacl_protected is True
    assert publication.directory_identity.attributes & 0x10
    assert publication.directory_identity.reparse_tag == 0


def test_source_has_no_path_reopen_movefile_replace_or_delete_fallback() -> None:
    source = (
        Path(__file__).parents[1]
        / "src"
        / "evm"
        / "scale_validation"
        / "phase_b2_r7s3_handle_io.py"
    ).read_text(encoding="utf-8")
    forbidden = ("MoveFileEx", "os.replace", ".read_bytes()", "unlink(", "remove(")
    assert all(token not in source for token in forbidden)
