from __future__ import annotations

from collections.abc import Sequence

from evm.core.object_store import ObjectStoreClient, configured_buckets
from evm.core.pipeline import build_context, write_json, write_markdown_report


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("object_storage_bootstrap", config_path)
    cfg = ctx.pipeline_config()
    buckets = [str(bucket) for bucket in cfg.get("buckets", configured_buckets(ctx.config))]

    client = ObjectStoreClient.from_config(ctx.config)
    ensured = client.ensure_buckets(buckets)
    summary = {
        "endpoint_url": client.endpoint_url,
        "buckets": ensured,
        "bucket_count": len(ensured),
        "trace_id": ctx.trace.trace_id,
        "pipeline_run_id": ctx.run_id,
    }
    write_json(ctx.run_dir / "summary.json", summary)
    write_markdown_report(
        ctx,
        "Object Storage Bootstrap Pipeline",
        summary,
        [
            "",
            "## Contract",
            "",
            "- Input: object store endpoint and bucket configuration.",
            "- Output: required MinIO/S3 buckets exist.",
            "- Next: `data_ingestion` uploads raw dataset objects and manifest.",
        ],
    )
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()
