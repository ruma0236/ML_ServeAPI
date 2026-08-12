from __future__ import annotations

import json
from pathlib import Path

import pytest

from evm.control_panel.scenario_workload_production import (
    ScenarioProductionApprovalRequest,
    ScenarioProductionRequest,
    ScenarioProductionRollbackRequest,
    approve_production_intent,
    create_production_intent,
    current_production_intent,
    get_production_intent,
    request_production_rollback,
)
from evm.control_panel.scenario_workloads import (
    ScenarioWorkloadError,
    ScenarioWorkloadRun,
    WorkloadIdentity,
    atomic_write_json,
    file_sha256,
)
from evm.model_runtime import scenario_workload_production as runtime


SOURCE_COMMIT = "b" * 40
MODEL_REVISION = "a" * 40


class FakeProcess:
    pid = 4321

    def poll(self):
        return None


def install_completed_run(tmp_path: Path, monkeypatch) -> ScenarioWorkloadRun:
    root = tmp_path / "scenario_workloads"
    run_root = root / "run-vlm-1"
    model_root = run_root / "model"
    adapter = model_root / "adapter" / "adapter_model.safetensors"
    evaluation = model_root / "adapted-evaluation.json"
    evidence_index = run_root / "evidence-index.json"
    adapter.parent.mkdir(parents=True)
    adapter.write_bytes(b"exact-adapter-bytes")
    atomic_write_json(
        evaluation,
        {"schema_version": "evm.scenario_vlm_evaluation.v1", "metrics": {"accuracy": 0.75}},
    )
    atomic_write_json(
        model_root / "training-result.json",
        {"status": "pass", "promotion_blockers": [], "metrics": {"accuracy": 0.75}},
    )
    atomic_write_json(evidence_index, {"schema_version": "test", "entries": []})
    identity = WorkloadIdentity(
        scenario_id="scienceqa-vlm-evaluation",
        dataset_id="scienceqa",
        dataset_version="scienceqa-test-v1",
        manifest_uri=str(tmp_path / "manifest.jsonl"),
        manifest_sha256="1" * 64,
        split_manifest_uri=str(tmp_path / "split.json"),
        split_manifest_sha256="2" * 64,
        data_identity_sha256="3" * 64,
        quality_status="pass",
        quality_report_uri=str(tmp_path / "quality.json"),
        data_view_uri=str(tmp_path / "data-view.json"),
        model_family="vlm",
        model_repository="HuggingFaceTB/SmolVLM-500M-Instruct",
        model_revision=MODEL_REVISION,
        processor_revision=MODEL_REVISION,
        source_commit=SOURCE_COMMIT,
        source_branch="codex/test",
        dirty_worktree=False,
        compute_backend="windows-host-cuda",
        identity_sha256="4" * 64,
    )
    run = ScenarioWorkloadRun(
        run_id="run-vlm-1",
        state="completed",
        version=2,
        actor="ml-engineer",
        reason="Validate one exact transformer release candidate",
        dry_run=False,
        created_at="2026-08-12T00:00:00Z",
        updated_at="2026-08-12T00:01:00Z",
        finished_at="2026-08-12T00:01:00Z",
        progress=1.0,
        identity=identity,
        adaptation_method="lora",
        quantization_requested="none",
        quantization_observed="none",
        artifact_root=str(run_root),
        gpu_lease_state="released",
        mlflow_run_id="mlflow-run",
        model_artifact_uri=str(adapter),
        model_artifact_sha256=file_sha256(adapter),
        evaluation_uri=str(evaluation),
        serving_endpoint="http://127.0.0.1:30920",
        metrics_endpoint="http://127.0.0.1:30920/metrics",
        evidence_index_uri=str(evidence_index),
        evidence_index_sha256=file_sha256(evidence_index),
        stages=[],
        audit=[],
    )
    atomic_write_json(run_root / "workload_run.json", run.model_dump(mode="json"))
    presets = tmp_path / "presets.json"
    atomic_write_json(
        presets,
        {
            "schema_version": "evm.scenario_workload_preset_catalog.v1",
            "presets": [
                {
                    "preset_id": "smolvlm-scienceqa-local-production",
                    "label": "SmolVLM / ScienceQA",
                    "model_family": "vlm",
                    "scenario_id": "scienceqa-vlm-evaluation",
                    "model_repository": identity.model_repository,
                    "model_revision": MODEL_REVISION,
                    "model_dir": str(tmp_path / "base-model"),
                    "data_view_uri": str(tmp_path / "data-view.json"),
                    "adaptation_method": "lora",
                    "quantization_requested": "none",
                    "max_steps": 8,
                    "staging_port": 30920,
                    "production_port": 31020,
                    "record_counts": {"train": 32, "validation": 8, "test": 8},
                    "quality_metrics": ["accuracy", "parse_rate"],
                    "claim_boundary": "Bounded local validation only.",
                }
            ],
        },
    )
    monkeypatch.setenv("EVM_SCENARIO_WORKLOAD_ROOT", str(root))
    monkeypatch.setenv("EVM_SCENARIO_WORKLOAD_CANONICAL_ROOT", str(root))
    monkeypatch.setenv("EVM_SCENARIO_WORKLOAD_PRESETS", str(presets))
    ci_path = root / "_production" / "local-ci-evidence.json"
    atomic_write_json(
        ci_path,
        {
            "schema_version": "evm.scenario_local_ci_evidence.v1",
            "status": "pass",
            "source_commit": SOURCE_COMMIT,
            "commands": [
                {"name": f"check-{index}", "status": "pass", "exit_code": 0}
                for index in range(5)
            ],
        },
    )
    return run


def test_production_intent_requires_independent_approval_and_exact_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    run = install_completed_run(tmp_path, monkeypatch)
    intent = create_production_intent(
        run.run_id,
        ScenarioProductionRequest(
            actor="release-requester",
            reason="Promote the exact evaluated adapter to local production",
        ),
    )
    assert intent.state == "pending_approval"
    assert intent.target.endpoint == "http://127.0.0.1:31020"

    with pytest.raises(ScenarioWorkloadError) as conflict:
        approve_production_intent(
            intent.intent_id,
            ScenarioProductionApprovalRequest(
                actor="release-requester",
                reason="Attempt self approval for the same release intent",
            ),
        )
    assert conflict.value.code == "scenario_production_approver_requester_conflict"

    Path(run.model_artifact_uri).write_bytes(b"tampered")
    with pytest.raises(ScenarioWorkloadError) as tampered:
        approve_production_intent(
            intent.intent_id,
            ScenarioProductionApprovalRequest(
                actor="platform-approver",
                reason="Approve only after exact identity validation passes",
            ),
        )
    assert tampered.value.code == "scenario_production_artifact_digest_mismatch"


def test_production_apply_and_rollback_state_machine(tmp_path: Path, monkeypatch) -> None:
    run = install_completed_run(tmp_path, monkeypatch)
    intent = create_production_intent(
        run.run_id,
        ScenarioProductionRequest(
            actor="release-requester",
            reason="Promote the exact evaluated adapter to local production",
        ),
    )
    intent = approve_production_intent(
        intent.intent_id,
        ScenarioProductionApprovalRequest(
            actor="platform-approver",
            reason="Approve the identity-bound local production action",
        ),
    )
    holder = {
        "namespace": "evm-production",
        "name": "evm-b0-production",
        "uid": "deployment-uid",
        "selector": "app=evm-b0-production",
        "pod_name": "pod-1",
        "pod_uid": "pod-uid",
    }
    scaled: list[tuple[int, bool]] = []
    monkeypatch.setattr(runtime, "exact_gpu_holder", lambda: holder)
    monkeypatch.setattr(
        runtime,
        "scale_holder",
        lambda _holder, *, replicas, require_ready: scaled.append((replicas, require_ready)),
    )
    monkeypatch.setattr(
        runtime,
        "start_production_server",
        lambda _intent, _model_dir: (FakeProcess(), "20260812010101.000000+000", ["serve"]),
    )
    monkeypatch.setattr(
        runtime,
        "wait_for_ready",
        lambda _intent, _process: {"status": "ready", "environment": "local-production"},
    )
    monkeypatch.setattr(runtime, "verify_production_inference", lambda _intent: {"status": "pass"})
    monkeypatch.setattr(runtime, "write_prometheus_target", lambda _intent: None)
    monkeypatch.setattr(runtime, "wait_for_prometheus", lambda _intent: {"status": "pass"})

    applied = runtime.apply_production_intent(intent.intent_id)
    assert applied.state == "applied"
    assert applied.service_pid == 4321
    assert current_production_intent().intent_id == intent.intent_id  # type: ignore[union-attr]
    assert scaled == [(0, False)]

    requested = request_production_rollback(
        intent.intent_id,
        ScenarioProductionRollbackRequest(
            actor="platform-operator",
            reason="Restore the known-good B0 holder after validation",
        ),
    )
    monkeypatch.setattr(runtime, "stop_exact_process", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "restore_target_from_evidence_dir", lambda _root: None)
    rolled_back = runtime.rollback_production_intent(requested.intent_id)
    assert rolled_back.state == "rolled_back"
    assert current_production_intent() is None
    assert scaled == [(0, False), (1, True)]
    assert get_production_intent(intent.intent_id).version >= 6


def test_production_apply_fails_closed_before_gpu_mutation_when_artifact_changes(
    tmp_path: Path, monkeypatch
) -> None:
    run = install_completed_run(tmp_path, monkeypatch)
    intent = create_production_intent(
        run.run_id,
        ScenarioProductionRequest(
            actor="release-requester",
            reason="Promote the exact evaluated adapter to local production",
        ),
    )
    intent = approve_production_intent(
        intent.intent_id,
        ScenarioProductionApprovalRequest(
            actor="platform-approver",
            reason="Approve the identity-bound local production action",
        ),
    )
    Path(run.model_artifact_uri).write_bytes(b"changed-after-approval")
    gpu_mutations: list[str] = []
    monkeypatch.setattr(runtime, "exact_gpu_holder", lambda: gpu_mutations.append("query"))

    failed = runtime.apply_production_intent(intent.intent_id)

    assert failed.state == "failed"
    assert failed.blockers == [
        "scenario_production_admission_failed:scenario_production_artifact_digest_mismatch:run-vlm-1"
    ]
    assert gpu_mutations == []
    assert current_production_intent() is None
