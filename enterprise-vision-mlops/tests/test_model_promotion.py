from __future__ import annotations

from evm.core.model_promotion import evaluate_promotion, metric_thresholds, stage_from_promotion


def test_evaluate_promotion_blocks_when_any_threshold_fails():
    thresholds = metric_thresholds(
        {
            "min_accuracy": 0.7,
            "min_precision": 0.5,
            "min_recall": 0.7,
            "min_f1": 0.5,
            "min_auroc": 0.65,
        }
    )

    result = evaluate_promotion(
        {
            "accuracy": 0.58,
            "precision": 0.12,
            "recall": 0.51,
            "f1": 0.2,
            "auroc": 0.56,
        },
        thresholds,
    )

    assert result["status"] == "blocked"
    assert result["decision"] == "shadow_only"
    assert "precision<0.5" in result["blockers"]


def test_stage_from_promotion_uses_shadow_for_blocked_gate():
    stage = stage_from_promotion(
        {"lifecycle": {"promotion_gate": "blocked"}},
        default_stage="Production",
        stage_on_blocked="Shadow",
    )

    assert stage == "Shadow"
