from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from evm.operations.failure_evidence import OperationalFailureReport, validate_closure


DIGEST = "a" * 64


def _report_payload(artifact_path: Path, *, live: bool = False) -> dict:
    artifact_digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    started = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 8, 1, 12, 1, tzinfo=timezone.utc)
    timing = {
        "audit_started_at": started,
        "audit_finished_at": finished,
        "monotonic_started_ns": 1_000_000_000,
        "monotonic_finished_ns": 61_000_000_000,
        "sample_cadence_seconds": 5.0,
        "signal_precedence": ["kubernetes_pod", "readiness", "prometheus"],
    }
    if live:
        timing.update(
            {
                "injection_monotonic_ns": 2_000_000_000,
                "detection_monotonic_ns": 7_000_000_000,
                "recovery_monotonic_ns": 42_000_000_000,
                "detection_seconds": 5.0,
                "recovery_seconds": 40.0,
            }
        )
    return {
        "schema_version": "evm.operational_failure_evidence.v1",
        "scenario_id": "A",
        "run_id": "scenario-a-test-001",
        "claim_class": "local_operational_validation",
        "status": "passed" if live else "blocked",
        "started_at": started,
        "finished_at": finished,
        "actor": "pytest",
        "approval": {
            "required": True,
            "decision": "consumed" if live else "pending",
            "approval_id": "approval-1" if live else None,
            "run_id": "scenario-a-test-001" if live else None,
            "target_uid": "uid" if live else None,
            "action_digest": DIGEST if live else None,
            "source_revision": "abcdef1" if live else None,
            "expires_at": finished if live else None,
            "consumed_at": started if live else None,
            "single_use": True,
        },
        "source": {
            "commit": "abcdef1",
            "branch": "test",
            "dirty": False,
            "api_revision": "abcdef1",
            "worker_revision": "abcdef1",
            "observer_revision": "abcdef1",
        },
        "environment": {
            "cluster_context": "docker-desktop",
            "node": "docker-desktop",
            "namespaces": ["evm-production"],
            "hardware": {"gpu": "test"},
            "runtime_versions": {"kubernetes": "test"},
        },
        "identities": {
            "dataset_version": "visa-test",
            "split_digest": DIGEST,
            "model_digest": DIGEST,
            "artifact_digest": DIGEST,
            "image_digest": DIGEST,
            "ct_digest": DIGEST,
            "rollback_digest": DIGEST,
        },
        "identity_requirements": [
            "dataset_version",
            "model_digest",
            "artifact_digest",
            "image_digest",
            "ct_digest",
            "rollback_digest",
        ],
        "preconditions": [
            {"check_id": "baseline", "passed": True, "observed": "healthy"}
        ],
        "injection": {
            "method": "pod_restart",
            "action": "delete_exact_pod",
            "target": {"namespace": "evm-production", "name": "pod", "uid": "uid"},
            "expected_effect": "brief interruption",
            "blast_radius": "single replica",
            "performed": live,
        },
        "signals": [],
        "decision": {
            "expected": "approved" if live else "blocked",
            "observed": "approved" if live else "blocked",
            "blocker_codes": [] if live else ["live_proof_not_run"],
        },
        "mitigation": {"action": "controller_reconcile"},
        "recovery": {
            "action": "wait_for_exact_identity",
            "target_identity": {"model_digest": DIGEST},
            "result": "passed" if live else "not_run",
        },
        "postconditions": [
            {"check_id": "ready", "passed": live, "observed": live}
        ],
        "artifacts": [
            {
                "uri": str(artifact_path.resolve()),
                "sha256": artifact_digest,
                "media_type": "application/json",
                "evidence_role": "run_evidence",
            }
        ],
        "limitations": ["single-node local validation"],
        "portfolio": {
            "competencies": ["Kubernetes recovery"],
            "interview_questions": ["How is recovery measured?"],
            "trade_offs": ["single replica interruption"],
            "factual_claims": ["local bounded recovery"],
            "prohibited_claims": ["multi-node HA"],
        },
        "timing": timing,
        "readiness_closure": {
            "decision": "passed",
            "required_check_ids": ["baseline"],
            "blockers": [],
            "completed_at": finished,
        },
        "live_proof_closure": {
            "decision": "passed" if live else "not_run",
            "required_check_ids": ["ready"],
            "blockers": [] if live else ["live_proof_not_run"],
            "completed_at": finished if live else None,
        },
    }


def test_readiness_and_live_proof_closures_are_independent(tmp_path: Path) -> None:
    artifact = tmp_path / "evidence.json"
    artifact.write_text("{}", encoding="utf-8")
    report = OperationalFailureReport.model_validate(_report_payload(artifact))

    assert validate_closure(report, "readiness") == []
    assert validate_closure(report, "live_proof") == [
        "live_proof_closure_not_run",
        "live_proof_not_run",
        "overall_status_blocked",
    ]


def test_live_proof_accepts_monotonic_timing_and_run_evidence(tmp_path: Path) -> None:
    artifact = tmp_path / "evidence.json"
    artifact.write_text("{}", encoding="utf-8")
    report = OperationalFailureReport.model_validate(_report_payload(artifact, live=True))

    assert validate_closure(report, "live_proof") == []


def test_timing_rejects_wall_clock_derived_duration(tmp_path: Path) -> None:
    artifact = tmp_path / "evidence.json"
    artifact.write_text("{}", encoding="utf-8")
    payload = _report_payload(artifact, live=True)
    payload["timing"]["detection_seconds"] = 4.0

    with pytest.raises(ValidationError, match="monotonic clock"):
        OperationalFailureReport.model_validate(payload)


def test_live_proof_rejects_incomplete_identity_subset(tmp_path: Path) -> None:
    artifact = tmp_path / "evidence.json"
    artifact.write_text("{}", encoding="utf-8")
    payload = _report_payload(artifact, live=True)
    payload["identities"]["ct_digest"] = None

    with pytest.raises(ValidationError, match="identity subset is incomplete"):
        OperationalFailureReport.model_validate(payload)


def test_p0_baseline_reference_cannot_close_live_proof(tmp_path: Path) -> None:
    artifact = tmp_path / "evidence.json"
    artifact.write_text("{}", encoding="utf-8")
    payload = _report_payload(artifact, live=True)
    payload["artifacts"][0]["evidence_role"] = "baseline_reference"

    with pytest.raises(ValidationError, match="P0 baseline references"):
        OperationalFailureReport.model_validate(payload)


def test_artifact_index_fails_closed_on_digest_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "evidence.json"
    artifact.write_text("{}", encoding="utf-8")
    report = OperationalFailureReport.model_validate(_report_payload(artifact, live=True))
    artifact.write_text("changed", encoding="utf-8")

    assert validate_closure(report, "live_proof") == [
        f"artifact_digest_mismatch:{artifact.resolve()}"
    ]
