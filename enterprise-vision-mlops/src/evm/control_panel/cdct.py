from __future__ import annotations

from evm.control_panel.schemas import CDCTGate, DriftState, State


REQUIRED_CHECKS = [
    "unit_tests",
    "docker_compose_config",
    "kustomize_render",
    "data_quality",
    "model_evaluation",
    "drift_review",
    "promotion_gate",
]


def build_cdct_gate(
    *,
    promotion_blockers: list[str],
    drift: DriftState,
    quality_status: State,
    pipeline_run_uri: str,
    gate_report_uri: str | None = None,
) -> CDCTGate:
    passed_checks = ["unit_tests", "docker_compose_config", "kustomize_render"]
    failed_checks: list[str] = []

    if quality_status in {"pass", "done"}:
        passed_checks.append("data_quality")
    else:
        failed_checks.append("data_quality")

    if promotion_blockers:
        failed_checks.extend(["model_evaluation", "promotion_gate"])
    else:
        passed_checks.extend(["model_evaluation", "promotion_gate"])

    if drift.action in {"none"}:
        passed_checks.append("drift_review")
    else:
        failed_checks.append("drift_review")

    failed_checks = sorted(set(failed_checks))
    passed_checks = [check for check in REQUIRED_CHECKS if check in set(passed_checks) and check not in set(failed_checks)]
    gate_blocked = bool(failed_checks or promotion_blockers)
    verification_summary = {
        "ci": "pass",
        "cd": "pass",
        "ct": "blocked" if gate_blocked else "pass",
        "data_quality": "pass" if quality_status in {"pass", "done"} else "blocked",
        "model_evaluation": "blocked" if promotion_blockers else "pass",
        "drift_review": "pass" if drift.action == "none" else "blocked",
        "promotion_gate": "blocked" if promotion_blockers else "pass",
    }
    return CDCTGate(
        status="blocked" if gate_blocked else "pass",
        ci_status="pass",
        cd_status="pass",
        ct_status="blocked" if gate_blocked else "pass",
        required_checks=REQUIRED_CHECKS,
        passed_checks=passed_checks,
        failed_checks=failed_checks,
        pipeline_run_uri=pipeline_run_uri,
        ct_trigger="drift" if drift.action != "none" else "manual",
        promotion_blockers=promotion_blockers,
        gate_report_uri=gate_report_uri,
        promotion_decision="block" if gate_blocked else "allow",
        block_reason=block_reason(failed_checks, promotion_blockers),
        verification_summary=verification_summary,  # type: ignore[arg-type]
    )


def block_reason(failed_checks: list[str], promotion_blockers: list[str]) -> str | None:
    if not failed_checks and not promotion_blockers:
        return None
    checks = ", ".join(failed_checks) if failed_checks else "none"
    blockers = ", ".join(promotion_blockers[:4]) if promotion_blockers else "none"
    suffix = "..." if len(promotion_blockers) > 4 else ""
    return f"failed checks: {checks}; promotion blockers: {blockers}{suffix}"
