from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from pathlib import Path

import pytest

from evm.scale_validation import phase_b2_r7s3_handle_io as r7s3
from evm.scale_validation.phase_b2_r7s4_handle_io import (
    DurableHandleApi,
    DurablePublicationError,
    HandleBoundIoError,
    HandleIdentity,
    publish_bound_no_replace_durable,
    source_contract,
)


RUN_UUID = "354a301c-5dc1-4cb0-9348-a5d1b0db42b2"
DIRECTORY = r"C:\trusted\r7s4-evidence"
RAW = b'{"status":"review_pending"}\n'


def _file_identity(path: str) -> HandleIdentity:
    return HandleIdentity(
        final_path=path,
        volume_serial_number=20260902,
        file_id_hex="12" * 16,
        size=len(RAW),
        link_count=1,
        attributes=0x80,
        reparse_tag=0,
        file_type=1,
        owner_sid="S-1-5-32-544",
        security_descriptor_sha256="34" * 32,
        dacl_present=True,
        dacl_protected=True,
    )


def _directory_identity() -> HandleIdentity:
    return replace(
        _file_identity(DIRECTORY),
        attributes=0x10,
        size=0,
        dacl_protected=False,
        file_id_hex="56" * 16,
    )


class FakeDurableApi(DurableHandleApi):
    def __init__(
        self,
        *,
        directory_identities: list[HandleIdentity] | None = None,
        file_identities: list[HandleIdentity] | None = None,
        directory_flush_error: Exception | None = None,
        rename_error: Exception | None = None,
    ) -> None:
        temporary = _file_identity(rf"{DIRECTORY}\.review.json.{RUN_UUID}.partial")
        final = replace(temporary, final_path=rf"{DIRECTORY}\review.json")
        self.directory_identities = list(directory_identities or [_directory_identity()] * 2)
        self.file_identities = list(file_identities or [temporary, final])
        self.directory_flush_error = directory_flush_error
        self.rename_error = rename_error
        self.calls: list[tuple[object, ...]] = []
        self.written = b""

    def open_read(self, path: str) -> int:
        raise AssertionError(f"unexpected open_read:{path}")

    def open_directory(self, path: str) -> int:
        self.calls.append(("open_directory", path))
        return 20

    def create_relative_file(self, directory_handle: int, leaf: str) -> int:
        self.calls.append(("create_relative_file", directory_handle, leaf))
        return 30

    def protect_dacl(self, handle: int) -> None:
        self.calls.append(("protect_dacl", handle))

    def identity(self, handle: int) -> HandleIdentity:
        self.calls.append(("identity", handle))
        identities = self.directory_identities if handle == 20 else self.file_identities
        if len(identities) > 1:
            return identities.pop(0)
        return identities[0]

    def read_all(self, handle: int, expected_size: int) -> bytes:
        self.calls.append(("read_all", handle, expected_size))
        return self.written

    def write_all(self, handle: int, raw: bytes) -> None:
        self.calls.append(("write_all", handle, raw))
        self.written = raw

    def flush(self, handle: int) -> None:
        self.calls.append(("flush", handle))

    def flush_directory(self, handle: int) -> None:
        self.calls.append(("flush_directory", handle))
        if self.directory_flush_error is not None:
            raise self.directory_flush_error

    def rename_no_replace(self, handle: int, directory_handle: int, leaf: str) -> None:
        self.calls.append(("rename_no_replace", handle, directory_handle, leaf))
        if self.rename_error is not None:
            raise self.rename_error

    def close(self, handle: int | None) -> None:
        self.calls.append(("close", handle))


def test_success_requires_file_then_directory_flush_and_same_handle_readback() -> None:
    api = FakeDurableApi()

    result = publish_bound_no_replace_durable(
        DIRECTORY,
        "review.json",
        RAW,
        run_uuid=RUN_UUID,
        api=api,
    )

    names = [str(call[0]) for call in api.calls]
    assert names == [
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
        "flush_directory",
        "identity",
        "read_all",
        "identity",
        "close",
        "close",
    ]
    rename_index = names.index("rename_no_replace")
    assert names[rename_index + 1 : rename_index + 3] == ["flush", "flush_directory"]
    assert {call[1] for call in api.calls if call[0] in {"write_all", "read_all"}} == {30}
    assert result.file_flush_count == 2
    assert result.directory_flush_count == 1
    assert result.directory_flush_succeeded is True
    assert result.power_loss_durability_proven is False
    assert result.same_token_hostile_admin_protected is False
    assert result.go_evidence_eligible is False


def test_directory_flush_failure_is_terminal_without_retry_or_final_readback() -> None:
    api = FakeDurableApi(directory_flush_error=OSError("directory flush denied"))

    with pytest.raises(DurablePublicationError) as captured:
        publish_bound_no_replace_durable(
            DIRECTORY,
            "review.json",
            RAW,
            run_uuid=RUN_UUID,
            api=api,
        )

    names = [call[0] for call in api.calls]
    assert names.count("create_relative_file") == 1
    assert names.count("rename_no_replace") == 1
    assert names.count("flush_directory") == 1
    assert isinstance(captured.value.__cause__, OSError)
    assert captured.value.observation.stage == "flush_directory"
    assert captured.value.observation.rename_completed is True
    assert captured.value.observation.observation_status == "same_handle_observed"
    assert captured.value.observation.current_sha256 == hashlib.sha256(RAW).hexdigest()
    assert names.count("read_all") == 2
    assert not any(name in {"unlink", "remove", "replace", "retry"} for name in names)


def test_destination_collision_is_not_replaced_retried_or_cleaned() -> None:
    api = FakeDurableApi(rename_error=FileExistsError("destination exists"))

    with pytest.raises(DurablePublicationError) as captured:
        publish_bound_no_replace_durable(
            DIRECTORY,
            "review.json",
            RAW,
            run_uuid=RUN_UUID,
            api=api,
        )

    assert isinstance(captured.value.__cause__, FileExistsError)
    assert captured.value.observation.stage == "rename_no_replace"
    assert captured.value.observation.rename_completed is False
    names = [call[0] for call in api.calls]
    assert names.count("rename_no_replace") == 1
    assert "flush_directory" not in names
    assert not any(name in {"unlink", "remove", "replace", "retry"} for name in names)


def test_directory_identity_swap_after_flush_is_rejected() -> None:
    before = _directory_identity()
    after = replace(before, file_id_hex="78" * 16)
    api = FakeDurableApi(directory_identities=[before, after])

    with pytest.raises(DurablePublicationError) as captured:
        publish_bound_no_replace_durable(
            DIRECTORY,
            "review.json",
            RAW,
            run_uuid=RUN_UUID,
            api=api,
        )
    assert isinstance(captured.value.__cause__, HandleBoundIoError)
    assert "directory_handle_identity_changed:file_id_hex" in str(captured.value.__cause__)


def test_contract_explicitly_excludes_stronger_durability_and_authority_claims() -> None:
    contract = source_contract()
    assert contract["directory_handle_flush_after_rename"] is True
    assert contract["directory_flush_success_required"] is True
    assert contract["output_directory_parent_metadata_flushed"] is False
    assert contract["power_loss_durability_proven"] is False
    assert contract["same_token_hostile_admin_protected"] is False
    assert contract["legacy_evidence_writers_modified"] is False
    assert contract["ubuntu_ci_executes_windows_handle_tests"] is False
    assert contract["separate_local_windows_evidence_required"] is True
    assert contract["production_go_enabled"] is False
    assert contract["go_evidence_eligible"] is False


@pytest.mark.parametrize(
    "leaf",
    [
        "report.json:stream",
        "CON",
        "nul.json",
        "COM1.txt",
        "lpt9",
        "../report.json",
        r"C:\absolute.json",
        r"nested\report.json",
        "nested/report.json",
        "trailing.",
        "trailing ",
        "control\x01.json",
        "wild*.json",
        "question?.json",
        "a" * 181,
    ],
)
def test_strict_windows_leaf_rejects_ads_devices_controls_and_paths(leaf: str) -> None:
    api = FakeDurableApi()
    with pytest.raises(HandleBoundIoError, match="strict_windows_leaf"):
        publish_bound_no_replace_durable(
            DIRECTORY,
            leaf,
            RAW,
            run_uuid=RUN_UUID,
            api=api,
        )
    assert api.calls == []


@pytest.mark.skipif(os.name != "nt", reason="requires Windows directory handle APIs")
def test_real_windows_relative_publication_flushes_directory_handle(tmp_path: Path) -> None:
    raw = b'{"schema":"r7s4-real-directory-flush"}\n'

    result = publish_bound_no_replace_durable(
        tmp_path,
        "review.json",
        raw,
        run_uuid=RUN_UUID,
    )

    assert (tmp_path / "review.json").read_bytes() == raw
    assert result.directory_flush_count == 1
    assert result.directory_flush_succeeded is True
    assert result.identity.file_id_hex
    assert result.directory_identity.file_id_hex
    assert result.go_evidence_eligible is False


@pytest.mark.skipif(os.name != "nt", reason="requires Windows directory handle APIs")
def test_real_windows_existing_destination_is_unchanged_and_partial_is_preserved(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "review.json"
    existing_raw = b'{"user_owned":true}\n'
    attempted_raw = b'{"replacement":true}\n'
    destination.write_bytes(existing_raw)
    before = r7s3.read_bound_file(destination, require_protected_dacl=False)

    with pytest.raises(DurablePublicationError) as captured:
        publish_bound_no_replace_durable(
            tmp_path,
            destination.name,
            attempted_raw,
            run_uuid=RUN_UUID,
        )

    after = r7s3.read_bound_file(destination, require_protected_dacl=False)
    partial = tmp_path / f".{destination.name}.{RUN_UUID}.partial"
    assert after.raw == existing_raw
    assert after.sha256 == before.sha256
    assert after.identity == before.identity
    assert partial.read_bytes() == attempted_raw
    observation = captured.value.observation
    assert observation.stage == "rename_no_replace"
    assert observation.rename_completed is False
    assert observation.observation_status == "same_handle_observed"
    assert observation.current_sha256 == hashlib.sha256(attempted_raw).hexdigest()
    assert observation.current_bytes == len(attempted_raw)
    assert observation.retry_allowed is False
    assert observation.cleanup_attempted is False
