from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from evm.core.pipeline import build_context, write_json
from evm.observability.otel import trace_span
from evm.observability.trace_context import W3CTraceContext


def execute_spark_probe(*, master: str, row_count: int, partitions: int) -> dict[str, Any]:
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("evm-s0-runtime-probe")
        .master(master)
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", str(partitions))
        .getOrCreate()
    )
    try:
        frame = spark.range(0, row_count, numPartitions=partitions).selectExpr(
            "id", "CAST(id % 7 AS INT) AS bucket"
        )
        counts = {
            str(row["bucket"]): int(row["count"])
            for row in frame.groupBy("bucket").count().orderBy("bucket").collect()
        }
        return {
            "spark_version": spark.version,
            "application_id": spark.sparkContext.applicationId,
            "master": spark.sparkContext.master,
            "default_parallelism": spark.sparkContext.defaultParallelism,
            "input_partitions": frame.rdd.getNumPartitions(),
            "row_count": sum(counts.values()),
            "bucket_counts": counts,
        }
    finally:
        spark.stop()


def run(config_path: str | Path) -> dict[str, Any]:
    ctx = build_context("spark-runtime-probe", config_path)
    config = ctx.pipeline_config()
    master = str(config.get("master") or "local[2]")
    row_count = int(config.get("row_count") or 4096)
    partitions = int(config.get("partitions") or 2)
    if row_count < 1 or row_count > 100_000:
        raise ValueError("spark_runtime_probe_row_count_out_of_bounds")
    if partitions < 1 or partitions > 8:
        raise ValueError("spark_runtime_probe_partitions_out_of_bounds")

    parent = W3CTraceContext.parse(
        ctx.trace.traceparent,
        tracestate=ctx.trace.tracestate or None,
    )
    with trace_span(
        "spark.runtime_probe",
        parent=parent,
        kind="consumer",
        attributes={
            "evm.stage": "spark",
            "spark.master": master,
            "spark.input.rows": row_count,
            "spark.input.partitions": partitions,
        },
    ) as active:
        result = execute_spark_probe(
            master=master,
            row_count=row_count,
            partitions=partitions,
        )
        active.set_attribute("spark.output.rows", int(result["row_count"]))

    canonical = json.dumps(
        result["bucket_counts"], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    report = {
        "schema_version": "evm.spark_runtime_probe.v1",
        "status": "pass" if result["row_count"] == row_count else "fail",
        "execution_mode": "local_control",
        "claim_boundary": (
            "This is a real local Spark control probe. Distributed executor, shuffle, spill, "
            "and scale behavior remain Scenario S5 work."
        ),
        "trace": ctx.trace.to_dict(),
        "spark_span": {
            "trace_id": active.context.trace_id,
            "span_id": active.context.span_id,
            "parent_span_id": active.context.parent_span_id,
        },
        "result": result,
        "result_sha256": hashlib.sha256(canonical).hexdigest(),
    }
    report_path = ctx.run_dir / "spark_runtime_probe.json"
    write_json(report_path, report)
    configured_report = config.get("probe_report")
    if configured_report:
        write_json(ctx.path(str(configured_report)), report)
    if report["status"] != "pass":
        raise RuntimeError("spark_runtime_probe_row_count_mismatch")
    return {**report, "report_path": str(report_path)}
