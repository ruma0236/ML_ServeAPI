from __future__ import annotations

from pathlib import Path

from evm.control_panel.schemas import DriftState, State


DriftAction = str


def drift_severity(queue_count: int, drift_score: float | None) -> str:
    if queue_count <= 0 and not drift_score:
        return "none"
    score = drift_score or 0.0
    if queue_count >= 100 or score >= 0.4:
        return "high"
    if queue_count >= 20 or score >= 0.15:
        return "medium"
    return "low"


def drift_action(
    *,
    queue_count: int,
    data_drift_status: State,
    prediction_drift_status: State,
    promotion_blockers: list[str],
) -> DriftAction:
    if data_drift_status in {"fail", "blocked"} or prediction_drift_status in {"fail", "blocked"}:
        return "block_promotion"
    if queue_count and promotion_blockers:
        return "label_review"
    if queue_count >= 100 or data_drift_status == "warn" and prediction_drift_status == "warn":
        return "retrain_candidate"
    if queue_count:
        return "label_review"
    return "none"


def recommended_action(action: DriftAction, queue_count: int) -> str:
    if action == "block_promotion":
        return "block promotion until drift review closes"
    if action == "retrain_candidate":
        return "open retraining candidate from current dataset"
    if action == "label_review":
        return f"review {queue_count} queued drift/special cases"
    if action == "rollback_review":
        return "open rollback review"
    return "no drift action required"


def build_drift_state(
    *,
    drift_queue: list[dict],
    drift_queue_path: Path,
    dataset_version: str,
    promotion_blockers: list[str],
) -> DriftState:
    queue_count = len(drift_queue)
    data_drift_status: State = "warn" if queue_count else "unknown"
    prediction_drift_status: State = "unknown"
    score = drift_score(queue_count)
    action = drift_action(
        queue_count=queue_count,
        data_drift_status=data_drift_status,
        prediction_drift_status=prediction_drift_status,
        promotion_blockers=promotion_blockers,
    )
    return DriftState(
        status=data_drift_status if queue_count else "unknown",
        data_drift_status=data_drift_status,
        prediction_drift_status=prediction_drift_status,
        reference_dataset_version=dataset_version,
        current_dataset_version=dataset_version,
        drifting_columns=["class_name"] if queue_count else [],
        drift_score=score,
        report_uri=str(drift_queue_path) if drift_queue_path.exists() else None,
        action=action,  # type: ignore[arg-type]
        review_queue_count=queue_count,
        severity=drift_severity(queue_count, score),  # type: ignore[arg-type]
        recommended_action=recommended_action(action, queue_count),
        retraining_candidate_required=action == "retrain_candidate",
    )


def drift_score(queue_count: int) -> float | None:
    if queue_count <= 0:
        return None
    return round(min(0.12 + queue_count / 1000, 0.95), 4)
