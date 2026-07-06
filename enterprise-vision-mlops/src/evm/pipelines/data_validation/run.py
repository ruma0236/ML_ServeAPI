from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from evm.core.config import get_nested
from evm.core.dataset import dimension_summary, label_distribution, stable_record_digest, write_parquet
from evm.core.object_store import ObjectStoreClient
from evm.core.pipeline import (
    build_context,
    display_path,
    read_jsonl,
    utc_now,
    write_json,
    write_jsonl,
    write_markdown_report,
)


REQUIRED_FIELDS = ("id", "image_uri", "label", "width", "height")


def _is_valid_record(
    record: dict[str, object],
    allowed_extensions: set[str],
    client: ObjectStoreClient,
) -> tuple[bool, str]:
    missing_fields = [field for field in REQUIRED_FIELDS if record.get(field) in (None, "")]
    if missing_fields:
        return False, f"missing_{missing_fields[0]}"
    image_uri = str(record.get("image_uri", ""))
    extension = Path(image_uri).suffix.lower()
    if not image_uri:
        return False, "missing_image_uri"
    if extension not in allowed_extensions:
        return False, "unsupported_extension"
    if image_uri.startswith("s3://") and not client.object_exists(image_uri):
        return False, "object_missing"
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
    dataset_name = str(cfg.get("dataset_name", "public-vision-local"))
    processed_parquet = ctx.path(str(cfg.get("processed_parquet", "data/processed/processed_dataset.parquet")))
    validated_parquet = ctx.path(str(cfg.get("validated_parquet", "data/validated/validated_dataset.parquet")))
    dataset_metadata_path = ctx.path(
        str(cfg.get("dataset_metadata", "data/validated/dataset_version.json"))
    )
    processed_bucket = str(get_nested(ctx.config, "object_store.processed_bucket", "processed"))
    validated_bucket = str(get_nested(ctx.config, "object_store.validated_bucket", "validated"))

    client = ObjectStoreClient.from_config(ctx.config)
    client.ensure_buckets([processed_bucket, validated_bucket])

    records = read_jsonl(input_manifest)
    valid_records: list[dict[str, object]] = []
    processed_records: list[dict[str, object]] = []
    failures: Counter[str] = Counter()

    for record in records:
        is_valid, reason = _is_valid_record(record, allowed_extensions, client)
        processed_record = dict(record)
        processed_record["validation_status"] = "valid" if is_valid else "invalid"
        processed_record["validation_reason"] = reason
        processed_records.append(processed_record)
        if is_valid:
            valid_records.append(record)
        else:
            failures[reason] += 1

    write_jsonl(output_manifest, valid_records)
    manifest_digest = stable_record_digest(valid_records)
    dataset_version = f"{dataset_name}-{manifest_digest[:12]}"
    object_prefix = f"{dataset_name}/{dataset_version}"
    processed_parquet_info = write_parquet(processed_parquet, processed_records)
    validated_parquet_info = write_parquet(validated_parquet, valid_records)
    validated_manifest_uri = client.upload_file(
        output_manifest,
        validated_bucket,
        f"{object_prefix}/manifests/{output_manifest.name}",
        "application/x-ndjson",
    )
    processed_parquet_uri = client.upload_file(
        processed_parquet,
        processed_bucket,
        f"{object_prefix}/processed/{processed_parquet.name}",
        "application/vnd.apache.parquet",
    )
    validated_parquet_uri = client.upload_file(
        validated_parquet,
        validated_bucket,
        f"{object_prefix}/validated/{validated_parquet.name}",
        "application/vnd.apache.parquet",
    )
    report = {
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
        "input_records": len(records),
        "valid_records": len(valid_records),
        "invalid_records": len(records) - len(valid_records),
        "failure_reasons": dict(failures),
        "label_counts": label_distribution(valid_records),
        "dimension_summary": dimension_summary(valid_records),
        "schema": {
            "required_fields": list(REQUIRED_FIELDS),
            "allowed_extensions": sorted(allowed_extensions),
        },
        "input_manifest": display_path(input_manifest, ctx.project_root),
        "output_manifest": display_path(output_manifest, ctx.project_root),
        "validated_manifest_uri": validated_manifest_uri,
        "processed_parquet": display_path(processed_parquet, ctx.project_root),
        "processed_parquet_uri": processed_parquet_uri,
        "validated_parquet": display_path(validated_parquet, ctx.project_root),
        "validated_parquet_uri": validated_parquet_uri,
    }
    report_path = ctx.path(get_nested(ctx.config, "paths.validated_zone", "data/validated")) / report_name
    write_json(report_path, report)
    validation_report_uri = client.upload_file(
        report_path,
        validated_bucket,
        f"{object_prefix}/reports/{report_name}",
        "application/json",
    )
    dataset_metadata = {
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
        "manifest_digest": manifest_digest,
        "created_at": utc_now(),
        "record_count": len(valid_records),
        "label_counts": label_distribution(valid_records),
        "dimension_summary": dimension_summary(valid_records),
        "input_manifest": display_path(input_manifest, ctx.project_root),
        "output_manifest": display_path(output_manifest, ctx.project_root),
        "validated_manifest_uri": validated_manifest_uri,
        "processed_parquet": display_path(processed_parquet, ctx.project_root),
        "processed_parquet_uri": processed_parquet_uri,
        "processed_parquet_info": processed_parquet_info,
        "validated_parquet": display_path(validated_parquet, ctx.project_root),
        "validated_parquet_uri": validated_parquet_uri,
        "validated_parquet_info": validated_parquet_info,
        "validation_report": display_path(report_path, ctx.project_root),
        "validation_report_uri": validation_report_uri,
        "trace": ctx.trace.to_dict(),
    }
    write_json(dataset_metadata_path, dataset_metadata)
    dataset_metadata_uri = client.upload_file(
        dataset_metadata_path,
        validated_bucket,
        f"{object_prefix}/metadata/{dataset_metadata_path.name}",
        "application/json",
    )
    report["validation_report_uri"] = validation_report_uri
    report["dataset_metadata"] = display_path(dataset_metadata_path, ctx.project_root)
    report["dataset_metadata_uri"] = dataset_metadata_uri
    write_json(ctx.run_dir / "summary.json", report)
    write_markdown_report(
        ctx,
        "Data Validation Pipeline",
        report,
        [
            "",
            "## Contract",
            "",
            "- Input: local raw manifest with MinIO/S3 image URIs.",
            "- Output: validated manifest, validation report, Parquet datasets, dataset version metadata.",
            "- Next: `training` consumes validated records and records the dataset version.",
        ],
    )
    return report


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()
