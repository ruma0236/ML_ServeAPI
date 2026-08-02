from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel.pipeline_profiles import (
    PipelineProfileLaunchRequest,
    default_profile,
    launch_profile,
    read_profiles,
    save_profile,
    validate_profile,
    validate_profile_replay,
)


def configure_roots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVM_PIPELINE_PROFILE_ROOT", str(tmp_path / "profiles"))
    monkeypatch.setenv("EVM_PIPELINE_PROFILE_RUNTIME_ROOT", "/mnt/evm-data/test-profiles")
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path / "operations"))
    monkeypatch.setenv(
        "EVM_PROJECT_ROOT",
        str(Path(__file__).resolve().parents[1]),
    )


def profile_with_evidence(
    tmp_path: Path,
    *,
    execution_scope: str = "full_lifecycle",
):
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    source_manifest = data_root / "manifest.jsonl"
    source_manifest.write_text('{"sample_id":"sample-1"}\n', encoding="utf-8")
    split_manifest = data_root / "shard_index.json"
    split_identity = "a" * 64
    split_manifest.write_text(
        json.dumps(
            {
                "schema_version": "evm.dataset_shards.v1",
                "identity_sha256": split_identity,
                "split_seed": 20260706,
                "split_ratios": {"train": 0.6, "validation": 0.2, "test": 0.2},
            }
        ),
        encoding="utf-8",
    )
    profile = default_profile()
    data = profile.data.model_copy(
        update={
            "source_manifest_uri": str(source_manifest),
            "split_manifest_uri": str(split_manifest),
            "split_manifest_sha256": split_identity,
        }
    )
    return profile.model_copy(update={"execution_scope": execution_scope, "data": data})


def test_default_full_lifecycle_is_executable_through_lifecycle_orchestrator(tmp_path: Path) -> None:
    validation = validate_profile(profile_with_evidence(tmp_path))

    assert validation.valid is True
    assert validation.executable is True
    assert validation.status == "ready"
    assert validation.blockers == []
    capability = next(
        item for item in validation.capabilities if item.capability_id == "full_lifecycle_orchestrator"
    )
    assert capability.status == "wired"


def test_data_cycle_profile_and_cross_validation_are_executable(tmp_path: Path) -> None:
    data_profile = profile_with_evidence(tmp_path, execution_scope="data_cycle")
    validation = validate_profile(data_profile)
    assert validation.valid is True
    assert validation.executable is True

    split = data_profile.data.split.model_copy(update={"cross_validation_enabled": True})
    data = data_profile.data.model_copy(update={"split": split})
    cv_profile = data_profile.model_copy(update={"data": data})
    cv_validation = validate_profile(cv_profile)
    assert cv_validation.valid is True
    assert cv_validation.executable is True
    capability = next(
        item
        for item in cv_validation.capabilities
        if item.capability_id == "cross_validation_executor"
    )
    assert capability.status == "wired"
    assert capability.active is True


def test_automated_search_rejects_unbounded_parallelism(tmp_path: Path) -> None:
    profile = profile_with_evidence(tmp_path)
    model = profile.model.model_copy(update={"tuning_mode": "grid", "max_trials": 3})
    resources = profile.resources.model_copy(update={"gpu_count": 1, "max_parallel_trials": 2})

    validation = validate_profile(
        profile.model_copy(update={"model": model, "resources": resources})
    )

    assert validation.executable is False
    assert "parallel_trial_gpu_quota_exceeded" in validation.blockers


def test_automated_approval_is_limited_to_non_protected_environments(tmp_path: Path) -> None:
    profile = profile_with_evidence(tmp_path)
    staging_gates = profile.gates.model_copy(
        update={"approval_policy": "automated_non_production"}
    )
    staging = validate_profile(profile.model_copy(update={"gates": staging_gates}))

    protected_gates = staging_gates.model_copy(
        update={"target_environment": "pre-production"}
    )
    protected = validate_profile(profile.model_copy(update={"gates": protected_gates}))

    assert staging.executable is True
    assert protected.executable is False
    assert (
        "automated_approval_not_allowed_for_protected_environment"
        in protected.blockers
    )


def test_saved_profile_is_versioned_idempotent_and_renders_runtime_configs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configure_roots(tmp_path, monkeypatch)
    profile = profile_with_evidence(tmp_path, execution_scope="data_cycle")

    first = save_profile(profile)
    repeated = save_profile(profile)
    updated = save_profile(
        profile.model_copy(
            update={
                "model": profile.model.model_copy(update={"learning_rate": 0.0002}),
            }
        )
    )

    assert first.version == 1
    assert repeated.version == 1
    assert repeated.digest == first.digest
    assert updated.version == 2
    assert len(read_profiles().profiles) == 2
    airflow = json.loads(Path(first.airflow_config_uri).read_text(encoding="utf-8"))
    model = json.loads(Path(first.model_config_uri).read_text(encoding="utf-8"))
    assert airflow["pipelines"]["data_validation"]["dataset_version"] == (
        profile.data.dataset_version
    )
    assert airflow["pipelines"]["dataset_shards"]["split_seed"] == 20260706
    assert airflow["pipelines"]["dataset_shards"]["split_ratios"]["test"] == 0.2
    assert model["candidates"][0]["architecture"] == "efficientnet-b0"
    assert model["candidates"][0]["early_stop_accuracy"] == 0.93
    assert model["candidates"][0]["weight_decay"] == 0.0001
    assert model["experiment_search"]["enabled"] is False
    assert model["experiment_search"]["search_space"]["batch_sizes"] == [32, 64]
    assert model["inputs"]["base_config"] == first.airflow_runtime_uri
    assert model["model_matrix"]["rollback_registry_path"].endswith(
        "/artifacts/registry/efficientnet-b0/rollback.json"
    )
    assert len(first.profile_snapshot_sha256) == 64
    assert len(first.source_manifest_sha256) == 64
    assert len(first.split_manifest_file_sha256) == 64
    assert len(first.airflow_config_sha256) == 64
    assert len(first.model_config_sha256) == 64
    assert len(first.model_component_catalog_sha256) == 64
    assert len(first.reproducibility_digest) == 64
    replay = validate_profile_replay(first)
    assert replay.status == "ready"
    assert replay.blockers == []
    assert all(check.status == "pass" for check in replay.checks)


def test_replay_validation_blocks_tampered_runtime_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configure_roots(tmp_path, monkeypatch)
    record = save_profile(profile_with_evidence(tmp_path))
    model_path = Path(record.model_config_uri)
    model = json.loads(model_path.read_text(encoding="utf-8"))
    model["candidates"][0]["batch_size"] = 1
    model_path.write_text(json.dumps(model), encoding="utf-8")

    replay = validate_profile_replay(record)

    assert replay.status == "blocked"
    assert "replay_identity_mismatch:model_runtime_config" in replay.blockers
    assert "replay_identity_mismatch:reproducibility_digest" in replay.blockers


def test_profile_catalog_marks_replay_identity_mismatch_blocked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configure_roots(tmp_path, monkeypatch)
    record = save_profile(profile_with_evidence(tmp_path))
    source_manifest = Path(record.profile.data.source_manifest_uri)
    source_manifest.write_text(
        source_manifest.read_text(encoding="utf-8") + '{"sample_id":"sample-2"}\n',
        encoding="utf-8",
    )

    refreshed = read_profiles().profiles[0]

    assert refreshed.validation.status == "blocked"
    assert refreshed.validation.executable is False
    assert (
        "replay:replay_identity_mismatch:source_manifest"
        in refreshed.validation.blockers
    )
    assert all(
        stage.state == "blocked"
        for stage in refreshed.validation.stages
        if stage.state != "not_started"
    )


def test_same_profile_creates_new_version_when_component_catalog_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configure_roots(tmp_path, monkeypatch)
    source_catalog = Path(__file__).resolve().parents[1] / "configs" / "model_components.json"
    catalog_path = tmp_path / "model_components.json"
    catalog_path.write_bytes(source_catalog.read_bytes())
    monkeypatch.setenv("EVM_MODEL_COMPONENT_CATALOG", str(catalog_path))
    profile = profile_with_evidence(tmp_path)

    first = save_profile(profile)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["components"][0]["source_revision"] = "b" * 40
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    second = save_profile(profile)

    assert second.version == 2
    assert second.digest == first.digest
    assert second.model_component_catalog_sha256 != first.model_component_catalog_sha256
    assert second.reproducibility_digest != first.reproducibility_digest
    assert validate_profile_replay(first).status == "blocked"
    assert validate_profile_replay(second).status == "ready"


def test_saved_profile_validation_is_refreshed_without_mutating_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configure_roots(tmp_path, monkeypatch)
    record = save_profile(profile_with_evidence(tmp_path))
    manifest_path = Path(record.profile_uri).with_name("manifest.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["validation"].update(
        {
            "status": "blocked",
            "executable": False,
            "blockers": ["capability_not_wired:full_lifecycle_orchestrator"],
        }
    )
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    refreshed = read_profiles().profiles[0]

    assert refreshed.validation.status == "ready"
    assert refreshed.validation.executable is True
    assert refreshed.validation.blockers == []
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["validation"]["status"] == "blocked"


def test_data_cycle_launch_creates_guarded_task_assignment(tmp_path: Path, monkeypatch) -> None:
    configure_roots(tmp_path, monkeypatch)
    profile = profile_with_evidence(tmp_path, execution_scope="data_cycle")
    record = save_profile(profile)

    preview = launch_profile(
        record,
        PipelineProfileLaunchRequest(actor="ml-platform", dry_run=True),
    )
    queued = launch_profile(
        record,
        PipelineProfileLaunchRequest(actor="ml-platform", dry_run=False),
    )

    assert preview.task is not None
    assert preview.task.status == "dry_run"
    assert preview.task.config_payload["pipeline_config_uri"] == record.airflow_runtime_uri
    assert queued.task is not None
    assert queued.task.status == "pending_confirmation"
    assert queued.task.approval_policy == "manual"


def test_full_lifecycle_launch_does_not_create_task(tmp_path: Path, monkeypatch) -> None:
    configure_roots(tmp_path, monkeypatch)
    record = save_profile(profile_with_evidence(tmp_path))

    result = launch_profile(record, PipelineProfileLaunchRequest(dry_run=False))

    assert result.task is None
    assert result.validation.executable is True


def test_profile_validation_checks_manifest_identity_and_presence(tmp_path: Path) -> None:
    profile = profile_with_evidence(tmp_path, execution_scope="data_cycle")
    wrong_identity = profile.data.model_copy(update={"split_manifest_sha256": "b" * 64})
    mismatched = validate_profile(profile.model_copy(update={"data": wrong_identity}))
    assert mismatched.valid is False
    assert "split_manifest_identity_mismatch" in mismatched.blockers

    missing_data = profile.data.model_copy(
        update={"source_manifest_uri": str(tmp_path / "missing.jsonl")}
    )
    missing = validate_profile(profile.model_copy(update={"data": missing_data}))
    assert missing.valid is False
    assert "source_manifest_not_found" in missing.blockers

    wrong_seed = profile.data.split.model_copy(update={"seed": 20260712})
    seed_data = profile.data.model_copy(update={"split": wrong_seed})
    seed_mismatch = validate_profile(profile.model_copy(update={"data": seed_data}))
    assert seed_mismatch.valid is False
    assert "split_manifest_seed_mismatch" in seed_mismatch.blockers


def test_profile_rejects_base_config_outside_allowlist(tmp_path: Path) -> None:
    profile = profile_with_evidence(tmp_path, execution_scope="data_cycle").model_copy(
        update={"base_airflow_config": str(tmp_path / "untrusted.toml")}
    )
    (tmp_path / "untrusted.toml").write_text("[project]\nname='untrusted'\n", encoding="utf-8")

    validation = validate_profile(profile)

    assert validation.valid is False
    assert "airflow_base_config_not_allowed" in validation.blockers
