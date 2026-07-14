from __future__ import annotations

from pathlib import Path

import pytest

from apps.api import control_panel as control_panel_api
from evm.control_panel import model_candidates
from evm.control_panel.model_candidates import (
    ModelCandidateCatalog,
    ModelCandidateRecord,
    ModelCandidateSelectionRequest,
    get_model_selection,
    select_model_candidate,
)
from evm.control_panel.schemas import CycleRun


def example_cycle() -> CycleRun:
    return CycleRun.model_validate_json(
        Path("contracts/control-panel/examples/cycle-run.json").read_text(encoding="utf-8")
    )


def selectable_candidate() -> ModelCandidateRecord:
    return ModelCandidateRecord(
        candidate_key="candidate-ready",
        candidate_id="effnet-b0-ready",
        cycle_id="cycle-ready",
        lifecycle_run_id="lifecycle-ready",
        matrix_id="matrix-ready",
        architecture="efficientnet-b0",
        framework="torch",
        dataset_id="visa",
        dataset_version="visa-v1",
        model_version="1",
        resource_profile="gpu-local",
        status="pass",
        metrics={"accuracy": 0.95, "f1": 0.81, "auroc": 0.98},
        artifact_uri="F:/artifacts/model.pt",
        artifact_digest="a" * 64,
        readiness_decision="ready",
        ct_decision="pass",
        source_commit="b" * 40,
        environment="staging",
        selectable=True,
        blockers=[],
        started_at="2026-07-14T00:00:00Z",
    )


def test_catalog_keeps_unverified_matrix_candidates_visible_but_blocked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVM_LIFECYCLE_RUN_ROOT", str(tmp_path / "runs"))
    catalog = model_candidates.build_model_candidate_catalog(example_cycle(), root=tmp_path / "history")

    assert catalog.total == 2
    assert catalog.selectable == 0
    assert all(not candidate.selectable for candidate in catalog.candidates)
    assert all("artifact_readiness_not_ready" in candidate.blockers for candidate in catalog.candidates)


def test_candidate_selection_is_immutable_and_auditable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVM_MODEL_SELECTION_ROOT", str(tmp_path / "selections"))
    candidate = selectable_candidate()
    monkeypatch.setattr(
        model_candidates,
        "build_model_candidate_catalog",
        lambda _live, root=None, limit=1000: ModelCandidateCatalog(
            candidates=[candidate],
            total=1,
            selectable=1,
        ),
    )
    selection = select_model_candidate(
        example_cycle(),
        candidate.candidate_key,
        ModelCandidateSelectionRequest(
            actor="release-operator",
            reason="Select verified candidate for staging promotion",
        ),
    )

    assert selection.candidate_id == candidate.candidate_id
    assert selection.artifact_digest == candidate.artifact_digest
    assert get_model_selection(selection.selection_id) == selection
    assert Path(selection.audit_uri).is_file()


def test_api_candidate_catalog_reuses_expensive_evidence_scan(monkeypatch) -> None:
    cycle = example_cycle()
    candidate = selectable_candidate()
    calls = 0

    def build_catalog(_live_cycle, *, limit=1000):
        nonlocal calls
        calls += 1
        return ModelCandidateCatalog(candidates=[candidate], total=1, selectable=1)

    monkeypatch.setenv("EVM_MODEL_CANDIDATE_CACHE_TTL_SECONDS", "30")
    monkeypatch.setattr(control_panel_api, "cycle_snapshot", lambda: cycle)
    monkeypatch.setattr(control_panel_api, "build_model_candidate_catalog", build_catalog)
    control_panel_api.invalidate_model_candidate_cache()

    first = control_panel_api.model_candidate_catalog_snapshot(limit=1)
    second = control_panel_api.model_candidate_catalog_snapshot(limit=1)

    assert calls == 1
    assert first.candidates == second.candidates == [candidate]
