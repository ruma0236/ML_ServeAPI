from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evm.scale_validation.v4_ledger import read_events

SCHEMA_VERSION = "evm.s8_v4.x1_runtime_config.v2"
ATTEMPT_SCHEMA_VERSION = "evm.s8_v4.x1_attempt_manifest.v2"
DEPENDENCY_SCHEMA_VERSION = "evm.s8_v4.x1_s6bm_dependency.v1"
CLAIM_BOUNDARY = (
    "Four lightweight governed CUDA model artifacts on one Windows/WSL2 physical "
    "node, one RTX 4080, and one Triton GPU runtime; no production capacity, SLA, "
    "HA/DR, multi-node, multi-GPU, MIG, MPS, or tenant-isolation claim."
)

EXPECTED_MODELS = (
    "higgs_logistic_regression",
    "higgs_gaussian_nb",
    "higgs_tiny_mlp",
    "criteo_dlrm_lite",
)
ACCEPTED_PHASE_COUNTS = {"P2": 3, "P3": 3, "P4": 3, "P5": 30}
NON_CREDIT_PHASES = frozenset({"Q0", "P0", "P1", "CANDIDATE", "PROFILER", "HOT_GUARD"})
NON_CREDIT_SUCCESS_COUNTS = {
    "Q0": 4,
    "P0": 12,
    "P1": 3,
    "CANDIDATE": 1,
    "PROFILER": 3,
    "HOT_GUARD": 1,
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class X1RuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class X1ModelSpec:
    model_id: str
    display_name: str
    dataset: str
    algorithm: str
    cuda_activity_required: bool
    cpu_fallback_allowed: bool

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> X1ModelSpec:
        return cls(
            model_id=str(payload.get("model_id") or ""),
            display_name=str(payload.get("display_name") or ""),
            dataset=str(payload.get("dataset") or ""),
            algorithm=str(payload.get("algorithm") or ""),
            cuda_activity_required=bool(payload.get("cuda_activity_required")),
            cpu_fallback_allowed=bool(payload.get("cpu_fallback_allowed")),
        )


@dataclass(frozen=True)
class X1RuntimeConfig:
    schema_version: str
    method_contract_version: str
    models: tuple[X1ModelSpec, ...]
    accepted_phase_counts: Mapping[str, int]
    p5_point_ids: tuple[str, ...]
    p5_repetitions_per_point: int
    non_credit_phases: frozenset[str]
    non_credit_success_counts: Mapping[str, int]
    p0_repetitions_per_model: int
    p0_total_runs: int
    p1_total_runs: int
    profiler_total_runs: int
    dependency_work_item: str
    dependency_required_status: str
    dependency_required_acceptance_credit: bool
    dependency_tuple_frozen: bool

    @classmethod
    def from_path(cls, path: Path) -> X1RuntimeConfig:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
        execution = dict(payload.get("execution", {}))
        non_credit = dict(payload.get("non_credit", {}))
        dependency = dict(payload.get("dependency", {}))
        config = cls(
            schema_version=str(payload.get("schema_version") or ""),
            method_contract_version=str(payload.get("method_contract_version") or ""),
            models=tuple(X1ModelSpec.from_mapping(item) for item in payload.get("models", [])),
            accepted_phase_counts={
                key: int(value)
                for key, value in dict(execution.get("accepted_phase_counts", {})).items()
            },
            p5_point_ids=tuple(str(item) for item in execution.get("p5_point_ids", [])),
            p5_repetitions_per_point=int(execution.get("p5_repetitions_per_point", 0)),
            non_credit_phases=frozenset(str(item) for item in non_credit.get("phases", [])),
            non_credit_success_counts={
                key: int(value)
                for key, value in dict(non_credit.get("successful_phase_counts", {})).items()
            },
            p0_repetitions_per_model=int(non_credit.get("p0_repetitions_per_model", 0)),
            p0_total_runs=int(non_credit.get("p0_total_runs", 0)),
            p1_total_runs=int(non_credit.get("p1_total_runs", 0)),
            profiler_total_runs=int(non_credit.get("profiler_total_runs", 0)),
            dependency_work_item=str(dependency.get("work_item") or ""),
            dependency_required_status=str(dependency.get("required_status") or ""),
            dependency_required_acceptance_credit=bool(
                dependency.get("required_acceptance_credit")
            ),
            dependency_tuple_frozen=bool(dependency.get("tuple_frozen")),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise X1RuntimeError("x1_config_schema_version")
        if self.method_contract_version != "v2":
            raise X1RuntimeError("x1_method_contract_version")
        if tuple(item.model_id for item in self.models) != EXPECTED_MODELS:
            raise X1RuntimeError("x1_model_set")
        if any(
            not item.cuda_activity_required or item.cpu_fallback_allowed for item in self.models
        ):
            raise X1RuntimeError("x1_model_cuda_contract")
        if dict(self.accepted_phase_counts) != ACCEPTED_PHASE_COUNTS:
            raise X1RuntimeError("x1_accepted_phase_counts")
        if sum(self.accepted_phase_counts.values()) != 39:
            raise X1RuntimeError("x1_accepted_total")
        if len(self.p5_point_ids) != 10 or len(set(self.p5_point_ids)) != 10:
            raise X1RuntimeError("x1_p5_point_set")
        if self.p5_repetitions_per_point != 3:
            raise X1RuntimeError("x1_p5_repetitions")
        if len(self.p5_point_ids) * self.p5_repetitions_per_point != 30:
            raise X1RuntimeError("x1_p5_arithmetic")
        if self.non_credit_phases != NON_CREDIT_PHASES:
            raise X1RuntimeError("x1_non_credit_phase_set")
        if dict(self.non_credit_success_counts) != NON_CREDIT_SUCCESS_COUNTS:
            raise X1RuntimeError("x1_non_credit_success_counts")
        if self.p0_repetitions_per_model != 3 or self.p0_total_runs != 12:
            raise X1RuntimeError("x1_p0_arithmetic")
        if len(self.models) * self.p0_repetitions_per_model != self.p0_total_runs:
            raise X1RuntimeError("x1_p0_model_arithmetic")
        if self.p1_total_runs != 3 or self.profiler_total_runs != 3:
            raise X1RuntimeError("x1_non_credit_repetitions")
        if (
            self.dependency_work_item != "S6B-M"
            or self.dependency_required_status != "verified"
            or not self.dependency_required_acceptance_credit
        ):
            raise X1RuntimeError("x1_dependency_contract")
        if self.dependency_tuple_frozen:
            raise X1RuntimeError("x1_dependency_tuple_prematurely_frozen")

    @property
    def accepted_total_runs(self) -> int:
        return sum(self.accepted_phase_counts.values())


@dataclass(frozen=True)
class X1AttemptRequest:
    attempt_id: str
    phase: str
    run_index: int
    point_id: str
    requested_credit: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dependency_tuple(event: Mapping[str, Any], closure: Mapping[str, Any]) -> dict[str, Any]:
    reference = dict(event.get("verified_closure", {}))
    sign_off = event.get("reviewer_sign_off")
    sign_off_result = sign_off.get("result") if isinstance(sign_off, Mapping) else sign_off
    source = dict(closure.get("source_identity", {}))
    tuple_payload = {
        "schema_version": DEPENDENCY_SCHEMA_VERSION,
        "work_item": str(event.get("work_item") or ""),
        "status": str(event.get("to_status") or ""),
        "acceptance_credit": event.get("acceptance_credit"),
        "reviewer_sign_off": sign_off_result,
        "closure_event_id": str(event.get("event_id") or ""),
        "closure_event_hash": str(event.get("event_hash") or ""),
        "source_git_revision": str(event.get("source_git_revision") or ""),
        "source_tree_sha": str(event.get("source_tree_sha") or ""),
        "public_closure_path": str(reference.get("path") or ""),
        "public_closure_sha256": str(reference.get("sha256") or ""),
        "closure_source_git_revision": str(source.get("handoff_revision") or ""),
        "closure_source_tree_sha": str(source.get("handoff_tree_sha") or ""),
    }
    if (
        tuple_payload["work_item"] != "S6B-M"
        or tuple_payload["status"] != "verified"
        or tuple_payload["acceptance_credit"] is not True
        or tuple_payload["reviewer_sign_off"] != "passed"
        or not HEX64.fullmatch(tuple_payload["closure_event_hash"])
        or not HEX40.fullmatch(tuple_payload["source_git_revision"])
        or not HEX40.fullmatch(tuple_payload["source_tree_sha"])
        or not HEX64.fullmatch(tuple_payload["public_closure_sha256"])
        or tuple_payload["closure_source_git_revision"] != tuple_payload["source_git_revision"]
        or tuple_payload["closure_source_tree_sha"] != tuple_payload["source_tree_sha"]
        or closure.get("schema_version") != "evm.s8_v4.s6bm_verified_closure.v1"
        or closure.get("work_item") not in (None, "S6B-M")
        or closure.get("status") != "verified"
        or closure.get("acceptance_credit") is not True
    ):
        raise X1RuntimeError("x1_s6bm_dependency_tuple")
    return tuple_payload


def resolve_verified_s6bm_dependency(*, project_root: Path, ledger_path: Path) -> dict[str, Any]:
    events = read_events(ledger_path)
    s6bm_events = [item for item in events if item.get("work_item") == "S6B-M"]
    if not s6bm_events:
        raise X1RuntimeError("x1_s6bm_dependency_absent")
    event = s6bm_events[-1]
    if event.get("to_status") != "verified" or event.get("acceptance_credit") is not True:
        raise X1RuntimeError("x1_s6bm_dependency_not_verified")
    reference = dict(event.get("verified_closure", {}))
    relative = str(reference.get("path") or "")
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise X1RuntimeError("x1_s6bm_dependency_closure_path")
    closure_path = project_root / relative
    try:
        closure_path.resolve().relative_to(project_root.resolve())
    except ValueError as exc:
        raise X1RuntimeError("x1_s6bm_dependency_closure_path") from exc
    if not closure_path.is_file() or sha256_file(closure_path) != reference.get("sha256"):
        raise X1RuntimeError("x1_s6bm_dependency_closure_hash")
    try:
        raw_closure = closure_path.read_bytes()
        if b"\r" in raw_closure or not raw_closure.endswith(b"\n"):
            raise X1RuntimeError("x1_s6bm_dependency_closure_canonical_lf")
        closure = json.loads(raw_closure)
    except (OSError, json.JSONDecodeError) as exc:
        raise X1RuntimeError("x1_s6bm_dependency_closure_json") from exc
    return _dependency_tuple(event, closure)


def prepare_x1_attempt(
    request: X1AttemptRequest,
    *,
    config: X1RuntimeConfig,
    project_root: Path,
    ledger_path: Path,
) -> dict[str, Any]:
    if not request.attempt_id or request.run_index < 1 or not request.point_id:
        raise X1RuntimeError("x1_attempt_identity")
    known_phases = set(config.accepted_phase_counts) | set(config.non_credit_phases)
    if request.phase not in known_phases:
        raise X1RuntimeError("x1_attempt_phase")
    accepted_phase = request.phase in config.accepted_phase_counts
    required_credit = "credit" if accepted_phase else "non_credit"
    if request.requested_credit != required_credit:
        raise X1RuntimeError("x1_attempt_credit_class")

    dependency: dict[str, Any] | None = None
    if accepted_phase:
        dependency = resolve_verified_s6bm_dependency(
            project_root=project_root, ledger_path=ledger_path
        )

    return {
        "schema_version": ATTEMPT_SCHEMA_VERSION,
        "attempt_id": request.attempt_id,
        "phase": request.phase,
        "run_index": request.run_index,
        "point_id": request.point_id,
        "credit": required_credit,
        "acceptance_credit": False,
        "execution_authorized": True,
        "dependency": dependency,
        "model_ids": list(EXPECTED_MODELS),
        "claim_boundary": CLAIM_BOUNDARY,
    }
