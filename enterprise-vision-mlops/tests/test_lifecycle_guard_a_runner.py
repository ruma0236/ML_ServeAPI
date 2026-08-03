from __future__ import annotations

from pathlib import Path

import pytest

from evm.operations.lifecycle_guard_a_runner import (
    ModelIdentity,
    ScenarioAIntegrationError,
    TargetSnapshot,
    active_pod,
    build_deployment_patch,
    consume_approval,
    container_identity,
    issue_approval,
    write_pointer,
)
from evm.operations.failure_scenarios import TargetRef


def identity(digest: str = "a" * 64) -> ModelIdentity:
    return ModelIdentity(
        candidate_id="candidate-m1",
        dataset_version="dataset-v1",
        model_sha256=digest,
        image_digest="serving@sha256:" + "b" * 64,
        model_path="/mnt/evm-data/model.pt",
    )


def deployment() -> dict:
    return {
        "metadata": {"uid": "deployment-uid", "resourceVersion": "17", "generation": 4},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "serving",
                            "image": "serving@sha256:" + "b" * 64,
                            "env": [
                                {"name": "EVM_MODEL_CANDIDATE_ID", "value": "candidate-m1"},
                                {"name": "EVM_DATASET_VERSION", "value": "dataset-v1"},
                                {"name": "EVM_MODEL_SHA256", "value": "a" * 64},
                                {"name": "EVM_MODEL_PATH", "value": "/mnt/evm-data/model.pt"},
                            ],
                        }
                    ]
                }
            }
        },
    }


def snapshot() -> TargetSnapshot:
    return TargetSnapshot(
        captured_at="2026-08-03T00:00:00Z",
        deployment_uid="deployment-uid",
        deployment_resource_version="17",
        deployment_generation=4,
        pod_name="pod-a",
        pod_uid="pod-uid-a",
        identity=identity(),
        deployment=deployment(),
        pod={"metadata": {"name": "pod-a", "uid": "pod-uid-a"}},
        readiness={},
        prediction={},
        prometheus={},
    )


def test_container_identity_is_exact() -> None:
    assert container_identity(deployment(), "serving") == identity()


def test_active_pod_excludes_historical_and_requires_one() -> None:
    current = {"metadata": {"uid": "current"}, "status": {"phase": "Running"}}
    failed = {"metadata": {"uid": "old"}, "status": {"phase": "Failed"}}
    assert active_pod([failed, current]) == current
    with pytest.raises(ScenarioAIntegrationError, match="active_pod_cardinality:0"):
        active_pod([failed])


def test_patch_binds_resource_version_and_model_identity() -> None:
    target = identity("c" * 64).model_copy(
        update={"candidate_id": "candidate-m2", "model_path": "/mnt/evm-data/m2.pt"}
    )
    patch = build_deployment_patch(
        snapshot(),
        target,
        transaction_id="transaction-1",
        action="apply_m1",
    )
    assert patch["metadata"]["resourceVersion"] == "17"
    container = patch["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "serving"
    assert {item["name"]: item["value"] for item in container["env"]} == {
        "EVM_MODEL_PATH": "/mnt/evm-data/m2.pt",
        "EVM_MODEL_SHA256": "c" * 64,
        "EVM_MODEL_CANDIDATE_ID": "candidate-m2",
        "EVM_DATASET_VERSION": "dataset-v1",
    }


def test_stable_pointer_uses_compare_and_swap(tmp_path: Path) -> None:
    path = tmp_path / "stable-pointer.json"
    m0 = identity("a" * 64)
    m1 = identity("c" * 64)
    first = write_pointer(
        path,
        expected_digest=None,
        identity=m1,
        deployment_uid="deployment-uid",
        transaction_id="transaction-1",
        state="m1_committed",
    )
    assert first["revision"] == 1
    with pytest.raises(ScenarioAIntegrationError, match="stable_pointer_cas_failed"):
        write_pointer(
            path,
            expected_digest=m0.model_sha256,
            identity=m0,
            deployment_uid="deployment-uid",
            transaction_id="transaction-1",
            state="m0_restored",
        )
    second = write_pointer(
        path,
        expected_digest=m1.model_sha256,
        identity=m0,
        deployment_uid="deployment-uid",
        transaction_id="transaction-1",
        state="m0_restored",
    )
    assert second["revision"] == 2


def test_approval_is_consumed_only_at_action_boundary(tmp_path: Path) -> None:
    target = TargetRef(namespace="evm-production", name="evm-b0-production", uid="uid-1")
    approval = issue_approval(
        tmp_path,
        run_id="transaction-1",
        target=target,
        action="apply_m1:contract-digest",
        source_revision="a" * 40,
        approver="maintenance-owner",
        ttl_seconds=300,
    )
    assert approval["consumed"] is False
    assert not list((tmp_path / "approvals").glob("*.consumed.json"))
    consumed = consume_approval(
        tmp_path,
        approval,
        run_id="transaction-1",
        target=target,
        source_revision="a" * 40,
    )
    assert consumed["consumed"] is True
    assert consumed["replay_blocked"] is True
