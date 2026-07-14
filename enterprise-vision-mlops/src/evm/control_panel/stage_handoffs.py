from __future__ import annotations

from typing import Literal

from pydantic import Field

from evm.control_panel.lifecycle_runs import LifecycleRun, read_runs
from evm.control_panel.schemas import ContractModel


StageHandoffBucket = Literal[
    "ready",
    "active",
    "blocked",
    "completed",
    "consumed",
    "cancelled",
    "waiting",
]


class StageHandoff(ContractModel):
    handoff_id: str
    run_id: str
    run_state: str
    run_version: int = Field(ge=1)
    execution_mode: Literal["automatic", "stepwise"]
    profile_id: str
    profile_version: int = Field(ge=1)
    stage_id: str
    stage_label: str
    stage_state: str
    runtime: str
    bucket: StageHandoffBucket
    previous_stage_id: str | None = None
    next_stage_id: str | None = None
    progress: float = Field(ge=0, le=1)
    eligible_actions: list[Literal["continue", "retry", "inspect"]] = Field(default_factory=list)
    input_refs: dict[str, str] = Field(default_factory=dict)
    output_refs: dict[str, str] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    detail: str | None = None
    updated_at: str


class StageHandoffCatalog(ContractModel):
    schema_version: Literal["evm.stage_handoff_catalog.v1"] = "evm.stage_handoff_catalog.v1"
    handoffs: list[StageHandoff] = Field(default_factory=list)
    total: int = Field(ge=0)
    ready: int = Field(ge=0)
    active: int = Field(ge=0)
    blocked: int = Field(ge=0)


def build_stage_handoff_catalog(
    *,
    run_id: str | None = None,
    limit: int = 250,
) -> StageHandoffCatalog:
    runs = read_runs().runs
    if run_id:
        runs = [run for run in runs if run.run_id == run_id]
    handoffs = [
        build_handoff(run, index)
        for run in runs
        for index in range(len(run.stages))
    ]
    rank = {"ready": 0, "active": 1, "blocked": 2, "completed": 3, "consumed": 4, "cancelled": 5, "waiting": 6}
    handoffs.sort(key=lambda item: item.updated_at, reverse=True)
    handoffs.sort(key=lambda item: rank[item.bucket])
    selected = handoffs[: max(1, min(limit, 1000))]
    return StageHandoffCatalog(
        handoffs=selected,
        total=len(handoffs),
        ready=sum(item.bucket == "ready" for item in handoffs),
        active=sum(item.bucket == "active" for item in handoffs),
        blocked=sum(item.bucket == "blocked" for item in handoffs),
    )


def build_handoff(run: LifecycleRun, index: int) -> StageHandoff:
    stage = run.stages[index]
    previous = run.stages[index - 1] if index else None
    following = run.stages[index + 1] if index + 1 < len(run.stages) else None
    actions: list[Literal["continue", "retry", "inspect"]] = ["inspect"]
    if (
        run.execution_mode == "stepwise"
        and run.state == "paused"
        and run.current_stage == stage.stage_id
        and stage.state == "not_started"
    ):
        actions.insert(0, "continue")
    if (
        run.state in {"failed", "blocked"}
        and run.current_stage == stage.stage_id
        and stage.state in {"failed", "blocked"}
        and stage.attempt < stage.max_attempts
    ):
        actions.insert(0, "retry")
    return StageHandoff(
        handoff_id=f"{run.run_id}:{stage.stage_id}",
        run_id=run.run_id,
        run_state=run.state,
        run_version=run.version,
        execution_mode=run.execution_mode,
        profile_id=run.profile_id,
        profile_version=run.profile_version,
        stage_id=stage.stage_id,
        stage_label=stage.label,
        stage_state=stage.state,
        runtime=stage.runtime,
        bucket=handoff_bucket(run, stage.stage_id, stage.state, following.state if following else None),
        previous_stage_id=previous.stage_id if previous else None,
        next_stage_id=following.stage_id if following else None,
        progress=stage.progress,
        eligible_actions=actions,
        input_refs=stage_input_refs(run, previous),
        output_refs=stage_output_refs(run, stage.stage_id, stage.evidence_uri),
        blockers=stage.blockers,
        detail=stage.detail,
        updated_at=run.updated_at,
    )


def handoff_bucket(
    run: LifecycleRun,
    stage_id: str,
    stage_state: str,
    next_state: str | None,
) -> StageHandoffBucket:
    if run.state == "paused" and run.current_stage == stage_id and stage_state == "not_started":
        return "ready"
    if stage_state in {"queued", "running", "waiting_approval"}:
        return "active"
    if stage_state in {"blocked", "failed"}:
        return "blocked"
    if stage_state == "cancelled":
        return "cancelled"
    if stage_state in {"completed", "skipped"}:
        if next_state in {"queued", "running", "waiting_approval", "completed", "skipped"}:
            return "consumed"
        return "completed"
    return "waiting"


def stage_input_refs(run: LifecycleRun, previous) -> dict[str, str]:
    refs = {
        "profile_snapshot": run.profile_snapshot_uri,
        "effective_config_digest": run.effective_config_digest,
    }
    if run.source_commit:
        refs["source_commit"] = run.source_commit
    if previous and previous.evidence_uri:
        refs["previous_stage_evidence"] = previous.evidence_uri
    return refs


def stage_output_refs(
    run: LifecycleRun,
    stage_id: str,
    evidence_uri: str | None,
) -> dict[str, str]:
    refs: dict[str, str] = {}
    if evidence_uri:
        refs["stage_evidence"] = evidence_uri
    mapped = {
        "data_pipeline": ("cycle_snapshot", run.cycle_snapshot_uri),
        "model_training": ("model_matrix", run.model_matrix_uri),
        "model_evaluation": ("model_matrix", run.model_matrix_uri),
        "artifact_readiness": ("readiness", run.readiness_uri),
        "ci_ct_gate": ("ct_evaluation", run.ct_evaluation_uri),
        "deployment": ("deployment_intent", run.deployment_intent_id),
    }.get(stage_id)
    if mapped and mapped[1]:
        refs[mapped[0]] = mapped[1]
    return refs
