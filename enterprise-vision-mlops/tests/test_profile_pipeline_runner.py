from __future__ import annotations

import json
from pathlib import Path

from scripts.run_profile_pipeline import (
    MODEL_PIPELINES,
    execution_scope,
    pipeline_stage_scope,
    should_skip_pipeline,
)


def test_profile_runner_reads_scope_and_identifies_model_only_stages(tmp_path: Path) -> None:
    config = tmp_path / "profile.json"
    config.write_text(
        json.dumps({"control_plane": {"execution_scope": "data_cycle"}}),
        encoding="utf-8",
    )

    assert execution_scope(config) == "data_cycle"
    assert {"train", "register-model", "model-lifecycle", "deploy-check", "monitor-check"} == MODEL_PIPELINES


def test_profile_runner_defaults_legacy_configs_to_full_lifecycle(tmp_path: Path) -> None:
    config = tmp_path / "legacy.json"
    config.write_text(json.dumps({"project": {"name": "evm"}}), encoding="utf-8")

    assert execution_scope(config) == "full_lifecycle"
    assert pipeline_stage_scope(config) == "all"


def test_lifecycle_airflow_scope_skips_model_stages_only(tmp_path: Path) -> None:
    config = tmp_path / "lifecycle.json"
    config.write_text(
        json.dumps(
            {
                "control_plane": {
                    "execution_scope": "full_lifecycle",
                    "pipeline_stage_scope": "data",
                }
            }
        ),
        encoding="utf-8",
    )

    assert pipeline_stage_scope(config) == "data"
    assert should_skip_pipeline(config, "train") is True
    assert should_skip_pipeline(config, "vlm-batch-eval") is True
    assert should_skip_pipeline(config, "image-quality") is False
    assert should_skip_pipeline(config, "spark-runtime-probe") is False
    assert should_skip_pipeline(config, "spark-data-scale") is False
