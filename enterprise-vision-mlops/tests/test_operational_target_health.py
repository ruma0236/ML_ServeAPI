from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from evm.operations.runtime_adapters import (
    ExactSelectionError,
    HttpAdapter,
    KubernetesAdapter,
    PrometheusTargetSelector,
    ResourceSelector,
    ScenarioACollector,
    ScenarioASelectors,
    select_exact,
    select_prometheus_target,
)
from evm.operations.target_health import (
    ScenarioAIdentityContract,
    evaluate_scenario_a_health,
)


FIXTURES = Path(__file__).parent / "fixtures" / "operations"
IMAGE_DIGEST = "sha256:" + "b" * 64


def _payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _selectors() -> ScenarioASelectors:
    return ScenarioASelectors(
        node=ResourceSelector(kind="node", name="docker-desktop", uid="node-uid"),
        device_plugin=ResourceSelector(
            kind="daemonset",
            namespace="kube-system",
            name="nvidia-device-plugin-daemonset",
            uid="plugin-uid",
        ),
        deployment=ResourceSelector(
            kind="deployment",
            namespace="evm-production",
            name="evm-b0-production",
            uid="deployment-uid",
        ),
        pod=ResourceSelector(
            kind="pod",
            namespace="evm-production",
            name="evm-b0-production-current",
            uid="pod-current",
        ),
        pod_label="app.kubernetes.io/name=evm-b0-production",
        prometheus=PrometheusTargetSelector(
            job="evm-b0-production",
            instance="host.docker.internal:30800",
        ),
        readiness_url="http://ready",
        prometheus_targets_url="http://prometheus",
    )


def _collector(kubernetes_payload: dict) -> ScenarioACollector:
    objects = {
        "node": kubernetes_payload["node"],
        "daemonset": kubernetes_payload["device_plugin"],
        "deployment": kubernetes_payload["deployment"],
    }

    def runner(args: list[str]) -> dict:
        if args[2] == "pod":
            return kubernetes_payload["pods"]
        return objects[args[2]]

    prometheus = _payload("scenario_a_prometheus_baseline.json")
    readiness = _payload("scenario_a_readiness_baseline.json")
    return ScenarioACollector(
        KubernetesAdapter(runner),
        HttpAdapter(lambda url: readiness if url == "http://ready" else prometheus),
    )


def _identity() -> ScenarioAIdentityContract:
    return ScenarioAIdentityContract(
        service="evm-b0-production",
        candidate_id="effnet-b0-img224-expedited-adamw",
        dataset_version="visa-open-data-e35d93d5561f",
        model_sha256="a" * 64,
        image_digest=IMAGE_DIGEST,
        device="cuda",
    )


def test_historical_terminal_pods_do_not_block_exact_active_target() -> None:
    observation = _collector(
        _payload("scenario_a_kubernetes_baseline.json")
    ).collect(_selectors())
    health = evaluate_scenario_a_health(observation, _identity())

    assert health.decision == "passed"
    assert health.historical_pod_count == 2
    assert health.blockers == []


def test_zero_exact_resource_match_fails_closed() -> None:
    payload = _payload("scenario_a_kubernetes_baseline.json")
    with pytest.raises(ExactSelectionError, match="matches=0"):
        select_exact(payload["pods"]["items"], _selectors().pod.model_copy(update={"uid": "x"}))


def test_multiple_exact_resource_match_fails_closed() -> None:
    payload = _payload("scenario_a_kubernetes_baseline.json")
    payload["pods"]["items"].append(deepcopy(payload["pods"]["items"][0]))
    with pytest.raises(ExactSelectionError, match="matches=2"):
        select_exact(payload["pods"]["items"], _selectors().pod)


def test_second_non_terminal_pod_fails_active_cardinality() -> None:
    payload = _payload("scenario_a_kubernetes_baseline.json")
    second = deepcopy(payload["pods"]["items"][0])
    second["metadata"].update({"name": "other", "uid": "other-uid"})
    payload["pods"]["items"].append(second)

    with pytest.raises(ExactSelectionError, match="active_pod_cardinality_failed"):
        _collector(payload).collect(_selectors())


def test_prometheus_zero_and_multiple_match_fail_closed() -> None:
    payload = _payload("scenario_a_prometheus_baseline.json")
    selector = _selectors().prometheus
    assert select_prometheus_target(payload, selector)["health"] == "up"
    payload["data"]["activeTargets"].append(deepcopy(payload["data"]["activeTargets"][0]))
    with pytest.raises(ExactSelectionError, match="matches=2"):
        select_prometheus_target(payload, selector)
    with pytest.raises(ExactSelectionError, match="matches=0"):
        select_prometheus_target(
            payload,
            selector.model_copy(update={"instance": "missing"}),
        )


def test_identity_subset_mismatch_blocks_health() -> None:
    observation = _collector(
        _payload("scenario_a_kubernetes_baseline.json")
    ).collect(_selectors())
    identity = _identity().model_copy(update={"model_sha256": "c" * 64})
    health = evaluate_scenario_a_health(observation, identity)

    assert health.decision == "blocked"
    assert "identity_model_sha256_failed" in health.blockers
