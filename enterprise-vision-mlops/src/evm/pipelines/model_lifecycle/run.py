from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evm.core.config import get_nested
from evm.core.model_promotion import evaluate_promotion, metric_thresholds
from evm.core.pipeline import build_context, display_path, utc_now, write_json, write_markdown_report


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _selected_eval(source_model: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    evaluation = source_model.get("evaluation", {})
    if not isinstance(evaluation, dict):
        return "unknown", {}
    selected_split = str(evaluation.get("selected_split", "test"))
    selected_eval = evaluation.get(selected_split, {})
    return selected_split, selected_eval if isinstance(selected_eval, dict) else {}


def _drift_queue(
    source_model: dict[str, Any],
    selected_eval: dict[str, Any],
    *,
    confidence_threshold: float,
) -> list[dict[str, Any]]:
    false_predictions = selected_eval.get("false_predictions", [])
    if not isinstance(false_predictions, list):
        false_predictions = []
    queue = []
    for idx, item in enumerate(false_predictions[:50], start=1):
        if not isinstance(item, dict):
            continue
        confidence = float(item.get("confidence", 0.0) or 0.0)
        queue.append(
            {
                "case_id": f"w5-special-case-{idx:03d}",
                "sample_id": item.get("sample_id", ""),
                "image_uri": item.get("image_uri", ""),
                "expected_label": item.get("label", ""),
                "predicted_label": item.get("prediction", ""),
                "confidence": confidence,
                "case_type": "misclassification",
                "severity": "high" if confidence >= confidence_threshold else "medium",
                "owner": "mlops-review",
                "status": "open",
            }
        )
    label_counts = source_model.get("label_counts", {})
    if isinstance(label_counts, dict):
        total = sum(int(value) for value in label_counts.values() if isinstance(value, int | float)) or 1
        anomaly_ratio = float(label_counts.get("anomaly", 0) or 0) / total
        queue.append(
            {
                "case_id": "w5-drift-label-ratio-001",
                "case_type": "label_distribution_monitor",
                "observed_anomaly_ratio": round(anomaly_ratio, 6),
                "severity": "medium",
                "owner": "data-quality",
                "status": "watching",
            }
        )
    return queue


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("model_lifecycle", config_path)
    cfg = ctx.pipeline_config()
    model_name = str(get_nested(ctx.config, "serving.model_name", "vision-baseline"))
    registry_root = ctx.path(get_nested(ctx.config, "paths.registry_root", "artifacts/registry"))
    artifacts_root = ctx.path(get_nested(ctx.config, "paths.artifacts_root", "artifacts"))
    registry_latest = ctx.path(str(cfg.get("registry_latest", registry_root / model_name / "latest.json")))
    output_dir = ctx.path(str(cfg.get("output_dir", artifacts_root / "lifecycle" / model_name)))
    thresholds = metric_thresholds(cfg, fallback_accuracy=0.50)
    confidence_threshold = float(cfg.get("special_case_confidence_threshold", 0.70))

    registry_payload = _read_json(registry_latest)
    source_model = registry_payload.get("source_model", {})
    if not isinstance(source_model, dict) or not source_model:
        raise RuntimeError(f"registry source_model is missing: {registry_latest}")

    metrics = source_model.get("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
    selected_split, selected_eval = _selected_eval(source_model)
    promotion_policy = evaluate_promotion(metrics, thresholds)
    target_state = "Promoted" if promotion_policy["status"] == "passed" else "Shadow"
    blockers = promotion_policy["blockers"]
    lifecycle_record = {
        "schema_version": "evm.model_lifecycle.v1",
        "generated_at": utc_now(),
        "model_name": model_name,
        "registry_version": registry_payload.get("version", ""),
        "registry_stage": registry_payload.get("stage", ""),
        "model_type": source_model.get("model_type", ""),
        "current_state": source_model.get("lifecycle", {}).get("state", "Registered"),
        "target_state": target_state,
        "promotion_gate": promotion_policy["status"],
        "promotion_decision": promotion_policy["decision"],
        "blockers": blockers,
        "transitions": [
            {
                "from": "Draft",
                "to": "Registered",
                "evidence": "training artifact exists",
                "status": "complete",
            },
            {
                "from": "Registered",
                "to": "Validated",
                "evidence": f"{selected_split} metrics attached",
                "status": "complete",
            },
            {
                "from": "Validated",
                "to": target_state,
                "evidence": "all configured promotion thresholds are satisfied",
                "status": "complete" if not blockers else "blocked",
                "blockers": blockers,
            },
        ],
        "promotion_policy": promotion_policy,
        "trace": ctx.trace.to_dict(),
    }

    dataset = source_model.get("dataset", {})
    if not isinstance(dataset, dict):
        dataset = {}
    lineage_matrix = {
        "schema_version": "evm.model_lineage_matrix.v1",
        "generated_at": utc_now(),
        "model_name": model_name,
        "registry_version": registry_payload.get("version", ""),
        "model_digest": source_model.get("model_digest", ""),
        "model_type": source_model.get("model_type", ""),
        "dataset_version": dataset.get("dataset_version", ""),
        "validated_parquet_uri": dataset.get("validated_parquet_uri", ""),
        "input_manifest": dataset.get("output_manifest") or dataset.get("input_manifest", ""),
        "feature_sample_bytes": source_model.get("feature_sample_bytes", ""),
        "evaluation_split": selected_split,
        "metrics": metrics,
        "resource_profile": source_model.get("resource_profile", {}),
        "registry_record": str(registry_latest),
    }

    drift_queue = _drift_queue(
        source_model,
        selected_eval,
        confidence_threshold=confidence_threshold,
    )
    rca_candidates = {
        "schema_version": "evm.rca_regression_candidates.v1",
        "generated_at": utc_now(),
        "source": "model_lifecycle.false_predictions",
        "promotion_rule": "reviewed=true and label_verified=true",
        "candidate_count": len([item for item in drift_queue if item.get("case_type") == "misclassification"]),
        "candidates": [
            {
                "case_id": item["case_id"],
                "sample_id": item.get("sample_id", ""),
                "image_uri": item.get("image_uri", ""),
                "expected_label": item.get("expected_label", ""),
                "predicted_label": item.get("predicted_label", ""),
                "status": "needs_review",
            }
            for item in drift_queue
            if item.get("case_type") == "misclassification"
        ],
    }
    dashboard = {
        "schema_version": "evm.lifecycle_dashboard.v1",
        "generated_at": utc_now(),
        "model_name": model_name,
        "registry_version": registry_payload.get("version", ""),
        "state": target_state,
        "blockers": blockers,
        "promotion_policy": promotion_policy,
        "metrics": metrics,
        "open_special_cases": len([item for item in drift_queue if item.get("status") == "open"]),
        "watch_items": len([item for item in drift_queue if item.get("status") == "watching"]),
        "lineage": lineage_matrix,
    }

    lifecycle_path = output_dir / "lifecycle_record.json"
    lineage_path = output_dir / "lineage_matrix.json"
    drift_path = output_dir / "drift_special_case_queue.json"
    rca_path = output_dir / "rca_regression_candidates.json"
    dashboard_path = output_dir / "lifecycle_dashboard.json"
    write_json(lifecycle_path, lifecycle_record)
    write_json(lineage_path, lineage_matrix)
    write_json(drift_path, drift_queue)
    write_json(rca_path, rca_candidates)
    write_json(dashboard_path, dashboard)

    summary = {
        "model_name": model_name,
        "registry_version": registry_payload.get("version", ""),
        "target_state": target_state,
        "blockers": blockers,
        "selected_eval_split": selected_split,
        "accuracy": round(float(metrics.get("accuracy", 0.0)), 6),
        "recall": round(float(metrics.get("recall", 0.0)), 6),
        "open_special_cases": dashboard["open_special_cases"],
        "rca_candidate_count": rca_candidates["candidate_count"],
        "lifecycle_record": display_path(lifecycle_path, ctx.project_root),
        "lineage_matrix": display_path(lineage_path, ctx.project_root),
        "drift_queue": display_path(drift_path, ctx.project_root),
        "rca_candidates": display_path(rca_path, ctx.project_root),
        "dashboard": display_path(dashboard_path, ctx.project_root),
        "trace_id": ctx.trace.trace_id,
        "pipeline_run_id": ctx.run_id,
    }
    write_json(ctx.run_dir / "summary.json", summary)
    write_markdown_report(
        ctx,
        "Model Lifecycle Pipeline",
        summary,
        [
            "",
            "## Contract",
            "",
            "- Input: latest registry model artifact.",
            "- Output: lifecycle state record, lineage matrix, drift/special-case queue, RCA regression candidates, and dashboard JSON.",
            "- Next: `deployment` verifies the promoted registry contract at the API boundary.",
        ],
    )
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()
