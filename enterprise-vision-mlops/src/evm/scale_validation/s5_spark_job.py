from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import statistics
import time
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

FINGERPRINT_MODULUS = 2_147_483_647
RECORD_KEY_SHARD_FACTOR = 1_000_000_000_000


class S5RuntimeError(RuntimeError):
    pass


def main() -> int:
    args = _parse_args()
    final_root = Path(args.commit_root) / args.logical_output_id
    report_path = Path(args.report_path)
    existing = _read_commit(final_root)
    if existing is not None:
        _assert_existing(existing, args)
        write_public_json(
            report_path,
            {
                "schema_version": "evm.s5_spark_job_report.v1",
                "status": "passed",
                "commit_state": "replayed",
                "result": {**existing["result"], "commit_state": "replayed"},
            },
        )
        return 0

    from pyspark import StorageLevel
    from pyspark.sql import SparkSession, functions as functions

    spark = (
        SparkSession.builder.appName(args.application_name)
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", str(args.shuffle_partitions))
        .config("spark.sql.adaptive.enabled", str(args.adaptive_enabled).lower())
        .config("spark.sql.adaptive.skewJoin.enabled", "true")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    temporary = Path(args.commit_root) / (
        f".{args.logical_output_id}.building-{uuid4().hex[:8]}"
    )
    started = time.perf_counter()
    transformed = None
    try:
        frame = spark.read.parquet(*args.input)
        required = {"label", "source_shard", "source_row_index"}
        required.update(f"int_feature_{index}" for index in range(1, 14))
        required.update(f"cat_feature_{index}" for index in range(1, 27))
        missing_columns = sorted(required - set(frame.columns))
        if missing_columns:
            raise S5RuntimeError(
                f"s5_spark_input_schema_missing:{','.join(missing_columns)}"
            )
        semantic_rows = frame.count()
        if semantic_rows != args.semantic_row_count:
            raise S5RuntimeError(
                f"s5_spark_semantic_row_count_mismatch:{semantic_rows}:"
                f"{args.semantic_row_count}"
            )
        if args.repeat_factor > 1:
            frame = frame.withColumn(
                "io_replica",
                functions.explode(
                    functions.sequence(
                        functions.lit(0), functions.lit(args.repeat_factor - 1)
                    )
                ),
            )
        else:
            frame = frame.withColumn("io_replica", functions.lit(0))
        record_key = (
            functions.col("source_shard").cast("long")
            * functions.lit(RECORD_KEY_SHARD_FACTOR)
            + functions.col("source_row_index").cast("long")
            * functions.lit(args.repeat_factor)
            + functions.col("io_replica").cast("long")
        )
        dense_missing = sum(
            (
                functions.when(functions.col(f"int_feature_{index}").isNull(), 1)
                .otherwise(0)
                .cast("short")
                for index in range(1, 14)
            ),
            functions.lit(0).cast("short"),
        )
        categorical_missing = sum(
            (
                functions.when(functions.col(f"cat_feature_{index}").isNull(), 1)
                .otherwise(0)
                .cast("short")
                for index in range(1, 27)
            ),
            functions.lit(0).cast("short"),
        )
        transformed = frame.select(
            record_key.alias("record_key"),
            functions.col("label").cast("byte").alias("label"),
            dense_missing.alias("dense_missing"),
            categorical_missing.alias("categorical_missing"),
        ).withColumns(
            {
                "output_bucket": functions.pmod(
                    functions.col("record_key"), functions.lit(args.output_partitions)
                ).cast("short"),
                "skew_key": functions.when(
                    functions.pmod(functions.col("record_key"), functions.lit(100))
                    < args.skew_fraction_percent,
                    functions.lit(0),
                )
                .otherwise(
                    functions.pmod(functions.col("record_key"), functions.lit(128))
                    + functions.lit(1)
                )
                .cast("short"),
            }
        )
        if args.partition_hold_ms:
            schema = transformed.schema
            hold_seconds = args.partition_hold_ms / 1000.0

            def hold_partition(rows: Iterator[Any]) -> Iterator[Any]:
                time.sleep(hold_seconds)
                yield from rows

            transformed = spark.createDataFrame(
                transformed.rdd.mapPartitions(hold_partition),
                schema=schema,
            )
        transformed = transformed.persist(StorageLevel.MEMORY_AND_DISK)
        expected_rows = semantic_rows * args.repeat_factor
        fingerprint_before = _spark_fingerprint(transformed, functions)
        if int(fingerprint_before["row_count"]) != expected_rows:
            raise S5RuntimeError("s5_spark_transformed_row_count_mismatch")
        skew_counts = [
            int(row["count"])
            for row in transformed.groupBy("skew_key").count().collect()
        ]
        temporary.mkdir(parents=True, exist_ok=False)
        output_data = temporary / "data"
        (
            transformed.repartition(args.output_partitions, "output_bucket")
            .sortWithinPartitions("record_key")
            .write.mode("errorifexists")
            .parquet(str(output_data))
        )
        output = spark.read.parquet(str(output_data))
        fingerprint_after = _spark_fingerprint(output, functions)
        output_rows = int(fingerprint_after["row_count"])
        distinct_rows = output.select("record_key").distinct().count()
        missing_records = max(0, expected_rows - distinct_rows)
        duplicate_records = max(0, output_rows - distinct_rows)
        if fingerprint_before != fingerprint_after:
            raise S5RuntimeError("s5_spark_output_fingerprint_mismatch")
        if missing_records or duplicate_records:
            raise S5RuntimeError(
                f"s5_spark_output_integrity_failed:{missing_records}:{duplicate_records}"
            )
        elapsed = time.perf_counter() - started
        input_bytes = sum(Path(path).stat().st_size for path in args.input)
        result = {
            "engine": args.engine,
            "stage": args.stage,
            "repetition": args.repetition,
            "logical_output_id": args.logical_output_id,
            "application_id": spark.sparkContext.applicationId,
            "spark_version": spark.version,
            "spark_master": spark.sparkContext.master,
            "executor_count": args.executor_count,
            "semantic_row_count": semantic_rows,
            "effective_row_count": expected_rows,
            "repeat_factor": args.repeat_factor,
            "generated_io_only": args.repeat_factor > 1,
            "duration_seconds": elapsed,
            "records_per_second": expected_rows / elapsed,
            "input_bytes": input_bytes,
            "mib_per_second": input_bytes
            * args.repeat_factor
            / (1024 * 1024)
            / elapsed,
            "skew_ratio": _skew_ratio(skew_counts),
            "missing_records": missing_records,
            "duplicate_records": duplicate_records,
            "output_digest": payload_sha256(fingerprint_after),
            "output_fingerprint": fingerprint_after,
            "commit_state": "committed",
            "profile": args.profile,
            "claim_boundary": args.claim_boundary,
        }
        commit = {
            "schema_version": "evm.s5_output_commit.v1",
            "logical_output_id": args.logical_output_id,
            "stage": args.stage,
            "repeat_factor": args.repeat_factor,
            "result": result,
        }
        write_public_json(temporary / "commit-manifest.json", commit)
        _atomic_commit(temporary, final_root, commit)
        write_public_json(
            report_path,
            {
                "schema_version": "evm.s5_spark_job_report.v1",
                "status": "passed",
                "commit_state": "committed",
                "result": result,
            },
        )
        return 0
    except Exception as exc:
        write_public_json(
            report_path,
            {
                "schema_version": "evm.s5_spark_job_report.v1",
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise
    finally:
        if transformed is not None:
            transformed.unpersist(blocking=False)
        spark.stop()
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute the S5 Spark data-scale job.")
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--commit-root", required=True)
    parser.add_argument("--logical-output-id", required=True)
    parser.add_argument("--application-name", required=True)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--stage", choices=("small", "medium", "large"), required=True)
    parser.add_argument("--profile", default="scale")
    parser.add_argument("--repetition", type=int, required=True)
    parser.add_argument("--semantic-row-count", type=int, required=True)
    parser.add_argument("--repeat-factor", type=int, default=1)
    parser.add_argument("--executor-count", type=int, required=True)
    parser.add_argument("--output-partitions", type=int, required=True)
    parser.add_argument("--shuffle-partitions", type=int, required=True)
    parser.add_argument("--skew-fraction-percent", type=int, required=True)
    parser.add_argument("--partition-hold-ms", type=int, default=0)
    adaptive = parser.add_mutually_exclusive_group(required=True)
    adaptive.add_argument("--adaptive-enabled", dest="adaptive_enabled", action="store_true")
    adaptive.add_argument(
        "--no-adaptive-enabled", dest="adaptive_enabled", action="store_false"
    )
    parser.add_argument("--claim-boundary", required=True)
    args = parser.parse_args()
    if args.repetition < 1 or args.semantic_row_count < 1 or args.repeat_factor < 1:
        raise S5RuntimeError("s5_spark_job_numeric_identity_invalid")
    if args.executor_count < 1 or args.output_partitions < 1 or args.shuffle_partitions < 1:
        raise S5RuntimeError("s5_spark_job_execution_bound_invalid")
    return args


def write_public_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def payload_sha256(payload: Any) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _spark_fingerprint(frame: Any, functions: Any) -> dict[str, int]:
    row = frame.agg(
        functions.count(functions.lit(1)).alias("row_count"),
        functions.sum(
            functions.pmod(
                functions.col("record_key"), functions.lit(FINGERPRINT_MODULUS)
            )
        ).alias("record_key_mod_sum"),
        functions.expr("bit_xor(record_key)").alias("record_key_xor"),
        functions.sum(functions.col("label").cast("long")).alias("positive_labels"),
        functions.sum(functions.col("dense_missing").cast("long")).alias(
            "dense_missing_total"
        ),
        functions.sum(functions.col("categorical_missing").cast("long")).alias(
            "categorical_missing_total"
        ),
    ).collect()[0]
    return {name: int(row[name] or 0) for name in row.__fields__}


def _read_commit(root: Path) -> dict[str, Any] | None:
    manifest = root / "commit-manifest.json"
    return json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else None


def _assert_existing(existing: dict[str, Any], args: argparse.Namespace) -> None:
    if (
        existing.get("schema_version") != "evm.s5_output_commit.v1"
        or existing.get("logical_output_id") != args.logical_output_id
        or existing.get("stage") != args.stage
        or int(existing.get("repeat_factor", 0)) != args.repeat_factor
    ):
        raise S5RuntimeError("s5_output_commit_identity_mismatch")


def _atomic_commit(temporary: Path, final_root: Path, commit: dict[str, Any]) -> None:
    final_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.rename(final_root)
    except FileExistsError:
        existing = _read_commit(final_root)
        if existing is None or payload_sha256(existing) != payload_sha256(commit):
            raise S5RuntimeError("s5_output_commit_conflict")


def _skew_ratio(values: list[int]) -> float:
    observed = [value for value in values if value > 0]
    if not observed:
        return 0.0
    median = statistics.median(observed)
    return max(observed) / median if median else math.inf


if __name__ == "__main__":
    raise SystemExit(main())
