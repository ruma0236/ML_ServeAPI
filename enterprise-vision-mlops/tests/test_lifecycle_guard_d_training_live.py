from __future__ import annotations

import json
import subprocess

import evm.operations.lifecycle_guard_d_training_live as training_live
import pytest

from evm.control_panel.lifecycle_guards import (
    LifecycleSideEffect,
    LifecycleSideEffectLedger,
)
from evm.operations.lifecycle_guard_d_training_live import (
    exact_training_entry,
    exact_training_job,
    release_approval_request,
    training_job_state,
)
from evm.control_panel.lifecycle_kubernetes import short_run_id


def training_entry(*, state: str = "reserved") -> LifecycleSideEffect:
    return LifecycleSideEffect(
        side_effect_key="a" * 64,
        lifecycle_series_id="series-d",
        lifecycle_run_id="lifecycle-d",
        attempt_id="attempt-d",
        correlation_id="correlation-d",
        stage_id="model_training",
        action="execute_kubernetes_job",
        action_digest="b" * 64,
        state=state,
        reserved_at="2026-08-02T00:00:00Z",
        updated_at="2026-08-02T00:00:00Z",
    )


def test_exact_training_entry_requires_one_matching_side_effect() -> None:
    ledger = LifecycleSideEffectLedger(
        lifecycle_run_id="lifecycle-d",
        entries=[training_entry()],
    )

    assert exact_training_entry(ledger)["side_effect_key"] == "a" * 64


def test_training_job_state_precedence() -> None:
    assert training_job_state({"status": {"active": 1}}) == "active"
    assert training_job_state(
        {"status": {"conditions": [{"type": "Complete", "status": "True"}]}}
    ) == "complete"
    assert training_job_state(
        {"status": {"conditions": [{"type": "Failed", "status": "True"}]}}
    ) == "failed"


def test_release_approval_is_bound_to_sealed_submission_identity() -> None:
    request = release_approval_request(
        {
            "state": "waiting_approval",
            "current_stage": "approval",
            "version": 42,
        },
        {
            "candidate_id": "candidate-d",
            "model_digest": "c" * 64,
            "ct_evaluation_id": "ct-eval-d",
        },
    )

    assert request == {
        "actor": "scenario-d-release-approver",
        "approver": "scenario-d-release-approver",
        "reason": (
            "Approve the sealed local-staging release for the pre-authorized "
            "Scenario D integrated lifecycle validation"
        ),
        "candidate_id": "candidate-d",
        "model_digest": "c" * 64,
        "ct_evaluation_id": "ct-eval-d",
        "expected_version": 42,
    }


def test_release_approval_fails_closed_on_incomplete_identity() -> None:
    with pytest.raises(RuntimeError, match="release_approval_identity_incomplete"):
        release_approval_request(
            {
                "state": "waiting_approval",
                "current_stage": "approval",
                "version": 42,
            },
            {
                "candidate_id": "candidate-d",
                "model_digest": "short",
                "ct_evaluation_id": "ct-eval-d",
            },
        )


def test_exact_training_job_uses_canonical_hashed_run_label(
    tmp_path, monkeypatch
) -> None:
    run_id = "lifecycle-20260802T190057-c8cae6d4"
    run_label = short_run_id(run_id)
    assert not run_id.endswith(run_label)
    job_name = f"evm-lifecycle-train-{run_label}"
    manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": "evm-training",
            "labels": {
                "app.kubernetes.io/part-of": "enterprise-vision-mlops",
                "evm.openai.local/lifecycle-run": run_label,
                "evm.openai.local/candidate-id": "candidate-d",
            },
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "trainer",
                            "image": "pipeline@sha256:" + "a" * 64,
                            "env": [
                                {"name": "EVM_LIFECYCLE_RUN_ID", "value": run_id},
                                {
                                    "name": "EVM_EXPECTED_COMPONENT_SOURCE_REVISION",
                                    "value": "b" * 40,
                                },
                            ],
                        }
                    ]
                }
            }
        },
    }
    (tmp_path / "training-job.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    observed = json.loads(json.dumps(manifest))
    observed["metadata"]["uid"] = "job-uid-d"
    observed["status"] = {"active": 1}
    monkeypatch.setattr(training_live, "kubectl_json", lambda _command: observed)
    task = {
        "runtime_id": f"evm-training/job/{job_name}",
        "config_payload": {
            "manifest_dir": str(tmp_path),
            "namespace": "evm-training",
            "job_name": job_name,
            "lifecycle_run_id": run_id,
        },
    }

    assert exact_training_job(run_id, task)["metadata"]["uid"] == "job-uid-d"


def test_exact_training_job_waits_only_for_kubernetes_not_found(
    tmp_path, monkeypatch
) -> None:
    run_id = "lifecycle-20260802T190057-c8cae6d4"
    run_label = short_run_id(run_id)
    job_name = f"evm-lifecycle-train-{run_label}"
    manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": "evm-training",
            "labels": {
                "app.kubernetes.io/part-of": "enterprise-vision-mlops",
                "evm.openai.local/lifecycle-run": run_label,
                "evm.openai.local/candidate-id": "candidate-d",
            },
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {"name": "trainer", "image": "pipeline:immutable"}
                    ]
                }
            }
        },
    }
    (tmp_path / "training-job.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    task = {
        "runtime_id": f"evm-training/job/{job_name}",
        "config_payload": {
            "manifest_dir": str(tmp_path),
            "namespace": "evm-training",
            "job_name": job_name,
            "lifecycle_run_id": run_id,
        },
    }

    def missing(_command):
        raise subprocess.CalledProcessError(
            1,
            ["kubectl", "get", "job"],
            stderr='Error from server (NotFound): jobs.batch "missing" not found',
        )

    monkeypatch.setattr(training_live, "kubectl_json", missing)

    assert exact_training_job(run_id, task, allow_not_found=True) is None
    with pytest.raises(subprocess.CalledProcessError):
        exact_training_job(run_id, task)
