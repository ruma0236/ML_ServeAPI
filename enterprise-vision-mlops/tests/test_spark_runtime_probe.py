from __future__ import annotations

import json
from pathlib import Path

from evm.pipelines.spark_runtime_probe import run as spark_probe


def test_spark_runtime_probe_uses_real_pipeline_contract_without_claiming_scale(
    tmp_path, monkeypatch
) -> None:
    artifacts = tmp_path / "artifacts"
    config = tmp_path / "control.toml"
    config.write_text(
        "\n".join(
            [
                "[paths]",
                f'artifacts_root = "{artifacts.as_posix()}"',
                f'reports_root = "{(artifacts / "reports").as_posix()}"',
                "",
                "[pipelines.spark-runtime-probe]",
                'master = "local[2]"',
                "row_count = 14",
                "partitions = 2",
                f'probe_report = "{(artifacts / "spark-probe.json").as_posix()}"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVM_OTEL_ENABLED", "false")
    monkeypatch.setenv("EVM_PROJECT_ROOT", str(Path.cwd()))
    monkeypatch.setattr(
        spark_probe,
        "execute_spark_probe",
        lambda **_kwargs: {
            "spark_version": "test-runtime",
            "application_id": "local-control",
            "master": "local[2]",
            "default_parallelism": 2,
            "input_partitions": 2,
            "row_count": 14,
            "bucket_counts": {str(index): 2 for index in range(7)},
        },
    )

    result = spark_probe.run(config)
    persisted = json.loads((artifacts / "spark-probe.json").read_text(encoding="utf-8"))

    assert result["status"] == "pass"
    assert persisted["execution_mode"] == "local_control"
    assert persisted["result"]["row_count"] == 14
    assert "Scenario S5" in persisted["claim_boundary"]
    assert len(persisted["result_sha256"]) == 64
