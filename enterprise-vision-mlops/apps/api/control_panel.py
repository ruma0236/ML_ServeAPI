from __future__ import annotations

from fastapi import APIRouter, HTTPException

from evm.control_panel.aggregation import build_latest_cycle
from evm.control_panel.promotion_policy import evaluate_cycle_promotion
from evm.control_panel.schemas import (
    CycleRun,
    PromotionPolicyDecision,
    PromotionPolicyRequest,
    ResourceRef,
    RuntimeResource,
    RuntimeResourceList,
    State,
)


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel"])


@router.get("/cycles/latest", response_model=CycleRun)
def latest_cycle() -> CycleRun:
    return build_latest_cycle()


@router.post("/promotion-policy/evaluate", response_model=PromotionPolicyDecision)
def evaluate_promotion_policy(request: PromotionPolicyRequest) -> PromotionPolicyDecision:
    return evaluate_cycle_promotion(build_latest_cycle(), request, persist=True)


@router.get("/cycles/{cycle_id}", response_model=CycleRun)
def get_cycle(cycle_id: str) -> CycleRun:
    cycle = build_latest_cycle()
    if cycle.cycle_id != cycle_id:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "cycle_not_found",
                "message": f"Only latest local cycle is available: {cycle.cycle_id}",
            },
        )
    return cycle


@router.get("/resources", response_model=RuntimeResourceList)
def list_resources(namespace: str | None = None, owner_issue: str | None = None) -> RuntimeResourceList:
    cycle = build_latest_cycle()
    resources = build_runtime_resources(cycle)
    if namespace:
        resources = [resource for resource in resources if resource.namespace == namespace]
    if owner_issue:
        resources = [resource for resource in resources if resource.owner_issue == owner_issue]
    return RuntimeResourceList(resources=resources)


def build_runtime_resources(cycle: CycleRun) -> list[RuntimeResource]:
    resource_map: dict[str, ResourceRef] = {}
    stage_statuses: dict[str, list[State]] = {}
    stage_names: dict[str, list[str]] = {}
    last_transition: dict[str, str | None] = {}

    def add_ref(ref: ResourceRef, status: State, stage_name: str | None, timestamp: str | None) -> None:
        key = resource_key(ref)
        resource_map[key] = ref
        stage_statuses.setdefault(key, []).append(status)
        if stage_name:
            stage_names.setdefault(key, []).append(stage_name)
        if timestamp:
            last_transition[key] = timestamp

    for ref in cycle.resources:
        add_ref(ref, cycle.status, None, cycle.started_at)

    for stage in cycle.stages:
        for ref in stage.resources:
            add_ref(ref, stage.status, stage.name, stage.finished_at or stage.started_at)

    for ref in list(resource_map.values()):
        related_status = worst_status(stage_statuses.get(resource_key(ref), [cycle.status]))
        related_stages = stage_names.get(resource_key(ref), [])
        if ref.kind.lower() == "deployment":
            add_ref(
                ResourceRef(namespace=ref.namespace, kind="Service", name=ref.name),
                related_status,
                related_stages[0] if related_stages else None,
                last_transition.get(resource_key(ref)) or cycle.started_at,
            )
            add_ref(
                ResourceRef(namespace=ref.namespace, kind="Pod", name=f"{ref.name}-pod-template"),
                related_status,
                related_stages[0] if related_stages else None,
                last_transition.get(resource_key(ref)) or cycle.started_at,
            )
        if ref.name == "evm-minio":
            add_ref(
                ResourceRef(namespace=ref.namespace, kind="PersistentVolumeClaim", name="evm-minio-data"),
                related_status,
                related_stages[0] if related_stages else None,
                last_transition.get(resource_key(ref)) or cycle.started_at,
            )

    if any(ref.namespace == "evm-pipelines" for ref in resource_map.values()):
        add_ref(
            ResourceRef(namespace="evm-pipelines", kind="PersistentVolumeClaim", name="evm-f-drive-artifacts"),
            cycle.status,
            None,
            cycle.started_at,
        )

    return [
        RuntimeResource(
            resource_id=key,
            namespace=ref.namespace,
            kind=ref.kind,
            name=ref.name,
            status=worst_status(stage_statuses.get(key, [cycle.status])),
            readiness=readiness_for(worst_status(stage_statuses.get(key, [cycle.status]))),
            restarts=0,
            cpu_request=cpu_request_for(ref),
            memory_request=memory_request_for(ref),
            gpu_request=gpu_request_for(ref),
            storage_claim=storage_claim_for(ref),
            storage_root=storage_root_for(ref),
            node_pool=node_pool_for(ref),
            last_transition_time=last_transition.get(key) or cycle.started_at,
            owner_issue=cycle.owner_issue,
            control_actions=control_actions_for(ref),
            pressure=pressure_for(worst_status(stage_statuses.get(key, [cycle.status])), ref),
            related_stages=sorted(set(stage_names.get(key, []))),
        )
        for key, ref in sorted(resource_map.items())
    ]


def resource_key(ref: ResourceRef) -> str:
    return f"{ref.namespace}:{ref.kind}:{ref.name}"


def worst_status(statuses: list[State]) -> State:
    order: dict[State, int] = {
        "fail": 8,
        "blocked": 7,
        "cancelled": 6,
        "warn": 5,
        "running": 4,
        "queued": 3,
        "unknown": 2,
        "pass": 1,
        "done": 0,
    }
    return max(statuses, key=lambda item: order.get(item, 2))


def readiness_for(status: State) -> str:
    if status in {"pass", "done"}:
        return "ready"
    if status in {"running", "queued"}:
        return "progressing"
    if status == "unknown":
        return "not_available"
    return "blocked"


def node_pool_for(ref: ResourceRef) -> str:
    kind = ref.kind.lower()
    if kind in {"persistentvolumeclaim", "pvc"}:
        return "f-drive-local-storage"
    if "efficientnet" in ref.name or "training" in ref.name:
        return "windows-rtx-4080-super"
    if ref.namespace == "evm-pipelines":
        return "local-pipeline-workers"
    if ref.name in {"evm-api", "evm-mlflow", "evm-minio"}:
        return "local-compose-platform"
    return "docker-desktop"


def cpu_request_for(ref: ResourceRef) -> str | None:
    if ref.kind.lower() == "job":
        return "500m"
    if ref.name == "evm-api":
        return "250m"
    if ref.name in {"evm-mlflow", "evm-minio"}:
        return "500m"
    return None


def memory_request_for(ref: ResourceRef) -> str | None:
    if ref.name == "evm-minio":
        return "1Gi"
    if ref.kind.lower() == "job":
        return "1Gi"
    if ref.name in {"evm-api", "evm-mlflow"}:
        return "512Mi"
    return None


def gpu_request_for(ref: ResourceRef) -> str | None:
    return "1 x RTX 4080 SUPER" if "efficientnet" in ref.name or "training" in ref.name else None


def storage_claim_for(ref: ResourceRef) -> str | None:
    kind = ref.kind.lower()
    if kind in {"persistentvolumeclaim", "pvc"}:
        return ref.name
    if ref.name == "evm-minio":
        return "evm-minio-data"
    if ref.namespace == "evm-pipelines":
        return "evm-f-drive-artifacts"
    return None


def storage_root_for(ref: ResourceRef) -> str | None:
    kind = ref.kind.lower()
    if kind in {"persistentvolumeclaim", "pvc"} or ref.name in {"evm-minio", "evm-lakehouse-probe"}:
        return "F:/EnterpriseMLOps_Data/enterprise-vision-mlops"
    return None


def control_actions_for(ref: ResourceRef) -> list[str]:
    if ref.kind.lower() == "deployment":
        return ["view", "restart_dry_run", "scale_dry_run"]
    if ref.kind.lower() == "job":
        return ["view", "rerun_dry_run", "cancel_dry_run"]
    return ["view"]


def pressure_for(status: State, ref: ResourceRef) -> State:
    if status in {"blocked", "fail"}:
        return "warn"
    if "efficientnet" in ref.name or "training" in ref.name:
        return "queued"
    return "pass" if status in {"pass", "done"} else "unknown"
