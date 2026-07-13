from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from evm.control_panel.operations import create_task_assignment, dispatch_task_assignment
from evm.control_panel.schemas import ContractModel, TaskAssignment, TaskAssignmentRequest
from evm.core.config import map_runtime_data_path


ScenarioDataState = Literal[
    "verified",
    "ready",
    "review_required",
    "running",
    "failed",
    "not_started",
]
ScenarioAdapterState = Literal["verified", "not_implemented", "blocked"]
ScenarioReadiness = Literal[
    "verified_full_lifecycle",
    "data_ready",
    "intake_ready",
    "running",
    "blocked",
]


class ScenarioTransform(ContractModel):
    transform_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ScenarioDataset(ContractModel):
    dataset_id: str
    dataset_name: str
    dataset_version: str
    source_url: str
    source_revision: str
    license_id: str
    license_url: str
    usage_policy: str
    manifest_uri: str
    split_manifest_uri: str
    source_size_bytes: int = Field(default=0, ge=0)


class ScenarioIntakeState(ContractModel):
    status: str
    phase: str
    progress: float = Field(ge=0, le=1)
    records_processed: int = Field(default=0, ge=0)
    records_output: int = Field(default=0, ge=0)
    updated_at: str | None = None
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    quality_status: str | None = None
    quality_report_uri: str | None = None
    source_registry_uri: str | None = None


class EnterpriseScenario(ContractModel):
    scenario_id: str
    display_name: str
    department: str
    business_outcome: str
    modality: Literal["image", "text", "image_text"]
    readiness: ScenarioReadiness
    data_readiness: ScenarioDataState
    model_readiness: ScenarioAdapterState
    deployment_readiness: ScenarioAdapterState
    intake_supported: bool
    profile_template: dict[str, Any] | None = None
    model_component_id: str | None = None
    dataset: ScenarioDataset
    recipe_id: str
    recipe_version: str
    transforms: list[ScenarioTransform] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    intake_state: ScenarioIntakeState | None = None
    config_uri: str
    runtime_config_uri: str


class EnterpriseScenarioCatalog(ContractModel):
    schema_version: Literal["evm.enterprise_scenario_catalog.v1"] = (
        "evm.enterprise_scenario_catalog.v1"
    )
    catalog_digest: str
    scenarios: list[EnterpriseScenario] = Field(default_factory=list)


class ScenarioIntakeLaunchRequest(ContractModel):
    actor: str = "ml-platform"
    reason: str = "Acquire and preprocess an approved enterprise scenario dataset"
    dry_run: bool = True


class ScenarioCatalogError(RuntimeError):
    pass


def scenario_config_root() -> Path:
    configured = os.getenv("EVM_SCENARIO_CONFIG_ROOT", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "configs" / "scenarios"


def read_scenario_catalog() -> EnterpriseScenarioCatalog:
    payloads: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(scenario_config_root().glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != "evm.scenario_intake.v1":
            raise ScenarioCatalogError(f"scenario_config_contract_invalid:{path.name}")
        payloads.append((path, payload))
    if not payloads:
        raise ScenarioCatalogError("scenario_catalog_empty")
    canonical = [payload for _, payload in payloads]
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()
    return EnterpriseScenarioCatalog(
        catalog_digest=digest,
        scenarios=[scenario_from_config(path, payload) for path, payload in payloads],
    )


def get_scenario(scenario_id: str) -> EnterpriseScenario | None:
    return next(
        (item for item in read_scenario_catalog().scenarios if item.scenario_id == scenario_id),
        None,
    )


def launch_scenario_intake(
    scenario_id: str,
    request: ScenarioIntakeLaunchRequest,
) -> TaskAssignment:
    scenario = get_scenario(scenario_id)
    if scenario is None:
        raise ScenarioCatalogError("scenario_not_found")
    if not scenario.intake_supported:
        raise ScenarioCatalogError("scenario_intake_not_supported")
    task = create_task_assignment(
        TaskAssignmentRequest(
            cycle_id=f"scenario:{scenario.scenario_id}",
            task_type="airflow_dag_run",
            owner=request.actor,
            priority="normal",
            resource_profile="local-pipeline-workers",
            requester_team=scenario.department,
            approval_policy="auto",
            dry_run=request.dry_run,
            config_payload={
                "dag_id": "enterprise_mlops_scenario_intake",
                "scenario_id": scenario.scenario_id,
                "dataset_version": scenario.dataset.dataset_version,
                "pipeline_config_uri": scenario.runtime_config_uri,
                "reason": request.reason,
            },
        )
    )
    if not request.dry_run and task.status == "queued":
        dispatched = dispatch_task_assignment(task.task_id)
        if dispatched is None:
            raise ScenarioCatalogError("scenario_intake_dispatch_failed")
        return dispatched
    return task


def scenario_from_config(path: Path, payload: dict[str, Any]) -> EnterpriseScenario:
    scenario = required_object(payload, "scenario")
    dataset = required_object(payload, "dataset")
    acquisition = required_object(payload, "acquisition")
    preprocessing = required_object(payload, "preprocessing")
    output_root = required_text(dataset, "output_root")
    state_path = map_runtime_data_path(output_root) / "evidence" / "intake_state.json"
    intake_state = read_intake_state(state_path)
    manifest = map_runtime_data_path(required_text(dataset, "manifest_uri"))
    split_manifest = map_runtime_data_path(required_text(dataset, "split_manifest_uri"))
    model_readiness = str(scenario.get("model_readiness") or "not_implemented")
    deployment_readiness = str(scenario.get("deployment_readiness") or "not_implemented")
    intake_supported = bool(scenario.get("intake_supported"))
    if intake_state and intake_state.status == "running":
        data_readiness: ScenarioDataState = "running"
    elif intake_state and intake_state.status == "failed":
        data_readiness = "failed"
    elif intake_state and intake_state.quality_status == "review_required":
        data_readiness = "review_required"
    elif manifest.is_file() and split_manifest.is_file():
        data_readiness = "verified" if not intake_supported else "ready"
    else:
        data_readiness = "not_started"
    blockers = [str(item) for item in scenario.get("platform_blockers", []) if item]
    if intake_state and intake_state.status == "failed":
        blockers.extend(intake_state.blockers)
    if intake_state and intake_state.quality_status == "review_required":
        blockers.extend(intake_state.warnings or ["data_quality_review_required"])
    if data_readiness == "running":
        readiness: ScenarioReadiness = "running"
    elif (
        data_readiness in {"verified", "ready"}
        and model_readiness == "verified"
        and deployment_readiness == "verified"
        and not blockers
    ):
        readiness = "verified_full_lifecycle"
    elif data_readiness in {"verified", "ready"}:
        readiness = "data_ready"
    elif data_readiness in {"failed", "review_required"}:
        readiness = "blocked"
    else:
        readiness = "intake_ready" if intake_supported else "blocked"
    profile_template = build_profile_template(str(scenario.get("profile_template") or ""))
    sources = acquisition.get("source_files")
    source_size = sum(
        int(item.get("size_bytes") or 0)
        for item in sources
        if isinstance(sources, list) and isinstance(item, dict)
    ) if isinstance(sources, list) else 0
    steps = preprocessing.get("steps")
    transforms = [
        ScenarioTransform.model_validate(item)
        for item in steps
        if isinstance(steps, list) and isinstance(item, dict)
    ] if isinstance(steps, list) else []
    return EnterpriseScenario(
        scenario_id=required_text(scenario, "scenario_id"),
        display_name=required_text(scenario, "display_name"),
        department=required_text(scenario, "department"),
        business_outcome=required_text(scenario, "business_outcome"),
        modality=required_text(scenario, "modality"),  # type: ignore[arg-type]
        readiness=readiness,
        data_readiness=data_readiness,
        model_readiness=model_readiness,  # type: ignore[arg-type]
        deployment_readiness=deployment_readiness,  # type: ignore[arg-type]
        intake_supported=intake_supported,
        profile_template=profile_template,
        model_component_id=str(scenario.get("model_component_id") or "") or None,
        dataset=ScenarioDataset(
            dataset_id=required_text(dataset, "dataset_id"),
            dataset_name=required_text(dataset, "dataset_name"),
            dataset_version=required_text(dataset, "dataset_version"),
            source_url=required_text(dataset, "source_url"),
            source_revision=required_text(dataset, "source_revision"),
            license_id=required_text(dataset, "license_id"),
            license_url=required_text(dataset, "license_url"),
            usage_policy=required_text(dataset, "usage_policy"),
            manifest_uri=required_text(dataset, "manifest_uri"),
            split_manifest_uri=required_text(dataset, "split_manifest_uri"),
            source_size_bytes=source_size,
        ),
        recipe_id=required_text(preprocessing, "recipe_id"),
        recipe_version=required_text(preprocessing, "version"),
        transforms=transforms,
        blockers=sorted(set(blockers)),
        intake_state=intake_state,
        config_uri=str(path),
        runtime_config_uri=f"/opt/airflow/evm_project/configs/scenarios/{path.name}",
    )


def build_profile_template(template_id: str) -> dict[str, Any] | None:
    if template_id != "efficientnet-b0-visa":
        return None
    from evm.control_panel.pipeline_profiles import default_profile

    return default_profile().model_dump(mode="json")


def read_intake_state(path: Path) -> ScenarioIntakeState | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return ScenarioIntakeState(
            status=str(payload.get("status") or "unknown"),
            phase=str(payload.get("phase") or "unknown"),
            progress=float(payload.get("progress") or 0.0),
            records_processed=int(payload.get("records_processed") or 0),
            records_output=int(payload.get("records_output") or 0),
            updated_at=str(payload.get("updated_at") or "") or None,
            blockers=[str(item) for item in payload.get("blockers", []) if item],
            warnings=[str(item) for item in payload.get("warnings", []) if item],
            quality_status=str(payload.get("quality_status") or "") or None,
            quality_report_uri=str(payload.get("quality_report_uri") or "") or None,
            source_registry_uri=str(payload.get("source_registry_uri") or "") or None,
        )
    except (OSError, ValueError, TypeError):
        return None


def required_object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ScenarioCatalogError(f"scenario_object_missing:{key}")
    return value


def required_text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ScenarioCatalogError(f"scenario_field_missing:{key}")
    return value
