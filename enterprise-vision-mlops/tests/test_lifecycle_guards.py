from __future__ import annotations

import json
from pathlib import Path

import pytest

from evm.control_panel.lifecycle_guards import (
    LifecycleGuardBlocked,
    LifecycleGuardState,
    LifecycleSideEffectLedger,
    complete_side_effect,
    dispatch_lifecycle_guard,
    reserve_side_effect,
    seal_lifecycle_guard_artifacts,
)


SOURCE_COMMIT = "a" * 40


def seal_fixture(tmp_path: Path):
    source = tmp_path / "source.jsonl"
    split = tmp_path / "split.json"
    profile = tmp_path / "profile.json"
    airflow = tmp_path / "airflow.json"
    model = tmp_path / "model.json"
    source.write_text('{"sample_id":"visa-1"}\n', encoding="utf-8")
    split.write_text('{"identity_sha256":"split-v1"}', encoding="utf-8")
    profile.write_text(
        json.dumps(
            {
                "data": {
                    "source_manifest_uri": str(source),
                    "split_manifest_uri": str(split),
                    "split_manifest_sha256": "split-v1",
                },
                "gates": {
                    "target_environment": "staging",
                    "target_namespace": "evm-staging",
                    "approval_policy": "two_person",
                },
            }
        ),
        encoding="utf-8",
    )
    airflow.write_text('{"pipelines":{}}', encoding="utf-8")
    model.write_text(
        '{"product":{"target_deployment":"evm-b0-staging"}}',
        encoding="utf-8",
    )
    envelope = seal_lifecycle_guard_artifacts(
        directory=tmp_path,
        run_id="lifecycle-guard-test",
        profile_id="visa-b0",
        profile_version=1,
        profile_digest="profile-digest",
        effective_config_digest="effective-digest",
        source_commit=SOURCE_COMMIT,
        source_branch="test/guard",
        profile_snapshot_uri=str(profile),
        airflow_config_uri=str(airflow),
        model_config_uri=str(model),
    )
    identity = {
        "run_id": envelope.lifecycle_run_id,
        "profile_id": envelope.profile_id,
        "profile_version": envelope.profile_version,
        "profile_digest": envelope.profile_digest,
        "effective_config_digest": envelope.effective_config_digest,
        "source_commit": envelope.source_commit,
        "profile_snapshot_uri": str(profile),
        "airflow_config_uri": str(airflow),
        "model_config_uri": str(model),
        "lifecycle_series_id": envelope.lifecycle_series_id,
        "attempt_id": envelope.attempt_id,
        "correlation_id": envelope.correlation_id,
    }
    return envelope, identity, source


def test_identity_envelope_is_sealed_and_guard_dispatch_is_auditable(tmp_path: Path) -> None:
    envelope, identity, _ = seal_fixture(tmp_path)

    decision = dispatch_lifecycle_guard(
        directory=tmp_path,
        stage_id="model_training",
        transition="queued",
        run_identity=identity,
    )

    assert decision.decision == "pass"
    assert decision.authorities == ["D", "E", "C"]
    assert decision.identity_digest == envelope.envelope_digest
    assert len(envelope.trace_id) == 32
    assert envelope.traceparent.startswith(f"00-{envelope.trace_id}-")
    state = LifecycleGuardState.model_validate_json(
        (tmp_path / "guard_state.json").read_text(encoding="utf-8")
    )
    assert state.current_decision == "pass"
    assert len(state.decisions) == 2


def test_tampered_source_manifest_blocks_dependent_stage_fail_closed(tmp_path: Path) -> None:
    _, identity, source = seal_fixture(tmp_path)
    source.write_text('{"sample_id":"tampered"}\n', encoding="utf-8")

    with pytest.raises(LifecycleGuardBlocked) as exc_info:
        dispatch_lifecycle_guard(
            directory=tmp_path,
            stage_id="model_training",
            transition="running",
            run_identity=identity,
        )

    assert exc_info.value.blockers == ["identity_source_manifest_digest_mismatch"]
    state = LifecycleGuardState.model_validate_json(
        (tmp_path / "guard_state.json").read_text(encoding="utf-8")
    )
    assert state.current_decision == "blocked"
    assert state.current_authorities == ["D", "E", "C"]


@pytest.mark.parametrize(
    ("runtime_revisions", "expected"),
    [
        (
            {"lifecycle_worker": SOURCE_COMMIT, "kubernetes_observer": None},
            "runtime_revision_unavailable:kubernetes_observer",
        ),
        (
            {"lifecycle_worker": "b" * 40, "kubernetes_observer": SOURCE_COMMIT},
            "runtime_revision_mismatch:lifecycle_worker",
        ),
    ],
)
def test_runtime_revision_gate_requires_exact_declared_components(
    tmp_path: Path,
    runtime_revisions: dict[str, str | None],
    expected: str,
) -> None:
    _, identity, _ = seal_fixture(tmp_path)

    with pytest.raises(LifecycleGuardBlocked) as exc_info:
        dispatch_lifecycle_guard(
            directory=tmp_path,
            stage_id="data_pipeline",
            transition="queue",
            run_identity=identity,
            runtime_revisions=runtime_revisions,
            require_runtime_match=True,
        )

    assert expected in exc_info.value.blockers


def test_side_effect_ledger_returns_same_key_and_suppresses_duplicate(tmp_path: Path) -> None:
    envelope, _, _ = seal_fixture(tmp_path)
    request = {
        "directory": tmp_path,
        "lifecycle_series_id": envelope.lifecycle_series_id,
        "run_id": envelope.lifecycle_run_id,
        "attempt_id": envelope.attempt_id,
        "correlation_id": envelope.correlation_id,
        "stage_id": "model_training",
        "action": "dispatch_kubernetes_job",
        "action_payload": {"namespace": "evm-staging", "name": "train-b0"},
    }

    first, created = reserve_side_effect(**request)
    duplicate, duplicate_created = reserve_side_effect(**request)
    completed = complete_side_effect(
        directory=tmp_path,
        side_effect_key=first.side_effect_key,
        state="completed",
        runtime_id="evm-staging/job/train-b0",
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate.side_effect_key == first.side_effect_key
    assert completed.state == "completed"
    ledger = LifecycleSideEffectLedger.model_validate_json(
        (tmp_path / "side_effect_ledger.json").read_text(encoding="utf-8")
    )
    assert len(ledger.entries) == 1
    assert ledger.entries[0].runtime_id == "evm-staging/job/train-b0"


def test_side_effect_ledger_rejects_duplicate_key_and_wrong_run_identity(
    tmp_path: Path,
) -> None:
    envelope, _, _ = seal_fixture(tmp_path)
    request = {
        "directory": tmp_path,
        "lifecycle_series_id": envelope.lifecycle_series_id,
        "run_id": envelope.lifecycle_run_id,
        "attempt_id": envelope.attempt_id,
        "correlation_id": envelope.correlation_id,
        "stage_id": "model_training",
        "action": "dispatch_kubernetes_job",
        "action_payload": {"namespace": "evm-staging", "name": "train-b0"},
    }
    first, _ = reserve_side_effect(**request)
    path = tmp_path / "side_effect_ledger.json"
    duplicate = json.loads(path.read_text(encoding="utf-8"))
    duplicate["entries"].append(dict(duplicate["entries"][0]))
    path.write_text(json.dumps(duplicate), encoding="utf-8")

    with pytest.raises(LifecycleGuardBlocked, match="side_effect_ledger_invalid"):
        reserve_side_effect(**request)

    duplicate["entries"] = [duplicate["entries"][0]]
    duplicate["entries"][0]["lifecycle_run_id"] = "wrong-run"
    path.write_text(json.dumps(duplicate), encoding="utf-8")
    with pytest.raises(LifecycleGuardBlocked, match="side_effect_ledger_invalid"):
        complete_side_effect(
            directory=tmp_path,
            side_effect_key=first.side_effect_key,
            state="completed",
        )


def test_completed_side_effect_cannot_move_back_to_reconciled(tmp_path: Path) -> None:
    envelope, _, _ = seal_fixture(tmp_path)
    first, _ = reserve_side_effect(
        directory=tmp_path,
        lifecycle_series_id=envelope.lifecycle_series_id,
        run_id=envelope.lifecycle_run_id,
        attempt_id=envelope.attempt_id,
        correlation_id=envelope.correlation_id,
        stage_id="model_training",
        action="dispatch_kubernetes_job",
        action_payload={"namespace": "evm-staging", "name": "train-b0"},
    )
    complete_side_effect(
        directory=tmp_path,
        side_effect_key=first.side_effect_key,
        state="completed",
    )

    with pytest.raises(
        LifecycleGuardBlocked,
        match="side_effect_state_transition_invalid:completed:reconciled",
    ):
        complete_side_effect(
            directory=tmp_path,
            side_effect_key=first.side_effect_key,
            state="reconciled",
        )
