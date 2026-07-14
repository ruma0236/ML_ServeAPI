from __future__ import annotations

import pytest

from evm.control_panel.model_components import (
    ModelComponentRegistrationRequest,
    component_contract_blockers,
    get_model_component,
    read_model_components,
    register_model_component,
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


def test_custom_component_registration_is_immutable_and_catalogued(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVM_MODEL_COMPONENT_REGISTRY_ROOT", str(tmp_path))
    reference = get_model_component("torchvision-efficientnet-b0", "1.0.0")
    assert reference is not None
    component = reference.model_copy(
        update={
            "component_id": "manufacturing-custom-b0",
            "version": "2026.07.14",
            "display_name": "Manufacturing Custom B0",
        }
    )

    registration = register_model_component(
        ModelComponentRegistrationRequest(
            component=component,
            actor="ml-platform",
            reason="Register a digest-pinned test component for governed execution.",
        )
    )

    assert registration.registry_uri.endswith(".json")
    assert registration.catalog_digest
    assert get_model_component(component.component_id, component.version) == component
    with pytest.raises(ValueError, match="model_component_version_exists"):
        register_model_component(
            ModelComponentRegistrationRequest(
                component=component,
                actor="ml-platform",
                reason="Attempt to overwrite an immutable component version.",
            )
        )


def test_custom_component_registration_rejects_unwired_adapter(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVM_MODEL_COMPONENT_REGISTRY_ROOT", str(tmp_path))
    reference = get_model_component("torchvision-efficientnet-b0", "1.0.0")
    assert reference is not None
    component = reference.model_copy(
        update={
            "component_id": "unwired-container-model",
            "runtime_adapter": "container-v1",
        }
    )

    with pytest.raises(ValueError, match="model_component_runtime_adapter_not_wired"):
        register_model_component(
            ModelComponentRegistrationRequest(
                component=component,
                actor="ml-platform",
                reason="Verify that unproven runtime adapters fail closed.",
            )
        )
