from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from evm.core.readiness_snapshot import (
    capture_readiness_snapshot,
    file_sha256,
    read_json,
    write_json,
)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill digest-bound readiness inputs for a completed W7 candidate.",
    )
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--dataset-metadata", required=True)
    parser.add_argument("--quality-report", required=True)
    parser.add_argument("--source-shard", required=True)
    parser.add_argument("--output-dir")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    candidate_dir = Path(args.candidate_dir)
    candidate_summary_path = candidate_dir / "candidate_summary.json"
    split_manifest_path = candidate_dir / "split_manifest.json"
    candidate = read_json(candidate_summary_path)
    split = read_json(split_manifest_path)
    candidate_id = str(candidate.get("candidate_id") or "")
    dataset_version = str(candidate.get("dataset_version") or split.get("dataset_version") or "")
    expected_record_count = int(split.get("record_count") or 0)
    expected_source_digest = str(split.get("source_shard_index_sha256") or "")
    if not all(
        (
            candidate_id,
            dataset_version,
            expected_record_count,
            expected_source_digest,
        )
    ):
        raise SystemExit("candidate summary or split manifest is missing pinned identity fields")

    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else candidate_dir.parent / "_readiness_inputs"
    )
    manifest_path = capture_readiness_snapshot(
        output_dir=output_dir,
        candidate_id=candidate_id,
        dataset_version=dataset_version,
        expected_record_count=expected_record_count,
        expected_source_digest=expected_source_digest,
        dataset_metadata_path=args.dataset_metadata,
        quality_report_path=args.quality_report,
        source_shard_path=args.source_shard,
    )
    manifest_digest = file_sha256(manifest_path)
    manifest_uri = str(manifest_path).replace("\\", "/")
    if (
        candidate.get("readiness_snapshot_manifest") != manifest_uri
        or candidate.get("readiness_snapshot_manifest_sha256") != manifest_digest
    ):
        candidate["readiness_snapshot_manifest"] = manifest_uri
        candidate["readiness_snapshot_manifest_sha256"] = manifest_digest
        candidate["readiness_snapshot_backfill"] = {
            "created_at": utc_now(),
            "reason": (
                "Bind the completed candidate to immutable training-time data evidence "
                "after a scheduled pipeline overwrote mutable latest artifacts."
            ),
            "dataset_metadata_source": str(Path(args.dataset_metadata)).replace("\\", "/"),
            "quality_report_source": str(Path(args.quality_report)).replace("\\", "/"),
            "source_shard_snapshot": str(Path(args.source_shard)).replace("\\", "/"),
        }
        write_json(candidate_summary_path, candidate)
    print(
        json.dumps(
            {
                "status": "pass",
                "candidate_id": candidate_id,
                "dataset_version": dataset_version,
                "readiness_snapshot_manifest": manifest_uri,
                "readiness_snapshot_manifest_sha256": manifest_digest,
                "candidate_summary": str(candidate_summary_path).replace("\\", "/"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
