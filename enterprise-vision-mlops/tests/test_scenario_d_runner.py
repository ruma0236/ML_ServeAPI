from __future__ import annotations

import json
from pathlib import Path

from evm.operations.failure_evidence import OperationalFailureReport, validate_closure
from evm.operations.scenario_d_runner import run_fixture_proof


def test_fixture_runner_writes_valid_readiness_evidence(tmp_path: Path) -> None:
    report_path = run_fixture_proof(
        policy_path=Path("configs/operations/scenario_d_supervision.toml"),
        output_root=tmp_path,
        source_commit="a" * 40,
        source_branch="test-branch",
    )
    report = OperationalFailureReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    fixture_results = json.loads((report_path.parent / "fixture-results.json").read_text())
    claims = json.loads((report_path.parent / "claim-results.json").read_text())
    assert report.status == "blocked"
    assert report.readiness_closure.decision == "passed"
    assert report.live_proof_closure.decision == "not_run"
    assert validate_closure(report, "readiness") == []
    assert fixture_results["passed"] is True
    assert len(fixture_results["results"]) == 13
    assert claims["passed"] is True
    assert (report_path.parent / "evidence-index.json").is_file()
