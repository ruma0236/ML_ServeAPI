from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

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
    traceparent: str
    input_values: list[float] = Field(min_length=4, max_length=4)
    hold_ms: int = Field(default=0, ge=0, le=2500)
    expected_model_role: ModelRole | None = None
    expected_model_name: str | None = Field(default=None, pattern=r"^[a-z0-9_-]+$")
    expected_model_version: str | None = Field(default=None, pattern=r"^[0-9]+$")
    expected_artifact_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

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
    output: list[float]
    elapsed_ms: float = Field(ge=0)
    replayed: bool = False


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


@dataclass
class _State:
    request: TritonBlueGreenInitializeRequest
    generation: int = 1
    phase: str = "blue_only"
    weights: dict[ModelRole, int] = field(default_factory=lambda: {"blue": 100, "green": 0})
    loaded: set[ModelRole] = field(default_factory=lambda: {"blue"})
    in_flight: dict[ModelRole, int] = field(default_factory=lambda: {"blue": 0, "green": 0})
    responses: dict[str, tuple[str, TritonBlueGreenPredictResponse]] = field(default_factory=dict)
    used_approvals: set[str] = field(default_factory=set)
    duplicate_replays: int = 0


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

    def control(self, request: TritonBlueGreenControlRequest) -> TritonBlueGreenStateResponse:
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
            self._apply_model_control(state, request.action)
            self._transition(state, request.action)
            state.used_approvals.add(request.approval_id)
            state.generation += 1
            TRANSITIONS.labels(request.action, "applied").inc()
            return self.snapshot()

    async def predict(
        self, request: TritonBlueGreenPredictRequest
    ) -> TritonBlueGreenPredictResponse:
        with self._lock:
            state = self._require_state(request.run_id)
            digest = _request_digest(request)
            cached = state.responses.get(request.request_id)
            if cached is not None:
                if cached[0] != digest:
                    raise TritonBlueGreenError(
                        "idempotency_payload_mismatch", request.request_id, status_code=409
                    )
                state.duplicate_replays += 1
                return cached[1].model_copy(update={"replayed": True})
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
            triton_url = state.request.triton_http_url

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
            "evm.effect.id": effect_id,
        }
        try:
            with trace_span(
                "s6bm.controller.predict",
                kind="internal",
                attributes=span_attributes,
            ) as controller_span:
                payload = {
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
                    output=values,
                    elapsed_ms=elapsed_ms,
                )
                with self._lock:
                    current = self._require_state(request.run_id)
                    current.responses[request.request_id] = (digest, result)
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
                return result
        except Exception:
            REQUESTS.labels(role, identity.model_name, identity.model_version, "failed").inc()
            raise
        finally:
            with self._lock:
                current = self._state
                if current is not None and current.request.run_id == request.run_id:
                    current.in_flight[role] = max(0, current.in_flight[role] - 1)
                    IN_FLIGHT.labels(role, identity.model_name, identity.model_version).set(
                        current.in_flight[role]
                    )

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
