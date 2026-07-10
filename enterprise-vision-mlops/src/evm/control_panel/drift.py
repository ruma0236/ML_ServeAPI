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
    measured_report: dict | None = None,
    review_event: dict | None = None,
) -> DriftState:
    if (
        measured_report
        and measured_report.get("schema_version") == "evm.w7.measured_drift_report.v1"
    ):
        return build_measured_drift_state(measured_report, review_event or {})

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
        retraining_candidate_required=False,
        measurement_status="legacy_queue" if drift_queue_path.exists() else "unavailable",
    )


def drift_score(queue_count: int) -> float | None:
    if queue_count <= 0:
        return None
    return round(min(0.12 + queue_count / 1000, 0.95), 4)


def build_measured_drift_state(report: dict, event: dict) -> DriftState:
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    thresholds = report.get("thresholds") if isinstance(report.get("thresholds"), dict) else {}
    reference = report.get("reference") if isinstance(report.get("reference"), dict) else {}
    current = report.get("current") if isinstance(report.get("current"), dict) else {}
    reference_confidence = (
        reference.get("confidence") if isinstance(reference.get("confidence"), dict) else {}
    )
    current_confidence = (
        current.get("confidence") if isinstance(current.get("confidence"), dict) else {}
    )
    triggered_rules = [str(value) for value in report.get("triggered_rules", [])]
    review_required = report.get("decision") == "review_required"
    data_rules = {"input_category_js"}
    prediction_rules = {
        "predicted_class_js",
        "confidence_psi",
        "mean_confidence_drop",
        "low_confidence_rate_increase",
    }
    data_status: State = "warn" if data_rules.intersection(triggered_rules) else "pass"
    prediction_status: State = (
        "warn" if prediction_rules.intersection(triggered_rules) else "pass"
    )
    ratios = [
        float(metrics.get(name, 0.0)) / float(threshold)
        for name, threshold in thresholds.items()
        if isinstance(threshold, int | float) and float(threshold) > 0
    ]
    max_ratio = max(ratios, default=0.0)
    severity = "none"
    if review_required:
        severity = "critical" if max_ratio >= 4 else "high" if max_ratio >= 2 else "medium"
    drifting_columns: list[str] = []
    if "input_category_js" in triggered_rules:
        drifting_columns.append("class_name")
    if "predicted_class_js" in triggered_rules:
        drifting_columns.append("predicted_label")
    if {
        "confidence_psi",
        "mean_confidence_drop",
        "low_confidence_rate_increase",
    }.intersection(triggered_rules):
        drifting_columns.append("confidence")
    event_id = str(event.get("event_id") or report.get("review_event_id") or "") or None
    queue_count = int(report.get("review_queue_count") or 0)
    return DriftState(
        status="warn" if review_required else "pass",
        data_drift_status=data_status,
        prediction_drift_status=prediction_status,
        action="label_review" if review_required else "none",
        reference_dataset_version=str(report.get("reference_dataset_version") or "") or None,
        current_dataset_version=str(report.get("current_dataset_version") or "") or None,
        drifting_columns=drifting_columns,
        drift_score=round(max((float(value) for value in metrics.values()), default=0.0), 6),
        report_uri=str(event.get("evidence_uri") or "") or None,
        review_queue_count=queue_count,
        severity=severity,  # type: ignore[arg-type]
        recommended_action=(
            f"review measured drift event {event_id} through label review and approval"
            if review_required
            else "measured drift remains within policy"
        ),
        retraining_candidate_required=False,
        measurement_status="measured",
        review_event_id=event_id,
        review_event_type=("review_required" if review_required else "within_policy"),
        review_event_status=str(
            event.get("status") or ("open" if review_required else "closed")
        ),  # type: ignore[arg-type]
        model_candidate_id=str(report.get("candidate_id") or "") or None,
        reference_window_id=str(report.get("reference_window_id") or "") or None,
        current_window_id=str(report.get("current_window_id") or "") or None,
        reference_record_count=int(reference.get("record_count") or 0),
        current_record_count=int(current.get("record_count") or 0),
        input_category_js=float(metrics.get("input_category_js") or 0.0),
        predicted_class_js=float(metrics.get("predicted_class_js") or 0.0),
        confidence_psi=float(metrics.get("confidence_psi") or 0.0),
        mean_confidence_drop=float(metrics.get("mean_confidence_drop") or 0.0),
        low_confidence_rate_increase=float(
            metrics.get("low_confidence_rate_increase") or 0.0
        ),
        reference_low_confidence_rate=float(
            reference_confidence.get("low_confidence_rate") or 0.0
        ),
        current_low_confidence_rate=float(
            current_confidence.get("low_confidence_rate") or 0.0
        ),
        reference_confidence_quantiles={
            str(key): float(value)
            for key, value in (reference_confidence.get("quantiles") or {}).items()
        },
        current_confidence_quantiles={
            str(key): float(value)
            for key, value in (current_confidence.get("quantiles") or {}).items()
        },
        thresholds={str(key): float(value) for key, value in thresholds.items()},
        triggered_rules=triggered_rules,
        label_review_queue_uri=str(event.get("label_review_queue_uri") or "") or None,
        approval_required=bool(event.get("approval_required", review_required)),
        automatic_retraining=False,
    )
