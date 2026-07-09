from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pyarrow.compute as pc
import pyarrow.parquet as pq

from evm.core.pipeline import build_context, display_path, utc_now, write_json, write_markdown_report


ENGINE_MATRIX = [
    {
        "engine": "DuckDB",
        "role": "local SQL analytics over Parquet and object-store data",
        "fit": "best first step for analyst-style dataset QA and ad hoc joins",
        "tradeoffs": [
            "single-node execution",
            "excellent local developer ergonomics",
            "needs package/runtime addition",
        ],
        "recommended_stage": "W6/W7 local lakehouse query layer",
    },
    {
        "engine": "Polars",
        "role": "fast lazy dataframe transformations over Parquet",
        "fit": "best for local feature/curation transforms that need Python-native pipelines",
        "tradeoffs": [
            "single-node execution",
            "less SQL-native than DuckDB",
            "good bridge between ETL code and notebook analysis",
        ],
        "recommended_stage": "large local transforms before Spark is justified",
    },
    {
        "engine": "Spark",
        "role": "distributed ETL and large-scale batch processing",
        "fit": "best when data volume exceeds one workstation or requires cluster scheduling",
        "tradeoffs": [
            "heavier local runtime",
            "requires cluster resource planning",
            "strong production ecosystem",
        ],
        "recommended_stage": "post-local scale-out or multi-node processing",
    },
    {
        "engine": "Iceberg",
        "role": "open table format for ACID snapshots, schema evolution, and partition evolution",
        "fit": "best when dataset versions become managed tables rather than loose Parquet files",
        "tradeoffs": [
            "table catalog required",
            "not a query engine by itself",
            "pairs with DuckDB, Spark, Trino, or Flink",
        ],
        "recommended_stage": "production dataset registry and versioned lakehouse tables",
    },
]


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _engine_availability() -> dict[str, bool]:
    return {
        "duckdb": _module_available("duckdb"),
        "polars": _module_available("polars"),
        "pyspark": _module_available("pyspark"),
        "pyiceberg": _module_available("pyiceberg"),
        "pyarrow": True,
    }


def _counts(table: Any, column_name: str) -> dict[str, int]:
    if column_name not in table.column_names:
        return {}
    values = pc.value_counts(table[column_name]).to_pylist()
    return {
        str(item["values"]): int(item["counts"])
        for item in values
    }


def _read_parquet_summary(path: Path) -> dict[str, Any]:
    metadata = pq.read_metadata(path)
    table = pq.read_table(path)
    return {
        "path": str(path),
        "file_size_bytes": path.stat().st_size,
        "row_count": metadata.num_rows,
        "row_group_count": metadata.num_row_groups,
        "column_count": metadata.num_columns,
        "columns": table.column_names,
        "schema": str(table.schema),
        "label_counts": _counts(table, "label"),
        "label_type_counts": _counts(table, "label_type"),
        "split_counts": _counts(table, "split"),
        "class_counts_sample": dict(list(_counts(table, "class_name").items())[:20]),
    }


def _recommendation(availability: dict[str, bool], summary: dict[str, Any]) -> dict[str, Any]:
    row_count = int(summary.get("row_count", 0) or 0)
    if availability.get("duckdb"):
        first_engine = "DuckDB"
        prototype = "duckdb_parquet_sql"
    else:
        first_engine = "PyArrow now, DuckDB next"
        prototype = "pyarrow_parquet_probe"
    scale_trigger = "Spark or Iceberg is not required for the current 10k-record VisA proof."
    if row_count >= 10_000_000:
        scale_trigger = "Spark plus Iceberg should be prioritized because row count is in the multi-million range."
    return {
        "first_engine": first_engine,
        "current_prototype": prototype,
        "next_dependency_to_add": "duckdb==1.x for SQL lakehouse probes" if not availability.get("duckdb") else "",
        "scale_trigger": scale_trigger,
        "storage_policy": "Keep Parquet and table data on F:/EnterpriseMLOps_Data/enterprise-vision-mlops.",
        "control_panel_use": "Expose dataset row counts, split/label distributions, engine availability, and table freshness.",
    }


def _write_recommendation_md(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Lakehouse Probe Recommendation",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Input parquet: `{payload['input_parquet']}`",
        f"- Row count: `{payload['parquet_summary']['row_count']}`",
        f"- Recommended first engine: `{payload['recommendation']['first_engine']}`",
        f"- Current prototype: `{payload['recommendation']['current_prototype']}`",
        "",
        "## Engine Tradeoff Matrix",
        "",
        "| Engine | Role | Fit | Recommended Stage |",
        "|---|---|---|---|",
    ]
    for item in payload["engine_matrix"]:
        lines.append(
            f"| {item['engine']} | {item['role']} | {item['fit']} | {item['recommended_stage']} |"
        )
    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            f"- {payload['recommendation']['scale_trigger']}",
            f"- {payload['recommendation']['storage_policy']}",
            f"- {payload['recommendation']['control_panel_use']}",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("lakehouse_probe", config_path)
    cfg = ctx.pipeline_config()
    input_parquet = ctx.path(str(cfg.get("input_parquet", "data/validated/validated_dataset.parquet")))
    output_dir = ctx.path(str(cfg.get("output_dir", "artifacts/lakehouse")))
    probe_report = ctx.path(str(cfg.get("probe_report", output_dir / "lakehouse_probe.json")))
    tradeoff_matrix = ctx.path(str(cfg.get("tradeoff_matrix", output_dir / "engine_tradeoff_matrix.json")))
    recommendation_doc = ctx.path(str(cfg.get("recommendation_doc", output_dir / "lakehouse_recommendation.md")))

    availability = _engine_availability()
    parquet_summary = _read_parquet_summary(input_parquet)
    payload = {
        "schema_version": "evm.lakehouse_probe.v1",
        "created_at": utc_now(),
        "status": "pass",
        "input_parquet": display_path(input_parquet, ctx.project_root),
        "engine_availability": availability,
        "engine_matrix": ENGINE_MATRIX,
        "parquet_summary": parquet_summary,
        "recommendation": _recommendation(availability, parquet_summary),
        "trace": ctx.trace.to_dict(),
    }

    write_json(probe_report, payload)
    write_json(tradeoff_matrix, ENGINE_MATRIX)
    write_json(ctx.run_dir / "summary.json", payload)
    _write_recommendation_md(recommendation_doc, payload)
    write_markdown_report(
        ctx,
        "Lakehouse Probe Pipeline",
        {
            "status": payload["status"],
            "row_count": parquet_summary["row_count"],
            "row_group_count": parquet_summary["row_group_count"],
            "column_count": parquet_summary["column_count"],
            "first_engine": payload["recommendation"]["first_engine"],
            "probe_report": display_path(probe_report, ctx.project_root),
        },
        [
            "",
            "## Contract",
            "",
            "- Input: validated Parquet dataset.",
            "- Output: lakehouse probe report, engine tradeoff matrix, and recommendation doc.",
            "- Purpose: decide when to use DuckDB, Polars, Spark, and Iceberg while keeping TB-scale data on the F drive.",
        ],
    )
    return payload


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()
