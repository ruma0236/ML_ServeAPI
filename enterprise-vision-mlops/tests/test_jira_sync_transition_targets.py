from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DEV = Path(__file__).resolve().parents[1] / "scripts" / "dev"
sys.path.insert(0, str(SCRIPTS_DEV))

from jira_sync import transition_targets_for_status  # noqa: E402


def test_transition_targets_include_korean_workflow_names(monkeypatch) -> None:
    monkeypatch.delenv("JIRA_STATUS_TRANSITION_MAP", raising=False)

    assert "진행 중" in transition_targets_for_status("In Progress")
    assert "완료" in transition_targets_for_status("Done")


def test_local_transition_map_extends_defaults(monkeypatch) -> None:
    monkeypatch.setenv(
        "JIRA_STATUS_TRANSITION_MAP",
        '{"In Progress":["custom-active","진행 중"]}',
    )

    targets = transition_targets_for_status("In Progress")

    assert targets == ["in progress", "진행 중", "custom-active"]
