from __future__ import annotations

import json
from pathlib import Path

from evm.operations.lifecycle_guard_closure_validator import (
    _claim_boundary_valid,
    _e_result,
    sha256_file,
    validate_evidence_graph,
    validate_runtime,
)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_index(root: Path, artifacts: list[Path], name: str = "evidence-index.json") -> Path:
    index = root / name
    write_json(
        index,
        {
            "artifact_count": len(artifacts),
            "artifacts": [
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
                for path in artifacts
            ],
        },
    )
    return index


def test_evidence_graph_follows_referenced_child_index(tmp_path: Path) -> None:
    child_file = tmp_path / "child" / "proof.json"
    write_json(child_file, {"passed": True})
    child_index = write_index(child_file.parent, [child_file])
    parent_result = tmp_path / "result.json"
    write_json(parent_result, {"evidence_index_uri": str(child_index)})
    parent_index = write_index(tmp_path, [parent_result, child_index])

    result = validate_evidence_graph([parent_index], tmp_path)

    assert result["passed"] is True
    assert result["index_count"] == 2
    assert result["artifact_count"] == 3


def test_evidence_graph_rejects_hash_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "proof.json"
    write_json(artifact, {"passed": True})
    index = write_index(tmp_path, [artifact])
    write_json(artifact, {"passed": False})

    result = validate_evidence_graph([index], tmp_path)

    assert result["passed"] is False
    assert any("mismatch" in item for item in result["failures"])


def test_evidence_graph_supports_legacy_files_map_and_ignores_index_digest(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "proof.json"
    write_json(
        artifact,
        {"evidence_index_sha256": "a" * 64, "passed": True},
    )
    index = tmp_path / "evidence-index.json"
    write_json(
        index,
        {
            "files": {
                "proof": {
                    "uri": str(artifact),
                    "sha256": sha256_file(artifact),
                }
            }
        },
    )

    result = validate_evidence_graph([index], tmp_path)

    assert result["passed"] is True
    assert result["index_count"] == 1
    assert result["artifact_count"] == 1


def test_scenario_e_replay_only_is_not_live_lifecycle_reachability(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    write_json(
        result_path,
        {
            "schema_version": "evm.lifecycle_guard_scenario_e_replay.v1",
            "status": "pass",
            "mode": "non_disruptive_controlled_branch_replay",
            "golden_run_id": "golden-1",
            "checks": {"canonical_three_replays": True},
        },
    )
    write_index(tmp_path, [result_path])

    result = _e_result(
        {"root": str(tmp_path), "required_live_mode": "lifecycle_stage_injection"},
        tmp_path,
    )

    assert result["status"] == "blocked"
    assert result["decision_determinism"]["passed"] is True
    assert result["lifecycle_reachability"]["passed"] is False
    assert "e_actual_lifecycle_injection_missing:controlled_branch_replay_only" in result["blockers"]


def test_runtime_validation_requires_exact_model_and_live_control_plane() -> None:
    digest = "a" * 64
    snapshot = {
        "deployment": {"uid": "uid-1", "image": "image", "ready_replicas": 1},
        "ready": {"model_sha256": digest, "candidate_id": "candidate"},
        "inference": {"device": "cuda"},
        "gpu_allocatable": ["1"],
        "device_plugin": [{"ready": True}],
        "prometheus_targets": {"evm-api": "up", "evm-b0-production": "up"},
        "runtime_supervisor": {
            "source_commit": "b" * 40,
            "status": "healthy",
            "children": [
                {"name": "lifecycle_worker", "status": "live", "source_commit": "b" * 40},
                {"name": "kubernetes_observer", "status": "live", "source_commit": "b" * 40},
            ],
        },
    }

    passed = validate_runtime(
        snapshot,
        expected_model_digest=digest,
        expected_runtime_revision="b" * 40,
    )
    blocked = validate_runtime(snapshot, expected_model_digest="c" * 64)

    assert passed["passed"] is True
    assert passed["checks"]["runtime_revision_converged"] is True
    assert blocked["passed"] is False
    assert blocked["checks"]["exact_m0_model"] is False


def test_claim_boundary_accepts_comma_scoped_prohibitions() -> None:
    assert _claim_boundary_valid(
        "Controlled local single-node proof; no HA, real-user traffic, or SLA claim."
    )
    assert not _claim_boundary_valid("Enterprise production guard proof")
