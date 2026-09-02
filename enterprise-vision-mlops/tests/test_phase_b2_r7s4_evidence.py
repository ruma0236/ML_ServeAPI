from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from evm.scale_validation.phase_b2_r7s4_evidence import (
    AGGREGATE_INDEX_LEAF,
    AGGREGATE_MANIFEST_LEAF,
    ATOMIC_FAILURE_SEAL_LEAF,
    EMERGENCY_SEAL_LEAF,
    RESERVATION_FAILURE_SEAL_LEAF,
    R7S4EvidenceError,
    R7S4EvidencePublicationError,
    RUN_RESERVATION_PREFIX,
    RUN_RESERVATION_SUFFIX,
    _publish_review_json_batch_for_test,
    publish_review_json_batch,
    source_contract,
)
from evm.scale_validation.phase_b2_r7s4_handle_io import (
    DurableBoundPublication,
    HandleIdentity,
)


RUN_UUID = "ae85b8b3-820f-4d89-84a3-f9172607b4a9"
OTHER_RUN_UUID = "be85b8b3-820f-4d89-84a3-f9172607b4a9"


def _run_reservation(parent: Path, run_uuid: str = RUN_UUID) -> Path:
    return parent / f"{RUN_RESERVATION_PREFIX}{run_uuid}{RUN_RESERVATION_SUFFIX}"


def _reservation_failure_directory(
    parent: Path,
    output_leaf: str,
    run_uuid: str = RUN_UUID,
) -> Path:
    return parent / f".{output_leaf}.{run_uuid}.reservation-failure-seal"


def _reservation_emergency_directory(
    parent: Path,
    output_leaf: str,
    run_uuid: str = RUN_UUID,
) -> Path:
    return parent / f".{output_leaf}.{run_uuid}.reservation-emergency-seal"


def _identity(path: Path, raw: bytes, *, directory: bool = False) -> HandleIdentity:
    return HandleIdentity(
        final_path=str(path),
        volume_serial_number=20260902,
        file_id_hex=hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:32],
        size=0 if directory else len(raw),
        link_count=1,
        attributes=0x10 if directory else 0x80,
        reparse_tag=0,
        file_type=1,
        owner_sid="S-1-5-32-544",
        security_descriptor_sha256="ab" * 32,
        dacl_present=True,
        dacl_protected=not directory,
    )


class FakePublisher:
    def __init__(
        self,
        *,
        fail_on: str | set[str] | None = None,
        directory_flush_count: int = 1,
        publication_overrides: dict[str, object] | None = None,
        identity_overrides: dict[str, object] | None = None,
        directory_identity_overrides: dict[str, object] | None = None,
    ) -> None:
        self.fail_on = {fail_on} if isinstance(fail_on, str) else set(fail_on or ())
        self.directory_flush_count = directory_flush_count
        self.publication_overrides = publication_overrides or {}
        self.identity_overrides = identity_overrides or {}
        self.directory_identity_overrides = directory_identity_overrides or {}
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
            partial = directory / f".{leaf}.{run_uuid}.partial"
            with partial.open("xb") as stream:
                stream.write(raw)
            raise OSError(f"injected publication failure:{leaf}")
        final = directory / leaf
        with final.open("xb") as stream:
            stream.write(raw)
        publication = DurableBoundPublication(
            final_path=str(final),
            temporary_leaf=f".{leaf}.{run_uuid}.partial",
            sha256=hashlib.sha256(raw).hexdigest(),
            bytes=len(raw),
            identity=_identity(final, raw),
            directory_identity=_identity(directory, b"", directory=True),
            file_flush_count=2,
            directory_flush_count=self.directory_flush_count,
            directory_flush_succeeded=self.directory_flush_count == 1,
        )
        publication = replace(
            publication,
            identity=replace(publication.identity, **self.identity_overrides),
            directory_identity=replace(
                publication.directory_identity,
                **self.directory_identity_overrides,
            ),
        )
        return replace(publication, **self.publication_overrides)


def test_all_json_is_serialized_before_exclusive_output_directory(tmp_path: Path) -> None:
    output = tmp_path / "batch"
    publisher = FakePublisher()

    with pytest.raises(ValueError, match="Out of range float values"):
        _publish_review_json_batch_for_test(
            tmp_path,
            output.name,
            {"a.json": {"ok": True}, "b.json": {"invalid": float("nan")}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )

    assert not output.exists()
    assert not _run_reservation(tmp_path).exists()
    assert publisher.calls == []


@pytest.mark.parametrize(
    "leaf",
    ["completion-marker.json", "PRIVATE-SUCCESS-INDEX.JSON", "phase-b2-success.json"],
)
def test_phase_b2_success_leaf_is_rejected_before_directory_creation(
    tmp_path: Path,
    leaf: str,
) -> None:
    with pytest.raises(R7S4EvidenceError, match="phase_b2_success_leaf_forbidden"):
        _publish_review_json_batch_for_test(
            tmp_path,
            "batch",
            {leaf: {"passed": True}},
            run_uuid=RUN_UUID,
            publisher=FakePublisher(),
        )
    assert not (tmp_path / "batch").exists()


def test_case_insensitive_collision_is_rejected_before_disk_change(tmp_path: Path) -> None:
    with pytest.raises(R7S4EvidenceError, match="case_insensitive_document_leaf_collision"):
        _publish_review_json_batch_for_test(
            tmp_path,
            "batch",
            {"Report.json": {"value": 1}, "report.JSON": {"value": 2}},
            run_uuid=RUN_UUID,
            publisher=FakePublisher(),
        )
    assert not (tmp_path / "batch").exists()


def test_success_is_deterministic_review_only_and_never_a_success_marker(tmp_path: Path) -> None:
    publisher = FakePublisher()

    result = _publish_review_json_batch_for_test(
        tmp_path,
        "batch",
        {"z-report.json": {"value": 2}, "a-reservation.json": {"value": 1}},
        run_uuid=RUN_UUID,
        publisher=publisher,
    )

    assert publisher.calls == [
        "a-reservation.json",
        "z-report.json",
        AGGREGATE_MANIFEST_LEAF,
        AGGREGATE_INDEX_LEAF,
    ]
    assert [Path(item.final_path).name for item in result.publications] == publisher.calls
    assert all(item.directory_flush_count == 1 for item in result.publications)
    assert result.status == "review_pending"
    assert result.run_reservation_directory == _run_reservation(tmp_path)
    assert result.run_reservation_directory.is_dir()
    assert len(result.run_reservation_identity_sha256) == 64
    assert result.retry_count == 0
    assert result.success_marker_created is False
    assert result.production_go_enabled is False
    assert result.go_evidence_eligible is False
    assert result.aggregate_manifest_publication is result.publications[-2]
    assert result.aggregate_index_publication is result.publications[-1]
    index = json.loads((result.output_directory / AGGREGATE_INDEX_LEAF).read_bytes())
    assert index["index_is_final_review_artifact"] is True
    assert index["go_evidence_eligible"] is False
    assert index["run_reservation"]["run_uuid"] == RUN_UUID
    assert index["run_reservation"]["reservation_path"] == str(result.run_reservation_directory)
    assert (
        index["run_reservation"]["logical_identity_sha256"]
        == result.run_reservation_identity_sha256
    )
    assert index["run_reservation"]["global_one_shot_proven"] is False
    assert index["run_reservation"]["physical_handle_identity_proven"] is False
    assert index["run_reservation"]["power_loss_durability_proven"] is False
    assert index["run_reservation"]["same_token_deletion_protected"] is False
    assert not (result.output_directory / "completion-marker.json").exists()


def test_failure_preserves_published_and_partial_files_without_retry_or_marker(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher(fail_on="b-report.json")

    with pytest.raises(R7S4EvidencePublicationError) as captured:
        _publish_review_json_batch_for_test(
            tmp_path,
            "batch",
            {"a-reservation.json": {"value": 1}, "b-report.json": {"value": 2}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )

    failure = captured.value
    output = tmp_path / "batch"
    assert publisher.calls == [
        "a-reservation.json",
        "b-report.json",
        ATOMIC_FAILURE_SEAL_LEAF,
    ]
    assert (output / "a-reservation.json").is_file()
    assert (output / f".b-report.json.{RUN_UUID}.partial").is_file()
    assert failure.stage == "artifact_publication"
    assert failure.attempted_leaf == "b-report.json"
    assert len(failure.publications) == 1
    assert failure.failure_seal_attempt_count == 1
    assert failure.failure_seal_directory == output
    assert failure.failure_seal_publication is not None
    assert failure.failure_seal_error_type is None
    assert failure.emergency_seal_attempt_count == 0
    seal = json.loads((output / ATOMIC_FAILURE_SEAL_LEAF).read_bytes())
    assert seal["attempted_artifact"]["sha256"]
    assert seal["failed_publication_observation"]["observation_status"].startswith("unknown_")
    assert failure.retry_count == 0
    assert failure.success_marker_created is False
    assert failure.go_evidence_eligible is False
    assert failure.run_reservation_directory == _run_reservation(tmp_path)
    assert failure.run_reservation_directory.is_dir()
    assert not (output / "completion-marker.json").exists()


def test_publisher_without_directory_flush_is_fail_closed(tmp_path: Path) -> None:
    publisher = FakePublisher(directory_flush_count=0)
    failure_publisher = FakePublisher()

    with pytest.raises(R7S4EvidencePublicationError) as captured:
        _publish_review_json_batch_for_test(
            tmp_path,
            "batch",
            {"review.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=publisher,
            failure_publisher=failure_publisher,
        )

    assert isinstance(captured.value.__cause__, R7S4EvidenceError)
    assert "publisher_contract_mismatch" in str(captured.value.__cause__)
    assert captured.value.retry_count == 0
    assert captured.value.success_marker_created is False
    assert failure_publisher.calls == [ATOMIC_FAILURE_SEAL_LEAF]


def test_existing_output_directory_is_never_reused_or_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "batch"
    output.mkdir()
    marker = output / "user-owned.txt"
    marker.write_text("preserve", encoding="utf-8")
    publisher = FakePublisher()

    with pytest.raises(R7S4EvidencePublicationError) as captured:
        _publish_review_json_batch_for_test(
            tmp_path,
            output.name,
            {"review.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )

    assert captured.value.stage == "output_directory_create"
    assert captured.value.publications == ()
    assert publisher.calls == [RESERVATION_FAILURE_SEAL_LEAF]
    assert captured.value.failure_seal_attempt_count == 1
    assert captured.value.failure_seal_publication is not None
    assert captured.value.failure_seal_error_type is None
    assert captured.value.emergency_seal_attempt_count == 0
    assert captured.value.emergency_seal_publication is None
    assert captured.value.emergency_seal_error_type is None
    assert captured.value.emergency_seal_directory is None
    assert _run_reservation(tmp_path).is_dir()
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_reservation_failure_seal_failure_is_explicit_and_not_retried(
    tmp_path: Path,
) -> None:
    output = tmp_path / "batch"
    output.mkdir()
    marker = output / "user-owned.txt"
    marker.write_bytes(b"preserve")
    publisher = FakePublisher(fail_on=RESERVATION_FAILURE_SEAL_LEAF)

    with pytest.raises(R7S4EvidencePublicationError) as captured:
        _publish_review_json_batch_for_test(
            tmp_path,
            output.name,
            {"review.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )

    failure = captured.value
    assert publisher.calls == [RESERVATION_FAILURE_SEAL_LEAF, EMERGENCY_SEAL_LEAF]
    assert failure.failure_seal_attempt_count == 1
    assert failure.failure_seal_publication is None
    assert failure.failure_seal_error_type == "builtins.OSError"
    assert failure.emergency_seal_attempt_count == 1
    assert failure.emergency_seal_publication is not None
    assert failure.emergency_seal_error_type is None
    assert failure.retry_count == 0
    assert marker.read_bytes() == b"preserve"


def test_reservation_failure_and_upper_emergency_failure_are_unknown_no_retry(
    tmp_path: Path,
) -> None:
    _run_reservation(tmp_path).mkdir()
    publisher = FakePublisher(fail_on={RESERVATION_FAILURE_SEAL_LEAF, EMERGENCY_SEAL_LEAF})

    with pytest.raises(R7S4EvidencePublicationError) as captured:
        _publish_review_json_batch_for_test(
            tmp_path,
            "different-output",
            {"review.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )

    failure = captured.value
    assert failure.stage == "run_uuid_reservation_create"
    assert publisher.calls == [RESERVATION_FAILURE_SEAL_LEAF, EMERGENCY_SEAL_LEAF]
    assert failure.failure_seal_attempt_count == 1
    assert failure.failure_seal_publication is None
    assert failure.failure_seal_error_type == "builtins.OSError"
    assert failure.emergency_seal_attempt_count == 1
    assert failure.emergency_seal_publication is None
    assert failure.emergency_seal_error_type == "builtins.OSError"
    assert failure.retry_count == 0
    assert failure.automatic_retry_count == 0
    assert failure.downstream_call_count == 0
    assert failure.manual_intervention_required is True
    assert failure.publication_outcome == "unknown"
    assert failure.success_marker_created is False
    assert not (tmp_path / "different-output").exists()


def test_reservation_failure_directory_collision_uses_one_upper_emergency_seal(
    tmp_path: Path,
) -> None:
    output_leaf = "different-output"
    _run_reservation(tmp_path).mkdir()
    _reservation_failure_directory(tmp_path, output_leaf).mkdir()
    publisher = FakePublisher()

    with pytest.raises(R7S4EvidencePublicationError) as captured:
        _publish_review_json_batch_for_test(
            tmp_path,
            output_leaf,
            {"review.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )

    failure = captured.value
    assert failure.stage == "run_uuid_reservation_create"
    assert publisher.calls == [EMERGENCY_SEAL_LEAF]
    assert failure.failure_seal_attempt_count == 1
    assert failure.failure_seal_directory == _reservation_failure_directory(tmp_path, output_leaf)
    assert failure.failure_seal_publication is None
    assert failure.failure_seal_error_type == "builtins.FileExistsError"
    assert failure.emergency_seal_attempt_count == 1
    assert failure.emergency_seal_publication is not None
    assert failure.emergency_seal_error_type is None
    assert failure.emergency_seal_directory == _reservation_emergency_directory(
        tmp_path, output_leaf
    )
    assert failure.automatic_retry_count == 0
    assert failure.downstream_call_count == 0
    assert failure.manual_intervention_required is True
    assert failure.publication_outcome == "unknown"


def test_reservation_failure_and_upper_emergency_directory_collisions_fail_closed(
    tmp_path: Path,
) -> None:
    output_leaf = "different-output"
    _run_reservation(tmp_path).mkdir()
    _reservation_failure_directory(tmp_path, output_leaf).mkdir()
    _reservation_emergency_directory(tmp_path, output_leaf).mkdir()
    publisher = FakePublisher()

    with pytest.raises(R7S4EvidencePublicationError) as captured:
        _publish_review_json_batch_for_test(
            tmp_path,
            output_leaf,
            {"review.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )

    failure = captured.value
    assert publisher.calls == []
    assert failure.failure_seal_attempt_count == 1
    assert failure.failure_seal_directory == _reservation_failure_directory(tmp_path, output_leaf)
    assert failure.failure_seal_error_type == "builtins.FileExistsError"
    assert failure.emergency_seal_attempt_count == 1
    assert failure.emergency_seal_publication is None
    assert failure.emergency_seal_error_type == "builtins.FileExistsError"
    assert failure.automatic_retry_count == 0
    assert failure.downstream_call_count == 0
    assert failure.manual_intervention_required is True
    assert failure.publication_outcome == "unknown"
    assert failure.go_evidence_eligible is False
    assert failure.success_marker_created is False


def test_same_run_uuid_different_output_leaf_is_rejected_by_canonical_reservation(
    tmp_path: Path,
) -> None:
    first_publisher = FakePublisher()
    first = _publish_review_json_batch_for_test(
        tmp_path,
        "batch-a",
        {"review.json": {"value": 1}},
        run_uuid=RUN_UUID,
        publisher=first_publisher,
    )
    first_sha = hashlib.sha256((first.output_directory / "review.json").read_bytes()).hexdigest()
    second_publisher = FakePublisher()

    with pytest.raises(R7S4EvidencePublicationError) as captured:
        _publish_review_json_batch_for_test(
            tmp_path,
            "batch-b",
            {"review.json": {"value": 2}},
            run_uuid=RUN_UUID,
            publisher=second_publisher,
        )

    failure = captured.value
    assert failure.stage == "run_uuid_reservation_create"
    assert failure.failure_seal_publication is not None
    assert failure.emergency_seal_attempt_count == 0
    assert failure.automatic_retry_count == 0
    assert failure.downstream_call_count == 0
    assert failure.manual_intervention_required is True
    assert second_publisher.calls == [RESERVATION_FAILURE_SEAL_LEAF]
    assert not (tmp_path / "batch-b").exists()
    assert (
        hashlib.sha256((first.output_directory / "review.json").read_bytes()).hexdigest()
        == first_sha
    )
    assert len(list(tmp_path.glob(f"{RUN_RESERVATION_PREFIX}*{RUN_RESERVATION_SUFFIX}"))) == 1


def test_different_run_uuid_has_distinct_parent_reservation(tmp_path: Path) -> None:
    first = _publish_review_json_batch_for_test(
        tmp_path,
        "batch-a",
        {"review.json": {"value": 1}},
        run_uuid=RUN_UUID,
        publisher=FakePublisher(),
    )
    second = _publish_review_json_batch_for_test(
        tmp_path,
        "batch-b",
        {"review.json": {"value": 2}},
        run_uuid=OTHER_RUN_UUID,
        publisher=FakePublisher(),
    )

    assert first.run_reservation_directory == _run_reservation(tmp_path, RUN_UUID)
    assert second.run_reservation_directory == _run_reservation(tmp_path, OTHER_RUN_UUID)
    assert first.run_reservation_directory.is_dir()
    assert second.run_reservation_directory.is_dir()
    assert first.run_reservation_identity_sha256 != second.run_reservation_identity_sha256


def test_concurrent_same_output_identity_has_one_winner_and_no_retry(tmp_path: Path) -> None:
    def invoke() -> str:
        try:
            _publish_review_json_batch_for_test(
                tmp_path,
                "batch",
                {"review.json": {"value": 1}},
                run_uuid=RUN_UUID,
                publisher=FakePublisher(),
            )
        except R7S4EvidencePublicationError:
            return "rejected"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _: invoke(), range(2)))

    assert outcomes == ["published", "rejected"]
    assert (tmp_path / "batch" / "review.json").is_file()


def test_concurrent_same_run_uuid_different_output_leaf_has_one_winner(
    tmp_path: Path,
) -> None:
    def invoke(output_leaf: str) -> tuple[str, str]:
        try:
            _publish_review_json_batch_for_test(
                tmp_path,
                output_leaf,
                {"review.json": {"output": output_leaf}},
                run_uuid=RUN_UUID,
                publisher=FakePublisher(),
            )
        except R7S4EvidencePublicationError as exc:
            assert exc.stage == "run_uuid_reservation_create"
            assert exc.automatic_retry_count == 0
            assert exc.downstream_call_count == 0
            return output_leaf, "rejected"
        return output_leaf, "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(invoke, ("batch-a", "batch-b")))

    assert sorted(status for _, status in outcomes) == ["published", "rejected"]
    winning_leaf = next(leaf for leaf, status in outcomes if status == "published")
    losing_leaf = next(leaf for leaf, status in outcomes if status == "rejected")
    assert (tmp_path / winning_leaf / "review.json").is_file()
    assert not (tmp_path / losing_leaf).exists()
    assert len(list(tmp_path.glob(f"{RUN_RESERVATION_PREFIX}*{RUN_RESERVATION_SUFFIX}"))) == 1


@pytest.mark.parametrize(
    "leaf",
    [AGGREGATE_MANIFEST_LEAF, AGGREGATE_INDEX_LEAF, ATOMIC_FAILURE_SEAL_LEAF],
)
def test_review_control_leaves_are_reserved_before_disk_change(
    tmp_path: Path,
    leaf: str,
) -> None:
    with pytest.raises(R7S4EvidenceError, match="review_control_leaf_reserved"):
        _publish_review_json_batch_for_test(
            tmp_path,
            "batch",
            {leaf: {"forged": True}},
            run_uuid=RUN_UUID,
            publisher=FakePublisher(),
        )
    assert not (tmp_path / "batch").exists()


def test_long_final_leaf_with_impossible_temporary_leaf_is_rejected_preflight(
    tmp_path: Path,
) -> None:
    feasible_leaf = f"{'a' * 129}.json"
    impossible_leaf = f"{'b' * 130}.json"
    assert len(feasible_leaf) == 134
    assert len(impossible_leaf) == 135

    result = _publish_review_json_batch_for_test(
        tmp_path,
        "feasible-batch",
        {feasible_leaf: {"value": 1}},
        run_uuid=RUN_UUID,
        publisher=FakePublisher(),
    )
    assert (result.output_directory / feasible_leaf).is_file()

    publisher = FakePublisher()
    with pytest.raises(R7S4EvidenceError, match="temporary_leaf_not_feasible"):
        _publish_review_json_batch_for_test(
            tmp_path,
            "impossible-batch",
            {impossible_leaf: {"value": 2}},
            run_uuid=OTHER_RUN_UUID,
            publisher=publisher,
        )
    assert publisher.calls == []
    assert not (tmp_path / "impossible-batch").exists()
    assert not _run_reservation(tmp_path, OTHER_RUN_UUID).exists()


def test_planned_final_to_document_temporary_collision_is_rejected_preflight(
    tmp_path: Path,
) -> None:
    colliding_leaf = f".A.JSON.{RUN_UUID.upper()}.PARTIAL"
    publisher = FakePublisher()

    with pytest.raises(
        R7S4EvidenceError,
        match="planned_final_temporary_leaf_collision",
    ):
        _publish_review_json_batch_for_test(
            tmp_path,
            "batch",
            {"a.json": {"value": 1}, colliding_leaf: {"value": 2}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )

    assert publisher.calls == []
    assert not (tmp_path / "batch").exists()
    assert not _run_reservation(tmp_path).exists()


def test_output_leaf_with_impossible_parent_control_leaf_is_rejected_preflight(
    tmp_path: Path,
) -> None:
    output_leaf = f"{'o' * 125}.json"
    publisher = FakePublisher()

    with pytest.raises(R7S4EvidenceError, match="directory_leaf_invalid"):
        _publish_review_json_batch_for_test(
            tmp_path,
            output_leaf,
            {"review.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )

    assert publisher.calls == []
    assert not (tmp_path / output_leaf).exists()
    assert not _run_reservation(tmp_path).exists()


@pytest.mark.parametrize(
    "control_leaf",
    [AGGREGATE_MANIFEST_LEAF, ATOMIC_FAILURE_SEAL_LEAF, EMERGENCY_SEAL_LEAF],
)
def test_planned_final_to_control_temporary_collision_is_rejected_preflight(
    tmp_path: Path,
    control_leaf: str,
) -> None:
    colliding_leaf = f".{control_leaf}.{RUN_UUID}.partial"
    publisher = FakePublisher()

    with pytest.raises(
        R7S4EvidenceError,
        match="planned_final_temporary_leaf_collision",
    ):
        _publish_review_json_batch_for_test(
            tmp_path,
            "batch",
            {colliding_leaf: {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )

    assert publisher.calls == []
    assert not (tmp_path / "batch").exists()
    assert not _run_reservation(tmp_path).exists()


def test_public_writer_rejects_caller_selected_publisher_before_disk_change(
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError):
        publish_review_json_batch(
            tmp_path,
            "batch",
            {"review.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=FakePublisher(),
        )
    assert not (tmp_path / "batch").exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"final_path": "wrong-path.json"},
        {"sha256": "0" * 64},
        {"bytes": 1},
        {"file_flush_count": 1},
        {"file_flush_count": 2.0},
        {"directory_flush_count": 0},
        {"directory_flush_count": 1.0},
        {"bytes": 12.0},
        {"temporary_leaf": ".wrong.partial"},
        {"replace_if_exists": True},
        {"same_handle_readback": False},
        {"file_identity_stable_across_rename": False},
        {"power_loss_durability_proven": True},
        {"same_token_hostile_admin_protected": True},
    ],
)
def test_publication_invariant_mutation_is_sealed_fail_closed(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    invalid = FakePublisher(publication_overrides=overrides)
    seal = FakePublisher()
    with pytest.raises(R7S4EvidencePublicationError) as captured:
        _publish_review_json_batch_for_test(
            tmp_path,
            "batch",
            {"review.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=invalid,
            failure_publisher=seal,
        )
    assert isinstance(captured.value.__cause__, R7S4EvidenceError)
    assert seal.calls == [ATOMIC_FAILURE_SEAL_LEAF]
    assert captured.value.retry_count == 0


def test_file_link_count_must_be_exactly_one(tmp_path: Path) -> None:
    invalid = FakePublisher(identity_overrides={"link_count": 2})
    seal = FakePublisher()
    with pytest.raises(R7S4EvidencePublicationError) as captured:
        _publish_review_json_batch_for_test(
            tmp_path,
            "batch",
            {"review.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=invalid,
            failure_publisher=seal,
        )
    assert isinstance(captured.value.__cause__, R7S4EvidenceError)
    assert seal.calls == [ATOMIC_FAILURE_SEAL_LEAF]


@pytest.mark.parametrize(
    ("identity_kind", "overrides"),
    [
        ("file", {"volume_serial_number": 0}),
        ("file", {"volume_serial_number": True}),
        ("file", {"size": True}),
        ("file", {"link_count": True}),
        ("file", {"attributes": True}),
        ("file", {"reparse_tag": False}),
        ("file", {"file_type": True}),
        ("file", {"owner_sid": "S-invalid"}),
        ("file", {"dacl_present": 1}),
        ("file", {"dacl_protected": False}),
        ("directory", {"volume_serial_number": 0}),
        ("directory", {"volume_serial_number": True}),
        ("directory", {"size": False}),
        ("directory", {"link_count": True}),
        ("directory", {"attributes": True}),
        ("directory", {"reparse_tag": False}),
        ("directory", {"file_type": True}),
        ("directory", {"owner_sid": "S-invalid"}),
        ("directory", {"dacl_present": 1}),
        ("directory", {"dacl_protected": "unknown"}),
    ],
)
def test_identity_numeric_sid_and_dacl_mutations_are_sealed_fail_closed(
    tmp_path: Path,
    identity_kind: str,
    overrides: dict[str, object],
) -> None:
    kwargs = (
        {"identity_overrides": overrides}
        if identity_kind == "file"
        else {"directory_identity_overrides": overrides}
    )
    invalid = FakePublisher(**kwargs)
    seal = FakePublisher()

    with pytest.raises(R7S4EvidencePublicationError) as captured:
        _publish_review_json_batch_for_test(
            tmp_path,
            "batch",
            {"review.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=invalid,
            failure_publisher=seal,
        )

    assert isinstance(captured.value.__cause__, R7S4EvidenceError)
    assert "publisher_contract_mismatch" in str(captured.value.__cause__)
    assert seal.calls == [ATOMIC_FAILURE_SEAL_LEAF]
    assert captured.value.automatic_retry_count == 0
    assert captured.value.downstream_call_count == 0


def test_file_and_directory_volume_and_file_id_cross_invariants_are_required(
    tmp_path: Path,
) -> None:
    for index, mutation in enumerate(("volume_mismatch", "same_file_id")):
        batch_leaf = f"batch-{index}"
        directory_overrides = (
            {"volume_serial_number": 20260903}
            if mutation == "volume_mismatch"
            else {
                "file_id_hex": hashlib.sha256(
                    str(tmp_path / batch_leaf / "review.json").encode("utf-8")
                ).hexdigest()[:32]
            }
        )
        invalid = FakePublisher(directory_identity_overrides=directory_overrides)
        seal = FakePublisher()
        with pytest.raises(R7S4EvidencePublicationError) as captured:
            _publish_review_json_batch_for_test(
                tmp_path,
                batch_leaf,
                {"review.json": {"value": 1}},
                run_uuid=(RUN_UUID if index == 0 else OTHER_RUN_UUID),
                publisher=invalid,
                failure_publisher=seal,
            )
        assert isinstance(captured.value.__cause__, R7S4EvidenceError)
        assert "publisher_contract_mismatch" in str(captured.value.__cause__)
        assert seal.calls == [ATOMIC_FAILURE_SEAL_LEAF]


def test_atomic_failure_seal_failure_creates_one_parent_emergency_seal(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher(fail_on={"review.json", ATOMIC_FAILURE_SEAL_LEAF})
    with pytest.raises(R7S4EvidencePublicationError) as captured:
        _publish_review_json_batch_for_test(
            tmp_path,
            "batch",
            {"review.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )
    failure = captured.value
    assert publisher.calls == ["review.json", ATOMIC_FAILURE_SEAL_LEAF, EMERGENCY_SEAL_LEAF]
    assert failure.failure_seal_attempt_count == 1
    assert failure.failure_seal_publication is None
    assert failure.failure_seal_error_type == "builtins.OSError"
    assert failure.emergency_seal_attempt_count == 1
    assert failure.emergency_seal_publication is not None
    assert failure.emergency_seal_error_type is None
    assert failure.emergency_seal_directory is not None
    assert (failure.emergency_seal_directory / EMERGENCY_SEAL_LEAF).is_file()


def test_upper_emergency_seal_persists_every_prior_validated_publication(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher(fail_on={"b-report.json", ATOMIC_FAILURE_SEAL_LEAF})

    with pytest.raises(R7S4EvidencePublicationError) as captured:
        _publish_review_json_batch_for_test(
            tmp_path,
            "batch",
            {"a-report.json": {"value": 1}, "b-report.json": {"value": 2}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )

    failure = captured.value
    assert len(failure.publications) == 1
    assert failure.emergency_seal_directory is not None
    emergency = json.loads((failure.emergency_seal_directory / EMERGENCY_SEAL_LEAF).read_bytes())
    assert emergency["already_published_count"] == 1
    assert emergency["already_published"] == [failure.publications[0].to_dict()]
    assert emergency["already_published"][0]["final_path"].endswith("a-report.json")
    assert (
        emergency["already_published"][0]["sha256"]
        == hashlib.sha256((tmp_path / "batch" / "a-report.json").read_bytes()).hexdigest()
    )
    assert emergency["already_published"][0]["file_flush_count"] == 2
    assert emergency["already_published"][0]["directory_flush_count"] == 1


def test_preexisting_failure_seal_is_preserved_and_routes_to_emergency(
    tmp_path: Path,
) -> None:
    class OccupiedSealPublisher(FakePublisher):
        def __call__(self, directory: Path, leaf: str, raw: bytes, **kwargs: object):
            if leaf == "review.json":
                (directory / ATOMIC_FAILURE_SEAL_LEAF).write_bytes(b"user-owned-seal")
            return super().__call__(directory, leaf, raw, **kwargs)

    publisher = OccupiedSealPublisher(fail_on="review.json")
    with pytest.raises(R7S4EvidencePublicationError) as captured:
        _publish_review_json_batch_for_test(
            tmp_path,
            "batch",
            {"review.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )
    assert (tmp_path / "batch" / ATOMIC_FAILURE_SEAL_LEAF).read_bytes() == b"user-owned-seal"
    assert publisher.calls == ["review.json", ATOMIC_FAILURE_SEAL_LEAF, EMERGENCY_SEAL_LEAF]
    assert captured.value.emergency_seal_publication is not None


def test_emergency_seal_failure_is_not_retried_or_cleaned(tmp_path: Path) -> None:
    publisher = FakePublisher(
        fail_on={"review.json", ATOMIC_FAILURE_SEAL_LEAF, EMERGENCY_SEAL_LEAF}
    )
    with pytest.raises(R7S4EvidencePublicationError) as captured:
        _publish_review_json_batch_for_test(
            tmp_path,
            "batch",
            {"review.json": {"value": 1}},
            run_uuid=RUN_UUID,
            publisher=publisher,
        )
    failure = captured.value
    assert publisher.calls == ["review.json", ATOMIC_FAILURE_SEAL_LEAF, EMERGENCY_SEAL_LEAF]
    assert failure.emergency_seal_attempt_count == 1
    assert failure.emergency_seal_publication is None
    assert failure.emergency_seal_error_type == "builtins.OSError"
    assert failure.retry_count == 0
    assert failure.emergency_seal_directory is not None
    assert any(failure.emergency_seal_directory.iterdir())


def test_contract_keeps_go_and_power_loss_claims_disabled() -> None:
    contract = source_contract()
    assert contract["all_json_serialized_before_output_directory"] is False
    assert contract["all_planned_success_json_serialized_before_output_directory"] is True
    assert contract["dynamic_failure_seal_serialized_only_after_failure"] is True
    assert contract["canonical_parent_run_uuid_reservation"] is True
    assert contract["same_parent_run_uuid_different_output_leaf_rejected"] is True
    assert contract["run_reservation_writer_preserves_after_success_or_failure"] is True
    assert contract["run_reservation_cleanup_or_removal_attempts"] == 0
    assert contract["run_reservation_physical_handle_identity_proven"] is False
    assert contract["run_reservation_same_token_deletion_protected"] is False
    assert contract["run_reservation_bound_into_aggregate"] is True
    assert contract["temporary_leaf_feasibility_checked_before_directory_create"] is True
    assert contract["planned_final_temporary_collision_checked_before_directory_create"] is True
    assert contract["reservation_collision_manual_intervention_required"] is True
    assert contract["reservation_collision_downstream_call_count"] == 0
    assert contract["reservation_failure_seal_attempts"] == 1
    assert contract["reservation_failure_upper_emergency_seal_attempts"] == 1
    assert contract["fixed_global_reservation_root_wired"] is False
    assert contract["parent_directory_change_global_one_shot_proven"] is False
    assert contract["directory_flush_required_per_artifact"] is True
    assert contract["identity_numeric_exact_type_required"] is True
    assert contract["identity_volume_serial_positive_and_equal_required"] is True
    assert contract["file_directory_file_id_distinct_required"] is True
    assert contract["identity_owner_sid_grammar_required"] is True
    assert contract["file_dacl_protected_required"] is True
    assert contract["directory_dacl_present_required"] is True
    assert contract["directory_dacl_protected_required"] is False
    assert contract["directory_dacl_protected_boolean_observation_required"] is True
    assert contract["aggregate_persists_logical_sha_bytes_inventory"] is True
    assert contract["aggregate_persists_handle_flush_publication_evidence"] is False
    assert contract["handle_flush_publication_evidence_available_in_return_object_only"] is True
    assert contract["retry_count"] == 0
    assert contract["upper_emergency_persists_prior_validated_publications"] is True
    assert contract["cleanup_or_overwrite_on_failure"] is False
    assert contract["phase_b2_success_marker_supported"] is False
    assert contract["power_loss_durability_proven"] is False
    assert contract["same_token_hostile_admin_protected"] is False
    assert contract["go_evidence_eligible"] is False


@pytest.mark.skipif(os.name != "nt", reason="requires Windows directory handle APIs")
def test_real_windows_batch_uses_durable_handle_publication(tmp_path: Path) -> None:
    result = publish_review_json_batch(
        tmp_path,
        "batch",
        {"reservation.json": {"sequence": 1}, "report.json": {"sequence": 2}},
        run_uuid=RUN_UUID,
    )

    assert len(result.publications) == 4
    assert all(item.directory_flush_succeeded for item in result.publications)
    assert all(item.directory_flush_count == 1 for item in result.publications)
    assert result.go_evidence_eligible is False
    assert result.publications[-2].final_path.endswith(AGGREGATE_MANIFEST_LEAF)
    assert result.publications[-1].final_path.endswith(AGGREGATE_INDEX_LEAF)
    assert not (result.output_directory / "completion-marker.json").exists()
