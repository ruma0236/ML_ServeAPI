from __future__ import annotations

from evm.control_panel.drift import build_drift_state, drift_action, drift_severity


def test_drift_action_none_when_no_queue_or_blockers():
    assert (
        drift_action(
            queue_count=0,
            data_drift_status="unknown",
            prediction_drift_status="unknown",
            promotion_blockers=[],
        )
        == "none"
    )


def test_drift_action_routes_queued_cases_to_label_review_when_promotion_blocked():
    assert (
        drift_action(
            queue_count=7,
            data_drift_status="warn",
            prediction_drift_status="unknown",
            promotion_blockers=["accuracy<0.7"],
        )
        == "label_review"
    )


def test_drift_action_never_auto_routes_queue_to_retraining():
    assert (
        drift_action(
            queue_count=100,
            data_drift_status="warn",
            prediction_drift_status="warn",
            promotion_blockers=[],
        )
        == "label_review"
    )


def test_drift_state_keeps_reference_current_versions_and_report_uri(tmp_path):
    report = tmp_path / "drift_special_case_queue.json"
    report.write_text("[]", encoding="utf-8")
    queue = [{"case_id": "case-1"}, {"case_id": "case-2"}]

    state = build_drift_state(
        drift_queue=queue,
        drift_queue_path=report,
        dataset_version="visa-open-data-test",
        promotion_blockers=["accuracy<0.7"],
    )

    assert state.reference_dataset_version == "visa-open-data-test"
    assert state.current_dataset_version == "visa-open-data-test"
    assert state.review_queue_count == 2
    assert state.action == "label_review"
    assert state.report_uri == str(report)
    assert state.recommended_action == "review 2 queued drift/special cases"


def test_drift_severity_uses_queue_and_score_thresholds():
    assert drift_severity(0, None) == "none"
    assert drift_severity(1, 0.01) == "low"
    assert drift_severity(20, 0.12) == "medium"
    assert drift_severity(100, 0.2) == "high"


def test_measured_drift_state_routes_to_review_without_retraining(tmp_path):
    report_path = tmp_path / "drift_report.json"
    report_path.write_text("{}", encoding="utf-8")
    report = {
        "schema_version": "evm.w7.measured_drift_report.v1",
        "decision": "review_required",
        "candidate_id": "effnet-b7-img600-finetune-adamw",
        "reference_dataset_version": "visa:validation",
        "current_dataset_version": "visa:test-pcb3",
        "reference_window_id": "validation-all-products",
        "current_window_id": "test-pcb3-intake",
        "triggered_rules": ["input_category_js", "confidence_psi"],
        "thresholds": {"input_category_js": 0.1, "confidence_psi": 0.1},
        "metrics": {
            "input_category_js": 0.42,
            "predicted_class_js": 0.02,
            "confidence_psi": 0.24,
            "mean_confidence_drop": 0.03,
            "low_confidence_rate_increase": 0.05,
        },
        "reference": {
            "record_count": 2136,
            "confidence": {
                "quantiles": {"p10": 0.71, "p50": 0.9, "p90": 0.99},
                "low_confidence_rate": 0.09,
            },
        },
        "current": {
            "record_count": 205,
            "confidence": {
                "quantiles": {"p10": 0.55, "p50": 0.82, "p90": 0.98},
                "low_confidence_rate": 0.18,
            },
        },
        "review_queue_count": 128,
        "review_event_id": "drift-measured-1",
    }
    event = {
        "event_id": "drift-measured-1",
        "status": "open",
        "evidence_uri": str(report_path),
        "label_review_queue_uri": str(tmp_path / "queue.jsonl"),
        "approval_required": True,
        "automatic_retraining": False,
    }

    state = build_drift_state(
        drift_queue=[],
        drift_queue_path=tmp_path / "legacy.json",
        dataset_version="legacy",
        promotion_blockers=[],
        measured_report=report,
        review_event=event,
    )

    assert state.measurement_status == "measured"
    assert state.review_event_type == "review_required"
    assert state.action == "label_review"
    assert state.retraining_candidate_required is False
    assert state.automatic_retraining is False
    assert state.approval_required is True
    assert state.reference_record_count == 2136
    assert state.current_record_count == 205
    assert state.triggered_rules == ["input_category_js", "confidence_psi"]
