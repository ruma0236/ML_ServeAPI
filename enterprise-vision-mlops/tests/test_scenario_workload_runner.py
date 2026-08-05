from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from evm.model_runtime.workload_runner import (
    ScenarioExecutionConfig,
    staging_approval,
    write_prometheus_target,
)


def config(tmp_path: Path) -> ScenarioExecutionConfig:
    return ScenarioExecutionConfig(
        scenario_id="scienceqa-vlm-evaluation",
        model_family="vlm",
        model_repository="example/model",
        model_revision="a" * 40,
        model_dir=tmp_path,
        data_view_uri=str(tmp_path / "view.json"),
        source_commit="b" * 40,
        source_branch="test",
        actor="ml-platform",
        reason="Run a controlled local lifecycle validation",
        staging_approver="release-operator",
        staging_reason="Approve the exact adapter for local staging validation only",
        serving_port=30920,
        max_steps=8,
    )


def test_staging_approval_binds_run_identity_artifact_and_source(tmp_path: Path) -> None:
    run = SimpleNamespace(
        run_id="run-1",
        identity=SimpleNamespace(identity_sha256="c" * 64),
    )
    first = staging_approval(config(tmp_path), run, "d" * 64)
    second = staging_approval(config(tmp_path), run, "d" * 64)
    assert first["action_digest"] == second["action_digest"]
    assert first["production_promotion"] is False
    assert first["target_environment"] == "local-staging"


def test_prometheus_target_is_run_scoped(tmp_path: Path) -> None:
    path = tmp_path / "targets.json"
    run = SimpleNamespace(run_id="run-1")
    write_prometheus_target(path, config(tmp_path), run)
    payload = path.read_text(encoding="utf-8")
    assert "host.docker.internal:30920" in payload
    assert '"evm_run_id": "run-1"' in payload
