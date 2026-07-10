from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evm.core.config import get_nested, map_runtime_data_path
from evm.core.data_intake import (
    IMAGE_EXTENSIONS,
    build_manifest_records,
    cleaning_benchmark,
    directory_size_bytes,
    infer_dataset_layout,
    is_mask_path,
    iter_image_files,
)
from evm.core.domain_pack import load_domain_pack
from evm.core.pipeline import build_context, display_path, utc_now, write_json, write_jsonl, write_markdown_report


def _dataset_entry(
    dataset: dict[str, Any],
    *,
    root: Path,
    image_files: list[Path],
    mask_files: list[Path],
    license_review_required: bool,
) -> dict[str, Any]:
    exists = root.exists()
    sample_images = [path for path in image_files if not is_mask_path(path)]
    status = "ready_for_manifest" if sample_images else ("root_empty" if exists else "root_missing")
    license_status = "needs_manual_review" if license_review_required else "recorded"
    return {
        "id": str(dataset.get("id", "")),
        "name": str(dataset.get("name", "")),
        "role": str(dataset.get("role", "")),
        "status": status,
        "raw_root": str(root),
        "exists": exists,
        "layout": infer_dataset_layout(str(dataset.get("id", "")), root),
        "source_url": str(dataset.get("source_url", "")),
        "license_id": str(dataset.get("license_id", "manual-review-required")),
        "license_url": str(dataset.get("license_url", "")),
        "license_status": license_status,
        "access_policy": str(dataset.get("access_policy", "")),
        "retention_policy": str(dataset.get("retention_policy", "local-lab-retain-until-user-removal")),
        "object_prefix": str(dataset.get("object_prefix", "")),
        "sample_image_count": len(sample_images),
        "mask_image_count": len(mask_files),
        "total_image_files": len(image_files),
        "scanned_bytes": directory_size_bytes(image_files),
    }


def _acquisition_plan(
    datasets: list[dict[str, Any]],
    *,
    checkpoint_dir: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    missing = [item for item in datasets if item["status"] != "ready_for_manifest"]
    return {
        "status": "ready_for_import" if not missing and datasets else "needs_data",
        "created_at": utc_now(),
        "checkpoint_dir": str(checkpoint_dir),
        "target_manifest": str(manifest_path),
        "steps": [
            "Review source license and access terms before acquisition.",
            "Download or mount raw dataset files under the configured F-drive raw_root.",
            "Run dataset-intake-audit to create source registry, acquisition plan, cleaning benchmark, and import manifest.",
            "Point image-quality or a production validation config at the generated import manifest.",
            "Promote curated records into validated shards only after fatal quality errors are zero.",
        ],
        "datasets": [
            {
                "id": item["id"],
                "status": item["status"],
                "raw_root": item["raw_root"],
                "object_prefix": item["object_prefix"],
                "checkpoint_file": str(checkpoint_dir / f"{item['id']}_checkpoint.json"),
                "next_action": "scan_ready" if item["status"] == "ready_for_manifest" else "download_or_mount_raw_data",
            }
            for item in datasets
        ],
    }


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("dataset_intake_audit", config_path)
    cfg = ctx.pipeline_config()
    allowed_extensions = {
        str(item).lower() for item in cfg.get("allowed_extensions", sorted(IMAGE_EXTENSIONS))
    }
    domain_pack_path, pack = load_domain_pack(ctx.config, str(cfg.get("domain_pack", "")))
    output_dir = ctx.path(str(cfg.get("output_dir", "data/raw/industrial")))
    registry_path = ctx.path(str(cfg.get("registry_path", output_dir / "source_registry.json")))
    acquisition_plan_path = ctx.path(str(cfg.get("acquisition_plan_path", output_dir / "acquisition_plan.json")))
    cleaning_report_path = ctx.path(str(cfg.get("cleaning_report_path", output_dir / "cleaning_benchmark.json")))
    manifest_path = ctx.path(str(cfg.get("manifest_path", output_dir / "mvi_import_manifest.jsonl")))
    checkpoint_dir = ctx.path(str(cfg.get("checkpoint_dir", output_dir / "_checkpoints")))
    max_scan_files = int(cfg.get("max_scan_files", 0) or 0)
    max_quality_samples = int(cfg.get("max_quality_samples", 200) or 200)
    license_review_required = bool(cfg.get("license_review_required", True))
    fail_on_empty = bool(cfg.get("fail_on_empty", False))
    dataset_version = str(cfg.get("dataset_version", "real-intake-unversioned"))

    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    dataset_entries: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []
    for dataset in pack.get("datasets", []):
        root = map_runtime_data_path(str(dataset.get("raw_root", "")))
        image_files = iter_image_files(root, allowed_extensions, max_files=max_scan_files)
        mask_files = [path for path in image_files if is_mask_path(path)]
        dataset_entries.append(
            _dataset_entry(
                dataset,
                root=root,
                image_files=image_files,
                mask_files=mask_files,
                license_review_required=license_review_required,
            )
        )
        all_records.extend(
            build_manifest_records(
                dataset,
                image_files,
                mask_files,
                dataset_version=dataset_version,
                allowed_extensions=allowed_extensions,
            )
        )

    if fail_on_empty and not all_records:
        raise RuntimeError(
            "dataset intake discovered zero records; canonical manifests were not updated"
        )

    registry = {
        "schema_version": "evm.data_source_registry.v1",
        "created_at": utc_now(),
        "domain_pack": display_path(domain_pack_path, ctx.project_root),
        "dataset_count": len(dataset_entries),
        "datasets_ready": sum(1 for item in dataset_entries if item["status"] == "ready_for_manifest"),
        "datasets": dataset_entries,
        "policy": {
            "storage_root": str(get_nested(ctx.config, "paths.external_storage_root", "")),
            "allowed_extensions": sorted(allowed_extensions),
            "license_review_required": license_review_required,
            "large_data_policy": "store raw datasets and generated manifests outside the Git repository on the F drive",
        },
        "trace": ctx.trace.to_dict(),
    }
    plan = _acquisition_plan(dataset_entries, checkpoint_dir=checkpoint_dir, manifest_path=manifest_path)
    benchmark = {
        "schema_version": "evm.cleaning_benchmark.v1",
        "created_at": utc_now(),
        "status": "ready" if all_records else "needs_data",
        "manifest_path": str(manifest_path),
        "benchmark": cleaning_benchmark(all_records, max_quality_samples=max_quality_samples),
        "trace": ctx.trace.to_dict(),
    }
    summary = {
        "status": "ready_for_import" if all_records else "needs_data",
        "domain_pack": display_path(domain_pack_path, ctx.project_root),
        "datasets_checked": len(dataset_entries),
        "datasets_ready": registry["datasets_ready"],
        "records_discovered": len(all_records),
        "manifest_path": str(manifest_path),
        "source_registry": str(registry_path),
        "acquisition_plan": str(acquisition_plan_path),
        "cleaning_report": str(cleaning_report_path),
        "trace": ctx.trace.to_dict(),
    }

    write_json(registry_path, registry)
    write_json(acquisition_plan_path, plan)
    write_json(cleaning_report_path, benchmark)
    write_jsonl(manifest_path, all_records)
    write_json(ctx.run_dir / "summary.json", summary)
    write_markdown_report(
        ctx,
        "Dataset Intake Audit Pipeline",
        {
            "status": summary["status"],
            "datasets_checked": summary["datasets_checked"],
            "datasets_ready": summary["datasets_ready"],
            "records_discovered": summary["records_discovered"],
            "manifest_path": summary["manifest_path"],
        },
        [
            "",
            "## Contract",
            "",
            "- Input: domain-pack dataset source definitions and F-drive raw dataset roots.",
            "- Output: source registry, acquisition plan, cleaning benchmark, and import manifest.",
            "- A `needs_data` status means the pipeline is wired correctly but raw dataset files are not present yet.",
        ],
    )
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()
