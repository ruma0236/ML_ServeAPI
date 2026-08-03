from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evm.control_panel.lifecycle_guards import file_digest
from evm.control_panel.lifecycle_integrity import (
    LifecycleIntegrityBlocked,
    build_lifecycle_release_submission,
    validate_lifecycle_data_integrity,
    validate_lifecycle_release_submission,
)
from evm.control_panel.lifecycle_integrity_injection import (
    DATA_ACTION,
    RELEASE_ACTION,
    LifecycleIntegrityInjectionBlocked,
    consume_data_integrity_injection,
    injection_receipt_path,
    issue_lifecycle_integrity_injection,
    release_submission_for_admission,
    validate_injection_contract,
)
from evm.core.dataset import shard_index_identity_digest


NOW = datetime(2026, 8, 3, 1, 30, tzinfo=UTC)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def lifecycle_identity(root: Path, *, run_id: str = "lifecycle-injection-test"):
    root = root / run_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "identity.envelope.json").write_text("{}", encoding="utf-8")
    return SimpleNamespace(
        run_id=run_id,
        lifecycle_series_id="series-integrity-test",
        attempt_id="attempt-integrity-test",
        correlation_id="correlation-integrity-test",
        profile_digest="a" * 64,
        effective_config_digest="b" * 64,
        source_commit="c" * 40,
        identity_envelope_uri=str(root / "identity.envelope.json"),
        artifact_root=str(root),
    )


def data_fixture(root: Path) -> Path:
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
    source = root / "data" / "quality" / "quality_manifest.jsonl"
    write_jsonl(source, records)
    shard_root = root / "data" / "shards"
    shards = []
    for index, record in enumerate(records):
        split = str(record["split"])
        path = shard_root / f"{split}.jsonl"
        write_jsonl(path, [record])
        shards.append(
            {
                "shard_id": f"{split}-{index:04d}",
                "split": split,
                "path": str(path),
                "record_count": 1,
                "first_sample_id": record["sample_id"],
                "last_sample_id": record["sample_id"],
            }
        )
    payload = {
        "schema_version": "evm.dataset_shards.v1",
        "input_manifest": str(source),
        "records_per_shard": 1,
        "record_count": 3,
        "shard_count": 3,
        "split_counts": {"train": 1, "validation": 1, "test": 1},
        "label_counts": {"normal": 2, "anomaly": 1},
        "label_type_counts": {"binary": 3},
        "shards": shards,
    }
    payload["identity_sha256"] = shard_index_identity_digest(payload)
    index = shard_root / "shard_index.json"
    write_json(index, payload)
    return index


def release_fixture(root: Path, run) -> tuple[Path, dict[str, str]]:
    model = root / "model" / "model.pt"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"run-local-real-model")
    model_digest = file_digest(model)
    candidate = "candidate-integrity-test"
    dataset = "visa-integrity-test"
    mlflow = "mlflow-integrity-test"
    ct_id = "ct-integrity-test"
    readiness = root / "readiness.json"
    matrix = root / "model-matrix.json"
    ct = root / "ct-evaluation.json"
    write_json(
        readiness,
        {
            "decision": "ready",
            "status": "pass",
            "candidate_id": candidate,
            "dataset_version": dataset,
            "checks": [
                {
                    "check_id": "model_artifact",
                    "evidence_uri": str(model),
                    "observed": {"actual_sha256": model_digest},
                },
                {
                    "check_id": "kubernetes_runtime",
                    "observed": {"serving_image_digest": "sha256:" + "d" * 64},
                },
                {"check_id": "mlflow_run", "observed": {"run_id": mlflow}},
            ],
        },
    )
    write_json(
        matrix,
        {
            "status": "pass",
            "candidates": [
                {
                    "candidate_id": candidate,
                    "status": "pass",
                    "dataset_version": dataset,
                    "model_sha256": model_digest,
                    "mlflow_run_id": mlflow,
                    "model_artifact": str(model),
                }
            ],
        },
    )
    write_json(
        ct,
        {
            "evaluation_id": ct_id,
            "lifecycle_run_id": run.run_id,
            "candidate_id": candidate,
            "dataset_version": dataset,
            "status": "pass",
            "decision": "pass",
            "model_sha256": model_digest,
        },
    )
    submission = build_lifecycle_release_submission(
        artifact_root=root,
        run_id=run.run_id,
        source_commit=run.source_commit,
        readiness_uri=str(readiness),
        model_matrix_uri=str(matrix),
        ct_evaluation_uri=str(ct),
    )
    return submission, {
        "candidate_id": candidate,
        "model_digest": model_digest,
        "ct_evaluation_id": ct_id,
    }


def test_data_injection_is_exact_single_use_and_blocks_integrity(tmp_path: Path) -> None:
    run = lifecycle_identity(tmp_path / "run")
    index = data_fixture(Path(run.artifact_root))
    canonical_identity = json.loads(index.read_text(encoding="utf-8"))["identity_sha256"]
    issue_lifecycle_integrity_injection(
        run,
        action=DATA_ACTION,
        actor="integrity-validator",
        reason="Exercise run-local L2 integrity admission",
        issued_at=NOW,
    )

    receipt = consume_data_integrity_injection(run, observed_at=NOW + timedelta(seconds=1))

    assert receipt == injection_receipt_path(run, DATA_ACTION)
    assert canonical_identity != "0" * 64
    with pytest.raises(LifecycleIntegrityBlocked) as exc_info:
        validate_lifecycle_data_integrity(Path(run.artifact_root))
    assert "integrity_shard_index_identity_mismatch" in exc_info.value.blockers
    with pytest.raises(LifecycleIntegrityInjectionBlocked) as replay:
        consume_data_integrity_injection(run, observed_at=NOW + timedelta(seconds=2))
    assert "integrity_injection_already_consumed" in replay.value.blockers


def test_contract_is_fail_closed_on_identity_mismatch_and_expiry(tmp_path: Path) -> None:
    run = lifecycle_identity(tmp_path / "run")
    data_fixture(Path(run.artifact_root))
    issue_lifecycle_integrity_injection(
        run,
        action=DATA_ACTION,
        actor="integrity-validator",
        reason="Exercise exact identity and expiry gates",
        ttl_seconds=30,
        issued_at=NOW,
    )
    wrong = SimpleNamespace(
        **{**run.__dict__, "correlation_id": "different-correlation"}
    )
    with pytest.raises(LifecycleIntegrityInjectionBlocked) as mismatch:
        validate_injection_contract(wrong, DATA_ACTION, observed_at=NOW)
    assert "integrity_injection_correlation_id_mismatch" in mismatch.value.blockers
    with pytest.raises(LifecycleIntegrityInjectionBlocked) as expired:
        validate_injection_contract(run, DATA_ACTION, observed_at=NOW + timedelta(seconds=30))
    assert "integrity_injection_expired_or_not_yet_valid" in expired.value.blockers


def test_release_injection_preserves_canonical_and_blocks_admission(tmp_path: Path) -> None:
    run = lifecycle_identity(tmp_path / "run")
    submission, identity = release_fixture(Path(run.artifact_root), run)
    canonical_sha = file_digest(submission)
    issue_lifecycle_integrity_injection(
        run,
        action=RELEASE_ACTION,
        actor="integrity-validator",
        reason="Exercise run-local L6 release identity admission",
        issued_at=NOW,
    )

    derived = release_submission_for_admission(
        run,
        submission,
        observed_at=NOW + timedelta(seconds=1),
    )

    assert derived != submission
    assert file_digest(submission) == canonical_sha
    with pytest.raises(LifecycleIntegrityBlocked) as exc_info:
        validate_lifecycle_release_submission(
            derived,
            run_id=run.run_id,
            source_commit=run.source_commit,
            expected_candidate_id=identity["candidate_id"],
            expected_model_digest=identity["model_digest"],
            expected_ct_evaluation_id=identity["ct_evaluation_id"],
        )
    assert "release_model_digest_mismatch" in exc_info.value.blockers
    assert "release_model_artifact_digest_mismatch" in exc_info.value.blockers
    assert injection_receipt_path(run, RELEASE_ACTION).is_file()
