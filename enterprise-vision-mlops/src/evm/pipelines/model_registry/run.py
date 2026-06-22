from __future__ import annotations

import json
from collections.abc import Sequence

from evm.core.config import get_nested
from evm.core.pipeline import build_context, utc_now, write_json, write_markdown_report


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("model_registry", config_path)
    cfg = ctx.pipeline_config()
    model_name = str(cfg.get("model_name", "vision-baseline"))
    source_model_path = ctx.path(str(cfg.get("source_model_path", f"artifacts/models/{model_name}/model.json")))
    registry_root = ctx.path(get_nested(ctx.config, "paths.registry_root", "artifacts/registry"))
    model_registry_dir = registry_root / model_name
    model_registry_dir.mkdir(parents=True, exist_ok=True)

    existing_versions = [
        int(path.stem.replace("v", ""))
        for path in model_registry_dir.glob("v*.json")
        if path.stem.replace("v", "").isdigit()
    ]
    next_version = max(existing_versions, default=0) + 1

    source_payload = {}
    if source_model_path.exists():
        source_payload = json.loads(source_model_path.read_text(encoding="utf-8"))

    registry_payload = {
        "model_name": model_name,
        "version": next_version,
        "stage": get_nested(ctx.config, "serving.model_stage", "Production"),
        "registered_at": utc_now(),
        "source_model_path": str(source_model_path.relative_to(ctx.project_root)),
        "source_model": source_payload,
        "trace": ctx.trace.to_dict(),
    }
    version_path = model_registry_dir / f"v{next_version}.json"
    latest_path = model_registry_dir / "latest.json"
    write_json(version_path, registry_payload)
    write_json(latest_path, registry_payload)

    summary = {
        "model_name": model_name,
        "version": next_version,
        "stage": registry_payload["stage"],
        "registry_record": str(version_path.relative_to(ctx.project_root)),
        "latest_record": str(latest_path.relative_to(ctx.project_root)),
        "trace_id": ctx.trace.trace_id,
        "pipeline_run_id": ctx.run_id,
    }
    write_json(ctx.run_dir / "summary.json", summary)
    write_markdown_report(
        ctx,
        "Model Registry Pipeline",
        summary,
        [
            "",
            "## Contract",
            "",
            "- Input: selected model artifact from `training`.",
            "- Output: versioned model registry metadata.",
            "- Next: `deployment` reads the promoted model metadata.",
        ],
    )
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()
