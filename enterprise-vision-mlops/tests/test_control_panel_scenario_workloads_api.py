from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException

from apps.api.control_panel_workloads import scenario_workload_run, scenario_workload_runs


def test_scenario_workload_api_lists_persisted_runs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVM_SCENARIO_WORKLOAD_ROOT", str(tmp_path))
    run_root = tmp_path / "run-1"
    run_root.mkdir()
    model_root = run_root / "model"
    model_root.mkdir()
    evaluation_path = model_root / "adapted-evaluation.json"
    evaluation_path.write_text(
        json.dumps(
            {
                "schema_version": "evm.scenario_vlm_evaluation.v1",
                "metrics": {
                    "record_count": 8,
                    "accuracy": 0.75,
                    "parse_rate": 1.0,
                    "p95_latency_seconds": 0.49,
                },
                "evaluated_at": "2026-08-05T00:00:02Z",
            }
        ),
        encoding="utf-8",
    )
    (model_root / "training-result.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "metrics": {
                    "peak_gpu_allocated_mib": 768,
                    "training_seconds": 12.5,
                },
                "promotion_blockers": [],
                "claim_boundary": "Bounded local VLM evaluation.",
            }
        ),
        encoding="utf-8",
    )
    (run_root / "workload_run.json").write_text(
        json.dumps(
            {
                "schema_version": "evm.scenario_workload_run.v1",
                "run_id": "run-1",
                "state": "completed",
                "version": 1,
                "actor": "operator",
                "reason": "Validate a persisted workload API response",
                "dry_run": False,
                "created_at": "2026-08-05T00:00:00Z",
                "updated_at": "2026-08-05T00:00:01Z",
                "progress": 1.0,
                "identity": {
                    "scenario_id": "scienceqa-vlm-evaluation",
                    "dataset_id": "scienceqa",
                    "dataset_version": "v1",
                    "manifest_uri": "F:/manifest.jsonl",
                    "manifest_sha256": "a" * 64,
                    "split_manifest_uri": "F:/split.json",
                    "split_manifest_sha256": "b" * 64,
                    "data_identity_sha256": "c" * 64,
                    "quality_status": "pass",
                    "quality_report_uri": "F:/quality.json",
                    "model_family": "vlm",
                    "model_repository": "example/model",
                    "model_revision": "d" * 40,
                    "processor_revision": "d" * 40,
                    "source_commit": "e" * 40,
                    "dirty_worktree": False,
                    "compute_backend": "windows-host-cuda",
                    "identity_sha256": "f" * 64,
                },
                "adaptation_method": "lora",
                "quantization_requested": "none",
                "artifact_root": str(run_root),
                "evaluation_uri": str(evaluation_path),
                "stages": [],
                "audit": [],
            }
        ),
        encoding="utf-8",
    )

    listed = scenario_workload_runs()

    assert listed.total == 1
    assert listed.runs[0].identity.model_family == "vlm"
    assert listed.runs[0].evaluation_summary is not None
    assert listed.runs[0].evaluation_summary.quality_metrics == {
        "accuracy": 0.75,
        "parse_rate": 1.0,
    }
    assert listed.runs[0].evaluation_summary.operational_metrics == {
        "p95_latency_seconds": 0.49,
        "evaluated_records": 8.0,
        "peak_gpu_allocated_mib": 768.0,
        "training_seconds": 12.5,
    }
    assert listed.runs[0].evaluation_summary.release_gate.status == "pass"
    assert scenario_workload_run("run-1").state == "completed"


def test_scenario_workload_api_maps_missing_run_to_404(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVM_SCENARIO_WORKLOAD_ROOT", str(tmp_path))
    try:
        scenario_workload_run("missing")
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("missing workload must return HTTP 404")
