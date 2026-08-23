from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from scripts.dev import run_s5_spark_data_scale_experiment as s5_runner
from evm.scale_validation.s5_runtime import (
    S5RuntimeConfig,
    analyze_s5_results,
    execute_columnar_control,
    file_sha256,
    nearest_existing_parent,
    parse_spark_event_log,
    source_contract_sha256,
)


def test_s5_retry_replay_keeps_generated_io_identity() -> None:
    config = S5RuntimeConfig.from_path(CONFIG_PATH, data_root=Path("unused"))

    injected = s5_runner._kubernetes_run_identity(
        config, inject_executor_loss=True, replay_only=False
    )
    replay = s5_runner._kubernetes_run_identity(
        config, inject_executor_loss=False, replay_only=True
    )

    assert injected == (True, config.retry_generated_io_factor, config.retry_partition_hold_ms)
    assert replay == (True, config.retry_generated_io_factor, 0)


CONFIG_PATH = Path("configs/s5_spark_data_scale.toml")


def test_s5_config_freezes_stage_executor_and_repeat_contract(tmp_path: Path) -> None:
    config = S5RuntimeConfig.from_path(CONFIG_PATH, data_root=tmp_path)

    assert config.stages == {"small": 1, "medium": 2, "large": 4}
    assert config.executor_counts == (1, 2, 4)
    assert config.repetitions == 3
    assert config.retry_generated_io_factor == 4
    assert config.maximum_skew_ratio == 400.0
    assert "full-terabyte" in config.claim_boundary
    assert nearest_existing_parent(tmp_path / "not-yet" / "nested") == tmp_path
    assert len(source_contract_sha256(config)) == 64


def test_s5_columnar_control_commits_and_replays_exact_output(tmp_path: Path) -> None:
    config = S5RuntimeConfig.from_path(CONFIG_PATH, data_root=tmp_path)
    manifest = _write_governed_fixture(config)

    first = execute_columnar_control(
        config=config,
        manifest=manifest,
        stage="small",
        repetition=1,
        logical_output_id="unit-small-1",
    )
    replay = execute_columnar_control(
        config=config,
        manifest=manifest,
        stage="small",
        repetition=1,
        logical_output_id="unit-small-1",
    )

    assert first["semantic_row_count"] == 4
    assert first["effective_row_count"] == 4
    assert first["missing_records"] == 0
    assert first["duplicate_records"] == 0
    assert first["commit_state"] == "committed"
    assert replay["commit_state"] == "replayed"
    assert replay["output_digest"] == first["output_digest"]


def test_s5_event_log_projects_gc_shuffle_spill_and_executor_loss(tmp_path: Path) -> None:
    path = tmp_path / "event-log.json"
    events = [
        {"Event": "SparkListenerExecutorAdded", "Executor ID": "1"},
        {"Event": "SparkListenerExecutorRemoved", "Executor ID": "1"},
        {
            "Event": "SparkListenerTaskEnd",
            "Task End Reason": {"Reason": "Success"},
            "Task Metrics": {
                "Executor Run Time": 100,
                "JVM GC Time": 5,
                "Memory Bytes Spilled": 11,
                "Disk Bytes Spilled": 13,
                "Peak Execution Memory": 17,
                "Shuffle Read Metrics": {
                    "Remote Bytes Read": 19,
                    "Local Bytes Read": 23,
                },
                "Shuffle Write Metrics": {"Shuffle Bytes Written": 29},
            },
        },
    ]
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
        newline="\n",
    )

    result = parse_spark_event_log(path)

    assert result["gc_time_ms"] == 5
    assert result["shuffle_read_bytes"] == 42
    assert result["shuffle_write_bytes"] == 29
    assert result["memory_spill_bytes"] == 11
    assert result["disk_spill_bytes"] == 13
    assert result["executors_removed"] == 1


def test_s5_acceptance_is_recomputed_and_fails_closed(tmp_path: Path) -> None:
    config = S5RuntimeConfig.from_path(CONFIG_PATH, data_root=tmp_path)
    engines = (
        "single_process_columnar",
        "spark_local",
        "spark_kubernetes_1",
        "spark_kubernetes_2",
        "spark_kubernetes_4",
    )
    results = [_accepted_result(engine) for engine in engines]
    retry = _accepted_result("spark_kubernetes_4")
    retry.update(
        {
            "profile": "executor_loss_retry",
            "executor_kill_observed": True,
            "executors_removed": 1,
            "retry_output_digest": retry["output_digest"],
            "retry_row_count": retry["effective_row_count"],
            "retry_commit_state": "replayed",
        }
    )
    results.append(retry)

    accepted = analyze_s5_results(results, config)
    results[0]["duplicate_records"] = 1
    rejected = analyze_s5_results(results, config)

    assert accepted["status"] == "passed"
    assert all(
        criterion["status"] == "passed"
        for criterion in accepted["acceptance"].values()
    )
    assert rejected["status"] == "failed"
    assert rejected["acceptance"]["S5-AC-02"]["status"] == "failed"


def test_s5_spark_image_and_rbac_use_existing_pipeline_namespace() -> None:
    dockerfile = Path("infra/docker/spark/Dockerfile").read_text(encoding="utf-8")
    rbac = Path("infra/kubernetes/s5/spark-rbac.yaml").read_text(encoding="utf-8")

    assert "apache/spark:3.5.5-python3" in dockerfile
    assert "PYTHONPATH=/opt/evm/src" in dockerfile
    assert "pip install" not in dockerfile
    assert "namespace: evm-pipelines" in rbac
    assert "name: evm-spark" in rbac


def _write_governed_fixture(config: S5RuntimeConfig) -> dict[str, object]:
    config.governed_root.mkdir(parents=True)
    shards = []
    for shard_id in range(4):
        payload: dict[str, pa.Array] = {
            "label": pa.array([0, 1, 0, 1], type=pa.int8()),
        }
        for index in range(1, 14):
            payload[f"int_feature_{index}"] = pa.array(
                [index, None, index + 1, index + 2], type=pa.int64()
            )
        for index in range(1, 27):
            payload[f"cat_feature_{index}"] = pa.array(
                [f"a{index}", None, f"b{index}", f"c{index}"]
            )
        payload["source_shard"] = pa.array([shard_id] * 4, type=pa.int16())
        payload["source_row_index"] = pa.array(range(4), type=pa.int64())
        path = config.governed_root / f"shard-{shard_id:03d}.parquet"
        pq.write_table(pa.table(payload), path, compression="snappy")
        shards.append(
            {
                "shard_id": shard_id,
                "governed_path": path.name,
                "governed_sha256": file_sha256(path),
                "row_count": 4,
                "source_bytes": path.stat().st_size,
                "governed_bytes": path.stat().st_size,
            }
        )
    return {
        "schema_version": "evm.s5_criteo_dataset_manifest.v1",
        "dataset_version": config.dataset_version,
        "source_revision": config.source_revision,
        "source_contract_sha256": source_contract_sha256(config),
        "shards": shards,
        "stages": {
            "small": {"semantic_row_count": 4},
            "medium": {"semantic_row_count": 8},
            "large": {"semantic_row_count": 16},
        },
    }


def _accepted_result(engine: str) -> dict[str, object]:
    return {
        "engine": engine,
        "semantic_row_count": 10,
        "effective_row_count": 10,
        "repeat_factor": 1,
        "generated_io_only": False,
        "duration_seconds": 1.0,
        "records_per_second": 10.0,
        "mib_per_second": 1.0,
        "peak_executor_memory_bytes": 100,
        "gc_time_ms": 1,
        "gc_ratio": 0.01,
        "shuffle_read_bytes": 10,
        "shuffle_write_bytes": 10,
        "memory_spill_bytes": 0,
        "disk_spill_bytes": 0,
        "skew_ratio": 2.0,
        "missing_records": 0,
        "duplicate_records": 0,
        "output_digest": "a" * 64,
        "commit_state": "committed",
    }
