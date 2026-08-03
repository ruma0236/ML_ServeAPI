from __future__ import annotations

import json
from pathlib import Path

import pytest

from evm.control_panel.lifecycle_guards import file_digest
from evm.operations.lifecycle_guard_e_integrated_runner import (
    evidence_references,
    receipt_derived_submission,
    release_identity,
    stable_replay,
)


def test_stable_replay_requires_three_identical_fast_decisions() -> None:
    results = [
        {
            "decision": "blocked",
            "blockers": ["identity_mismatch"],
            "decision_fingerprint": "same",
            "elapsed_seconds": 0.1,
        }
        for _ in range(3)
    ]

    assert stable_replay(
        results,
        decision="blocked",
        required_blockers={"identity_mismatch"},
    )
    results[2]["decision_fingerprint"] = "different"
    assert not stable_replay(
        results,
        decision="blocked",
        required_blockers={"identity_mismatch"},
    )


def test_release_identity_requires_exact_run_and_complete_identity() -> None:
    run = {"run_id": "run-1"}
    submission = {
        "run_id": "run-1",
        "candidate_id": "candidate-1",
        "model_digest": "a" * 64,
        "ct_evaluation_id": "ct-1",
    }

    assert release_identity(run, submission) == {
        "candidate_id": "candidate-1",
        "model_digest": "a" * 64,
        "ct_evaluation_id": "ct-1",
    }
    with pytest.raises(RuntimeError, match="release_run_identity_mismatch"):
        release_identity(run, {**submission, "run_id": "run-2"})


def test_external_evidence_references_are_content_addressed(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_text(json.dumps({"status": "pass"}), encoding="utf-8")

    result = evidence_references(
        {"artifact": artifact, "missing": tmp_path / "missing.json"}
    )

    assert result["status"] == "blocked"
    assert result["references"]["artifact"]["sha256"] == file_digest(artifact)
    assert result["blockers"] == ["scenario_e_external_evidence_missing:missing"]


def test_receipt_derived_submission_maps_container_path_and_rehashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", str(tmp_path))
    artifact = tmp_path / "artifacts" / "w7" / "run-1" / "e-inject" / "release.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({"status": "blocked"}), encoding="utf-8")
    receipt = {
        "derived_submission_uri": "/app/artifacts/w7/run-1/e-inject/release.json",
        "derived_submission_sha256": file_digest(artifact),
    }

    assert receipt_derived_submission(receipt) == artifact
    receipt["derived_submission_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="derived_release_submission_digest_mismatch"):
        receipt_derived_submission(receipt)
