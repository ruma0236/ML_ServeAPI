from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evm.core.image_quality import (
    byte_quality_proxies,
    read_image_dimensions,
    resolve_local_image,
    sha256_file,
    stable_split,
    summarize_counts,
)
from evm.core.pipeline import (
    build_context,
    display_path,
    read_jsonl,
    utc_now,
    write_json,
    write_jsonl,
    write_markdown_report,
)
from evm.data_quality.policy import QualityPolicy, load_quality_policy
from evm.etl.recipe import load_etl_recipe, summarize_etl_recipe


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _issue(
    policy: QualityPolicy,
    default_level: str,
    code: str,
    message: str,
    *,
    sample_id: str = "",
    check_id: str = "image_quality",
) -> dict[str, Any]:
    return policy.issue(
        default_level,
        code,
        message,
        sample_id=sample_id,
        check_id=check_id,
    ).to_dict()


def _label_type(label: str) -> str:
    return "normal" if label.lower() in {"normal", "ok", "good", "pass"} else "anomaly"


def _class_name(record: dict[str, Any]) -> str:
    return str(record.get("class_name") or record.get("category") or record.get("source") or "unknown")


def _enrich_record(
    record: dict[str, Any],
    *,
    index: int,
    cfg: dict[str, Any],
    dataset_version: str,
    raw_image_root: Path,
    policy: QualityPolicy,
    hash_counts: dict[str, int],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sample_id = str(record.get("sample_id") or record.get("id") or f"sample_{index + 1:06d}")
    image_uri = str(record.get("image_uri", "") or "")
    label = str(record.get("label", "") or "unknown")
    local_image = resolve_local_image(record, raw_image_root)
    diagnostics: list[dict[str, Any]] = []
    content_sha256 = str(record.get("content_sha256", "") or "")
    detected_width = 0
    detected_height = 0
    brightness_proxy = 0.0
    blur_proxy = 0.0
    image_readable = False

    if not image_uri:
        diagnostics.append(
            _issue(policy, "error", "missing_image_uri", f"{sample_id} has no image_uri", sample_id=sample_id)
        )
    if not label or label == "unknown":
        diagnostics.append(
            _issue(policy, "warn", "missing_label", f"{sample_id} has no useful label", sample_id=sample_id)
        )

    if local_image and local_image.exists():
        content_sha256 = content_sha256 or sha256_file(local_image)
        dimensions = read_image_dimensions(local_image)
        if dimensions:
            detected_width, detected_height = dimensions
            image_readable = True
        else:
            prefix = local_image.read_bytes()[:8]
            manifest_width = int(record.get("width", 0) or 0)
            manifest_height = int(record.get("height", 0) or 0)
            if (prefix.startswith(b"\xff\xd8") or prefix.startswith(b"\x89PNG\r\n\x1a\n")) and (
                manifest_width > 0 and manifest_height > 0
            ):
                image_readable = True
                diagnostics.append(
                    _issue(
                        policy,
                        "warn",
                        "header_dimensions_unavailable",
                        f"{sample_id} uses manifest dimensions because header dimensions were unavailable",
                        sample_id=sample_id,
                    )
                )
            else:
                diagnostics.append(
                    _issue(
                        policy,
                        "error",
                        "unreadable_image",
                        f"{sample_id} image header is unreadable",
                        sample_id=sample_id,
                    )
                )
        proxies = byte_quality_proxies(local_image)
        brightness_proxy = proxies["brightness_proxy"]
        blur_proxy = proxies["blur_proxy"]
    else:
        diagnostics.append(
            _issue(
                policy,
                "warn",
                "local_image_missing",
                f"{sample_id} local image was not found at {local_image or raw_image_root}",
                sample_id=sample_id,
            )
        )

    manifest_width = int(record.get("width", 0) or 0)
    manifest_height = int(record.get("height", 0) or 0)
    width = detected_width or manifest_width
    height = detected_height or manifest_height
    if detected_width and manifest_width and detected_width != manifest_width:
        diagnostics.append(
            _issue(
                policy,
                "warn",
                "width_mismatch",
                f"{sample_id} manifest width={manifest_width}, detected width={detected_width}",
                sample_id=sample_id,
            )
        )
    if detected_height and manifest_height and detected_height != manifest_height:
        diagnostics.append(
            _issue(
                policy,
                "warn",
                "height_mismatch",
                f"{sample_id} manifest height={manifest_height}, detected height={detected_height}",
                sample_id=sample_id,
            )
        )
    if width <= 0 or height <= 0:
        diagnostics.append(
            _issue(
                policy,
                "error",
                "invalid_dimensions",
                f"{sample_id} has invalid dimensions",
                sample_id=sample_id,
            )
        )
    if content_sha256 and hash_counts.get(content_sha256, 0) > 1:
        diagnostics.append(
            _issue(
                policy,
                "warn",
                "duplicate_content_hash",
                f"{sample_id} shares content_sha256 with another record",
                sample_id=sample_id,
            )
        )

    split = str(record.get("split") or stable_split(sample_id, {"train": 0.6, "validation": 0.2, "test": 0.2}))
    enriched = {
        **record,
        "dataset_id": str(cfg.get("dataset_id", "manufacturing_visual_inspection")),
        "dataset_version": dataset_version,
        "sample_id": sample_id,
        "image_uri": image_uri,
        "image_path": str(local_image) if local_image else "",
        "split": split,
        "label": label,
        "label_type": str(record.get("label_type") or _label_type(label)),
        "class_name": _class_name(record),
        "width": width,
        "height": height,
        "content_sha256": content_sha256,
        "source_uri": str(record.get("source_uri") or image_uri),
        "license_id": str(record.get("license_id") or cfg.get("license_id", "manual-review-required")),
        "image_quality": {
            "image_readable": image_readable,
            "manifest_width": manifest_width,
            "manifest_height": manifest_height,
            "detected_width": detected_width,
            "detected_height": detected_height,
            "brightness_proxy": brightness_proxy,
            "blur_proxy": blur_proxy,
            "diagnostics": diagnostics,
        },
    }
    return enriched, diagnostics


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("image_quality", config_path)
    cfg = ctx.pipeline_config()
    input_manifest = ctx.path(str(cfg.get("input_manifest", "data/validated/validated_manifest.jsonl")))
    dataset_metadata_path = ctx.path(str(cfg.get("dataset_metadata", "data/validated/dataset_version.json")))
    output_manifest = ctx.path(str(cfg.get("output_manifest", "data/validated/mvi_quality_manifest.jsonl")))
    report_path = ctx.path(str(cfg.get("report_path", "data/validated/mvi_quality_report.json")))
    baseline_path = ctx.path(str(cfg.get("baseline_path", "data/validated/mvi_quality_baseline.json")))
    raw_image_root = ctx.path(str(cfg.get("raw_image_root", "data/raw/images")))
    duplicate_severity = str(cfg.get("duplicate_hash_severity", "warn"))
    dimension_mismatch_severity = str(cfg.get("dimension_mismatch_severity", "warn"))
    fail_on_error = bool(cfg.get("fail_on_error", True))
    policy_ref = str(cfg.get("quality_policy", "") or "")
    policy_path = ctx.path(policy_ref) if policy_ref else None
    policy = load_quality_policy(
        policy_path,
        severity_defaults={
            "duplicate_content_hash": duplicate_severity,
            "width_mismatch": dimension_mismatch_severity,
            "height_mismatch": dimension_mismatch_severity,
        },
    )
    etl_recipe_summary: dict[str, Any] = {}
    etl_recipe_ref = str(cfg.get("etl_recipe", "") or "")
    if etl_recipe_ref:
        etl_recipe_summary = summarize_etl_recipe(load_etl_recipe(ctx.path(etl_recipe_ref)))

    records = read_jsonl(input_manifest)
    if bool(cfg.get("fail_on_empty", False)) and not records:
        raise RuntimeError(
            "image quality received zero records; canonical quality artifacts were not updated"
        )
    dataset_metadata = _load_json(dataset_metadata_path)
    dataset_version = str(
        dataset_metadata.get("dataset_version")
        or cfg.get("dataset_version")
        or f"{cfg.get('dataset_name', 'dataset')}-unversioned"
    )
    hash_counts: dict[str, int] = Counter()
    for record in records:
        local_image = resolve_local_image(record, raw_image_root)
        if local_image and local_image.exists():
            hash_counts[sha256_file(local_image)] += 1

    enriched_records: list[dict[str, Any]] = []
    diagnostics_by_level: dict[str, int] = defaultdict(int)
    diagnostics_by_code: dict[str, int] = defaultdict(int)
    diagnostic_examples: list[dict[str, Any]] = []
    all_diagnostics: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        enriched, diagnostics = _enrich_record(
            record,
            index=index,
            cfg=cfg,
            dataset_version=dataset_version,
            raw_image_root=raw_image_root,
            policy=policy,
            hash_counts=hash_counts,
        )
        enriched_records.append(enriched)
        for item in diagnostics:
            all_diagnostics.append(item)
            diagnostics_by_level[item["level"]] += 1
            diagnostics_by_code[item["code"]] += 1
            if len(diagnostic_examples) < 20:
                diagnostic_examples.append(item)

    error_count = diagnostics_by_level.get("error", 0)
    warning_count = diagnostics_by_level.get("warn", 0)
    gate_decision = policy.evaluate(all_diagnostics)
    quality_pass = gate_decision.status == "pass"
    report = {
        "status": "pass" if quality_pass else "fail",
        "evaluated_at": utc_now(),
        "gate_decision": gate_decision.to_dict(),
        "quality_policy": policy.to_report(),
        "etl_recipe": etl_recipe_summary,
        "dataset_id": str(cfg.get("dataset_id", "manufacturing_visual_inspection")),
        "dataset_version": dataset_version,
        "input_manifest": display_path(input_manifest, ctx.project_root),
        "output_manifest": display_path(output_manifest, ctx.project_root),
        "record_count": len(records),
        "quality_records": len(enriched_records),
        "error_count": error_count,
        "warning_count": warning_count,
        "diagnostics_by_level": dict(diagnostics_by_level),
        "diagnostics_by_code": dict(diagnostics_by_code),
        "diagnostic_examples": diagnostic_examples,
        "label_counts": summarize_counts(enriched_records, "label"),
        "split_counts": summarize_counts(enriched_records, "split"),
        "label_type_counts": summarize_counts(enriched_records, "label_type"),
        "duplicate_content_hashes": sum(1 for count in hash_counts.values() if count > 1),
        "raw_image_root": display_path(raw_image_root, ctx.project_root),
        "baseline_path": display_path(baseline_path, ctx.project_root),
        "trace": ctx.trace.to_dict(),
    }
    baseline = {
        "dataset_id": report["dataset_id"],
        "dataset_version": dataset_version,
        "record_count": len(enriched_records),
        "label_counts": report["label_counts"],
        "split_counts": report["split_counts"],
        "label_type_counts": report["label_type_counts"],
        "created_at": utc_now(),
    }
    write_jsonl(output_manifest, enriched_records)
    write_json(report_path, report)
    write_json(baseline_path, baseline)
    write_json(ctx.run_dir / "summary.json", report)
    write_markdown_report(
        ctx,
        "Manufacturing Image Quality Pipeline",
        {
            "status": report["status"],
            "record_count": len(enriched_records),
            "error_count": error_count,
            "warning_count": warning_count,
            "output_manifest": report["output_manifest"],
        },
        [
            "",
            "## Contract",
            "",
            "- Input: validated image manifest.",
            "- Output: manufacturing visual inspection quality manifest and quality report.",
            "- Checks: readability, hash duplicate, dimensions, byte-level brightness/blur proxies, label/split integrity.",
            "- Gate: severity and fail-level decisions are loaded from the configured quality policy.",
        ],
    )
    if fail_on_error and not quality_pass:
        raise RuntimeError(f"image quality validation failed: {error_count} errors")
    return report


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()
