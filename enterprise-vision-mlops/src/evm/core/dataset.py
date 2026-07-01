from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


VERSION_FIELDS = ("id", "image_uri", "label", "width", "height", "source")


def stable_record_digest(records: list[dict[str, Any]]) -> str:
    normalized = [
        {field: record.get(field) for field in VERSION_FIELDS}
        for record in sorted(records, key=lambda item: str(item.get("id", "")))
    ]
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
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "pyarrow is required to write Parquet datasets. Install project dependencies "
            "or run the pipeline inside the Airflow container."
        ) from exc

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
    }
