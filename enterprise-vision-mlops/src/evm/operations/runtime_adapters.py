from __future__ import annotations

import json
import subprocess
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


JsonObject = dict[str, Any]
CommandRunner = Callable[[list[str]], JsonObject]
UrlReader = Callable[[str], JsonObject]


class ExactSelectionError(RuntimeError):
    pass


class ResourceSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    name: str
    uid: str
    namespace: str | None = None


class PrometheusTargetSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job: str
    instance: str


def default_command_runner(args: list[str]) -> JsonObject:
    completed = subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


def default_url_reader(url: str) -> JsonObject:
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - local allowlist
        return json.loads(response.read().decode("utf-8"))


def _identity(item: JsonObject) -> tuple[str | None, str | None, str | None]:
    metadata = item.get("metadata") or {}
    return metadata.get("namespace"), metadata.get("name"), metadata.get("uid")


def select_exact(items: list[JsonObject], selector: ResourceSelector) -> JsonObject:
    matches = []
    for item in items:
        namespace, name, uid = _identity(item)
        if selector.namespace is not None and namespace != selector.namespace:
            continue
        if name == selector.name and uid == selector.uid:
            matches.append(item)
    if len(matches) != 1:
        raise ExactSelectionError(
            "exact_resource_selection_failed:"
            f"{selector.kind}:{selector.namespace or '_cluster'}/{selector.name}:"
            f"uid={selector.uid}:matches={len(matches)}"
        )
    return matches[0]


def select_prometheus_target(
    payload: JsonObject,
    selector: PrometheusTargetSelector,
) -> JsonObject:
    targets = ((payload.get("data") or {}).get("activeTargets") or [])
    matches = [
        target
        for target in targets
        if (target.get("labels") or {}).get("job") == selector.job
        and (target.get("labels") or {}).get("instance") == selector.instance
    ]
    if len(matches) != 1:
        raise ExactSelectionError(
            "prometheus_target_selection_failed:"
            f"job={selector.job}:instance={selector.instance}:matches={len(matches)}"
        )
    return matches[0]


class KubernetesAdapter:
    def __init__(self, runner: CommandRunner = default_command_runner) -> None:
        self.runner = runner

    def get_exact(self, selector: ResourceSelector) -> JsonObject:
        args = ["kubectl", "get", selector.kind, selector.name]
        if selector.namespace:
            args.extend(["-n", selector.namespace])
        args.extend(["-o", "json"])
        payload = self.runner(args)
        return select_exact([payload], selector)

    def list_by_label(self, *, kind: str, namespace: str, label: str) -> list[JsonObject]:
        payload = self.runner(
            ["kubectl", "get", kind, "-n", namespace, "-l", label, "-o", "json"]
        )
        return payload.get("items") or []


class HttpAdapter:
    def __init__(self, reader: UrlReader = default_url_reader) -> None:
        self.reader = reader

    def get_json(self, url: str) -> JsonObject:
        return self.reader(url)


TERMINAL_POD_PHASES = {"Failed", "Succeeded"}
TERMINAL_POD_REASONS = {
    "Completed",
    "ContainerStatusUnknown",
    "DeadlineExceeded",
    "Evicted",
    "NodeLost",
    "UnexpectedAdmissionError",
}


def pod_is_historical(item: JsonObject) -> bool:
    status = item.get("status") or {}
    if status.get("phase") in TERMINAL_POD_PHASES:
        return True
    if status.get("reason") in TERMINAL_POD_REASONS:
        return True
    return bool((item.get("metadata") or {}).get("deletionTimestamp"))


@dataclass(frozen=True)
class ScenarioASelectors:
    node: ResourceSelector
    device_plugin: ResourceSelector
    deployment: ResourceSelector
    pod: ResourceSelector
    pod_label: str
    prometheus: PrometheusTargetSelector
    readiness_url: str
    prometheus_targets_url: str


class ScenarioAObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: JsonObject
    device_plugin: JsonObject
    deployment: JsonObject
    pod: JsonObject
    historical_pods: list[JsonObject] = Field(default_factory=list)
    prometheus_target: JsonObject
    readiness: JsonObject


class ScenarioACollector:
    def __init__(
        self,
        kubernetes: KubernetesAdapter,
        http: HttpAdapter,
    ) -> None:
        self.kubernetes = kubernetes
        self.http = http

    def collect(self, selectors: ScenarioASelectors) -> ScenarioAObservation:
        node = self.kubernetes.get_exact(selectors.node)
        plugin = self.kubernetes.get_exact(selectors.device_plugin)
        deployment = self.kubernetes.get_exact(selectors.deployment)
        pods = self.kubernetes.list_by_label(
            kind="pod",
            namespace=selectors.pod.namespace or "",
            label=selectors.pod_label,
        )
        pod = select_exact(pods, selectors.pod)
        active = [item for item in pods if not pod_is_historical(item)]
        if len(active) != 1 or _identity(active[0]) != _identity(pod):
            raise ExactSelectionError(
                f"active_pod_cardinality_failed:expected_uid={selectors.pod.uid}:"
                f"active={len(active)}"
            )
        historical = [item for item in pods if pod_is_historical(item)]
        targets = self.http.get_json(selectors.prometheus_targets_url)
        prometheus_target = select_prometheus_target(targets, selectors.prometheus)
        readiness = self.http.get_json(selectors.readiness_url)
        return ScenarioAObservation(
            node=node,
            device_plugin=plugin,
            deployment=deployment,
            pod=pod,
            historical_pods=historical,
            prometheus_target=prometheus_target,
            readiness=readiness,
        )
