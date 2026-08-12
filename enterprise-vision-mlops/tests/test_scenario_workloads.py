from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evm.control_panel.scenario_workload_control import (
    ScenarioWorkloadLaunchRequest,
    launch_workload,
    load_execution_request,
)
from evm.control_panel.scenario_workloads import (
    ScenarioWorkloadError,
    ScenarioWorkloadRequest,
    acquire_gpu_lease,
    assert_gpu_lease_owner,
    create_workload_run,
    get_workload_run,
    release_gpu_lease,
    seal_workload_run,
    transition_workload_stage,
    update_workload_results,
)


MODEL_REVISION = "a" * 40
SOURCE_REVISION = "b" * 40


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_scenario(tmp_path: Path, monkeypatch, *, quality: str = "pass") -> Path:
    config_root = tmp_path / "configs"
    output_root = tmp_path / "data"
    manifest = output_root / "processed" / "normalized_manifest.jsonl"
    split = output_root / "evidence" / "split_manifest.json"
    quality_report = output_root / "evidence" / "quality_report.json"
    manifest.parent.mkdir(parents=True)
    split.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "sample_id": "sample-1",
                "split": "train",
                "content_sha256": "c" * 64,
                "question": "What is shown?",
                "image_uri": str(output_root / "images" / "sample-1.png"),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    split.write_text(
        json.dumps(
            {
                "manifest_sha256": sha256(manifest),
                "identity_sha256": "d" * 64,
                "record_count": 1,
                "split_counts": {"train": 1},
            }
        ),
        encoding="utf-8",
    )
    quality_report.write_text(
        json.dumps({"status": quality, "records_out": 1}), encoding="utf-8"
    )
    config_root.mkdir()
    (config_root / "scenario.json").write_text(
        json.dumps(
            {
                "schema_version": "evm.scenario_intake.v1",
                "scenario": {
                    "scenario_id": "scienceqa-test",
                    "display_name": "ScienceQA Test",
                    "department": "AI Research",
                    "business_outcome": "Test a real multimodal lifecycle contract.",
                    "modality": "image_text",
                    "intake_supported": True,
                    "model_readiness": "not_implemented",
                    "deployment_readiness": "not_implemented",
                },
                "dataset": {
                    "dataset_id": "scienceqa",
                    "dataset_name": "ScienceQA",
                    "dataset_version": "scienceqa-test-v1",
                    "source_url": "https://example.invalid/scienceqa",
                    "source_revision": "revision",
                    "license_id": "CC-BY-NC-SA-4.0",
                    "license_url": "https://example.invalid/license",
                    "usage_policy": "test-only",
                    "output_root": str(output_root),
                    "manifest_uri": str(manifest),
                    "split_manifest_uri": str(split),
                },
                "preprocessing": {
                    "recipe_id": "scienceqa-test",
                    "version": "1.0.0",
                    "steps": [],
                },
                "acquisition": {"parser": "scienceqa_parquet", "source_files": []},
                "split_policy": {"seed": 7, "train": 1.0, "validation": 0.0, "test": 0.0},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVM_SCENARIO_CONFIG_ROOT", str(config_root))
    monkeypatch.setenv("EVM_SCENARIO_WORKLOAD_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("EVM_SCENARIO_GPU_LEASE_ROOT", str(tmp_path / "lease"))
    return output_root


def request(
    *,
    dry_run: bool = False,
    dirty: bool = False,
    disposition: str | None = None,
    data_view: str | None = None,
):
    return ScenarioWorkloadRequest(
        scenario_id="scienceqa-test",
        model_family="vlm",
        model_repository="HuggingFaceTB/SmolVLM-500M-Instruct",
        model_revision=MODEL_REVISION,
        adaptation_method="lora",
        actor="test-operator",
        reason="Validate the generic scenario lifecycle",
        dry_run=dry_run,
        source_commit=None if dry_run else SOURCE_REVISION,
        dirty_worktree=dirty,
        quality_disposition_uri=disposition,
        data_view_uri=data_view,
    )


def test_create_run_seals_exact_data_and_model_identity(tmp_path: Path, monkeypatch) -> None:
    install_scenario(tmp_path, monkeypatch)

    run = create_workload_run(request())

    assert run.state == "queued"
    assert run.identity.model_revision == MODEL_REVISION
    assert run.identity.processor_revision == MODEL_REVISION
    assert run.identity.manifest_sha256 == sha256(Path(run.identity.manifest_uri))
    assert len(run.identity.identity_sha256) == 64
    assert [stage.stage_id for stage in run.stages] == [
        "data_intake",
        "identity_quality_gate",
        "gpu_lease",
        "adaptation",
        "experiment_tracking",
        "isolated_evaluation",
        "artifact_seal",
        "approval",
        "staging_serving",
        "observability",
    ]


def test_execution_request_round_trip_and_tamper_detection(
    tmp_path: Path, monkeypatch
) -> None:
    install_scenario(tmp_path, monkeypatch)
    monkeypatch.setenv("EVM_SCENARIO_WORKLOAD_CANONICAL_ROOT", str(tmp_path / "runs"))
    presets = tmp_path / "presets.json"
    presets.write_text(
        json.dumps(
            {
                "schema_version": "evm.scenario_workload_preset_catalog.v1",
                "presets": [
                    {
                        "preset_id": "smolvlm-test",
                        "label": "SmolVLM test",
                        "model_family": "vlm",
                        "scenario_id": "scienceqa-test",
                        "model_repository": "HuggingFaceTB/SmolVLM-500M-Instruct",
                        "model_revision": MODEL_REVISION,
                        "model_dir": str(tmp_path / "model"),
                        "data_view_uri": "",
                        "adaptation_method": "lora",
                        "quantization_requested": "none",
                        "max_steps": 8,
                        "staging_port": 30920,
                        "production_port": 31020,
                        "record_counts": {"train": 1, "validation": 0, "test": 0},
                        "quality_metrics": ["accuracy", "parse_rate"],
                        "claim_boundary": "Test-only bounded lifecycle.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVM_SCENARIO_WORKLOAD_PRESETS", str(presets))

    run = launch_workload(
        ScenarioWorkloadLaunchRequest(
            preset_id="smolvlm-test",
            actor="test-operator",
            reason="Validate the signed execution request round trip",
        ),
        source_commit=SOURCE_REVISION,
        source_branch="codex/test",
    )

    loaded = load_execution_request(run.run_id)
    assert loaded.run_id == run.run_id
    assert loaded.preset.record_counts["train"] == 1
    request_path = Path(run.artifact_root) / "execution-request.json"
    tampered = json.loads(request_path.read_text(encoding="utf-8"))
    tampered["reason"] = "Tampered after the signed request was persisted"
    request_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ScenarioWorkloadError) as error:
        load_execution_request(run.run_id)
    assert error.value.code == "workload_execution_request_digest_mismatch"


def test_executable_run_fails_closed_for_dirty_or_mismatched_input(
    tmp_path: Path, monkeypatch
) -> None:
    output = install_scenario(tmp_path, monkeypatch)
    with pytest.raises(ScenarioWorkloadError, match="clean source tree"):
        create_workload_run(request(dirty=True))

    split_path = output / "evidence" / "split_manifest.json"
    split = json.loads(split_path.read_text(encoding="utf-8"))
    split["manifest_sha256"] = "0" * 64
    split_path.write_text(json.dumps(split), encoding="utf-8")
    with pytest.raises(ScenarioWorkloadError) as error:
        create_workload_run(request())
    assert error.value.code == "scenario_manifest_identity_mismatch"


def test_quality_review_requires_identity_bound_disposition(tmp_path: Path, monkeypatch) -> None:
    output = install_scenario(tmp_path, monkeypatch, quality="review_required")
    with pytest.raises(ScenarioWorkloadError) as error:
        create_workload_run(request())
    assert error.value.code == "scenario_quality_not_approved"

    disposition = tmp_path / "disposition.json"
    manifest = output / "processed" / "normalized_manifest.jsonl"
    split = output / "evidence" / "split_manifest.json"
    disposition.write_text(
        json.dumps(
            {
                "schema_version": "evm.scenario_quality_disposition.v1",
                "decision": "approved",
                "dataset_version": "scienceqa-test-v1",
                "input_manifest_sha256": sha256(manifest),
                "output_manifest_uri": str(manifest),
                "output_manifest_sha256": sha256(manifest),
                "output_split_manifest_uri": str(split),
                "output_identity_sha256": "d" * 64,
                "approver": "data-steward",
                "approved_at": "2026-08-05T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    run = create_workload_run(request(disposition=str(disposition)))
    assert run.identity.quality_status == "approved"
    assert run.identity.quality_disposition_uri == str(disposition)


def test_quality_disposition_and_same_derived_data_view_compose(
    tmp_path: Path, monkeypatch
) -> None:
    output = install_scenario(tmp_path, monkeypatch, quality="review_required")
    source_manifest = output / "processed" / "normalized_manifest.jsonl"
    derived_root = tmp_path / "derived"
    derived_manifest = derived_root / "processed" / "normalized_manifest.jsonl"
    derived_split = derived_root / "evidence" / "split_manifest.json"
    derived_manifest.parent.mkdir(parents=True)
    derived_split.parent.mkdir(parents=True)
    derived_manifest.write_text(source_manifest.read_text(encoding="utf-8"), encoding="utf-8")
    derived_split.write_text(
        json.dumps({"identity_sha256": "e" * 64}), encoding="utf-8"
    )
    disposition = derived_root / "evidence" / "quality_disposition.json"
    disposition.write_text(
        json.dumps(
            {
                "schema_version": "evm.scenario_quality_disposition.v1",
                "decision": "approved",
                "dataset_version": "scienceqa-test-v1",
                "input_manifest_sha256": sha256(source_manifest),
                "output_manifest_uri": str(derived_manifest),
                "output_manifest_sha256": sha256(derived_manifest),
                "output_split_manifest_uri": str(derived_split),
                "output_identity_sha256": "e" * 64,
                "approver": "data-steward",
                "approved_at": "2026-08-05T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    view = derived_root / "evidence" / "data_view.json"
    view.write_text(
        json.dumps(
            {
                "schema_version": "evm.scenario_data_view.v1",
                "status": "pass",
                "source_dataset_version": "scienceqa-test-v1",
                "input_manifest_sha256": sha256(source_manifest),
                "output_manifest_uri": str(derived_manifest),
                "output_manifest_sha256": sha256(derived_manifest),
                "output_split_manifest_uri": str(derived_split),
                "output_identity_sha256": "e" * 64,
                "recipe_id": "bounded-approved-view-v1",
            }
        ),
        encoding="utf-8",
    )

    run = create_workload_run(request(disposition=str(disposition), data_view=str(view)))

    assert run.identity.quality_status == "approved"
    assert run.identity.manifest_uri == str(derived_manifest)
    assert run.identity.data_identity_sha256 == "e" * 64


def test_stage_dependencies_and_completion_are_fail_closed(tmp_path: Path, monkeypatch) -> None:
    install_scenario(tmp_path, monkeypatch)
    run = create_workload_run(request())
    with pytest.raises(ScenarioWorkloadError) as error:
        transition_workload_stage(
            run.run_id,
            "adaptation",
            "running",
            actor="worker",
        )
    assert error.value.code == "workload_dependency_incomplete"

    with pytest.raises(ScenarioWorkloadError) as error:
        seal_workload_run(run.run_id, actor="worker")
    assert error.value.code == "workload_stages_incomplete"
    assert get_workload_run(run.run_id).state != "completed"


def test_gpu_lease_is_exclusive_fenced_and_idempotent(tmp_path: Path, monkeypatch) -> None:
    install_scenario(tmp_path, monkeypatch)
    first = create_workload_run(request())
    second = create_workload_run(request())

    lease = acquire_gpu_lease(first.run_id, owner_pid=1234)
    assert acquire_gpu_lease(first.run_id, owner_pid=9999) == lease
    assert assert_gpu_lease_owner(first.run_id) == lease
    with pytest.raises(ScenarioWorkloadError) as error:
        acquire_gpu_lease(second.run_id, owner_pid=4321)
    assert error.value.code == "gpu_lease_conflict"
    with pytest.raises(ScenarioWorkloadError) as error:
        release_gpu_lease(
            first.run_id,
            lease_id=lease.lease_id,
            fencing_token="wrong",
            reason="invalid release",
        )
    assert error.value.code == "gpu_lease_release_identity_mismatch"

    released = release_gpu_lease(
        first.run_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        reason="test complete",
    )
    assert released.state == "released"
    assert get_workload_run(first.run_id).gpu_lease_state == "released"
    assert acquire_gpu_lease(second.run_id, owner_pid=4321).run_id == second.run_id


def test_evidence_seal_rehashes_all_stages_and_model_artifact(
    tmp_path: Path, monkeypatch
) -> None:
    install_scenario(tmp_path, monkeypatch)
    run = create_workload_run(request())
    lease = acquire_gpu_lease(run.run_id, owner_pid=1234)
    artifact_root = Path(run.artifact_root)
    artifact = artifact_root / "adapter.safetensors"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"real-adapter-bytes")
    for stage_id, _, _ in (
        (
            (stage.stage_id, stage.label, stage.runtime)
            for stage in run.stages
        )
    ):
        evidence = artifact_root / "evidence" / f"{stage_id}.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps({"stage_id": stage_id, "passed": True}), encoding="utf-8")
        run = transition_workload_stage(
            run.run_id,
            stage_id,
            "running" if stage_id != "approval" else "waiting_approval",
            actor="worker",
        )
        run = transition_workload_stage(
            run.run_id,
            stage_id,
            "completed",
            actor="worker",
            evidence_uri=str(evidence),
        )
    run = update_workload_results(
        run.run_id,
        actor="worker",
        mlflow_run_id="mlflow-run-real",
        model_artifact_uri=str(artifact),
        model_artifact_sha256=sha256(artifact),
        evaluation_uri=str(artifact_root / "evidence" / "isolated_evaluation.json"),
        serving_endpoint="http://127.0.0.1:8191/infer",
        metrics_endpoint="http://127.0.0.1:8191/metrics",
    )
    release_gpu_lease(
        run.run_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        reason="staging validation completed",
    )

    completed = seal_workload_run(run.run_id, actor="worker")

    assert completed.state == "completed"
    assert completed.progress == 1.0
    index_path = Path(completed.evidence_index_uri or "")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["entry_count"] == 10
    assert completed.evidence_index_sha256 == sha256(index_path)
