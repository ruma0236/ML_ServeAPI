from __future__ import annotations

from evm.control_panel.product_validation import (
    product_evidence_state,
    result_value,
    summed_result,
)


def passing_evidence() -> dict:
    return {
        "status": "pass",
        "deployment": {
            "intent_id": "deploy-final",
            "state": "applied",
        },
        "product_inference": {
            "ready": {
                "status": "ok",
                "model_loaded": True,
                "model_sha256": "a" * 64,
            }
        },
        "monitoring": {
            "prometheus_target": {"health": "up"},
            "p95_latency_ms": 450.0,
            "inference_request_total": 4,
        },
    }


def test_product_evidence_requires_matching_applied_intent_and_model() -> None:
    valid, p95_latency_ms, healthy_targets = product_evidence_state(
        passing_evidence(),
        deployment_intent_id="deploy-final",
        model_digest="a" * 64,
    )

    assert valid is True
    assert p95_latency_ms == 450.0
    assert healthy_targets == 1


def test_product_evidence_fails_closed_on_stale_intent() -> None:
    valid, _, healthy_targets = product_evidence_state(
        passing_evidence(),
        deployment_intent_id="deploy-newer",
        model_digest="a" * 64,
    )

    assert valid is False
    assert healthy_targets == 1


def test_product_evidence_fails_closed_on_malformed_metrics() -> None:
    evidence = passing_evidence()
    evidence["monitoring"]["p95_latency_ms"] = "not-a-number"
    evidence["monitoring"]["inference_request_total"] = "not-a-number"

    valid, p95_latency_ms, _ = product_evidence_state(
        evidence,
        deployment_intent_id="deploy-final",
        model_digest="a" * 64,
    )

    assert valid is False
    assert p95_latency_ms is None


def test_prometheus_result_helpers_handle_labeled_series() -> None:
    series = [
        {"value": [1, "1"]},
        {"value": [1, "2"]},
        {"value": [1]},
    ]

    assert result_value(series) == 1.0
    assert summed_result(series) == 3.0
