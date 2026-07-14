from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from evm.control_panel.schemas import ContractModel


class ModelComponent(ContractModel):
    component_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,127}$")
    version: str = Field(pattern=r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")
    display_name: str
    status: Literal["approved", "deprecated", "blocked"] = "approved"
    framework: Literal["torch"] = "torch"
    architecture: str
    backbone: str
    runtime_adapter: str
    default_input_size: int = Field(ge=64, le=4096)
    supported_input_sizes: list[int] = Field(min_length=1)
    source_revision: str
    training_image: str
    serving_image: str


class ModelComponentCatalog(ContractModel):
    schema_version: Literal["evm.model_component_catalog.v1"] = (
        "evm.model_component_catalog.v1"
    )
    components: list[ModelComponent] = Field(default_factory=list)
    catalog_digest: str = ""


class ModelComponentRegistrationRequest(ContractModel):
    component: ModelComponent
    actor: str = Field(min_length=2, max_length=128)
    reason: str = Field(min_length=8, max_length=500)


class ModelComponentRegistration(ContractModel):
    schema_version: Literal["evm.model_component_registration.v1"] = (
        "evm.model_component_registration.v1"
    )
    component: ModelComponent
    actor: str
    reason: str
    registered_at: str
    registry_uri: str
    catalog_digest: str


def read_model_components() -> ModelComponentCatalog:
    path = model_component_catalog_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    components = [ModelComponent.model_validate(item) for item in payload.get("components", [])]
    known = {(component.component_id, component.version) for component in components}
    for component in registered_model_components():
        identity = (component.component_id, component.version)
        if identity in known:
            raise ValueError(f"duplicate model component identity: {component.component_id}@{component.version}")
        components.append(component)
        known.add(identity)
    components.sort(key=lambda item: (item.component_id, item.version))
    canonical = {
        "schema_version": "evm.model_component_catalog.v1",
        "components": [component.model_dump(mode="json") for component in components],
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ModelComponentCatalog(components=components, catalog_digest=digest)


def register_model_component(
    request: ModelComponentRegistrationRequest,
) -> ModelComponentRegistration:
    root = model_component_registry_root()
    if root is None:
        raise ValueError("model_component_registry_not_configured")
    blockers = component_contract_blockers(request.component)
    if blockers:
        raise ValueError(",".join(sorted(set(blockers))))
    identity = (request.component.component_id, request.component.version)
    if any(
        (component.component_id, component.version) == identity
        for component in read_model_components().components
    ):
        raise ValueError("model_component_version_exists")

    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{identity[0]}@{identity[1]}".encode("utf-8")).hexdigest()[:20]
    path = root / f"{digest}.json"
    registered_at = datetime.now(UTC).isoformat()
    payload = {
        "schema_version": "evm.model_component_registration.v1",
        "component": request.component.model_dump(mode="json"),
        "actor": request.actor,
        "reason": request.reason,
        "registered_at": registered_at,
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
    catalog = read_model_components()
    return ModelComponentRegistration(
        component=request.component,
        actor=request.actor,
        reason=request.reason,
        registered_at=registered_at,
        registry_uri=str(path),
        catalog_digest=catalog.catalog_digest,
    )


def get_model_component(component_id: str, version: str) -> ModelComponent | None:
    return next(
        (
            component
            for component in read_model_components().components
            if component.component_id == component_id and component.version == version
        ),
        None,
    )


def component_contract_blockers(component: ModelComponent) -> list[str]:
    blockers: list[str] = []
    if component.status != "approved":
        blockers.append("model_component_not_approved")
    if not re.fullmatch(r"[0-9a-f]{40}", component.source_revision):
        blockers.append("model_component_source_revision_not_pinned")
    if not pinned_image(component.training_image):
        blockers.append("model_component_training_image_not_pinned")
    if not pinned_image(component.serving_image):
        blockers.append("model_component_serving_image_not_pinned")
    if component.runtime_adapter != "efficientnet":
        blockers.append("model_component_runtime_adapter_not_wired")
    return blockers


def model_component_catalog_path() -> Path:
    configured = os.getenv("EVM_MODEL_COMPONENT_CATALOG", "configs/model_components.json")
    path = Path(configured)
    if path.is_absolute():
        return path
    project_root = Path(
        os.getenv("EVM_PROJECT_ROOT", str(Path(__file__).resolve().parents[3]))
    )
    return project_root / path


def model_component_registry_root() -> Path | None:
    configured = os.getenv("EVM_MODEL_COMPONENT_REGISTRY_ROOT", "").strip()
    return Path(configured) if configured else None


def registered_model_components() -> list[ModelComponent]:
    root = model_component_registry_root()
    if root is None or not root.exists():
        return []
    components: list[ModelComponent] = []
    for path in sorted(root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "evm.model_component_registration.v1":
            raise ValueError(f"unsupported model component registration: {path}")
        components.append(ModelComponent.model_validate(payload.get("component", {})))
    return components


def pinned_image(value: str) -> bool:
    return bool(re.fullmatch(r".+@sha256:[0-9a-f]{64}", value))
