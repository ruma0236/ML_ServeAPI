from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ETLTransformSpec:
    transform_id: str
    stage: str
    action: str
    enabled: bool = True
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ETLRecipe:
    recipe_id: str
    version: str
    dataset_types: tuple[str, ...]
    transforms: tuple[ETLTransformSpec, ...]


def _tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def load_etl_recipe(path: Path) -> ETLRecipe:
    with path.open("rb") as fp:
        payload = tomllib.load(fp)
    recipe = payload.get("recipe", {})
    transforms: list[ETLTransformSpec] = []
    for item in payload.get("transforms", []):
        transforms.append(
            ETLTransformSpec(
                transform_id=str(item.get("id", "")),
                stage=str(item.get("stage", "")),
                action=str(item.get("action", "")),
                enabled=bool(item.get("enabled", True)),
                inputs=_tuple(item.get("inputs")),
                outputs=_tuple(item.get("outputs")),
                parameters=dict(item.get("parameters", {})),
            )
        )
    return ETLRecipe(
        recipe_id=str(recipe.get("id", path.stem)),
        version=str(recipe.get("version", "unversioned")),
        dataset_types=_tuple(recipe.get("dataset_types")),
        transforms=tuple(transforms),
    )


def summarize_etl_recipe(recipe: ETLRecipe) -> dict[str, Any]:
    enabled = [item for item in recipe.transforms if item.enabled]
    return {
        "recipe_id": recipe.recipe_id,
        "version": recipe.version,
        "dataset_types": list(recipe.dataset_types),
        "transform_count": len(recipe.transforms),
        "enabled_transform_count": len(enabled),
        "transforms": [
            {
                "id": item.transform_id,
                "stage": item.stage,
                "action": item.action,
                "enabled": item.enabled,
                "inputs": list(item.inputs),
                "outputs": list(item.outputs),
                "parameters": item.parameters,
            }
            for item in recipe.transforms
        ],
    }
