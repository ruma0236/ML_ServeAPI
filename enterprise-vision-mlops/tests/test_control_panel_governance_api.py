from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException, Response

from apps.api.control_panel_governance import (
    create_decision_route,
    latest_diagnostics,
    transition_decision_route,
    transition_drift_review_route,
)
from evm.control_panel import decision_registry
from evm.control_panel.schemas import (
    CycleRun,
    DecisionRecordRequest,
    DecisionTransitionRequest,
    DriftReviewTransitionRequest,
    RuntimeResourceList,
)


def seed_drift_event(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "latest_review_event.json").write_text(
        json.dumps(
            {
                "event_id": "drift-api-test",
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


def test_governance_routes_expose_structured_diagnostics(tmp_path, monkeypatch):
    cycle = CycleRun.model_validate_json(
        Path("contracts/control-panel/examples/cycle-run.json").read_text(encoding="utf-8")
    )
    resources = RuntimeResourceList(resources=[], observation_status="live")
    monkeypatch.setattr("apps.api.control_panel_governance.cycle_snapshot", lambda: cycle)
    monkeypatch.setattr(
        "apps.api.control_panel_governance.resources_for_cycle",
        lambda _cycle: resources,
    )
    monkeypatch.setenv("EVM_CONTROL_PANEL_DIAGNOSTIC_ROOT", str(tmp_path))

    payload = latest_diagnostics().model_dump(mode="json")

    assert payload["schema_version"] == "evm.control_panel.diagnostics.v1"
    assert payload["status"] == "blocked"
    assert payload["blocked_count"] > 0
    assert all(item["code"] and item["remediation"] for item in payload["diagnostics"])


def test_diagnostics_can_be_bound_to_selected_cycle_without_overwriting_latest(
    tmp_path, monkeypatch
) -> None:
    cycle = CycleRun.model_validate_json(
        Path("contracts/control-panel/examples/cycle-run.json").read_text(encoding="utf-8")
    )
    selected = cycle.model_copy(update={"cycle_id": "cycle-selected-context"})
    resources = RuntimeResourceList(resources=[], observation_status="live")
    monkeypatch.setattr("apps.api.control_panel_governance.cycle_snapshot", lambda: cycle)
    monkeypatch.setattr(
        "apps.api.control_panel_governance.find_cycle",
        lambda cycle_id, _live: selected if cycle_id == selected.cycle_id else None,
    )
    monkeypatch.setattr(
        "apps.api.control_panel_governance.resources_for_cycle",
        lambda value: resources if value.cycle_id == selected.cycle_id else None,
    )
    monkeypatch.setenv("EVM_CONTROL_PANEL_DIAGNOSTIC_ROOT", str(tmp_path))

    payload = latest_diagnostics(selected.cycle_id)

    assert payload.cycle_id == selected.cycle_id
    assert payload.snapshot_uri is None
    assert payload.audit_uri is None
    assert not (tmp_path / "latest.json").exists()


def test_drift_route_previews_without_mutating_real_state(tmp_path, monkeypatch):
    monkeypatch.setenv("EVM_DRIFT_REVIEW_ROOT", str(tmp_path))
    seed_drift_event(tmp_path)

    response = Response()
    workflow = transition_drift_review_route(
        "drift-api-test",
        DriftReviewTransitionRequest(
            target_status="acknowledged",
            actor="ml-platform",
            reason="preview measured drift evidence",
            expected_status="open",
            dry_run=True,
        ),
        response,
    )

    assert response.status_code == 200
    assert workflow.status == "open"
    assert workflow.projected_status == "acknowledged"
    assert json.loads((tmp_path / "latest_review_event.json").read_text())["status"] == "open"


def test_decision_routes_enforce_independent_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("EVM_DECISION_REGISTRY_ROOT", str(tmp_path))
    created = create_decision_route(
        DecisionRecordRequest(
            subject_type="model_candidate",
            title="B7 evidence decision",
            summary="Review the real CUDA inference and model evidence bundle.",
            owner="ml-platform",
            evidence_uris=["F:/evidence/gpu-inference.json"],
            metadata={"candidate_id": "effnet-b7-img600-finetune-adamw"},
        )
    )
    reviewing = transition_decision_route(
        created.decision_id,
        DecisionTransitionRequest(
            target_state="review",
            actor="ml-platform",
            reason="submit evidence for independent review",
            expected_version=created.version,
        ),
    )

    with pytest.raises(HTTPException) as denied:
        transition_decision_route(
            created.decision_id,
            DecisionTransitionRequest(
                target_state="approved",
                actor="ml-platform",
                reason="owner must not approve own decision",
                expected_version=reviewing.version,
            ),
        )
    assert denied.value.status_code == 422
    assert "separation_of_duties" in denied.value.detail


def test_decision_route_returns_service_unavailable_for_persistence_failure(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("EVM_DECISION_REGISTRY_ROOT", str(tmp_path))
    monkeypatch.setattr(
        decision_registry,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            decision_registry.DecisionPersistenceError(
                "decision_registry_persistence_failed"
            )
        ),
    )

    with pytest.raises(HTTPException) as failure:
        create_decision_route(
            DecisionRecordRequest(
                subject_type="serving_change",
                title="Persistence failure contract",
                summary="Return an explicit service availability error.",
                owner="ml-platform",
                evidence_uris=[],
                metadata={},
            )
        )

    assert failure.value.status_code == 503
    assert failure.value.detail == "decision_registry_persistence_failed"
