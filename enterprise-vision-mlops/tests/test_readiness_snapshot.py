from __future__ import annotations

import json
from pathlib import Path

from evm.core.readiness_snapshot import (
    capture_readiness_snapshot,
    file_sha256,
    load_readiness_snapshot,
    read_json,
)


CANDIDATE_ID = "effnet-b7-img600-finetune-adamw"
DATASET_VERSION = "visa-open-data-f1f1c9ee9922"
SOURCE_DIGEST = "4" * 64


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def source_files(tmp_path: Path, *, split_snapshot: bool) -> tuple[Path, Path, Path]:
    dataset = tmp_path / "sources" / "dataset-summary.json"
    quality = tmp_path / "sources" / "quality-summary.json"
    source = tmp_path / "sources" / "source.json"
    write_json(
        dataset,
        {
            "dataset_version": DATASET_VERSION,
            "valid_records": 6,
        },
    )
    write_json(
        quality,
        {
            "status": "pass",
            "dataset_version": DATASET_VERSION,
            "record_count": 6,
        },
    )
    source_payload: dict[str, object] = {
        "schema_version": "evm.w7.efficientnet_split_manifest.v1",
        "record_count": 6,
        "source_shard_index_sha256": SOURCE_DIGEST,
    }
    if not split_snapshot:
        source_payload = {
            "schema_version": "evm.dataset_shards.v1",
            "record_count": 6,
        }
    write_json(source, source_payload)
    return dataset, quality, source


def test_snapshot_survives_mutation_of_original_latest_sources(tmp_path: Path) -> None:
    dataset, quality, source = source_files(tmp_path, split_snapshot=True)
    manifest = capture_readiness_snapshot(
        output_dir=tmp_path / "candidate-run" / "_readiness_inputs",
        candidate_id=CANDIDATE_ID,
        dataset_version=DATASET_VERSION,
        expected_record_count=6,
        expected_source_digest=SOURCE_DIGEST,
        dataset_metadata_path=dataset,
        quality_report_path=quality,
        source_shard_path=source,
    )
    manifest_digest = file_sha256(manifest)

    write_json(
        dataset,
        {
            "dataset_version": "visa-open-data-new-latest",
            "valid_records": 6,
        },
    )
    write_json(
        quality,
        {
            "status": "blocked",
            "dataset_version": "visa-open-data-new-latest",
            "record_count": 6,
        },
    )

    selection = load_readiness_snapshot(
        manifest,
        candidate_id=CANDIDATE_ID,
        dataset_version=DATASET_VERSION,
        expected_record_count=6,
        expected_source_digest=SOURCE_DIGEST,
        expected_manifest_digest=manifest_digest,
        required=True,
    )

    assert selection is not None
    assert selection.blockers == ()
    assert selection.dataset_metadata_path is not None
    assert read_json(selection.dataset_metadata_path)["dataset_version"] == DATASET_VERSION
    assert selection.quality_report_path is not None
    assert read_json(selection.quality_report_path)["status"] == "pass"
    assert selection.source_shard_path is not None
    assert selection.expected_digests["source_shard"] == file_sha256(
        selection.source_shard_path
    )


def test_snapshot_loader_blocks_tampered_copied_evidence(tmp_path: Path) -> None:
    dataset, quality, source = source_files(tmp_path, split_snapshot=True)
    manifest = capture_readiness_snapshot(
        output_dir=tmp_path / "candidate-run" / "_readiness_inputs",
        candidate_id=CANDIDATE_ID,
        dataset_version=DATASET_VERSION,
        expected_record_count=6,
        expected_source_digest=SOURCE_DIGEST,
        dataset_metadata_path=dataset,
        quality_report_path=quality,
        source_shard_path=source,
    )
    manifest_digest = file_sha256(manifest)
    copied_quality = manifest.parent / "quality_report.json"
    copied_quality.write_text("{}\n", encoding="utf-8")

    selection = load_readiness_snapshot(
        manifest,
        candidate_id=CANDIDATE_ID,
        dataset_version=DATASET_VERSION,
        expected_record_count=6,
        expected_source_digest=SOURCE_DIGEST,
        expected_manifest_digest=manifest_digest,
        required=True,
    )

    assert selection is not None
    assert "readiness_snapshot_quality_report_digest_mismatch" in selection.blockers


def test_snapshot_requires_pinned_manifest_digest(tmp_path: Path) -> None:
    dataset, quality, source = source_files(tmp_path, split_snapshot=True)
    manifest = capture_readiness_snapshot(
        output_dir=tmp_path / "candidate-run" / "_readiness_inputs",
        candidate_id=CANDIDATE_ID,
        dataset_version=DATASET_VERSION,
        expected_record_count=6,
        expected_source_digest=SOURCE_DIGEST,
        dataset_metadata_path=dataset,
        quality_report_path=quality,
        source_shard_path=source,
    )

    selection = load_readiness_snapshot(
        manifest,
        candidate_id=CANDIDATE_ID,
        dataset_version=DATASET_VERSION,
        expected_record_count=6,
        expected_source_digest=SOURCE_DIGEST,
        required=True,
    )

    assert selection is not None
    assert "readiness_snapshot_manifest_digest_missing" in selection.blockers


def test_snapshot_is_idempotent_and_rejects_source_replacement(tmp_path: Path) -> None:
    dataset, quality, source = source_files(tmp_path, split_snapshot=True)
    kwargs = {
        "output_dir": tmp_path / "candidate-run" / "_readiness_inputs",
        "candidate_id": CANDIDATE_ID,
        "dataset_version": DATASET_VERSION,
        "expected_record_count": 6,
        "expected_source_digest": SOURCE_DIGEST,
        "dataset_metadata_path": dataset,
        "quality_report_path": quality,
        "source_shard_path": source,
    }
    first = capture_readiness_snapshot(**kwargs)
    first_digest = file_sha256(first)

    second = capture_readiness_snapshot(**kwargs)
    assert second == first
    assert file_sha256(second) == first_digest

    write_json(
        quality,
        {
            "status": "pass",
            "dataset_version": DATASET_VERSION,
            "record_count": 6,
            "note": "different evidence",
        },
    )
    try:
        capture_readiness_snapshot(**kwargs)
    except ValueError as exc:
        assert str(exc) == "readiness_snapshot_immutable_conflict"
    else:
        raise AssertionError("immutable snapshot replacement must be rejected")
