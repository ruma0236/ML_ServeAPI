from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel.aggregation import load_model_config, path_from_config
from evm.core.config import load_config
from evm.control_panel.real_test_policy import read_config


def test_control_panel_consumers_accept_run_scoped_json_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    config = tmp_path / "model.runtime.json"
    config.write_text(
        json.dumps(
            {
                "model_matrix": {"matrix_id": "lifecycle-1"},
                "resources": {"artifact_root": str(tmp_path / "artifacts")},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVM_PROJECT_ROOT", str(project_root))

    aggregation_payload = load_model_config(config)
    policy_payload = read_config(config)

    assert aggregation_payload["model_matrix"]["matrix_id"] == "lifecycle-1"
    assert policy_payload["model_matrix"]["matrix_id"] == "lifecycle-1"
    assert policy_payload["_config_path"] == str(config.resolve())


def test_aggregation_maps_container_runtime_paths_before_windows_resolution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    host_root = tmp_path / "evm-data"
    metadata = host_root / "artifacts" / "run-1" / "dataset_version.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text('{"dataset_version":"v1"}\n', encoding="utf-8")
    config_path = tmp_path / "airflow.runtime.json"
    config_path.write_text(
        json.dumps(
            {
                "paths": {"artifacts_root": "/mnt/evm-data/artifacts"},
                "pipelines": {
                    "data_validation": {
                        "dataset_metadata": (
                            "/mnt/evm-data/artifacts/run-1/dataset_version.json"
                        )
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVM_PROJECT_ROOT", str(Path(__file__).resolve().parents[1]))
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", str(host_root))
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")

    config = load_config(config_path)

    assert path_from_config(config, "paths.artifacts_root", "artifacts") == (
        host_root / "artifacts"
    )
    assert path_from_config(
        config,
        "pipelines.data_validation.dataset_metadata",
        "dataset_version.json",
    ) == metadata
