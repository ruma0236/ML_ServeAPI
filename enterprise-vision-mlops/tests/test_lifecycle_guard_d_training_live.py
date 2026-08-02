from __future__ import annotations

from evm.control_panel.lifecycle_guards import (
    LifecycleSideEffect,
    LifecycleSideEffectLedger,
)
from evm.operations.lifecycle_guard_d_training_live import (
    exact_training_entry,
    training_job_state,
)


def training_entry(*, state: str = "reserved") -> LifecycleSideEffect:
    return LifecycleSideEffect(
        side_effect_key="a" * 64,
        lifecycle_series_id="series-d",
        lifecycle_run_id="lifecycle-d",
        attempt_id="attempt-d",
        correlation_id="correlation-d",
        stage_id="model_training",
        action="execute_kubernetes_job",
        action_digest="b" * 64,
        state=state,
        reserved_at="2026-08-02T00:00:00Z",
        updated_at="2026-08-02T00:00:00Z",
    )


def test_exact_training_entry_requires_one_matching_side_effect() -> None:
    ledger = LifecycleSideEffectLedger(
        lifecycle_run_id="lifecycle-d",
        entries=[training_entry()],
    )

    assert exact_training_entry(ledger)["side_effect_key"] == "a" * 64


def test_training_job_state_precedence() -> None:
    assert training_job_state({"status": {"active": 1}}) == "active"
    assert training_job_state(
        {"status": {"conditions": [{"type": "Complete", "status": "True"}]}}
    ) == "complete"
    assert training_job_state(
        {"status": {"conditions": [{"type": "Failed", "status": "True"}]}}
    ) == "failed"
