from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from evm.control_panel.readiness_evaluator import (
    canonical_evidence_uri,
    payload_sha256,
    runtime_path,
)
from evm.control_panel.schemas import (
    CDCTGate,
    CIEvidenceBundle,
    CIEvidenceValidation,
    CTEvaluation,
    DriftState,
    State,
)


REQUIRED_CHECKS = [
    "ci_evidence",
    "python_tests",
    "frontend_tests",
    "evidence_validator",
    "docker_compose_config",
    "kustomize_render",
    "data_quality",
    "model_evaluation",
    "artifact_readiness",
    "isolated_ct_evaluation",
    "drift_review",
    "promotion_gate",
]
DEFAULT_CI_REPOSITORY = "ruma0236/ML_ServeAPI"
DEFAULT_CI_WORKFLOW = "Enterprise Vision MLOps CI"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ci_bundle_material(bundle: CIEvidenceBundle) -> dict[str, Any]:
    return bundle.model_dump(mode="json", exclude={"bundle_digest"})


def with_ci_bundle_digest(payload: dict[str, Any]) -> CIEvidenceBundle:
    draft = CIEvidenceBundle.model_validate(
        {
            **{key: value for key, value in payload.items() if key != "bundle_digest"},
            "bundle_digest": "",
        }
    )
    material = draft.model_dump(mode="json", exclude={"bundle_digest"})
    return draft.model_copy(update={"bundle_digest": payload_sha256(material)})


def validate_ci_evidence(
    bundle: CIEvidenceBundle,
    *,
    expected_commit: str | None = None,
    expected_repository: str | None = None,
    expected_workflow: str | None = None,
    report_uri: str | None = None,
    checked_at: str | None = None,
) -> CIEvidenceValidation:
    repository = expected_repository or DEFAULT_CI_REPOSITORY
    workflow = expected_workflow or DEFAULT_CI_WORKFLOW
    actual_digest = payload_sha256(ci_bundle_material(bundle))
    checks: dict[str, State] = {
        "schema_version": state(bundle.schema_version == "evm.w7.ci_evidence.v1"),
        "repository": state(bundle.repository == repository),
        "workflow_identity": state(bundle.workflow_name == workflow),
        "workflow_run_id": state(bundle.workflow_run_id.isdigit()),
        "commit_sha": state(bool(re.fullmatch(r"[0-9a-f]{40}", bundle.commit_sha))),
        "ref": state(bool(bundle.ref.strip())),
        "workflow_completion": state(
            bundle.status == "completed" and bundle.conclusion == "success"
        ),
        "python_tests": state(bundle.python_test_result == "pass"),
        "frontend_tests": state(bundle.frontend_test_result == "pass"),
        "evidence_validator": state(bundle.evidence_validator_result == "pass"),
        "docker_compose_config": state(bundle.compose_config_result == "pass"),
        "kustomize_render": state(bundle.kustomize_render_result == "pass"),
        "image_digest": state(
            bool(re.fullmatch(r".+@sha256:[0-9a-f]{64}", bundle.image_digest))
        ),
        "config_render_digest": state(is_sha256(bundle.config_render_digest)),
        "contract_digest": state(is_sha256(bundle.contract_digest)),
        "source_uri": state(
            bundle.source_uri
            == f"https://github.com/{bundle.repository}/actions/runs/{bundle.workflow_run_id}"
        ),
        "generated_at": state(valid_iso_timestamp(bundle.generated_at)),
        "bundle_digest": state(bundle.bundle_digest == actual_digest),
        "expected_commit": state(
            not expected_commit or bundle.commit_sha.lower() == expected_commit.lower()
        ),
    }
    blockers = sorted(
        f"ci_{check_id}_failed" for check_id, status in checks.items() if status != "pass"
    )
    input_digest = payload_sha256(
        {
            "bundle_digest": bundle.bundle_digest,
            "expected_commit": expected_commit or "",
            "checks": checks,
        }
    )
    return CIEvidenceValidation(
        validation_id=f"ci-validation-{input_digest[:16]}",
        valid=not blockers,
        status="pass" if not blockers else "blocked",
        workflow_run_id=bundle.workflow_run_id,
        commit_sha=bundle.commit_sha,
        checked_at=checked_at or utc_now(),
        input_digest=input_digest,
        checks=checks,
        blockers=blockers,
        source_uri=bundle.source_uri,
        report_uri=report_uri,
    )


def load_ci_evidence(
    path: str | Path | None,
    *,
    expected_commit: str | None = None,
    expected_repository: str | None = None,
    expected_workflow: str | None = None,
    report_uri: str | Path | None = None,
) -> CIEvidenceValidation:
    checked_at = utc_now()
    if not path:
        return missing_ci_validation("ci_evidence_path_missing", expected_commit, checked_at)
    evidence_path = runtime_path(path)
    if not evidence_path.exists():
        return missing_ci_validation("ci_evidence_missing", expected_commit, checked_at)
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        bundle = CIEvidenceBundle.model_validate(payload)
    except (json.JSONDecodeError, ValueError):
        return missing_ci_validation("ci_evidence_malformed", expected_commit, checked_at)

    report_path = runtime_path(report_uri) if report_uri else None
    validation = validate_ci_evidence(
        bundle,
        expected_commit=expected_commit,
        expected_repository=expected_repository,
        expected_workflow=expected_workflow,
        report_uri=canonical_evidence_uri(report_path) if report_path else None,
        checked_at=checked_at,
    )
    if report_path:
        try:
            atomic_write_json(report_path, validation.model_dump(mode="json"))
        except OSError:
            blockers = sorted(
                set([*validation.blockers, "ci_validation_report_persistence_failed"])
            )
            return validation.model_copy(
                update={
                    "valid": False,
                    "status": "blocked",
                    "blockers": blockers,
                    "report_uri": None,
                }
            )
    return validation


def missing_ci_validation(
    blocker: str,
    expected_commit: str | None,
    checked_at: str,
) -> CIEvidenceValidation:
    input_digest = payload_sha256({"blocker": blocker, "expected_commit": expected_commit or ""})
    return CIEvidenceValidation(
        validation_id=f"ci-validation-{input_digest[:16]}",
        valid=False,
        status="blocked",
        workflow_run_id="",
        commit_sha=expected_commit or "",
        checked_at=checked_at,
        input_digest=input_digest,
        checks={"ci_evidence": "blocked"},
        blockers=[blocker],
    )


def build_cdct_gate(
    *,
    promotion_blockers: list[str],
    drift: DriftState,
    quality_status: State,
    pipeline_run_uri: str,
    gate_report_uri: str | None = None,
    ci_evidence: CIEvidenceValidation | None = None,
    ct_evaluation: CTEvaluation | None = None,
    readiness_status: State = "unknown",
) -> CDCTGate:
    ci_validation = ci_evidence or missing_ci_validation(
        "ci_evidence_missing",
        None,
        utc_now(),
    )
    ci_status: State = "pass" if ci_validation.valid else "blocked"
    cd_ready = ci_validation.valid and all(
        ci_validation.checks.get(check) == "pass"
        for check in ("docker_compose_config", "kustomize_render", "image_digest")
    )
    cd_status: State = "pass" if cd_ready else "blocked"
    ct_evidence_ready = bool(
        ct_evaluation is not None
        and ct_evaluation.decision == "pass"
        and ct_evaluation.status == "pass"
        and not ct_evaluation.blockers
    )
    ct_ready = (
        ct_evidence_ready
        and quality_status in {"pass", "done"}
        and readiness_status in {"pass", "done"}
        and not promotion_blockers
        and drift.action == "none"
    )
    ct_status: State = "pass" if ct_ready else "blocked"
    verification_summary: dict[str, State] = {
        "ci_evidence": ci_status,
        "python_tests": ci_check(ci_validation, "python_tests"),
        "frontend_tests": ci_check(ci_validation, "frontend_tests"),
        "evidence_validator": ci_check(ci_validation, "evidence_validator"),
        "docker_compose_config": ci_check(ci_validation, "docker_compose_config"),
        "kustomize_render": ci_check(ci_validation, "kustomize_render"),
        "data_quality": "pass" if quality_status in {"pass", "done"} else "blocked",
        "model_evaluation": "blocked" if promotion_blockers else "pass",
        "artifact_readiness": (
            "pass" if readiness_status in {"pass", "done"} else "blocked"
        ),
        "isolated_ct_evaluation": "pass" if ct_evidence_ready else "blocked",
        "drift_review": "pass" if drift.action == "none" else "blocked",
        "promotion_gate": "blocked" if promotion_blockers else "pass",
    }
    failed_checks = [
        check for check in REQUIRED_CHECKS if verification_summary.get(check) != "pass"
    ]
    passed_checks = [check for check in REQUIRED_CHECKS if check not in failed_checks]
    gate_blocked = bool(failed_checks or ci_validation.blockers or promotion_blockers)
    ct_blockers = (
        list(ct_evaluation.blockers)
        if ct_evaluation is not None
        else ["ct_evaluation_missing"]
    )
    blockers = sorted(
        set([*promotion_blockers, *ci_validation.blockers, *ct_blockers])
    )
    return CDCTGate(
        status="blocked" if gate_blocked else "pass",
        ci_status=ci_status,
        cd_status=cd_status,
        ct_status=ct_status,
        required_checks=REQUIRED_CHECKS,
        passed_checks=passed_checks,
        failed_checks=failed_checks,
        pipeline_run_uri=ci_validation.source_uri or pipeline_run_uri,
        ct_trigger="drift" if drift.action != "none" else "manual",
        promotion_blockers=blockers,
        gate_report_uri=ci_validation.report_uri or gate_report_uri,
        promotion_decision="block" if gate_blocked else "allow",
        block_reason=block_reason(failed_checks, blockers),
        verification_summary=verification_summary,
        ct_snapshot_id=ct_evaluation.snapshot_id if ct_evaluation else None,
        ct_snapshot_digest=ct_evaluation.snapshot_digest if ct_evaluation else None,
        ct_evaluation_id=ct_evaluation.evaluation_id if ct_evaluation else None,
        ct_evidence_uri=ct_evaluation.report_uri if ct_evaluation else None,
    )


def ci_check(validation: CIEvidenceValidation, check_id: str) -> State:
    return "pass" if validation.checks.get(check_id) == "pass" else "blocked"


def state(passed: bool) -> State:
    return "pass" if passed else "blocked"


def is_sha256(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{64}", value) is not None


def valid_iso_timestamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def block_reason(failed_checks: list[str], blockers: list[str]) -> str | None:
    if not failed_checks and not blockers:
        return None
    checks = ", ".join(failed_checks) if failed_checks else "none"
    blocker_text = ", ".join(blockers[:4]) if blockers else "none"
    suffix = "..." if len(blockers) > 4 else ""
    return f"failed checks: {checks}; blockers: {blocker_text}{suffix}"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)
