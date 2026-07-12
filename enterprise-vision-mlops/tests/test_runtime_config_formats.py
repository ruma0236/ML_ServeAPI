from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel.aggregation import load_model_config
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
