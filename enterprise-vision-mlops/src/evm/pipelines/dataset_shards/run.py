from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from evm.core.config import get_nested
from evm.core.dataset import shard_index_identity_digest
from evm.core.image_quality import stable_split, summarize_counts
from evm.core.pipeline import (
    build_context,
    display_path,
    read_jsonl,
    write_json,
    write_jsonl,
    write_markdown_report,
)


def _record_key(record: dict[str, Any]) -> str:
    return str(record.get("sample_id") or record.get("id") or "")


def _stable_order(records: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    def key(record: dict[str, Any]) -> str:
        return hashlib.sha256(f"{seed}:{_record_key(record)}".encode("utf-8")).hexdigest()

    return sorted(records, key=key)


def _split_ratios(value: Any) -> dict[str, float]:
    if isinstance(value, dict):
        return {str(key): float(val) for key, val in value.items()}
    return {"train": 0.6, "validation": 0.2, "test": 0.2}


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("dataset_shards", config_path)
    cfg = ctx.pipeline_config()
    input_manifest = ctx.path(str(cfg.get("input_manifest", "data/validated/mvi_quality_manifest.jsonl")))
    output_dir = ctx.path(str(cfg.get("output_dir", "data/validated/shards")))
    index_path = ctx.path(str(cfg.get("index_path", output_dir / "shard_index.json")))
    records_per_shard = max(1, int(cfg.get("records_per_shard", 128)))
    split_seed = int(cfg.get("split_seed", 20260706))
    ratios = _split_ratios(cfg.get("split_ratios", {}))

    records = read_jsonl(input_manifest)
    normalized: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        sample_id = _record_key(item)
        if not item.get("split"):
            item["split"] = stable_split(sample_id, ratios)
        normalized.append(item)

    shards: list[dict[str, Any]] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    shard_index = 0
    for split in ("train", "validation", "test"):
        split_records = _stable_order([record for record in normalized if record.get("split") == split], split_seed)
        for offset in range(0, len(split_records), records_per_shard):
            chunk = split_records[offset : offset + records_per_shard]
            shard_name = f"{split}_shard_{shard_index:04d}.jsonl"
            shard_path = output_dir / shard_name
            write_jsonl(shard_path, chunk)
            shards.append(
                {
                    "shard_id": f"{split}-{shard_index:04d}",
                    "split": split,
                    "path": display_path(shard_path, ctx.project_root),
                    "record_count": len(chunk),
                    "first_sample_id": _record_key(chunk[0]) if chunk else "",
                    "last_sample_id": _record_key(chunk[-1]) if chunk else "",
                }
            )
            shard_index += 1

    index_payload = {
        "schema_version": "evm.dataset_shards.v1",
        "input_manifest": display_path(input_manifest, ctx.project_root),
        "output_dir": display_path(output_dir, ctx.project_root),
        "records_per_shard": records_per_shard,
        "record_count": len(normalized),
        "shard_count": len(shards),
        "split_counts": summarize_counts(normalized, "split"),
        "label_counts": summarize_counts(normalized, "label"),
        "label_type_counts": summarize_counts(normalized, "label_type"),
        "shards": shards,
        "trace": ctx.trace.to_dict(),
    }
    index_payload["identity_sha256"] = shard_index_identity_digest(index_payload)
    write_json(index_path, index_payload)
    write_json(ctx.run_dir / "summary.json", index_payload)
    write_markdown_report(
        ctx,
        "Dataset Shard Builder",
        {
            "record_count": len(normalized),
            "shard_count": len(shards),
            "records_per_shard": records_per_shard,
            "index_path": display_path(index_path, ctx.project_root),
        },
        [
            "",
            "## Contract",
            "",
            "- Input: manufacturing quality manifest.",
            "- Output: deterministic split/shard JSONL files plus a shard index.",
            "- Next: `vlm_batch_eval` consumes the shard index and writes VLM JSONL outputs.",
        ],
    )
    return index_payload


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()
