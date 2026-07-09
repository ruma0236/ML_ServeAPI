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
