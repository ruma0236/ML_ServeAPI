from __future__ import annotations

import gzip
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from evm.control_panel.scenario_workloads import CapacityProbeRequest
from evm.model_runtime.capacity_probe import (
    clear_capacity_probe_cache,
    load_capacity_probe_catalog,
    run_capacity_probe,
)
from evm.scale_validation.s3_higgs import (
    HiggsPreparationConfig,
    HiggsPreparationError,
    file_sha256,
    prepare_higgs_capacity,
)


def _write_source(path: Path, *, rows: int) -> None:
    with gzip.open(path, "wt", encoding="ascii", newline="") as handle:
        for row_index in range(rows):
            label = row_index % 2
            features = [
                f"{((row_index + feature_index) % 31) / 10:.6f}"
                for feature_index in range(28)
            ]
            handle.write(",".join([str(label), *features]) + "\n")


def _config(
    tmp_path: Path,
    *,
    source_sha256: str,
) -> HiggsPreparationConfig:
    artifact_root = tmp_path / "artifacts"
    return HiggsPreparationConfig(
        source_path=tmp_path / "HIGGS.csv.gz",
        output_root=artifact_root / "higgs-test-v1",
        registry_path=artifact_root / "capacity-registry.json",
        source_revision="a" * 40,
        source_branch="codex/test-s3",
        experiment_config_sha256="c" * 64,
        source_sha256=source_sha256,
        dataset_version="controlled-test-v1",
        total_rows=120,
        official_test_rows=20,
        train_sample_rows=60,
        validation_sample_rows=20,
        test_sample_rows=10,
        replay_sample_rows=10,
        seed=20260817,
        incremental_epochs=2,
        incremental_batch_rows=16,
    )


def test_preparation_streams_source_and_loads_all_runtime_probes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_path = tmp_path / "HIGGS.csv.gz"
    _write_source(source_path, rows=120)
    config = _config(tmp_path, source_sha256=file_sha256(source_path))

    result = prepare_higgs_capacity(config)

    assert result["row_count"] == 120
    assert result["sample_counts"] == {
        "train": 60,
        "validation": 20,
        "test": 10,
        "replay": 10,
    }
    assert result["models"]["logistic"]["metrics"]["test_accuracy"] >= 0
    assert set(result["models"]) == {
        "logistic",
        "probabilistic",
        "online-linear",
        "branch-heavy",
        "incremental",
    }
    assert config.output_root.is_dir()
    assert config.registry_path.is_file()
    assert not list(config.output_root.parent.glob("*.building-*"))

    monkeypatch.setenv("EVM_S3_CAPACITY_REGISTRY_PATH", str(config.registry_path))
    clear_capacity_probe_cache()
    catalog = load_capacity_probe_catalog()
    replay_features = np.load(
        config.output_root / "splits" / "replay" / "features.npy",
        allow_pickle=False,
    )
    for descriptor in catalog.probes:
        response = run_capacity_probe(
            CapacityProbeRequest(
                probe_family=descriptor.probe_family,
                dataset_identity_sha256=catalog.dataset_identity_sha256,
                features=replay_features[0].tolist(),
            )
        )
        assert response.probe_family == descriptor.probe_family
        assert response.model_identity_sha256 == descriptor.model_identity_sha256
        assert 0 <= response.positive_probability <= 1


def test_preparation_fails_before_output_on_source_digest_mismatch(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "HIGGS.csv.gz"
    _write_source(source_path, rows=120)
    config = _config(tmp_path, source_sha256="0" * 64)

    with pytest.raises(HiggsPreparationError, match="source_digest_mismatch"):
        prepare_higgs_capacity(config)

    assert not config.output_root.exists()
    assert not config.registry_path.exists()


def test_preparation_cli_bootstraps_repository_source_without_pythonpath(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() != "PYTHONPATH"
    }

    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "dev" / "prepare_s3_higgs_capacity.py"),
            "--help",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Prepare governed HIGGS samples" in result.stdout


def test_repository_preparation_config_digest_remains_immutable() -> None:
    root = Path(__file__).resolve().parents[1]

    assert file_sha256(root / "configs" / "s3_higgs_capacity.toml") == (
        "3d9869aa69033cab06d64c0d83c118a524d2291b0c2c624ecac03da465c2f468"
    )
