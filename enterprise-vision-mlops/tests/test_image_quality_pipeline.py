from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from evm.pipelines.image_quality.run import run


def _write_png_header(path: Path, width: int, height: int) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\r"
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
    )


def test_image_quality_uses_policy_and_writes_enriched_manifest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='tmp'\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    raw_dir = tmp_path / "raw"
    validated_dir = tmp_path / "validated"
    artifacts_dir = tmp_path / "artifacts"
    raw_dir.mkdir()
    validated_dir.mkdir()
    artifacts_dir.mkdir()

    image_path = raw_dir / "sample.png"
    _write_png_header(image_path, 2, 3)

    manifest_path = validated_dir / "validated_manifest.jsonl"
    record = {
        "id": "sample_1",
        "sample_id": "sample_1",
        "image_uri": "file:///sample.png",
        "image_path": "/mnt/evm-data/raw/sample.png",
        "label": "normal",
        "split": "train",
        "width": 2,
        "height": 3,
    }
    manifest_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    metadata_path = validated_dir / "dataset_version.json"
    metadata_path.write_text('{"dataset_version":"fixture-v1"}\n', encoding="utf-8")
    policy_path = tmp_path / "quality_policy.toml"
    policy_path.write_text(
        "\n".join(
            [
                "[policy]",
                'id = "fixture_policy"',
                'version = "v1"',
                'dataset_types = ["image_manifest"]',
                'fail_levels = ["error"]',
                "",
                "[severity]",
                'duplicate_content_hash = "warn"',
                "",
                "[thresholds]",
                "local_image_coverage_minimum = 1.0",
            ]
        ),
        encoding="utf-8",
    )
    recipe_path = tmp_path / "etl_recipe.toml"
    recipe_path.write_text(
        "\n".join(
            [
                "[recipe]",
                'id = "fixture_recipe"',
                'version = "v1"',
                'dataset_types = ["image_manifest"]',
                "",
                "[[transforms]]",
                'id = "read_image_header"',
                'stage = "quality"',
                'action = "dimension_detection"',
                "enabled = true",
            ]
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[project]
name = "tmp"

[paths]
artifacts_root = "{artifacts_dir.as_posix()}"
reports_root = "{(artifacts_dir / "reports").as_posix()}"
external_storage_root = "{tmp_path.as_posix()}"

[pipelines.image_quality]
dataset_id = "fixture"
dataset_name = "fixture"
dataset_version = "stale-config-version"
input_manifest = "{manifest_path.as_posix()}"
dataset_metadata = "{metadata_path.as_posix()}"
output_manifest = "{(validated_dir / "quality_manifest.jsonl").as_posix()}"
report_path = "{(validated_dir / "quality_report.json").as_posix()}"
baseline_path = "{(validated_dir / "quality_baseline.json").as_posix()}"
raw_image_root = "{raw_dir.as_posix()}"
quality_policy = "{policy_path.as_posix()}"
etl_recipe = "{recipe_path.as_posix()}"
fail_on_error = true
""",
        encoding="utf-8",
    )

    report = run(str(config_path))

    assert report["status"] == "pass"
    assert report["dataset_version"] == "fixture-v1"
    assert report["quality_policy"]["policy_id"] == "fixture_policy"
    assert report["etl_recipe"]["recipe_id"] == "fixture_recipe"
    quality_manifest = validated_dir / "quality_manifest.jsonl"
    records = [json.loads(line) for line in quality_manifest.read_text(encoding="utf-8").splitlines()]
    assert records[0]["image_quality"]["image_readable"] is True
    assert records[0]["image_quality"]["detected_width"] == 2
    assert records[0]["image_quality"]["detected_height"] == 3
    assert records[0]["image_path"] == "/mnt/evm-data/raw/sample.png"
    assert "quality_checked_at" not in records[0]
    assert report["evaluated_at"].endswith("Z")
    assert report["local_image_count"] == 1
    assert report["local_image_coverage"] == 1.0
    assert report["readable_image_coverage"] == 1.0
    assert b"\r\n" not in quality_manifest.read_bytes()

    record["content_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="image quality validation failed"):
        run(str(config_path))
    blocked_report = json.loads(
        (validated_dir / "quality_report.json").read_text(encoding="utf-8")
    )
    assert blocked_report["status"] == "fail"
    assert blocked_report["diagnostics_by_code"]["content_hash_mismatch"] == 1


def test_image_quality_rejects_empty_input_without_overwriting_outputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='tmp'\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    manifest = tmp_path / "empty.jsonl"
    manifest.write_text("", encoding="utf-8")
    output = tmp_path / "quality.jsonl"
    report = tmp_path / "quality.json"
    output.write_text("sentinel\n", encoding="utf-8")
    report.write_text('{"sentinel":true}\n', encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        f"""
[project]
name = "tmp"

[paths]
artifacts_root = "{(tmp_path / 'artifacts').as_posix()}"
reports_root = "{(tmp_path / 'reports').as_posix()}"

[pipelines.image_quality]
input_manifest = "{manifest.as_posix()}"
output_manifest = "{output.as_posix()}"
report_path = "{report.as_posix()}"
baseline_path = "{(tmp_path / 'baseline.json').as_posix()}"
raw_image_root = "{tmp_path.as_posix()}"
fail_on_empty = true
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="zero records"):
        run(str(config))

    assert output.read_text(encoding="utf-8") == "sentinel\n"
    assert json.loads(report.read_text(encoding="utf-8")) == {"sentinel": True}
