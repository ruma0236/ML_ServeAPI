from __future__ import annotations

import json
from pathlib import Path

import pytest

from evm.pipelines.curation_workflow.run import run


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_curation_workflow_writes_hitl_and_eval_outputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='tmp'\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    manifest = tmp_path / "quality_manifest.jsonl"
    records = [
        {
            "sample_id": "normal_train",
            "label": "normal",
            "split": "train",
            "class_name": "candle",
            "license_id": "CC-BY-4.0",
            "image_quality": {"diagnostics": []},
        },
        {
            "sample_id": "unknown_val",
            "label": "unknown",
            "split": "validation",
            "class_name": "candle",
            "license_id": "CC-BY-4.0",
            "image_quality": {"diagnostics": []},
        },
        {
            "sample_id": "anomaly_test",
            "label": "anomaly",
            "split": "test",
            "class_name": "capsules",
            "license_id": "CC-BY-4.0",
            "image_quality": {"diagnostics": []},
        },
        {
            "sample_id": "bad_test",
            "label": "normal",
            "split": "test",
            "class_name": "capsules",
            "license_id": "CC-BY-4.0",
            "image_quality": {
                "diagnostics": [
                    {"level": "error", "code": "unreadable_image", "message": "bad image"}
                ]
            },
        },
    ]
    manifest.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    out_dir = tmp_path / "curation"
    config = tmp_path / "config.toml"
    config.write_text(
        f"""
[project]
name = "tmp"

[paths]
artifacts_root = "{(tmp_path / "artifacts").as_posix()}"
reports_root = "{(tmp_path / "reports").as_posix()}"

[pipelines.curation_workflow]
input_manifest = "{manifest.as_posix()}"
output_dir = "{out_dir.as_posix()}"
state_path = "{(out_dir / "curation_state.json").as_posix()}"
curation_manifest = "{(out_dir / "curation_manifest.jsonl").as_posix()}"
hitl_queue = "{(out_dir / "hitl_queue.jsonl").as_posix()}"
sample_review_manifest = "{(out_dir / "sample_review.jsonl").as_posix()}"
curated_eval_manifest = "{(out_dir / "curated_eval_manifest.jsonl").as_posix()}"
sample_seed = 123
max_review_samples = 1
max_eval_records = 0
eval_splits = ["validation", "test"]
""",
        encoding="utf-8",
    )

    summary = run(str(config))

    assert summary["record_count"] == 4
    assert summary["hitl_queue_count"] >= 2
    assert summary["curated_eval_count"] == 1

    curation_manifest = _read_jsonl(out_dir / "curation_manifest.jsonl")
    by_id = {str(record["sample_id"]): record for record in curation_manifest}
    assert by_id["unknown_val"]["curation"]["review_state"] == "hitl_required"
    assert by_id["bad_test"]["curation"]["eval_promotion_state"] == "blocked"
    assert by_id["anomaly_test"]["curation"]["eval_promotion_state"] == "candidate"

    eval_records = _read_jsonl(out_dir / "curated_eval_manifest.jsonl")
    assert [record["sample_id"] for record in eval_records] == ["anomaly_test"]


def test_curation_workflow_fails_on_empty_input_manifest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='tmp'\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    manifest = tmp_path / "quality_manifest.jsonl"
    manifest.write_text("", encoding="utf-8")
    out_dir = tmp_path / "curation"
    config = tmp_path / "config.toml"
    config.write_text(
        f"""
[project]
name = "tmp"

[paths]
artifacts_root = "{(tmp_path / "artifacts").as_posix()}"
reports_root = "{(tmp_path / "reports").as_posix()}"

[pipelines.curation_workflow]
input_manifest = "{manifest.as_posix()}"
output_dir = "{out_dir.as_posix()}"
fail_on_empty = true
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="curation input manifest is empty"):
        run(str(config))
