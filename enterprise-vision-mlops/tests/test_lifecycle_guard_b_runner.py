from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evm.operations.lifecycle_guard_b_runner import (
    approval_denial,
    ensure_replay_runtime_ready,
    lifecycle_binding,
    replay_inputs,
)


WRAPPER = Path("scripts/dev/lifecycle_guard_b_integrated_proof.ps1")


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_lifecycle_binding_captures_exact_release_and_ct_identity(tmp_path: Path) -> None:
    ct_path = tmp_path / "ct.json"
    write_json(ct_path, {"evaluation_id": "ct-1"})
    run = {
        "run_id": "run-1",
        "lifecycle_series_id": "series-1",
        "attempt_id": "attempt-1",
        "correlation_id": "correlation-1",
        "profile_digest": "a" * 64,
        "effective_config_digest": "b" * 64,
        "source_commit": "c" * 40,
        "ct_evaluation_uri": str(ct_path),
    }
    submission = {
        "candidate_id": "candidate-1",
        "model_digest": "d" * 64,
        "ct_evaluation_id": "ct-1",
        "submission_digest": "e" * 64,
    }

    binding = lifecycle_binding(run, submission)

    assert binding["lifecycle_run_id"] == "run-1"
    assert binding["ct_evaluation_id"] == "ct-1"
    assert len(binding["ct_evaluation_sha256"]) == 64
    assert binding["model_digest"] == "d" * 64


def test_replay_inputs_rejects_candidate_digest_mismatch(tmp_path: Path) -> None:
    model_path = tmp_path / "candidate" / "model.pt"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"model")
    write_json(model_path.parent / "candidate_summary.json", {"candidate_id": "candidate"})
    submission_path = tmp_path / "release.json"
    write_json(
        submission_path,
        {
            "model_artifact_uri": str(model_path),
            "model_digest": "f" * 64,
        },
    )

    with pytest.raises(RuntimeError, match="candidate_digest_mismatch"):
        replay_inputs({"release_submission_uri": str(submission_path)})


def test_approval_denial_requires_release_guard_error(monkeypatch: pytest.MonkeyPatch) -> None:
    response = SimpleNamespace(
        status_code=422,
        json=lambda: {
            "detail": {
                "error": "release_guard_release_blocked",
                "message": "release_guard_state:rolled_back",
            }
        },
        text="",
    )
    monkeypatch.setattr(
        "evm.operations.lifecycle_guard_b_runner.requests.post",
        lambda *_args, **_kwargs: response,
    )

    result = approval_denial(
        {"run_id": "run-1", "version": 9},
        {
            "candidate_id": "candidate-1",
            "model_digest": "a" * 64,
            "ct_evaluation_id": "ct-1",
        },
    )

    assert result["status_code"] == 422


def test_wrapper_falls_through_invalid_python_candidates_to_cuda_runtime() -> None:
    script = WRAPPER.read_text(encoding="utf-8")

    assert '"F:\\evm_w7_torch\\python.exe"' in script
    assert '$ErrorActionPreference = "Continue"' in script
    assert "$probeExitCode = $LASTEXITCODE" in script
    assert "if ($probeExitCode -eq 0)" in script


def test_pre_replay_runtime_requires_bounded_two_scrape_restoration() -> None:
    before = {"kubernetes": {"deployment_uid": "uid-1"}}
    calls: list[dict[str, object]] = []

    def passing_waiter(**kwargs: object) -> tuple[dict[str, object], dict[str, object]]:
        calls.append(kwargs)
        return {"prometheus": {"health": "up"}}, {
            "status": "pass",
            "required_distinct_consecutive_scrapes": 2,
        }

    runtime, restoration = ensure_replay_runtime_ready(
        before_runtime=before,
        source_commit="a" * 40,
        inference_image_uri="image@sha256:" + "b" * 64,
        timeout_seconds=90,
        waiter=passing_waiter,
    )

    assert runtime["prometheus"]["health"] == "up"
    assert restoration["required_distinct_consecutive_scrapes"] == 2
    assert calls[0]["timeout_seconds"] == 90


def test_pre_replay_runtime_fails_closed_on_convergence_timeout() -> None:
    def blocked_waiter(**_kwargs: object) -> tuple[dict[str, object], dict[str, object]]:
        return {}, {"status": "blocked", "observations": []}

    with pytest.raises(RuntimeError, match="pre_replay_runtime_not_restored"):
        ensure_replay_runtime_ready(
            before_runtime={},
            source_commit="a" * 40,
            inference_image_uri="image@sha256:" + "b" * 64,
            timeout_seconds=90,
            waiter=blocked_waiter,
        )
