from __future__ import annotations

import os
from pathlib import Path

from evm.control_panel.readiness_evaluator import canonical_evidence_uri, runtime_path
from evm.control_panel.schemas import CycleRun, CycleRunList, CycleRunSummary, EnvironmentTier, State


CYCLE_FILENAMES = {
    "cycle-run.json",
    "cycle_run.json",
    "cycle_run_latest.json",
    "cycle-run-live.json",
    "cycle-run-final.json",
    "cycle-run-post-ci.json",
    "cycle_run_http.json",
    "cycle_run_final.json",
    "cycle.snapshot.json",
}


def cycle_history_root() -> Path:
    configured = os.getenv(
        "EVM_CONTROL_PANEL_CYCLE_HISTORY_ROOT",
        "/app/artifacts/w7",
    )
    root = runtime_path(configured)
    if root.exists():
        return root
    host_root = Path(
        os.getenv(
            "EVM_HOST_ARTIFACTS_ROOT",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts",
        )
    )
    return host_root / "w7"


def candidate_cycle_paths(root: Path | None = None) -> list[Path]:
    selected_root = root or cycle_history_root()
    if not selected_root.exists():
        return []
    limit = max(1, int(os.getenv("EVM_CONTROL_PANEL_CATALOG_SCAN_LIMIT", "500")))
    candidates = [
        path
        for path in selected_root.rglob("*.json")
        if path.name.lower() in CYCLE_FILENAMES
    ]
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def load_cycle(path: Path) -> CycleRun | None:
    try:
        return CycleRun.model_validate_json(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def summarize_cycle(cycle: CycleRun, *, source_uri: str | None, live: bool) -> CycleRunSummary:
    progress = (
        sum(stage.progress for stage in cycle.stages) / len(cycle.stages)
        if cycle.stages
        else 0.0
    )
    return CycleRunSummary(
        cycle_id=cycle.cycle_id,
        status=cycle.status,
        started_at=cycle.started_at,
        finished_at=cycle.finished_at,
        dataset_id=cycle.dataset.dataset_id,
        dataset_version=cycle.dataset.version,
        model_name=cycle.model.model_name,
        model_version=cycle.model.version,
        model_stage=cycle.model.stage,
        environment=cycle.environment.tier if cycle.environment else None,
        owner_issue=cycle.owner_issue,
        stage_count=len(cycle.stages),
        progress=progress,
        source_uri=source_uri,
        live=live,
    )


def build_cycle_catalog(
    live_cycle: CycleRun,
    *,
    status: State | None = None,
    environment: EnvironmentTier | None = None,
    query: str | None = None,
    limit: int = 50,
    root: Path | None = None,
) -> CycleRunList:
    summaries: dict[str, CycleRunSummary] = {
        live_cycle.cycle_id: summarize_cycle(live_cycle, source_uri=None, live=True)
    }
    for path in candidate_cycle_paths(root):
        cycle = load_cycle(path)
        if cycle is None or cycle.cycle_id in summaries:
            continue
        summaries[cycle.cycle_id] = summarize_cycle(
            cycle,
            source_uri=canonical_evidence_uri(path),
            live=False,
        )

    normalized_query = (query or "").strip().lower()
    selected = [
        summary
        for summary in summaries.values()
        if (status is None or summary.status == status)
        and (environment is None or summary.environment == environment)
        and (
            not normalized_query
            or normalized_query
            in " ".join(
                [
                    summary.cycle_id,
                    summary.dataset_id,
                    summary.dataset_version,
                    summary.model_name,
                    summary.model_version,
                    summary.owner_issue,
                ]
            ).lower()
        )
    ]
    selected.sort(key=lambda item: (not item.live, item.started_at), reverse=False)
    live_items = [item for item in selected if item.live]
    history_items = sorted(
        (item for item in selected if not item.live),
        key=lambda item: item.started_at,
        reverse=True,
    )
    ordered = (live_items + history_items)[: max(1, min(limit, 200))]
    return CycleRunList(
        cycles=ordered,
        latest_cycle_id=live_cycle.cycle_id,
        selected_cycle_id=live_cycle.cycle_id,
        total=len(selected),
    )


def find_cycle(cycle_id: str, live_cycle: CycleRun, *, root: Path | None = None) -> CycleRun | None:
    if cycle_id == live_cycle.cycle_id:
        return live_cycle
    for path in candidate_cycle_paths(root):
        cycle = load_cycle(path)
        if cycle is not None and cycle.cycle_id == cycle_id:
            return cycle
    return None
