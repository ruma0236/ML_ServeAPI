from __future__ import annotations

from pathlib import Path

from evm.control_panel.schemas import DataPipelineReadiness, ExperimentPipelineReadiness, OrgContext, State


def approval_status(owner: str | None, blockers: list[str]) -> State:
    if not owner:
        return "blocked"
    return "blocked" if blockers else "pass"


def build_data_readiness(
    *,
    contract_path: Path,
    quality_status: State,
    lineage_exists: bool,
    replay_ready: bool,
    source_policy_uri: str,
    quality_report_uri: str | None,
    lineage_uri: str | None,
    backfill_window: str,
    org_context: OrgContext | None,
) -> DataPipelineReadiness:
    blockers = data_readiness_blockers(
        contract_status="pass" if contract_path.exists() else "blocked",
        quality_status=quality_status,
        lineage_status="pass" if lineage_exists else "blocked",
        replay_ready=replay_ready,
    )
    data_owner = org_context.data_owner if org_context else None
    return DataPipelineReadiness(
        contract_status="pass" if contract_path.exists() else "blocked",
        quality_status=quality_status,
        lineage_status="pass" if lineage_exists else "blocked",
        replay_ready=replay_ready,
        source_policy_uri=source_policy_uri,
        quality_report_uri=quality_report_uri,
        lineage_uri=lineage_uri,
        backfill_window=backfill_window,
        owner_approval_required=True,
        owner_approval_status=approval_status(data_owner, blockers),
        owner_approval_actor=data_owner,
        blockers=blockers,
    )


def build_experiment_readiness(
    *,
    registry_exists: bool,
    blockers: list[str],
    experiment_uri: str,
    model_card_uri: str | None,
    evaluation_report_uri: str | None,
    org_context: OrgContext | None,
) -> ExperimentPipelineReadiness:
    evaluation_status: State = "blocked" if blockers else ("pass" if registry_exists else "blocked")
    registry_status: State = "pass" if registry_exists else "blocked"
    readiness_blockers = experiment_readiness_blockers(
        tracking_status="pass" if registry_exists else "blocked",
        evaluation_status=evaluation_status,
        registry_status=registry_status,
        promotion_ready=not blockers and registry_exists,
        extra_blockers=blockers,
    )
    model_owner = org_context.model_owner if org_context else None
    return ExperimentPipelineReadiness(
        tracking_status="pass" if registry_exists else "blocked",
        evaluation_status=evaluation_status,
        registry_status=registry_status,
        promotion_ready=not blockers and registry_exists,
        experiment_uri=experiment_uri,
        model_card_uri=model_card_uri,
        evaluation_report_uri=evaluation_report_uri,
        rollback_ready=registry_exists,
        owner_approval_required=True,
        owner_approval_status=approval_status(model_owner, readiness_blockers),
        owner_approval_actor=model_owner,
        blockers=readiness_blockers,
    )


def data_readiness_blockers(
    *,
    contract_status: State,
    quality_status: State,
    lineage_status: State,
    replay_ready: bool,
) -> list[str]:
    blockers: list[str] = []
    if contract_status != "pass":
        blockers.append("source_policy_or_contract_missing")
    if quality_status not in {"pass", "done"}:
        blockers.append("quality_report_not_passing")
    if lineage_status != "pass":
        blockers.append("lineage_evidence_missing")
    if not replay_ready:
        blockers.append("replay_or_backfill_not_ready")
    return blockers


def experiment_readiness_blockers(
    *,
    tracking_status: State,
    evaluation_status: State,
    registry_status: State,
    promotion_ready: bool,
    extra_blockers: list[str] | None = None,
) -> list[str]:
    blockers = list(extra_blockers or [])
    if tracking_status != "pass":
        blockers.append("mlflow_tracking_missing")
    if evaluation_status != "pass":
        blockers.append("evaluation_not_passing")
    if registry_status != "pass":
        blockers.append("registry_artifact_missing")
    if not promotion_ready:
        blockers.append("owner_or_gate_promotion_blocked")
    return sorted(set(blockers))
