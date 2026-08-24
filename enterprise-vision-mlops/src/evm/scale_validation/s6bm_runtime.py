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
            ports=ports,
            telemetry=telemetry,
            claim_boundary=str(dict(raw["claim_boundary"])["scope"]),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.schema_version != "evm.s8_v4.s6bm_runtime_config.v1":
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

    def public_snapshot(self) -> dict[str, Any]:
        return {
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
            "ports": dict(self.ports),
            "telemetry": dict(self.telemetry),
            "claim_boundary": CLAIM_BOUNDARY,
        }


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
    if len(set(trace_ids)) != len(trace_ids) or any(TRACE_ID.fullmatch(item) is None for item in trace_ids):
        raise S6BMRuntimeError("s6bm_trace_identity")
    completion_times: list[float] = []
    latencies: list[float] = []
    for record in records:
        role = str(record.get("model_role", ""))
        if role not in {"blue", "green"}:
            raise S6BMRuntimeError("s6bm_request_role")
        expected_identity = _expected_identity(config, role)
        if any(record.get(key) != value for key, value in expected_identity.items()):
            raise S6BMRuntimeError("s6bm_request_model_identity")
        expected_output = config.blue.expected_output if role == "blue" else config.green.expected_output
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
        completion_times.append(completed)
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
    if dict(raw.get("requests", {})) != requests:
        raise S6BMRuntimeError("s6bm_request_projection")
    total = requests["logical"]
    if total < int(config.procedure["logical_requests_per_transition"]):
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
        or int(replay.get("unique_count_before", -1))
        != int(replay.get("unique_count_after", -2))
        or int(replay.get("unique_count_after", 0)) > total
        or replay_record.get("replayed") is not True
        or any(
            replay_record.get(key) != original.get(key)
            for key in (
                "request_id",
                "trace_id",
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
    ):
        raise S6BMRuntimeError("s6bm_telemetry")
    cleanup = dict(raw.get("cleanup", {}))
    if not cleanup or not all(value is True for value in cleanup.values()):
        raise S6BMRuntimeError("s6bm_success_cleanup")
    return {
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


def analyze_attempts(raw_attempts: Sequence[Mapping[str, Any]], config: S6BMConfig) -> dict[str, Any]:
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
    repetitions = int(config.procedure["successful_transition_repetitions"])
    fault_repetitions = int(config.procedure["wrong_digest_repetitions"])
    supplemental_repetitions = int(config.procedure["negative_profile_repetitions"])
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
