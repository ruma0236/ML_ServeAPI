from __future__ import annotations

from evm.pipelines.drift_review.run import (
    backbone_for_architecture,
    evaluate_measured_drift,
    jensen_shannon_divergence,
    population_stability_index,
    queue_records,
    real_input_validation,
    window_observation_range,
)


def test_drift_checkpoint_contract_supports_b0_and_b7() -> None:
    assert backbone_for_architecture("efficientnet-b0").endswith("efficientnet_b0")
    assert backbone_for_architecture("efficientnet-b7").endswith("efficientnet_b7")


def prediction(sample_id: str, class_name: str, predicted_label: str, confidence: float) -> dict:
    return {
        "sample_id": sample_id,
        "class_name": class_name,
        "predicted_label": predicted_label,
        "confidence": confidence,
        "actual_label": predicted_label,
    }


def test_distribution_metrics_are_zero_for_identical_windows():
    distribution = {"a": 0.5, "b": 0.5}
    assert jensen_shannon_divergence(distribution, distribution) == 0.0
    assert population_stability_index([0.51, 0.72, 0.93], [0.51, 0.72, 0.93]) == 0.0


def test_measured_drift_requires_review_from_real_distribution_changes():
    reference = [
        prediction("r1", "pcb1", "normal", 0.96),
        prediction("r2", "pcb2", "normal", 0.92),
        prediction("r3", "pcb3", "anomaly", 0.90),
        prediction("r4", "pcb4", "normal", 0.88),
    ]
    current = [
        prediction("c1", "pcb3", "anomaly", 0.61),
        prediction("c2", "pcb3", "anomaly", 0.58),
        prediction("c3", "pcb3", "normal", 0.55),
    ]
    thresholds = {
        "input_category_js": 0.10,
        "predicted_class_js": 0.05,
        "confidence_psi": 0.10,
        "mean_confidence_drop": 0.05,
        "low_confidence_rate_increase": 0.10,
    }

    result = evaluate_measured_drift(
        reference_predictions=reference,
        current_predictions=current,
        thresholds=thresholds,
        low_confidence_threshold=0.70,
    )

    assert result["decision"] == "review_required"
    assert "input_category_js" in result["triggered_rules"]
    assert "mean_confidence_drop" in result["triggered_rules"]
    assert result["current"]["confidence"]["low_confidence_rate"] == 1.0


def test_label_review_queue_never_requests_automatic_retraining():
    records = [
        prediction("c1", "pcb3", "normal", 0.81),
        prediction("c2", "pcb3", "anomaly", 0.44),
    ]
    queue = queue_records(
        records,
        event_id="drift-1",
        triggered_rules=["confidence_psi"],
        low_confidence_threshold=0.70,
        max_records=1,
    )

    assert queue[0]["sample_id"] == "c2"
    assert queue[0]["review_state"] == "pending_label_review"
    assert queue[0]["approval_required"] is True
    assert "low_confidence" in queue[0]["reasons"]


def test_real_input_validation_requires_disjoint_ids_and_content_lineage():
    reference = [
        {
            **prediction("r1", "pcb1", "normal", 0.9),
            "content_sha256": "a" * 64,
            "image_uri": "file:///F:/data/r1.jpg",
        }
    ]
    current = [
        {
            **prediction("c1", "pcb3", "normal", 0.8),
            "content_sha256": "b" * 64,
            "image_uri": "file:///F:/data/c1.jpg",
        }
    ]

    result = real_input_validation(reference, current)

    assert result["valid"] is True
    assert result["sample_overlap_count"] == 0
    assert result["content_sha256_coverage_rate"] == 1.0


def test_window_observation_range_keeps_source_timestamps():
    result = window_observation_range(
        [
            {"quality_checked_at": "2026-07-09T14:19:22Z"},
            {"quality_checked_at": "2026-07-09T14:19:00Z"},
        ]
    )

    assert result == {
        "observed_from": "2026-07-09T14:19:00Z",
        "observed_to": "2026-07-09T14:19:22Z",
    }
