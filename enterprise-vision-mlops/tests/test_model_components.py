from __future__ import annotations

from evm.control_panel.model_components import (
    component_contract_blockers,
    get_model_component,
    read_model_components,
)
from evm.control_panel.pipeline_profiles import default_profile, validate_profile


def test_reference_model_components_are_pinned_and_approved() -> None:
    catalog = read_model_components()

    assert catalog.catalog_digest
    assert {component.architecture for component in catalog.components} == {
        "efficientnet-b0",
        "efficientnet-b7",
    }
    assert all(component_contract_blockers(component) == [] for component in catalog.components)


def test_profile_fails_closed_for_unregistered_model_component() -> None:
    profile = default_profile()
    model = profile.model.model_copy(
        update={
            "component_id": "unregistered-model",
            "component_version": "1.0.0",
        }
    )

    validation = validate_profile(profile.model_copy(update={"model": model}))

    assert validation.executable is False
    assert "model_component_not_registered" in validation.blockers


def test_component_identity_must_match_runtime_architecture() -> None:
    profile = default_profile()
    model = profile.model.model_copy(update={"architecture": "efficientnet-b7"})

    validation = validate_profile(profile.model_copy(update={"model": model}))

    assert validation.executable is False
    assert "model_component_architecture_mismatch" in validation.blockers
    assert get_model_component("torchvision-efficientnet-b0", "1.0.0") is not None
