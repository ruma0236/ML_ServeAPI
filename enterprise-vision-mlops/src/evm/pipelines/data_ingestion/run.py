from __future__ import annotations

from collections.abc import Sequence

from evm.core.config import get_nested
from evm.core.pipeline import build_context, utc_now, write_json, write_jsonl, write_markdown_report


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("data_ingestion", config_path)
    cfg = ctx.pipeline_config()
    raw_zone = ctx.path(get_nested(ctx.config, "paths.raw_zone", "data/raw"))
    manifest_path = raw_zone / str(cfg.get("manifest_name", "raw_manifest.jsonl"))
    sample_count = int(cfg.get("sample_records", 8))

    records = []
    labels = ["normal", "scratch", "stain", "normal"]
    for idx in range(sample_count):
        records.append(
            {
                "id": f"sample_{idx + 1:04d}",
                "image_uri": f"s3://raw/sample_{idx + 1:04d}.jpg",
                "label": labels[idx % len(labels)],
                "width": 640 + (idx % 3) * 16,
                "height": 480 + (idx % 2) * 16,
                "source": "synthetic_manifest_seed",
                "ingested_at": utc_now(),
            }
        )

    write_jsonl(manifest_path, records)
    summary = {
        "records": len(records),
        "manifest": str(manifest_path.relative_to(ctx.project_root)),
        "raw_zone": str(raw_zone.relative_to(ctx.project_root)),
    }
    write_json(ctx.run_dir / "summary.json", summary)
    write_markdown_report(
        ctx,
        "Data Ingestion Pipeline",
        summary,
        [
            "",
            "## Contract",
            "",
            "- Input: external raw image source or synthetic seed records.",
            "- Output: `data/raw/raw_manifest.jsonl`.",
            "- Next: `data_validation` consumes the raw manifest.",
        ],
    )
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()
