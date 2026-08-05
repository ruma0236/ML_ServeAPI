from __future__ import annotations

from evm.model_runtime.serving import ScenarioInferenceRequest


def test_scenario_inference_request_separates_vlm_and_llm_inputs() -> None:
    vlm = ScenarioInferenceRequest(
        model_family="vlm",
        image_uri="file:///F:/image.png",
        image_sha256="a" * 64,
        question="What is shown?",
        choices=["one", "two"],
    )
    llm = ScenarioInferenceRequest(
        model_family="llm",
        instruction="Give a concise answer.",
        context="Bounded local context.",
    )
    assert vlm.choices == ["one", "two"]
    assert llm.max_new_tokens == 32
