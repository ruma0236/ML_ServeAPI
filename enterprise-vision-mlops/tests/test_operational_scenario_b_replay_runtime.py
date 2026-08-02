from __future__ import annotations

import json
from pathlib import Path

import pytest

from evm.operations.scenario_b_canary import (
    CanaryPolicy,
    InferenceObservation,
    ModelIdentity,
    ReplayRequest,
    build_assignment_routes,
)
from evm.operations.scenario_b_replay_runtime import (
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

