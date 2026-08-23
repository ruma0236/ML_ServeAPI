from __future__ import annotations

import hashlib
import json
import math
import shutil
import statistics
import time
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote
from uuid import uuid4

import numpy as np
import psutil
import pyarrow as pa
import pyarrow.parquet as pq
import requests

from evm.scale_validation.evidence import write_public_json


CLAIM_BOUNDARY = (
    "Measured Criteo click-log columnar and Spark executor behavior on one local "
    "physical node. No customer traffic, production SLA, physical multi-node or "
    "multi-zone HA, stateful HA/DR, full-terabyte processing, or new "
    "semantic-diversity claim."
)
FINGERPRINT_MODULUS = 2_147_483_647
RECORD_KEY_SHARD_FACTOR = 1_000_000_000_000


class S5RuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceFileSpec:
    path: str
    expected_bytes: int


@dataclass(frozen=True)
class S5RuntimeConfig:
    path: Path
    sha256: str
    dataset_version: str
    seed: int
    dataset_id: str
    source_revision: str
    source_license: str
    source_page: str
    source_repository: str
    source_files: tuple[SourceFileSpec, ...]
    raw_root: Path
    governed_root: Path
    manifest_path: Path
    source_batch_rows: int
    stages: dict[str, int]
    repetitions: int
    single_process_batch_rows: int
    local_threads: int
    executor_counts: tuple[int, ...]
    executor_cores: int
    executor_memory: str
    executor_memory_overhead: str
    driver_memory: str
    shuffle_partitions: int
    output_partitions: int
    adaptive_enabled: bool
    skew_fraction_percent: int
    retry_generated_io_factor: int
    retry_partition_hold_ms: int
    spark_image_repository: str
    namespace: str
    service_account: str
    pvc_name: str
    minimum_free_disk_bytes: int
    maximum_run_seconds: int
    maximum_executor_peak_memory_bytes: int
    maximum_gc_ratio: float
    maximum_skew_ratio: float
    private_root: Path
    event_log_root: Path
    output_root: Path
    claim_boundary: str

    @classmethod
    def from_path(cls, path: Path, *, data_root: Path) -> "S5RuntimeConfig":
        resolved = path.resolve()
        with resolved.open("rb") as handle:
            payload = tomllib.load(handle)
        if payload.get("schema_version") != "evm.s5_spark_data_scale.v1":
            raise S5RuntimeError("s5_runtime_config_schema_invalid")
        source = _section(payload, "source")
        execution = _section(payload, "execution")
        guardrails = _section(payload, "guardrails")
        paths = _section(payload, "paths")
        claim = _section(payload, "claim")
        source_files = tuple(
            SourceFileSpec(path=str(item["path"]), expected_bytes=int(item["expected_bytes"]))
            for item in source.get("files", [])
            if isinstance(item, dict)
        )
        stage_payload = _section(payload, "stages")
        stages = {
            str(name): int(_section(stage_payload, str(name))["shard_count"])
            for name in ("small", "medium", "large")
        }
        config = cls(
            path=resolved,
            sha256=file_sha256(resolved),
            dataset_version=str(payload.get("dataset_version") or ""),
            seed=int(payload.get("seed", 0)),
            dataset_id=str(source.get("dataset_id") or ""),
            source_revision=str(source.get("revision") or ""),
            source_license=str(source.get("license") or ""),
            source_page=str(source.get("source_page") or ""),
            source_repository=str(source.get("repository") or ""),
            source_files=source_files,
            raw_root=data_root / str(source["raw_relative_root"]),
            governed_root=data_root / str(source["governed_relative_root"]),
            manifest_path=data_root / str(source["manifest_relative_path"]),
            source_batch_rows=int(source.get("batch_rows", 65_536)),
            stages=stages,
            repetitions=int(execution.get("repetitions", 0)),
            single_process_batch_rows=int(execution.get("single_process_batch_rows", 0)),
            local_threads=int(execution.get("local_threads", 0)),
            executor_counts=tuple(int(value) for value in execution.get("executor_counts", [])),
            executor_cores=int(execution.get("executor_cores", 0)),
            executor_memory=str(execution.get("executor_memory") or ""),
            executor_memory_overhead=str(execution.get("executor_memory_overhead") or ""),
            driver_memory=str(execution.get("driver_memory") or ""),
            shuffle_partitions=int(execution.get("shuffle_partitions", 0)),
            output_partitions=int(execution.get("output_partitions", 0)),
            adaptive_enabled=bool(execution.get("adaptive_enabled", False)),
            skew_fraction_percent=int(execution.get("skew_fraction_percent", 0)),
            retry_generated_io_factor=int(execution.get("retry_generated_io_factor", 0)),
            retry_partition_hold_ms=int(execution.get("retry_partition_hold_ms", 0)),
            spark_image_repository=str(execution.get("spark_image_repository") or ""),
            namespace=str(execution.get("namespace") or ""),
            service_account=str(execution.get("service_account") or ""),
            pvc_name=str(execution.get("pvc_name") or ""),
            minimum_free_disk_bytes=int(guardrails.get("minimum_free_disk_bytes", 0)),
            maximum_run_seconds=int(guardrails.get("maximum_run_seconds", 0)),
            maximum_executor_peak_memory_bytes=int(
                guardrails.get("maximum_executor_peak_memory_bytes", 0)
            ),
            maximum_gc_ratio=float(guardrails.get("maximum_gc_ratio", 0.0)),
            maximum_skew_ratio=float(guardrails.get("maximum_skew_ratio", 0.0)),
            private_root=data_root / str(paths["private_relative_root"]),
            event_log_root=data_root / str(paths["spark_event_log_relative_root"]),
            output_root=data_root / str(paths["output_relative_root"]),
            claim_boundary=str(claim.get("boundary") or CLAIM_BOUNDARY),
        )
        _validate_config(config)
        return config


def prepare_criteo_dataset(config: S5RuntimeConfig) -> dict[str, Any]:
    if config.manifest_path.is_file():
        manifest = json.loads(config.manifest_path.read_text(encoding="utf-8"))
        validate_dataset_manifest(config, manifest)
        if "source_contract_sha256" not in manifest:
            manifest["source_contract_sha256"] = source_contract_sha256(config)
            manifest["legacy_preparation_config_sha256"] = manifest.pop(
                "config_sha256", ""
            )
            manifest["validated_with_config_sha256"] = config.sha256
            write_public_json(config.manifest_path, manifest)
        return {**manifest, "replayed": True}
    if config.governed_root.exists():
        raise S5RuntimeError("s5_governed_root_exists_without_manifest")
    free_bytes = shutil.disk_usage(nearest_existing_parent(config.raw_root)).free
    if free_bytes < config.minimum_free_disk_bytes:
        raise S5RuntimeError(f"s5_minimum_free_disk_not_met:{free_bytes}")

    config.raw_root.mkdir(parents=True, exist_ok=True)
    temporary = config.governed_root.with_name(
        f"{config.governed_root.name}.building-{uuid4().hex[:8]}"
    )
    temporary.mkdir(parents=True)
    governed_shards: list[dict[str, Any]] = []
    schema_names: list[str] | None = None
    try:
        for shard_id, spec in enumerate(config.source_files):
            raw_path = config.raw_root / Path(spec.path).name
            source_url = (
                f"{config.source_repository}/resolve/{config.source_revision}/"
                f"{quote(spec.path, safe='/=') }"
            )
            _download_immutable_source(
                source_url,
                raw_path,
                expected_bytes=spec.expected_bytes,
            )
            output_path = temporary / f"shard-{shard_id:03d}.parquet"
            result = _govern_source_shard(
                raw_path,
                output_path,
                shard_id=shard_id,
                batch_rows=config.source_batch_rows,
            )
            if schema_names is None:
                schema_names = result["source_schema"]
            elif schema_names != result["source_schema"]:
                raise S5RuntimeError("s5_source_schema_drift")
            governed_shards.append(
                {
                    "shard_id": shard_id,
                    "source_path": spec.path,
                    "source_uri": source_url,
                    "source_bytes": raw_path.stat().st_size,
                    "source_sha256": file_sha256(raw_path),
                    "governed_path": output_path.name,
                    "governed_bytes": output_path.stat().st_size,
                    "governed_sha256": file_sha256(output_path),
                    "row_count": result["row_count"],
                }
            )
        stage_manifests = {}
        for stage, shard_count in config.stages.items():
            selected = governed_shards[:shard_count]
            stage_manifests[stage] = {
                "shard_ids": [item["shard_id"] for item in selected],
                "semantic_row_count": sum(int(item["row_count"]) for item in selected),
                "source_bytes": sum(int(item["source_bytes"]) for item in selected),
                "governed_bytes": sum(int(item["governed_bytes"]) for item in selected),
            }
        manifest = {
            "schema_version": "evm.s5_criteo_dataset_manifest.v1",
            "generated_at": utc_now(),
            "dataset_id": config.dataset_id,
            "dataset_version": config.dataset_version,
            "source_revision": config.source_revision,
            "source_license": config.source_license,
            "source_page": config.source_page,
            "repository": config.source_repository,
            "source_contract_sha256": source_contract_sha256(config),
            "preparation_config_sha256": config.sha256,
            "seed": config.seed,
            "source_schema": schema_names or [],
            "identity_columns": ["source_shard", "source_row_index"],
            "shards": governed_shards,
            "stages": stage_manifests,
            "semantic_diversity_contract": (
                "Only source rows are semantic records. repeat_factor above one is generated "
                "I/O volume and never increases semantic diversity."
            ),
            "claim_boundary": config.claim_boundary,
        }
        write_public_json(temporary / "dataset-manifest.json", manifest)
        config.governed_root.parent.mkdir(parents=True, exist_ok=True)
        temporary.replace(config.governed_root)
        validate_dataset_manifest(config, manifest)
        return {**manifest, "replayed": False}
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def validate_dataset_manifest(config: S5RuntimeConfig, manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != "evm.s5_criteo_dataset_manifest.v1":
        raise S5RuntimeError("s5_dataset_manifest_schema_invalid")
    if manifest.get("dataset_version") != config.dataset_version:
        raise S5RuntimeError("s5_dataset_version_mismatch")
    if manifest.get("source_revision") != config.source_revision:
        raise S5RuntimeError("s5_source_revision_mismatch")
    observed_source_contract = manifest.get("source_contract_sha256")
    if observed_source_contract is not None and (
        observed_source_contract != source_contract_sha256(config)
    ):
        raise S5RuntimeError("s5_dataset_source_contract_mismatch")
    shards = manifest.get("shards")
    if not isinstance(shards, list) or len(shards) != len(config.source_files):
        raise S5RuntimeError("s5_dataset_shard_count_mismatch")
    for expected_id, shard in enumerate(shards):
        if not isinstance(shard, dict) or int(shard.get("shard_id", -1)) != expected_id:
            raise S5RuntimeError("s5_dataset_shard_identity_invalid")
        governed_path = config.governed_root / str(shard.get("governed_path") or "")
        if not governed_path.is_file():
            raise S5RuntimeError("s5_governed_shard_missing")
        if file_sha256(governed_path) != shard.get("governed_sha256"):
            raise S5RuntimeError("s5_governed_shard_digest_mismatch")
        if int(shard.get("row_count", 0)) <= 0:
            raise S5RuntimeError("s5_governed_shard_empty")
        source_spec = config.source_files[expected_id]
        if (
            shard.get("source_path") != source_spec.path
            or int(shard.get("source_bytes", -1)) != source_spec.expected_bytes
        ):
            raise S5RuntimeError("s5_dataset_source_shard_contract_mismatch")


def stage_input_paths(
    config: S5RuntimeConfig,
    manifest: dict[str, Any],
    stage: str,
) -> list[Path]:
    if stage not in config.stages:
        raise S5RuntimeError(f"s5_stage_unknown:{stage}")
    shard_count = config.stages[stage]
    return [
        config.governed_root / str(item["governed_path"])
        for item in manifest["shards"][:shard_count]
    ]


def execute_columnar_control(
    *,
    config: S5RuntimeConfig,
    manifest: dict[str, Any],
    stage: str,
    repetition: int,
    logical_output_id: str,
    repeat_factor: int = 1,
) -> dict[str, Any]:
    inputs = stage_input_paths(config, manifest, stage)
    final_root = config.output_root / logical_output_id
    existing = _read_committed_manifest(final_root)
    if existing is not None:
        _assert_commit_identity(existing, logical_output_id, stage, repeat_factor)
        return {**existing["result"], "commit_state": "replayed"}
    temporary = config.output_root / f".{logical_output_id}.building-{uuid4().hex[:8]}"
    temporary.mkdir(parents=True, exist_ok=False)
    output_path = temporary / "part-00000.parquet"
    writer: pq.ParquetWriter | None = None
    process = psutil.Process()
    peak_rss = process.memory_info().rss
    started = time.perf_counter()
    accumulator = _empty_fingerprint()
    skew_counts: dict[int, int] = {}
    try:
        for path in inputs:
            parquet = pq.ParquetFile(path)
            for batch in parquet.iter_batches(batch_size=config.single_process_batch_rows):
                table = pa.Table.from_batches([batch])
                transformed = _transform_arrow_batch(
                    table,
                    repeat_factor=repeat_factor,
                    output_partitions=config.output_partitions,
                    skew_fraction_percent=config.skew_fraction_percent,
                )
                if writer is None:
                    writer = pq.ParquetWriter(output_path, transformed.schema, compression="snappy")
                writer.write_table(transformed)
                _update_fingerprint(accumulator, transformed)
                keys, counts = np.unique(
                    transformed["skew_key"].to_numpy(zero_copy_only=False),
                    return_counts=True,
                )
                for key, count in zip(keys, counts, strict=True):
                    skew_counts[int(key)] = skew_counts.get(int(key), 0) + int(count)
                peak_rss = max(peak_rss, process.memory_info().rss)
        if writer is None:
            raise S5RuntimeError("s5_columnar_input_empty")
        writer.close()
        writer = None
        validation = _validate_columnar_output(output_path)
        if validation["fingerprint"] != _finalize_fingerprint(accumulator):
            raise S5RuntimeError("s5_columnar_output_fingerprint_mismatch")
        elapsed = time.perf_counter() - started
        semantic_rows = int(manifest["stages"][stage]["semantic_row_count"])
        expected_rows = semantic_rows * repeat_factor
        if validation["row_count"] != expected_rows:
            raise S5RuntimeError("s5_columnar_output_row_count_mismatch")
        result = {
            "engine": "single_process_columnar",
            "stage": stage,
            "repetition": repetition,
            "logical_output_id": logical_output_id,
            "semantic_row_count": semantic_rows,
            "effective_row_count": expected_rows,
            "repeat_factor": repeat_factor,
            "generated_io_only": repeat_factor > 1,
            "duration_seconds": elapsed,
            "records_per_second": expected_rows / elapsed,
            "input_bytes": sum(path.stat().st_size for path in inputs),
            "mib_per_second": (
                sum(path.stat().st_size for path in inputs) * repeat_factor
                / (1024 * 1024)
                / elapsed
            ),
            "peak_executor_memory_bytes": peak_rss,
            "gc_time_ms": 0,
            "executor_run_time_ms": elapsed * 1000,
            "shuffle_read_bytes": 0,
            "shuffle_write_bytes": 0,
            "memory_spill_bytes": 0,
            "disk_spill_bytes": 0,
            "skew_ratio": _skew_ratio(skew_counts.values()),
            "missing_records": 0,
            "duplicate_records": 0,
            "output_digest": payload_sha256(validation["fingerprint"]),
            "output_fingerprint": validation["fingerprint"],
            "commit_state": "committed",
            "claim_boundary": config.claim_boundary,
        }
        commit = {
            "schema_version": "evm.s5_output_commit.v1",
            "logical_output_id": logical_output_id,
            "stage": stage,
            "repeat_factor": repeat_factor,
            "result": result,
        }
        write_public_json(temporary / "commit-manifest.json", commit)
        _atomic_commit(temporary, final_root, commit)
        return result
    finally:
        if writer is not None:
            writer.close()
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def parse_spark_event_log(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise S5RuntimeError(f"s5_spark_event_log_missing:{path}")
    task_count = 0
    failed_task_count = 0
    executor_run_ms = 0
    gc_ms = 0
    shuffle_read = 0
    shuffle_write = 0
    memory_spill = 0
    disk_spill = 0
    peak_execution_memory = 0
    peak_executor_metrics = 0
    executors_added: set[str] = set()
    executors_removed: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        event_name = str(event.get("Event") or "")
        if event_name.endswith("SparkListenerExecutorAdded"):
            executors_added.add(str(event.get("Executor ID") or ""))
        elif event_name.endswith("SparkListenerExecutorRemoved"):
            executors_removed.add(str(event.get("Executor ID") or ""))
        elif event_name.endswith("SparkListenerExecutorMetricsUpdate"):
            for update in event.get("Executor Updates", []):
                if not isinstance(update, dict):
                    continue
                metrics = update.get("Executor Metrics") or {}
                if isinstance(metrics, dict):
                    observed = sum(
                        int(metrics.get(name, 0) or 0)
                        for name in (
                            "JVMHeapMemory",
                            "JVMOffHeapMemory",
                            "ProcessTreeJVMRSSMemory",
                            "ProcessTreePythonRSSMemory",
                            "ProcessTreeOtherRSSMemory",
                        )
                    )
                    peak_executor_metrics = max(peak_executor_metrics, observed)
        elif event_name.endswith("SparkListenerTaskEnd"):
            task_count += 1
            reason = event.get("Task End Reason") or {}
            reason_name = reason.get("Reason") if isinstance(reason, dict) else str(reason)
            if reason_name not in {"Success", None, ""}:
                failed_task_count += 1
            metrics = event.get("Task Metrics") or {}
            executor_run_ms += int(metrics.get("Executor Run Time", 0) or 0)
            gc_ms += int(metrics.get("JVM GC Time", 0) or 0)
            memory_spill += int(metrics.get("Memory Bytes Spilled", 0) or 0)
            disk_spill += int(metrics.get("Disk Bytes Spilled", 0) or 0)
            peak_execution_memory = max(
                peak_execution_memory,
                int(metrics.get("Peak Execution Memory", 0) or 0),
            )
            shuffle_read_metrics = metrics.get("Shuffle Read Metrics") or {}
            shuffle_read += sum(
                int(shuffle_read_metrics.get(name, 0) or 0)
                for name in ("Remote Bytes Read", "Local Bytes Read")
            )
            shuffle_write_metrics = metrics.get("Shuffle Write Metrics") or {}
            shuffle_write += int(shuffle_write_metrics.get("Shuffle Bytes Written", 0) or 0)
    peak_memory = max(peak_execution_memory, peak_executor_metrics)
    return {
        "task_count": task_count,
        "failed_task_count": failed_task_count,
        "executor_run_time_ms": executor_run_ms,
        "gc_time_ms": gc_ms,
        "gc_ratio": gc_ms / executor_run_ms if executor_run_ms else 0.0,
        "shuffle_read_bytes": shuffle_read,
        "shuffle_write_bytes": shuffle_write,
        "memory_spill_bytes": memory_spill,
        "disk_spill_bytes": disk_spill,
        "peak_executor_memory_bytes": peak_memory,
        "executors_added": len({value for value in executors_added if value}),
        "executors_removed": len({value for value in executors_removed if value}),
    }


def analyze_s5_results(results: list[dict[str, Any]], config: S5RuntimeConfig) -> dict[str, Any]:
    if not results:
        raise S5RuntimeError("s5_results_empty")
    metric_names = (
        "records_per_second",
        "mib_per_second",
        "peak_executor_memory_bytes",
        "gc_time_ms",
        "shuffle_read_bytes",
        "shuffle_write_bytes",
        "memory_spill_bytes",
        "disk_spill_bytes",
        "skew_ratio",
    )
    metrics_complete = all(
        all(_finite_nonnegative(result.get(name)) for name in metric_names) for result in results
    )
    required_modes = {
        "single_process_columnar",
        "spark_local",
        "spark_kubernetes_1",
        "spark_kubernetes_2",
        "spark_kubernetes_4",
    }
    observed_modes = {str(result.get("engine")) for result in results}
    integrity_passed = all(
        int(result.get("missing_records", -1)) == 0
        and int(result.get("duplicate_records", -1)) == 0
        and result.get("commit_state") in {"committed", "replayed"}
        for result in results
    )
    retry = [result for result in results if result.get("profile") == "executor_loss_retry"]
    retry_passed = bool(retry) and all(
        result.get("executor_kill_observed") is True
        and int(result.get("executors_removed", 0)) >= 1
        and result.get("retry_output_digest") == result.get("output_digest")
        and int(result.get("retry_row_count", -1)) == int(result.get("effective_row_count", -2))
        and result.get("retry_commit_state") == "replayed"
        for result in retry
    )
    generated_volume_truthful = all(
        (
            int(result.get("repeat_factor", 1)) == 1
            and result.get("generated_io_only") is False
            and int(result.get("semantic_row_count", 0))
            == int(result.get("effective_row_count", -1))
        )
        or (
            int(result.get("repeat_factor", 1)) > 1
            and result.get("generated_io_only") is True
            and int(result.get("effective_row_count", 0))
            == int(result.get("semantic_row_count", -1))
            * int(result.get("repeat_factor", 0))
        )
        for result in results
    )
    bounds_passed = all(
        float(result.get("duration_seconds", math.inf)) <= config.maximum_run_seconds
        and int(result.get("peak_executor_memory_bytes", 0))
        <= config.maximum_executor_peak_memory_bytes
        and float(result.get("gc_ratio", 0.0)) <= config.maximum_gc_ratio
        and float(result.get("skew_ratio", 0.0)) <= config.maximum_skew_ratio
        for result in results
    )
    acceptance = {
        "S5-AC-01": {
            "status": "passed" if metrics_complete and required_modes <= observed_modes and bounds_passed else "failed",
            "metrics_complete": metrics_complete,
            "required_modes_present": sorted(required_modes & observed_modes),
            "bounds_passed": bounds_passed,
        },
        "S5-AC-02": {"status": "passed" if integrity_passed else "failed"},
        "S5-AC-03": {"status": "passed" if retry_passed else "failed"},
        "S5-AC-04": {"status": "passed" if generated_volume_truthful else "failed"},
    }
    summaries: dict[str, Any] = {}
    for engine in sorted(observed_modes):
        selected = [item for item in results if item.get("engine") == engine]
        summaries[engine] = {
            "runs": len(selected),
            "mean_records_per_second": statistics.fmean(
                float(item["records_per_second"]) for item in selected
            ),
            "max_peak_executor_memory_bytes": max(
                int(item["peak_executor_memory_bytes"]) for item in selected
            ),
            "total_shuffle_bytes": sum(
                int(item["shuffle_read_bytes"]) + int(item["shuffle_write_bytes"])
                for item in selected
            ),
            "total_spill_bytes": sum(
                int(item["memory_spill_bytes"]) + int(item["disk_spill_bytes"])
                for item in selected
            ),
        }
    return {
        "status": "passed"
        if all(item["status"] == "passed" for item in acceptance.values())
        else "failed",
        "acceptance": acceptance,
        "engine_summaries": summaries,
    }


def private_evidence_index(root: Path) -> dict[str, Any]:
    entries = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name == "private-evidence-index.json":
            continue
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )
    return {
        "schema_version": "evm.s5_private_evidence_index.v1",
        "artifact_count": len(entries),
        "total_bytes": sum(int(item["bytes"]) for item in entries),
        "entries": entries,
    }


def payload_sha256(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def source_contract_sha256(config: S5RuntimeConfig) -> str:
    return payload_sha256(
        {
            "dataset_id": config.dataset_id,
            "dataset_version": config.dataset_version,
            "source_revision": config.source_revision,
            "source_license": config.source_license,
            "source_repository": config.source_repository,
            "source_batch_rows": config.source_batch_rows,
            "files": [
                {"path": item.path, "expected_bytes": item.expected_bytes}
                for item in config.source_files
            ],
            "stages": config.stages,
        }
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def nearest_existing_parent(path: Path) -> Path:
    candidate = path.resolve()
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            raise S5RuntimeError(f"s5_no_existing_storage_parent:{path}")
        candidate = parent
    return candidate


def _validate_config(config: S5RuntimeConfig) -> None:
    if not config.dataset_version or not config.dataset_id:
        raise S5RuntimeError("s5_dataset_identity_missing")
    if len(config.source_revision) != 40 or any(
        value not in "0123456789abcdef" for value in config.source_revision
    ):
        raise S5RuntimeError("s5_source_revision_invalid")
    if len(config.source_files) < 4:
        raise S5RuntimeError("s5_source_file_contract_too_small")
    if config.stages != {"small": 1, "medium": 2, "large": 4}:
        raise S5RuntimeError("s5_stage_contract_invalid")
    if config.repetitions != 3 or config.executor_counts != (1, 2, 4):
        raise S5RuntimeError("s5_repetition_or_executor_contract_invalid")
    positive = (
        config.source_batch_rows,
        config.single_process_batch_rows,
        config.local_threads,
        config.executor_cores,
        config.shuffle_partitions,
        config.output_partitions,
        config.retry_generated_io_factor,
        config.minimum_free_disk_bytes,
        config.maximum_run_seconds,
        config.maximum_executor_peak_memory_bytes,
    )
    if min(positive) <= 0:
        raise S5RuntimeError("s5_numeric_bound_invalid")
    if not 0 < config.skew_fraction_percent < 100:
        raise S5RuntimeError("s5_skew_fraction_invalid")
    if not 0 < config.maximum_gc_ratio < 1:
        raise S5RuntimeError("s5_gc_ratio_invalid")
    if config.manifest_path != config.governed_root / "dataset-manifest.json":
        raise S5RuntimeError("s5_manifest_root_mismatch")


def _section(payload: dict[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise S5RuntimeError(f"s5_config_section_missing:{name}")
    return value


def _download_immutable_source(url: str, path: Path, *, expected_bytes: int) -> None:
    if path.is_file():
        if path.stat().st_size != expected_bytes:
            raise S5RuntimeError(f"s5_existing_source_size_mismatch:{path.name}")
        return
    temporary = path.with_name(f".{path.name}.download-{uuid4().hex[:8]}")
    try:
        with requests.get(url, stream=True, timeout=(15, 120)) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        if temporary.stat().st_size != expected_bytes:
            raise S5RuntimeError(
                f"s5_download_size_mismatch:{path.name}:{temporary.stat().st_size}"
            )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _govern_source_shard(
    source_path: Path,
    output_path: Path,
    *,
    shard_id: int,
    batch_rows: int,
) -> dict[str, Any]:
    parquet = pq.ParquetFile(source_path)
    names = parquet.schema_arrow.names
    _validate_criteo_schema(names)
    writer: pq.ParquetWriter | None = None
    row_offset = 0
    try:
        for batch in parquet.iter_batches(batch_size=batch_rows):
            table = pa.Table.from_batches([batch])
            table = table.rename_columns(
                [
                    name.replace("integer_feature_", "int_feature_").replace(
                        "categorical_feature_", "cat_feature_"
                    )
                    for name in table.column_names
                ]
            )
            table = table.append_column(
                "source_shard",
                pa.array(np.full(table.num_rows, shard_id, dtype=np.int16)),
            )
            table = table.append_column(
                "source_row_index",
                pa.array(np.arange(row_offset, row_offset + table.num_rows, dtype=np.int64)),
            )
            if writer is None:
                writer = pq.ParquetWriter(output_path, table.schema, compression="snappy")
            writer.write_table(table)
            row_offset += table.num_rows
    finally:
        if writer is not None:
            writer.close()
    if row_offset <= 0:
        raise S5RuntimeError("s5_source_shard_empty")
    return {"row_count": row_offset, "source_schema": names}


def _validate_criteo_schema(names: list[str]) -> None:
    required = {"label"}
    required.update(f"integer_feature_{index}" for index in range(1, 14))
    required.update(f"categorical_feature_{index}" for index in range(1, 27))
    missing = sorted(required - set(names))
    if missing:
        raise S5RuntimeError(f"s5_source_schema_missing:{','.join(missing)}")
    if len(names) != 40:
        raise S5RuntimeError(f"s5_source_schema_column_count_invalid:{len(names)}")


def _transform_arrow_batch(
    table: pa.Table,
    *,
    repeat_factor: int,
    output_partitions: int,
    skew_fraction_percent: int,
) -> pa.Table:
    if repeat_factor < 1:
        raise S5RuntimeError("s5_repeat_factor_invalid")
    shard = table["source_shard"].to_numpy(zero_copy_only=False).astype(np.int64)
    row_index = table["source_row_index"].to_numpy(zero_copy_only=False).astype(np.int64)
    base = shard * RECORD_KEY_SHARD_FACTOR + row_index * repeat_factor
    record_keys = np.repeat(base, repeat_factor) + np.tile(
        np.arange(repeat_factor, dtype=np.int64), table.num_rows
    )
    labels = np.repeat(
        table["label"].to_numpy(zero_copy_only=False).astype(np.int8), repeat_factor
    )
    dense_missing = np.zeros(table.num_rows, dtype=np.int16)
    for index in range(1, 14):
        dense_missing += table[f"int_feature_{index}"].is_null().to_numpy().astype(np.int16)
    categorical_missing = np.zeros(table.num_rows, dtype=np.int16)
    for index in range(1, 27):
        categorical_missing += (
            table[f"cat_feature_{index}"].is_null().to_numpy().astype(np.int16)
        )
    dense_missing = np.repeat(dense_missing, repeat_factor)
    categorical_missing = np.repeat(categorical_missing, repeat_factor)
    mod100 = record_keys % 100
    skew_key = np.where(
        mod100 < skew_fraction_percent,
        0,
        (record_keys % 128) + 1,
    ).astype(np.int16)
    return pa.table(
        {
            "record_key": record_keys,
            "label": labels,
            "dense_missing": dense_missing,
            "categorical_missing": categorical_missing,
            "output_bucket": (record_keys % output_partitions).astype(np.int16),
            "skew_key": skew_key,
        }
    )


def _empty_fingerprint() -> dict[str, int]:
    return {
        "row_count": 0,
        "record_key_mod_sum": 0,
        "record_key_xor": 0,
        "positive_labels": 0,
        "dense_missing_total": 0,
        "categorical_missing_total": 0,
    }


def _update_fingerprint(accumulator: dict[str, int], table: pa.Table) -> None:
    keys = table["record_key"].to_numpy(zero_copy_only=False).astype(np.int64)
    accumulator["row_count"] += table.num_rows
    accumulator["record_key_mod_sum"] += int(np.sum(keys % FINGERPRINT_MODULUS))
    if keys.size:
        accumulator["record_key_xor"] ^= int(np.bitwise_xor.reduce(keys))
    accumulator["positive_labels"] += int(
        np.sum(table["label"].to_numpy(zero_copy_only=False))
    )
    accumulator["dense_missing_total"] += int(
        np.sum(table["dense_missing"].to_numpy(zero_copy_only=False))
    )
    accumulator["categorical_missing_total"] += int(
        np.sum(table["categorical_missing"].to_numpy(zero_copy_only=False))
    )


def _finalize_fingerprint(accumulator: dict[str, int]) -> dict[str, int]:
    return {name: int(value) for name, value in accumulator.items()}


def _validate_columnar_output(path: Path) -> dict[str, Any]:
    accumulator = _empty_fingerprint()
    previous_key: int | None = None
    duplicate_records = 0
    for batch in pq.ParquetFile(path).iter_batches(batch_size=65_536):
        table = pa.Table.from_batches([batch])
        keys = table["record_key"].to_numpy(zero_copy_only=False).astype(np.int64)
        if keys.size:
            if previous_key is not None and int(keys[0]) <= previous_key:
                raise S5RuntimeError("s5_columnar_output_order_invalid")
            differences = np.diff(keys)
            duplicate_records += int(np.sum(differences == 0))
            if np.any(differences < 1):
                raise S5RuntimeError("s5_columnar_output_identity_invalid")
            previous_key = int(keys[-1])
        _update_fingerprint(accumulator, table)
    return {
        "row_count": accumulator["row_count"],
        "duplicate_records": duplicate_records,
        "fingerprint": _finalize_fingerprint(accumulator),
    }


def _read_committed_manifest(root: Path) -> dict[str, Any] | None:
    path = root / "commit-manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _assert_commit_identity(
    manifest: dict[str, Any],
    logical_output_id: str,
    stage: str,
    repeat_factor: int,
) -> None:
    if (
        manifest.get("schema_version") != "evm.s5_output_commit.v1"
        or manifest.get("logical_output_id") != logical_output_id
        or manifest.get("stage") != stage
        or int(manifest.get("repeat_factor", 0)) != repeat_factor
    ):
        raise S5RuntimeError("s5_output_commit_identity_mismatch")


def _atomic_commit(temporary: Path, final_root: Path, commit: dict[str, Any]) -> None:
    final_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.rename(final_root)
    except FileExistsError:
        existing = _read_committed_manifest(final_root)
        if existing is None or payload_sha256(existing) != payload_sha256(commit):
            raise S5RuntimeError("s5_output_commit_conflict")


def _skew_ratio(values: Iterable[int]) -> float:
    observed = [int(value) for value in values if int(value) > 0]
    if not observed:
        return 0.0
    median = statistics.median(observed)
    return max(observed) / median if median else math.inf


def _finite_nonnegative(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number >= 0
