from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evm.control_panel.lifecycle_guards import file_digest
from evm.control_panel.lifecycle_release_guard import (
    LifecycleReleaseGuardBlocked,
    LifecycleReleaseGuardRegistration,
    authorize_release_guard,
    load_release_guard,
    register_release_guard,
)
from evm.operations.scenario_b_canary import (
    CanaryPolicy,
    InferenceObservation,
    ModelIdentity,
    QualityMetrics,
    ReplayRequest,
    run_controlled_replay,
    write_controlled_replay_evidence,
)


SOURCE = "d" * 40
STABLE_DIGEST = "a" * 64
CANDIDATE_DIGEST = "b" * 64
IMAGE_DIGEST = "c" * 64


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def release_run(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "runs" / "lifecycle-run-1"
    submission = {
        "schema_version": "evm.lifecycle_release_submission.v1",
        "run_id": "lifecycle-run-1",
        "source_commit": SOURCE,
        "candidate_id": "candidate-b0",
        "dataset_version": "visa-v1",
        "model_digest": CANDIDATE_DIGEST,
        "ct_evaluation_id": "ct-eval-1",
        "submission_digest": "e" * 64,
    }
    submission_path = root / "validation" / "release-submission.json"
    write_json(submission_path, submission)
    return SimpleNamespace(
        run_id="lifecycle-run-1",
        profile_digest="1" * 64,
        effective_config_digest="2" * 64,
        lifecycle_series_id="series-1",
        attempt_id="attempt-1",
        correlation_id="correlation-1",
        source_commit=SOURCE,
        artifact_root=str(root),
        release_submission_uri=str(submission_path),
        release_guard_required=True,
        release_guard_uri=None,
    )


def replay_evidence(
    tmp_path: Path,
    run: SimpleNamespace,
    *,
    quality_f1: float,
) -> Path:
    policy = CanaryPolicy(
        policy_id="scenario-b-test",
        assignment_seed="seed-1",
        min_shadow_requests=500,
        total_replay_requests=1000,
        challenger_requests=100,
        max_challenger_fraction=0.1,
        min_accuracy=0.8,
        min_f1=0.75,
        min_auroc=0.8,
        max_latency_p95_ms=30,
        max_error_rate=0.01,
        stop_budget_seconds=30,
        rollback_budget_seconds=300,
        signal_precedence=["identity", "error_rate", "latency", "quality"],
    )
    stable = ModelIdentity(
        candidate_id="stable-b0",
        architecture="efficientnet-b0",
        dataset_version="visa-v1",
        model_digest=STABLE_DIGEST,
        image_digest=IMAGE_DIGEST,
    )
    challenger = ModelIdentity(
        candidate_id="candidate-b0",
        architecture="efficientnet-b0",
        dataset_version="visa-v1",
        model_digest=CANDIDATE_DIGEST,
        image_digest=IMAGE_DIGEST,
    )
    requests = [
        ReplayRequest(
            request_id=f"request-{index:04d}",
            content_digest=f"{index:064x}",
            image_uri=f"file:///record-{index}.png",
            expected_label="normal",
        )
        for index in range(1000)
    ]
    stable_observations = [
        InferenceObservation(
            request_id=item.request_id,
            model_digest=STABLE_DIGEST,
            latency_ms=1,
            succeeded=True,
            prediction="normal",
            confidence=0.99,
        )
        for item in requests
    ]
    challenger_observations = [
        InferenceObservation(
            request_id=item.request_id,
            model_digest=CANDIDATE_DIGEST,
            latency_ms=2,
            succeeded=True,
            prediction="normal",
            confidence=0.98,
        )
        for item in requests
    ]
    result = run_controlled_replay(
        run_id=f"scenario-b-{quality_f1}",
        policy=policy,
        stable=stable,
        challenger=challenger,
        requests=requests,
        stable_observations=stable_observations,
        challenger_observations=challenger_observations,
        challenger_quality=QualityMetrics(
            accuracy=0.95,
            f1=quality_f1,
            auroc=0.95,
        ),
    )
    run_root = tmp_path / "evidence" / result.run_id
    runtime = run_root / "runtime.json"
    candidate = run_root / "candidate-summary-reference.json"
    binding = run_root / "lifecycle-binding.json"
    write_json(
        runtime,
        {
            "source": {"commit": SOURCE, "dirty": False},
            "production_mutated": False,
            "stable_identity_unchanged": True,
            "cuda": {"device": "cuda"},
        },
    )
    write_json(
        candidate,
        {
            "candidate_id": "candidate-b0",
            "model_sha256": CANDIDATE_DIGEST,
            "metrics": {"accuracy": 0.95, "f1": quality_f1, "auroc": 0.95},
        },
    )
    write_json(
        binding,
        {
            "schema_version": "evm.lifecycle_release_binding.v1",
            "lifecycle_run_id": run.run_id,
            "lifecycle_series_id": run.lifecycle_series_id,
            "attempt_id": run.attempt_id,
            "correlation_id": run.correlation_id,
            "profile_digest": run.profile_digest,
            "effective_config_digest": run.effective_config_digest,
            "source_commit": run.source_commit,
            "candidate_id": "candidate-b0",
            "model_digest": CANDIDATE_DIGEST,
            "ct_evaluation_id": "ct-eval-1",
            "release_submission_digest": "e" * 64,
        },
    )
    return write_controlled_replay_evidence(
        root=tmp_path / "evidence",
        result=result,
        requests=requests,
        additional_artifacts={
            "runtime": runtime,
            "candidate_summary_reference": candidate,
            "lifecycle_binding": binding,
        },
    )


def registration(index_path: Path) -> LifecycleReleaseGuardRegistration:
    return LifecycleReleaseGuardRegistration(
        actor="release-guard-controller",
        reason="Bind controlled replay evidence to release",
        expected_version=7,
        evidence_index_uri=str(index_path),
        evidence_index_sha256=file_digest(index_path),
    )


def test_quality_breach_rejects_exact_lifecycle_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVM_LIFECYCLE_RUN_ROOT", str(tmp_path / "runs"))
    run = release_run(tmp_path)
    index_path = replay_evidence(tmp_path, run, quality_f1=0.70)

    guard = register_release_guard(run, registration(index_path))

    assert guard.state == "rejected_release"
    assert guard.blocker_codes == ["quality_f1_below_minimum"]
    assert load_release_guard(run) == guard
    with pytest.raises(LifecycleReleaseGuardBlocked, match="release_guard_release_blocked"):
        authorize_release_guard(run)


def test_passing_replay_authorizes_only_exact_bound_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVM_LIFECYCLE_RUN_ROOT", str(tmp_path / "runs"))
    run = release_run(tmp_path)
    index_path = replay_evidence(tmp_path, run, quality_f1=0.90)

    guard = register_release_guard(run, registration(index_path))

    assert guard.state == "approved_for_release"
    assert guard.blocker_codes == []
    assert authorize_release_guard(run) == guard


def test_required_release_guard_fails_closed_when_missing(tmp_path: Path) -> None:
    run = release_run(tmp_path)

    with pytest.raises(LifecycleReleaseGuardBlocked, match="release_guard_required_missing"):
        authorize_release_guard(run)
