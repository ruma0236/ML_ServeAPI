from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

from evm.control_panel import isolated_ct


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def source_evidence(tmp_path: Path, monkeypatch):
    data_root = tmp_path / "data-root"
    ct_root = tmp_path / "ct-root"
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", str(data_root))
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")
    monkeypatch.setenv("EVM_HOST_CT_ROOT", str(ct_root))
    monkeypatch.setenv("EVM_CT_MOUNT_ROOT", "/mnt/evm-ct")
    image_root = data_root / "data" / "raw"
    image_root.mkdir(parents=True)
    train_image = image_root / "train.png"
    test_image = image_root / "test.png"
    train_image.write_bytes(b"train-image")
    test_image.write_bytes(b"test-image")
    train_record = {
        "record_id": "train-1",
        "dataset_version": "visa-test-v1",
        "image_uri": "/mnt/evm-data/data/raw/train.png",
        "image_path": "/mnt/evm-data/data/raw/train.png",
        "label_type": "normal",
        "split": "train",
        "content_sha256": hashlib.sha256(train_image.read_bytes()).hexdigest(),
    }
    test_record = {
        "record_id": "test-1",
        "dataset_version": "visa-test-v1",
        "image_uri": "/mnt/evm-data/data/raw/test.png",
        "image_path": "/mnt/evm-data/data/raw/test.png",
        "label_type": "anomaly",
        "split": "test",
        "content_sha256": hashlib.sha256(test_image.read_bytes()).hexdigest(),
    }
    shard_root = data_root / "data" / "validated" / "shards"
    train_shard = shard_root / "train.jsonl"
    test_shard = shard_root / "test.jsonl"
    write_jsonl(train_shard, [train_record])
    write_jsonl(test_shard, [test_record])
    index = shard_root / "shard_index.json"
    write_json(
        index,
        {
            "schema_version": "evm.dataset_shards.v1",
            "identity_sha256": "a" * 64,
            "dataset_version": "visa-test-v1",
            "shards": [
                {"shard_id": "train-0000", "split": "train", "path": str(train_shard)},
                {"shard_id": "test-0001", "split": "test", "path": str(test_shard)},
            ],
        },
    )
    return data_root, ct_root, index, train_record, test_record


def evaluation_evidence(
    tmp_path: Path,
    snapshot,
    train_record: dict[str, object],
    *,
    overlap: bool = False,
):
    fold = tmp_path / "fold_manifest.json"
    assignment_id = (
        isolated_ct.record_id(json.loads(Path(snapshot.manifest_uri).read_text().splitlines()[0]))
        if overlap
        else isolated_ct.record_id(train_record)
    )
    write_json(
        fold,
        {
            "schema_version": "evm.experiment_fold_manifest.v2",
            "experiment_id": "lifecycle-run-test",
            "dataset_version": "visa-test-v1",
            "holdout_access_policy": "isolated_control_plane_only",
            "holdout_used_for_selection": False,
            "ct_evidence_exposed": False,
            "assignments": [{"record_id": assignment_id, "repeat": 0, "fold": 0}],
        },
    )
    training_job = tmp_path / "training-job.json"
    write_json(
        training_job,
        {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "env": [
                                    {
                                        "name": "EVM_TRAINING_DATA_SCOPE",
                                        "value": "development-only",
                                    }
                                ],
                                "volumeMounts": [
                                    {
                                        "name": "large-data",
                                        "mountPath": "/mnt/evm-data/data",
                                        "readOnly": True,
                                    }
                                ],
                            }
                        ]
                    }
                }
            }
        },
    )
    candidate = tmp_path / "candidate_summary.json"
    write_json(
        candidate,
        {
            "candidate_id": "efficientnet-b0-test",
            "dataset_version": "visa-test-v1",
            "architecture": "efficientnet-b0",
            "conditions": {"input_size": 224},
            "decision_threshold": 0.5,
        },
    )
    model = tmp_path / "model.pt"
    model.write_bytes(b"real-model-artifact")
    return fold, training_job, candidate, model


def test_real_snapshot_copy_and_evaluator_pass_fail_closed_contract(tmp_path, monkeypatch):
    _data_root, ct_root, index, train_record, _test_record = source_evidence(
        tmp_path,
        monkeypatch,
    )
    snapshot = isolated_ct.create_ct_snapshot(
        index,
        lifecycle_run_id="lifecycle-run-test",
        profile_id="profile-test",
        profile_version=1,
        profile_digest="b" * 64,
    )
    fold, training_job, candidate, model = evaluation_evidence(
        tmp_path,
        snapshot,
        train_record,
    )
    monkeypatch.setattr(
        isolated_ct,
        "evaluate_model",
        lambda *_args, **_kwargs: (
            {"accuracy": 0.95, "f1": 0.91, "auroc": 0.97},
            "cuda",
        ),
    )

    evaluation = isolated_ct.evaluate_ct_snapshot(
        snapshot.snapshot_uri,
        fold_manifest_uri=fold,
        training_job_manifest_uri=training_job,
        candidate_summary_uri=candidate,
        model_artifact_uri=model,
        metric_thresholds={"accuracy": 0.9, "f1": 0.9, "auroc": 0.9},
    )

    assert snapshot.record_count == 1
    assert snapshot.byte_count == len(b"test-image")
    assert snapshot.training_mount_isolated is True
    assert Path(snapshot.manifest_uri).is_file()
    assert ct_root.as_posix() in snapshot.snapshot_uri
    snapshot_record = json.loads(Path(snapshot.manifest_uri).read_text().splitlines()[0])
    assert isolated_ct.record_id(snapshot_record) == isolated_ct.record_id(_test_record)
    assert evaluation.status == "pass"
    assert evaluation.decision == "pass"
    assert evaluation.overlap_count == 0
    assert evaluation.mutated is False
    assert evaluation.device == "cuda"
    assert evaluation.checks["training_mount_isolation"] == "pass"


def test_snapshot_reuses_identical_holdout_across_lifecycle_runs(tmp_path, monkeypatch):
    _data_root, _ct_root, index, _train_record, _test_record = source_evidence(
        tmp_path,
        monkeypatch,
    )
    first = isolated_ct.create_ct_snapshot(
        index,
        lifecycle_run_id="lifecycle-run-first",
        profile_id="profile-test",
        profile_version=1,
        profile_digest="b" * 64,
    )

    second = isolated_ct.create_ct_snapshot(
        index,
        lifecycle_run_id="lifecycle-run-second",
        profile_id="profile-test",
        profile_version=2,
        profile_digest="c" * 64,
    )

    assert second.snapshot_id == first.snapshot_id
    assert second.snapshot_uri == first.snapshot_uri
    assert second.source_records_sha256 == first.source_records_sha256
    assert second.snapshot_digest == first.snapshot_digest


def test_snapshot_reuse_fails_closed_when_manifest_was_mutated(tmp_path, monkeypatch):
    _data_root, _ct_root, index, _train_record, _test_record = source_evidence(
        tmp_path,
        monkeypatch,
    )
    snapshot = isolated_ct.create_ct_snapshot(
        index,
        lifecycle_run_id="lifecycle-run-first",
        profile_id="profile-test",
        profile_version=1,
        profile_digest="b" * 64,
    )
    manifest = Path(snapshot.manifest_uri)
    manifest.chmod(stat.S_IWRITE | stat.S_IREAD)
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    try:
        isolated_ct.create_ct_snapshot(
            index,
            lifecycle_run_id="lifecycle-run-second",
            profile_id="profile-test",
            profile_version=2,
            profile_digest="c" * 64,
        )
    except ValueError as exc:
        assert str(exc) == "ct_snapshot_identity_collision"
    else:
        raise AssertionError("mutated CT snapshot must not be reused")


def test_evaluator_blocks_training_overlap(tmp_path, monkeypatch):
    _data_root, _ct_root, index, train_record, _test_record = source_evidence(
        tmp_path,
        monkeypatch,
    )
    snapshot = isolated_ct.create_ct_snapshot(
        index,
        lifecycle_run_id="lifecycle-run-test",
        profile_id="profile-test",
        profile_version=1,
        profile_digest="b" * 64,
    )
    fold, training_job, candidate, model = evaluation_evidence(
        tmp_path,
        snapshot,
        train_record,
        overlap=True,
    )
    monkeypatch.setattr(
        isolated_ct,
        "evaluate_model",
        lambda *_args, **_kwargs: ({"accuracy": 1.0, "f1": 1.0, "auroc": 1.0}, "cuda"),
    )

    evaluation = isolated_ct.evaluate_ct_snapshot(
        snapshot.snapshot_uri,
        fold_manifest_uri=fold,
        training_job_manifest_uri=training_job,
        candidate_summary_uri=candidate,
        model_artifact_uri=model,
    )

    assert evaluation.status == "blocked"
    assert evaluation.overlap_count == 1
    assert "ct_training_ct_overlap_failed" in evaluation.blockers


def test_evaluator_blocks_snapshot_mutation_and_missing_training_evidence(
    tmp_path,
    monkeypatch,
):
    _data_root, _ct_root, index, train_record, _test_record = source_evidence(
        tmp_path,
        monkeypatch,
    )
    snapshot = isolated_ct.create_ct_snapshot(
        index,
        lifecycle_run_id="lifecycle-run-test",
        profile_id="profile-test",
        profile_version=1,
        profile_digest="b" * 64,
    )
    fold, _training_job, candidate, model = evaluation_evidence(
        tmp_path,
        snapshot,
        train_record,
    )
    manifest = Path(snapshot.manifest_uri)
    manifest.chmod(stat.S_IWRITE | stat.S_IREAD)
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    monkeypatch.setattr(
        isolated_ct,
        "evaluate_model",
        lambda *_args, **_kwargs: ({"accuracy": 1.0, "f1": 1.0, "auroc": 1.0}, "cuda"),
    )

    evaluation = isolated_ct.evaluate_ct_snapshot(
        snapshot.snapshot_uri,
        fold_manifest_uri=fold,
        training_job_manifest_uri=tmp_path / "missing-training-job.json",
        candidate_summary_uri=candidate,
        model_artifact_uri=model,
    )

    assert evaluation.status == "blocked"
    assert evaluation.mutated is True
    assert evaluation.training_mount_isolated is False
    assert "ct_manifest_digest_failed" in evaluation.blockers
    assert "ct_training_mount_isolation_failed" in evaluation.blockers


def test_runtime_roots_use_container_mounts_when_windows_paths_are_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    data_mount = tmp_path / "mounted-data"
    ct_mount = tmp_path / "mounted-ct"
    data_mount.mkdir()
    ct_mount.mkdir()
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", "Z:/missing/evm-data")
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", str(data_mount))
    monkeypatch.setenv("EVM_HOST_CT_ROOT", "Z:/missing/evm-ct")
    monkeypatch.setenv("EVM_CT_MOUNT_ROOT", str(ct_mount))

    assert isolated_ct.host_data_root() == data_mount
    assert isolated_ct.host_ct_root() == ct_mount
    assert isolated_ct.ct_runtime_path("Z:/missing/evm-ct/snapshots/a.json") == (
        ct_mount / "snapshots" / "a.json"
    )
    assert isolated_ct.canonical_ct_uri(ct_mount / "snapshots" / "a.json") == (
        "Z:/missing/evm-ct/snapshots/a.json"
    )
    converted = isolated_ct.docker_desktop_path(data_mount)
    if data_mount.drive:
        assert converted.startswith("/run/desktop/mnt/host/")
    else:
        assert converted == str(data_mount).replace("\\", "/")
