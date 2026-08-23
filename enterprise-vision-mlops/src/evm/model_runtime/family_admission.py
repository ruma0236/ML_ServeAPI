from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import tomllib
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


AdmissionFamily = Literal["image", "vlm", "llm"]


class FamilyAdmissionError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        status_code: int,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class AdmissionCost:
    request_bytes: int
    image_bytes: int = 0
    image_pixels: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def scheduling_units(self) -> float:
        return max(
            1.0,
            self.request_bytes / 1024.0,
            self.image_bytes / 1024.0,
            self.image_pixels / 1024.0,
            float(self.total_tokens),
        )


@dataclass(frozen=True)
class FamilyAdmissionLimits:
    family: AdmissionFamily
    config_path: Path
    config_sha256: str
    max_active_requests: int
    max_queue_depth: int
    max_short_bypass: int
    starvation_seconds: float
    default_deadline_seconds: float
    maximum_deadline_seconds: float
    retry_after_seconds: int
    max_request_bytes: int
    max_image_bytes: int
    max_image_pixels: int
    max_input_tokens: int
    max_output_tokens: int
    max_in_flight_request_bytes: int
    max_in_flight_image_bytes: int
    max_in_flight_pixels: int
    max_in_flight_tokens: int
    long_request_cost_units: float

    @classmethod
    def from_path(
        cls,
        family: AdmissionFamily,
        path: Path | None = None,
    ) -> "FamilyAdmissionLimits":
        resolved = (path or default_config_path()).resolve()
        with resolved.open("rb") as handle:
            payload = tomllib.load(handle)
        if payload.get("schema_version") != "evm.s7_family_admission.v1":
            raise FamilyAdmissionError("family_admission_config_schema_invalid", status_code=500)
        scheduler = _section(payload, "scheduler")
        family_payload = _section(payload, family)
        limits = cls(
            family=family,
            config_path=resolved,
            config_sha256=file_sha256(resolved),
            max_active_requests=int(scheduler["max_active_requests"]),
            max_queue_depth=int(scheduler["max_queue_depth"]),
            max_short_bypass=int(scheduler["max_short_bypass"]),
            starvation_seconds=float(scheduler["starvation_seconds"]),
            default_deadline_seconds=float(scheduler["default_deadline_seconds"]),
            maximum_deadline_seconds=float(scheduler["maximum_deadline_seconds"]),
            retry_after_seconds=int(scheduler["retry_after_seconds"]),
            max_request_bytes=int(family_payload["max_request_bytes"]),
            max_image_bytes=int(family_payload["max_image_bytes"]),
            max_image_pixels=int(family_payload["max_image_pixels"]),
            max_input_tokens=int(family_payload["max_input_tokens"]),
            max_output_tokens=int(family_payload["max_output_tokens"]),
            max_in_flight_request_bytes=int(
                family_payload["max_in_flight_request_bytes"]
            ),
            max_in_flight_image_bytes=int(family_payload["max_in_flight_image_bytes"]),
            max_in_flight_pixels=int(family_payload["max_in_flight_pixels"]),
            max_in_flight_tokens=int(family_payload["max_in_flight_tokens"]),
            long_request_cost_units=float(family_payload["long_request_cost_units"]),
        )
        limits.validate()
        return limits

    def validate(self) -> None:
        positive = (
            self.max_active_requests,
            self.max_queue_depth,
            self.max_short_bypass,
            self.retry_after_seconds,
            self.max_request_bytes,
            self.max_in_flight_request_bytes,
        )
        if min(positive) <= 0:
            raise FamilyAdmissionError("family_admission_positive_bound_invalid", status_code=500)
        if self.max_active_requests != 1:
            raise FamilyAdmissionError("family_admission_single_gpu_contract_invalid", status_code=500)
        if not 0 < self.default_deadline_seconds <= self.maximum_deadline_seconds:
            raise FamilyAdmissionError("family_admission_deadline_contract_invalid", status_code=500)
        if self.starvation_seconds <= 0 or self.long_request_cost_units <= 0:
            raise FamilyAdmissionError("family_admission_fairness_contract_invalid", status_code=500)
        if self.family == "image" and any(
            (self.max_input_tokens, self.max_output_tokens, self.max_in_flight_tokens)
        ):
            raise FamilyAdmissionError("image_token_budget_must_be_zero", status_code=500)
        if self.family == "llm" and any(
            (self.max_image_bytes, self.max_image_pixels, self.max_in_flight_image_bytes,
             self.max_in_flight_pixels)
        ):
            raise FamilyAdmissionError("llm_image_budget_must_be_zero", status_code=500)
        if self.family in {"image", "vlm"} and min(
            self.max_image_bytes,
            self.max_image_pixels,
            self.max_in_flight_image_bytes,
            self.max_in_flight_pixels,
        ) <= 0:
            raise FamilyAdmissionError("image_family_budget_invalid", status_code=500)
        if self.family in {"vlm", "llm"} and min(
            self.max_input_tokens,
            self.max_output_tokens,
            self.max_in_flight_tokens,
        ) <= 0:
            raise FamilyAdmissionError("generative_token_budget_invalid", status_code=500)

    def public_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "config_sha256": self.config_sha256,
            "max_active_requests": self.max_active_requests,
            "max_queue_depth": self.max_queue_depth,
            "max_short_bypass": self.max_short_bypass,
            "starvation_seconds": self.starvation_seconds,
            "default_deadline_seconds": self.default_deadline_seconds,
            "maximum_deadline_seconds": self.maximum_deadline_seconds,
            "retry_after_seconds": self.retry_after_seconds,
            "max_request_bytes": self.max_request_bytes,
            "max_image_bytes": self.max_image_bytes,
            "max_image_pixels": self.max_image_pixels,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_in_flight_request_bytes": self.max_in_flight_request_bytes,
            "max_in_flight_image_bytes": self.max_in_flight_image_bytes,
            "max_in_flight_pixels": self.max_in_flight_pixels,
            "max_in_flight_tokens": self.max_in_flight_tokens,
            "long_request_cost_units": self.long_request_cost_units,
        }


@dataclass
class _Ticket:
    sequence: int
    cost: AdmissionCost
    enqueued_at: float
    deadline_at: float
    long_request: bool


class AdmissionLease(AbstractContextManager["AdmissionLease"]):
    def __init__(
        self,
        controller: "FamilyAdmissionController",
        ticket: _Ticket,
        queue_wait_seconds: float,
    ) -> None:
        self._controller = controller
        self._ticket = ticket
        self.queue_wait_seconds = queue_wait_seconds
        self._released = False

    @property
    def cost(self) -> AdmissionCost:
        return self._ticket.cost

    @property
    def long_request(self) -> bool:
        return self._ticket.long_request

    def __enter__(self) -> "AdmissionLease":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if not self._released:
            self._controller.release(self)
            self._released = True


class FamilyAdmissionController:
    def __init__(
        self,
        limits: FamilyAdmissionLimits,
        *,
        registry: CollectorRegistry,
    ) -> None:
        self.limits = limits
        self._condition = threading.Condition()
        self._waiting: list[_Ticket] = []
        self._active: dict[int, _Ticket] = {}
        self._sequence = 0
        self._short_dispatch_streak = 0
        self.requests = Counter(
            "evm_family_admission_requests_total",
            "Family-aware model admission outcomes.",
            ["model_family", "outcome", "reason"],
            registry=registry,
        )
        self.queue_depth = Gauge(
            "evm_family_admission_queue_depth",
            "Current bounded family admission queue depth.",
            ["model_family"],
            registry=registry,
        )
        self.active_requests = Gauge(
            "evm_family_admission_active_requests",
            "Current admitted model requests.",
            ["model_family"],
            registry=registry,
        )
        self.reserved_cost = Gauge(
            "evm_family_admission_reserved_cost",
            "Queued plus active cost reserved by dimension.",
            ["model_family", "dimension"],
            registry=registry,
        )
        self.queue_wait = Histogram(
            "evm_family_admission_queue_wait_seconds",
            "Time spent waiting for family-aware admission.",
            ["model_family", "request_class"],
            buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
            registry=registry,
        )
        self.request_cost = Histogram(
            "evm_family_admission_request_cost",
            "Accepted request cost by verified dimension.",
            ["model_family", "dimension"],
            buckets=(1, 8, 32, 128, 512, 1024, 2048, 8192, 32768, 131072, 524288, 2097152),
            registry=registry,
        )
        self.stage_latency = Histogram(
            "evm_family_inference_stage_latency_seconds",
            "Verified family-specific inference stage latency.",
            ["model_family", "stage"],
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
            registry=registry,
        )
        self.generated_tokens: Counter | None = None
        self.generation_rate: Histogram | None = None
        self.ttft: Histogram | None = None
        self.tpot: Histogram | None = None
        if limits.family in {"vlm", "llm"}:
            self.generated_tokens = Counter(
                "evm_family_generated_tokens_total",
                "Generated tokens observed on a verified generative path.",
                ["model_family"],
                registry=registry,
            )
            self.generation_rate = Histogram(
                "evm_family_generation_tokens_per_second",
                "Observed generation token rate for non-streaming local inference.",
                ["model_family"],
                buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 40, 80, 160),
                registry=registry,
            )
            self.ttft = Histogram(
                "evm_family_time_to_first_token_seconds",
                "Time to first generated token when the runtime exposes step timing.",
                ["model_family"],
                buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
                registry=registry,
            )
            self.tpot = Histogram(
                "evm_family_time_per_output_token_seconds",
                "Mean time per output token after the first token when measurable.",
                ["model_family"],
                buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
                registry=registry,
            )
        self.peak_vram = Gauge(
            "evm_family_inference_peak_vram_bytes",
            "Last observed torch peak allocated VRAM for the active family.",
            ["model_family"],
            registry=registry,
        )
        self._publish_state()

    def acquire(
        self,
        cost: AdmissionCost,
        *,
        deadline_seconds: float | None = None,
    ) -> AdmissionLease:
        self._validate_item(cost)
        requested_deadline = (
            self.limits.default_deadline_seconds
            if deadline_seconds is None
            else float(deadline_seconds)
        )
        if not math.isfinite(requested_deadline) or not (
            0 < requested_deadline <= self.limits.maximum_deadline_seconds
        ):
            self._reject("deadline_invalid", status_code=422)
        now = time.monotonic()
        with self._condition:
            self._expire_waiters(now)
            if len(self._waiting) >= self.limits.max_queue_depth:
                self._reject_capacity("queue_depth_exceeded")
            if len(self._waiting) + len(self._active) >= (
                self.limits.max_queue_depth + self.limits.max_active_requests
            ):
                self._reject_capacity("in_flight_request_count_exceeded")
            totals = self._reserved_totals()
            self._assert_capacity(totals, cost)
            self._sequence += 1
            ticket = _Ticket(
                sequence=self._sequence,
                cost=cost,
                enqueued_at=now,
                deadline_at=now + requested_deadline,
                long_request=cost.scheduling_units >= self.limits.long_request_cost_units,
            )
            self._waiting.append(ticket)
            self._publish_state()
            self._condition.notify_all()
            while True:
                current = time.monotonic()
                self._expire_waiters(current)
                if ticket not in self._waiting:
                    self._publish_state()
                    self.requests.labels(
                        self.limits.family, "expired", "deadline_exceeded"
                    ).inc()
                    raise FamilyAdmissionError("admission_deadline_exceeded", status_code=408)
                selected = self._selected_ticket(current)
                if (
                    selected is ticket
                    and len(self._active) < self.limits.max_active_requests
                ):
                    self._waiting.remove(ticket)
                    self._active[ticket.sequence] = ticket
                    if ticket.long_request:
                        self._short_dispatch_streak = 0
                    else:
                        self._short_dispatch_streak += 1
                    wait_seconds = current - ticket.enqueued_at
                    request_class = "long" if ticket.long_request else "short"
                    self.requests.labels(self.limits.family, "accepted", "admitted").inc()
                    self.queue_wait.labels(self.limits.family, request_class).observe(
                        wait_seconds
                    )
                    self._observe_cost(cost)
                    self._publish_state()
                    return AdmissionLease(self, ticket, wait_seconds)
                remaining = ticket.deadline_at - current
                self._condition.wait(timeout=max(0.001, min(0.1, remaining)))

    def release(self, lease: AdmissionLease) -> None:
        with self._condition:
            self._active.pop(lease._ticket.sequence, None)
            self._publish_state()
            self._condition.notify_all()

    def record_runtime_metrics(self, metrics: dict[str, float | int]) -> None:
        family = self.limits.family
        for key, stage in (
            ("decode_seconds", "decode"),
            ("preprocess_seconds", "preprocess"),
            ("inference_seconds", "inference"),
            ("generation_seconds", "generation"),
        ):
            value = metrics.get(key)
            if value is not None:
                self.stage_latency.labels(family, stage).observe(_finite_nonnegative(value, key))
        generated = int(metrics.get("generated_tokens", 0))
        if generated > 0:
            if self.generated_tokens is None or self.generation_rate is None:
                raise FamilyAdmissionError(
                    "unsupported_generation_metric_for_family", status_code=500
                )
            self.generated_tokens.labels(family).inc(generated)
            rate = metrics.get("tokens_per_second")
            if rate is not None:
                self.generation_rate.labels(family).observe(
                    _finite_nonnegative(rate, "tokens_per_second")
                )
            ttft = metrics.get("ttft_seconds")
            if ttft is not None:
                if self.ttft is None:
                    raise FamilyAdmissionError("unsupported_ttft_metric", status_code=500)
                self.ttft.labels(family).observe(_finite_nonnegative(ttft, "ttft_seconds"))
            tpot = metrics.get("tpot_seconds")
            if tpot is not None:
                if self.tpot is None:
                    raise FamilyAdmissionError("unsupported_tpot_metric", status_code=500)
                self.tpot.labels(family).observe(_finite_nonnegative(tpot, "tpot_seconds"))
        peak_vram = metrics.get("peak_vram_bytes")
        if peak_vram is not None:
            self.peak_vram.labels(family).set(_finite_nonnegative(peak_vram, "peak_vram_bytes"))

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            totals = self._reserved_totals()
            return {
                "policy": self.limits.public_dict(),
                "queue_depth": len(self._waiting),
                "active_requests": len(self._active),
                "reserved": totals,
                "scheduler": "bounded_cost_aware_with_bypass_cap",
            }

    def _validate_item(self, cost: AdmissionCost) -> None:
        for value in (
            cost.request_bytes,
            cost.image_bytes,
            cost.image_pixels,
            cost.input_tokens,
            cost.output_tokens,
        ):
            if not isinstance(value, int) or value < 0:
                self._reject("request_cost_invalid", status_code=422)
        if cost.request_bytes > self.limits.max_request_bytes:
            self._reject("request_bytes_exceeded", status_code=413)
        if cost.image_bytes > self.limits.max_image_bytes:
            self._reject("image_bytes_exceeded", status_code=413)
        if cost.image_pixels > self.limits.max_image_pixels:
            self._reject("image_pixels_exceeded", status_code=422)
        if cost.input_tokens > self.limits.max_input_tokens:
            self._reject("input_tokens_exceeded", status_code=422)
        if cost.output_tokens > self.limits.max_output_tokens:
            self._reject("output_tokens_exceeded", status_code=422)

    def _assert_capacity(self, totals: dict[str, int], cost: AdmissionCost) -> None:
        checks = (
            ("request_bytes", self.limits.max_in_flight_request_bytes, cost.request_bytes),
            ("image_bytes", self.limits.max_in_flight_image_bytes, cost.image_bytes),
            ("image_pixels", self.limits.max_in_flight_pixels, cost.image_pixels),
            ("tokens", self.limits.max_in_flight_tokens, cost.total_tokens),
        )
        for dimension, limit, increment in checks:
            if limit == 0 and increment == 0:
                continue
            if limit <= 0 or totals[dimension] + increment > limit:
                self._reject_capacity(f"in_flight_{dimension}_exceeded")

    def _selected_ticket(self, now: float) -> _Ticket | None:
        if not self._waiting:
            return None
        long_waiters = [ticket for ticket in self._waiting if ticket.long_request]
        if long_waiters:
            oldest_long = min(long_waiters, key=lambda ticket: ticket.sequence)
            if (
                self._short_dispatch_streak >= self.limits.max_short_bypass
                or now - oldest_long.enqueued_at >= self.limits.starvation_seconds
            ):
                return oldest_long
        return min(
            self._waiting,
            key=lambda ticket: (ticket.cost.scheduling_units, ticket.sequence),
        )

    def _expire_waiters(self, now: float) -> None:
        expired = [ticket for ticket in self._waiting if ticket.deadline_at <= now]
        if expired:
            self._waiting = [ticket for ticket in self._waiting if ticket.deadline_at > now]
            self._condition.notify_all()

    def _reserved_totals(self) -> dict[str, int]:
        tickets = [*self._waiting, *self._active.values()]
        return {
            "request_bytes": sum(ticket.cost.request_bytes for ticket in tickets),
            "image_bytes": sum(ticket.cost.image_bytes for ticket in tickets),
            "image_pixels": sum(ticket.cost.image_pixels for ticket in tickets),
            "tokens": sum(ticket.cost.total_tokens for ticket in tickets),
        }

    def _publish_state(self) -> None:
        family = self.limits.family
        self.queue_depth.labels(family).set(len(self._waiting))
        self.active_requests.labels(family).set(len(self._active))
        for dimension, value in self._reserved_totals().items():
            if value or _dimension_supported(self.limits, dimension):
                self.reserved_cost.labels(family, dimension).set(value)

    def _observe_cost(self, cost: AdmissionCost) -> None:
        family = self.limits.family
        values = {
            "request_bytes": cost.request_bytes,
            "image_bytes": cost.image_bytes,
            "image_pixels": cost.image_pixels,
            "input_tokens": cost.input_tokens,
            "output_tokens": cost.output_tokens,
        }
        for dimension, value in values.items():
            if value > 0:
                self.request_cost.labels(family, dimension).observe(value)

    def _reject_capacity(self, reason: str) -> None:
        self._reject(
            reason,
            status_code=429,
            retry_after_seconds=self.limits.retry_after_seconds,
        )

    def _reject(
        self,
        reason: str,
        *,
        status_code: int,
        retry_after_seconds: int | None = None,
    ) -> None:
        outcome = "rejected" if status_code != 408 else "expired"
        self.requests.labels(self.limits.family, outcome, reason).inc()
        raise FamilyAdmissionError(
            reason,
            status_code=status_code,
            retry_after_seconds=retry_after_seconds,
        )


def request_json_bytes(payload: Any) -> int:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    return len(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    )


def default_config_path() -> Path:
    configured = os.getenv("EVM_S7_ADMISSION_CONFIG", "").strip()
    if configured:
        return Path(configured)
    repository_path = Path(__file__).resolve().parents[3] / "configs" / "s7_family_admission.toml"
    if repository_path.is_file():
        return repository_path
    return Path("/app/configs/s7_family_admission.toml")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _section(payload: dict[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise FamilyAdmissionError(
            f"family_admission_config_section_missing:{name}", status_code=500
        )
    return value


def _dimension_supported(limits: FamilyAdmissionLimits, dimension: str) -> bool:
    return {
        "request_bytes": limits.max_in_flight_request_bytes,
        "image_bytes": limits.max_in_flight_image_bytes,
        "image_pixels": limits.max_in_flight_pixels,
        "tokens": limits.max_in_flight_tokens,
    }[dimension] > 0


def _finite_nonnegative(value: float | int, label: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise FamilyAdmissionError(f"runtime_metric_invalid:{label}", status_code=500)
    return result
