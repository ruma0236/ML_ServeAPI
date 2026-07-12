from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evm.core.config import get_nested, load_config, map_runtime_data_path, resolve_path
from evm.core.dataset import shard_index_identity_digest
from evm.core.readiness_snapshot import read_json, write_json


def configured_path(config: dict[str, Any], key: str, default: str) -> Path:
    value = str(get_nested(config, key, default))
    mapped = map_runtime_data_path(value)
    if mapped.is_absolute():
        return mapped
    return resolve_path(config, mapped)


def edge_manifest_records(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    first: dict[str, Any] = {}
    last: dict[str, Any] = {}
    with path.open("r", encoding="utf-8-sig") as fp:
        for line in fp:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            if not first:
                first = payload
            last = payload
    return first, last


def canonical_relative_path(record: dict[str, Any]) -> str:
    metadata = record.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    return str(metadata.get("relative_path") or "").replace("\\", "/")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture and compare dataset identity across host and container runtimes.",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--runtime-label", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--compare-to")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    dataset_path = configured_path(
        config,
        "pipelines.data_validation.dataset_metadata",
        "data/validated/visa/dataset_version.json",
    )
    quality_path = configured_path(
        config,
        "pipelines.image_quality.report_path",
        "data/validated/visa/mvi_quality_report.json",
    )
    shard_path = configured_path(
        config,
        "pipelines.dataset_shards.index_path",
        "data/validated/visa/shards/shard_index.json",
    )
    manifest_path = configured_path(
        config,
        "pipelines.dataset_intake_audit.manifest_path",
        "data/raw/industrial/mvi_import_manifest.jsonl",
    )
    dataset = read_json(dataset_path)
    quality = read_json(quality_path)
    shard = read_json(shard_path)
    first, last = edge_manifest_records(manifest_path)
    calculated_shard_identity = shard_index_identity_digest(shard)
    first_relative = canonical_relative_path(first)
    last_relative = canonical_relative_path(last)

    blockers: list[str] = []
    if dataset.get("dataset_version") != quality.get("dataset_version"):
        blockers.append("quality_dataset_version_mismatch")
    if int(dataset.get("record_count") or 0) != int(shard.get("record_count") or 0):
        blockers.append("shard_record_count_mismatch")
    if shard.get("identity_sha256") != calculated_shard_identity:
        blockers.append("shard_identity_digest_mismatch")
    if not first_relative or Path(first_relative).is_absolute() or ":" in first_relative:
        blockers.append("first_sample_relative_path_not_canonical")
    if not last_relative or Path(last_relative).is_absolute() or ":" in last_relative:
        blockers.append("last_sample_relative_path_not_canonical")

    identity = {
        "dataset_version": dataset.get("dataset_version"),
        "manifest_digest": dataset.get("manifest_digest"),
        "record_count": dataset.get("record_count"),
        "quality_split_counts": quality.get("split_counts"),
        "shard_split_counts": shard.get("split_counts"),
        "shard_identity_sha256": calculated_shard_identity,
        "first_sample_id": first.get("sample_id") or first.get("id"),
        "last_sample_id": last.get("sample_id") or last.get("id"),
        "first_relative_path": first_relative,
        "last_relative_path": last_relative,
    }
    comparison: dict[str, Any] | None = None
    if args.compare_to:
        baseline = read_json(Path(args.compare_to))
        baseline_identity = baseline.get("identity")
        baseline_identity = baseline_identity if isinstance(baseline_identity, dict) else {}
        mismatches = [
            key
            for key, value in identity.items()
            if baseline_identity.get(key) != value
        ]
        comparison = {
            "baseline": str(args.compare_to).replace("\\", "/"),
            "matching_fields": sorted(set(identity) - set(mismatches)),
            "mismatches": mismatches,
        }
        blockers.extend(f"cross_runtime_mismatch:{key}" for key in mismatches)

    report = {
        "schema_version": "evm.dataset_cross_runtime_identity.v1",
        "status": "pass" if not blockers else "blocked",
        "runtime_label": args.runtime_label,
        "identity": identity,
        "comparison": comparison,
        "blockers": sorted(set(blockers)),
    }
    write_json(Path(args.output), report)
    print(json.dumps(report, indent=2))
    if blockers:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
