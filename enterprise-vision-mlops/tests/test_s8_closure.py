from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

import evm.scale_validation.s8_closure as closure
from evm.scale_validation.s8_closure import S8ClosureValidationError
from evm.scale_validation.s8_runtime import S8RuntimeConfig


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def smoke_payload(tmp_path: Path) -> dict:
    checks = {
        "api_healthy": True,
        "queue_worker_healthy": True,
        "control_plane_postgresql_healthy": True,
        "source_serving_ready": True,
        "target_serving_scaled_zero": True,
        "real_cuda_inference": True,
        "prometheus_all_targets_up": True,
        "prometheus_targets_total": 5,
        "prometheus_targets_up": 5,
        "queue_active_zero": True,
        "queue_leased_zero": True,
        "queue_outcome_unknown_zero": True,
        "s8_processes_removed": True,
        "s8_containers_removed": True,
        "worker_metrics_port_available": True,
        "historical_terminal_serving_pods": 3,
    }
    raw_path = tmp_path / "runtime" / "smoke.json"
    write_json(raw_path, {"checks": checks})
    blob = {"path": "value", "blob_oid": "b" * 40, "sha256": "c" * 64}
    return {
        "schema_version": closure.SMOKE_SCHEMA_VERSION,
        "status": "verified",
        "verdict": "passed",
        "acceptance_credit": False,
        "source_identity": {
            "revision": "a" * 40,
            "git_blobs": {label: blob for label in closure.SMOKE_SOURCE_PATHS},
        },
        "checks": checks,
        "private_evidence": {
            "path": raw_path.relative_to(tmp_path).as_posix(),
            "bytes": raw_path.stat().st_size,
            "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        },
    }


def test_runtime_smoke_recomputes_private_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = smoke_payload(tmp_path)
    blob = {"path": "value", "blob_oid": "b" * 40, "sha256": "c" * 64}
    monkeypatch.setattr(closure, "git_blob_identity", lambda *_args: blob)
    monkeypatch.setattr(closure, "_is_ancestor", lambda *_args: True)

    result = closure.validate_s8_runtime_smoke(
        payload,
        private_closure_root=tmp_path,
        project_root=ROOT,
    )
    assert result["valid"] is True

    mutated = copy.deepcopy(payload)
    mutated["checks"]["real_cuda_inference"] = False
    with pytest.raises(S8ClosureValidationError, match="smoke"):
        closure.validate_s8_runtime_smoke(
            mutated,
            private_closure_root=tmp_path,
            project_root=ROOT,
        )


def regression_payload(tmp_path: Path) -> dict:
    outputs = {
        "changed_file_lint": "All checks passed!\n",
        "focused_s8_closure": "3 passed in 1.0s\n",
        "real_postgresql": "4 passed in 1.0s\n",
        "lifecycle_host_e2e": "5 passed in 1.0s\n",
        "s0_s8_status_evidence": "6 passed in 1.0s\n",
        "full_python": "7 passed, 1 skipped in 1.0s\n",
        "control_panel": "8 passed in 1.0s\nTests 9 passed (9)\n",
        "frontend_production_build": "built in 1ms\n",
    }
    suites = []
    total = 0
    skipped = 0
    for suite_id in closure.REQUIRED_SUITE_IDS:
        log_path = tmp_path / "regressions" / f"{suite_id}.log"
        meta_path = tmp_path / "regressions" / f"{suite_id}.meta.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(outputs[suite_id], encoding="utf-8")
        write_json(
            meta_path,
            {
                "suite_id": suite_id,
                "source_revision": "a" * 40,
                "exit_code": 0,
                "public_command": f"run {suite_id}",
            },
        )
        observed = closure._derive_regression_counts(suite_id, outputs[suite_id])
        total += observed["tests_passed"]
        skipped += observed["tests_skipped"]
        suites.append(
            {
                "suite_id": suite_id,
                "status": "passed",
                "command": f"run {suite_id}",
                "log_path": log_path.relative_to(tmp_path).as_posix(),
                "log_bytes": log_path.stat().st_size,
                "log_sha256": hashlib.sha256(log_path.read_bytes()).hexdigest(),
                "metadata_path": meta_path.relative_to(tmp_path).as_posix(),
                "metadata_bytes": meta_path.stat().st_size,
                "metadata_sha256": hashlib.sha256(meta_path.read_bytes()).hexdigest(),
                "observed": observed,
            }
        )
    return {
        "schema_version": closure.REGRESSION_SCHEMA_VERSION,
        "status": "passed",
        "source_identity": {"revision": "a" * 40},
        "suites": suites,
        "summary": {
            "suite_count": len(suites),
            "tests_passed": total,
            "tests_skipped": skipped,
            "all_exit_codes_zero": True,
        },
    }


def test_regression_validator_rejects_log_count_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = regression_payload(tmp_path)
    monkeypatch.setattr(closure, "_is_ancestor", lambda *_args: True)
    assert closure.validate_s8_regression_evidence(
        payload,
        private_closure_root=tmp_path,
        project_root=ROOT,
    )["valid"] is True

    payload["suites"][1]["observed"]["tests_passed"] = 999
    with pytest.raises(S8ClosureValidationError, match="count_projection"):
        closure.validate_s8_regression_evidence(
            payload,
            private_closure_root=tmp_path,
            project_root=ROOT,
        )


def test_closure_rejects_acceptance_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = S8RuntimeConfig.from_path(ROOT / "configs/s8_dependency_soak_v6.toml")
    identity = {"path": "value", "blob_oid": "b" * 40, "sha256": "c" * 64}
    loaded = {
        "experiment": {},
        "runtime_smoke": {},
        "regression": {},
    }
    monkeypatch.setattr(closure, "_is_ancestor", lambda *_args: True)
    monkeypatch.setattr(closure, "git_blob_identity", lambda *_args: identity)
    monkeypatch.setattr(
        closure,
        "_git_bytes",
        lambda _root, _revision, path: json.dumps(
            loaded[
                {
                    closure.EXPERIMENT_PATH: "experiment",
                    closure.SMOKE_PATH: "runtime_smoke",
                    closure.REGRESSION_PATH: "regression",
                }[path]
            ]
        ).encode(),
    )
    monkeypatch.setattr(
        closure,
        "validate_s8_experiment",
        lambda *_args, **_kwargs: {
            "valid": True,
            "fault_result_count": 21,
            "soak_result_count": 3,
            "recomputed_acceptance": {
                "S8-AC-01": True,
                "S8-AC-02": True,
                "S8-AC-03": True,
                "S8-AC-04": False,
            },
        },
    )
    monkeypatch.setattr(
        closure, "validate_s8_runtime_smoke", lambda *_args, **_kwargs: {"valid": True}
    )
    monkeypatch.setattr(
        closure,
        "validate_s8_regression_evidence",
        lambda *_args, **_kwargs: {"valid": True, "tests_passed": 10},
    )
    monkeypatch.setattr(
        closure,
        "validate_private_closure_index",
        lambda *_args, **_kwargs: {"artifact_count": 1},
    )
    payload = {
        "schema_version": closure.CLOSURE_SCHEMA_VERSION,
        "status": "verified",
        "verdict": "passed",
        "source_identity": {
            "supporting_revision": "a" * 40,
            "supporting_artifacts": {key: identity for key in loaded},
        },
        "acceptance": {
            "S8-AC-01": True,
            "S8-AC-02": True,
            "S8-AC-03": True,
            "S8-AC-04": True,
        },
        "private_closure_evidence": {"artifact_count": 1},
        "closure_checks": {"all": True},
        "unresolved_blockers": [],
    }
    assert closure.validate_s8_closure(
        payload,
        experiment_private_root=tmp_path,
        private_closure_root=tmp_path,
        project_root=ROOT,
        config=config,
    )["valid"] is True

    payload["acceptance"]["S8-AC-04"] = False
    with pytest.raises(S8ClosureValidationError, match="acceptance_projection"):
        closure.validate_s8_closure(
            payload,
            experiment_private_root=tmp_path,
            private_closure_root=tmp_path,
            project_root=ROOT,
            config=config,
        )
