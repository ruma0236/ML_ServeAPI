from __future__ import annotations

import copy
from pathlib import Path

import pytest

from evm.scale_validation.x1_contract import X1Contract
from evm.scale_validation.x1_topology import (
    X1TopologyError,
    kubernetes_resource_list,
    validate_kubernetes_resource_list,
    validate_runtime_topology_readback,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops")


def bundle() -> dict[str, object]:
    contract = X1Contract.from_path(
        ROOT / "configs/s8_v4_x1_heterogeneous_v1.toml",
        source_root=ROOT,
        data_root=DATA_ROOT,
    )
    return kubernetes_resource_list(
        contract,
        suite_id="x1-unit-topology-0001",
        source_revision="a" * 40,
        api_image="enterprise-vision-mlops-api:x1-unit",
        api_replicas=2,
        cpu_workers=4,
        profile_relative_root="artifacts/scale_validation/private/s8-v4/x1/unit/model-repositories/disabled",
        runtime_manifest_relative_path="artifacts/scale_validation/private/s8-v4/x1/unit/runtime.json",
        database_url="postgresql://unit@host.docker.internal:5434/unit",
        database_schema="evm_x1_unit",
        lease_id="lease-unit-0001",
        fencing_token="fencing-token-unit-0001",
    )


def validate(value: dict[str, object]) -> None:
    validate_kubernetes_resource_list(
        value,
        suite_id="x1-unit-topology-0001",
        source_revision="a" * 40,
        api_image="enterprise-vision-mlops-api:x1-unit",
        api_replicas=2,
        cpu_workers=4,
    )


def test_x1_kubernetes_bundle_freezes_real_server_topology() -> None:
    value = bundle()
    validate(value)
    serialized = str(value)
    assert "--workers', '4" in serialized
    assert "nvidia.com/gpu': '1" in serialized
    assert "--load-model=higgs_logistic_regression" in serialized
    assert "/usr/lib/wsl/lib" in serialized
    assert "/usr/lib/wsl/drivers" in serialized


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (
            lambda value: value["items"][0]["spec"]["template"]["spec"]["containers"][0][
                "resources"
            ]["limits"].__setitem__("nvidia.com/gpu", "0"),
            "x1_triton_gpu_limit",
        ),
        (
            lambda value: value["items"][0]["spec"]["template"]["spec"]["containers"][0][
                "volumeMounts"
            ].pop(0),
            "x1_triton_gpu_mounts",
        ),
        (
            lambda value: value["items"][0]["spec"]["template"]["spec"]["containers"][0][
                "volumeMounts"
            ].append(
                copy.deepcopy(
                    value["items"][0]["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][
                        0
                    ]
                )
            ),
            "x1_triton_gpu_mounts",
        ),
        (
            lambda value: value["items"][0]["spec"]["template"]["spec"]["volumes"][0][
                "hostPath"
            ].__setitem__("path", "/wrong/wsl/lib"),
            "x1_triton_gpu_volumes",
        ),
        (
            lambda value: value["items"][2]["spec"].__setitem__("replicas", 1),
            "x1_api_replica_readback",
        ),
        (
            lambda value: value["items"][2]["spec"]["template"]["spec"]["containers"][0][
                "args"
            ].__setitem__(-1, "2"),
            "x1_server_worker_command",
        ),
    ],
)
def test_x1_kubernetes_bundle_rejects_topology_mutation(mutator: object, reason: str) -> None:
    value = copy.deepcopy(bundle())
    mutator(value)  # type: ignore[operator]
    with pytest.raises(X1TopologyError, match=reason):
        validate(value)


def test_x1_runtime_readback_requires_each_pod_and_each_server_worker() -> None:
    snapshot = {
        "triton_pods_ready": 1,
        "triton_gpu_limits": 1,
        "api_pods_ready": 2,
        "observed_api_pod_uids": ["pod-a", "pod-b"],
        "observed_worker_slots_by_pod": {
            "pod-a": ["pod-a:1", "pod-a:2"],
            "pod-b": ["pod-b:3", "pod-b:4"],
        },
        "client_lanes_are_server_workers": False,
    }
    validate_runtime_topology_readback(snapshot, expected_replicas=2, expected_workers=2)
    snapshot["observed_worker_slots_by_pod"]["pod-b"].pop()
    with pytest.raises(X1TopologyError, match="x1_runtime_worker_count"):
        validate_runtime_topology_readback(snapshot, expected_replicas=2, expected_workers=2)
