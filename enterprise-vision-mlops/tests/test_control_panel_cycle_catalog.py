from __future__ import annotations

from pathlib import Path

from apps.api import control_panel as control_panel_api
from evm.control_panel import cycle_catalog
from evm.control_panel.cycle_catalog import build_cycle_catalog, find_cycle
from evm.control_panel.schemas import CycleRun, CycleRunList


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


def test_api_catalog_reuses_expensive_history_scan(monkeypatch) -> None:
    cycle = example_cycle().model_copy(update={"cycle_id": "cycle-live"})
    expected = CycleRunList(cycles=[], latest_cycle_id=cycle.cycle_id, total=0)
    calls = 0

    def build_catalog(_live_cycle, **_filters):
        nonlocal calls
        calls += 1
        return expected

    monkeypatch.setenv("EVM_CYCLE_CATALOG_CACHE_TTL_SECONDS", "30")
    monkeypatch.setattr(control_panel_api, "cycle_snapshot", lambda: cycle)
    monkeypatch.setattr(control_panel_api, "build_cycle_catalog", build_catalog)
    control_panel_api.invalidate_cycle_catalog_cache()

    first = control_panel_api.cycle_catalog_snapshot(limit=100)
    second = control_panel_api.cycle_catalog_snapshot(limit=100)

    assert calls == 1
    assert first == second == expected


def test_detail_lookup_reuses_catalog_history_index(tmp_path, monkeypatch) -> None:
    live = example_cycle().model_copy(update={"cycle_id": "cycle-live"})
    history = example_cycle().model_copy(update={"cycle_id": "cycle-history"})
    write_cycle(tmp_path, "history", history)
    calls = 0
    original_load_cycle = cycle_catalog.load_cycle

    def count_loads(path: Path):
        nonlocal calls
        calls += 1
        return original_load_cycle(path)

    monkeypatch.setattr(cycle_catalog, "load_cycle", count_loads)
    cycle_catalog.invalidate_cycle_history_cache()

    build_cycle_catalog(live, root=tmp_path)
    loaded = find_cycle(history.cycle_id, live, root=tmp_path)

    assert loaded == history
    assert calls == 1
