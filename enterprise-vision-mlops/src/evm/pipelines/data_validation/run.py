from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from evm.core.config import get_nested
from evm.core.pipeline import (
    build_context,
    read_jsonl,
    write_json,
    write_jsonl,
    write_markdown_report,
)


def _is_valid_record(record: dict[str, object], allowed_extensions: set[str]) -> tuple[bool, str]:
    image_uri = str(record.get("image_uri", ""))
    extension = Path(image_uri).suffix.lower()
    if not image_uri:
        return False, "missing_image_uri"
    if extension not in allowed_extensions:
        return False, "unsupported_extension"
    if not record.get("label"):
        return False, "missing_label"
    if int(record.get("width", 0) or 0) <= 0 or int(record.get("height", 0) or 0) <= 0:
        return False, "invalid_dimensions"
    return True, "ok"


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("data_validation", config_path)
    cfg = ctx.pipeline_config()
    input_manifest = ctx.path(str(cfg.get("input_manifest", "data/raw/raw_manifest.jsonl")))
    output_manifest = ctx.path(str(cfg.get("output_manifest", "data/validated/validated_manifest.jsonl")))
    report_name = str(cfg.get("report_name", "validation_report.json"))
    allowed_extensions = {str(item).lower() for item in cfg.get("allowed_extensions", [".jpg"])}

    records = read_jsonl(input_manifest)
    valid_records: list[dict[str, object]] = []
    failures: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()

    for record in records:
        is_valid, reason = _is_valid_record(record, allowed_extensions)
        if is_valid:
            valid_records.append(record)
            label_counts[str(record["label"])] += 1
        else:
            failures[reason] += 1

    write_jsonl(output_manifest, valid_records)
    report = {
        "input_records": len(records),
        "valid_records": len(valid_records),
        "invalid_records": len(records) - len(valid_records),
        "failure_reasons": dict(failures),
        "label_counts": dict(label_counts),
        "input_manifest": str(input_manifest.relative_to(ctx.project_root)),
        "output_manifest": str(output_manifest.relative_to(ctx.project_root)),
    }
    report_path = ctx.path(get_nested(ctx.config, "paths.validated_zone", "data/validated")) / report_name
    write_json(report_path, report)
    write_json(ctx.run_dir / "summary.json", report)
    write_markdown_report(
        ctx,
        "Data Validation Pipeline",
        report,
        [
            "",
            "## Contract",
            "",
            "- Input: `data/raw/raw_manifest.jsonl`.",
            "- Output: `data/validated/validated_manifest.jsonl` and validation report.",
            "- Next: `training` consumes only validated records.",
        ],
    )
    return report


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()
