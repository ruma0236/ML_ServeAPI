from __future__ import annotations

from pathlib import Path

from evm.control_panel.cycle_catalog import build_cycle_catalog, find_cycle
from evm.control_panel.schemas import CycleRun


def example_cycle() -> CycleRun:
    return CycleRun.model_validate_json(
        Path("contracts/control-panel/examples/cycle-run.json").read_text(encoding="utf-8")
    )


def write_cycle(root: Path, name: str, cycle: CycleRun) -> Path:
    path = root / name / "cycle-run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cycle.model_dump_json(indent=2), encoding="utf-8")
    return path


def test_catalog_combines_live_and_deduplicated_history(tmp_path):
    live = example_cycle().model_copy(update={"cycle_id": "cycle-live"})
    history = example_cycle().model_copy(
        update={"cycle_id": "cycle-history", "started_at": "2026-07-01T00:00:00Z"}
    )
    write_cycle(tmp_path, "first", history)
    write_cycle(tmp_path, "duplicate", history)

    catalog = build_cycle_catalog(live, root=tmp_path)

    assert catalog.latest_cycle_id == "cycle-live"
    assert catalog.total == 2
    assert [item.cycle_id for item in catalog.cycles] == ["cycle-live", "cycle-history"]
    assert catalog.cycles[0].live is True
    assert catalog.cycles[1].source_uri


def test_catalog_filters_and_loads_historical_cycle(tmp_path):
    live = example_cycle().model_copy(update={"cycle_id": "cycle-live"})
    history = example_cycle().model_copy(
        update={"cycle_id": "cycle-b7-history", "status": "pass"}
    )
    write_cycle(tmp_path, "history", history)

    catalog = build_cycle_catalog(live, status="pass", query="b7", root=tmp_path)
    loaded = find_cycle("cycle-b7-history", live, root=tmp_path)

    assert catalog.total == 1
    assert catalog.cycles[0].cycle_id == "cycle-b7-history"
    assert loaded is not None
    assert loaded.cycle_id == "cycle-b7-history"


def test_catalog_indexes_lifecycle_cycle_snapshots(tmp_path):
    live = example_cycle().model_copy(update={"cycle_id": "cycle-live"})
    lifecycle = example_cycle().model_copy(update={"cycle_id": "cycle-lifecycle"})
    path = tmp_path / "lifecycle-runs" / "lifecycle-1" / "cycle.snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lifecycle.model_dump_json(indent=2), encoding="utf-8")

    catalog = build_cycle_catalog(live, root=tmp_path)

    assert [item.cycle_id for item in catalog.cycles] == ["cycle-live", "cycle-lifecycle"]
    assert find_cycle("cycle-lifecycle", live, root=tmp_path) == lifecycle
