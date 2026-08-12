from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from evm.model_runtime.common import ModelRuntimeError
from evm.model_runtime.workload_runner import (
    ScenarioExecutionConfig,
    mark_stage_failed,
    staging_approval,
    verify_base_model_bundle,
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


def model_config(tmp_path: Path, *, family: str = "vlm") -> ScenarioExecutionConfig:
    revision = "a" * 40
    model_dir = tmp_path / revision
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "model.safetensors").write_bytes(b"model-weights")
    (model_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    if family == "vlm":
        (model_dir / "preprocessor_config.json").write_text("{}", encoding="utf-8")
        (model_dir / "processor_config.json").write_text("{}", encoding="utf-8")
    return ScenarioExecutionConfig(
        **{
            **config(tmp_path).__dict__,
            "model_family": family,
            "model_revision": revision,
            "model_dir": model_dir,
        }
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


@pytest.mark.parametrize("family", ["vlm", "llm"])
def test_base_model_bundle_binds_exact_revision_and_family_files(
    tmp_path: Path, family: str
) -> None:
    verified = verify_base_model_bundle(model_config(tmp_path, family=family))

    assert verified["status"] == "pass"
    assert verified["model_revision"] == "a" * 40
    assert len(verified["files"]["model.safetensors"]["sha256"]) == 64


def test_base_model_bundle_fails_closed_before_runtime_side_effects(tmp_path: Path) -> None:
    invalid = model_config(tmp_path)
    (invalid.model_dir / "preprocessor_config.json").unlink()
    with pytest.raises(ModelRuntimeError, match="base_model_bundle_incomplete"):
        verify_base_model_bundle(invalid)

    mismatched = ScenarioExecutionConfig(
        **{**invalid.__dict__, "model_revision": "b" * 40}
    )
    with pytest.raises(ModelRuntimeError, match="base_model_revision_path_mismatch"):
        verify_base_model_bundle(mismatched)


def test_post_stage_closure_failure_marks_run_terminal(tmp_path: Path, monkeypatch) -> None:
    failure = tmp_path / "failure.json"
    failure.write_text("{}", encoding="utf-8")
    run = SimpleNamespace(
        stages=[SimpleNamespace(stage_id="observability", state="completed")]
    )
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(
        "evm.model_runtime.workload_runner.get_workload_run",
        lambda _run_id: run,
    )
    monkeypatch.setattr(
        "evm.model_runtime.workload_runner.fail_workload_run",
        lambda _run_id, **kwargs: calls.append(kwargs),
    )

    mark_stage_failed("run-1", "observability", failure, RuntimeError("seal failed"))

    assert calls == [
        {
            "actor": "scenario-workload-runtime",
            "blocker": "RuntimeError:seal failed",
            "evidence_uri": str(failure),
        }
    ]
