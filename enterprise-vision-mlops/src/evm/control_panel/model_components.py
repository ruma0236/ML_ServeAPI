from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import Field

from evm.control_panel.schemas import ContractModel


class ModelComponent(ContractModel):
    component_id: str
    version: str
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


def read_model_components() -> ModelComponentCatalog:
    path = model_component_catalog_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload["catalog_digest"] = digest
    return ModelComponentCatalog.model_validate(payload)


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


def pinned_image(value: str) -> bool:
    return bool(re.fullmatch(r".+@sha256:[0-9a-f]{64}", value))
