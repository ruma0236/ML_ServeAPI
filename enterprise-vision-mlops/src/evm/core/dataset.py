from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


VERSION_FIELDS = (
    "dataset_id",
    "sample_id",
    "relative_path",
    "content_sha256",
    "label",
    "width",
    "height",
    "source",
)


def canonical_record_identity(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    sample_id = str(record.get("sample_id") or record.get("id") or "")
    relative_path = str(metadata.get("relative_path") or "").replace("\\", "/")
    if not relative_path:
        relative_path = sample_id or str(record.get("image_uri") or "").replace("\\", "/")
    return {
        "dataset_id": str(record.get("dataset_id") or record.get("source") or ""),
        "sample_id": sample_id,
        "relative_path": relative_path,
        "content_sha256": str(record.get("content_sha256") or ""),
        "label": record.get("label"),
        "width": record.get("width"),
        "height": record.get("height"),
        "source": str(record.get("source_uri") or record.get("source") or ""),
    }


def stable_record_digest(records: list[dict[str, Any]]) -> str:
    normalized = sorted(
        (canonical_record_identity(record) for record in records),
        key=lambda item: (
            str(item["dataset_id"]),
            str(item["sample_id"]),
            str(item["relative_path"]),
        ),
    )
    payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def dimension_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    widths = [int(record.get("width", 0) or 0) for record in records]
    heights = [int(record.get("height", 0) or 0) for record in records]
    if not widths or not heights:
        return {
            "count": 0,
            "width_min": 0,
            "width_max": 0,
            "height_min": 0,
            "height_max": 0,
        }
    return {
        "count": len(records),
        "width_min": min(widths),
        "width_max": max(widths),
        "height_min": min(heights),
        "height_max": max(heights),
        "width_avg": round(sum(widths) / len(widths), 3),
        "height_avg": round(sum(heights) / len(heights), 3),
    }


def label_distribution(records: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(record.get("label", "")) for record in records if record.get("label")))


def write_parquet(path: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            for record in records:
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        columns = sorted({key for record in records for key in record.keys()})
        return {
            "path": str(path),
            "rows": len(records),
            "columns": columns,
            "bytes": path.stat().st_size,
            "format": "jsonl_fallback",
            "warning": "pyarrow unavailable; wrote JSONL fallback at configured parquet path",
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    if records:
        table = pa.Table.from_pylist(records)
    else:
        table = pa.table(
            {
                "id": pa.array([], type=pa.string()),
                "image_uri": pa.array([], type=pa.string()),
                "label": pa.array([], type=pa.string()),
                "width": pa.array([], type=pa.int64()),
                "height": pa.array([], type=pa.int64()),
            }
        )
    pq.write_table(table, path)
    return {
        "path": str(path),
        "rows": table.num_rows,
        "columns": list(table.column_names),
        "bytes": path.stat().st_size,
        "format": "parquet",
    }
