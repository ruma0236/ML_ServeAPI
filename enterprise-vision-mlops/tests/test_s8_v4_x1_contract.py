from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from evm.scale_validation.v4_ledger import append_event
from evm.scale_validation.x1_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    X1EvidenceValidationError,
    validate_x1_evidence,
)
from evm.scale_validation.x1_runtime import (
    ACCEPTED_PHASE_COUNTS,
    EXPECTED_MODELS,
    NON_CREDIT_SUCCESS_COUNTS,
    X1AttemptRequest,
    X1RuntimeConfig,
    X1RuntimeError,
    prepare_x1_attempt,
    resolve_verified_s6bm_dependency,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/s8_v4_x1_concurrency_v2.toml"
LEDGER = ROOT / "docs/status/2026-08-24-s8-v4-progress-ledger.jsonl"
PLAN = ROOT / "docs/agenda/2026-08-24-distributed-scale-operational-validation-plan-v4.md"
AMENDMENT = ROOT / "docs/status/evidence/s8-v4-x1-method-contract-amendment.json"


def _config() -> X1RuntimeConfig:
    return X1RuntimeConfig.from_path(CONFIG)


def _git_blob_bytes(path: Path) -> bytes:
    repository_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    relative = path.resolve().relative_to(repository_root.resolve()).as_posix()
    return subprocess.run(
        ["git", "show", f"HEAD:{relative}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def _planned_payload() -> dict[str, object]:
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "work_item": "X1",
        "status": "planned",
        "acceptance_credit": False,
        "reviewer_sign_off": "not_applicable",
        "dependency": None,
        "runs": [],
    }


def _install_verified_dependency(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    ledger = project_root / "ledger.jsonl"
    ledger.write_bytes(LEDGER.read_bytes())
    revision = "a" * 40
    tree = "b" * 40
    closure_relative = Path("docs/status/evidence/s8-v4-s6bm-verified-closure.json")
    closure_path = project_root / closure_relative
    closure_path.parent.mkdir(parents=True)
    closure = {
        "schema_version": "evm.s8_v4.s6bm_verified_closure.v1",
        "work_item": "S6B-M",
        "status": "verified",
        "acceptance_credit": True,
        "source_identity": {
            "handoff_revision": revision,
            "handoff_tree_sha": tree,
        },
    }
    closure_path.write_bytes(
        (json.dumps(closure, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    )
    closure_sha = hashlib.sha256(closure_path.read_bytes()).hexdigest()
    event = append_event(
        ledger,
        {
            "schema_version": "evm.s8_v4.progress_event.v1",
            "event_id": "s8-v4-9998",
            "event_type": "s6bm_verified_closure",
            "work_item": "S6B-M",
            "occurred_at": "2026-08-25T13:00:00Z",
            "from_status": "review_pending",
            "to_status": "verified",
            "source_git_revision": revision,
            "source_tree_sha": tree,
            "acceptance_credit": True,
            "credit": "credit",
            "reviewer_sign_off": {"result": "passed"},
            "verified_closure": {
                "path": closure_relative.as_posix(),
                "sha256": closure_sha,
            },
        },
    )
    return project_root, event


def _accepted_runs(config: X1RuntimeConfig) -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    for phase in ("P2", "P3", "P4"):
        for repetition in range(1, 4):
            runs.append(
                {
                    "attempt_id": f"x1-{phase.lower()}-{repetition}",
                    "phase": phase,
                    "point_id": f"{phase.lower()}-control",
                    "run_index": repetition,
                    "credit": "credit",
                    "acceptance_credit": False,
                    "model_ids": list(EXPECTED_MODELS),
                }
            )
    for point in config.p5_point_ids:
        for repetition in range(1, 4):
            runs.append(
                {
                    "attempt_id": f"x1-p5-{point}-{repetition}",
                    "phase": "P5",
                    "point_id": point,
                    "run_index": repetition,
                    "credit": "credit",
                    "acceptance_credit": False,
                    "model_ids": list(EXPECTED_MODELS),
                }
            )
    return runs


def _noncredit_gate_runs() -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    for model_id in EXPECTED_MODELS:
        runs.append(
            {
                "attempt_id": f"x1-q0-{model_id}",
                "phase": "Q0",
                "point_id": model_id,
                "run_index": 1,
                "credit": "non_credit",
                "acceptance_credit": False,
                "model_ids": [model_id],
                "cuda_activity_observed": True,
                "cpu_fallback_observed": False,
            }
        )
        for repetition in range(1, 4):
            runs.append(
                {
                    "attempt_id": f"x1-p0-{model_id}-{repetition}",
                    "phase": "P0",
                    "point_id": model_id,
                    "run_index": repetition,
                    "credit": "non_credit",
                    "acceptance_credit": False,
                    "model_ids": [model_id],
                }
            )
    for phase, count in {"P1": 3, "CANDIDATE": 1, "PROFILER": 3, "HOT_GUARD": 1}.items():
        for repetition in range(1, count + 1):
            runs.append(
                {
                    "attempt_id": f"x1-{phase.lower()}-{repetition}",
                    "phase": phase,
                    "point_id": f"{phase.lower()}-control",
                    "run_index": repetition,
                    "credit": "non_credit",
                    "acceptance_credit": False,
                    "model_ids": list(EXPECTED_MODELS),
                }
            )
    return runs


def test_method_amendment_preserves_plan_and_freezes_exact_arithmetic() -> None:
    config = _config()
    plan_bytes = _git_blob_bytes(PLAN)
    amendment_bytes = AMENDMENT.read_bytes()
    amendment = json.loads(amendment_bytes)
    assert amendment_bytes == (
        json.dumps(
            amendment, allow_nan=False, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode("ascii")
    assert hashlib.sha256(plan_bytes).hexdigest() == amendment["amends"]["preserved_sha256"]
    assert len(plan_bytes) == amendment["amends"]["preserved_byte_length"]
    assert tuple(item["model_id"] for item in amendment["model_set"]) == EXPECTED_MODELS
    assert config.accepted_phase_counts == ACCEPTED_PHASE_COUNTS
    assert config.accepted_total_runs == 39
    assert config.non_credit_success_counts == NON_CREDIT_SUCCESS_COUNTS
    assert amendment["accepted_execution"]["exact_total_runs"] == 39
    assert amendment["status"] == "planned"
    assert amendment["credit"] == "non_credit"
    assert amendment["acceptance_credit"] is False


def test_planned_noncredit_scaffold_validates_without_s6bm_closure() -> None:
    result = validate_x1_evidence(
        _planned_payload(), config=_config(), project_root=ROOT, ledger_path=LEDGER
    )
    assert result["valid"] is True
    assert result["accepted_run_count"] == 0
    assert result["dependency_verified"] is False


def test_noncredit_preparation_is_allowed_before_s6bm_closure() -> None:
    manifest = prepare_x1_attempt(
        X1AttemptRequest("x1-q0-1", "Q0", 1, "cuda-probe", "non_credit"),
        config=_config(),
        project_root=ROOT,
        ledger_path=LEDGER,
    )
    assert manifest["execution_authorized"] is True
    assert manifest["dependency"] is None
    assert manifest["acceptance_credit"] is False


def test_accepted_execution_fails_closed_while_s6bm_is_unverified() -> None:
    with pytest.raises(X1RuntimeError, match="dependency_not_verified"):
        prepare_x1_attempt(
            X1AttemptRequest("x1-p2-1", "P2", 1, "p2-control", "credit"),
            config=_config(),
            project_root=ROOT,
            ledger_path=LEDGER,
        )


def test_running_evidence_fails_closed_while_s6bm_is_unverified() -> None:
    payload = _planned_payload()
    payload["status"] = "running"
    with pytest.raises(X1EvidenceValidationError, match="dependency_not_verified"):
        validate_x1_evidence(payload, config=_config(), project_root=ROOT, ledger_path=LEDGER)


def test_exact_verified_dependency_unlocks_exact_39_run_review_handoff(tmp_path: Path) -> None:
    config = _config()
    project_root, _ = _install_verified_dependency(tmp_path)
    ledger = project_root / "ledger.jsonl"
    dependency = resolve_verified_s6bm_dependency(project_root=project_root, ledger_path=ledger)
    attempt = prepare_x1_attempt(
        X1AttemptRequest("x1-p2-1", "P2", 1, "p2-control", "credit"),
        config=config,
        project_root=project_root,
        ledger_path=ledger,
    )
    assert attempt["execution_authorized"] is True
    assert attempt["dependency"] == dependency
    payload = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "work_item": "X1",
        "status": "review_pending",
        "acceptance_credit": False,
        "reviewer_sign_off": "pending",
        "dependency": dependency,
        "runs": _noncredit_gate_runs() + _accepted_runs(config),
    }
    result = validate_x1_evidence(
        payload, config=config, project_root=project_root, ledger_path=ledger
    )
    assert result["accepted_run_count"] == 39
    assert result["accepted_phase_counts"] == ACCEPTED_PHASE_COUNTS
    assert result["dependency_verified"] is True


def test_review_handoff_rejects_dependency_tuple_tampering(tmp_path: Path) -> None:
    config = _config()
    project_root, _ = _install_verified_dependency(tmp_path)
    ledger = project_root / "ledger.jsonl"
    dependency = resolve_verified_s6bm_dependency(project_root=project_root, ledger_path=ledger)
    dependency["source_tree_sha"] = "c" * 40
    payload = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "work_item": "X1",
        "status": "review_pending",
        "acceptance_credit": False,
        "reviewer_sign_off": "pending",
        "dependency": dependency,
        "runs": _noncredit_gate_runs() + _accepted_runs(config),
    }
    with pytest.raises(X1EvidenceValidationError, match="dependency_tuple"):
        validate_x1_evidence(payload, config=config, project_root=project_root, ledger_path=ledger)


def test_exact_run_validator_rejects_missing_and_duplicate_credit(tmp_path: Path) -> None:
    config = _config()
    project_root, _ = _install_verified_dependency(tmp_path)
    ledger = project_root / "ledger.jsonl"
    dependency = resolve_verified_s6bm_dependency(project_root=project_root, ledger_path=ledger)
    payload = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "work_item": "X1",
        "status": "review_pending",
        "acceptance_credit": False,
        "reviewer_sign_off": "pending",
        "dependency": dependency,
        "runs": _noncredit_gate_runs() + _accepted_runs(config),
    }
    payload["runs"] = payload["runs"][:-1] + [copy.deepcopy(payload["runs"][-2])]
    with pytest.raises(
        X1EvidenceValidationError,
        match="accepted_run_identity_duplicate|p5_exact_repetitions",
    ):
        validate_x1_evidence(payload, config=config, project_root=project_root, ledger_path=ledger)


def test_credit_class_cannot_be_relabelled() -> None:
    with pytest.raises(X1RuntimeError, match="credit_class"):
        prepare_x1_attempt(
            X1AttemptRequest("x1-p0-1", "P0", 1, "solo", "credit"),
            config=_config(),
            project_root=ROOT,
            ledger_path=LEDGER,
        )


def test_q0_silent_cpu_fallback_is_rejected() -> None:
    payload = _planned_payload()
    payload["runs"] = [_noncredit_gate_runs()[0]]
    payload["runs"][0]["cuda_activity_observed"] = False
    payload["runs"][0]["cpu_fallback_observed"] = True
    with pytest.raises(X1EvidenceValidationError, match="q0_cpu_fallback|q0_cuda_activity"):
        validate_x1_evidence(payload, config=_config(), project_root=ROOT, ledger_path=LEDGER)
