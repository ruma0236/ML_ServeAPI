from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from evm.scale_validation.phase_b2_r7s3_handle_io import HandleIdentity
from evm.scale_validation.phase_b2_r7s4_handle_io import DurableBoundPublication
from evm.scale_validation.phase_b2_r7s5_evidence import (
    ATOMIC_FAILURE_SEAL_LEAF,
    EMERGENCY_SEAL_LEAF,
    IDENTITY_INDEX_LEAF,
    IDENTITY_MANIFEST_LEAF,
    R7S5EvidenceError,
    R7S5EvidencePublicationError,
    _publish_identity_catalogued_batch_for_test,
    canonical_json_bytes,
    source_contract,
)


RUN_UUID = "b2ad7fd0-7d34-4a3e-a670-70fa997a9513"


class FakePublisher:
    def __init__(self, *, fail_on: str | set[str] | None = None) -> None:
        self.fail_on = {fail_on} if isinstance(fail_on, str) else set(fail_on or ())
        self.calls: list[str] = []

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
        self.calls.append(leaf)
        if leaf in self.fail_on:
            raise OSError(f"injected:{leaf}")
        final = directory / leaf
        with final.open("xb") as stream:
            stream.write(raw)
        final_path = str(final.resolve())
        directory_path = str(directory.resolve())
        file_id = hashlib.sha256(leaf.encode("utf-8")).hexdigest()[:32]
        if file_id == "fe" * 16:
            file_id = "ab" * 16
        identity = HandleIdentity(
            final_path=final_path,
            volume_serial_number=20260902,
            file_id_hex=file_id,
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
            file_id_hex="fe" * 16,
            size=0,
            link_count=1,
            attributes=0x10,
            reparse_tag=0,
            file_type=1,
            owner_sid="S-1-5-32-544",
            security_descriptor_sha256="ef" * 32,
            dacl_present=True,
            dacl_protected=False,
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
    return _publish_identity_catalogued_batch_for_test(
        tmp_path,
        "r7s5-review",
        {"windows.json": {"domain": "windows"}, "wsl.json": {"domain": "wsl"}},
        run_uuid=RUN_UUID,
        publisher=publisher,
        **kwargs,
    )


def test_manifest_and_index_persist_actual_publication_identity_and_flush(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher()
    result = _publish(tmp_path, publisher)

    assert publisher.calls == [
        "windows.json",
        "wsl.json",
        IDENTITY_MANIFEST_LEAF,
        IDENTITY_INDEX_LEAF,
    ]
    manifest = json.loads((result.output_directory / IDENTITY_MANIFEST_LEAF).read_bytes())
    index = json.loads((result.output_directory / IDENTITY_INDEX_LEAF).read_bytes())
    assert manifest["document_publication_count"] == 2
    assert manifest["document_handle_flush_identity_persisted"] is True
    assert [item["sequence"] for item in manifest["documents"]] == [1, 2]
    for item in manifest["documents"]:
        publication = item["publication"]
        assert publication["file_flush_count"] == 2
        assert publication["directory_flush_count"] == 1
        assert publication["directory_flush_succeeded"] is True
        assert publication["identity"]["file_id_hex"]
        assert publication["identity"]["dacl_protected"] is True
        assert publication["identity"]["volume_serial_number"] == 20260902
    assert index["aggregate_manifest"]["publication"] == result.manifest_publication.to_dict()
    assert index["terminal_index_identity_requires_external_readback"] is True
    assert result.index_publication == result.publications[-1]
    assert not (result.output_directory / "completion-marker.json").exists()


def test_manifest_publication_failure_seals_prior_document_identities(tmp_path: Path) -> None:
    publisher = FakePublisher(fail_on=IDENTITY_MANIFEST_LEAF)
    with pytest.raises(R7S5EvidencePublicationError) as captured:
        _publish(tmp_path, publisher)

    failure = captured.value
    assert failure.stage == "aggregate_manifest_publication"
    assert publisher.calls == [
        "windows.json",
        "wsl.json",
        IDENTITY_MANIFEST_LEAF,
        ATOMIC_FAILURE_SEAL_LEAF,
    ]
    assert len(failure.publications) == 2
    seal = json.loads((failure.output_directory / ATOMIC_FAILURE_SEAL_LEAF).read_bytes())
    assert seal["already_published"] == [item.to_dict() for item in failure.publications]
    assert seal["retry_count"] == 0
    assert seal["cleanup_or_overwrite_attempted"] is False
    assert not (failure.output_directory / IDENTITY_INDEX_LEAF).exists()
    assert not (failure.output_directory / "completion-marker.json").exists()


def test_index_failure_preserves_manifest_and_catalogues_it_in_failure_seal(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher(fail_on=IDENTITY_INDEX_LEAF)
    with pytest.raises(R7S5EvidencePublicationError) as captured:
        _publish(tmp_path, publisher)

    failure = captured.value
    assert failure.stage == "aggregate_index_publication"
    assert len(failure.publications) == 3
    assert failure.publications[-1].final_path.endswith(IDENTITY_MANIFEST_LEAF)
    seal = json.loads((failure.output_directory / ATOMIC_FAILURE_SEAL_LEAF).read_bytes())
    assert seal["already_published_count"] == 3
    assert seal["already_published"][-1] == failure.publications[-1].to_dict()
    assert (failure.output_directory / IDENTITY_MANIFEST_LEAF).is_file()
    assert not (failure.output_directory / IDENTITY_INDEX_LEAF).exists()


def test_late_manifest_serialization_failure_has_no_manifest_or_index(tmp_path: Path) -> None:
    publisher = FakePublisher()

    def fail_serializer(value: Any) -> bytes:
        del value
        raise PermissionError("injected late serializer failure")

    with pytest.raises(R7S5EvidencePublicationError) as captured:
        _publish(tmp_path, publisher, aggregate_serializer=fail_serializer)

    failure = captured.value
    assert failure.stage == "aggregate_manifest_serialization"
    assert publisher.calls == ["windows.json", "wsl.json", ATOMIC_FAILURE_SEAL_LEAF]
    assert len(failure.publications) == 2
    assert not (failure.output_directory / IDENTITY_MANIFEST_LEAF).exists()
    assert not (failure.output_directory / IDENTITY_INDEX_LEAF).exists()


def test_failure_seal_failure_uses_one_parent_emergency_seal(tmp_path: Path) -> None:
    publisher = FakePublisher(fail_on=IDENTITY_MANIFEST_LEAF)
    failure_publisher = FakePublisher(fail_on=ATOMIC_FAILURE_SEAL_LEAF)
    emergency_publisher = FakePublisher()

    with pytest.raises(R7S5EvidencePublicationError) as captured:
        _publish(
            tmp_path,
            publisher,
            failure_publisher=failure_publisher,
            emergency_publisher=emergency_publisher,
        )

    failure = captured.value
    assert failure.failure_seal_error_type == "builtins.OSError"
    assert failure.emergency_seal_publication is not None
    assert failure.emergency_seal_error_type is None
    assert failure.emergency_seal_directory is not None
    emergency = json.loads((failure.emergency_seal_directory / EMERGENCY_SEAL_LEAF).read_bytes())
    assert emergency["already_published_count"] == 2
    assert emergency["already_published"] == [item.to_dict() for item in failure.publications]
    assert failure_publisher.calls == [ATOMIC_FAILURE_SEAL_LEAF]
    assert emergency_publisher.calls == [EMERGENCY_SEAL_LEAF]


def test_existing_output_is_unchanged_and_routes_to_unique_create_failure_seal(
    tmp_path: Path,
) -> None:
    output = tmp_path / "r7s5-review"
    output.mkdir()
    sentinel = output / "user-owned.json"
    sentinel.write_bytes(b'{"preserve":true}\n')
    before = sentinel.read_bytes()
    publisher = FakePublisher()

    with pytest.raises(R7S5EvidencePublicationError) as captured:
        _publish(tmp_path, publisher)

    assert captured.value.stage == "output_directory_create"
    assert sentinel.read_bytes() == before
    assert publisher.calls == [EMERGENCY_SEAL_LEAF]
    assert captured.value.downstream_call_count == 0
    assert captured.value.retry_count == 0


@pytest.mark.parametrize(
    "leaf",
    [
        IDENTITY_MANIFEST_LEAF,
        IDENTITY_INDEX_LEAF,
        ATOMIC_FAILURE_SEAL_LEAF,
        EMERGENCY_SEAL_LEAF,
        "completion-marker.json",
        "private-success-index.json",
    ],
)
def test_control_and_success_document_leaves_are_rejected_before_disk(
    tmp_path: Path, leaf: str
) -> None:
    publisher = FakePublisher()
    with pytest.raises(R7S5EvidenceError):
        _publish_identity_catalogued_batch_for_test(
            tmp_path,
            "r7s5-review",
            {leaf: {"forbidden": True}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )
    assert publisher.calls == []
    assert list(tmp_path.iterdir()) == []


def test_publisher_identity_mutation_is_rejected_and_zero_retry_sealed(tmp_path: Path) -> None:
    class MutatingPublisher(FakePublisher):
        def __call__(self, *args: Any, **kwargs: Any) -> DurableBoundPublication:
            result = super().__call__(*args, **kwargs)
            if self.calls[-1] == "windows.json":
                return DurableBoundPublication(**{**result.__dict__, "file_flush_count": 1})
            return result

    publisher = MutatingPublisher()
    with pytest.raises(R7S5EvidencePublicationError) as captured:
        _publish(tmp_path, publisher)

    assert captured.value.stage == "document_publication"
    assert captured.value.publications == ()
    assert captured.value.retry_count == 0
    assert publisher.calls == ["windows.json", ATOMIC_FAILURE_SEAL_LEAF]


def test_final_to_temporary_leaf_collision_is_rejected_before_directory_create(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher()
    colliding_leaf = f".a.json.{RUN_UUID}.partial"
    with pytest.raises(R7S5EvidenceError, match="planned_final_temporary_leaf_collision"):
        _publish_identity_catalogued_batch_for_test(
            tmp_path,
            "r7s5-review",
            {"a.json": {"value": 1}, colliding_leaf: {"value": 2}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )
    assert publisher.calls == []
    assert list(tmp_path.iterdir()) == []


def test_parent_control_leaf_length_is_rejected_before_directory_create(tmp_path: Path) -> None:
    publisher = FakePublisher()
    with pytest.raises(R7S5EvidenceError, match="parent_control_leaf_invalid"):
        _publish_identity_catalogued_batch_for_test(
            tmp_path,
            "a" * 170,
            {"review.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )
    assert publisher.calls == []
    assert list(tmp_path.iterdir()) == []


def test_publication_subclass_is_rejected_before_catalog_and_failure_is_sealed(
    tmp_path: Path,
) -> None:
    class DerivedPublication(DurableBoundPublication):
        pass

    class DerivedFirstPublisher(FakePublisher):
        def __call__(self, *args: Any, **kwargs: Any) -> DurableBoundPublication:
            result = super().__call__(*args, **kwargs)
            if self.calls[-1] == "windows.json":
                return DerivedPublication(**result.__dict__)
            return result

    publisher = DerivedFirstPublisher()
    with pytest.raises(R7S5EvidencePublicationError) as captured:
        _publish(tmp_path, publisher)
    failure = captured.value
    assert failure.stage == "document_publication"
    assert failure.publications == ()
    assert failure.failure_seal_publication is not None
    assert failure.retry_count == 0


def test_ambiguous_publication_failure_seal_contains_planned_sha_bytes_and_flag(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher(fail_on=IDENTITY_MANIFEST_LEAF)
    with pytest.raises(R7S5EvidencePublicationError) as captured:
        _publish(tmp_path, publisher)
    failure = captured.value
    attempted = failure.attempted_artifact
    assert attempted is not None
    assert attempted["leaf"] == IDENTITY_MANIFEST_LEAF
    assert len(attempted["sha256"]) == 64
    assert attempted["bytes"] > 0
    assert attempted["serialized"] is True
    assert attempted["publication_may_have_committed"] is True
    seal = json.loads((failure.output_directory / ATOMIC_FAILURE_SEAL_LEAF).read_bytes())
    assert seal["attempted_artifact"] == attempted


def test_contract_keeps_unproven_boundaries_and_production_go_disabled() -> None:
    contract = source_contract()
    assert contract["document_publication_identity_persisted_in_manifest"] is True
    assert contract["document_file_id_volume_dacl_and_flush_persisted"] is True
    assert contract["manifest_publication_identity_persisted_in_index"] is True
    assert contract["index_published_last"] is True
    assert contract["terminal_index_self_identity_embedded"] is False
    assert contract["terminal_index_external_readback_required"] is True
    assert contract["aggregate_late_serialization"] is True
    assert contract["all_json_serialized_before_output_directory"] is False
    assert contract["all_final_temporary_and_parent_control_leaves_preflighted"] is True
    assert contract["exact_publication_dataclass_type_required"] is True
    assert contract["publication_snapshot_canonicalized_immediately"] is True
    assert contract["failure_seal_persists_attempted_sha_bytes_or_unserialized_state"] is True
    assert contract["ambiguous_publication_marked_may_have_committed"] is True
    assert contract["fixed_global_reservation_integrated"] is False
    assert contract["multi_host_global_one_shot_proven"] is False
    assert contract["same_token_hostile_admin_protected"] is False
    assert contract["retry_count"] == 0
    assert contract["automatic_retry_count"] == 0
    assert contract["cleanup_or_overwrite_on_failure"] is False
    assert contract["success_or_completion_marker_supported"] is False
    assert contract["production_go_enabled"] is False
    assert contract["go_evidence_eligible"] is False


def test_canonical_json_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError):
        canonical_json_bytes({"bad": float("nan")})
