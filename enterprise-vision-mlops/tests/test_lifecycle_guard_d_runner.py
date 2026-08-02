from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel.lifecycle_guards import (
    LifecycleSideEffect,
    LifecycleSideEffectLedger,
    file_digest,
)
from evm.operations.lifecycle_guard_d_runner import (
    replay_terminal_ledger,
    run_exact_reconciliation_fixture,
)


def completed_entry() -> LifecycleSideEffect:
    return LifecycleSideEffect(
        side_effect_key="a" * 64,
        lifecycle_series_id="series-d",
        lifecycle_run_id="lifecycle-d",
        attempt_id="attempt-d",
        correlation_id="correlation-d",
        stage_id="model_training",
        action="execute_kubernetes_job",
        action_digest="b" * 64,
        state="completed",
        runtime_id="evm-training/job/train-d",
        reserved_at="2026-08-02T00:00:00Z",
        updated_at="2026-08-02T00:01:00Z",
    )


def test_terminal_ledger_replay_is_byte_stable(tmp_path: Path) -> None:
    path = tmp_path / "side_effect_ledger.json"
    path.write_text(
        json.dumps(
            LifecycleSideEffectLedger(
                lifecycle_run_id="lifecycle-d",
                entries=[completed_entry()],
            ).model_dump(mode="json"),
            indent=2,
        ),
        encoding="utf-8",
    )
    before = file_digest(path)

    results = replay_terminal_ledger(tmp_path)

    assert len(results) == 3
    assert len({item["decision_fingerprint"] for item in results}) == 1
    assert file_digest(path) == before


def test_exact_observation_resumes_without_kubectl_mutation(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = run_exact_reconciliation_fixture(
        tmp_path / "exact-replay-001",
        project_root=project_root,
        source_revision="1" * 40,
        wrong_candidate=False,
    )

    assert result["decision"] == "pass"
    assert result["task_state"] == "done"
    assert result["side_effect_state"] == "completed"
    assert result["runtime_id"].startswith("evm-training/job/")
    assert result["mutating_commands"] == []


def test_wrong_observation_blocks_without_kubectl_mutation(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = run_exact_reconciliation_fixture(
        tmp_path / "wrong-replay-001",
        project_root=project_root,
        source_revision="1" * 40,
        wrong_candidate=True,
    )

    assert result["decision"] == "blocked"
    assert "kubernetes_reconciliation_label_identity_mismatch" in result["blockers"]
    assert result["task_state"] == "running"
    assert result["side_effect_state"] == "reserved"
    assert result["runtime_id"] is None
    assert result["mutating_commands"] == []


def test_wrong_observation_fingerprint_ignores_attempt_specific_side_effect_key(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    results = [
        run_exact_reconciliation_fixture(
            tmp_path / f"wrong-replay-{replay}",
            project_root=project_root,
            source_revision="1" * 40,
            wrong_candidate=True,
        )
        for replay in range(1, 4)
    ]

    assert len({item["side_effect_key"] for item in results}) == 3
    assert len({item["decision_fingerprint"] for item in results}) == 1
