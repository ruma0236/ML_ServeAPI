from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evm.operations.failure_evidence import validate_closure
from evm.operations.scenario_b_canary import (
    CanaryPolicy,
    InferenceObservation,
    ModelIdentity,
    QualityMetrics,
    ReplayRequest,
    build_assignment_routes,
    run_controlled_replay,
)
from evm.operations.scenario_b_replay_runtime import (
    ReplayExecutionContext,
    _scenario_b_report,
    inject_controlled_errors,
    load_replay_records,
    source_image_uri,
)


def _policy() -> CanaryPolicy:
    return CanaryPolicy(
        policy_id="runtime-test",
        assignment_seed="fixed",
        min_shadow_requests=5,
        total_replay_requests=10,
        challenger_requests=2,
        max_challenger_fraction=0.2,
        min_accuracy=0.8,
        min_f1=0.75,
        min_auroc=0.8,
        max_latency_p95_ms=30,
        max_error_rate=0.01,
        stop_budget_seconds=30,
        rollback_budget_seconds=300,
        signal_precedence=["identity", "error_rate", "latency", "quality"],
    )


def _requests() -> list[ReplayRequest]:
    return [
        ReplayRequest(
            request_id=f"r-{index}",
            content_digest=f"{index:064x}",
            image_uri=f"file:///F:/data/{index}.jpg",
            expected_label="normal",
        )
        for index in range(10)
    ]


def test_source_image_uri_rejects_path_traversal() -> None:
    with pytest.raises(ValueError, match="unsafe_replay_relative_path"):
        source_image_uri("../secret.jpg", "F:/root")


def test_load_replay_records_binds_ct_and_source_paths(tmp_path: Path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"image")
    import hashlib

    digest = hashlib.sha256(b"image").hexdigest()
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "ct_record_id": "ct-1",
                "content_sha256": digest,
                "image_path": str(image.resolve()),
                "label": "normal",
                "metadata": {"relative_path": "pcb1/Data/Images/Normal/1.JPG"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    records = load_replay_records(
        manifest,
        count=1,
        host_data_root="F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
        verify_content=True,
    )

    assert records[0].challenger_image_path == image.resolve()
    assert records[0].request.image_uri.endswith(
        "/data/raw/industrial/visa/pcb1/Data/Images/Normal/1.JPG"
    )


def test_failure_overlay_only_changes_deterministic_challenger_requests() -> None:
    model = ModelIdentity(
        candidate_id="candidate",
        architecture="efficientnet-b7",
        dataset_version="dataset",
        model_digest="a" * 64,
        image_digest="b" * 64,
    )
    observations = [
        InferenceObservation(
            request_id=request.request_id,
            model_digest=model.model_digest,
            latency_ms=5,
            succeeded=True,
            prediction="normal",
            confidence=0.9,
        )
        for request in _requests()
    ]
    effective, injection = inject_controlled_errors(
        observations,
        requests=_requests(),
        policy=_policy(),
        count=1,
    )
    routes = build_assignment_routes(_requests(), policy=_policy())
    failed = [item.request_id for item in effective if not item.succeeded]

    assert len(failed) == 1
    assert routes[failed[0]][0] == "challenger"
    assert all(item.succeeded for item in observations)
    assert injection["raw_observations_mutated"] is False
    assert injection["production_endpoint_mutated"] is False


def test_scenario_b_report_passes_common_live_proof_contract(tmp_path: Path) -> None:
    policy = _policy()
    requests = _requests()
    stable = ModelIdentity(
        candidate_id="stable",
        architecture="efficientnet-b0",
        dataset_version="dataset-v1",
        model_digest="a" * 64,
        image_digest="b" * 64,
    )
    challenger = ModelIdentity(
        candidate_id="challenger",
        architecture="efficientnet-b7",
        dataset_version="dataset-v2",
        model_digest="c" * 64,
        image_digest=stable.image_digest,
    )
    stable_observations = [
        InferenceObservation(
            request_id=request.request_id,
            model_digest=stable.model_digest,
            latency_ms=4,
            succeeded=True,
            prediction="normal",
            confidence=0.9,
        )
        for request in requests
    ]
    raw_challenger = [
        InferenceObservation(
            request_id=request.request_id,
            model_digest=challenger.model_digest,
            latency_ms=5,
            succeeded=True,
            prediction="normal",
            confidence=0.9,
        )
        for request in requests
    ]
    effective, injection = inject_controlled_errors(
        raw_challenger,
        requests=requests,
        policy=policy,
        count=1,
    )
    started = datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)
    result = run_controlled_replay(
        run_id="scenario-b-report-test",
        policy=policy,
        stable=stable,
        challenger=challenger,
        requests=requests,
        stable_observations=stable_observations,
        challenger_observations=effective,
        challenger_quality=QualityMetrics(accuracy=0.9, f1=0.8, auroc=0.9),
        started_at=started,
        stop_seconds=1,
        rollback_seconds=2,
    )
    run_root = tmp_path / "scenario-b-report-test"
    run_root.mkdir()
    runtime_path = run_root / "runtime.json"
    runtime_path.write_text("{}", encoding="utf-8")
    readiness = {
        "readiness": {
            "status": "ok",
            "cuda_available": True,
            "model_sha256": stable.model_digest,
        },
        "prometheus": {"health": "up"},
        "inference": {
            "request_id": "r-0",
            "model_digest": stable.model_digest,
            "latency_ms": 4,
            "succeeded": True,
            "prediction": "normal",
            "confidence": 0.9,
            "failure_code": None,
        },
    }
    report = _scenario_b_report(
        result=result,
        expected_state="rolled_back",
        expected_blocker="runtime_error_rate_exceeded",
        context=ReplayExecutionContext(
            source_commit="abcdef1",
            source_branch="test",
            source_dirty=False,
            api_revision="runtime1",
            worker_revision="runtime1",
            observer_revision="runtime1",
            cluster_context="docker-desktop",
            node="docker-desktop",
            target_namespace="evm-production",
            target_name="evm-b0-production",
            target_uid="uid-1",
            actor="pytest",
        ),
        stable_before=readiness,
        stable_after=readiness,
        cuda_runtime={
            "cuda_device_name": "test-gpu",
            "gpu_memory_peak_mb": 128,
            "torch_version": "test",
            "torchvision_version": "test",
        },
        injection=injection,
        manifest_digest="d" * 64,
        candidate_summary_digest="e" * 64,
        stable_observations=stable_observations,
        raw_challenger=raw_challenger,
        artifact_paths={"runtime": runtime_path},
        runtime_root=run_root,
        canonical_root=tmp_path,
        audit_started_at=started,
        audit_finished_at=started + timedelta(seconds=4),
        monotonic_started_ns=1_000_000_000,
        injection_monotonic_ns=2_000_000_000,
        detection_monotonic_ns=3_000_000_000,
        recovery_monotonic_ns=4_000_000_000,
        monotonic_finished_ns=5_000_000_000,
    )

    assert report.status == "passed"
    assert report.decision.observed == "rolled_back"
    assert report.injection.performed is True
    assert any(
        item.check_id == "post_replay_inference" and item.passed for item in report.postconditions
    )
    assert validate_closure(report, "live_proof") == []
