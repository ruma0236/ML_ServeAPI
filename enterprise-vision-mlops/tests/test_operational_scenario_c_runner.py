from __future__ import annotations

from datetime import datetime, timezone

from evm.operations.scenario_c_quality import (
    RetrainingProfile,
    ScenarioCIdentity,
)
from evm.operations.scenario_c_runner import (
    canonical_uri,
    gate_fixture_matrix,
    prediction_records,
    runtime_path,
)


NOW = datetime(2026, 8, 2, 4, 0, tzinfo=timezone.utc)


def test_runtime_path_projects_data_and_ct_roots(monkeypatch) -> None:
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", "F:/data-root")
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", "/mnt/data")
    monkeypatch.setenv("EVM_CT_HOST_ROOT", "F:/ct-root")
    monkeypatch.setenv("EVM_CT_MOUNT_ROOT", "/mnt/ct")
    assert runtime_path("F:/data-root/artifacts/run.json").as_posix() == (
        "/mnt/data/artifacts/run.json"
    )
    assert runtime_path("F:/ct-root/snapshots/manifest.jsonl").as_posix() == (
        "/mnt/ct/snapshots/manifest.jsonl"
    )


def test_canonical_uri_remains_host_addressable() -> None:
    assert canonical_uri("F:/evidence", "run-1", "report.json") == (
        "F:/evidence/run-1/report.json"
    )


def test_prediction_payload_is_strictly_projected() -> None:
    records = prediction_records(
        [
            {
                "sample_id": "sample-1",
                "content_sha256": "a" * 64,
                "image_uri": "file:///mnt/data/image.jpg",
                "class_name": "pcb3",
                "actual_label": "normal",
                "predicted_label": "normal",
                "confidence": 0.9,
                "ignored": "runtime-only",
            }
        ]
    )
    assert records[0].sample_id == "sample-1"
    assert records[0].model_dump().get("ignored") is None


def test_gate_fixture_matrix_covers_safety_paths() -> None:
    from evm.operations.scenario_c_quality import (
        build_retraining_candidate,
        build_review_event,
        evaluate_quality_windows,
    )
    from tests.test_operational_scenario_c_quality import baseline, identity, policy, shifted

    decision = evaluate_quality_windows(
        policy=policy(), baseline=baseline(), current=shifted()
    )
    event = build_review_event(
        policy=policy(),
        identity=identity(),
        baseline=baseline(),
        current=shifted(),
        decision=decision,
        affected_slices=["pcb3"],
        created_at=NOW,
    )
    candidate = build_retraining_candidate(
        event=event,
        identity=identity(),
        profile=RetrainingProfile(
            profile_id="profile",
            architecture="efficientnet-b0",
            framework="torch",
            seed=1,
            max_epochs=2,
            early_stop_patience=1,
            metric_names=["f1"],
        ),
        derived_manifest_digest="d" * 64,
        created_at=NOW,
    )
    fixtures = gate_fixture_matrix(
        candidate=candidate,
        identity=ScenarioCIdentity.model_validate(identity().model_dump()),
        requester="ml-platform",
        evaluated_at=NOW,
    )
    indexed = {item["case_id"]: item["gate"] for item in fixtures}
    assert set(indexed) == {
        "manual_hold",
        "rejected",
        "same_actor_approval",
        "expired_approval",
        "scenario_e_open",
        "fully_valid_handoff",
    }
    assert indexed["scenario_e_open"]["state"] == "blocked"
    assert "scenario_e_integrity_not_passed" in indexed["scenario_e_open"]["blockers"]
    assert indexed["fully_valid_handoff"]["state"] == "limited_release_handoff"
    assert all(not item["deployment_intent_created"] for item in indexed.values())
