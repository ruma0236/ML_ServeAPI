from __future__ import annotations

from collections.abc import Sequence

from evm.core.config import get_nested
from evm.core.http import request_json
from evm.core.pipeline import build_context, write_json, write_markdown_report


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("deployment", config_path)
    cfg = ctx.pipeline_config()
    api_url = str(get_nested(ctx.config, "serving.api_url", "http://localhost:8000")).rstrip("/")
    sample_image_uri = str(cfg.get("sample_image_uri", "s3://raw/sample_0001.jpg"))

    health_status, health_payload = request_json("GET", f"{api_url}/health")
    ready_status, ready_payload = request_json("GET", f"{api_url}/ready")
    predict_status, predict_payload = request_json(
        "POST",
        f"{api_url}/predict",
        {"image_uri": sample_image_uri, "features": {"width": 640, "height": 480}},
    )
    ready_model_loaded = (
        ready_payload.get("model_loaded") if isinstance(ready_payload, dict) else None
    )
    predict_placeholder = (
        predict_payload.get("placeholder") if isinstance(predict_payload, dict) else None
    )
    contract_ok = (
        health_status == 200
        and ready_status == 200
        and predict_status == 200
        and ready_model_loaded is True
        and predict_placeholder is False
    )

    summary = {
        "api_url": api_url,
        "health_status": health_status,
        "ready_status": ready_status,
        "predict_status": predict_status,
        "ready_model_loaded": ready_model_loaded,
        "predict_placeholder": predict_placeholder,
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
            "contract_ok": contract_ok,
            "trace_id": ctx.trace.trace_id,
        },
        [
            "",
            "## Contract",
            "",
            "- Input: promoted model metadata and running serving API.",
            "- Output: deployment smoke-test report with registry-driven serving contract.",
            "- Pass condition: `/ready` has `model_loaded=true` and `/predict` has `placeholder=false`.",
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
