from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from evm.model_runtime.family_admission import (
    AdmissionCost,
    FamilyAdmissionController,
    FamilyAdmissionError,
    FamilyAdmissionLimits,
    request_json_bytes,
)


CONFIG_PATH = Path("configs/s7_family_admission.toml")


def controller(family: str, **changes: object) -> FamilyAdmissionController:
    limits = FamilyAdmissionLimits.from_path(family, CONFIG_PATH)  # type: ignore[arg-type]
    return FamilyAdmissionController(
        replace(limits, **changes),
        registry=CollectorRegistry(),
    )


def test_family_limits_are_distinct_and_frozen() -> None:
    image = FamilyAdmissionLimits.from_path("image", CONFIG_PATH)
    vlm = FamilyAdmissionLimits.from_path("vlm", CONFIG_PATH)
    llm = FamilyAdmissionLimits.from_path("llm", CONFIG_PATH)

    assert image.max_in_flight_tokens == 0
    assert vlm.max_image_pixels > 0
    assert vlm.max_output_tokens == 32
    assert llm.max_image_pixels == 0
    assert llm.max_output_tokens == 128
    assert len({image.config_sha256, vlm.config_sha256, llm.config_sha256}) == 1


@pytest.mark.parametrize(
    ("cost", "status_code", "code"),
    [
        (AdmissionCost(request_bytes=20_000, image_bytes=1, image_pixels=1), 413, "request_bytes_exceeded"),
        (AdmissionCost(request_bytes=1, image_bytes=1, image_pixels=3_000_000), 422, "image_pixels_exceeded"),
    ],
)
def test_item_limits_distinguish_payload_and_semantic_cost(
    cost: AdmissionCost,
    status_code: int,
    code: str,
) -> None:
    admission = controller("image")

    with pytest.raises(FamilyAdmissionError) as captured:
        admission.acquire(cost)

    assert captured.value.status_code == status_code
    assert captured.value.code == code


def test_aggregate_pressure_returns_bounded_retry_after() -> None:
    admission = controller(
        "image",
        max_in_flight_request_bytes=100,
        max_in_flight_image_bytes=100,
        max_in_flight_pixels=100,
    )
    active = admission.acquire(
        AdmissionCost(request_bytes=80, image_bytes=80, image_pixels=80)
    )
    try:
        with pytest.raises(FamilyAdmissionError) as captured:
            admission.acquire(
                AdmissionCost(request_bytes=30, image_bytes=30, image_pixels=30)
            )
    finally:
        active.__exit__(None, None, None)

    assert captured.value.status_code == 429
    assert captured.value.retry_after_seconds == 2


def test_cost_aware_scheduler_caps_short_request_bypass() -> None:
    admission = controller(
        "image",
        max_short_bypass=2,
        long_request_cost_units=10,
        max_in_flight_request_bytes=10_000,
        max_in_flight_image_bytes=10_000,
        max_in_flight_pixels=10_000,
    )
    blocker = admission.acquire(
        AdmissionCost(request_bytes=20, image_bytes=20, image_pixels=20)
    )
    order: list[str] = []
    errors: list[Exception] = []

    def run(name: str, cost: int) -> None:
        try:
            with admission.acquire(
                AdmissionCost(request_bytes=cost, image_bytes=cost, image_pixels=cost)
            ):
                order.append(name)
                time.sleep(0.005)
        except Exception as exc:  # pragma: no cover - assertion reports the error
            errors.append(exc)

    threads = [threading.Thread(target=run, args=("long", 20), daemon=True)]
    threads[0].start()
    _wait_for_depth(admission, 1)
    for index in range(3):
        thread = threading.Thread(target=run, args=(f"short-{index}", 1), daemon=True)
        threads.append(thread)
        thread.start()
        _wait_for_depth(admission, index + 2)
    blocker.__exit__(None, None, None)
    for thread in threads:
        thread.join(timeout=2)

    assert not errors
    assert order.index("long") <= 2
    assert len(order) == 4
    assert admission.snapshot()["queue_depth"] == 0
    assert admission.snapshot()["active_requests"] == 0


def test_queued_request_expires_without_execution() -> None:
    admission = controller("llm")
    active = admission.acquire(AdmissionCost(request_bytes=10, input_tokens=10, output_tokens=1))
    observed: list[FamilyAdmissionError] = []

    def wait_for_expiry() -> None:
        try:
            admission.acquire(
                AdmissionCost(request_bytes=10, input_tokens=10, output_tokens=1),
                deadline_seconds=0.03,
            )
        except FamilyAdmissionError as exc:
            observed.append(exc)

    thread = threading.Thread(target=wait_for_expiry, daemon=True)
    thread.start()
    thread.join(timeout=1)
    active.__exit__(None, None, None)

    assert len(observed) == 1
    assert observed[0].status_code == 408
    assert admission.snapshot()["queue_depth"] == 0


def test_image_metrics_do_not_publish_unobserved_token_series() -> None:
    registry = CollectorRegistry()
    admission = FamilyAdmissionController(
        FamilyAdmissionLimits.from_path("image", CONFIG_PATH),
        registry=registry,
    )
    with admission.acquire(AdmissionCost(request_bytes=10, image_bytes=20, image_pixels=30)):
        admission.record_runtime_metrics(
            {
                "decode_seconds": 0.01,
                "preprocess_seconds": 0.02,
                "inference_seconds": 0.03,
                "peak_vram_bytes": 1024,
            }
        )
    metrics = generate_latest(registry).decode("utf-8")

    assert 'dimension="image_pixels"' in metrics
    assert "evm_family_generated_tokens_total" not in metrics
    assert "evm_family_time_to_first_token_seconds" not in metrics


def test_request_json_bytes_uses_canonical_utf8() -> None:
    assert request_json_bytes({"text": "가", "value": 1}) == len(
        '{"text":"\\uac00","value":1}'.encode("utf-8")
    )


def _wait_for_depth(admission: FamilyAdmissionController, depth: int) -> None:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if admission.snapshot()["queue_depth"] == depth:
            return
        time.sleep(0.005)
    raise AssertionError(f"queue depth did not reach {depth}")
