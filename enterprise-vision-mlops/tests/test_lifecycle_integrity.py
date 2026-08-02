from __future__ import annotations

import json
from pathlib import Path

import pytest

from evm.control_panel.lifecycle_guards import file_digest
from evm.control_panel.lifecycle_integrity import (
    LifecycleIntegrityBlocked,
    build_lifecycle_release_submission,
    validate_lifecycle_data_integrity,
    validate_lifecycle_release_submission,
)
from evm.core.dataset import shard_index_identity_digest


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def data_fixture(root: Path) -> tuple[Path, Path]:
    records: list[dict[str, object]] = [
        {
            "sample_id": "train-1",
            "content_sha256": "1" * 64,
            "label": "normal",
            "split": "train",
        },
        {
            "sample_id": "validation-1",
            "content_sha256": "2" * 64,
            "label": "normal",
            "split": "validation",
        },
        {
            "sample_id": "test-1",
            "content_sha256": "3" * 64,
            "label": "anomaly",
            "split": "test",
        },
    ]
    source_path = root / "data" / "quality" / "quality_manifest.jsonl"
    write_jsonl(source_path, records)
    shard_root = root / "data" / "shards"
    shards: list[dict[str, object]] = []
    for index, record in enumerate(records):
        split = str(record["split"])
        shard_path = shard_root / f"{split}.jsonl"
        write_jsonl(shard_path, [record])
        shards.append(
            {
                "shard_id": f"{split}-{index:04d}",
                "split": split,
                "path": str(shard_path),
                "record_count": 1,
                "first_sample_id": record["sample_id"],
                "last_sample_id": record["sample_id"],
            }
        )
    index = {
        "schema_version": "evm.dataset_shards.v1",
        "input_manifest": str(source_path),
        "records_per_shard": 1,
        "record_count": 3,
        "shard_count": 3,
        "split_counts": {"train": 1, "validation": 1, "test": 1},
        "label_counts": {"normal": 2, "anomaly": 1},
        "label_type_counts": {"binary": 3},
        "shards": shards,
    }
    index["identity_sha256"] = shard_index_identity_digest(index)
    index_path = shard_root / "shard_index.json"
    write_json(index_path, index)
    return index_path, source_path


def release_fixture(root: Path) -> tuple[Path, dict[str, str]]:
    run_id = "lifecycle-integrity-test"
    source_commit = "a" * 40
    candidate_id = "efficientnet-b0-integrity-test"
    dataset_version = "visa-integrity-v1"
    mlflow_run_id = "mlflow-integrity-test"
    ct_evaluation_id = "ct-integrity-test"
    image_digest = "sha256:" + "b" * 64
    model_path = root / "model" / "model.pt"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(b"deterministic-real-model-artifact")
    model_digest = file_digest(model_path)
    readiness_path = root / "readiness.json"
    matrix_path = root / "model-matrix.json"
    ct_path = root / "ct-evaluation.json"
    write_json(
        readiness_path,
        {
            "decision": "ready",
            "status": "pass",
            "candidate_id": candidate_id,
            "dataset_version": dataset_version,
            "checks": [
                {
                    "check_id": "model_artifact",
                    "evidence_uri": str(model_path),
                    "observed": {"actual_sha256": model_digest},
                },
                {
                    "check_id": "kubernetes_runtime",
                    "observed": {"serving_image_digest": image_digest},
                },
                {
                    "check_id": "mlflow_run",
                    "observed": {"run_id": mlflow_run_id},
                },
            ],
        },
    )
    write_json(
        matrix_path,
        {
            "status": "pass",
            "candidates": [
                {
                    "candidate_id": candidate_id,
                    "status": "pass",
                    "dataset_version": dataset_version,
                    "model_sha256": model_digest,
                    "mlflow_run_id": mlflow_run_id,
                    "model_artifact": str(model_path),
                }
            ],
        },
    )
    write_json(
        ct_path,
        {
            "evaluation_id": ct_evaluation_id,
            "lifecycle_run_id": run_id,
            "candidate_id": candidate_id,
            "dataset_version": dataset_version,
            "status": "pass",
            "decision": "pass",
            "model_sha256": model_digest,
        },
    )
    submission = build_lifecycle_release_submission(
        artifact_root=root,
        run_id=run_id,
        source_commit=source_commit,
        readiness_uri=str(readiness_path),
        model_matrix_uri=str(matrix_path),
        ct_evaluation_uri=str(ct_path),
    )
    return submission, {
        "run_id": run_id,
        "source_commit": source_commit,
        "candidate_id": candidate_id,
        "model_digest": model_digest,
        "ct_evaluation_id": ct_evaluation_id,
        "model_path": str(model_path),
        "readiness_path": str(readiness_path),
    }


def test_canonical_data_integrity_is_repeatable(tmp_path: Path) -> None:
    data_fixture(tmp_path)

    reports = []
    for _ in range(3):
        report_path = validate_lifecycle_data_integrity(tmp_path)
        reports.append(json.loads(report_path.read_text(encoding="utf-8")))

    assert all(report["decision"] == "pass" for report in reports)
    assert len({report["decision_fingerprint"] for report in reports}) == 1
    assert reports[0]["observed_record_count"] == 3


def test_corrupt_shard_identity_blocks_three_replays_deterministically(
    tmp_path: Path,
) -> None:
    index_path, _source = data_fixture(tmp_path)
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["identity_sha256"] = "0" * 64
    write_json(index_path, payload)

    fingerprints = []
    for _ in range(3):
        with pytest.raises(LifecycleIntegrityBlocked) as exc_info:
            validate_lifecycle_data_integrity(tmp_path)
        assert "integrity_shard_index_identity_mismatch" in exc_info.value.blockers
        report = json.loads(
            (tmp_path / "data" / "integrity-validation.json").read_text(
                encoding="utf-8"
            )
        )
        fingerprints.append(report["decision_fingerprint"])

    assert len(set(fingerprints)) == 1


def test_cross_split_duplicate_blocks_three_replays_deterministically(
    tmp_path: Path,
) -> None:
    index_path, _source = data_fixture(tmp_path)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    train_path = Path(index["shards"][0]["path"])
    validation_path = Path(index["shards"][1]["path"])
    validation_path.write_text(train_path.read_text(encoding="utf-8"), encoding="utf-8")

    decisions = []
    for _ in range(3):
        with pytest.raises(LifecycleIntegrityBlocked) as exc_info:
            validate_lifecycle_data_integrity(tmp_path)
        assert "integrity_duplicate_record_identity" in exc_info.value.blockers
        assert "integrity_split_leakage_detected" in exc_info.value.blockers
        report = json.loads(
            (tmp_path / "data" / "integrity-validation.json").read_text(
                encoding="utf-8"
            )
        )
        decisions.append((tuple(report["blockers"]), report["decision_fingerprint"]))

    assert len(set(decisions)) == 1


def test_empty_data_is_fail_closed(tmp_path: Path) -> None:
    source_path = tmp_path / "data" / "quality" / "quality_manifest.jsonl"
    write_jsonl(source_path, [])
    index_path = tmp_path / "data" / "shards" / "shard_index.json"
    index = {
        "schema_version": "evm.dataset_shards.v1",
        "input_manifest": str(source_path),
        "records_per_shard": 1,
        "record_count": 0,
        "shard_count": 0,
        "split_counts": {},
        "label_counts": {},
        "label_type_counts": {},
        "shards": [],
    }
    index["identity_sha256"] = shard_index_identity_digest(index)
    write_json(index_path, index)

    with pytest.raises(LifecycleIntegrityBlocked) as exc_info:
        validate_lifecycle_data_integrity(tmp_path)

    assert {
        "integrity_shards_missing",
        "integrity_source_records_empty",
        "integrity_shard_records_empty",
    }.issubset(exc_info.value.blockers)


def test_release_submission_exact_identity_passes_three_replays(tmp_path: Path) -> None:
    submission, identity = release_fixture(tmp_path)

    results = [
        validate_lifecycle_release_submission(
            submission,
            run_id=identity["run_id"],
            source_commit=identity["source_commit"],
            expected_candidate_id=identity["candidate_id"],
            expected_model_digest=identity["model_digest"],
            expected_ct_evaluation_id=identity["ct_evaluation_id"],
        )
        for _ in range(3)
    ]

    assert all(result["decision"] == "pass" for result in results)
    assert len({result["decision_fingerprint"] for result in results}) == 1


def test_release_identity_mismatch_blocks_three_replays(tmp_path: Path) -> None:
    submission, identity = release_fixture(tmp_path)

    fingerprints = []
    for _ in range(3):
        with pytest.raises(LifecycleIntegrityBlocked) as exc_info:
            validate_lifecycle_release_submission(
                submission,
                run_id=identity["run_id"],
                source_commit=identity["source_commit"],
                expected_model_digest="f" * 64,
            )
        assert exc_info.value.blockers == ["release_model_digest_mismatch"]
        fingerprints.append(exc_info.value.decision_fingerprint)

    assert None not in fingerprints
    assert len(set(fingerprints)) == 1


def test_model_artifact_tamper_is_fail_closed(tmp_path: Path) -> None:
    submission, identity = release_fixture(tmp_path)
    Path(identity["model_path"]).write_bytes(b"tampered-model-artifact")

    with pytest.raises(LifecycleIntegrityBlocked) as exc_info:
        validate_lifecycle_release_submission(
            submission,
            run_id=identity["run_id"],
            source_commit=identity["source_commit"],
        )

    assert "release_model_artifact_evidence_digest_mismatch" in exc_info.value.blockers
    assert "release_model_artifact_digest_mismatch" in exc_info.value.blockers


def test_readiness_evidence_tamper_is_fail_closed(tmp_path: Path) -> None:
    submission, identity = release_fixture(tmp_path)
    readiness_path = Path(identity["readiness_path"])
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    readiness["decision"] = "blocked"
    write_json(readiness_path, readiness)

    with pytest.raises(LifecycleIntegrityBlocked) as exc_info:
        validate_lifecycle_release_submission(
            submission,
            run_id=identity["run_id"],
            source_commit=identity["source_commit"],
        )

    assert "release_readiness_evidence_digest_mismatch" in exc_info.value.blockers
    assert "release_readiness_not_ready" in exc_info.value.blockers
