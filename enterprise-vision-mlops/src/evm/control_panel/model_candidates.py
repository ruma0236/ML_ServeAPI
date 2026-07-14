from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field

from evm.control_panel.cycle_catalog import candidate_cycle_paths, load_cycle
from evm.control_panel.lifecycle_runs import read_runs, utc_now
from evm.control_panel.readiness_evaluator import (
    canonical_evidence_uri,
    payload_sha256,
    runtime_path,
)
from evm.control_panel.schemas import ContractModel, CycleRun, ModelCandidate


class ModelCandidateRecord(ContractModel):
    candidate_key: str
    candidate_id: str
    cycle_id: str
    lifecycle_run_id: str | None = None
    matrix_id: str
    architecture: str
    framework: str
    dataset_id: str
    dataset_version: str
    model_version: str
    resource_profile: str
    status: str
    metrics: dict[str, float] = Field(default_factory=dict)
    metric_thresholds: dict[str, float] = Field(default_factory=dict)
    mlflow_run_uri: str | None = None
    artifact_uri: str | None = None
    artifact_digest: str | None = None
    readiness_decision: str
    ct_decision: str
    source_commit: str | None = None
    environment: str | None = None
    selectable: bool
    blockers: list[str] = Field(default_factory=list)
    started_at: str
    live: bool = False


class ModelCandidateCatalog(ContractModel):
    schema_version: Literal["evm.model_candidate_catalog.v1"] = "evm.model_candidate_catalog.v1"
    candidates: list[ModelCandidateRecord] = Field(default_factory=list)
    total: int = Field(ge=0)
    selectable: int = Field(ge=0)


class ModelCandidateSelectionRequest(ContractModel):
    actor: str = Field(min_length=2)
    reason: str = Field(min_length=8)


class ModelCandidateSelection(ContractModel):
    schema_version: Literal["evm.model_candidate_selection.v1"] = "evm.model_candidate_selection.v1"
    selection_id: str
    candidate_key: str
    candidate_id: str
    cycle_id: str
    lifecycle_run_id: str | None = None
    matrix_id: str
    dataset_version: str
    artifact_uri: str
    artifact_digest: str
    metrics: dict[str, float] = Field(default_factory=dict)
    actor: str
    reason: str
    created_at: str
    status: Literal["selected"] = "selected"
    audit_uri: str


class ModelCandidateSelectionBlocked(RuntimeError):
    def __init__(self, blockers: list[str]):
        self.blockers = sorted(set(blockers))
        super().__init__(", ".join(self.blockers))


def build_model_candidate_catalog(
    live_cycle: CycleRun,
    *,
    root: Path | None = None,
    limit: int = 200,
) -> ModelCandidateCatalog:
    cycles: dict[str, tuple[CycleRun, bool]] = {live_cycle.cycle_id: (live_cycle, True)}
    for path in candidate_cycle_paths(root):
        cycle = load_cycle(path)
        if cycle is not None and cycle.cycle_id not in cycles:
            cycles[cycle.cycle_id] = (cycle, False)

    runs = read_runs().runs
    runs_by_cycle = {run.cycle_id: run for run in runs if run.cycle_id}
    runs_by_id = {run.run_id: run for run in runs}
    records: list[ModelCandidateRecord] = []
    for cycle, live in cycles.values():
        if cycle.model_matrix is None:
            continue
        linked_run = runs_by_cycle.get(cycle.cycle_id)
        if linked_run is None and cycle.ct_evaluation is not None:
            linked_run = runs_by_id.get(cycle.ct_evaluation.lifecycle_run_id)
        for candidate in cycle.model_matrix.candidates:
            records.append(candidate_record(cycle, candidate, linked_run, live=live))

    records.sort(key=lambda item: (not item.live, item.started_at), reverse=False)
    live_records = [item for item in records if item.live]
    historical = sorted(
        (item for item in records if not item.live),
        key=lambda item: item.started_at,
        reverse=True,
    )
    selected = (live_records + historical)[: max(1, min(limit, 1000))]
    return ModelCandidateCatalog(
        candidates=selected,
        total=len(records),
        selectable=sum(item.selectable for item in records),
    )


def candidate_record(cycle: CycleRun, candidate: ModelCandidate, linked_run, *, live: bool) -> ModelCandidateRecord:
    readiness = cycle.readiness_evaluation
    ct = cycle.ct_evaluation
    model_check = next(
        (check for check in readiness.checks if check.check_id == "model_artifact"),
        None,
    ) if readiness else None
    artifact_uri = candidate.artifact_uri or (model_check.evidence_uri if model_check else None)
    artifact_digest = None
    if model_check:
        artifact_digest = model_check.evidence_digest or str(
            model_check.observed.get("actual_sha256")
            or model_check.observed.get("model_sha256")
            or ""
        ) or None
    metrics = {metric.name: metric.value for metric in candidate.metrics}
    thresholds = {
        metric.name: metric.threshold
        for metric in candidate.metrics
        if metric.threshold is not None
    }
    blockers = list(candidate.promotion_blockers)
    if candidate.status != "pass":
        blockers.append(f"candidate_status_{candidate.status}")
    if readiness is None or readiness.decision != "ready":
        blockers.append("artifact_readiness_not_ready")
    elif readiness.candidate_id != candidate.candidate_id:
        blockers.append("candidate_not_selected_by_readiness")
    if not artifact_uri:
        blockers.append("model_artifact_uri_missing")
    if not artifact_digest:
        blockers.append("model_artifact_digest_missing")
    if ct is None or ct.decision != "pass":
        blockers.append("ct_evaluation_not_pass")
    elif ct.candidate_id != candidate.candidate_id:
        blockers.append("ct_candidate_mismatch")
    if ct and artifact_digest and ct.model_sha256 and ct.model_sha256 != artifact_digest:
        blockers.append("ct_model_digest_mismatch")
    blockers = sorted(set(blockers))
    key_material = {
        "cycle_id": cycle.cycle_id,
        "candidate_id": candidate.candidate_id,
        "artifact_digest": artifact_digest or "missing",
    }
    return ModelCandidateRecord(
        candidate_key=f"candidate-{payload_sha256(key_material)[:20]}",
        candidate_id=candidate.candidate_id,
        cycle_id=cycle.cycle_id,
        lifecycle_run_id=(linked_run.run_id if linked_run else (ct.lifecycle_run_id if ct else None)),
        matrix_id=cycle.model_matrix.matrix_id,
        architecture=candidate.architecture,
        framework=candidate.framework,
        dataset_id=cycle.dataset.dataset_id,
        dataset_version=candidate.dataset_version,
        model_version=cycle.model.version,
        resource_profile=candidate.resource_profile,
        status=candidate.status,
        metrics=metrics,
        metric_thresholds=thresholds,
        mlflow_run_uri=candidate.run_uri,
        artifact_uri=artifact_uri,
        artifact_digest=artifact_digest,
        readiness_decision=readiness.decision if readiness else "missing",
        ct_decision=ct.decision if ct else "missing",
        source_commit=linked_run.source_commit if linked_run else None,
        environment=cycle.environment.tier if cycle.environment else None,
        selectable=not blockers,
        blockers=blockers,
        started_at=cycle.started_at,
        live=live,
    )


def select_model_candidate(
    live_cycle: CycleRun,
    candidate_key: str,
    request: ModelCandidateSelectionRequest,
    *,
    root: Path | None = None,
) -> ModelCandidateSelection:
    catalog = build_model_candidate_catalog(live_cycle, root=root, limit=1000)
    candidate = next(
        (item for item in catalog.candidates if item.candidate_key == candidate_key),
        None,
    )
    if candidate is None:
        raise KeyError(candidate_key)
    if not candidate.selectable or not candidate.artifact_uri or not candidate.artifact_digest:
        raise ModelCandidateSelectionBlocked(candidate.blockers or ["candidate_not_selectable"])
    created_at = utc_now()
    material = {
        "candidate_key": candidate.candidate_key,
        "cycle_id": candidate.cycle_id,
        "artifact_digest": candidate.artifact_digest,
        "actor": request.actor,
        "created_at": created_at,
    }
    selection_id = f"selection-{payload_sha256(material)[:20]}"
    path = selection_root() / selection_id / "model_selection.json"
    selection = ModelCandidateSelection(
        selection_id=selection_id,
        candidate_key=candidate.candidate_key,
        candidate_id=candidate.candidate_id,
        cycle_id=candidate.cycle_id,
        lifecycle_run_id=candidate.lifecycle_run_id,
        matrix_id=candidate.matrix_id,
        dataset_version=candidate.dataset_version,
        artifact_uri=candidate.artifact_uri,
        artifact_digest=candidate.artifact_digest,
        metrics=candidate.metrics,
        actor=request.actor,
        reason=request.reason,
        created_at=created_at,
        audit_uri=canonical_evidence_uri(path),
    )
    atomic_write_json(path, selection.model_dump(mode="json"))
    atomic_write_json(selection_root() / "latest_model_selection.json", selection.model_dump(mode="json"))
    return selection


def get_model_selection(selection_id: str) -> ModelCandidateSelection:
    path = selection_root() / selection_id / "model_selection.json"
    if not path.is_file():
        raise KeyError(selection_id)
    return ModelCandidateSelection.model_validate_json(path.read_text(encoding="utf-8-sig"))


def selection_root() -> Path:
    configured = os.getenv(
        "EVM_MODEL_SELECTION_ROOT",
        "/app/artifacts/w8/model-selections",
    )
    return runtime_path(configured)


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
