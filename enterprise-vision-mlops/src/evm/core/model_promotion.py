from __future__ import annotations

from typing import Any


METRIC_KEYS = ("accuracy", "precision", "recall", "f1", "auroc")


def metric_thresholds(config: dict[str, Any], *, fallback_accuracy: float = 0.0) -> dict[str, float]:
    return {
        "accuracy": float(config.get("min_accuracy", config.get("min_metric", fallback_accuracy))),
        "precision": float(config.get("min_precision", 0.0)),
        "recall": float(config.get("min_recall", 0.0)),
        "f1": float(config.get("min_f1", 0.0)),
        "auroc": float(config.get("min_auroc", 0.0)),
    }


def evaluate_promotion(metrics: dict[str, Any], thresholds: dict[str, float]) -> dict[str, Any]:
    observed = {}
    blockers = []
    for key in METRIC_KEYS:
        value = float(metrics.get(key, 0.0) or 0.0)
        threshold = float(thresholds.get(key, 0.0) or 0.0)
        observed[key] = value
        if value < threshold:
            blockers.append(f"{key}<{threshold}")
    status = "passed" if not blockers else "blocked"
    return {
        "status": status,
        "decision": "production_candidate" if status == "passed" else "shadow_only",
        "observed": observed,
        "thresholds": thresholds,
        "blockers": blockers,
    }


def stage_from_promotion(
    source_model: dict[str, Any],
    *,
    default_stage: str,
    stage_on_passed: str | None = None,
    stage_on_blocked: str = "Shadow",
) -> str:
    lifecycle = source_model.get("lifecycle", {})
    gate_status = lifecycle.get("promotion_gate") if isinstance(lifecycle, dict) else ""
    if gate_status == "blocked":
        return stage_on_blocked
    if gate_status == "passed":
        return stage_on_passed or default_stage
    return default_stage
