from __future__ import annotations

import os
from threading import RLock
from time import monotonic

from fastapi import APIRouter, HTTPException, Query

from evm.control_panel.aggregation import build_latest_cycle
from evm.control_panel.cycle_catalog import build_cycle_catalog, find_cycle
from evm.control_panel.kubernetes_observer import (
    load_kubernetes_resource_snapshot,
    merge_runtime_resources,
)
from evm.control_panel.model_candidates import (
    ModelCandidateCatalog,
    ModelCandidateSelection,
    ModelCandidateSelectionBlocked,
    ModelCandidateSelectionRequest,
    build_model_candidate_catalog,
    get_model_selection,
    select_model_candidate,
)
from evm.control_panel.promotion_policy import evaluate_cycle_promotion
from evm.control_panel.schemas import (
    CycleRun,
    CycleRunList,
    EnvironmentTier,
    PromotionPolicyDecision,
    PromotionPolicyRequest,
    ResourceRef,
    RuntimeResource,
    RuntimeResourceList,
    State,
)


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel"])
_CYCLE_CACHE_LOCK = RLock()
_CYCLE_CACHE: CycleRun | None = None
_CYCLE_CACHE_AT = 0.0
_CYCLE_CACHE_BUILDER_ID = 0
_CYCLE_CATALOG_CACHE_LOCK = RLock()
_CYCLE_CATALOG_CACHE: dict[
    tuple[str, str, str, int, str], tuple[float, CycleRunList]
] = {}
_MODEL_CANDIDATE_CACHE_LOCK = RLock()
_MODEL_CANDIDATE_CACHE: ModelCandidateCatalog | None = None
_MODEL_CANDIDATE_CACHE_AT = 0.0
_MODEL_CANDIDATE_CACHE_CYCLE_ID = ""


def cycle_cache_ttl() -> float:
    try:
        return max(0.0, float(os.getenv("EVM_CONTROL_PANEL_CACHE_TTL_SECONDS", "2")))
    except ValueError:
        return 2.0


def model_candidate_cache_ttl() -> float:
    try:
        return max(0.0, float(os.getenv("EVM_MODEL_CANDIDATE_CACHE_TTL_SECONDS", "30")))
    except ValueError:
        return 30.0


def cycle_catalog_cache_ttl() -> float:
    try:
        return max(0.0, float(os.getenv("EVM_CYCLE_CATALOG_CACHE_TTL_SECONDS", "30")))
    except ValueError:
        return 30.0


def cycle_snapshot() -> CycleRun:
    global _CYCLE_CACHE, _CYCLE_CACHE_AT, _CYCLE_CACHE_BUILDER_ID
    with _CYCLE_CACHE_LOCK:
        now = monotonic()
        builder_id = id(build_latest_cycle)
        if (
            _CYCLE_CACHE is not None
            and _CYCLE_CACHE_BUILDER_ID == builder_id
            and now - _CYCLE_CACHE_AT < cycle_cache_ttl()
        ):
            return _CYCLE_CACHE
        _CYCLE_CACHE = build_latest_cycle()
        _CYCLE_CACHE_AT = monotonic()
        _CYCLE_CACHE_BUILDER_ID = builder_id
        return _CYCLE_CACHE


def invalidate_cycle_cache() -> None:
    global _CYCLE_CACHE, _CYCLE_CACHE_AT
    with _CYCLE_CACHE_LOCK:
        _CYCLE_CACHE = None
        _CYCLE_CACHE_AT = 0.0
    invalidate_model_candidate_cache()


def invalidate_model_candidate_cache() -> None:
    global _MODEL_CANDIDATE_CACHE, _MODEL_CANDIDATE_CACHE_AT, _MODEL_CANDIDATE_CACHE_CYCLE_ID
    with _MODEL_CANDIDATE_CACHE_LOCK:
        _MODEL_CANDIDATE_CACHE = None
        _MODEL_CANDIDATE_CACHE_AT = 0.0
        _MODEL_CANDIDATE_CACHE_CYCLE_ID = ""


def invalidate_cycle_catalog_cache() -> None:
    with _CYCLE_CATALOG_CACHE_LOCK:
        _CYCLE_CATALOG_CACHE.clear()


def cycle_catalog_snapshot(
    *,
    status: State | None = None,
    environment: EnvironmentTier | None = None,
    query: str | None = None,
    limit: int = 50,
) -> CycleRunList:
    live_cycle = cycle_snapshot()
    key = (
        status.value if status else "",
        environment.value if environment else "",
        (query or "").strip().lower(),
        limit,
        live_cycle.cycle_id,
    )
    with _CYCLE_CATALOG_CACHE_LOCK:
        now = monotonic()
        cached = _CYCLE_CATALOG_CACHE.get(key)
        if cached and now - cached[0] < cycle_catalog_cache_ttl():
            return cached[1]
        catalog = build_cycle_catalog(
            live_cycle,
            status=status,
            environment=environment,
            query=query,
            limit=limit,
        )
        _CYCLE_CATALOG_CACHE[key] = (monotonic(), catalog)
        return catalog


def model_candidate_catalog_snapshot(*, limit: int = 200) -> ModelCandidateCatalog:
    global _MODEL_CANDIDATE_CACHE, _MODEL_CANDIDATE_CACHE_AT, _MODEL_CANDIDATE_CACHE_CYCLE_ID
    live_cycle = cycle_snapshot()
    with _MODEL_CANDIDATE_CACHE_LOCK:
        now = monotonic()
        if (
            _MODEL_CANDIDATE_CACHE is None
            or _MODEL_CANDIDATE_CACHE_CYCLE_ID != live_cycle.cycle_id
            or now - _MODEL_CANDIDATE_CACHE_AT >= model_candidate_cache_ttl()
        ):
            _MODEL_CANDIDATE_CACHE = build_model_candidate_catalog(live_cycle, limit=1000)
            _MODEL_CANDIDATE_CACHE_AT = monotonic()
            _MODEL_CANDIDATE_CACHE_CYCLE_ID = live_cycle.cycle_id
        return _MODEL_CANDIDATE_CACHE.model_copy(
            update={"candidates": _MODEL_CANDIDATE_CACHE.candidates[:limit]}
        )


@router.get("/cycles/latest", response_model=CycleRun)
def latest_cycle() -> CycleRun:
    return cycle_snapshot()


@router.get("/cycles", response_model=CycleRunList)
def list_cycles(
    status: State | None = None,
    environment: EnvironmentTier | None = None,
    query: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> CycleRunList:
    return cycle_catalog_snapshot(
        status=status,
        environment=environment,
        query=query,
        limit=limit,
    )


@router.post("/promotion-policy/evaluate", response_model=PromotionPolicyDecision)
def evaluate_promotion_policy(request: PromotionPolicyRequest) -> PromotionPolicyDecision:
    return evaluate_cycle_promotion(cycle_snapshot(), request, persist=True)


@router.get("/cycles/{cycle_id}", response_model=CycleRun)
def get_cycle(cycle_id: str) -> CycleRun:
    cycle = find_cycle(cycle_id, cycle_snapshot())
    if cycle is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "cycle_not_found",
                "message": f"CycleRun is not available in the live or historical catalog: {cycle_id}",
            },
        )
    return cycle


@router.get("/model-candidates", response_model=ModelCandidateCatalog)
def list_model_candidates(limit: int = Query(default=200, ge=1, le=1000)) -> ModelCandidateCatalog:
    return model_candidate_catalog_snapshot(limit=limit)


@router.post(
    "/model-candidates/{candidate_key}/select",
    response_model=ModelCandidateSelection,
    status_code=202,
)
def select_candidate(
    candidate_key: str,
    request: ModelCandidateSelectionRequest,
) -> ModelCandidateSelection:
    try:
        return select_model_candidate(cycle_snapshot(), candidate_key, request)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "model_candidate_not_found", "candidate_key": candidate_key},
        ) from exc
    except ModelCandidateSelectionBlocked as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "model_candidate_selection_blocked", "blockers": exc.blockers},
        ) from exc


@router.get("/model-selections/{selection_id}", response_model=ModelCandidateSelection)
def read_model_selection(selection_id: str) -> ModelCandidateSelection:
    try:
        return get_model_selection(selection_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "model_selection_not_found", "selection_id": selection_id},
        ) from exc


@router.get("/resources", response_model=RuntimeResourceList)
def list_resources(namespace: str | None = None, owner_issue: str | None = None) -> RuntimeResourceList:
    return resources_for_cycle(
        cycle_snapshot(),
        namespace=namespace,
        owner_issue=owner_issue,
    )


def resources_for_cycle(
    cycle: CycleRun,
    *,
    namespace: str | None = None,
    owner_issue: str | None = None,
) -> RuntimeResourceList:
    resource_list = merge_runtime_resources(
        build_runtime_resources(cycle),
        load_kubernetes_resource_snapshot(),
    )
    resources = resource_list.resources
    if namespace:
        resources = [resource for resource in resources if resource.namespace == namespace]
    if owner_issue:
        resources = [resource for resource in resources if resource.owner_issue == owner_issue]
    return resource_list.model_copy(update={"resources": resources})


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
