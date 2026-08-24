from __future__ import annotations

import hashlib
import json
import math
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


def project_success_attempt(raw: Mapping[str, Any], config: S6BMConfig) -> dict[str, Any]:
    if raw.get("profile") != "successful_transition":
        raise S6BMRuntimeError("s6bm_success_profile")
    phases = [str(item["phase"]) for item in raw.get("phase_timeline", [])]
    if phases != SUCCESS_PHASES:
        raise S6BMRuntimeError("s6bm_success_phase_order")
    requests = dict(raw.get("requests", {}))
    total = int(requests.get("logical", 0))
    if total < int(config.procedure["logical_requests_per_transition"]):
        raise S6BMRuntimeError("s6bm_success_request_count")
    if int(requests.get("accepted", -1)) != total or int(requests.get("terminal", -1)) != total:
        raise S6BMRuntimeError("s6bm_success_terminal_identity")
    _require_zero(
        requests,
        ("lost", "duplicate_effect", "wrong_version", "transport_failure", "http_5xx"),
        "success_request",
    )
    if int(raw.get("illegal_owner_overlap", -1)) != 0:
        raise S6BMRuntimeError("s6bm_owner_overlap")
    if int(raw.get("trace_complete", 0)) != total:
        raise S6BMRuntimeError("s6bm_trace_identity")
    if int(raw.get("blue_in_flight_before_unload", 0)) != 0:
        raise S6BMRuntimeError("s6bm_blue_drain")
    if int(raw.get("green_in_flight_before_unload", 0)) != 0:
        raise S6BMRuntimeError("s6bm_green_drain")
    if raw.get("rollback_exact_blue") is not True:
        raise S6BMRuntimeError("s6bm_rollback_identity")
    latency = dict(raw.get("latency", {}))
    p99 = _finite(latency.get("p99_ms"), "p99_ms")
    gap = _finite(latency.get("max_inter_completion_gap_ms"), "max_gap")
    if p99 > float(config.procedure["max_transition_p99_ms"]):
        raise S6BMRuntimeError("s6bm_p99_guardrail")
    if gap > float(config.procedure["max_inter_completion_gap_ms"]):
        raise S6BMRuntimeError("s6bm_gap_guardrail")
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
