from __future__ import annotations

import dis
import hashlib
import inspect
import json
import os
import stat
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from evm.scale_validation import phase_b2_r7s4_evidence as r7s4
from evm.scale_validation import phase_b2_r7s6_evidence as evidence
from evm.scale_validation.phase_b2_r7s3_handle_io import HandleIdentity
from evm.scale_validation.phase_b2_r7s4_handle_io import (
    DurableBoundPublication,
    DurablePublicationError,
    PublicationFailureObservation,
)
from evm.scale_validation.phase_b2_r7s6_evidence import (
    EMERGENCY_SEAL_LEAF,
    FAILURE_SEAL_LEAF,
    IDENTITY_INDEX_LEAF,
    IDENTITY_MANIFEST_LEAF,
    RUN_RESERVATION_LEAF,
    R7S6EvidenceError,
    R7S6EvidencePublicationError,
    _NoMutationWindowsHandleApi,
    _checked_lstat,
    _publish_pre_serialized_batch_for_test,
    _verify_final_handle_bound,
    canonical_json_bytes,
    publish_pre_serialized_batch,
    source_contract,
)


RUN_UUID = "b2ad7fd0-7d34-4a3e-a670-70fa997a9513"


class FakePublisher:
    def __init__(
        self,
        *,
        fail_on: str | None = None,
        leave_partial_and_fail_on: str | None = None,
    ) -> None:
        self.fail_on = fail_on
        self.leave_partial_and_fail_on = leave_partial_and_fail_on
        self.calls: list[tuple[Path, str]] = []

    def __call__(
        self,
        directory: Path,
        leaf: str,
        raw: bytes,
        *,
        run_uuid: str,
        api: object | None,
    ) -> DurableBoundPublication:
        del api
        self.calls.append((directory, leaf))
        if leaf == self.fail_on:
            raise OSError(f"injected:{leaf}")
        if leaf == self.leave_partial_and_fail_on:
            partial = directory / f".{leaf}.{run_uuid}.partial"
            partial.write_bytes(raw[: max(1, len(raw) // 2)])
            raise OSError(f"injected_after_partial:{leaf}")
        final = directory / leaf
        with final.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        final_path = str(final.resolve())
        directory_path = str(directory.resolve())
        identity = HandleIdentity(
            final_path=final_path,
            volume_serial_number=20260902,
            file_id_hex=hashlib.sha256(final_path.encode()).hexdigest()[:32],
            size=len(raw),
            link_count=1,
            attributes=0x80,
            reparse_tag=0,
            file_type=1,
            owner_sid="S-1-5-32-544",
            security_descriptor_sha256="cd" * 32,
            dacl_present=True,
            dacl_protected=True,
        )
        directory_identity = HandleIdentity(
            final_path=directory_path,
            volume_serial_number=20260902,
            file_id_hex=hashlib.sha256(directory_path.encode()).hexdigest()[:32],
            size=0,
            link_count=1,
            attributes=0x10,
            reparse_tag=0,
            file_type=1,
            owner_sid="S-1-5-32-544",
            security_descriptor_sha256="ef" * 32,
            dacl_present=True,
            dacl_protected=True,
        )
        return DurableBoundPublication(
            final_path=final_path,
            temporary_leaf=f".{leaf}.{run_uuid}.partial",
            sha256=hashlib.sha256(raw).hexdigest(),
            bytes=len(raw),
            identity=identity,
            directory_identity=directory_identity,
            file_flush_count=2,
            directory_flush_count=1,
            directory_flush_succeeded=True,
        )


def _publish(tmp_path: Path, publisher: FakePublisher, **kwargs: Any):
    return _publish_pre_serialized_batch_for_test(
        tmp_path,
        "r7s6-review",
        {
            "windows.json": {"domain": "windows", "sequence": [0, 1]},
            "wsl.json": {"domain": "wsl", "sequence": [0, 1]},
        },
        run_uuid=RUN_UUID,
        publisher=publisher,
        **kwargs,
    )


def test_every_success_json_exists_before_final_directory_is_published(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher()
    observed: dict[str, object] = {}

    def publish_directory(staging: Path, final: Path) -> None:
        assert not final.exists()
        leaves = sorted(path.name for path in staging.iterdir())
        assert leaves == sorted(
            ["windows.json", "wsl.json", IDENTITY_MANIFEST_LEAF, IDENTITY_INDEX_LEAF]
        )
        for path in staging.iterdir():
            json.loads(path.read_bytes())
        observed["all_json_serialized_before_final"] = True
        os.rename(staging, final)

    result = _publish(tmp_path, publisher, directory_publisher=publish_directory)

    assert observed == {"all_json_serialized_before_final": True}
    assert result.output_directory.is_dir()
    assert not result.staging_directory.exists()
    assert result.all_success_json_serialized_before_final_directory is True
    assert [leaf for _, leaf in publisher.calls] == [
        RUN_RESERVATION_LEAF,
        "windows.json",
        "wsl.json",
        IDENTITY_MANIFEST_LEAF,
        IDENTITY_INDEX_LEAF,
    ]
    assert result.reservation_directory.is_dir()
    assert (
        result.reservation_publication.sha256
        == hashlib.sha256(
            (result.reservation_directory / RUN_RESERVATION_LEAF).read_bytes()
        ).hexdigest()
    )
    assert all(
        Path(item.final_path).parent == result.staging_directory for item in result.publications
    )
    assert result.publication_identity_scope == (
        "staging_same_handle_before_parent_directory_rename"
    )
    assert result.publication_paths_rebased_to_final is False
    assert result.post_rename_handle_identity_verified is False
    assert result.final_verification.test_only_path_readback is True
    assert result.final_verification.kernel_handle_bound is False
    index = json.loads((result.output_directory / IDENTITY_INDEX_LEAF).read_bytes())
    assert index["all_success_json_serialized_before_final_directory"] is True
    assert index["run_reservation"]["key_scope"] == "parent_global_run_uuid_only"
    assert index["publication_paths_rebased_to_final"] is False
    assert not (result.output_directory / "completion-marker.json").exists()


def test_late_manifest_serialization_failure_preserves_staging_and_uses_sibling_seal(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher()

    def fail_serializer(value: Any) -> bytes:
        del value
        raise PermissionError("injected")

    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publisher, aggregate_serializer=fail_serializer)

    failure = captured.value
    assert failure.stage == "aggregate_manifest_serialization"
    assert not failure.output_directory.exists()
    assert failure.staging_directory.is_dir()
    assert sorted(path.name for path in failure.staging_directory.iterdir()) == [
        "windows.json",
        "wsl.json",
    ]
    assert failure.failure_seal_directory is not None
    seal_path = failure.failure_seal_directory / FAILURE_SEAL_LEAF
    seal = json.loads(seal_path.read_bytes())
    assert seal["partial_artifact_policy"] == "preserved_unmodified_and_sha_referenced"
    assert seal["run_reservation"]["key_scope"] == "parent_global_run_uuid_only"
    assert seal["run_reservation"]["inventory"][0]["leaf"] == RUN_RESERVATION_LEAF
    assert [item["leaf"] for item in seal["partial_inventory"]] == [
        "windows.json",
        "wsl.json",
    ]
    assert failure.automatic_retry_count == 0
    assert failure.cleanup_or_overwrite_attempted is False
    assert failure.reservation_directory.is_dir()
    assert failure.reservation_publication is not None


@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
def test_late_serialization_base_exception_is_fail_closed_and_sealed(
    tmp_path: Path,
    interruption_type: type[BaseException],
) -> None:
    publisher = FakePublisher()

    def interrupt_serializer(value: Any) -> bytes:
        del value
        raise interruption_type()

    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publisher, aggregate_serializer=interrupt_serializer)

    failure = captured.value
    assert failure.stage == "aggregate_manifest_serialization"
    assert failure.failure_seal_directory is not None
    seal = json.loads((failure.failure_seal_directory / FAILURE_SEAL_LEAF).read_bytes())
    assert seal["exception_type"].endswith(interruption_type.__qualname__)
    assert seal["success_marker_created"] is False
    assert failure.staging_directory.is_dir()
    assert not failure.output_directory.exists()


def test_final_rename_failure_preserves_complete_staging_without_success_marker(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher()

    def fail_rename(staging: Path, final: Path) -> None:
        assert staging.is_dir()
        assert not final.exists()
        raise PermissionError("rename denied")

    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publisher, directory_publisher=fail_rename)

    failure = captured.value
    assert failure.stage == "final_directory_publish"
    assert failure.staging_directory.is_dir()
    assert not failure.output_directory.exists()
    assert len(failure.partial_inventory) == 4
    assert {item["leaf"] for item in failure.partial_inventory} == {
        "windows.json",
        "wsl.json",
        IDENTITY_MANIFEST_LEAF,
        IDENTITY_INDEX_LEAF,
    }
    assert all(item["sha256"] for item in failure.partial_inventory)
    assert not (failure.staging_directory / "completion-marker.json").exists()


def test_rename_then_raise_seals_pre_rename_sha_inventory_without_following_final(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher()

    def rename_then_raise(staging: Path, final: Path) -> None:
        os.rename(staging, final)
        raise OSError("injected_after_successful_rename")

    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publisher, directory_publisher=rename_then_raise)

    failure = captured.value
    assert failure.stage == "final_directory_publish"
    assert not failure.staging_directory.exists()
    assert failure.output_directory.is_dir()
    assert failure.partial_inventory == ()
    assert failure.writer_owned_final_inventory == ()
    known = failure.last_known_pre_rename_inventory
    assert len(known) == 4
    assert {item["leaf"] for item in known} == {
        "windows.json",
        "wsl.json",
        IDENTITY_MANIFEST_LEAF,
        IDENTITY_INDEX_LEAF,
    }
    assert all(item["sha256"] and item["bytes"] > 0 for item in known)
    assert all(item["state"] == "durably_published_in_staging_before_rename" for item in known)
    assert all(item["path_followed_after_rename_failure"] is False for item in known)
    observation = failure.untrusted_output_observation
    assert observation["scope"] == "untrusted_path_lstat_only"
    assert observation["children_enumerated"] is False
    assert observation["content_read"] is False
    seal = json.loads((failure.failure_seal_directory / FAILURE_SEAL_LEAF).read_bytes())
    assert seal["last_known_pre_rename_inventory"] == list(known)
    assert seal["partial_artifact_policy"] == (
        "final_directory_present_content_inventory_unproven_last_known_pre_rename_sha_available"
    )
    assert seal["partial_artifact_evidence"]["writer_owned_staging_path_present"] is False
    assert seal["partial_artifact_evidence"]["final_path_observation"]["object_type"] == (
        "directory"
    )


def test_failure_seal_failure_creates_one_upper_emergency_seal(tmp_path: Path) -> None:
    publisher = FakePublisher()
    failure_publisher = FakePublisher(fail_on=FAILURE_SEAL_LEAF)
    emergency_publisher = FakePublisher()

    def fail_rename(staging: Path, final: Path) -> None:
        del staging, final
        raise OSError("injected")

    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(
            tmp_path,
            publisher,
            failure_publisher=failure_publisher,
            emergency_publisher=emergency_publisher,
            directory_publisher=fail_rename,
        )

    failure = captured.value
    assert failure.failure_seal_error_type == "builtins.OSError"
    assert failure.emergency_seal_directory is not None
    assert failure.emergency_seal_publication is not None
    assert [leaf for _, leaf in failure_publisher.calls] == [FAILURE_SEAL_LEAF]
    assert [leaf for _, leaf in emergency_publisher.calls] == [EMERGENCY_SEAL_LEAF]
    emergency = json.loads((failure.emergency_seal_directory / EMERGENCY_SEAL_LEAF).read_bytes())
    assert emergency["automatic_retry_count"] == 0
    assert emergency["success_marker_created"] is False
    assert emergency["run_reservation"]["key_scope"] == "parent_global_run_uuid_only"
    assert emergency["failure_seal_partial_inventory"] == []


def test_failure_seal_keyboard_interrupt_creates_upper_emergency_seal(tmp_path: Path) -> None:
    publisher = FakePublisher()
    emergency_publisher = FakePublisher()

    class InterruptFailurePublisher(FakePublisher):
        def __call__(self, *args: Any, **kwargs: Any) -> DurableBoundPublication:
            self.calls.append((Path(args[0]), str(args[1])))
            raise KeyboardInterrupt()

    failure_publisher = InterruptFailurePublisher()

    def fail_rename(_staging: Path, _final: Path) -> None:
        raise PermissionError("rename denied")

    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(
            tmp_path,
            publisher,
            failure_publisher=failure_publisher,
            emergency_publisher=emergency_publisher,
            directory_publisher=fail_rename,
        )

    failure = captured.value
    assert failure.failure_seal_error_type == "builtins.KeyboardInterrupt"
    assert failure.emergency_seal_publication is not None
    emergency = json.loads((failure.emergency_seal_directory / EMERGENCY_SEAL_LEAF).read_bytes())
    assert emergency["failure_seal_exception_type"] == "builtins.KeyboardInterrupt"
    assert emergency["success_marker_created"] is False


def test_emergency_seal_sha_inventories_failure_seal_partial_directory(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher()
    failure_publisher = FakePublisher(leave_partial_and_fail_on=FAILURE_SEAL_LEAF)
    emergency_publisher = FakePublisher()

    def fail_rename(staging: Path, final: Path) -> None:
        del staging, final
        raise OSError("injected")

    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(
            tmp_path,
            publisher,
            failure_publisher=failure_publisher,
            emergency_publisher=emergency_publisher,
            directory_publisher=fail_rename,
        )

    failure = captured.value
    assert len(failure.failure_seal_partial_inventory) == 1
    partial = failure.failure_seal_partial_inventory[0]
    assert partial["leaf"].endswith(".partial")
    assert partial["status"] == "read_back"
    assert partial["sha256"] is not None
    emergency = json.loads((failure.emergency_seal_directory / EMERGENCY_SEAL_LEAF).read_bytes())
    assert emergency["failure_seal_partial_inventory"] == [partial]
    assert emergency["writer_owned_staging_inventory"] == list(
        failure.writer_owned_staging_inventory
    )


def test_reservation_publication_partial_is_referenced_by_failure_seal(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher(leave_partial_and_fail_on=RUN_RESERVATION_LEAF)
    failure_publisher = FakePublisher()

    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(
            tmp_path,
            publisher,
            failure_publisher=failure_publisher,
        )

    failure = captured.value
    assert failure.stage == "run_uuid_reservation_publication"
    assert failure.reservation_publication is None
    assert len(failure.reservation_inventory) == 1
    assert failure.reservation_inventory[0]["leaf"].endswith(".partial")
    assert failure.reservation_inventory[0]["sha256"] is not None
    seal = json.loads((failure.failure_seal_directory / FAILURE_SEAL_LEAF).read_bytes())
    assert seal["run_reservation"]["inventory"] == list(failure.reservation_inventory)
    assert seal["run_reservation"]["publication"] is None


@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
def test_reservation_serialization_interruption_is_sealed_after_exclusive_mkdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption_type: type[BaseException],
) -> None:
    publisher = FakePublisher()
    real_serializer = evidence.canonical_json_bytes
    calls = 0

    def interrupt_first(value: Any) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise interruption_type("reservation serialization interrupted")
        return real_serializer(value)

    monkeypatch.setattr(evidence, "canonical_json_bytes", interrupt_first)
    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publisher)

    failure = captured.value
    assert failure.stage == "run_uuid_reservation_serialization"
    assert failure.reservation_directory.is_dir()
    assert failure.failure_seal_publication is not None
    seal = json.loads((failure.failure_seal_directory / FAILURE_SEAL_LEAF).read_bytes())
    assert seal["exception_type"] == f"builtins.{interruption_type.__name__}"
    assert seal["success_marker_created"] is False


@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
def test_reservation_mkdir_then_interruption_is_sealed_without_mutating_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption_type: type[BaseException],
) -> None:
    publisher = FakePublisher()
    real_mkdir = evidence.os.mkdir

    def mkdir_then_interrupt(path: str | os.PathLike[str], *args: Any, **kwargs: Any) -> None:
        real_mkdir(path, *args, **kwargs)
        if Path(path).name.endswith(".reservation"):
            raise interruption_type("after reservation mkdir")

    monkeypatch.setattr(evidence.os, "mkdir", mkdir_then_interrupt)
    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publisher)

    failure = captured.value
    assert failure.stage == "run_uuid_reservation_create"
    assert failure.reservation_directory.is_dir()
    assert list(failure.reservation_directory.iterdir()) == []
    assert failure.failure_seal_publication is not None
    seal = json.loads((failure.failure_seal_directory / FAILURE_SEAL_LEAF).read_bytes())
    assert seal["exception_type"] == f"builtins.{interruption_type.__name__}"
    assert seal["success_marker_created"] is False


@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
def test_post_reservation_state_preparation_interruption_is_sealed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption_type: type[BaseException],
) -> None:
    fallback = FakePublisher()

    def interrupt_copy(
        _prepared: object,
    ) -> list[r7s4.PreparedJsonArtifact]:
        raise interruption_type("post-reservation state preparation interrupted")

    def publish_then_arm_interruption(*args: Any, **kwargs: Any) -> DurableBoundPublication:
        publication = fallback(*args, **kwargs)
        if str(args[1]) == RUN_RESERVATION_LEAF:
            monkeypatch.setattr(evidence, "_prepared_success_artifact_list", interrupt_copy)
        return publication

    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publish_then_arm_interruption)  # type: ignore[arg-type]

    failure = captured.value
    assert failure.stage == "post_reservation_state_initialization"
    assert failure.reservation_publication is not None
    assert failure.reservation_directory.is_dir()
    assert failure.failure_seal_publication is not None
    assert not failure.staging_directory.exists()
    seal = json.loads((failure.failure_seal_directory / FAILURE_SEAL_LEAF).read_bytes())
    assert seal["exception_type"] == f"builtins.{interruption_type.__name__}"
    assert seal["success_marker_created"] is False


def test_continuation_dispatch_bytecode_is_inside_reservation_exception_entry() -> None:
    instructions = list(dis.get_instructions(evidence._publish_pre_serialized_batch))
    load_index = next(
        index
        for index, instruction in enumerate(instructions)
        if instruction.opname == "LOAD_GLOBAL"
        and instruction.argval == "_publish_after_reservation"
    )
    call_index = next(
        index
        for index in range(load_index + 1, len(instructions))
        if instructions[index].opname == "CALL_FUNCTION_EX"
    )
    dispatch = instructions[load_index : call_index + 1]
    entries = tuple(dis.Bytecode(evidence._publish_pre_serialized_batch).exception_entries)

    assert dispatch
    assert any(
        entry.start <= dispatch[0].offset
        and dispatch[-1].offset < entry.end
        and all(entry.start <= instruction.offset < entry.end for instruction in dispatch)
        for entry in entries
    )
    assert instructions[call_index + 1].opname == "RETURN_VALUE"


def _continuation_boundary_line(target: str) -> tuple[object, int]:
    if target == "dispatch":
        function = evidence._publish_pre_serialized_batch
        needle = "return _publish_after_reservation("
    else:
        function = evidence._publish_after_reservation
        needle = "    try:"
    lines, first_line = inspect.getsourcelines(function)
    line_number = next(
        first_line + offset for offset, source_line in enumerate(lines) if needle in source_line
    )
    return function.__code__, line_number


@pytest.mark.parametrize("target", ["dispatch", "continuation_entry"])
@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("failure_seal_fails", [False, True])
def test_trace_interruption_at_continuation_boundary_is_sealed_without_retry(
    tmp_path: Path,
    interruption_type: type[BaseException],
    target: str,
    failure_seal_fails: bool,
) -> None:
    publisher = FakePublisher()
    failure_publisher = FakePublisher(fail_on=FAILURE_SEAL_LEAF if failure_seal_fails else None)
    emergency_publisher = FakePublisher()
    target_code, target_line = _continuation_boundary_line(target)
    triggered = False

    def inject(frame: object, event: str, _arg: object):
        nonlocal triggered
        if (
            not triggered
            and event == "line"
            and frame.f_code is target_code  # type: ignore[attr-defined]
            and frame.f_lineno == target_line  # type: ignore[attr-defined]
        ):
            triggered = True
            sys.settrace(None)
            raise interruption_type()
        return inject

    previous_trace = sys.gettrace()
    sys.settrace(inject)
    try:
        with pytest.raises(R7S6EvidencePublicationError) as captured:
            _publish(
                tmp_path,
                publisher,
                failure_publisher=failure_publisher,
                emergency_publisher=emergency_publisher,
            )
    finally:
        sys.settrace(previous_trace)

    failure = captured.value
    assert triggered is True
    assert failure.stage == "post_reservation_continuation_dispatch"
    assert failure.reservation_directory.is_dir()
    assert failure.reservation_publication is not None
    reservation_path = failure.reservation_directory / RUN_RESERVATION_LEAF
    assert reservation_path.is_file()
    reservation_raw = reservation_path.read_bytes()
    reservation = json.loads(reservation_path.read_bytes())
    assert reservation["run_uuid"] == RUN_UUID
    assert reservation["released_or_deleted"] is False
    assert len(failure.reservation_inventory) == 1
    reservation_entry = failure.reservation_inventory[0]
    assert reservation_entry == {
        "leaf": RUN_RESERVATION_LEAF,
        "status": "read_back",
        "sha256": hashlib.sha256(reservation_raw).hexdigest(),
        "bytes": len(reservation_raw),
    }
    assert publisher.calls == [(failure.reservation_directory, RUN_RESERVATION_LEAF)]
    assert failure_publisher.calls == [(failure.failure_seal_directory, FAILURE_SEAL_LEAF)]
    assert not failure.output_directory.exists()
    assert not failure.staging_directory.exists()
    assert not any(
        path.name in {"completion-marker.json", "private-success-index.json"}
        for path in tmp_path.rglob("*")
    )

    if failure_seal_fails:
        assert failure.failure_seal_publication is None
        assert failure.emergency_seal_publication is not None
        assert emergency_publisher.calls == [
            (failure.emergency_seal_directory, EMERGENCY_SEAL_LEAF)
        ]
        seal = json.loads((failure.emergency_seal_directory / EMERGENCY_SEAL_LEAF).read_bytes())
        assert seal["automatic_retry_count"] == 0
        assert seal["cleanup_or_overwrite_attempted"] is False
        assert seal["success_marker_created"] is False
    else:
        assert failure.failure_seal_publication is not None
        assert failure.emergency_seal_publication is None
        assert emergency_publisher.calls == []
        seal = json.loads((failure.failure_seal_directory / FAILURE_SEAL_LEAF).read_bytes())
        assert seal["automatic_retry_count"] == 0
        assert seal["cleanup_or_overwrite_attempted"] is False
        assert seal["success_marker_created"] is False


@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
def test_staging_mkdir_then_interruption_is_sealed_without_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption_type: type[BaseException],
) -> None:
    publisher = FakePublisher()
    real_mkdir = evidence.os.mkdir

    def mkdir_then_interrupt(path: str | os.PathLike[str], *args: Any, **kwargs: Any) -> None:
        real_mkdir(path, *args, **kwargs)
        if Path(path).name.endswith(".r7s6-staging"):
            raise interruption_type("after staging mkdir")

    monkeypatch.setattr(evidence.os, "mkdir", mkdir_then_interrupt)
    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publisher)

    failure = captured.value
    assert failure.stage == "staging_directory_create"
    assert failure.staging_directory.is_dir()
    assert list(failure.staging_directory.iterdir()) == []
    assert failure.reservation_publication is not None
    assert failure.failure_seal_publication is not None
    assert not failure.output_directory.exists()


@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
def test_failure_observation_lstat_interruption_cannot_suppress_failure_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption_type: type[BaseException],
) -> None:
    publisher = FakePublisher()
    original_lstat = evidence.os.lstat
    observation_phase = False
    observation_interrupted = False

    def interrupt_serializer(_value: Any) -> bytes:
        nonlocal observation_phase
        observation_phase = True
        raise RuntimeError("late aggregate serialization failed")

    def interrupt_lstat(path: str | os.PathLike[str], *args: Any, **kwargs: Any):
        nonlocal observation_interrupted
        if observation_phase and not observation_interrupted:
            observation_interrupted = True
            raise interruption_type("failure observation interrupted")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(evidence.os, "lstat", interrupt_lstat)
    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publisher, aggregate_serializer=interrupt_serializer)

    observation_phase = False
    failure = captured.value
    assert failure.failure_seal_directory is not None
    seal_path = failure.failure_seal_directory / FAILURE_SEAL_LEAF
    assert seal_path.is_file()
    seal = json.loads(seal_path.read_bytes())
    assert seal["success_marker_created"] is False
    assert seal["partial_artifact_evidence"]["staging_path_presence_observation"] == {
        "status": "unproven_observation_failed",
        "conservative_possible_presence": True,
        "error_type": f"builtins.{interruption_type.__name__}",
    }
    assert failure.failure_seal_publication is not None


@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
def test_failure_context_preparation_interruption_creates_independent_emergency_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption_type: type[BaseException],
) -> None:
    publisher = FakePublisher()

    def fail_context(**_kwargs: Any) -> dict[str, Any]:
        raise interruption_type("failure context preparation interrupted")

    def fail_rename(_staging: Path, _final: Path) -> None:
        raise OSError("trigger primary failure")

    monkeypatch.setattr(evidence, "_prepare_failure_seal_context", fail_context)
    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publisher, directory_publisher=fail_rename)

    failure = captured.value
    assert failure.failure_seal_publication is None
    assert failure.failure_seal_error_type == f"builtins.{interruption_type.__name__}"
    assert failure.emergency_seal_publication is not None
    assert failure.staging_directory.is_dir()
    emergency = json.loads((failure.emergency_seal_directory / EMERGENCY_SEAL_LEAF).read_bytes())
    assert emergency["failure_seal_exception_type"] == (f"builtins.{interruption_type.__name__}")
    assert emergency["partial_artifact_evidence"]["policy"] == (
        "failure_context_preparation_unproven_partial_preserved"
    )
    assert emergency["cleanup_or_overwrite_attempted"] is False


@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
def test_failure_payload_preparation_interruption_creates_independent_emergency_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption_type: type[BaseException],
) -> None:
    publisher = FakePublisher()

    def interrupt_payload(**_kwargs: Any) -> dict[str, Any]:
        raise interruption_type("failure payload preparation interrupted")

    def fail_rename(_staging: Path, _final: Path) -> None:
        raise OSError("trigger primary failure")

    monkeypatch.setattr(evidence, "_failure_payload", interrupt_payload)
    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publisher, directory_publisher=fail_rename)

    failure = captured.value
    assert failure.failure_seal_publication is None
    assert failure.failure_seal_error_type == f"builtins.{interruption_type.__name__}"
    assert failure.emergency_seal_publication is not None
    emergency = json.loads((failure.emergency_seal_directory / EMERGENCY_SEAL_LEAF).read_bytes())
    assert emergency["failure_seal_exception_type"] == (f"builtins.{interruption_type.__name__}")
    assert emergency["partial_inventory"]
    assert emergency["success_marker_created"] is False


@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
def test_last_known_inventory_preparation_interruption_creates_emergency_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption_type: type[BaseException],
) -> None:
    publisher = FakePublisher(fail_on="wsl.json")

    def interrupt_inventory(*_args: Any, **_kwargs: Any) -> tuple[dict[str, Any], ...]:
        raise interruption_type("last-known inventory preparation interrupted")

    monkeypatch.setattr(evidence, "_last_known_pre_rename_inventory", interrupt_inventory)
    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publisher)

    failure = captured.value
    assert failure.stage == "document_publication"
    assert failure.failure_seal_publication is None
    assert failure.failure_seal_error_type == f"builtins.{interruption_type.__name__}"
    assert failure.emergency_seal_publication is not None
    assert failure.staging_directory.is_dir()
    emergency = json.loads((failure.emergency_seal_directory / EMERGENCY_SEAL_LEAF).read_bytes())
    assert emergency["original_exception_type"] == "builtins.OSError"
    assert emergency["failure_seal_exception_type"] == (f"builtins.{interruption_type.__name__}")
    assert emergency["success_marker_created"] is False


@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
def test_final_batch_constructor_interruption_is_sealed_after_final_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption_type: type[BaseException],
) -> None:
    publisher = FakePublisher()

    def interrupt_constructor(**_kwargs: Any) -> Any:
        raise interruption_type("final batch construction interrupted")

    monkeypatch.setattr(evidence, "PreSerializedBatch", interrupt_constructor)
    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publisher)

    failure = captured.value
    assert failure.stage == "terminal_batch_construction"
    assert failure.output_directory.is_dir()
    assert not failure.staging_directory.exists()
    assert failure.failure_seal_publication is not None
    seal = json.loads((failure.failure_seal_directory / FAILURE_SEAL_LEAF).read_bytes())
    assert seal["exception_type"] == f"builtins.{interruption_type.__name__}"
    assert seal["success_marker_created"] is False
    assert not (failure.output_directory / "completion-marker.json").exists()


def test_nonregular_partial_downgrades_failure_seal_sha_claim(
    tmp_path: Path,
) -> None:
    fallback = FakePublisher()

    def nonregular_partial(
        directory: Path,
        leaf: str,
        raw: bytes,
        *,
        run_uuid: str,
        api: object | None,
    ) -> DurableBoundPublication:
        if leaf == "windows.json":
            fallback.calls.append((directory, leaf))
            (directory / f".{leaf}.{run_uuid}.partial").mkdir()
            raise OSError("injected_nonregular_partial")
        return fallback(directory, leaf, raw, run_uuid=run_uuid, api=api)

    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, nonregular_partial)  # type: ignore[arg-type]

    failure = captured.value
    seal = json.loads((failure.failure_seal_directory / FAILURE_SEAL_LEAF).read_bytes())
    assert seal["partial_artifact_policy"] == (
        "writer_no_cleanup_or_overwrite_sha_reference_unproven"
    )
    assert seal["partial_inventory_sha_evidence"] == {
        "observed_entry_count": 1,
        "sha_referenced_entry_count": 0,
        "sha_reference_complete": False,
        "unproven_entry_count": 1,
    }
    assert seal["partial_inventory"][0]["status"] == "unreadable_or_non_regular"
    assert seal["failed_publication_handle_observation"]["record_available"] is False


def test_unreadable_partial_downgrades_failure_seal_sha_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = FakePublisher(leave_partial_and_fail_on="windows.json")
    original_read_bytes = Path.read_bytes

    def deny_partial(path: Path) -> bytes:
        if path.name.endswith(".partial"):
            raise PermissionError(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", deny_partial)
    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publisher)

    failure = captured.value
    seal = json.loads((failure.failure_seal_directory / FAILURE_SEAL_LEAF).read_bytes())
    assert seal["partial_artifact_policy"] == (
        "writer_no_cleanup_or_overwrite_sha_reference_unproven"
    )
    assert seal["partial_inventory_sha_evidence"]["sha_reference_complete"] is False
    assert seal["partial_inventory_sha_evidence"]["unproven_entry_count"] == 1
    assert seal["partial_inventory"][0]["status"] == "inventory_read_failed"


def test_durable_publication_failure_same_handle_observation_is_preserved(
    tmp_path: Path,
) -> None:
    fallback = FakePublisher()

    def durable_failure(
        directory: Path,
        leaf: str,
        raw: bytes,
        *,
        run_uuid: str,
        api: object | None,
    ) -> DurableBoundPublication:
        if leaf == "windows.json":
            fallback.calls.append((directory, leaf))
            digest = hashlib.sha256(raw).hexdigest()
            observation = PublicationFailureObservation(
                stage="rename_no_replace",
                temporary_leaf=f".{leaf}.{run_uuid}.partial",
                intended_final_path=str(directory / leaf),
                rename_completed=False,
                observation_status="same_handle_observed",
                current_identity=None,
                current_sha256=digest,
                current_bytes=len(raw),
                expected_sha256=digest,
                expected_bytes=len(raw),
                observation_error_type=None,
            )
            raise DurablePublicationError("injected_durable_failure", observation)
        return fallback(directory, leaf, raw, run_uuid=run_uuid, api=api)

    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, durable_failure)  # type: ignore[arg-type]

    failure = captured.value
    seal = json.loads((failure.failure_seal_directory / FAILURE_SEAL_LEAF).read_bytes())
    handle_observation = seal["failed_publication_handle_observation"]
    assert handle_observation["record_available"] is True
    assert handle_observation["status"] == ("durable_publication_failure_observation_preserved")
    assert handle_observation["observation"]["observation_status"] == "same_handle_observed"
    assert (
        handle_observation["observation"]["current_sha256"]
        == hashlib.sha256(
            canonical_json_bytes({"domain": "windows", "sequence": [0, 1]})
        ).hexdigest()
    )


def test_existing_output_and_control_paths_are_rejected_without_any_write(tmp_path: Path) -> None:
    output = tmp_path / "r7s6-review"
    output.mkdir()
    sentinel = output / "user-owned.json"
    sentinel.write_bytes(b'{"preserve":true}\n')
    before = sentinel.read_bytes()
    publisher = FakePublisher()

    with pytest.raises(R7S6EvidenceError, match="append_only_path_exists"):
        _publish(tmp_path, publisher)

    assert sentinel.read_bytes() == before
    assert publisher.calls == []
    assert sorted(path.name for path in tmp_path.iterdir()) == ["r7s6-review"]


@pytest.mark.parametrize(
    "documents",
    [
        {
            "x": {"value": 1},
            f".x.{RUN_UUID}.partial": {"value": 2},
        },
        {
            f".{IDENTITY_MANIFEST_LEAF}.{RUN_UUID}.partial": {"value": 1},
        },
        {
            f".{IDENTITY_INDEX_LEAF}.{RUN_UUID}.partial": {"value": 1},
        },
    ],
)
def test_planned_final_and_temporary_leaf_collision_is_rejected_before_any_write(
    tmp_path: Path,
    documents: dict[str, object],
) -> None:
    publisher = FakePublisher()

    with pytest.raises(R7S6EvidenceError, match="planned_final_temporary_leaf_collision"):
        _publish_pre_serialized_batch_for_test(
            tmp_path,
            "r7s6-review",
            documents,
            run_uuid=RUN_UUID,
            publisher=publisher,
        )

    assert publisher.calls == []
    assert list(tmp_path.iterdir()) == []


def test_long_final_leaf_with_infeasible_temporary_leaf_is_rejected_before_any_write(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher()

    with pytest.raises(R7S6EvidenceError, match="planned_temporary_leaf_invalid"):
        _publish_pre_serialized_batch_for_test(
            tmp_path,
            "r7s6-review",
            {"a" * 180: {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )

    assert publisher.calls == []
    assert list(tmp_path.iterdir()) == []


def test_output_leaf_cannot_alias_parent_global_reservation_before_any_write(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher()

    with pytest.raises(R7S6EvidenceError, match="control_directory_leaf_collision"):
        _publish_pre_serialized_batch_for_test(
            tmp_path,
            f".r7s6-run-{RUN_UUID}.reservation",
            {"document.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )

    assert publisher.calls == []
    assert list(tmp_path.iterdir()) == []


def test_long_output_leaf_with_infeasible_control_directory_is_rejected_before_any_write(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher()

    with pytest.raises(R7S6EvidenceError, match="control_directory_leaf_invalid"):
        _publish_pre_serialized_batch_for_test(
            tmp_path,
            "o" * 180,
            {"document.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )

    assert publisher.calls == []
    assert list(tmp_path.iterdir()) == []


def test_same_run_reservation_race_is_rejected_without_competing_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publisher = FakePublisher()
    real_mkdir = os.mkdir

    def racing_mkdir(path: str | os.PathLike[str], *args: object, **kwargs: object) -> None:
        candidate = Path(path)
        if candidate.name.endswith(".reservation"):
            raise FileExistsError(candidate)
        real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", racing_mkdir)
    with pytest.raises(R7S6EvidenceError, match="parent_global_run_uuid_already_reserved"):
        _publish(tmp_path, publisher)
    assert publisher.calls == []
    assert list(tmp_path.iterdir()) == []


def test_same_run_uuid_cannot_publish_a_different_output_leaf(tmp_path: Path) -> None:
    first_publisher = FakePublisher()
    first = _publish(tmp_path, first_publisher)
    second_publisher = FakePublisher()

    with pytest.raises(R7S6EvidenceError, match="parent_global_run_uuid_already_reserved"):
        _publish_pre_serialized_batch_for_test(
            tmp_path,
            "different-output-leaf",
            {"other.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=second_publisher,
        )

    assert first.output_directory.is_dir()
    assert first.reservation_directory.is_dir()
    assert not (tmp_path / "different-output-leaf").exists()
    assert second_publisher.calls == []


def test_rename_toctou_prioritizes_staging_and_only_lstats_untrusted_output(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher()

    def race_output(staging: Path, final: Path) -> None:
        assert staging.is_dir()
        final.mkdir()
        (final / "attacker-content-must-not-be-read.json").write_bytes(b"secret")
        raise FileExistsError(final)

    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publisher, directory_publisher=race_output)

    failure = captured.value
    assert failure.stage == "final_directory_publish"
    assert failure.staging_directory.is_dir()
    assert len(failure.writer_owned_staging_inventory) == 4
    assert failure.partial_inventory == failure.writer_owned_staging_inventory
    observation = failure.untrusted_output_observation
    assert observation["scope"] == "untrusted_path_lstat_only"
    assert observation["status"] == "lstat_observed"
    assert observation["object_type"] == "directory"
    assert observation["followed_path"] is False
    assert observation["children_enumerated"] is False
    assert observation["content_read"] is False
    assert observation["sha256"] is None
    seal = json.loads((failure.failure_seal_directory / FAILURE_SEAL_LEAF).read_bytes())
    assert seal["writer_owned_staging_inventory"] == list(failure.partial_inventory)
    assert seal["untrusted_output_observation"] == observation
    assert "attacker-content-must-not-be-read.json" not in json.dumps(seal)


def test_post_rename_child_swap_failure_is_sealed_without_path_inventory(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher()

    def swapped_child_verifier(
        output: Path,
        artifacts: tuple[r7s4.PreparedJsonArtifact, ...],
        publications: tuple[DurableBoundPublication, ...],
    ) -> evidence.FinalDirectoryVerification:
        del artifacts, publications
        (output / "attacker-child.json").write_bytes(b"do-not-read")
        raise R7S6EvidenceError("injected_child_swap")

    with pytest.raises(R7S6EvidencePublicationError) as captured:
        _publish(tmp_path, publisher, final_verifier=swapped_child_verifier)

    failure = captured.value
    assert failure.stage == "final_directory_handle_readback"
    assert failure.writer_owned_staging_inventory == ()
    assert failure.writer_owned_final_inventory == ()
    assert failure.partial_inventory == ()
    assert len(failure.last_known_pre_rename_inventory) == 4
    assert all(
        item["path_followed_after_rename_failure"] is False
        for item in failure.last_known_pre_rename_inventory
    )
    observation = failure.untrusted_output_observation
    assert observation["scope"] == "untrusted_path_lstat_only"
    assert observation["children_enumerated"] is False
    assert observation["content_read"] is False
    seal = json.loads((failure.failure_seal_directory / FAILURE_SEAL_LEAF).read_bytes())
    assert seal["last_known_pre_rename_inventory"] == list(failure.last_known_pre_rename_inventory)
    assert seal["partial_artifact_policy"] == (
        "final_directory_present_content_inventory_unproven_last_known_pre_rename_sha_available"
    )
    assert seal["partial_artifact_evidence"]["final_path_observation"]["object_type"] == (
        "directory"
    )
    assert "attacker-child.json" not in json.dumps(seal)


@pytest.mark.parametrize(
    "leaf",
    [
        IDENTITY_MANIFEST_LEAF,
        IDENTITY_INDEX_LEAF,
        FAILURE_SEAL_LEAF,
        EMERGENCY_SEAL_LEAF,
        RUN_RESERVATION_LEAF,
        "completion-marker.json",
        "private-success-index.json",
    ],
)
def test_control_and_success_leaves_are_rejected_before_directory_create(
    tmp_path: Path, leaf: str
) -> None:
    publisher = FakePublisher()
    with pytest.raises(R7S6EvidenceError, match="reserved_or_success"):
        _publish_pre_serialized_batch_for_test(
            tmp_path,
            "r7s6-review",
            {leaf: {"forbidden": True}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )
    assert publisher.calls == []
    assert list(tmp_path.iterdir()) == []


def test_source_contract_is_pre_serialized_append_only_and_still_no_go() -> None:
    contract = source_contract()
    assert contract["same_filesystem_staging_directory"] is True
    assert contract["parent_global_run_uuid_reservation"] is True
    assert contract["run_reservation_key_scope"] == "run_uuid_only_not_output_leaf"
    assert contract["success_and_failure_reference_run_reservation"] is True
    assert contract["all_success_json_serialized_before_final_output_directory"] is True
    assert contract["exclusive_final_directory_publish"] is True
    assert contract["atomic_final_directory_rename"] is True
    assert contract["parent_directory_flush_after_final_rename"] is True
    assert contract["documents_precede_manifest"] is True
    assert "domain_artifacts_precede_manifest" not in contract
    assert contract["publication_identity_scope"] == (
        "staging_same_handle_before_parent_directory_rename"
    )
    assert contract["publication_paths_rebased_to_final"] is False
    assert contract["embedded_manifest_post_rename_observation"] is False
    assert contract["returned_batch_post_rename_observation"] is True
    assert contract["post_rename_handle_identity_verified"] is True
    assert contract["post_rename_staging_to_final_file_id_crosscheck"] is True
    assert contract["final_directory_handle_delete_share"] is False
    assert contract["final_directory_handle_write_share"] is False
    assert contract["final_handles_held_through_exact_leaf_inventory"] is True
    assert contract["root_and_child_st_file_attributes_reparse_checked"] is True
    assert contract["partial_artifacts_preserved_unmodified"] is True
    assert contract["partial_artifacts_sha_referenced_by_sibling_failure_seal"] is False
    assert contract["partial_inventory_sha_completeness_recorded"] is True
    assert contract["unreadable_or_nonregular_partial_downgrades_to_unproven"] is True
    assert contract["empty_inventory_is_not_proof_of_no_partial_artifacts"] is True
    assert contract["no_partial_claim_requires_staging_and_final_paths_absent"] is True
    assert contract["post_rename_verification_failure_final_content_downgraded_to_unproven"] is True
    assert contract["durable_publication_failure_same_handle_observation_preserved"] is True
    assert contract["last_known_pre_rename_sha_inventory_in_failure_seal"] is True
    assert contract["writer_owned_staging_inventory_prioritized"] is True
    assert contract["unexpected_output_observation"] == (
        "lstat_only_no_path_follow_or_content_read"
    )
    assert contract["mutation_to_next_protected_boundary_base_exception_sealed"] is True
    assert contract["terminal_batch_construction_base_exception_sealed"] is True
    assert contract["failure_context_preparation_failure_uses_independent_emergency_seal"] is True
    assert contract["failure_seal_partial_inventory_referenced_by_emergency_seal"] is True
    assert contract["automatic_retry_count"] == 0
    assert contract["cleanup_or_overwrite_on_failure"] is False
    assert contract["success_or_completion_marker_supported"] is False
    assert contract["same_token_hostile_admin_protected"] is False
    assert contract["power_loss_durability_proven"] is False
    assert contract["production_go_enabled"] is False
    assert contract["go_evidence_eligible"] is False


def test_non_finite_json_is_rejected_before_any_directory(tmp_path: Path) -> None:
    publisher = FakePublisher()
    with pytest.raises(R7S6EvidenceError, match="input_or_serialization_invalid"):
        _publish_pre_serialized_batch_for_test(
            tmp_path,
            "r7s6-review",
            {"bad.json": {"value": float("nan")}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )
    assert publisher.calls == []
    assert list(tmp_path.iterdir()) == []


def test_canonical_json_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError):
        canonical_json_bytes({"bad": float("nan")})


@pytest.mark.parametrize("expect_directory", [True, False])
def test_checked_lstat_rejects_windows_reparse_attribute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    expect_directory: bool,
) -> None:
    mode = stat.S_IFDIR if expect_directory else stat.S_IFREG
    observed = SimpleNamespace(
        st_mode=mode,
        st_file_attributes=evidence.FILE_ATTRIBUTE_REPARSE_POINT,
    )
    monkeypatch.setattr(evidence.os, "lstat", lambda path: observed)

    with pytest.raises(R7S6EvidenceError, match="lstat_reparse_point_rejected"):
        _checked_lstat(tmp_path / "candidate", expect_directory=expect_directory)


def test_final_directory_api_denies_write_and_delete_share() -> None:
    class ProbeApi(_NoMutationWindowsHandleApi):
        def __init__(self) -> None:
            self.open_arguments: tuple[int, int, int] | None = None

        def _create_file(
            self,
            path: str,
            access: int,
            share: int,
            flags: int,
        ) -> int:
            del path
            self.open_arguments = (access, share, flags)
            return 123

    api = ProbeApi()
    assert api.open_directory(r"C:\evidence\final") == 123
    assert api.open_arguments is not None
    _, share, _ = api.open_arguments
    assert share == api._FILE_SHARE_READ
    assert share & api._FILE_SHARE_WRITE == 0
    assert share & 0x00000004 == 0  # FILE_SHARE_DELETE


class FakeFinalHandleApi:
    def __init__(
        self,
        *,
        directory_identity: HandleIdentity,
        file_identity: HandleIdentity,
        raw: bytes,
    ) -> None:
        self.directory_identity = directory_identity
        self.file_identity = file_identity
        self.raw = raw
        self.events: list[tuple[str, int]] = []

    def open_directory(self, path: str) -> int:
        del path
        self.events.append(("open_directory", 100))
        return 100

    def open_read(self, path: str) -> int:
        del path
        self.events.append(("open_read", 200))
        return 200

    def identity(self, handle: int) -> HandleIdentity:
        self.events.append(("identity", handle))
        return self.directory_identity if handle == 100 else self.file_identity

    def read_all(self, handle: int, expected_size: int) -> bytes:
        assert handle == 200
        assert expected_size == len(self.raw)
        self.events.append(("read", handle))
        return self.raw

    def close(self, handle: int | None) -> None:
        if handle is not None:
            self.events.append(("close", handle))


def _final_verifier_fixture(
    tmp_path: Path,
) -> tuple[
    Path,
    r7s4.PreparedJsonArtifact,
    DurableBoundPublication,
    HandleIdentity,
    HandleIdentity,
]:
    staging = tmp_path / "staging"
    output = tmp_path / "final"
    staging.mkdir()
    artifact = r7s4.PreparedJsonArtifact(
        leaf="document.json",
        role="test_document",
        raw=canonical_json_bytes({"value": 1}),
    )
    publication = FakePublisher()(staging, artifact.leaf, artifact.raw, run_uuid=RUN_UUID, api=None)
    os.rename(staging, output)
    final_directory_identity = replace(
        publication.directory_identity,
        final_path=str(output.resolve()),
    )
    final_file_identity = replace(
        publication.identity,
        final_path=str((output / artifact.leaf).resolve()),
    )
    return (
        output,
        artifact,
        publication,
        final_directory_identity,
        final_file_identity,
    )


def test_handle_bound_final_verifier_rejects_child_swap(tmp_path: Path) -> None:
    output, artifact, publication, directory_identity, file_identity = _final_verifier_fixture(
        tmp_path
    )
    swapped_identity = replace(file_identity, file_id_hex="adversarial-child-swap")
    api = FakeFinalHandleApi(
        directory_identity=directory_identity,
        file_identity=swapped_identity,
        raw=artifact.raw,
    )

    with pytest.raises(R7S6EvidenceError, match="final_child_identity_changed"):
        _verify_final_handle_bound(
            output,
            (artifact,),
            (publication,),
            api=api,
        )

    assert api.events[-2:] == [("close", 200), ("close", 100)]


def test_handle_bound_final_verifier_rejects_directory_swap(tmp_path: Path) -> None:
    output, artifact, publication, directory_identity, file_identity = _final_verifier_fixture(
        tmp_path
    )
    swapped_directory = replace(directory_identity, file_id_hex="adversarial-directory-swap")
    api = FakeFinalHandleApi(
        directory_identity=swapped_directory,
        file_identity=file_identity,
        raw=artifact.raw,
    )

    with pytest.raises(R7S6EvidenceError, match="final_directory_identity_changed"):
        _verify_final_handle_bound(
            output,
            (artifact,),
            (publication,),
            api=api,
        )

    assert api.events[-1] == ("close", 100)


def test_handle_bound_final_verifier_rejects_child_swap_between_reads(
    tmp_path: Path,
) -> None:
    output, artifact, publication, directory_identity, file_identity = _final_verifier_fixture(
        tmp_path
    )

    class SwapBetweenReadsApi(FakeFinalHandleApi):
        def __init__(self) -> None:
            super().__init__(
                directory_identity=directory_identity,
                file_identity=file_identity,
                raw=artifact.raw,
            )
            self.file_identity_calls = 0

        def identity(self, handle: int) -> HandleIdentity:
            if handle == 200:
                self.file_identity_calls += 1
                if self.file_identity_calls == 2:
                    self.events.append(("identity", handle))
                    return replace(file_identity, file_id_hex="swap-between-reads")
            return super().identity(handle)

    api = SwapBetweenReadsApi()
    with pytest.raises(R7S6EvidenceError, match="identity_changed_during_readback"):
        _verify_final_handle_bound(
            output,
            (artifact,),
            (publication,),
            api=api,
        )

    assert api.events[-2:] == [("close", 200), ("close", 100)]


def test_handle_bound_final_verifier_holds_handles_through_second_read(
    tmp_path: Path,
) -> None:
    output, artifact, publication, directory_identity, file_identity = _final_verifier_fixture(
        tmp_path
    )
    api = FakeFinalHandleApi(
        directory_identity=directory_identity,
        file_identity=file_identity,
        raw=artifact.raw,
    )

    result = _verify_final_handle_bound(
        output,
        (artifact,),
        (publication,),
        api=api,
    )

    assert result.kernel_handle_bound is True
    assert result.directory_handle_no_delete_share is True
    assert result.directory_handle_no_write_share is True
    assert result.child_handles_held_through_inventory is True
    assert result.handle_bound_content_readback is True
    assert [event for event in api.events if event[0] == "read"] == [
        ("read", 200),
        ("read", 200),
    ]
    assert api.events[-2:] == [("close", 200), ("close", 100)]


@pytest.mark.skipif(os.name != "nt", reason="production writer is Windows-only")
def test_windows_production_writer_returns_handle_bound_final_verification(
    tmp_path: Path,
) -> None:
    result = publish_pre_serialized_batch(
        tmp_path,
        "production-verification",
        {"document.json": {"value": 1}},
        run_uuid="29a42fa2-59af-49b4-a027-445ad06ca1c1",
    )

    verification = result.final_verification
    assert result.post_rename_handle_identity_verified is True
    assert verification.kernel_handle_bound is True
    assert verification.test_only_path_readback is False
    assert verification.directory_handle_no_delete_share is True
    assert verification.directory_handle_no_write_share is True
    assert verification.child_handles_held_through_inventory is True
    assert verification.handle_bound_content_readback is True
    assert verification.staging_to_final_identity_crosscheck is True
    assert len(verification.file_identities) == 3
