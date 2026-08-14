from __future__ import annotations

from pathlib import Path

import pytest

from evm.core.config import map_runtime_data_path, resolve_path
from evm.pipelines.data_validation.run import (
    resolve_dataset_version,
    run as run_data_validation,
)


def test_runtime_data_path_maps_windows_host_root_to_container_mount(monkeypatch) -> None:
    monkeypatch.setenv(
        "EVM_HOST_DATA_ROOT", "F:/EnterpriseMLOps_Data/enterprise-vision-mlops"
    )
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")

    mapped = map_runtime_data_path(
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/raw/industrial/visa"
    )

    assert mapped == Path("/mnt/evm-data/data/raw/industrial/visa")


def test_resolve_path_uses_the_absolute_path_for_the_current_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    host_root = "F:/EnterpriseMLOps_Data/enterprise-vision-mlops"
    mount_root = "/mnt/evm-data"
    configured = f"{host_root}/artifacts/s0/report.json"
    monkeypatch.setenv("EVM_HOST_DATA_ROOT", host_root)
    monkeypatch.setenv("EVM_DATA_MOUNT_ROOT", mount_root)

    resolved = resolve_path({"_project_root": str(tmp_path)}, configured)

    expected = Path(f"{mount_root}/artifacts/s0/report.json")
    if not expected.is_absolute():
        expected = Path(configured)
    assert resolved == expected


def test_configured_dataset_version_preserves_external_lineage() -> None:
    version, source, computed = resolve_dataset_version(
        "visa",
        "33c87aaa14fe" + "0" * 52,
        "visa-open-data-e35d93d5561f",
    )

    assert version == "visa-open-data-e35d93d5561f"
    assert source == "configured"
    assert computed == "visa-33c87aaa14fe"


def test_data_validation_rejects_empty_input_without_overwriting_outputs(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='tmp'\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    source = tmp_path / "empty.jsonl"
    source.write_text("", encoding="utf-8")
    output = tmp_path / "validated.jsonl"
    metadata = tmp_path / "dataset_version.json"
    output.write_text("sentinel\n", encoding="utf-8")
    metadata.write_text('{"sentinel":true}\n', encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        f"""
[project]
name = "tmp"

[paths]
validated_zone = "{tmp_path.as_posix()}"
artifacts_root = "{(tmp_path / 'artifacts').as_posix()}"
reports_root = "{(tmp_path / 'reports').as_posix()}"

[pipelines.data_validation]
input_manifest = "{source.as_posix()}"
output_manifest = "{output.as_posix()}"
dataset_metadata = "{metadata.as_posix()}"
processed_parquet = "{(tmp_path / 'processed.parquet').as_posix()}"
validated_parquet = "{(tmp_path / 'validated.parquet').as_posix()}"
fail_on_empty = true
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="zero records"):
        run_data_validation(str(config))

    assert output.read_text(encoding="utf-8") == "sentinel\n"
    assert metadata.read_text(encoding="utf-8") == '{"sentinel":true}\n'
