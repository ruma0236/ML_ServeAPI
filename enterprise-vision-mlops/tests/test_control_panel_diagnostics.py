from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel.diagnostics import build_control_panel_diagnostics
from evm.control_panel.schemas import CycleRun, RuntimeResourceList


def load_cycle() -> CycleRun:
    return CycleRun.model_validate_json(
        Path("contracts/control-panel/examples/cycle-run.json").read_text(encoding="utf-8")
    )


def test_diagnostics_explain_blocked_and_warn_states_without_duplicate_events(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EVM_CONTROL_PANEL_DIAGNOSTIC_ROOT", str(tmp_path))
    timestamps = iter(["2026-07-12T00:00:00Z", "2026-07-12T00:00:10Z"])
    monkeypatch.setattr("evm.control_panel.diagnostics.utc_now", lambda: next(timestamps))
    resources = RuntimeResourceList(
        resources=[],
        observation_status="live",
        observed_at="2026-07-12T00:00:00Z",
        snapshot_age_seconds=1,
    )

    first = build_control_panel_diagnostics(load_cycle(), resources)
    second = build_control_panel_diagnostics(load_cycle(), resources)

    assert first.status == "blocked"
    assert first.blocked_count > 0
    assert first.warn_count > 0
    assert any(item.scope == "drift" for item in first.diagnostics)
    assert any(item.code == "accuracy<0.7" for item in first.diagnostics)
    assert next(
        item for item in first.diagnostics if item.code == "accuracy<0.7"
    ).evidence_uri
    assert all(item.summary and item.remediation and item.source for item in first.diagnostics)
    assert first.state_digest == second.state_digest
    assert Path(first.snapshot_uri).exists()
    audit_lines = Path(first.audit_uri).read_text(encoding="utf-8").splitlines()
    assert len(audit_lines) == 1
    assert json.loads(audit_lines[0])["state_digest"] == first.state_digest


def test_diagnostics_state_digest_changes_when_source_health_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("EVM_CONTROL_PANEL_DIAGNOSTIC_ROOT", str(tmp_path))
    live = RuntimeResourceList(resources=[], observation_status="live")
    stale = RuntimeResourceList(resources=[], observation_status="stale")

    live_report = build_control_panel_diagnostics(load_cycle(), live)
    stale_report = build_control_panel_diagnostics(load_cycle(), stale)

    assert live_report.state_digest != stale_report.state_digest
    assert stale_report.sources[1].status == "stale"
    assert len(Path(stale_report.audit_uri).read_text(encoding="utf-8").splitlines()) == 2


def test_diagnostics_do_not_duplicate_stage_blockers_as_projected_resource_failures(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EVM_CONTROL_PANEL_DIAGNOSTIC_ROOT", str(tmp_path))
    resources = RuntimeResourceList.model_validate(
        {
            "observation_status": "live",
            "resources": [
                {
                    "resource_id": "evm-platform:Deployment:projected-api",
                    "namespace": "evm-platform",
                    "kind": "Deployment",
                    "name": "projected-api",
                    "node_pool": "projected",
                    "status": "blocked",
                    "observation_source": "cycle_projection",
                },
                {
                    "resource_id": "evm-staging:Pod:live-failed-pod",
                    "namespace": "evm-staging",
                    "kind": "Pod",
                    "name": "live-failed-pod",
                    "node_pool": "docker-desktop",
                    "status": "fail",
                    "reason": "CrashLoopBackOff",
                    "observation_source": "kubernetes_snapshot",
                },
            ],
        }
    )

    report = build_control_panel_diagnostics(load_cycle(), resources)
    resource_ids = {
        item.component for item in report.diagnostics if item.scope == "resource"
    }

    assert "evm-platform:Deployment:projected-api" not in resource_ids
    assert "evm-staging:Pod:live-failed-pod" in resource_ids


def test_diagnostics_return_structured_blocker_when_persistence_fails(
    monkeypatch,
) -> None:
    resources = RuntimeResourceList(resources=[], observation_status="live")
    monkeypatch.setattr(
        "evm.control_panel.diagnostics.persist_diagnostics",
        lambda _report: (_ for _ in ()).throw(OSError("read-only evidence root")),
    )

    report = build_control_panel_diagnostics(load_cycle(), resources, persist=True)

    assert report.status == "blocked"
    assert any(item.code == "diagnostics_persistence_failed" for item in report.diagnostics)
    failure = next(
        item for item in report.diagnostics if item.code == "diagnostics_persistence_failed"
    )
    assert failure.details["error_type"] == "OSError"
    assert "read-only evidence root" in failure.details["error"]
