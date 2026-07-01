from __future__ import annotations

import base64
from collections.abc import Sequence

from evm.core.config import get_nested
from evm.core.object_store import ObjectStoreClient
from evm.core.pipeline import build_context, utc_now, write_json, write_jsonl, write_markdown_report


SAMPLE_JPEG_BYTES = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////"
    "////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAVEAEBAAAAAAAAAAAAAAAAAAAAAP/aAAwDAQACEAMQAAAB9A//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/ASP/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/ASP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Al//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z"
)


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("data_ingestion", config_path)
    cfg = ctx.pipeline_config()
    raw_zone = ctx.path(get_nested(ctx.config, "paths.raw_zone", "data/raw"))
    image_dir = raw_zone / "images"
    manifest_path = raw_zone / str(cfg.get("manifest_name", "raw_manifest.jsonl"))
    sample_count = int(cfg.get("sample_records", 8))
    dataset_name = str(cfg.get("dataset_name", "public-vision-local"))
    dataset_seed_version = str(cfg.get("dataset_seed_version", "v1"))
    object_prefix = str(cfg.get("object_prefix", f"{dataset_name}/{dataset_seed_version}")).strip("/")
    raw_bucket = str(get_nested(ctx.config, "object_store.raw_bucket", "raw"))

    client = ObjectStoreClient.from_config(ctx.config)
    client.ensure_bucket(raw_bucket)

    records = []
    labels = ["normal", "scratch", "stain", "normal"]
    uploaded_images: list[str] = []
    for idx in range(sample_count):
        image_name = f"sample_{idx + 1:04d}.jpg"
        image_path = image_dir / image_name
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(SAMPLE_JPEG_BYTES)
        image_key = f"{object_prefix}/images/{image_name}"
        image_uri = client.upload_file(image_path, raw_bucket, image_key, "image/jpeg")
        uploaded_images.append(image_uri)
        records.append(
            {
                "id": f"sample_{idx + 1:04d}",
                "image_uri": image_uri,
                "label": labels[idx % len(labels)],
                "width": 640 + (idx % 3) * 16,
                "height": 480 + (idx % 2) * 16,
                "source": dataset_name,
                "source_version": dataset_seed_version,
                "ingested_at": utc_now(),
            }
        )

    write_jsonl(manifest_path, records)
    manifest_key = str(cfg.get("manifest_key", f"{object_prefix}/manifests/{manifest_path.name}"))
    raw_manifest_uri = client.upload_file(
        manifest_path,
        raw_bucket,
        manifest_key,
        "application/x-ndjson",
    )
    summary = {
        "dataset_name": dataset_name,
        "dataset_seed_version": dataset_seed_version,
        "records": len(records),
        "manifest": str(manifest_path.relative_to(ctx.project_root)),
        "raw_manifest_uri": raw_manifest_uri,
        "raw_bucket": raw_bucket,
        "uploaded_image_objects": len(uploaded_images),
        "raw_zone": str(raw_zone.relative_to(ctx.project_root)),
        "trace_id": ctx.trace.trace_id,
        "pipeline_run_id": ctx.run_id,
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
            "- Input: deterministic public-vision seed records.",
            "- Output: local raw manifest and MinIO raw image objects.",
            "- Next: `data_validation` consumes the raw manifest and verifies object existence.",
        ],
    )
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()
