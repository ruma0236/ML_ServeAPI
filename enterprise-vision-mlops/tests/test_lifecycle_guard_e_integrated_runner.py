from __future__ import annotations

import json
from pathlib import Path

import pytest

from evm.control_panel.lifecycle_guards import file_digest
from evm.operations.lifecycle_guard_e_integrated_runner import (
    evidence_references,
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
