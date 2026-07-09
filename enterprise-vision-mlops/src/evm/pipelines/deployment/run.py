from __future__ import annotations

import json
from collections.abc import Sequence

from evm.core.config import get_nested
from evm.core.http import request_json
from evm.core.pipeline import build_context, write_json, write_markdown_report


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("deployment", config_path)
    cfg = ctx.pipeline_config()
    api_url = str(get_nested(ctx.config, "serving.api_url", "http://localhost:8000")).rstrip("/")
    model_name = str(get_nested(ctx.config, "serving.model_name", "vision-baseline"))
    registry_root = ctx.path(get_nested(ctx.config, "paths.registry_root", "artifacts/registry"))
    registry_latest = ctx.path(str(cfg.get("registry_latest", registry_root / model_name / "latest.json")))
    sample_image_uri = str(cfg.get("sample_image_uri", "s3://raw/sample_0001.jpg"))
    sample_features = {}
    source_model_type = ""
    if registry_latest.exists():
        registry_payload = json.loads(registry_latest.read_text(encoding="utf-8"))
        source_model = registry_payload.get("source_model", {})
        if isinstance(source_model, dict):
            source_model_type = str(source_model.get("model_type", ""))
            sample_inference = source_model.get("sample_inference", {})
            if isinstance(sample_inference, dict):
                sample_image_uri = str(sample_inference.get("image_uri") or sample_image_uri)
                features = sample_inference.get("features", {})
                if isinstance(features, dict):
                    sample_features = features

    health_status, health_payload = request_json("GET", f"{api_url}/health")
    ready_status, ready_payload = request_json("GET", f"{api_url}/ready")
    predict_status, predict_payload = request_json(
        "POST",
        f"{api_url}/predict",
        {"image_uri": sample_image_uri, "features": sample_features},
    )
    ready_model_loaded = (
        ready_payload.get("model_loaded") if isinstance(ready_payload, dict) else None
    )
    predict_placeholder = (
        predict_payload.get("placeholder") if isinstance(predict_payload, dict) else None
    )
    predict_feature_source = (
        predict_payload.get("feature_source") if isinstance(predict_payload, dict) else None
    )
    contract_ok = (
        health_status == 200
        and ready_status == 200
        and predict_status == 200
        and ready_model_loaded is True
        and predict_placeholder is False
        and bool(predict_feature_source)
    )

    summary = {
        "api_url": api_url,
        "registry_latest": str(registry_latest),
        "source_model_type": source_model_type,
        "health_status": health_status,
        "ready_status": ready_status,
        "predict_status": predict_status,
        "ready_model_loaded": ready_model_loaded,
        "predict_placeholder": predict_placeholder,
        "predict_feature_source": predict_feature_source,
        "contract_ok": contract_ok,
        "trace_id": ctx.trace.trace_id,
        "pipeline_run_id": ctx.run_id,
        "sample_prediction": predict_payload if isinstance(predict_payload, dict) else str(predict_payload),
        "health_payload": health_payload if isinstance(health_payload, dict) else str(health_payload),
        "ready_payload": ready_payload if isinstance(ready_payload, dict) else str(ready_payload),
    }
    write_json(ctx.run_dir / "summary.json", summary)
    write_markdown_report(
        ctx,
        "Deployment Pipeline",
        {
            "api_url": api_url,
            "health_status": health_status,
            "ready_status": ready_status,
            "predict_status": predict_status,
            "ready_model_loaded": ready_model_loaded,
            "predict_placeholder": predict_placeholder,
            "predict_feature_source": predict_feature_source,
            "contract_ok": contract_ok,
            "trace_id": ctx.trace.trace_id,
        },
        [
            "",
            "## Contract",
            "",
            "- Input: promoted model metadata and running serving API.",
            "- Output: deployment smoke-test report with registry-driven serving contract.",
            "- Pass condition: `/ready` has `model_loaded=true`, `/predict` has `placeholder=false`, and inference uses registry sample features or a readable image URI.",
            "- Next: `monitoring` verifies metric collection.",
        ],
    )
    if not contract_ok:
        raise RuntimeError("deployment smoke contract failed; see deployment report")
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()
