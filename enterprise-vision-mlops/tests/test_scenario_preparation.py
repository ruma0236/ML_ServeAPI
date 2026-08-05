from __future__ import annotations

import hashlib
import json
from pathlib import Path

from evm.pipelines.scenario_preparation.run import (
    build_dolly_approved_view,
    build_scienceqa_adaptation_view,
)


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def test_scienceqa_view_is_deterministic_and_preserves_source_split(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    records = []
    for index in range(12):
        image = images / f"{index}.png"
        image.write_bytes(b"image" + bytes([index]))
        records.append(
            {
                "sample_id": f"sample-{index}",
                "dataset_version": "scienceqa-v1",
                "split": "test",
                "content_sha256": hashlib.sha256(f"content-{index}".encode()).hexdigest(),
                "image_uri": "file:///" + str(image).replace("\\", "/"),
                "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                "pii_flags": [],
            }
        )
    source = tmp_path / "source.jsonl"
    write_jsonl(source, records)

    first = build_scienceqa_adaptation_view(
        source, tmp_path / "first", train_count=6, validation_count=3, test_count=3
    )
    repeated = build_scienceqa_adaptation_view(
        source, tmp_path / "first", train_count=6, validation_count=3, test_count=3
    )
    second = build_scienceqa_adaptation_view(
        source, tmp_path / "second", train_count=6, validation_count=3, test_count=3
    )

    assert first["output_manifest_sha256"] == second["output_manifest_sha256"]
    assert first == repeated
    assert first["split_counts"] == {"test": 3, "train": 6, "validation": 3}
    output = [json.loads(line) for line in Path(first["output_manifest_uri"]).read_text().splitlines()]
    assert {item["source_split"] for item in output} == {"test"}


def test_dolly_view_excludes_flagged_records_and_writes_approval(tmp_path: Path) -> None:
    records = []
    for split in ("train", "validation", "test"):
        for index in range(5):
            records.append(
                {
                    "sample_id": f"{split}-{index}",
                    "dataset_version": "dolly-v1",
                    "split": split,
                    "content_sha256": hashlib.sha256(f"{split}-{index}".encode()).hexdigest(),
                    "pii_flags": ["email_pattern"] if index == 0 else [],
                }
            )
    source = tmp_path / "dolly.jsonl"
    write_jsonl(source, records)

    result = build_dolly_approved_view(
        source,
        tmp_path / "approved",
        approver="data-steward",
        reason="Approve a bounded PII-filtered local tuning view",
        train_count=3,
        validation_count=2,
        test_count=2,
    )

    output = [json.loads(line) for line in Path(result["output_manifest_uri"]).read_text().splitlines()]
    disposition = json.loads(Path(result["quality_disposition_uri"]).read_text())
    assert len(output) == 7
    assert all(not item["pii_flags"] for item in output)
    assert disposition["decision"] == "approved"
    assert disposition["excluded_pii_flagged_records"] == 3
