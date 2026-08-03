from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evm.operations.lifecycle_guard_actual_suite import read_json, record


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scenario_e_result(root: Path) -> Path:
    artifact = root / "artifact.json"
    write_json(artifact, {"ok": True})
    index = root / "evidence-index.json"
    write_json(
        index,
        {
            "artifact_count": 1,
            "artifacts": [
                {
                    "path": artifact.name,
                    "sha256": digest(artifact),
                    "size_bytes": artifact.stat().st_size,
                }
            ],
        },
    )
    result = root / "result.json"
    write_json(
        result,
        {
            "status": "pass",
            "mode": "lifecycle_stage_injection",
            "source_revision": "a" * 40,
            "data_blocked_run_id": "lifecycle-e-data",
            "release_blocked_run_id": "lifecycle-e-release",
            "evidence_index_uri": str(index),
            "claim_boundary": "local",
        },
    )
    return result


def test_record_starts_suite_with_rehashed_scenario_e(tmp_path: Path) -> None:
    manifest_path = record(
        suite_root=tmp_path / "suite",
        suite_id="suite-1",
        source_checkpoint="b" * 40,
        scenario="E",
        result_path=scenario_e_result(tmp_path / "e"),
    )

    manifest = read_json(manifest_path)
    assert manifest["status"] == "in_progress"
    assert manifest["scenarios"]["E"]["evidence_artifacts_matched"] == 1
    assert manifest["scenarios"]["E"]["lifecycle_run_ids"] == [
        "lifecycle-e-data",
        "lifecycle-e-release",
    ]


def test_record_rejects_out_of_order_scenario(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="scenario_order_violation"):
        record(
            suite_root=tmp_path / "suite",
            suite_id="suite-1",
            source_checkpoint="b" * 40,
            scenario="C",
            result_path=scenario_e_result(tmp_path / "c"),
        )
