from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel.lifecycle_guards import file_digest
from evm.operations.lifecycle_guard_c_runner import (
    count_delta,
    registration_payload,
    validate_source_evidence_index,
    validate_hold_boundary,
    validate_resume_boundary,
)


def test_registration_payload_binds_every_source_file(tmp_path: Path) -> None:
    files = (
        "policy.json",
        "source-identities.json",
        "review-event.json",
        "retraining-candidate.json",
        "derived-shift-manifest.jsonl",
    )
    for index, name in enumerate(files):
        (tmp_path / name).write_text(
            json.dumps({"index": index}),
            encoding="utf-8",
        )
    run = {"version": 7}

    payload = registration_payload(
        run=run,
        scenario_root=tmp_path,
        observed_at="2026-08-02T00:00:00+00:00",
    )

    assert payload["expected_version"] == 7
    assert payload["policy_sha256"] == file_digest(tmp_path / "policy.json")
    assert payload["identity_sha256"] == file_digest(
        tmp_path / "source-identities.json"
    )
    assert payload["review_event_sha256"] == file_digest(
        tmp_path / "review-event.json"
    )
    assert payload["retraining_candidate_sha256"] == file_digest(
        tmp_path / "retraining-candidate.json"
    )
    assert payload["derived_manifest_sha256"] == file_digest(
        tmp_path / "derived-shift-manifest.jsonl"
    )


def test_hold_boundary_requires_zero_downstream_side_effects() -> None:
    run = {
        "state": "blocked",
        "current_stage": "model_training",
        "quality_review_event_id": "event-1",
        "retraining_candidate_id": "candidate-1",
        "quality_review_state": "manual_hold",
        "stages": [
            {
                "stage_id": "model_training",
                "state": "blocked",
                "attempt": 0,
                "task_id": None,
                "runtime_id": None,
            }
        ],
    }
    review = {
        "state": "manual_hold",
        "event_id": "event-1",
        "candidate_id": "candidate-1",
        "registration_attempts": 3,
        "duplicate_attempts": 2,
    }
    delta = {
        "deployment_intents": 0,
        "kubernetes_jobs": 0,
        "mlflow_runs": 0,
        "model_candidates": 0,
    }

    checks = validate_hold_boundary(
        run,
        external_delta=delta,
        task_count=1,
        review=review,
    )

    assert all(checks.values())
    assert not all(
        validate_hold_boundary(
            run,
            external_delta={**delta, "kubernetes_jobs": 1},
            task_count=1,
            review=review,
        ).values()
    )


def test_resume_boundary_stops_before_release() -> None:
    stage_states = {
        "data_pipeline": "completed",
        "model_training": "completed",
        "model_evaluation": "completed",
        "artifact_readiness": "completed",
        "ci_ct_gate": "completed",
        "approval": "waiting_approval",
        "deployment": "not_started",
    }
    run = {
        "state": "waiting_approval",
        "current_stage": "approval",
        "deployment_intent_id": None,
        "stages": [
            {"stage_id": key, "state": value}
            for key, value in stage_states.items()
        ],
    }
    review = {
        "state": "approved_for_training",
        "approval_consumption_count": 1,
        "approval_consumed_at": "2026-08-02T00:00:00+00:00",
    }
    handoffs = {
        "training": {"consumed": True},
        "isolated_ct": {"consumed": True},
    }
    delta = {
        "deployment_intents": 0,
        "kubernetes_jobs": 2,
        "mlflow_runs": 1,
        "model_candidates": 1,
    }

    checks = validate_resume_boundary(
        run,
        external_delta=delta,
        task_count=3,
        review=review,
        handoffs=handoffs,
    )

    assert all(checks.values())


def test_count_delta_is_identity_collection_agnostic() -> None:
    before = {
        "jobs": {"count": 10},
        "runs": {"count": 20},
    }
    after = {
        "jobs": {"count": 12},
        "runs": {"count": 21},
    }

    assert count_delta(before, after) == {"jobs": 2, "runs": 1}


def test_source_evidence_index_is_independently_rehashed(tmp_path: Path) -> None:
    evidence = tmp_path / "event.json"
    evidence.write_text('{"event":"review_required"}', encoding="utf-8")
    index = {
        "files": {
            "event": {
                "uri": str(evidence),
                "sha256": file_digest(evidence),
            }
        }
    }

    result = validate_source_evidence_index(tmp_path, index)

    assert result["status"] == "pass"
    assert result["checked"] == result["matched"] == 1
