from __future__ import annotations

import json
from pathlib import Path

import pytest

from evm.control_panel.drift_workflow import (
    DriftWorkflowRejected,
    load_latest_drift_workflow,
    transition_drift_review,
)
from evm.control_panel.schemas import DriftReviewTransitionRequest


def seed_event(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "latest_review_event.json").write_text(
        json.dumps(
            {
                "schema_version": "evm.w7.drift_review_event.v1",
                "event_id": "drift-test",
                "event_type": "review_required",
                "status": "open",
                "candidate_id": "effnet-b7-img600-finetune-adamw",
                "dataset_version": "visa-open-data-test",
                "triggered_rules": ["input_category_js"],
                "approval_required": True,
                "automatic_retraining": False,
                "automatic_deployment": False,
                "automatic_promotion": False,
            }
        ),
        encoding="utf-8",
    )
    (root / "latest_drift_report.json").write_text(
        json.dumps({"review_queue_count": 128}), encoding="utf-8"
    )


def request(target: str, actor: str, expected: str, *, dry_run: bool = False):
    return DriftReviewTransitionRequest(
        target_status=target,
        actor=actor,
        reason=f"validate {target} workflow transition",
        expected_status=expected,
        dry_run=dry_run,
    )


def test_drift_workflow_enforces_preview_order_and_separation(tmp_path, monkeypatch):
    monkeypatch.setenv("EVM_DRIFT_REVIEW_ROOT", str(tmp_path))
    seed_event(tmp_path)

    preview = transition_drift_review(
        "drift-test", request("acknowledged", "ml-platform", "open", dry_run=True)
    )
    assert preview.status == "open"
    assert preview.projected_status == "acknowledged"
    assert load_latest_drift_workflow().status == "open"

    acknowledged = transition_drift_review(
        "drift-test", request("acknowledged", "ml-platform", "open")
    )
    assert acknowledged.status == "acknowledged"
    assert acknowledged.review_queue_count == 128

    with pytest.raises(DriftWorkflowRejected, match="separation_of_duties"):
        transition_drift_review(
            "drift-test", request("approved", "ml-platform", "acknowledged")
        )

    approved = transition_drift_review(
        "drift-test", request("approved", "ai-infra-sre", "acknowledged")
    )
    closed = transition_drift_review(
        "drift-test", request("closed", "release-manager", "approved")
    )

    assert approved.status == "approved"
    assert closed.status == "closed"
    assert closed.automatic_retraining is False
    assert len(closed.transitions) == 3
    assert (tmp_path / "drift-test" / "workflow.json").exists()
    assert len((tmp_path / "workflow_events.jsonl").read_text().splitlines()) == 3
