from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from evm.pipelines.lakehouse_probe.run import run


def test_lakehouse_probe_reads_parquet_and_writes_recommendation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='tmp'\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    parquet_path = tmp_path / "validated_dataset.parquet"
    table = pa.table(
        {
            "sample_id": ["a", "b", "c"],
            "label": ["normal", "anomaly", "normal"],
            "label_type": ["normal", "anomaly", "normal"],
            "split": ["train", "validation", "test"],
            "class_name": ["candle", "candle", "capsule"],
        }
    )
    pq.write_table(table, parquet_path)

    out_dir = tmp_path / "lakehouse"
    config = tmp_path / "config.toml"
    config.write_text(
        f"""
[project]
name = "tmp"

[paths]
artifacts_root = "{(tmp_path / "artifacts").as_posix()}"
reports_root = "{(tmp_path / "reports").as_posix()}"

[pipelines.lakehouse_probe]
input_parquet = "{parquet_path.as_posix()}"
output_dir = "{out_dir.as_posix()}"
probe_report = "{(out_dir / "lakehouse_probe.json").as_posix()}"
tradeoff_matrix = "{(out_dir / "engine_tradeoff_matrix.json").as_posix()}"
recommendation_doc = "{(out_dir / "lakehouse_recommendation.md").as_posix()}"
""",
        encoding="utf-8",
    )

    summary = run(str(config))

    assert summary["status"] == "pass"
    assert summary["parquet_summary"]["row_count"] == 3
    assert summary["parquet_summary"]["label_counts"] == {"normal": 2, "anomaly": 1}
    assert summary["engine_availability"]["pyarrow"] is True

    report = json.loads((out_dir / "lakehouse_probe.json").read_text(encoding="utf-8"))
    assert report["recommendation"]["current_prototype"] in {
        "pyarrow_parquet_probe",
        "duckdb_parquet_sql",
    }
    assert (out_dir / "lakehouse_recommendation.md").exists()
