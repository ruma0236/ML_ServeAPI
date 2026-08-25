from __future__ import annotations

import hashlib
import json
import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


CLAIM_BOUNDARY = (
    "one Windows/WSL2 physical node, one RTX 4080, one Triton GPU container, "
    "controlled traffic; not production SLA, physical HA, node failover, multi-node, "
    "multi-GPU, MIG, MPS, or a zero-interruption guarantee"
)
STRICT_V4_CLAIM_BOUNDARY = (
    "one Windows/WSL2 physical node, one RTX 4080, one Triton GPU container, "
    "one local PostgreSQL control-plane instance, controlled traffic; not production SLA, "
    "database HA/DR durability, physical HA, node failover, multi-node, multi-GPU, MIG, "
    "MPS, or a zero-interruption guarantee"
)
SUCCESS_PHASES = [
    "blue_only",
    "green_warmup",
    "canary",
    "green_active",
    "blue_draining",
    "green_only",
    "rollback_warmup",
    "blue_active_rollback",
    "green_draining",
    "rolled_back",
]
TRACE_ID = re.compile(r"^[a-f0-9]{32}$")
SPAN_ID = re.compile(r"^[a-f0-9]{16}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")


class S6BMRuntimeError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("ascii")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class S6BMModel:
    role: str
    model_name: str
    model_version: str
    artifact_sha256: str
    config_sha256: str
    expected_output: tuple[float, ...]

    @classmethod
    def from_mapping(cls, role: str, raw: Mapping[str, Any]) -> "S6BMModel":
        model = cls(
            role=role,
            model_name=str(raw["model_name"]),
            model_version=str(raw["model_version"]),
            artifact_sha256=str(raw["artifact_sha256"]),
            config_sha256=str(raw["config_sha256"]),
            expected_output=tuple(float(value) for value in raw["expected_output"]),
        )
        if len(model.artifact_sha256) != 64 or len(model.config_sha256) != 64:
            raise S6BMRuntimeError(f"s6bm_model_digest:{role}")
        if len(model.expected_output) != 4 or any(
            not math.isfinite(value) for value in model.expected_output
        ):
            raise S6BMRuntimeError(f"s6bm_model_output:{role}")
        return model


@dataclass(frozen=True)
class S6BMConfig:
    schema_version: str
    image: str
    image_digest: str
    expected_gpu_name: str
    repository_sha256: str
    seed: int
    blue: S6BMModel
    green: S6BMModel
    procedure: Mapping[str, int | float]
    clock: Mapping[str, Any]
    causal_fence: Mapping[str, Any]
    triton_actor_receipt: Mapping[str, Any]
    durable_effect: Mapping[str, Any]
    trace: Mapping[str, Any]
    run_set: Mapping[str, Any]
    continuity: Mapping[str, Any]
    ports: Mapping[str, int]
    telemetry: Mapping[str, str | int]
    claim_boundary: str

    @classmethod
    def from_path(cls, path: Path) -> "S6BMConfig":
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        identity = dict(raw["identity"])
        procedure = dict(raw["procedure"])
        ports = {str(key): int(value) for key, value in dict(raw["ports"]).items()}
        telemetry = dict(raw["telemetry"])
        clock = dict(raw.get("clock", {}))
        causal_fence = dict(raw.get("causal_fence", {}))
        triton_actor_receipt = dict(raw.get("triton_actor_receipt", {}))
        durable_effect = dict(raw.get("durable_effect", {}))
        trace = dict(raw.get("trace", {}))
        run_set = dict(raw.get("run_set", {}))
        continuity = dict(raw.get("continuity", {}))
        config = cls(
            schema_version=str(raw["schema_version"]),
            image=str(identity["triton_image"]),
            image_digest=str(identity["triton_image_digest"]),
            expected_gpu_name=str(identity["expected_gpu_name"]),
            repository_sha256=str(identity["model_repository_sha256"]),
            seed=int(identity["seed"]),
            blue=S6BMModel.from_mapping("blue", dict(raw["blue"])),
            green=S6BMModel.from_mapping("green", dict(raw["green"])),
            procedure=procedure,
            clock=clock,
            causal_fence=causal_fence,
            triton_actor_receipt=triton_actor_receipt,
            durable_effect=durable_effect,
            trace=trace,
            run_set=run_set,
            continuity=continuity,
            ports=ports,
            telemetry=telemetry,
            claim_boundary=str(dict(raw["claim_boundary"])["scope"]),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.schema_version not in {
            "evm.s8_v4.s6bm_runtime_config.v1",
            "evm.s8_v4.s6bm_runtime_config.v2",
            "evm.s8_v4.s6bm_runtime_config.v3",
            "evm.s8_v4.s6bm_runtime_config.v4",
        }:
            raise S6BMRuntimeError("s6bm_config_schema")
        if not self.image_digest.startswith("sha256:") or len(self.image_digest) != 71:
            raise S6BMRuntimeError("s6bm_image_digest")
        if len(self.repository_sha256) != 64:
            raise S6BMRuntimeError("s6bm_repository_digest")
        if self.blue.artifact_sha256 == self.green.artifact_sha256:
            raise S6BMRuntimeError("s6bm_model_artifacts_not_distinct")
        exact = {
            "baseline_repetitions": 3,
            "successful_transition_repetitions": 3,
            "wrong_digest_repetitions": 3,
            "negative_profile_repetitions": 3,
            "logical_requests_per_transition": 1000,
            "canary_weight_percent": 10,
            "max_transition_5xx": 0,
        }
        for key, expected in exact.items():
            if int(self.procedure[key]) != expected:
                raise S6BMRuntimeError(f"s6bm_frozen_procedure:{key}")
        if float(self.procedure["max_transition_p99_ms"]) <= 0:
            raise S6BMRuntimeError("s6bm_p99_guardrail")
        if float(self.procedure["max_inter_completion_gap_ms"]) <= 0:
            raise S6BMRuntimeError("s6bm_gap_guardrail")
        if len(set(self.ports.values())) != len(self.ports):
            raise S6BMRuntimeError("s6bm_port_collision")
        if self.schema_version.endswith(".v2"):
            required_clock = {
                "contract": "independent_dual_clock_anchor_chain_v1",
                "max_anchor_width_ns": 1_000_000,
                "max_anchor_gap_seconds": 30,
                "max_offset_spread_ns": 2_000_000,
                "max_phase_interval_ns": 250_000_000,
            }
            if any(self.clock.get(key) != value for key, value in required_clock.items()):
                raise S6BMRuntimeError("s6bm_clock_contract")
            required_effect = {
                "contract": "existing_control_plane_entities_idempotency_v1",
                "synchronous_commit": "on",
                "commit_readback_required": True,
                "entity_kind": "s6bm_terminal_effect",
            }
            if any(self.durable_effect.get(key) != value for key, value in required_effect.items()):
                raise S6BMRuntimeError("s6bm_durable_effect_contract")
        if self.schema_version.endswith(".v3"):
            required_clock = {
                "contract": "independent_dual_clock_anchor_chain_v2",
                "max_anchor_width_ns": 1_000_000,
                "max_anchor_gap_seconds": 30,
                "max_offset_spread_ns": 2_000_000,
                "max_phase_interval_ns": 250_000_000,
                "per_request_offset_forbidden": True,
                "overlapping_causal_intervals_fail": True,
                "source_identity_required": True,
            }
            if any(self.clock.get(key) != value for key, value in required_clock.items()):
                raise S6BMRuntimeError("s6bm_clock_contract_v3")
            required_fence = {
                "contract": "existing_transactional_store_causal_sequence_v1",
                "sequence_kind": "postgresql_bigserial",
                "receipt_commit_readback_required": True,
                "route_switch_requires_all_start_receipts": True,
                "stale_blue_admission_forbidden": True,
                "unload_requires_all_pre_switch_blue_terminal_effects": True,
                "exact_commit_instant_claimed": False,
            }
            if any(self.causal_fence.get(key) != value for key, value in required_fence.items()):
                raise S6BMRuntimeError("s6bm_causal_fence_contract")
            if self.causal_fence.get("required_start_receipts") != [
                "api_server_handler_entry",
                "controller_entry",
                "triton_backend_compute_entry",
            ]:
                raise S6BMRuntimeError("s6bm_causal_receipt_stages")
            required_actor = {
                "contract": "official_triton_timestamp_trace_collector_v1",
                "required_activity": "COMPUTE_START",
                "raw_trace_required": True,
                "request_nonce_binding_required": True,
                "model_version_artifact_binding_required": True,
                "collector_commit_readback_required": True,
                "missing_or_ambiguous_trace_fails": True,
            }
            if any(
                self.triton_actor_receipt.get(key) != value for key, value in required_actor.items()
            ):
                raise S6BMRuntimeError("s6bm_triton_actor_receipt_contract")
            required_effect = {
                "contract": "existing_control_plane_entities_idempotency_causal_v2",
                "synchronous_commit": "on",
                "commit_readback_required": True,
                "entity_kind": "s6bm_terminal_effect",
                "same_transaction_causal_receipt": True,
            }
            if any(self.durable_effect.get(key) != value for key, value in required_effect.items()):
                raise S6BMRuntimeError("s6bm_durable_effect_contract_v3")
        if self.schema_version.endswith(".v4"):
            required_clock = {
                "contract": "independent_dual_clock_anchor_chain_v3",
                "max_anchor_width_ns": 1_000_000,
                "max_anchor_gap_seconds": 30,
                "max_offset_spread_ns": 2_000_000,
                "max_phase_interval_ns": 250_000_000,
                "affine_model": "centered_ols_fraction_all_points_v1",
                "affine_max_drift_ppm": 100,
                "affine_max_residual_ns": 1_000_000,
                "affine_max_step_ns": 2_000_000,
                "affine_required_outlier_count": 0,
                "affine_projection_uncertainty": (
                    "ceil_max_abs_residual_plus_ceil_anchor_half_width_plus_1ns"
                ),
                "per_request_offset_forbidden": True,
                "overlapping_causal_intervals_fail": True,
                "source_identity_required": True,
                "independent_nonce_anchor_required": True,
                "adjudicated_request_anchor_forbidden": True,
            }
            if any(self.clock.get(key) != value for key, value in required_clock.items()):
                raise S6BMRuntimeError("s6bm_clock_contract_v4")
            required_fence = {
                "contract": "existing_transactional_store_causal_sequence_v2",
                "sequence_kind": "postgresql_bigserial",
                "receipt_commit_readback_required": True,
                "route_switch_requires_all_start_receipts": True,
                "stale_blue_admission_forbidden": True,
                "unload_requires_all_pre_switch_blue_terminal_effects": True,
                "same_transaction_effect_event_and_sequence": True,
                "same_transaction_entity_idempotency_effect_event_and_sequence": True,
                "exact_commit_instant_claimed": False,
            }
            if any(self.causal_fence.get(key) != value for key, value in required_fence.items()):
                raise S6BMRuntimeError("s6bm_causal_fence_contract_v4")
            if self.causal_fence.get("required_start_receipts") != [
                "api_server_handler_entry",
                "controller_entry",
                "triton_backend_compute_entry",
            ]:
                raise S6BMRuntimeError("s6bm_causal_receipt_stages_v4")
            required_actor = {
                "contract": "official_triton_timestamp_trace_collector_v2",
                "required_activity": "COMPUTE_START",
                "raw_trace_required": True,
                "request_nonce_binding_required": True,
                "model_version_artifact_binding_required": True,
                "collector_commit_readback_required": True,
                "missing_or_ambiguous_trace_fails": True,
                "registration_actor": "dedicated_collector_process",
                "runner_synthesized_receipt_forbidden": True,
            }
            if any(
                self.triton_actor_receipt.get(key) != value for key, value in required_actor.items()
            ):
                raise S6BMRuntimeError("s6bm_triton_actor_receipt_contract_v4")
            required_effect = {
                "contract": "existing_control_plane_entities_idempotency_causal_v3",
                "synchronous_commit": "on",
                "commit_readback_required": True,
                "commit_timestamp_tracking_required": True,
                "commit_timestamp_separate_connection_required": True,
                "commit_timestamp_readback_lane": "bounded_parallel_post_commit_v1",
                "commit_timestamp_readback_max_concurrency": 2,
                "commit_timestamp_readback_acquire_timeout_seconds": 2.0,
                "database_clock_anchor_max_selected_width_ns": 5_000_000,
                "write_pool_size_change_forbidden": True,
                "whole_request_serialization_forbidden": True,
                "entity_kind": "s6bm_terminal_effect",
                "same_transaction_causal_receipt": True,
            }
            if any(self.durable_effect.get(key) != value for key, value in required_effect.items()):
                raise S6BMRuntimeError("s6bm_durable_effect_contract_v4")
            if self.trace != {
                "contract": "w3c_parent_chain_to_triton_backend_v1",
                "required_chain": [
                    "api_server",
                    "controller",
                    "triton_client",
                    "triton_infer_request",
                    "triton_model",
                    "triton_compute",
                    "durable_effect",
                ],
                "exact_parent_span_required": True,
                "request_trace_effect_model_generation_backend_join_required": True,
            }:
                raise S6BMRuntimeError("s6bm_trace_contract_v4")
            expected_repetitions = [1, 2, 3]
            expected_profiles = {
                "contract": "exact_frozen_matrix_set_v1",
                "baseline": expected_repetitions,
                "successful_transition": expected_repetitions,
                "wrong_digest": expected_repetitions,
                "green_load_failure": expected_repetitions,
                "green_readiness_failure": expected_repetitions,
                "green_canary_failure": expected_repetitions,
                "vram_preflight_rejection": expected_repetitions,
            }
            if self.run_set != expected_profiles:
                raise S6BMRuntimeError("s6bm_run_set_contract_v4")
            expected_continuity = {
                "contract": "fixed_exact_1000_blue_continuity_v1",
                "logical_request_count": 1000,
                "canary_count": 100,
                "causal_hold_count": 1,
                "bridge_count": 40,
                "normal_count": 859,
                "cadence_ms": 150,
                "producer_workers": 4,
                "max_in_flight_requests": 4,
                "max_request_payload_bytes": 4096,
                "max_in_flight_payload_bytes": 16384,
                "bridge_hold_count": 4,
                "bridge_hold_ms": 400,
                "required_actor_bridge_count": 4,
                "crossover_bridge_count": 1,
                "required_actor_bridge_selection": "prefix",
                "held_bridge_selection": "suffix",
                "crossover_bridge_selection": "first_required",
                "pending_crossover_count": 2,
                "pre_switch_terminal_bridge_count": 39,
                "route_switch_barrier_mode": "exact_one_after_39_blue_terminal",
                "route_switch_barrier_timeout_seconds": 15,
                "max_schedule_lateness_ms": 500,
                "minimum_blue_in_flight_at_switch": 2,
                "minimum_bridge_cross_switch_completions": 1,
                "minimum_transition_terminal_completions": 8,
                "adaptive_pacing_forbidden": True,
                "post_hoc_windowing_forbidden": True,
                "schedule_hash_algorithm": "sha256_canonical_json",
            }
            if self.continuity != expected_continuity:
                raise S6BMRuntimeError("s6bm_continuity_contract_v4")
            if int(self.continuity["canary_count"]) + int(
                self.continuity["causal_hold_count"]
            ) + int(self.continuity["bridge_count"]) + int(self.continuity["normal_count"]) != int(
                self.continuity["logical_request_count"]
            ):
                raise S6BMRuntimeError("s6bm_continuity_partition_count")
            if not 1 <= int(self.continuity["required_actor_bridge_count"]) <= int(
                self.continuity["bridge_count"]
            ) or not 1 <= int(self.continuity["bridge_hold_count"]) <= int(
                self.continuity["bridge_count"]
            ):
                raise S6BMRuntimeError("s6bm_continuity_actor_receipt_bounds")
            if int(self.continuity["crossover_bridge_count"]) != 1 or int(
                self.continuity["crossover_bridge_count"]
            ) > int(self.continuity["required_actor_bridge_count"]):
                raise S6BMRuntimeError("s6bm_continuity_crossover_bounds")

    def public_snapshot(self) -> dict[str, Any]:
        snapshot = {
            "schema_version": self.schema_version,
            "identity": {
                "triton_image": self.image,
                "triton_image_digest": self.image_digest,
                "expected_gpu_name": self.expected_gpu_name,
                "model_repository_sha256": self.repository_sha256,
                "seed": self.seed,
            },
            "models": {
                "blue": self.blue.__dict__,
                "green": self.green.__dict__,
            },
            "procedure": dict(self.procedure),
            "clock": dict(self.clock),
            "causal_fence": dict(self.causal_fence),
            "triton_actor_receipt": dict(self.triton_actor_receipt),
            "durable_effect": dict(self.durable_effect),
            "trace": dict(self.trace),
            "run_set": dict(self.run_set),
            "ports": dict(self.ports),
            "telemetry": dict(self.telemetry),
            "claim_boundary": (
                STRICT_V4_CLAIM_BOUNDARY
                if self.schema_version.endswith((".v2", ".v3", ".v4"))
                else CLAIM_BOUNDARY
            ),
        }
        if self.continuity:
            snapshot["continuity"] = dict(self.continuity)
        return snapshot


def _finite(value: Any, name: str) -> float:
    projected = float(value)
    if not math.isfinite(projected):
        raise S6BMRuntimeError(f"s6bm_non_finite:{name}")
    return projected


def _require_zero(summary: Mapping[str, Any], names: Sequence[str], prefix: str) -> None:
    for name in names:
        if int(summary.get(name, -1)) != 0:
            raise S6BMRuntimeError(f"s6bm_{prefix}:{name}")


def _expected_identity(config: S6BMConfig, role: str) -> dict[str, Any]:
    model = config.blue if role == "blue" else config.green
    return {
        "model_role": role,
        "model_name": model.model_name,
        "model_version": model.model_version,
        "artifact_sha256": model.artifact_sha256,
    }


def _route_role(request_id: str, green_weight_percent: int) -> str:
    bucket = int(hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "green" if bucket < green_weight_percent else "blue"


def _blue_routed_request_id(prefix: str) -> str:
    for suffix in range(10_000):
        request_id = f"{prefix}-blue-{suffix:05d}"
        if _route_role(request_id, 10) == "blue":
            return request_id
    raise S6BMRuntimeError("s6bm_continuity_blue_request_identity")


def build_continuity_plan(config: S6BMConfig, attempt_id: str) -> dict[str, Any]:
    """Build the immutable exact-cardinality traffic plan before runtime control starts."""
    if not config.schema_version.endswith(".v4"):
        raise S6BMRuntimeError("s6bm_continuity_requires_v4")
    if not attempt_id:
        raise S6BMRuntimeError("s6bm_continuity_attempt_identity")
    continuity = config.continuity
    seed_prefix = f"{attempt_id}-s{config.seed}"
    ordinal = 0

    def entry(
        request_id: str,
        traffic_role: str,
        expected_model_role: str,
        *,
        scheduled_offset_ms: int | None = None,
        hold_ms: int = 0,
        actor_receipt_required: bool = False,
        causal_crossover: bool = False,
        route_switch_deadline_owner: bool = False,
    ) -> dict[str, Any]:
        nonlocal ordinal
        value = {
            "ordinal": ordinal,
            "request_id": request_id,
            "traffic_role": traffic_role,
            "expected_model_role": expected_model_role,
            "hold_ms": hold_ms,
            "actor_receipt_required": actor_receipt_required,
            "causal_crossover": causal_crossover,
            "route_switch_deadline_owner": route_switch_deadline_owner,
        }
        if scheduled_offset_ms is not None:
            value["scheduled_offset_ms"] = scheduled_offset_ms
        ordinal += 1
        return value

    canary = []
    for index in range(int(continuity["canary_count"])):
        request_id = f"{seed_prefix}-canary-{index:05d}"
        canary.append(
            entry(
                request_id,
                "canary",
                _route_role(request_id, int(config.procedure["canary_weight_percent"])),
            )
        )
    causal_hold = [
        entry(
            _blue_routed_request_id(f"{seed_prefix}-causal-hold"),
            "causal_hold",
            "blue",
            hold_ms=int(config.procedure["long_in_flight_hold_ms"]),
            causal_crossover=True,
        )
    ]
    bridge = []
    bridge_count = int(continuity["bridge_count"])
    held_count = int(continuity["bridge_hold_count"])
    receipt_count = int(continuity["required_actor_bridge_count"])
    crossover_count = int(continuity["crossover_bridge_count"])
    held_start = bridge_count - held_count
    for index in range(bridge_count):
        bridge.append(
            entry(
                _blue_routed_request_id(f"{seed_prefix}-bridge-{index:05d}"),
                "bridge",
                "blue",
                scheduled_offset_ms=index * int(continuity["cadence_ms"]),
                hold_ms=(int(continuity["bridge_hold_ms"]) if index >= held_start else 0),
                actor_receipt_required=index < receipt_count,
                causal_crossover=index < crossover_count,
                route_switch_deadline_owner=index < crossover_count,
            )
        )
    normal = [
        entry(
            f"{seed_prefix}-normal-{index:05d}",
            "normal",
            "green",
        )
        for index in range(int(continuity["normal_count"]))
    ]
    roles = {
        "canary": canary,
        "causal_hold": causal_hold,
        "bridge": bridge,
        "normal": normal,
    }
    ordered = [
        item for role in ("canary", "causal_hold", "bridge", "normal") for item in roles[role]
    ]
    request_ids = [str(item["request_id"]) for item in ordered]
    if len(request_ids) != int(continuity["logical_request_count"]):
        raise S6BMRuntimeError("s6bm_continuity_partition_count")
    if len(set(request_ids)) != len(request_ids):
        raise S6BMRuntimeError("s6bm_continuity_partition_overlap")
    schedule = [
        {
            "request_id": item["request_id"],
            "scheduled_offset_ms": item["scheduled_offset_ms"],
            "hold_ms": item["hold_ms"],
        }
        for item in bridge
    ]
    receipt_request_ids = [
        str(item["request_id"]) for item in bridge if item["actor_receipt_required"] is True
    ]
    held_request_ids = [str(item["request_id"]) for item in bridge if int(item["hold_ms"]) > 0]
    crossover_request_ids = [
        str(item["request_id"]) for item in bridge if item["causal_crossover"] is True
    ]
    deadline_owner_request_ids = [
        str(item["request_id"])
        for item in bridge
        if item["route_switch_deadline_owner"] is True
    ]
    bridge_subsets = {
        "receipt_required": {
            "request_ids": receipt_request_ids,
            "request_set_sha256": canonical_sha256(receipt_request_ids),
        },
        "held": {
            "request_ids": held_request_ids,
            "request_set_sha256": canonical_sha256(held_request_ids),
        },
        "crossover": {
            "request_ids": crossover_request_ids,
            "request_set_sha256": canonical_sha256(crossover_request_ids),
        },
        "deadline_owner": {
            "request_ids": deadline_owner_request_ids,
            "request_set_sha256": canonical_sha256(deadline_owner_request_ids),
        },
    }
    if (
        len(receipt_request_ids) != receipt_count
        or len(held_request_ids) != held_count
        or len(crossover_request_ids) != crossover_count
        or deadline_owner_request_ids != crossover_request_ids
        or not set(crossover_request_ids).issubset(receipt_request_ids)
        or set(receipt_request_ids) & set(held_request_ids)
    ):
        raise S6BMRuntimeError("s6bm_continuity_bridge_subset_contract")
    core = {
        "schema_version": "evm.s8_v4.s6bm_exact_traffic_plan.v1",
        "contract": continuity["contract"],
        "attempt_id": attempt_id,
        "seed": config.seed,
        "logical_request_count": len(request_ids),
        "role_order": ["canary", "causal_hold", "bridge", "normal"],
        "roles": roles,
        "request_id_set_sha256": canonical_sha256(request_ids),
        "role_partition_sha256": canonical_sha256(roles),
        "schedule_sha256": canonical_sha256(schedule),
        "bridge_subsets": bridge_subsets,
        "adaptive_pacing": False,
        "completion_windowing": "all_exact_logical_ids",
    }
    return {**core, "plan_sha256": canonical_sha256(core)}


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise S6BMRuntimeError("s6bm_latency_empty")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _assert_exact_float(observed: Any, expected: float, name: str) -> None:
    if not math.isclose(_finite(observed, name), expected, rel_tol=0, abs_tol=1e-6):
        raise S6BMRuntimeError(f"s6bm_projection_mismatch:{name}")


def _durable_terminal_monotonic(record: Mapping[str, Any]) -> float:
    receipt = dict(record.get("durable_effect", {}))
    if receipt.get("readback_visible") is not True:
        raise S6BMRuntimeError("s6bm_durable_terminal_readback")
    readback_ns = int(receipt.get("readback_finished_monotonic_ns", 0))
    if readback_ns <= 0:
        raise S6BMRuntimeError("s6bm_durable_terminal_timestamp")
    terminal = readback_ns / 1e9
    if terminal > _finite(record.get("completed_monotonic"), "completed_monotonic"):
        raise S6BMRuntimeError("s6bm_durable_terminal_after_response")
    return terminal


def _validate_model_identities(raw: Mapping[str, Any], config: S6BMConfig) -> None:
    identities = dict(raw.get("identities", {}))
    if identities.get("image_digest") != config.image_digest:
        raise S6BMRuntimeError("s6bm_identity_image")
    if identities.get("repository_sha256") != config.repository_sha256:
        raise S6BMRuntimeError("s6bm_identity_repository")
    for role in ("blue", "green"):
        observed = dict(identities.get(role, {}))
        model = config.blue if role == "blue" else config.green
        expected = {
            "model_name": model.model_name,
            "model_version": model.model_version,
            "artifact_sha256": model.artifact_sha256,
            "config_sha256": model.config_sha256,
        }
        if observed != expected:
            raise S6BMRuntimeError(f"s6bm_identity_model:{role}")
    lease = dict(identities.get("lease", {}))
    if (
        lease.get("scenario_id") != "S6B-M"
        or lease.get("model_family") != "tabular"
        or lease.get("purpose") != "scale_validation_inference"
        or lease.get("owner_exact") is not True
    ):
        raise S6BMRuntimeError("s6bm_identity_lease")


def project_raw_drain_timeline(raw: Mapping[str, Any], config: S6BMConfig) -> dict[str, Any]:
    """Recompute the Blue drain invariant from immutable request and phase records."""
    timeline = [dict(item) for item in raw.get("phase_timeline", [])]
    phases = {
        str(item.get("phase", "")): _finite(item.get("monotonic_seconds"), "phase_monotonic")
        for item in timeline
    }
    if set(phases) != set(SUCCESS_PHASES) or len(phases) != len(SUCCESS_PHASES):
        raise S6BMRuntimeError("s6bm_drain_phase_identity")
    switch = phases["green_active"]
    drain_started = phases["blue_draining"]
    unload_completed = phases["green_only"]
    if not switch < drain_started < unload_completed:
        raise S6BMRuntimeError("s6bm_drain_phase_order")

    blue_records: list[dict[str, Any]] = []
    for raw_record in raw.get("request_records", []):
        record = dict(raw_record)
        if record.get("model_role") != "blue":
            continue
        attempted = _finite(record.get("attempted_monotonic"), "attempted_monotonic")
        completed = _finite(record.get("completed_monotonic"), "completed_monotonic")
        if attempted <= 0 or completed <= attempted:
            raise S6BMRuntimeError("s6bm_drain_request_timing")
        record["attempted_monotonic"] = attempted
        record["completed_monotonic"] = completed
        record["wall_duration_ms"] = (completed - attempted) * 1000.0
        blue_records.append(record)
    if not blue_records:
        raise S6BMRuntimeError("s6bm_drain_blue_records_absent")

    pre_switch = [item for item in blue_records if item["attempted_monotonic"] < switch]
    in_flight_at_switch = [
        item
        for item in pre_switch
        if item["attempted_monotonic"] < switch < item["completed_monotonic"]
    ]
    frozen_hold_ms = float(config.procedure["long_in_flight_hold_ms"])
    hold_records = [
        item for item in in_flight_at_switch if item["wall_duration_ms"] >= frozen_hold_ms
    ]
    if not hold_records:
        raise S6BMRuntimeError("s6bm_drain_hold_request_absent")
    if any(
        item["attempted_monotonic"] >= switch
        or item["completed_monotonic"] <= switch
        or item["attempted_monotonic"] >= drain_started
        or item["completed_monotonic"] <= drain_started
        for item in hold_records
    ):
        raise S6BMRuntimeError("s6bm_drain_hold_switch_order")

    last_blue_completion = max(item["completed_monotonic"] for item in pre_switch)
    in_flight_at_unload_boundary = [
        item
        for item in pre_switch
        if item["attempted_monotonic"] < unload_completed < item["completed_monotonic"]
    ]
    if last_blue_completion >= unload_completed or in_flight_at_unload_boundary:
        raise S6BMRuntimeError("s6bm_drain_unload_before_blue_completion")
    if int(raw.get("blue_in_flight_before_unload", -1)) != len(in_flight_at_unload_boundary):
        raise S6BMRuntimeError("s6bm_drain_summary_projection")

    return {
        "green_active_monotonic": switch,
        "blue_drain_started_monotonic": drain_started,
        "blue_unload_completed_monotonic": unload_completed,
        "pre_switch_blue_request_count": len(pre_switch),
        "blue_in_flight_at_switch": len(in_flight_at_switch),
        "blue_in_flight_at_unload_boundary": len(in_flight_at_unload_boundary),
        "hold_request_count": len(hold_records),
        "hold_request_ids": sorted(str(item["request_id"]) for item in hold_records),
        "last_pre_switch_blue_completion_monotonic": last_blue_completion,
        "max_hold_wall_duration_ms": max(item["wall_duration_ms"] for item in hold_records),
        "drain_wait_after_switch_ms": (last_blue_completion - switch) * 1000.0,
        "frozen_hold_duration_ms": frozen_hold_ms,
    }


def _project_request_records(
    raw: Mapping[str, Any], config: S6BMConfig
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, float]]:
    records = [dict(item) for item in raw.get("request_records", [])]
    if not records:
        raise S6BMRuntimeError("s6bm_request_records_empty")
    request_ids = [str(item.get("request_id", "")) for item in records]
    if len(set(request_ids)) != len(request_ids) or any(not item for item in request_ids):
        raise S6BMRuntimeError("s6bm_request_identity_duplicate")
    if any(
        int(item.get("status_code", 0)) != 200 or item.get("outcome") != "completed"
        for item in records
    ):
        raise S6BMRuntimeError("s6bm_request_not_completed")
    trace_ids = [str(item.get("trace_id", "")) for item in records]
    if len(set(trace_ids)) != len(trace_ids) or any(
        TRACE_ID.fullmatch(item) is None for item in trace_ids
    ):
        raise S6BMRuntimeError("s6bm_trace_identity")
    attempt_id = str(raw.get("attempt_id", ""))
    run_id = str(dict(raw.get("identities", {})).get("lease", {}).get("run_id", ""))
    if not attempt_id or not run_id:
        raise S6BMRuntimeError("s6bm_request_attempt_identity")
    effect_ids: set[str] = set()
    completion_times: list[float] = []
    latencies: list[float] = []
    for record in records:
        if record.get("attempt_id") != attempt_id or record.get("run_id") != run_id:
            raise S6BMRuntimeError("s6bm_request_attempt_identity")
        role = str(record.get("model_role", ""))
        if role not in {"blue", "green"}:
            raise S6BMRuntimeError("s6bm_request_role")
        expected_identity = _expected_identity(config, role)
        if any(record.get(key) != value for key, value in expected_identity.items()):
            raise S6BMRuntimeError("s6bm_request_model_identity")
        if dict(record.get("offered_identity", {})) != expected_identity:
            raise S6BMRuntimeError("s6bm_offered_served_identity")
        traceparent = str(record.get("offered_traceparent", ""))
        trace_parts = traceparent.split("-")
        if (
            len(trace_parts) != 4
            or trace_parts[0] != "00"
            or trace_parts[1] != record.get("trace_id")
            or SPAN_ID.fullmatch(trace_parts[2]) is None
            or trace_parts[3] not in {"00", "01"}
        ):
            raise S6BMRuntimeError("s6bm_traceparent_binding")
        effect_id = str(record.get("effect_id", ""))
        if SHA256.fullmatch(effect_id) is None or effect_id in effect_ids:
            raise S6BMRuntimeError("s6bm_effect_identity")
        effect_ids.add(effect_id)
        expected_output = (
            config.blue.expected_output if role == "blue" else config.green.expected_output
        )
        output = tuple(_finite(item, "request_output") for item in record.get("output", []))
        if len(output) != len(expected_output) or any(
            not math.isclose(observed, expected, rel_tol=0, abs_tol=1e-5)
            for observed, expected in zip(output, expected_output, strict=True)
        ):
            raise S6BMRuntimeError("s6bm_request_output")
        elapsed = _finite(record.get("elapsed_ms"), "elapsed_ms")
        completed = _finite(record.get("completed_monotonic"), "completed_monotonic")
        if elapsed < 0 or completed <= 0:
            raise S6BMRuntimeError("s6bm_request_timing")
        latencies.append(elapsed)
        completion_times.append(
            _durable_terminal_monotonic(record)
            if config.schema_version.endswith(".v4")
            else completed
        )
    completion_times.sort()
    gaps = [
        (current - previous) * 1000
        for previous, current in zip(completion_times, completion_times[1:], strict=False)
    ]
    summary = {
        "logical": len(records),
        "accepted": len(records),
        "terminal": len(records),
        "lost": 0,
        "duplicate_effect": 0,
        "wrong_version": 0,
        "transport_failure": 0,
        "http_5xx": 0,
    }
    latency = {
        "p95_ms": _percentile(latencies, 0.95),
        "p99_ms": _percentile(latencies, 0.99),
        "max_inter_completion_gap_ms": max(gaps, default=0.0),
    }
    return records, summary, latency


def project_continuity_contract(raw: Mapping[str, Any], config: S6BMConfig) -> dict[str, Any]:
    """Recompute exact run cardinality and fixed-cadence transition continuity."""
    if not config.schema_version.endswith(".v4"):
        raise S6BMRuntimeError("s6bm_continuity_requires_v4")
    attempt_id = str(raw.get("attempt_id", ""))
    expected_plan = build_continuity_plan(config, attempt_id)
    observed_plan = dict(raw.get("traffic_plan", {}))
    if observed_plan != expected_plan:
        raise S6BMRuntimeError("s6bm_continuity_plan_binding")

    plan_artifact = dict(raw.get("traffic_plan_artifact", {}))
    expected_artifact_sha = hashlib.sha256(
        (canonical(expected_plan) + "\n").encode("ascii")
    ).hexdigest()
    if (
        plan_artifact.get("sha256") != expected_artifact_sha
        or not str(plan_artifact.get("path", "")).endswith("traffic-plan.json")
        or int(plan_artifact.get("bytes", -1))
        != len((canonical(expected_plan) + "\n").encode("ascii"))
    ):
        raise S6BMRuntimeError("s6bm_continuity_plan_artifact")

    records = [dict(item) for item in raw.get("request_records", [])]
    records_by_id = {str(item.get("request_id", "")): item for item in records}
    ordered_plan_items = [
        dict(item) for role in expected_plan["role_order"] for item in expected_plan["roles"][role]
    ]
    ordered_ids = [str(item["request_id"]) for item in ordered_plan_items]
    if set(records_by_id) != set(ordered_ids) or len(records_by_id) != len(ordered_ids):
        raise S6BMRuntimeError("s6bm_continuity_exact_logical_set")

    causal = dict(raw.get("causal_proof", {}))
    transition = dict(causal.get("route_transition_receipt", {}))
    old_generation = int(transition.get("old_route_generation", 0))
    new_generation = int(transition.get("new_route_generation", 0))
    route_applied_ns = int(transition.get("route_applied_monotonic_ns", 0))
    if old_generation < 1 or new_generation != old_generation + 1 or route_applied_ns <= 0:
        raise S6BMRuntimeError("s6bm_continuity_transition_identity")
    route_applied = route_applied_ns / 1e9

    role_counts: dict[str, int] = {}
    for item in ordered_plan_items:
        request_id = str(item["request_id"])
        record = records_by_id[request_id]
        traffic_role = str(item["traffic_role"])
        role_counts[traffic_role] = role_counts.get(traffic_role, 0) + 1
        if record.get("model_role") != item["expected_model_role"]:
            raise S6BMRuntimeError("s6bm_continuity_role_binding")
        expected_generation = new_generation if traffic_role == "normal" else old_generation
        if int(record.get("route_generation", 0)) != expected_generation:
            raise S6BMRuntimeError("s6bm_continuity_generation_binding")

    execution = dict(raw.get("continuity_execution", {}))
    if execution.get("plan_sha256") != expected_plan["plan_sha256"]:
        raise S6BMRuntimeError("s6bm_continuity_execution_plan")
    plan_frozen = _finite(execution.get("plan_frozen_monotonic"), "plan_frozen")
    initialized = _finite(
        execution.get("controller_initialized_monotonic"), "controller_initialized"
    )
    producer_started = _finite(execution.get("producer_started_monotonic"), "producer_started")
    causal_gate_started = _finite(
        execution.get("causal_gate_started_monotonic"), "causal_gate_started"
    )
    all_submitted = _finite(execution.get("all_submitted_monotonic"), "all_submitted")
    switch_invoked = _finite(execution.get("switch_invoked_monotonic"), "switch_invoked")
    receipt_observed = _finite(
        execution.get("transition_receipt_observed_monotonic"), "receipt_observed"
    )
    producer_finished = _finite(execution.get("producer_finished_monotonic"), "producer_finished")
    if not (
        plan_frozen < initialized <= producer_started <= causal_gate_started < switch_invoked
        and producer_started < all_submitted <= producer_finished
        and switch_invoked < receipt_observed <= producer_finished
    ):
        raise S6BMRuntimeError("s6bm_continuity_lifecycle_order")
    if execution.get("switch_gate_basis") != (
        "all40_schedule_plus_exact39_blue_terminal_plus_exact4x3_receipts_"
        "plus_exact2_pending_crossovers"
    ):
        raise S6BMRuntimeError("s6bm_continuity_switch_gate_basis")
    if not switch_invoked <= route_applied <= receipt_observed:
        raise S6BMRuntimeError("s6bm_continuity_actor_receipt_order")
    deadline_owner_ids = [
        str(item["request_id"])
        for item in expected_plan["roles"]["bridge"]
        if item.get("route_switch_deadline_owner") is True
    ]
    deadline_evidence = dict(execution.get("route_switch_deadline", {}))
    deadline_started_ns = int(deadline_evidence.get("started_monotonic_ns", 0))
    deadline_ns = int(deadline_evidence.get("deadline_monotonic_ns", 0))
    frozen_deadline_ns = int(
        float(config.continuity["route_switch_barrier_timeout_seconds"]) * 1_000_000_000
    )
    if (
        len(deadline_owner_ids) != 1
        or deadline_evidence.get("owner_request_id") != deadline_owner_ids[0]
        or deadline_evidence.get("source")
        != "api_control_plane_designated_crossover_registration"
        or deadline_started_ns <= int(producer_started * 1e9)
        or deadline_ns - deadline_started_ns != frozen_deadline_ns
        or int(execution.get("route_switch_deadline_monotonic_ns", 0)) != deadline_ns
        or int(receipt_observed * 1e9) >= deadline_ns
        or transition.get("route_switch_deadline_owner_request_id")
        != deadline_owner_ids[0]
        or int(transition.get("route_switch_deadline_started_monotonic_ns", 0))
        != deadline_started_ns
        or int(transition.get("route_switch_deadline_monotonic_ns", 0)) != deadline_ns
    ):
        raise S6BMRuntimeError("s6bm_continuity_route_switch_deadline")
    if int(execution.get("blue_in_flight_before_switch", -1)) < int(
        config.continuity["minimum_blue_in_flight_at_switch"]
    ):
        raise S6BMRuntimeError("s6bm_continuity_in_flight")

    bridge_plan = [dict(item) for item in expected_plan["roles"]["bridge"]]
    required_bridge_items = [
        item for item in bridge_plan if item.get("actor_receipt_required") is True
    ]
    required_bridge_ids = [str(item["request_id"]) for item in required_bridge_items]
    held_bridge_ids = [
        str(item["request_id"]) for item in bridge_plan if int(item.get("hold_ms", 0)) > 0
    ]
    crossover_bridge_ids = [
        str(item["request_id"]) for item in bridge_plan if item.get("causal_crossover") is True
    ]
    subset_contract = dict(expected_plan.get("bridge_subsets", {}))
    expected_subset_contract = {
        "receipt_required": {
            "request_ids": required_bridge_ids,
            "request_set_sha256": canonical_sha256(required_bridge_ids),
        },
        "held": {
            "request_ids": held_bridge_ids,
            "request_set_sha256": canonical_sha256(held_bridge_ids),
        },
        "crossover": {
            "request_ids": crossover_bridge_ids,
            "request_set_sha256": canonical_sha256(crossover_bridge_ids),
        },
        "deadline_owner": {
            "request_ids": deadline_owner_ids,
            "request_set_sha256": canonical_sha256(deadline_owner_ids),
        },
    }
    if (
        subset_contract != expected_subset_contract
        or len(held_bridge_ids) != int(config.continuity["bridge_hold_count"])
        or len(crossover_bridge_ids) != int(config.continuity["crossover_bridge_count"])
        or set(required_bridge_ids) & set(held_bridge_ids)
        or not set(crossover_bridge_ids).issubset(required_bridge_ids)
    ):
        raise S6BMRuntimeError("s6bm_continuity_bridge_subset_contract")
    if len(required_bridge_ids) != int(config.continuity["required_actor_bridge_count"]):
        raise S6BMRuntimeError("s6bm_continuity_actor_receipt_count")
    bridge_receipt_gate = dict(execution.get("bridge_actor_receipt_gate", {}))
    expected_bridge_set_sha = canonical_sha256(required_bridge_ids)
    required_event_count = len(required_bridge_ids) * 3
    gate_satisfied = _finite(
        bridge_receipt_gate.get("gate_satisfied_monotonic"),
        "bridge_actor_gate_satisfied",
    )
    receipt_events = [dict(item) for item in bridge_receipt_gate.get("events", [])]
    receipt_event_keys = [
        (str(item.get("request_id", "")), str(item.get("event_type", "")))
        for item in receipt_events
    ]
    receipt_transactions = [str(item.get("transaction_id", "")) for item in receipt_events]
    required_stages = {
        "api_server_handler_entry",
        "controller_entry",
        "triton_backend_compute_entry",
    }
    expected_event_keys = {
        (request_id, stage) for request_id in required_bridge_ids for stage in required_stages
    }
    collector_request_ids = [
        str(item) for item in bridge_receipt_gate.get("collector_request_ids", [])
    ]
    bridge_collectors = [dict(item) for item in execution.get("bridge_triton_start_receipts", [])]
    raw_readback_reference = dict(bridge_receipt_gate.get("raw_readback_export", {}))
    receipt_identity_valid = True
    for item in receipt_events:
        request_id = str(item.get("request_id", ""))
        record = records_by_id.get(request_id, {})
        expected_model = config.blue
        if (
            item.get("attempt_id") != attempt_id
            or item.get("run_id") != record.get("run_id")
            or item.get("request_nonce") != record.get("request_nonce")
            or item.get("trace_id") != record.get("trace_id")
            or item.get("effect_id") != record.get("effect_id")
            or item.get("model_role") != "blue"
            or item.get("model_name") != expected_model.model_name
            or str(item.get("model_version", "")) != expected_model.model_version
            or item.get("artifact_sha256") != expected_model.artifact_sha256
            or int(item.get("route_generation", 0)) != old_generation
            or not str(item.get("actor_identity", ""))
            or len(str(item.get("payload_sha256", ""))) != 64
            or not str(item.get("database_recorded_at", ""))
            or not str(item.get("readback_at", ""))
            or item.get("readback_visible") is not True
            or item.get("readback_source") != "postgresql_attempt_export"
        ):
            receipt_identity_valid = False
            break
    if (
        bridge_receipt_gate.get("schema_version") != "evm.s8_v4.s6bm_bridge_actor_receipt_gate.v1"
        or bridge_receipt_gate.get("attempt_id") != attempt_id
        or int(bridge_receipt_gate.get("route_generation", 0)) != old_generation
        or bridge_receipt_gate.get("required_request_ids") != required_bridge_ids
        or bridge_receipt_gate.get("required_request_set_sha256") != expected_bridge_set_sha
        or int(bridge_receipt_gate.get("required_stage_count", 0)) != 3
        or int(bridge_receipt_gate.get("expected_event_count", -1)) != required_event_count
        or int(bridge_receipt_gate.get("visible_event_count", -1)) != required_event_count
        or len(receipt_event_keys) != len(set(receipt_event_keys))
        or set(receipt_event_keys) != expected_event_keys
        or any(int(item.get("causal_sequence", 0)) <= 0 for item in receipt_events)
        or any(not item for item in receipt_transactions)
        or len(receipt_transactions) != len(set(receipt_transactions))
        or not receipt_identity_valid
        or not str(raw_readback_reference.get("path", ""))
        or len(str(raw_readback_reference.get("sha256", ""))) != 64
        or int(raw_readback_reference.get("bytes", 0)) <= 0
        or int(bridge_receipt_gate.get("raw_readback_event_count", -1)) < required_event_count
        or bridge_receipt_gate.get("selected_event_set_sha256") != canonical_sha256(receipt_events)
        or collector_request_ids != required_bridge_ids
        or bridge_receipt_gate.get("collector_request_set_sha256") != expected_bridge_set_sha
        or [str(item.get("request_id", "")) for item in bridge_collectors] != required_bridge_ids
        or not causal_gate_started <= gate_satisfied < switch_invoked
    ):
        raise S6BMRuntimeError("s6bm_continuity_actor_receipt_gate")
    causal_hold_ids = [str(item["request_id"]) for item in expected_plan["roles"]["causal_hold"]]
    expected_pending_crossover_ids = sorted(causal_hold_ids + crossover_bridge_ids)
    expected_terminal_ids = sorted(
        set(str(item["request_id"]) for item in bridge_plan) - set(crossover_bridge_ids)
    )
    if (
        transition.get("continuity_receipt_request_ids") != required_bridge_ids
        or transition.get("continuity_receipt_request_set_sha256")
        != canonical_sha256(required_bridge_ids)
        or transition.get("continuity_crossover_request_ids") != crossover_bridge_ids
        or transition.get("continuity_crossover_request_set_sha256")
        != canonical_sha256(crossover_bridge_ids)
        or transition.get("pending_crossover_request_ids") != expected_pending_crossover_ids
        or transition.get("pending_crossover_request_set_sha256")
        != canonical_sha256(expected_pending_crossover_ids)
        or transition.get("released_crossover_request_ids") != expected_pending_crossover_ids
        or transition.get("continuity_terminal_request_ids") != expected_terminal_ids
        or transition.get("continuity_terminal_request_set_sha256")
        != canonical_sha256(expected_terminal_ids)
        or transition.get("crossover_release_basis") != "fence_commit_readback_and_route_applied"
    ):
        raise S6BMRuntimeError("s6bm_continuity_crossover_release_binding")
    terminal_gate = dict(execution.get("pre_switch_terminal_gate", {}))
    terminal_gate_records = [dict(item) for item in terminal_gate.get("terminal_records", [])]
    observed_terminal_ids = [str(item.get("request_id", "")) for item in terminal_gate_records]
    online_response_records = [
        dict(item) for item in terminal_gate.get("online_response_records", [])
    ]
    raw_effect_reference = dict(terminal_gate.get("raw_effect_export", {}))
    raw_event_reference = dict(terminal_gate.get("raw_event_export", {}))
    if (
        terminal_gate.get("schema_version") != "evm.s8_v4.s6bm_pre_switch_bridge_terminal_gate.v2"
        or terminal_gate.get("crossover_request_id") != crossover_bridge_ids[0]
        or terminal_gate.get("expected_terminal_request_ids") != expected_terminal_ids
        or terminal_gate.get("expected_terminal_request_set_sha256")
        != canonical_sha256(expected_terminal_ids)
        or int(terminal_gate.get("expected_terminal_count", -1))
        != int(config.continuity["pre_switch_terminal_bridge_count"])
        or terminal_gate.get("observed_terminal_request_ids") != expected_terminal_ids
        or terminal_gate.get("observed_terminal_request_set_sha256")
        != canonical_sha256(expected_terminal_ids)
        or int(terminal_gate.get("observed_terminal_count", -1))
        != int(config.continuity["pre_switch_terminal_bridge_count"])
        or observed_terminal_ids != expected_terminal_ids
        or terminal_gate.get("terminal_records_sha256") != canonical_sha256(terminal_gate_records)
        or [str(item.get("request_id", "")) for item in online_response_records]
        != expected_terminal_ids
        or terminal_gate.get("online_response_records_sha256")
        != canonical_sha256(online_response_records)
        or terminal_gate.get("durable_readback_complete") is not True
        or not str(raw_effect_reference.get("path", ""))
        or len(str(raw_effect_reference.get("sha256", ""))) != 64
        or int(raw_effect_reference.get("bytes", 0)) <= 0
        or not str(raw_event_reference.get("path", ""))
        or len(str(raw_event_reference.get("sha256", ""))) != 64
        or int(raw_event_reference.get("bytes", 0)) <= 0
        or transition.get("continuity_terminal_records_sha256")
        != terminal_gate.get("terminal_records_sha256")
    ):
        raise S6BMRuntimeError("s6bm_continuity_pre_switch_terminal_set")
    terminal_gate_finished = _finite(
        terminal_gate.get("all_non_crossover_terminal_monotonic"),
        "all_non_crossover_terminal_monotonic",
    )
    if not all_submitted <= terminal_gate_finished < switch_invoked:
        raise S6BMRuntimeError("s6bm_continuity_pre_switch_terminal_order")
    pre_switch_state = dict(execution.get("pre_switch_state", {}))
    if (
        int(pre_switch_state.get("generation", 0)) != old_generation
        or pre_switch_state.get("phase") != "canary"
        or int(pre_switch_state.get("blue_in_flight", -1))
        < int(config.continuity["minimum_blue_in_flight_at_switch"])
        or pre_switch_state.get("pending_crossover_request_ids") != expected_pending_crossover_ids
        or int(pre_switch_state.get("pending_crossover_count", -1))
        != int(config.continuity["pending_crossover_count"])
    ):
        raise S6BMRuntimeError("s6bm_continuity_pending_crossover_gate")
    release_monotonic_ns = int(transition.get("crossover_release_monotonic_ns", 0))
    if not (
        int(gate_satisfied * 1e9)
        < int(switch_invoked * 1e9)
        <= route_applied_ns
        <= release_monotonic_ns
        <= int(receipt_observed * 1e9)
    ):
        raise S6BMRuntimeError("s6bm_continuity_crossover_release_order")
    for terminal_record in terminal_gate_records:
        request_id = str(terminal_record["request_id"])
        final_record = records_by_id[request_id]
        durable = dict(final_record.get("durable_effect") or {})
        expected_projection = {
            "attempt_id": final_record.get("attempt_id"),
            "run_id": final_record.get("run_id"),
            "request_id": request_id,
            "trace_id": final_record.get("trace_id"),
            "effect_id": final_record.get("effect_id"),
            "model_role": final_record.get("model_role"),
            "model_name": final_record.get("model_name"),
            "model_version": final_record.get("model_version"),
            "artifact_sha256": final_record.get("artifact_sha256"),
            "route_generation": final_record.get("route_generation"),
            "result_sha256": final_record.get("result_sha256"),
            "terminal_outcome": "completed",
            "entity_state": "completed",
            "idempotency_key": request_id,
            "request_sha256": durable.get("request_sha256"),
            "stored_payload_sha256": durable.get("stored_payload_sha256"),
            "causal_sequence": durable.get("causal_sequence"),
            "causal_transaction_id": durable.get("transaction_id"),
            "causal_payload_sha256": durable.get("causal_payload_sha256"),
        }
        if (
            terminal_record != expected_projection
            or terminal_record.get("model_role") != "blue"
            or int(terminal_record.get("route_generation", 0)) != old_generation
            or _finite(
                final_record.get("completed_monotonic"),
                "terminal_gate_completed",
            )
            > terminal_gate_finished
            or durable.get("readback_visible") is not True
            or int(durable.get("readback_finished_monotonic_ns", 0)) <= 0
            or online_response_records[expected_terminal_ids.index(request_id)] != final_record
        ):
            raise S6BMRuntimeError("s6bm_continuity_pre_switch_terminal_binding")
    dispatches = [dict(item) for item in execution.get("dispatches", [])]
    if [str(item.get("request_id", "")) for item in dispatches] != [
        str(item["request_id"]) for item in bridge_plan
    ]:
        raise S6BMRuntimeError("s6bm_continuity_schedule_identity")
    max_lateness = 0.0
    bridge_cross_switch = 0
    capacity_events: list[tuple[float, int, int]] = []
    for planned, dispatch in zip(bridge_plan, dispatches, strict=True):
        request_id = str(planned["request_id"])
        record = records_by_id[request_id]
        scheduled_offset = int(planned["scheduled_offset_ms"])
        if (
            int(dispatch.get("scheduled_offset_ms", -1)) != scheduled_offset
            or int(dispatch.get("hold_ms", -1)) != int(planned["hold_ms"])
            or dispatch.get("actor_receipt_required")
            != bool(planned.get("actor_receipt_required", False))
            or dispatch.get("causal_crossover") != bool(planned.get("causal_crossover", False))
        ):
            raise S6BMRuntimeError("s6bm_continuity_schedule_identity")
        payload_bytes = int(dispatch.get("payload_bytes", 0))
        if (
            payload_bytes <= 0
            or payload_bytes > int(config.continuity["max_request_payload_bytes"])
            or payload_bytes != int(record.get("offered_payload_bytes", -1))
            or dispatch.get("payload_sha256") != record.get("offered_payload_sha256")
        ):
            raise S6BMRuntimeError("s6bm_continuity_payload_binding")
        attempted = _finite(record.get("attempted_monotonic"), "bridge_attempted")
        completed = _durable_terminal_monotonic(record)
        client_completed = _finite(record.get("completed_monotonic"), "bridge_client_completed")
        if int(planned["hold_ms"]) > 0 and (client_completed - attempted) * 1000.0 < int(
            planned["hold_ms"]
        ):
            raise S6BMRuntimeError("s6bm_continuity_bridge_hold_duration")
        capacity_events.append((attempted, 1, payload_bytes))
        capacity_events.append((client_completed, -1, -payload_bytes))
        if attempted >= route_applied:
            raise S6BMRuntimeError("s6bm_continuity_stale_blue_admission")
        expected_lateness = (attempted - (producer_started + scheduled_offset / 1000.0)) * 1000
        if expected_lateness < -1e-6:
            raise S6BMRuntimeError("s6bm_continuity_schedule_early")
        _assert_exact_float(dispatch.get("attempted_monotonic"), attempted, "bridge_attempted")
        _assert_exact_float(
            dispatch.get("schedule_lateness_ms"), expected_lateness, "schedule_lateness"
        )
        max_lateness = max(max_lateness, expected_lateness)
        if attempted < route_applied < completed:
            bridge_cross_switch += 1
        if request_id in crossover_bridge_ids:
            if not attempted < route_applied < completed:
                raise S6BMRuntimeError("s6bm_continuity_designated_bridge_crossover")
        elif not completed <= terminal_gate_finished < switch_invoked:
            raise S6BMRuntimeError("s6bm_continuity_non_crossover_terminal_order")
    if max_lateness > float(config.continuity["max_schedule_lateness_ms"]):
        raise S6BMRuntimeError("s6bm_continuity_schedule_late")
    if bridge_cross_switch < int(
        config.continuity["minimum_bridge_cross_switch_completions"]
    ) or bridge_cross_switch != len(crossover_bridge_ids):
        raise S6BMRuntimeError("s6bm_continuity_cross_switch_absent")
    active_count = 0
    active_bytes = 0
    recomputed_max_count = 0
    recomputed_max_bytes = 0
    for _timestamp, count_delta, bytes_delta in sorted(
        capacity_events, key=lambda item: (item[0], item[1])
    ):
        active_count += count_delta
        active_bytes += bytes_delta
        if active_count < 0 or active_bytes < 0:
            raise S6BMRuntimeError("s6bm_continuity_capacity_timeline")
        recomputed_max_count = max(recomputed_max_count, active_count)
        recomputed_max_bytes = max(recomputed_max_bytes, active_bytes)
    observed_max_count = int(execution.get("max_reserved_requests_observed", -1))
    observed_max_bytes = int(execution.get("max_reserved_payload_bytes_observed", -1))
    if (
        active_count != 0
        or active_bytes != 0
        or int(execution.get("reserved_requests_at_finish", -1)) != 0
        or int(execution.get("reserved_payload_bytes_at_finish", -1)) != 0
        or not recomputed_max_count
        <= observed_max_count
        <= int(config.continuity["max_in_flight_requests"])
        or not recomputed_max_bytes
        <= observed_max_bytes
        <= int(config.continuity["max_in_flight_payload_bytes"])
    ):
        raise S6BMRuntimeError("s6bm_continuity_capacity_bound")

    terminal_during_transition = sum(
        producer_started <= _durable_terminal_monotonic(item) <= receipt_observed
        for item in records
    )
    if terminal_during_transition < int(
        config.continuity["minimum_transition_terminal_completions"]
    ):
        raise S6BMRuntimeError("s6bm_continuity_terminal_coverage")
    if raw.get("completion_window") is not None or raw.get("excluded_request_ids"):
        raise S6BMRuntimeError("s6bm_continuity_post_hoc_window")
    if execution.get("adaptive_pacing") is not False:
        raise S6BMRuntimeError("s6bm_continuity_adaptive_pacing")

    conservation = {
        "logical_request_ids": len(ordered_ids),
        "offered": len(records),
        "admitted": len(records),
        "terminal": len(records),
        "completed": len(records),
        "duplicate_replay_attempts": 1,
        "client_attempts": len(records) + 1,
        "missing": 0,
        "duplicate": 0,
        "dropped": 0,
        "backpressure_terminal": 0,
        "schedule_late_dispatches": sum(
            _finite(item.get("schedule_lateness_ms"), "schedule_lateness")
            > float(config.continuity["cadence_ms"])
            for item in dispatches
        ),
        "capacity_waited_dispatches": sum(
            _finite(item.get("capacity_wait_ms"), "capacity_wait") > 0.001 for item in dispatches
        ),
        "terminal_during_transition": terminal_during_transition,
        "bridge_cross_switch_completions": bridge_cross_switch,
        "gap_clock": "durable_effect_readback_monotonic_ns",
        "role_counts": role_counts,
    }
    if dict(raw.get("traffic_conservation", {})) != conservation:
        raise S6BMRuntimeError("s6bm_continuity_conservation_projection")
    return {
        "plan_sha256": expected_plan["plan_sha256"],
        "logical_request_count": len(ordered_ids),
        "role_counts": role_counts,
        "max_schedule_lateness_ms": max_lateness,
        "max_in_flight_requests": recomputed_max_count,
        "max_in_flight_payload_bytes": recomputed_max_bytes,
        "terminal_during_transition": terminal_during_transition,
        "bridge_cross_switch_completions": bridge_cross_switch,
        "blue_in_flight_before_switch": int(execution["blue_in_flight_before_switch"]),
        "required_actor_bridge_count": len(required_bridge_ids),
        "required_actor_bridge_request_set_sha256": expected_bridge_set_sha,
        "held_bridge_request_set_sha256": canonical_sha256(held_bridge_ids),
        "crossover_bridge_request_set_sha256": canonical_sha256(crossover_bridge_ids),
        "pre_switch_terminal_request_set_sha256": canonical_sha256(expected_terminal_ids),
        "required_actor_receipt_event_count": required_event_count,
        "old_route_generation": old_generation,
        "new_route_generation": new_generation,
        "passed": True,
    }


def project_success_attempt(raw: Mapping[str, Any], config: S6BMConfig) -> dict[str, Any]:
    if raw.get("profile") != "successful_transition":
        raise S6BMRuntimeError("s6bm_success_profile")
    _validate_model_identities(raw, config)
    timeline = [dict(item) for item in raw.get("phase_timeline", [])]
    phases = [str(item["phase"]) for item in timeline]
    if phases != SUCCESS_PHASES:
        raise S6BMRuntimeError("s6bm_success_phase_order")
    monotonic = [_finite(item.get("monotonic_seconds"), "phase_monotonic") for item in timeline]
    if monotonic != sorted(monotonic) or len(set(monotonic)) != len(monotonic):
        raise S6BMRuntimeError("s6bm_phase_monotonic")
    records, requests, latency = _project_request_records(raw, config)
    continuity_projection = None
    if config.schema_version.endswith(".v4"):
        continuity_projection = project_continuity_contract(raw, config)
    project_raw_drain_timeline(raw, config)
    if dict(raw.get("requests", {})) != requests:
        raise S6BMRuntimeError("s6bm_request_projection")
    total = requests["logical"]
    if total != int(config.procedure["logical_requests_per_transition"]):
        raise S6BMRuntimeError("s6bm_success_request_count")
    replay = dict(raw.get("idempotent_replay", {}))
    replay_record = dict(replay.get("record", {}))
    original = next(
        (item for item in records if item["request_id"] == replay.get("request_id")), None
    )
    if (
        replay.get("replayed") is not True
        or original is None
        or int(replay.get("unique_count_before", 0)) <= 0
        or int(replay.get("unique_count_before", -1)) != int(replay.get("unique_count_after", -2))
        or int(replay.get("unique_count_after", 0)) > total
        or replay_record.get("replayed") is not True
        or any(
            replay_record.get(key) != original.get(key)
            for key in (
                "request_id",
                "trace_id",
                "run_id",
                "attempt_id",
                "effect_id",
                "offered_traceparent",
                "offered_identity",
                "model_role",
                "model_name",
                "model_version",
                "artifact_sha256",
                "output",
            )
        )
    ):
        raise S6BMRuntimeError("s6bm_idempotent_replay")
    if int(raw.get("illegal_owner_overlap", -1)) != 0:
        raise S6BMRuntimeError("s6bm_owner_overlap")
    owner_samples = [dict(item) for item in raw.get("owner_samples", [])]
    if not owner_samples or any(item.get("owner_exact") is not True for item in owner_samples):
        raise S6BMRuntimeError("s6bm_owner_samples")
    if int(raw.get("trace_complete", 0)) != len(records):
        raise S6BMRuntimeError("s6bm_trace_identity")
    if int(raw.get("blue_in_flight_before_unload", 0)) != 0:
        raise S6BMRuntimeError("s6bm_blue_drain")
    if int(raw.get("green_in_flight_before_unload", 0)) != 0:
        raise S6BMRuntimeError("s6bm_green_drain")
    if raw.get("rollback_exact_blue") is not True:
        raise S6BMRuntimeError("s6bm_rollback_identity")
    recorded_latency = dict(raw.get("latency", {}))
    for key, expected in latency.items():
        _assert_exact_float(recorded_latency.get(key), expected, key)
    p99 = _finite(latency.get("p99_ms"), "p99_ms")
    gap = _finite(latency.get("max_inter_completion_gap_ms"), "max_gap")
    if p99 > float(config.procedure["max_transition_p99_ms"]):
        raise S6BMRuntimeError("s6bm_p99_guardrail")
    if gap > float(config.procedure["max_inter_completion_gap_ms"]):
        raise S6BMRuntimeError("s6bm_gap_guardrail")
    physical = dict(raw.get("physical_model_state", {}))
    required_physical = {
        "green_loaded_ready": True,
        "blue_unloaded_not_ready": True,
        "blue_reloaded_ready": True,
        "green_unloaded_not_ready": True,
        "blue_final_ready": True,
    }
    if any(physical.get(key) is not value for key, value in required_physical.items()):
        raise S6BMRuntimeError("s6bm_physical_model_state")
    telemetry = dict(raw.get("telemetry", {}))
    if (
        telemetry.get("api_target_up") is not True
        or telemetry.get("triton_target_up") is not True
        or telemetry.get("trace_correlation_complete") is not True
        or telemetry.get("metric_delta_complete") is not True
    ):
        raise S6BMRuntimeError("s6bm_telemetry")
    cleanup = dict(raw.get("cleanup", {}))
    if not cleanup or not all(value is True for value in cleanup.values()):
        raise S6BMRuntimeError("s6bm_success_cleanup")
    projection = {
        "attempt_id": str(raw["attempt_id"]),
        "repetition": int(raw["repetition"]),
        "logical_requests": total,
        "p95_ms": _finite(latency.get("p95_ms"), "p95_ms"),
        "p99_ms": p99,
        "max_inter_completion_gap_ms": gap,
        "transition_seconds": _finite(raw.get("transition_seconds"), "transition_seconds"),
        "rollback_seconds": _finite(raw.get("rollback_seconds"), "rollback_seconds"),
        "peak_vram_mib": _finite(raw.get("peak_vram_mib"), "peak_vram_mib"),
        "passed": True,
    }
    if continuity_projection is not None:
        projection["continuity"] = continuity_projection
    return projection


def project_fault_attempt(
    raw: Mapping[str, Any], config: S6BMConfig, profile: str
) -> dict[str, Any]:
    if raw.get("profile") != profile:
        raise S6BMRuntimeError(f"s6bm_fault_profile:{profile}")
    _validate_model_identities(raw, config)
    expected_codes = {
        "wrong_digest": "green_digest_mismatch",
        "green_load_failure": "triton_model_control_failed",
        "green_readiness_failure": "green_readiness_rejected",
        "green_canary_failure": "green_canary_rejected",
        "vram_preflight_rejection": "vram_preflight_rejected",
    }
    rejection = dict(raw.get("rejection", {}))
    if (
        int(rejection.get("status_code", 0)) != 409
        or rejection.get("guard_code") != expected_codes[profile]
        or rejection.get("request_sent") is not True
    ):
        raise S6BMRuntimeError(f"s6bm_fault_rejection:{profile}")
    if raw.get("guard_rejected") is not True:
        raise S6BMRuntimeError(f"s6bm_fault_not_rejected:{profile}")
    if raw.get("route_unchanged_blue") is not True:
        raise S6BMRuntimeError(f"s6bm_fault_route_changed:{profile}")
    _require_zero(
        raw,
        ("green_effect_count", "route_switch_count", "http_5xx", "orphan_count"),
        f"fault_{profile}",
    )
    if raw.get("blue_health_after") is not True:
        raise S6BMRuntimeError(f"s6bm_fault_blue_health:{profile}")
    before = dict(raw.get("before_state", {}))
    final = dict(raw.get("final_state", {}))
    for state in (before, final):
        if (
            state.get("phase") != "blue_only"
            or dict(state.get("route_weights", {})) != {"blue": 100, "green": 0}
            or list(state.get("loaded_roles", [])) != ["blue"]
        ):
            raise S6BMRuntimeError(f"s6bm_fault_state:{profile}")
    observation = dict(raw.get("fault_observation", {}))
    if observation.get("injection_observed") is not True:
        raise S6BMRuntimeError(f"s6bm_fault_injection:{profile}")
    if profile == "vram_preflight_rejection":
        if _finite(observation.get("required_vram_mib"), "required_vram_mib") <= _finite(
            observation.get("free_vram_mib"), "free_vram_mib"
        ):
            raise S6BMRuntimeError("s6bm_fault_vram_not_over_capacity")
    if profile == "green_canary_failure" and observation.get("canary_mismatch") is not True:
        raise S6BMRuntimeError("s6bm_fault_canary_not_failed")
    telemetry = dict(raw.get("telemetry", {}))
    if telemetry.get("api_target_up") is not True or telemetry.get("triton_target_up") is not True:
        raise S6BMRuntimeError(f"s6bm_fault_telemetry:{profile}")
    attempt_id = str(raw.get("attempt_id", ""))
    suite_id = str(telemetry.get("suite_id", ""))
    target_labels = [dict(item) for item in telemetry.get("target_labels", [])]
    expected_jobs = {
        str(config.telemetry["prometheus_job_api"]),
        str(config.telemetry["prometheus_job_triton"]),
    }
    if (
        telemetry.get("attempt_id") != attempt_id
        or not suite_id
        or int(telemetry.get("target_count", 0)) != 2
        or len(target_labels) != 2
        or {item.get("job") for item in target_labels} != expected_jobs
        or any(
            item.get("scenario") != "s8-v4-s6bm"
            or item.get("suite_id") != suite_id
            or item.get("attempt_id") != attempt_id
            for item in target_labels
        )
    ):
        raise S6BMRuntimeError(f"s6bm_fault_telemetry_identity:{profile}")
    cleanup = dict(raw.get("cleanup", {}))
    if not cleanup or not all(value is True for value in cleanup.values()):
        raise S6BMRuntimeError(f"s6bm_fault_cleanup:{profile}")
    return {
        "attempt_id": str(raw["attempt_id"]),
        "repetition": int(raw["repetition"]),
        "profile": profile,
        "guard_code": str(raw["guard_code"]),
        "passed": True,
    }


def analyze_attempts(
    raw_attempts: Sequence[Mapping[str, Any]], config: S6BMConfig
) -> dict[str, Any]:
    allowed_profiles = {
        "successful_transition",
        "wrong_digest",
        "green_load_failure",
        "green_readiness_failure",
        "green_canary_failure",
        "vram_preflight_rejection",
    }
    observed_profiles = [str(item.get("profile", "")) for item in raw_attempts]
    if any(profile not in allowed_profiles for profile in observed_profiles):
        raise S6BMRuntimeError("s6bm_profile_out_of_contract")
    attempt_ids = [str(item.get("attempt_id", "")) for item in raw_attempts]
    if any(not attempt_id for attempt_id in attempt_ids) or len(set(attempt_ids)) != len(
        attempt_ids
    ):
        raise S6BMRuntimeError("s6bm_attempt_identity_duplicate")

    def require_repetitions(profile: str, expected: int) -> None:
        observed = [
            int(item.get("repetition", 0))
            for item in raw_attempts
            if item.get("profile") == profile
        ]
        expected_set = set(range(1, expected + 1))
        if len(observed) != expected or set(observed) != expected_set:
            raise S6BMRuntimeError(f"s6bm_repetition_set:{profile}:{observed}")

    repetitions = int(config.procedure["successful_transition_repetitions"])
    fault_repetitions = int(config.procedure["wrong_digest_repetitions"])
    supplemental_repetitions = int(config.procedure["negative_profile_repetitions"])
    require_repetitions("successful_transition", repetitions)
    require_repetitions("wrong_digest", fault_repetitions)
    for profile in (
        "green_load_failure",
        "green_readiness_failure",
        "green_canary_failure",
        "vram_preflight_rejection",
    ):
        require_repetitions(profile, supplemental_repetitions)

    success = [
        project_success_attempt(item, config)
        for item in raw_attempts
        if item.get("profile") == "successful_transition"
    ]
    wrong = [
        project_fault_attempt(item, config, "wrong_digest")
        for item in raw_attempts
        if item.get("profile") == "wrong_digest"
    ]
    supplementary_profiles = (
        "green_load_failure",
        "green_readiness_failure",
        "green_canary_failure",
        "vram_preflight_rejection",
    )
    supplementary = {
        profile: [
            project_fault_attempt(item, config, profile)
            for item in raw_attempts
            if item.get("profile") == profile
        ]
        for profile in supplementary_profiles
    }
    ac = {
        "S6B-M-AC-01": len(success) == repetitions and all(item["passed"] for item in success),
        "S6B-M-AC-02": len(success) == repetitions and all(item["passed"] for item in success),
        "S6B-M-AC-03": len(wrong) == fault_repetitions and all(item["passed"] for item in wrong),
        "S6B-M-AC-04": len(success) == repetitions and all(item["passed"] for item in success),
    }
    supplementary_passed = all(
        len(items) == supplemental_repetitions and all(item["passed"] for item in items)
        for items in supplementary.values()
    )
    return {
        "schema_version": "evm.s8_v4.s6bm_analysis.v1",
        "success_attempts": success,
        "wrong_digest_attempts": wrong,
        "supplementary_fault_attempts": supplementary,
        "acceptance": ac,
        "supplementary_guards_passed": supplementary_passed,
        "evidence_ready": all(ac.values()) and supplementary_passed,
        "claim_boundary": CLAIM_BOUNDARY,
    }
