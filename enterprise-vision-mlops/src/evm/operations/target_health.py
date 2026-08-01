from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from evm.operations.runtime_adapters import ScenarioAObservation


class ScenarioAIdentityContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service: str
    candidate_id: str
    dataset_version: str
    model_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    device: str = "cuda"


class TargetHealthCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str
    passed: bool
    observed: Any
    reason_code: str | None = None


class ScenarioAHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    checks: list[TargetHealthCheck]
    blockers: list[str]
    historical_pod_count: int = Field(ge=0)
    observed_identity: dict[str, str]


def _pod_ready(pod: dict[str, Any]) -> bool:
    conditions = (pod.get("status") or {}).get("conditions") or []
    return any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in conditions
    )


def _pod_image_digest(pod: dict[str, Any]) -> str:
    statuses = (pod.get("status") or {}).get("containerStatuses") or []
    if len(statuses) != 1:
        return ""
    image_id = str(statuses[0].get("imageID") or "")
    return image_id.rsplit("@", maxsplit=1)[-1] if "@" in image_id else ""


def evaluate_scenario_a_health(
    observation: ScenarioAObservation,
    identity: ScenarioAIdentityContract,
) -> ScenarioAHealth:
    node_status = observation.node.get("status") or {}
    capacity = (node_status.get("capacity") or {}).get("nvidia.com/gpu")
    allocatable = (node_status.get("allocatable") or {}).get("nvidia.com/gpu")
    plugin_status = observation.device_plugin.get("status") or {}
    deployment_status = observation.deployment.get("status") or {}
    pod_status = observation.pod.get("status") or {}
    ready = observation.readiness
    image_digest = _pod_image_digest(observation.pod)
    observed_identity = {
        "service": str(ready.get("service") or ""),
        "candidate_id": str(ready.get("candidate_id") or ""),
        "dataset_version": str(ready.get("dataset_version") or ""),
        "model_sha256": str(ready.get("model_sha256") or ""),
        "image_digest": image_digest,
        "device": str(ready.get("device") or ""),
    }

    values = [
        ("node_gpu_capacity", capacity == "1" and allocatable == "1", [capacity, allocatable]),
        (
            "device_plugin_ready",
            plugin_status.get("desiredNumberScheduled") == 1
            and plugin_status.get("numberReady") == 1
            and plugin_status.get("numberAvailable") == 1,
            plugin_status,
        ),
        (
            "deployment_ready",
            deployment_status.get("replicas") == 1
            and deployment_status.get("readyReplicas") == 1
            and deployment_status.get("availableReplicas") == 1,
            deployment_status,
        ),
        (
            "exact_pod_ready",
            pod_status.get("phase") == "Running" and _pod_ready(observation.pod),
            {
                "phase": pod_status.get("phase"),
                "uid": (observation.pod.get("metadata") or {}).get("uid"),
            },
        ),
        (
            "readiness_cuda",
            ready.get("status") == "ok"
            and ready.get("model_loaded") is True
            and ready.get("cuda_available") is True,
            ready,
        ),
        (
            "prometheus_target_up",
            observation.prometheus_target.get("health") == "up"
            and not observation.prometheus_target.get("lastError"),
            {
                "health": observation.prometheus_target.get("health"),
                "lastError": observation.prometheus_target.get("lastError"),
            },
        ),
    ]
    expected_identity = identity.model_dump()
    for field_name, expected in expected_identity.items():
        values.append(
            (
                f"identity_{field_name}",
                observed_identity.get(field_name) == expected,
                {"expected": expected, "observed": observed_identity.get(field_name)},
            )
        )
    values.append(
        (
            "historical_pods_not_active_blockers",
            True,
            {"historical_pod_count": len(observation.historical_pods)},
        )
    )

    checks = [
        TargetHealthCheck(
            check_id=check_id,
            passed=passed,
            observed=observed,
            reason_code=None if passed else f"{check_id}_failed",
        )
        for check_id, passed, observed in values
    ]
    blockers = [check.reason_code for check in checks if check.reason_code]
    return ScenarioAHealth(
        decision="passed" if not blockers else "blocked",
        checks=checks,
        blockers=blockers,
        historical_pod_count=len(observation.historical_pods),
        observed_identity=observed_identity,
    )
