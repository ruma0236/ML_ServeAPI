from __future__ import annotations

from evm.control_panel.cdct import (
    REQUIRED_CHECKS,
    build_cdct_gate,
    validate_ci_evidence,
    with_ci_bundle_digest,
    load_ci_evidence,
)
from evm.control_panel.schemas import DriftState


def _drift(action: str = "none") -> DriftState:
    return DriftState(
        status="warn" if action != "none" else "unknown",
        data_drift_status="warn" if action != "none" else "unknown",
        prediction_drift_status="unknown",
        action=action,  # type: ignore[arg-type]
        reference_dataset_version="visa-open-data-test",
        current_dataset_version="visa-open-data-test",
        review_queue_count=1 if action != "none" else 0,
        recommended_action="review 1 queued drift/special cases" if action != "none" else "no drift action required",
    )


def _ci_evidence():
    bundle = with_ci_bundle_digest(
        {
            "repository": "ruma0236/ML_ServeAPI",
            "workflow_name": "Enterprise Vision MLOps CI",
            "workflow_run_id": "123456",
            "workflow_run_attempt": 1,
            "commit_sha": "a" * 40,
            "ref": "refs/heads/codex/mac-mini-worker",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "python_test_result": "pass",
            "frontend_test_result": "pass",
            "evidence_validator_result": "pass",
            "compose_config_result": "pass",
            "kustomize_render_result": "pass",
            "image_digest": "evm-serving@sha256:" + "b" * 64,
            "config_render_digest": "c" * 64,
            "contract_digest": "d" * 64,
            "source_uri": "https://github.com/ruma0236/ML_ServeAPI/actions/runs/123456",
            "generated_at": "2026-07-10T10:00:00Z",
        }
    )
    return validate_ci_evidence(bundle, expected_commit="a" * 40)


def test_cdct_gate_separates_required_ci_cd_ct_checks():
    gate = build_cdct_gate(
        promotion_blockers=[],
        drift=_drift("none"),
        quality_status="pass",
        pipeline_run_uri="https://github.com/ruma0236/ML_ServeAPI/actions",
        ci_evidence=_ci_evidence(),
        readiness_status="pass",
    )

    assert gate.required_checks == REQUIRED_CHECKS
    assert gate.ci_status == "pass"
    assert gate.cd_status == "pass"
    assert gate.ct_status == "pass"
    assert gate.promotion_decision == "allow"
    assert gate.failed_checks == []


def test_cdct_gate_blocks_promotion_on_model_and_drift_failures():
    gate = build_cdct_gate(
        promotion_blockers=["accuracy<0.7"],
        drift=_drift("label_review"),
        quality_status="pass",
        pipeline_run_uri="https://github.com/ruma0236/ML_ServeAPI/actions",
        gate_report_uri="F:/EnterpriseMLOps_Data/lifecycle_dashboard.json",
        ci_evidence=_ci_evidence(),
        readiness_status="pass",
    )

    assert gate.status == "blocked"
    assert gate.ct_status == "blocked"
    assert gate.promotion_decision == "block"
    assert "model_evaluation" in gate.failed_checks
    assert "drift_review" in gate.failed_checks
    assert "promotion_gate" in gate.failed_checks
    assert gate.block_reason is not None
    assert "accuracy<0.7" in gate.block_reason
    assert gate.gate_report_uri == "F:/EnterpriseMLOps_Data/lifecycle_dashboard.json"


def test_cdct_gate_blocks_data_quality_before_promotion():
    gate = build_cdct_gate(
        promotion_blockers=[],
        drift=_drift("none"),
        quality_status="blocked",
        pipeline_run_uri="https://github.com/ruma0236/ML_ServeAPI/actions",
        ci_evidence=_ci_evidence(),
        readiness_status="pass",
    )

    assert gate.status == "blocked"
    assert "data_quality" in gate.failed_checks
    assert gate.verification_summary["data_quality"] == "blocked"


def test_cdct_gate_fails_closed_without_ci_evidence():
    gate = build_cdct_gate(
        promotion_blockers=[],
        drift=_drift("none"),
        quality_status="pass",
        pipeline_run_uri="https://github.com/ruma0236/ML_ServeAPI/actions",
        readiness_status="pass",
    )

    assert gate.ci_status == "blocked"
    assert gate.cd_status == "blocked"
    assert gate.promotion_decision == "block"
    assert "ci_evidence" in gate.failed_checks
    assert "ci_evidence_missing" in gate.promotion_blockers


def test_ci_validation_fails_closed_when_audit_report_cannot_persist(
    tmp_path, monkeypatch
):
    bundle = with_ci_bundle_digest(
        {
            "repository": "ruma0236/ML_ServeAPI",
            "workflow_name": "Enterprise Vision MLOps CI",
            "workflow_run_id": "123456",
            "workflow_run_attempt": 1,
            "commit_sha": "a" * 40,
            "ref": "refs/heads/codex/mac-mini-worker",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "python_test_result": "pass",
            "frontend_test_result": "pass",
            "evidence_validator_result": "pass",
            "compose_config_result": "pass",
            "kustomize_render_result": "pass",
            "image_digest": "evm-serving@sha256:" + "b" * 64,
            "config_render_digest": "c" * 64,
            "contract_digest": "d" * 64,
            "source_uri": "https://github.com/ruma0236/ML_ServeAPI/actions/runs/123456",
            "generated_at": "2026-07-10T10:00:00Z",
        }
    )
    path = tmp_path / "ci.json"
    path.write_text(bundle.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        "evm.control_panel.cdct.atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read only")),
    )

    validation = load_ci_evidence(
        path,
        expected_commit="a" * 40,
        report_uri=tmp_path / "report.json",
    )

    assert validation.valid is False
    assert validation.status == "blocked"
    assert validation.report_uri is None
    assert validation.blockers == ["ci_validation_report_persistence_failed"]
