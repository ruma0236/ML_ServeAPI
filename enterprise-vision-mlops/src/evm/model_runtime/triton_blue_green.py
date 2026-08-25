from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Mapping

import httpx
from prometheus_client import Counter, Gauge, Histogram
from pydantic import ConfigDict, Field, field_validator, model_validator

from evm.control_panel.scenario_workloads import assert_scale_validation_gpu_lease_owner
from evm.control_panel.schemas import ContractModel
from evm.observability.otel import trace_span


ModelRole = Literal["blue", "green"]
ControlAction = Literal[
    "green_loaded",
    "canary_started",
    "green_switched",
    "blue_drain_started",
    "blue_unloaded",
    "blue_loaded",
    "blue_switched",
    "green_drain_started",
    "green_unloaded",
    "green_aborted",
    "closed",
]

_TRACEPARENT = re.compile(r"^00-([a-f0-9]{32})-([a-f0-9]{16})-0[01]$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _strict_causal_required() -> bool:
    return os.getenv("EVM_S6BM_REQUIRE_CAUSAL_FENCE", "0").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def causal_start_observation(actor_identity: str) -> dict[str, Any]:
    monotonic_before_ns = time.perf_counter_ns()
    unix_ns = time.time_ns()
    monotonic_after_ns = time.perf_counter_ns()
    return {
        "schema_version": "evm.s6bm.actor_start_observation.v1",
        "actor_identity": actor_identity,
        "actor_start_unix_ns": unix_ns,
        "monotonic_before_ns": monotonic_before_ns,
        "monotonic_after_ns": monotonic_after_ns,
        "process_id": os.getpid(),
        "thread_id": threading.get_ident(),
        "host_identity": socket.gethostname(),
        "source_revision": os.getenv("EVM_IMAGE_SOURCE_REVISION", "unknown"),
        "service_instance_id": os.getenv("OTEL_SERVICE_INSTANCE_ID", "unknown"),
    }


REQUESTS = Counter(
    "evm_s6bm_requests_total",
    "S6B-M routed request outcomes.",
    ("model_role", "model_name", "model_version", "outcome"),
)
TERMINAL_EFFECTS = Counter(
    "evm_s6bm_terminal_effects_total",
    "S6B-M unique terminal inference effects.",
    ("model_role", "model_name", "model_version", "outcome"),
)
IN_FLIGHT = Gauge(
    "evm_s6bm_in_flight",
    "S6B-M in-flight requests by governed model role.",
    ("model_role", "model_name", "model_version"),
)
LATENCY = Histogram(
    "evm_s6bm_request_latency_seconds",
    "S6B-M end-to-end routed request latency.",
    ("model_role", "model_name", "model_version"),
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)
TRANSITIONS = Counter(
    "evm_s6bm_transitions_total",
    "S6B-M model lifecycle transitions.",
    ("action", "outcome"),
)


class TritonBlueGreenError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class TritonModelIdentity(ContractModel):
    model_config = ConfigDict(extra="forbid")

    role: ModelRole
    model_name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    model_version: str = Field(min_length=1, max_length=16, pattern=r"^[0-9]+$")
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_output: list[float] = Field(min_length=4, max_length=4)


class TritonBlueGreenCausalIdentity(ContractModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$")
    run_id: str = Field(pattern=r"^s8-v4-s6bm-[a-z0-9-]+$")
    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$")
    request_nonce: str = Field(min_length=16, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$")
    trace_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    effect_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_role: Literal["blue"] = "blue"
    model_name: str = Field(pattern=r"^[a-z0-9_-]+$")
    model_version: str = Field(pattern=r"^[0-9]+$")
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    route_generation: int = Field(ge=1)


class TritonBlueGreenInitializeRequest(ContractModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evm.s8_v4.s6bm_initialize_request.v1"] = (
        "evm.s8_v4.s6bm_initialize_request.v1"
    )
    run_id: str = Field(pattern=r"^s8-v4-s6bm-[a-z0-9-]+$")
    source_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    triton_http_url: str = Field(pattern=r"^http://127\.0\.0\.1:[0-9]{2,5}$")
    image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    gpu_uuid: str = Field(min_length=8, max_length=128)
    lease_id: str = Field(min_length=1, max_length=128)
    fencing_token: str = Field(min_length=1, max_length=128)
    blue: TritonModelIdentity
    green: TritonModelIdentity
    max_request_cache: int = Field(default=5000, ge=1000, le=20000)

    @field_validator("green")
    @classmethod
    def validate_roles(cls, value: TritonModelIdentity) -> TritonModelIdentity:
        if value.role != "green":
            raise ValueError("green identity role must be green")
        return value


class TritonBlueGreenControlRequest(ContractModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evm.s8_v4.s6bm_control_request.v1"] = (
        "evm.s8_v4.s6bm_control_request.v1"
    )
    run_id: str = Field(pattern=r"^s8-v4-s6bm-[a-z0-9-]+$")
    action: ControlAction
    expected_generation: int = Field(ge=1)
    lease_id: str = Field(min_length=1, max_length=128)
    fencing_token: str = Field(min_length=1, max_length=128)
    blue_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    green_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_id: str = Field(min_length=8, max_length=128)
    action_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    preflight_vram_passed: bool = True
    readiness_passed: bool = True
    canary_passed: bool = True
    causal_crossover: TritonBlueGreenCausalIdentity | None = None

    @model_validator(mode="after")
    def validate_causal_action(self) -> "TritonBlueGreenControlRequest":
        if self.action in {"green_switched", "blue_unloaded"}:
            if self.causal_crossover is None and _strict_causal_required():
                raise ValueError("causal crossover identity is required")
        elif self.causal_crossover is not None:
            raise ValueError("causal crossover identity is only valid for switch and unload")
        return self


class TritonBlueGreenPredictRequest(ContractModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evm.s8_v4.s6bm_predict_request.v1"] = (
        "evm.s8_v4.s6bm_predict_request.v1"
    )
    run_id: str = Field(pattern=r"^s8-v4-s6bm-[a-z0-9-]+$")
    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$")
    attempt_id: str = Field(
        default="legacy-unbound",
        min_length=8,
        max_length=128,
        pattern=r"^[a-zA-Z0-9._:-]+$",
    )
    request_nonce: str = Field(
        default="legacy-unbound",
        min_length=8,
        max_length=128,
        pattern=r"^[a-zA-Z0-9._:-]+$",
    )
    traceparent: str
    input_values: list[float] = Field(min_length=4, max_length=4)
    hold_ms: int = Field(default=0, ge=0, le=2500)
    expected_model_role: ModelRole | None = None
    expected_model_name: str | None = Field(default=None, pattern=r"^[a-z0-9_-]+$")
    expected_model_version: str | None = Field(default=None, pattern=r"^[0-9]+$")
    expected_artifact_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    expected_route_generation: int = Field(default=0, ge=0)
    causal_crossover: bool = False

    @field_validator("traceparent")
    @classmethod
    def validate_traceparent(cls, value: str) -> str:
        if _TRACEPARENT.fullmatch(value) is None:
            raise ValueError("invalid W3C traceparent")
        return value

    @model_validator(mode="after")
    def validate_expected_identity(self) -> "TritonBlueGreenPredictRequest":
        values = (
            self.expected_model_role,
            self.expected_model_name,
            self.expected_model_version,
            self.expected_artifact_sha256,
        )
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError("expected model identity must be complete")
        if self.causal_crossover and (
            self.request_nonce == "legacy-unbound"
            or self.expected_route_generation < 1
            or self.expected_model_role != "blue"
            or self.hold_ms <= 0
        ):
            raise ValueError(
                "crossover request requires Blue identity, nonce, generation, and hold"
            )
        return self


class TritonBlueGreenResetRequest(ContractModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evm.s8_v4.s6bm_reset_request.v1"] = "evm.s8_v4.s6bm_reset_request.v1"
    run_id: str = Field(pattern=r"^s8-v4-s6bm-[a-z0-9-]+$")
    lease_id: str = Field(min_length=1, max_length=128)
    fencing_token: str = Field(min_length=1, max_length=128)


class TritonBlueGreenPredictResponse(ContractModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evm.s8_v4.s6bm_predict_response.v1"] = (
        "evm.s8_v4.s6bm_predict_response.v1"
    )
    run_id: str
    attempt_id: str
    request_id: str
    trace_id: str
    effect_id: str
    route_generation: int
    model_role: ModelRole
    model_name: str
    model_version: str
    artifact_sha256: str
    route_phase: str
    output: list[float]
    result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    elapsed_ms: float = Field(ge=0)
    replayed: bool = False
    durable_effect: "TritonBlueGreenDurableEffectReceipt | None" = None


class TritonBlueGreenDurableEffectReceipt(ContractModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[
        "evm.s6bm.durable_effect_receipt.v1",
        "evm.s6bm.durable_effect_receipt.v2",
        "evm.s6bm.durable_effect_receipt.v3",
        "evm.s6bm.durable_effect_receipt.v4",
    ] = "evm.s6bm.durable_effect_receipt.v1"
    entity_kind: Literal["s6bm_terminal_effect"]
    entity_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    stored_payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    database_recorded_at: str
    entity_created_at: str
    idempotency_created_at: str
    readback_at: str
    transaction_id: str = Field(min_length=1)
    synchronous_commit: Literal["on"]
    commit_ack_monotonic_ns: int = Field(gt=0)
    readback_started_monotonic_ns: int = Field(gt=0)
    readback_finished_monotonic_ns: int = Field(gt=0)
    readback_visible: Literal[True]
    replayed: bool
    causal_sequence: int | None = Field(default=None, ge=1)
    causal_payload_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    observed_transition: dict[str, object] | None = None
    transition_readback_visible: bool | None = None
    write_backend_pid: int | None = Field(default=None, gt=0)
    commit_timestamp: str | None = None
    commit_timestamp_observed_at: str | None = None
    commit_timestamp_backend_pid: int | None = Field(default=None, gt=0)
    commit_timestamp_tracking: Literal["on"] | None = None
    commit_timestamp_visible: bool | None = None
    separate_connection_readback: bool | None = None
    commit_timestamp_readback_lane: str | None = None
    commit_timestamp_readback_concurrency_limit: int | None = Field(default=None, ge=2)
    commit_timestamp_readback_wait_started_monotonic_ns: int | None = Field(default=None, gt=0)
    commit_timestamp_readback_acquired_monotonic_ns: int | None = Field(default=None, gt=0)
    commit_timestamp_readback_wait_seconds: float | None = Field(default=None, ge=0)
    commit_timestamp_readback_in_flight_at_acquire: int | None = Field(default=None, ge=1)
    commit_timestamp_readback_max_in_flight_observed: int | None = Field(default=None, ge=1)
    commit_timestamp_started_monotonic_ns: int | None = Field(default=None, gt=0)
    commit_timestamp_finished_monotonic_ns: int | None = Field(default=None, gt=0)
    database_clock_anchor: dict[str, object] | None = None
    database_clock_anchor_candidates: list[dict[str, object]] | None = None
    database_clock_anchor_selection: dict[str, object] | None = None


TerminalEffectCommitter = Callable[
    [TritonBlueGreenPredictRequest, TritonBlueGreenPredictResponse],
    Awaitable[Mapping[str, Any]],
]
StartReceiptCommitter = Callable[
    [str, TritonBlueGreenPredictRequest, Mapping[str, Any]],
    Awaitable[Mapping[str, Any]],
]
TransitionFenceCommitter = Callable[
    [TritonBlueGreenControlRequest, Mapping[str, Any]],
    Mapping[str, Any],
]

TritonBlueGreenPredictResponse.model_rebuild()


class TritonBlueGreenStateResponse(ContractModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evm.s8_v4.s6bm_state.v1"] = "evm.s8_v4.s6bm_state.v1"
    initialized: bool
    run_id: str | None = None
    generation: int = 0
    phase: str = "uninitialized"
    route_weights: dict[str, int] = Field(default_factory=dict)
    loaded_roles: list[ModelRole] = Field(default_factory=list)
    in_flight: dict[str, int] = Field(default_factory=dict)
    accepted_unique: int = 0
    terminal_unique: int = 0
    duplicate_replays: int = 0
    used_approvals: int = 0
    transition_receipt: dict[str, Any] | None = None


@dataclass
class _State:
    request: TritonBlueGreenInitializeRequest
    generation: int = 1
    phase: str = "blue_only"
    weights: dict[ModelRole, int] = field(default_factory=lambda: {"blue": 100, "green": 0})
    loaded: set[ModelRole] = field(default_factory=lambda: {"blue"})
    in_flight: dict[ModelRole, int] = field(default_factory=lambda: {"blue": 0, "green": 0})
    responses: dict[str, tuple[str, TritonBlueGreenPredictResponse]] = field(default_factory=dict)
    crossover_switch_events: dict[str, threading.Event] = field(default_factory=dict)
    used_approvals: set[str] = field(default_factory=set)
    duplicate_replays: int = 0
    last_transition_receipt: dict[str, Any] | None = None


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def action_digest(request: TritonBlueGreenControlRequest) -> str:
    payload = {
        "run_id": request.run_id,
        "action": request.action,
        "expected_generation": request.expected_generation,
        "lease_id": request.lease_id,
        "fencing_token": request.fencing_token,
        "blue_artifact_sha256": request.blue_artifact_sha256,
        "green_artifact_sha256": request.green_artifact_sha256,
        "approval_id": request.approval_id,
        "preflight_vram_passed": request.preflight_vram_passed,
        "readiness_passed": request.readiness_passed,
        "canary_passed": request.canary_passed,
        "causal_crossover": (
            request.causal_crossover.model_dump(mode="json")
            if request.causal_crossover is not None
            else None
        ),
    }
    return hashlib.sha256(canonical(payload).encode("ascii")).hexdigest()


def _request_digest(request: TritonBlueGreenPredictRequest) -> str:
    return hashlib.sha256(canonical(request.model_dump(mode="json")).encode("ascii")).hexdigest()


def _effect_id(request: TritonBlueGreenPredictRequest, identity: TritonModelIdentity) -> str:
    return hashlib.sha256(
        canonical(
            {
                "run_id": request.run_id,
                "attempt_id": request.attempt_id,
                "request_id": request.request_id,
                "model_name": identity.model_name,
                "model_version": identity.model_version,
                "artifact_sha256": identity.artifact_sha256,
            }
        ).encode("ascii")
    ).hexdigest()


def causal_identity_for_request(
    request: TritonBlueGreenPredictRequest,
    identity: TritonModelIdentity,
    *,
    route_generation: int,
) -> TritonBlueGreenCausalIdentity:
    return TritonBlueGreenCausalIdentity(
        attempt_id=request.attempt_id,
        run_id=request.run_id,
        request_id=request.request_id,
        request_nonce=request.request_nonce,
        trace_id=_trace_id(request.traceparent),
        effect_id=_effect_id(request, identity),
        model_name=identity.model_name,
        model_version=identity.model_version,
        artifact_sha256=identity.artifact_sha256,
        route_generation=route_generation,
    )


def expected_causal_identity_for_request(
    request: TritonBlueGreenPredictRequest,
) -> TritonBlueGreenCausalIdentity:
    if (
        request.expected_model_role != "blue"
        or request.expected_model_name is None
        or request.expected_model_version is None
        or request.expected_artifact_sha256 is None
        or request.expected_route_generation < 1
    ):
        raise TritonBlueGreenError(
            "causal_expected_identity_incomplete", request.request_id, status_code=422
        )
    effect_id = hashlib.sha256(
        canonical(
            {
                "run_id": request.run_id,
                "attempt_id": request.attempt_id,
                "request_id": request.request_id,
                "model_name": request.expected_model_name,
                "model_version": request.expected_model_version,
                "artifact_sha256": request.expected_artifact_sha256,
            }
        ).encode("ascii")
    ).hexdigest()
    return TritonBlueGreenCausalIdentity(
        attempt_id=request.attempt_id,
        run_id=request.run_id,
        request_id=request.request_id,
        request_nonce=request.request_nonce,
        trace_id=_trace_id(request.traceparent),
        effect_id=effect_id,
        model_name=request.expected_model_name,
        model_version=request.expected_model_version,
        artifact_sha256=request.expected_artifact_sha256,
        route_generation=request.expected_route_generation,
    )


def _result_digest(
    request: TritonBlueGreenPredictRequest,
    identity: TritonModelIdentity,
    *,
    role: ModelRole,
    generation: int,
    phase: str,
    output: list[float],
) -> str:
    return hashlib.sha256(
        canonical(
            {
                "run_id": request.run_id,
                "attempt_id": request.attempt_id,
                "request_id": request.request_id,
                "trace_id": _trace_id(request.traceparent),
                "model_role": role,
                "model_name": identity.model_name,
                "model_version": identity.model_version,
                "artifact_sha256": identity.artifact_sha256,
                "route_generation": generation,
                "route_phase": phase,
                "output": output,
            }
        ).encode("ascii")
    ).hexdigest()


def _role_for_request(request_id: str, weights: dict[ModelRole, int]) -> ModelRole:
    bucket = int(hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "green" if bucket < weights["green"] else "blue"


def _trace_id(traceparent: str) -> str:
    match = _TRACEPARENT.fullmatch(traceparent)
    if match is None:
        raise TritonBlueGreenError("trace_identity_invalid", traceparent, status_code=422)
    return match.group(1)


class TritonBlueGreenManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state: _State | None = None

    def initialize(self, request: TritonBlueGreenInitializeRequest) -> TritonBlueGreenStateResponse:
        if os.getenv("EVM_S6BM_ENABLED", "0").strip().lower() not in {"1", "true", "yes"}:
            raise TritonBlueGreenError("s6bm_disabled", "S6B-M route is disabled", status_code=503)
        if request.blue.role != "blue" or request.green.role != "green":
            raise TritonBlueGreenError("model_role_invalid", request.run_id, status_code=422)
        if request.blue.artifact_sha256 == request.green.artifact_sha256:
            raise TritonBlueGreenError(
                "model_identity_not_distinct", request.run_id, status_code=422
            )
        self._assert_lease(request.run_id, request.lease_id, request.fencing_token)
        with self._lock:
            if self._state is not None:
                if self._state.request.model_dump(mode="json") == request.model_dump(mode="json"):
                    return self.snapshot()
                raise TritonBlueGreenError("controller_already_initialized", request.run_id)
            self._state = _State(request=request)
            for identity in (request.blue, request.green):
                for outcome in ("completed", "failed"):
                    REQUESTS.labels(
                        identity.role,
                        identity.model_name,
                        identity.model_version,
                        outcome,
                    )
                TERMINAL_EFFECTS.labels(
                    identity.role,
                    identity.model_name,
                    identity.model_version,
                    "committed",
                )
                IN_FLIGHT.labels(identity.role, identity.model_name, identity.model_version).set(0)
                LATENCY.labels(identity.role, identity.model_name, identity.model_version)
            return self.snapshot()

    def control(
        self,
        request: TritonBlueGreenControlRequest,
        *,
        transition_fence_committer: TransitionFenceCommitter | None = None,
    ) -> TritonBlueGreenStateResponse:
        self._assert_lease(request.run_id, request.lease_id, request.fencing_token)
        with self._lock:
            state = self._require_state(request.run_id)
            if request.expected_generation != state.generation:
                TRANSITIONS.labels(request.action, "rejected_generation").inc()
                raise TritonBlueGreenError("route_generation_conflict", request.run_id)
            if request.blue_artifact_sha256 != state.request.blue.artifact_sha256:
                TRANSITIONS.labels(request.action, "rejected_identity").inc()
                raise TritonBlueGreenError("blue_digest_mismatch", request.run_id)
            if request.green_artifact_sha256 != state.request.green.artifact_sha256:
                TRANSITIONS.labels(request.action, "rejected_identity").inc()
                raise TritonBlueGreenError("green_digest_mismatch", request.run_id)
            if request.action_digest != action_digest(request):
                TRANSITIONS.labels(request.action, "rejected_approval").inc()
                raise TritonBlueGreenError("action_digest_mismatch", request.run_id)
            if request.approval_id in state.used_approvals:
                TRANSITIONS.labels(request.action, "rejected_approval").inc()
                raise TritonBlueGreenError("approval_reused", request.run_id)
            self._validate_guard_signals(request)
            self._validate_transition(state, request.action)
            fence_context: dict[str, Any] = {}
            fence_receipt: dict[str, Any] | None = None
            crossover: TritonBlueGreenCausalIdentity | None = None
            if request.action in {"green_switched", "blue_unloaded"} and (
                request.causal_crossover is not None or _strict_causal_required()
            ):
                crossover = self._validate_causal_crossover(state, request)
                if request.action == "blue_unloaded":
                    fence_context["pre_switch_blue_effects"] = sorted(
                        (
                            {
                                "request_id": response.request_id,
                                "effect_id": response.effect_id,
                            }
                            for _, response in state.responses.values()
                            if response.model_role == "blue"
                            and response.route_generation <= crossover.route_generation
                        ),
                        key=lambda item: item["request_id"],
                    )
                if transition_fence_committer is None:
                    if _strict_causal_required():
                        raise TritonBlueGreenError(
                            "causal_transition_store_unavailable",
                            request.action,
                            status_code=503,
                        )
                else:
                    try:
                        fence_receipt = dict(transition_fence_committer(request, fence_context))
                    except TritonBlueGreenError:
                        raise
                    except Exception as exc:
                        raise TritonBlueGreenError(
                            "causal_transition_fence_failed",
                            request.action,
                            status_code=503,
                        ) from exc
                    if fence_receipt.get("readback_visible") is not True:
                        raise TritonBlueGreenError(
                            "causal_transition_receipt_mismatch",
                            request.action,
                            status_code=503,
                        )
                    if request.action == "green_switched" and (
                        fence_receipt.get("schema_version")
                        != "evm.s6bm.route_switch_receipt.v2"
                        or fence_receipt.get("attempt_id") != crossover.attempt_id
                        or fence_receipt.get("run_id") != crossover.run_id
                        or fence_receipt.get("request_id") != crossover.request_id
                        or int(fence_receipt.get("old_route_generation", 0))
                        != request.expected_generation
                        or int(fence_receipt.get("new_route_generation", 0))
                        != request.expected_generation + 1
                        or int(fence_receipt.get("actor_process_id", 0)) != os.getpid()
                        or int(fence_receipt.get("actor_thread_id", 0))
                        != threading.get_ident()
                    ):
                        raise TritonBlueGreenError(
                            "causal_transition_receipt_mismatch",
                            request.action,
                            status_code=503,
                        )
            state.last_transition_receipt = None
            self._apply_model_control(state, request.action)
            self._transition(state, request.action)
            state.used_approvals.add(request.approval_id)
            state.generation += 1
            if request.action == "green_switched" and crossover is not None:
                if fence_receipt is None:
                    raise TritonBlueGreenError(
                        "causal_transition_receipt_mismatch",
                        request.action,
                        status_code=503,
                    )
                route_applied_monotonic_ns = time.perf_counter_ns()
                route_applied_actor = causal_start_observation(
                    "api-control-plane-route-switch-applied"
                )
                if (
                    int(route_applied_actor["process_id"])
                    != int(fence_receipt["actor_process_id"])
                    or int(route_applied_actor["thread_id"])
                    != int(fence_receipt["actor_thread_id"])
                    or int(fence_receipt["commit_ack_monotonic_ns"])
                    > int(fence_receipt["readback_started_monotonic_ns"])
                    or int(fence_receipt["readback_started_monotonic_ns"])
                    > int(fence_receipt["readback_finished_monotonic_ns"])
                    or int(fence_receipt["readback_finished_monotonic_ns"])
                    > route_applied_monotonic_ns
                    or state.generation != request.expected_generation + 1
                    or state.phase != "green_active"
                    or state.weights != {"blue": 0, "green": 100}
                ):
                    raise TritonBlueGreenError(
                        "causal_transition_actor_state_mismatch",
                        request.action,
                        status_code=503,
                    )
                state.last_transition_receipt = {
                    "schema_version": "evm.s6bm.route_transition_receipt.v1",
                    "transition_id": fence_receipt["transition_id"],
                    "fence_id": fence_receipt["fence_id"],
                    "attempt_id": crossover.attempt_id,
                    "run_id": crossover.run_id,
                    "request_id": crossover.request_id,
                    "cell_id": fence_receipt["cell_id"],
                    "replica_id": fence_receipt["replica_id"],
                    "source_revision": fence_receipt["source_revision"],
                    "source_payload_sha256": fence_receipt["source_payload_sha256"],
                    "old_route_generation": request.expected_generation,
                    "new_route_generation": state.generation,
                    "fence_sequence": fence_receipt["fence_sequence"],
                    "fence_transaction_id": fence_receipt["fence_transaction_id"],
                    "fence_payload_sha256": fence_receipt["fence_payload_sha256"],
                    "actor_identity": fence_receipt["actor_identity"],
                    "actor_process_id": fence_receipt["actor_process_id"],
                    "actor_thread_id": fence_receipt["actor_thread_id"],
                    "actor_commit_ack_monotonic_ns": fence_receipt[
                        "commit_ack_monotonic_ns"
                    ],
                    "fence_readback_started_monotonic_ns": fence_receipt[
                        "readback_started_monotonic_ns"
                    ],
                    "fence_readback_finished_monotonic_ns": fence_receipt[
                        "readback_finished_monotonic_ns"
                    ],
                    "route_applied_monotonic_ns": route_applied_monotonic_ns,
                    "route_applied_actor": route_applied_actor,
                    "state_readback": {
                        "generation": state.generation,
                        "phase": state.phase,
                        "route_weights": dict(state.weights),
                        "loaded_roles": sorted(state.loaded),
                    },
                    "fence_receipt_sha256": hashlib.sha256(
                        canonical(fence_receipt).encode("ascii")
                    ).hexdigest(),
                    "fence_receipt": fence_receipt,
                }
                state.crossover_switch_events[crossover.request_id].set()
            TRANSITIONS.labels(request.action, "applied").inc()
            return self.snapshot()

    @staticmethod
    def _validate_causal_crossover(
        state: _State,
        request: TritonBlueGreenControlRequest,
    ) -> TritonBlueGreenCausalIdentity:
        crossover = request.causal_crossover
        if crossover is None:
            raise TritonBlueGreenError(
                "causal_crossover_identity_absent", request.action, status_code=503
            )
        blue = state.request.blue
        if (
            crossover.run_id != request.run_id
            or crossover.model_role != "blue"
            or crossover.model_name != blue.model_name
            or crossover.model_version != blue.model_version
            or crossover.artifact_sha256 != blue.artifact_sha256
            or crossover.route_generation > request.expected_generation
        ):
            raise TritonBlueGreenError(
                "causal_crossover_identity_mismatch", request.action, status_code=409
            )
        if (
            request.action == "green_switched"
            and crossover.request_id not in state.crossover_switch_events
        ):
            raise TritonBlueGreenError(
                "causal_crossover_request_not_in_flight",
                request.action,
                status_code=409,
            )
        return crossover

    async def predict(
        self,
        request: TritonBlueGreenPredictRequest,
        *,
        terminal_effect_committer: TerminalEffectCommitter | None = None,
        start_receipt_committer: StartReceiptCommitter | None = None,
    ) -> TritonBlueGreenPredictResponse:
        controller_entry = causal_start_observation("s6bm-controller")
        switch_event: threading.Event | None = None
        with self._lock:
            state = self._require_state(request.run_id)
            digest = _request_digest(request)
            cached = state.responses.get(request.request_id)
            if cached is not None:
                if cached[0] != digest:
                    raise TritonBlueGreenError(
                        "idempotency_payload_mismatch", request.request_id, status_code=409
                    )
                cached_result = cached[1]
                state.duplicate_replays += 1
            else:
                cached_result = None
            if cached_result is not None:
                role = cached_result.model_role
                identity = state.request.blue if role == "blue" else state.request.green
                generation = cached_result.route_generation
                phase = cached_result.route_phase
                triton_url = state.request.triton_http_url
            else:
                if len(state.responses) >= state.request.max_request_cache:
                    raise TritonBlueGreenError(
                        "request_identity_cache_full", request.run_id, status_code=429
                    )
                role = _role_for_request(request.request_id, state.weights)
                if role not in state.loaded:
                    raise TritonBlueGreenError("route_target_not_loaded", role, status_code=503)
                identity = state.request.blue if role == "blue" else state.request.green
                offered = (
                    request.expected_model_role,
                    request.expected_model_name,
                    request.expected_model_version,
                    request.expected_artifact_sha256,
                )
                served = (
                    role,
                    identity.model_name,
                    identity.model_version,
                    identity.artifact_sha256,
                )
                if any(value is not None for value in offered) and offered != served:
                    raise TritonBlueGreenError(
                        "offered_served_identity_mismatch",
                        request.request_id,
                        status_code=409,
                    )
                state.in_flight[role] += 1
                IN_FLIGHT.labels(role, identity.model_name, identity.model_version).set(
                    state.in_flight[role]
                )
                generation = state.generation
                phase = state.phase
                triton_url = state.request.triton_http_url
                if request.causal_crossover and request.expected_route_generation != generation:
                    raise TritonBlueGreenError(
                        "causal_route_generation_mismatch",
                        request.request_id,
                        status_code=409,
                    )
                if request.causal_crossover:
                    switch_event = threading.Event()
                    state.crossover_switch_events[request.request_id] = switch_event

        started = time.perf_counter()
        effect_id = _effect_id(request, identity)
        span_attributes: dict[str, str | int] = {
            "evm.stage": "s6bm_controller",
            "evm.scenario.id": "S6B-M",
            "evm.run.id": request.run_id,
            "evm.attempt.id": request.attempt_id,
            "evm.request.id": request.request_id,
            "evm.model.role": role,
            "evm.model.name": identity.model_name,
            "evm.model.version": identity.model_version,
            "evm.model.artifact.sha256": identity.artifact_sha256,
            "evm.route.generation": generation,
            "evm.route.phase": phase,
            "evm.effect.id": effect_id,
        }
        counted_in_flight = cached_result is None
        try:
            if request.causal_crossover and cached_result is None:
                if start_receipt_committer is None:
                    raise TritonBlueGreenError(
                        "causal_start_store_unavailable",
                        request.request_id,
                        status_code=503,
                    )
                causal_identity = causal_identity_for_request(
                    request,
                    identity,
                    route_generation=generation,
                )
                try:
                    controller_receipt = await start_receipt_committer(
                        "controller_entry",
                        request,
                        {
                            **causal_identity.model_dump(mode="json"),
                            **controller_entry,
                            "route_phase": phase,
                        },
                    )
                except TritonBlueGreenError:
                    raise
                except Exception as exc:
                    raise TritonBlueGreenError(
                        "causal_controller_receipt_failed",
                        request.request_id,
                        status_code=503,
                    ) from exc
                if controller_receipt.get("readback_visible") is not True:
                    raise TritonBlueGreenError(
                        "causal_controller_receipt_mismatch",
                        request.request_id,
                        status_code=503,
                    )
            with trace_span(
                "s6bm.controller.predict",
                kind="internal",
                attributes=span_attributes,
            ) as controller_span:
                if cached_result is not None:
                    receipt = await self._commit_terminal_effect(
                        request,
                        cached_result.model_copy(update={"durable_effect": None}),
                        terminal_effect_committer,
                        span_attributes,
                    )
                    controller_span.set_attribute("evm.request.replayed", True)
                    controller_span.set_attribute("evm.terminal.outcome", "completed")
                    return cached_result.model_copy(
                        update={"replayed": True, "durable_effect": receipt}
                    )
                payload = {
                    "id": request.request_nonce,
                    "inputs": [
                        {
                            "name": "INPUT__0",
                            "shape": [1, 4],
                            "datatype": "FP32",
                            "data": request.input_values,
                        }
                    ],
                    "outputs": [{"name": "OUTPUT__0"}],
                }
                with trace_span(
                    "s6bm.triton.infer",
                    kind="client",
                    attributes={**span_attributes, "evm.stage": "triton_inference"},
                ) as inference_span:
                    async with httpx.AsyncClient(timeout=10) as client:
                        response = await client.post(
                            f"{triton_url}/v2/models/{identity.model_name}/versions/"
                            f"{identity.model_version}/infer",
                            json=payload,
                            headers=inference_span.context.headers(),
                        )
                    inference_span.set_attribute("http.response.status_code", response.status_code)
                    response.raise_for_status()
                    inference_span.set_attribute("evm.terminal.outcome", "completed")
                body = response.json()
                outputs = list(body.get("outputs", []))
                if len(outputs) != 1:
                    raise TritonBlueGreenError("triton_output_ambiguous", request.request_id)
                values = [float(value) for value in outputs[0].get("data", [])]
                if len(values) != 4:
                    raise TritonBlueGreenError("triton_output_shape", request.request_id)
                if request.hold_ms:
                    await asyncio.sleep(request.hold_ms / 1000)
                if request.causal_crossover:
                    if switch_event is None:
                        raise TritonBlueGreenError(
                            "causal_switch_event_absent",
                            request.request_id,
                            status_code=503,
                        )
                    timeout = float(os.getenv("EVM_S6BM_CAUSAL_SWITCH_TIMEOUT_SECONDS", "15"))
                    if timeout <= 0 or not await asyncio.to_thread(switch_event.wait, timeout):
                        raise TritonBlueGreenError(
                            "causal_switch_wait_timeout",
                            request.request_id,
                            status_code=503,
                        )
                elapsed_ms = (time.perf_counter() - started) * 1000
                result = TritonBlueGreenPredictResponse(
                    run_id=request.run_id,
                    attempt_id=request.attempt_id,
                    request_id=request.request_id,
                    trace_id=_trace_id(request.traceparent),
                    effect_id=effect_id,
                    route_generation=generation,
                    model_role=role,
                    model_name=identity.model_name,
                    model_version=identity.model_version,
                    artifact_sha256=identity.artifact_sha256,
                    route_phase=phase,
                    output=values,
                    result_sha256=_result_digest(
                        request,
                        identity,
                        role=role,
                        generation=generation,
                        phase=phase,
                        output=values,
                    ),
                    elapsed_ms=elapsed_ms,
                )
                receipt = await self._commit_terminal_effect(
                    request,
                    result,
                    terminal_effect_committer,
                    span_attributes,
                )
                result = result.model_copy(
                    update={"replayed": receipt.replayed, "durable_effect": receipt}
                )
                with self._lock:
                    current = self._require_state(request.run_id)
                    original = current.responses.get(request.request_id)
                    if original is None:
                        current.responses[request.request_id] = (digest, result)
                    elif original[0] != digest:
                        raise TritonBlueGreenError(
                            "idempotency_payload_mismatch", request.request_id, status_code=409
                        )
                if not receipt.replayed:
                    REQUESTS.labels(
                        role, identity.model_name, identity.model_version, "completed"
                    ).inc()
                    TERMINAL_EFFECTS.labels(
                        role, identity.model_name, identity.model_version, "committed"
                    ).inc()
                    LATENCY.labels(role, identity.model_name, identity.model_version).observe(
                        elapsed_ms / 1000
                    )
                controller_span.set_attribute("evm.terminal.outcome", "completed")
                controller_span.set_attribute("evm.request.replayed", receipt.replayed)
                return result
        except Exception:
            REQUESTS.labels(role, identity.model_name, identity.model_version, "failed").inc()
            raise
        finally:
            with self._lock:
                current = self._state
                if (
                    counted_in_flight
                    and current is not None
                    and current.request.run_id == request.run_id
                ):
                    current.crossover_switch_events.pop(request.request_id, None)
                    current.in_flight[role] = max(0, current.in_flight[role] - 1)
                    IN_FLIGHT.labels(role, identity.model_name, identity.model_version).set(
                        current.in_flight[role]
                    )

    async def _commit_terminal_effect(
        self,
        request: TritonBlueGreenPredictRequest,
        result: TritonBlueGreenPredictResponse,
        committer: TerminalEffectCommitter | None,
        span_attributes: Mapping[str, str | int],
    ) -> TritonBlueGreenDurableEffectReceipt:
        if committer is None:
            if os.getenv("EVM_S6BM_REQUIRE_DURABLE_EFFECT", "0").strip().lower() in {
                "1",
                "true",
                "yes",
            }:
                raise TritonBlueGreenError(
                    "durable_effect_store_unavailable",
                    request.request_id,
                    status_code=503,
                )
            now = time.perf_counter_ns()
            return TritonBlueGreenDurableEffectReceipt(
                entity_kind="s6bm_terminal_effect",
                entity_id=result.effect_id,
                request_sha256=_request_digest(request),
                stored_payload_sha256=hashlib.sha256(
                    canonical(result.model_dump(mode="json", exclude={"durable_effect"})).encode(
                        "ascii"
                    )
                ).hexdigest(),
                database_recorded_at="1970-01-01T00:00:00Z",
                entity_created_at="1970-01-01T00:00:00Z",
                idempotency_created_at="1970-01-01T00:00:00Z",
                readback_at="1970-01-01T00:00:00Z",
                transaction_id="test-only-nondurable",
                synchronous_commit="on",
                commit_ack_monotonic_ns=now,
                readback_started_monotonic_ns=now,
                readback_finished_monotonic_ns=now,
                readback_visible=True,
                replayed=False,
            )
        with trace_span(
            "s6bm.terminal_effect.commit",
            kind="internal",
            attributes={**span_attributes, "evm.stage": "durable_terminal_effect"},
        ) as effect_span:
            try:
                raw_receipt = await committer(request, result)
                receipt = TritonBlueGreenDurableEffectReceipt.model_validate(raw_receipt)
            except TritonBlueGreenError:
                raise
            except Exception as exc:
                raise TritonBlueGreenError(
                    "durable_effect_commit_failed",
                    request.request_id,
                    status_code=503,
                ) from exc
            if (
                receipt.entity_id != result.effect_id
                or receipt.readback_visible is not True
                or receipt.synchronous_commit != "on"
                or (
                    _strict_causal_required()
                    and (
                        receipt.schema_version != "evm.s6bm.durable_effect_receipt.v4"
                        or receipt.causal_sequence is None
                        or receipt.causal_payload_sha256 is None
                        or receipt.write_backend_pid is None
                        or receipt.commit_timestamp is None
                        or receipt.commit_timestamp_observed_at is None
                        or receipt.commit_timestamp_backend_pid is None
                        or receipt.commit_timestamp_tracking != "on"
                        or receipt.commit_timestamp_visible is not True
                        or receipt.separate_connection_readback is not True
                        or receipt.database_clock_anchor is None
                        or receipt.database_clock_anchor_candidates is None
                        or receipt.database_clock_anchor_selection is None
                    )
                )
            ):
                raise TritonBlueGreenError(
                    "durable_effect_receipt_mismatch",
                    request.request_id,
                    status_code=503,
                )
            for key, value in {
                "evm.effect.transaction.id": receipt.transaction_id,
                "evm.effect.causal.sequence": receipt.causal_sequence or 0,
                "evm.effect.commit.timestamp": receipt.commit_timestamp or "unavailable",
                "evm.effect.write.backend_pid": receipt.write_backend_pid or 0,
                "evm.effect.readback.backend_pid": (receipt.commit_timestamp_backend_pid or 0),
                "evm.effect.readback.visible": receipt.readback_visible,
                "evm.effect.replayed": receipt.replayed,
                "evm.terminal.outcome": "completed",
            }.items():
                effect_span.set_attribute(key, value)
            database_anchor = dict(receipt.database_clock_anchor or {})
            if database_anchor:
                effect_span.set_attribute(
                    "evm.effect.database_clock.anchor_hash",
                    str(database_anchor.get("anchor_hash", "")),
                )
                effect_span.set_attribute(
                    "evm.effect.database_clock.anchor_nonce",
                    str(database_anchor.get("anchor_nonce", "")),
                )
                effect_span.set_attribute(
                    "evm.effect.database_clock.backend_pid",
                    int(database_anchor.get("backend_pid", 0)),
                )
                effect_span.set_attribute(
                    "evm.effect.database_clock.anchor_width_ns",
                    int(database_anchor.get("monotonic_after_ns", 0))
                    - int(database_anchor.get("monotonic_before_ns", 0)),
                )
                candidates = list(receipt.database_clock_anchor_candidates or [])
                effect_span.set_attribute(
                    "evm.effect.database_clock.candidate_count", len(candidates)
                )
                effect_span.set_attribute(
                    "evm.effect.database_clock.candidates_sha256",
                    hashlib.sha256(canonical(candidates).encode("ascii")).hexdigest(),
                )
                effect_span.set_attribute(
                    "evm.effect.database_clock.selected_sequence",
                    int(
                        dict(receipt.database_clock_anchor_selection or {}).get(
                            "selected_sequence", 0
                        )
                    ),
                )
            return receipt

    def snapshot(self) -> TritonBlueGreenStateResponse:
        with self._lock:
            if self._state is None:
                return TritonBlueGreenStateResponse(initialized=False)
            state = self._state
            return TritonBlueGreenStateResponse(
                initialized=True,
                run_id=state.request.run_id,
                generation=state.generation,
                phase=state.phase,
                route_weights=dict(state.weights),
                loaded_roles=sorted(state.loaded),
                in_flight=dict(state.in_flight),
                accepted_unique=len(state.responses),
                terminal_unique=len(state.responses),
                duplicate_replays=state.duplicate_replays,
                used_approvals=len(state.used_approvals),
                transition_receipt=(
                    dict(state.last_transition_receipt)
                    if state.last_transition_receipt is not None
                    else None
                ),
            )

    def reset(self, run_id: str, lease_id: str, fencing_token: str) -> None:
        self._assert_lease(run_id, lease_id, fencing_token)
        with self._lock:
            state = self._require_state(run_id)
            if any(state.in_flight.values()):
                raise TritonBlueGreenError("controller_reset_in_flight", run_id)
            if state.phase not in {"rolled_back", "closed", "blue_only"}:
                raise TritonBlueGreenError("controller_reset_phase", state.phase)
            self._state = None

    def reset_for_tests(self) -> None:
        with self._lock:
            self._state = None

    @staticmethod
    def _assert_lease(run_id: str, lease_id: str, fencing_token: str) -> None:
        assert_scale_validation_gpu_lease_owner(
            run_id=run_id,
            lease_id=lease_id,
            fencing_token=fencing_token,
            purpose="scale_validation_inference",
            scenario_id="S6B-M",
            model_family="tabular",
        )

    def _require_state(self, run_id: str) -> _State:
        if self._state is None:
            raise TritonBlueGreenError("controller_not_initialized", run_id, status_code=503)
        if self._state.request.run_id != run_id:
            raise TritonBlueGreenError("controller_run_identity_mismatch", run_id)
        return self._state

    @staticmethod
    def _validate_guard_signals(request: TritonBlueGreenControlRequest) -> None:
        if request.action == "green_loaded" and not request.preflight_vram_passed:
            TRANSITIONS.labels(request.action, "rejected_vram").inc()
            raise TritonBlueGreenError("vram_preflight_rejected", request.run_id)
        if request.action in {"green_loaded", "canary_started"} and not request.readiness_passed:
            TRANSITIONS.labels(request.action, "rejected_readiness").inc()
            raise TritonBlueGreenError("green_readiness_rejected", request.run_id)
        if request.action in {"canary_started", "green_switched"} and not request.canary_passed:
            TRANSITIONS.labels(request.action, "rejected_canary").inc()
            raise TritonBlueGreenError("green_canary_rejected", request.run_id)

    @staticmethod
    def _validate_transition(state: _State, action: ControlAction) -> None:
        legal = {
            ("blue_only", "green_loaded"),
            ("green_warmup", "canary_started"),
            ("canary", "green_switched"),
            ("green_active", "blue_drain_started"),
            ("blue_draining", "blue_unloaded"),
            ("green_only", "blue_loaded"),
            ("rollback_warmup", "blue_switched"),
            ("blue_active_rollback", "green_drain_started"),
            ("green_draining", "green_unloaded"),
            ("green_warmup", "green_aborted"),
            ("canary", "green_aborted"),
            ("rolled_back", "closed"),
        }
        if (state.phase, action) not in legal:
            raise TritonBlueGreenError("illegal_blue_green_transition", f"{state.phase}:{action}")
        if action == "blue_unloaded" and state.in_flight["blue"] != 0:
            raise TritonBlueGreenError("blue_drain_incomplete", str(state.in_flight["blue"]))
        if action in {"green_unloaded", "green_aborted"} and state.in_flight["green"] != 0:
            code = (
                "green_drain_incomplete" if action == "green_unloaded" else "green_abort_in_flight"
            )
            raise TritonBlueGreenError(code, str(state.in_flight["green"]))

    @staticmethod
    def _apply_model_control(state: _State, action: ControlAction) -> None:
        if os.getenv("EVM_S6BM_APPLY_MODEL_CONTROL", "0").strip().lower() not in {
            "1",
            "true",
            "yes",
        }:
            return
        role_by_action: dict[ControlAction, ModelRole] = {
            "green_loaded": "green",
            "blue_unloaded": "blue",
            "blue_loaded": "blue",
            "green_unloaded": "green",
            "green_aborted": "green",
        }
        role = role_by_action.get(action)
        if role is None:
            return
        identity = state.request.blue if role == "blue" else state.request.green
        operation = "load" if action in {"green_loaded", "blue_loaded"} else "unload"
        url = (
            f"{state.request.triton_http_url}/v2/repository/models/"
            f"{identity.model_name}/{operation}"
        )
        try:
            with httpx.Client(timeout=15) as client:
                response = client.post(url)
                response.raise_for_status()
                ready = client.get(
                    f"{state.request.triton_http_url}/v2/models/"
                    f"{identity.model_name}/versions/{identity.model_version}/ready"
                )
            if operation == "load" and ready.status_code != 200:
                raise TritonBlueGreenError(
                    "triton_model_not_ready", f"{identity.model_name}:{ready.status_code}"
                )
            if operation == "unload" and ready.status_code == 200:
                raise TritonBlueGreenError("triton_model_still_ready", identity.model_name)
        except TritonBlueGreenError:
            TRANSITIONS.labels(action, "triton_effect_failed").inc()
            raise
        except (httpx.HTTPError, OSError) as exc:
            TRANSITIONS.labels(action, "triton_effect_failed").inc()
            raise TritonBlueGreenError(
                "triton_model_control_failed", f"{identity.model_name}:{operation}:{exc}"
            ) from exc

    @staticmethod
    def _transition(state: _State, action: ControlAction) -> None:
        expected = {
            ("blue_only", "green_loaded"): "green_warmup",
            ("green_warmup", "canary_started"): "canary",
            ("canary", "green_switched"): "green_active",
            ("green_active", "blue_drain_started"): "blue_draining",
            ("blue_draining", "blue_unloaded"): "green_only",
            ("green_only", "blue_loaded"): "rollback_warmup",
            ("rollback_warmup", "blue_switched"): "blue_active_rollback",
            ("blue_active_rollback", "green_drain_started"): "green_draining",
            ("green_draining", "green_unloaded"): "rolled_back",
            ("green_warmup", "green_aborted"): "blue_only",
            ("canary", "green_aborted"): "blue_only",
            ("rolled_back", "closed"): "closed",
        }
        target = expected[(state.phase, action)]
        if action == "green_loaded":
            state.loaded.add("green")
        elif action == "canary_started":
            state.weights = {"blue": 90, "green": 10}
        elif action == "green_switched":
            state.weights = {"blue": 0, "green": 100}
        elif action == "blue_unloaded":
            state.loaded.discard("blue")
        elif action == "blue_loaded":
            state.loaded.add("blue")
        elif action == "blue_switched":
            state.weights = {"blue": 100, "green": 0}
        elif action == "green_unloaded":
            state.loaded.discard("green")
        elif action == "green_aborted":
            state.weights = {"blue": 100, "green": 0}
            state.loaded.discard("green")
        state.phase = target


manager = TritonBlueGreenManager()


def ensure_sha256(value: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise TritonBlueGreenError("sha256_invalid", value, status_code=422)
    return value
