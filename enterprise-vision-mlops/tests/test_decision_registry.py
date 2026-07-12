from __future__ import annotations

import pytest

from evm.control_panel.decision_registry import (
    DecisionTransitionRejected,
    create_decision,
    read_decisions,
    transition_decision,
)
from evm.control_panel.schemas import DecisionRecordRequest, DecisionTransitionRequest


def transition(target: str, actor: str, version: int) -> DecisionTransitionRequest:
    return DecisionTransitionRequest(
        target_state=target,
        actor=actor,
        reason=f"move decision to {target}",
        expected_version=version,
    )


def test_decision_registry_tracks_review_and_independent_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("EVM_DECISION_REGISTRY_ROOT", str(tmp_path))
    created = create_decision(
        DecisionRecordRequest(
            subject_type="model_candidate",
            title="Promote B7 candidate",
            summary="Review the selected B7 production evidence bundle.",
            owner="ml-platform",
            evidence_uris=["F:/evidence/model-card.md"],
            metadata={"candidate_id": "effnet-b7-img600-finetune-adamw"},
        )
    )
    reviewing = transition_decision(
        created.decision_id, transition("review", "ml-platform", created.version)
    )

    with pytest.raises(DecisionTransitionRejected, match="separation_of_duties"):
        transition_decision(
            created.decision_id,
            transition("approved", "ml-platform", reviewing.version),
        )

    approved = transition_decision(
        created.decision_id,
        transition("approved", "ai-infra-sre", reviewing.version),
    )

    assert approved.state == "approved"
    assert approved.version == 3
    assert len(approved.transitions) == 2
    assert read_decisions().decisions[0].decision_id == created.decision_id
    assert (tmp_path / created.decision_id / "decision.json").exists()
