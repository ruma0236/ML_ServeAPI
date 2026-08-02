from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evm.operations.failure_evidence import sha256_file
from evm.operations.failure_scenarios import atomic_write_json


SHA256_PATTERN = r"^[a-f0-9]{64}$"
SCHEMA_VERSION = "evm.scenario_b_controlled_replay.v1"
POLICY_SCHEMA_VERSION = "evm.scenario_b_policy.v1"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelIdentity(StrictModel):
    candidate_id: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    model_digest: str = Field(pattern=SHA256_PATTERN)
    image_digest: str = Field(pattern=SHA256_PATTERN)


class QualityMetrics(StrictModel):
    accuracy: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)
    auroc: float = Field(ge=0, le=1)


class CanaryPolicy(StrictModel):
    schema_version: Literal["evm.scenario_b_policy.v1"] = POLICY_SCHEMA_VERSION
    policy_id: str = Field(min_length=1)
    assignment_seed: str = Field(min_length=1)
    min_shadow_requests: int = Field(ge=1)
    total_replay_requests: int = Field(ge=1)
    challenger_requests: int = Field(ge=1)
    max_challenger_fraction: float = Field(gt=0, le=1)
    min_accuracy: float = Field(ge=0, le=1)
    min_f1: float = Field(ge=0, le=1)
    min_auroc: float = Field(ge=0, le=1)
    max_latency_p95_ms: float = Field(gt=0)
    max_error_rate: float = Field(ge=0, le=1)
    stop_budget_seconds: float = Field(gt=0)
    rollback_budget_seconds: float = Field(gt=0)
    signal_precedence: list[Literal["identity", "error_rate", "latency", "quality"]]

    @model_validator(mode="after")
    def validate_bounds(self) -> "CanaryPolicy":
        if self.min_shadow_requests > self.total_replay_requests:
            raise ValueError("shadow sample cannot exceed replay sample")
        if self.challenger_requests > self.total_replay_requests:
            raise ValueError("challenger sample cannot exceed replay sample")
        observed = self.challenger_requests / self.total_replay_requests
        if observed > self.max_challenger_fraction:
            raise ValueError("challenger sample exceeds configured allocation bound")
        if len(self.signal_precedence) != len(set(self.signal_precedence)):
            raise ValueError("signal_precedence must be unique")
        if set(self.signal_precedence) != {"identity", "error_rate", "latency", "quality"}:
            raise ValueError("signal_precedence must cover every guardrail family")
        return self


class ReplayRequest(StrictModel):
    request_id: str = Field(min_length=1)
    content_digest: str = Field(pattern=SHA256_PATTERN)
    image_uri: str = Field(min_length=1)
    expected_label: str = Field(min_length=1)


class InferenceObservation(StrictModel):
    request_id: str = Field(min_length=1)
    model_digest: str = Field(pattern=SHA256_PATTERN)
    latency_ms: float = Field(ge=0)
    succeeded: bool
    prediction: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    failure_code: str | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "InferenceObservation":
        if self.succeeded and self.failure_code:
            raise ValueError("successful observation cannot contain failure_code")
        if not self.succeeded and not self.failure_code:
            raise ValueError("failed observation requires failure_code")
        return self


class ShadowLedgerEntry(StrictModel):
    request_id: str
    stable_model_digest: str = Field(pattern=SHA256_PATTERN)
    challenger_model_digest: str = Field(pattern=SHA256_PATTERN)
    authoritative_model_digest: str = Field(pattern=SHA256_PATTERN)
    stable_succeeded: bool
    challenger_succeeded: bool


class AssignmentLedgerEntry(StrictModel):
    sequence: int = Field(ge=0)
    request_id: str
    assignment_score: str = Field(pattern=SHA256_PATTERN)
    assigned_route: Literal["stable", "challenger"]
    assigned_model_digest: str = Field(pattern=SHA256_PATTERN)
    response_model_digest: str = Field(pattern=SHA256_PATTERN)
    latency_ms: float = Field(ge=0)
    succeeded: bool
    failure_code: str | None = None


class MetricWindow(StrictModel):
    total_requests: int = Field(ge=0)
    challenger_requests: int = Field(ge=0)
    challenger_fraction: float = Field(ge=0, le=1)
    challenger_error_count: int = Field(ge=0)
    challenger_error_rate: float = Field(ge=0, le=1)
    challenger_latency_p95_ms: float = Field(ge=0)
    identity_match_count: int = Field(ge=0)
    identity_match_fraction: float = Field(ge=0, le=1)
    quality: QualityMetrics


class CanaryDecision(StrictModel):
    state: Literal["blocked_admission", "canary_passed", "rolled_back"]
    blocker_codes: list[str]
    challenger_allocation_after: float = Field(ge=0, le=1)
    stop_seconds: float | None = Field(default=None, ge=0)
    signal_precedence: list[str]

    @model_validator(mode="after")
    def validate_state(self) -> "CanaryDecision":
        if self.state == "canary_passed" and self.blocker_codes:
            raise ValueError("passed canary cannot have blockers")
        if self.state != "canary_passed" and not self.blocker_codes:
            raise ValueError("blocked or rolled-back canary requires blockers")
        if self.state != "canary_passed" and self.challenger_allocation_after != 0:
            raise ValueError("failed canary must have zero challenger allocation")
        return self


class RollbackResult(StrictModel):
    action: Literal[
        "stable_route_retained",
        "not_required",
        "zero_allocation_and_restore_stable_route",
    ]
    target_model_digest: str = Field(pattern=SHA256_PATTERN)
    restored_model_digest: str = Field(pattern=SHA256_PATTERN)
    exact_identity_restored: bool
    duration_seconds: float = Field(ge=0)


class ControlledReplayResult(StrictModel):
    schema_version: Literal["evm.scenario_b_controlled_replay.v1"] = SCHEMA_VERSION
    run_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]+$")
    mode: Literal["isolated_controlled_replay"]
    status: Literal["passed", "blocked", "rolled_back"]
    policy: CanaryPolicy
    stable: ModelIdentity
    challenger: ModelIdentity
    replay_manifest_digest: str = Field(pattern=SHA256_PATTERN)
    started_at: datetime
    finished_at: datetime
    production_mutated: Literal[False]
    shadow_ledger: list[ShadowLedgerEntry]
    assignment_ledger: list[AssignmentLedgerEntry]
    metric_window: MetricWindow | None
    decision: CanaryDecision
    rollback: RollbackResult
    limitations: list[str] = Field(min_length=1)

    _validate_started_at = field_validator("started_at")(_utc)
    _validate_finished_at = field_validator("finished_at")(_utc)

    @model_validator(mode="after")
    def validate_result(self) -> "ControlledReplayResult":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at precedes started_at")
        if len(self.shadow_ledger) < self.policy.min_shadow_requests:
            raise ValueError("shadow ledger is below the minimum sample")
        if self.decision.state == "blocked_admission" and self.assignment_ledger:
            raise ValueError("admission-blocked challenger cannot receive canary assignments")
        if self.decision.state != "blocked_admission":
            if len(self.assignment_ledger) != self.policy.total_replay_requests:
                raise ValueError("assignment ledger does not cover the replay window")
            observed = sum(
                item.assigned_route == "challenger" for item in self.assignment_ledger
            )
            if observed != self.policy.challenger_requests:
                raise ValueError("challenger assignment count is not exact")
        if not self.rollback.exact_identity_restored:
            raise ValueError("scenario B result requires exact stable route identity")
        return self


def replay_manifest_digest(requests: list[ReplayRequest]) -> str:
    material = [request.model_dump(mode="json") for request in requests]
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _request_subset(requests: list[ReplayRequest], count: int) -> list[ReplayRequest]:
    if len(requests) < count:
        raise ValueError(f"insufficient_replay_requests:required={count},actual={len(requests)}")
    selected = requests[:count]
    request_ids = [request.request_id for request in selected]
    if len(request_ids) != len(set(request_ids)):
        raise ValueError("duplicate_replay_request_id")
    return selected


def _observation_map(
    observations: list[InferenceObservation],
    *,
    model: ModelIdentity,
) -> dict[str, InferenceObservation]:
    mapped: dict[str, InferenceObservation] = {}
    for observation in observations:
        if observation.request_id in mapped:
            raise ValueError(f"duplicate_observation:{model.candidate_id}:{observation.request_id}")
        if observation.model_digest != model.model_digest:
            raise ValueError(f"observation_model_identity_mismatch:{model.candidate_id}")
        mapped[observation.request_id] = observation
    return mapped


def quality_blockers(policy: CanaryPolicy, quality: QualityMetrics) -> list[str]:
    blockers: list[str] = []
    if quality.accuracy < policy.min_accuracy:
        blockers.append("quality_accuracy_below_minimum")
    if quality.f1 < policy.min_f1:
        blockers.append("quality_f1_below_minimum")
    if quality.auroc < policy.min_auroc:
        blockers.append("quality_auroc_below_minimum")
    return blockers


def build_assignment_routes(
    requests: list[ReplayRequest],
    *,
    policy: CanaryPolicy,
) -> dict[str, tuple[str, str]]:
    selected = _request_subset(requests, policy.total_replay_requests)
    ranked = sorted(
        (
            hashlib.sha256(
                f"{policy.assignment_seed}|{request.request_id}".encode("utf-8")
            ).hexdigest(),
            request.request_id,
        )
        for request in selected
    )
    challenger_ids = {request_id for _, request_id in ranked[: policy.challenger_requests]}
    return {
        request.request_id: (
            "challenger" if request.request_id in challenger_ids else "stable",
            hashlib.sha256(
                f"{policy.assignment_seed}|{request.request_id}".encode("utf-8")
            ).hexdigest(),
        )
        for request in selected
    }


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _ordered_runtime_blockers(
    *,
    policy: CanaryPolicy,
    window: MetricWindow,
) -> list[str]:
    families: dict[str, list[str]] = {
        "identity": [],
        "error_rate": [],
        "latency": [],
        "quality": quality_blockers(policy, window.quality),
    }
    if window.identity_match_fraction != 1:
        families["identity"].append("route_response_identity_mismatch")
    if window.challenger_error_rate > policy.max_error_rate:
        families["error_rate"].append("runtime_error_rate_exceeded")
    if window.challenger_latency_p95_ms > policy.max_latency_p95_ms:
        families["latency"].append("runtime_latency_p95_exceeded")
    return [code for family in policy.signal_precedence for code in families[family]]


def run_controlled_replay(
    *,
    run_id: str,
    policy: CanaryPolicy,
    stable: ModelIdentity,
    challenger: ModelIdentity,
    requests: list[ReplayRequest],
    stable_observations: list[InferenceObservation],
    challenger_observations: list[InferenceObservation],
    challenger_quality: QualityMetrics,
    started_at: datetime | None = None,
    stop_seconds: float = 0.0,
    rollback_seconds: float = 0.0,
) -> ControlledReplayResult:
    observed_started_at = _utc(started_at or datetime.now(timezone.utc))
    selected = _request_subset(requests, policy.total_replay_requests)
    stable_map = _observation_map(stable_observations, model=stable)
    challenger_map = _observation_map(challenger_observations, model=challenger)

    shadow_ledger: list[ShadowLedgerEntry] = []
    for request in selected[: policy.min_shadow_requests]:
        stable_observation = stable_map.get(request.request_id)
        challenger_observation = challenger_map.get(request.request_id)
        if stable_observation is None or challenger_observation is None:
            raise ValueError(f"shadow_observation_missing:{request.request_id}")
        shadow_ledger.append(
            ShadowLedgerEntry(
                request_id=request.request_id,
                stable_model_digest=stable_observation.model_digest,
                challenger_model_digest=challenger_observation.model_digest,
                authoritative_model_digest=stable.model_digest,
                stable_succeeded=stable_observation.succeeded,
                challenger_succeeded=challenger_observation.succeeded,
            )
        )

    admission_blockers = quality_blockers(policy, challenger_quality)
    if admission_blockers:
        finished_at = datetime.now(timezone.utc)
        return ControlledReplayResult(
            run_id=run_id,
            mode="isolated_controlled_replay",
            status="blocked",
            policy=policy,
            stable=stable,
            challenger=challenger,
            replay_manifest_digest=replay_manifest_digest(selected),
            started_at=observed_started_at,
            finished_at=finished_at,
            production_mutated=False,
            shadow_ledger=shadow_ledger,
            assignment_ledger=[],
            metric_window=None,
            decision=CanaryDecision(
                state="blocked_admission",
                blocker_codes=admission_blockers,
                challenger_allocation_after=0,
                stop_seconds=0,
                signal_precedence=list(policy.signal_precedence),
            ),
            rollback=RollbackResult(
                action="stable_route_retained",
                target_model_digest=stable.model_digest,
                restored_model_digest=stable.model_digest,
                exact_identity_restored=True,
                duration_seconds=0,
            ),
            limitations=_limitations(),
        )

    routes = build_assignment_routes(selected, policy=policy)
    assignment_ledger: list[AssignmentLedgerEntry] = []
    for sequence, request in enumerate(selected):
        route, score = routes[request.request_id]
        observation = (
            challenger_map.get(request.request_id)
            if route == "challenger"
            else stable_map.get(request.request_id)
        )
        if observation is None:
            raise ValueError(f"assigned_observation_missing:{route}:{request.request_id}")
        expected_digest = challenger.model_digest if route == "challenger" else stable.model_digest
        assignment_ledger.append(
            AssignmentLedgerEntry(
                sequence=sequence,
                request_id=request.request_id,
                assignment_score=score,
                assigned_route=route,
                assigned_model_digest=expected_digest,
                response_model_digest=observation.model_digest,
                latency_ms=observation.latency_ms,
                succeeded=observation.succeeded,
                failure_code=observation.failure_code,
            )
        )

    challenger_entries = [
        item for item in assignment_ledger if item.assigned_route == "challenger"
    ]
    identity_matches = sum(
        item.assigned_model_digest == item.response_model_digest for item in assignment_ledger
    )
    challenger_errors = sum(not item.succeeded for item in challenger_entries)
    window = MetricWindow(
        total_requests=len(assignment_ledger),
        challenger_requests=len(challenger_entries),
        challenger_fraction=len(challenger_entries) / len(assignment_ledger),
        challenger_error_count=challenger_errors,
        challenger_error_rate=challenger_errors / len(challenger_entries),
        challenger_latency_p95_ms=_p95([item.latency_ms for item in challenger_entries]),
        identity_match_count=identity_matches,
        identity_match_fraction=identity_matches / len(assignment_ledger),
        quality=challenger_quality,
    )
    blockers = _ordered_runtime_blockers(policy=policy, window=window)
    if blockers:
        if stop_seconds > policy.stop_budget_seconds:
            blockers.append("stop_budget_exceeded")
        if rollback_seconds > policy.rollback_budget_seconds:
            blockers.append("rollback_budget_exceeded")
        decision = CanaryDecision(
            state="rolled_back",
            blocker_codes=blockers,
            challenger_allocation_after=0,
            stop_seconds=stop_seconds,
            signal_precedence=list(policy.signal_precedence),
        )
        rollback = RollbackResult(
            action="zero_allocation_and_restore_stable_route",
            target_model_digest=stable.model_digest,
            restored_model_digest=stable.model_digest,
            exact_identity_restored=True,
            duration_seconds=rollback_seconds,
        )
        status: Literal["passed", "blocked", "rolled_back"] = "rolled_back"
    else:
        decision = CanaryDecision(
            state="canary_passed",
            blocker_codes=[],
            challenger_allocation_after=window.challenger_fraction,
            signal_precedence=list(policy.signal_precedence),
        )
        rollback = RollbackResult(
            action="not_required",
            target_model_digest=stable.model_digest,
            restored_model_digest=stable.model_digest,
            exact_identity_restored=True,
            duration_seconds=0,
        )
        status = "passed"

    return ControlledReplayResult(
        run_id=run_id,
        mode="isolated_controlled_replay",
        status=status,
        policy=policy,
        stable=stable,
        challenger=challenger,
        replay_manifest_digest=replay_manifest_digest(selected),
        started_at=observed_started_at,
        finished_at=datetime.now(timezone.utc),
        production_mutated=False,
        shadow_ledger=shadow_ledger,
        assignment_ledger=assignment_ledger,
        metric_window=window,
        decision=decision,
        rollback=rollback,
        limitations=_limitations(),
    )


def _limitations() -> list[str]:
    return [
        "single-node and single-GPU local validation",
        "isolated controlled replay without real-user traffic",
        "no business A/B, high availability, or enterprise SLA claim",
        "Kubernetes production canary is not authorized by this result",
    ]


def _atomic_write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_controlled_replay_evidence(
    *,
    root: Path,
    result: ControlledReplayResult,
    requests: list[ReplayRequest],
    additional_artifacts: dict[str, Path] | None = None,
    canonical_evidence_root: Path | None = None,
) -> Path:
    run_root = root / result.run_id
    selected = _request_subset(requests, result.policy.total_replay_requests)
    artifacts: dict[str, Path] = {
        "contract": run_root / "contract.json",
        "request_manifest": run_root / "request-manifest.jsonl",
        "shadow_ledger": run_root / "shadow-ledger.jsonl",
        "assignment_ledger": run_root / "assignment-ledger.jsonl",
        "metric_windows": run_root / "metric-windows.json",
        "decision": run_root / "decision.json",
        "rollback": run_root / "rollback.json",
        "report_core": run_root / "report-core.json",
    }
    atomic_write_json(
        artifacts["contract"],
        {
            "schema_version": POLICY_SCHEMA_VERSION,
            "run_id": result.run_id,
            "mode": result.mode,
            "policy": result.policy.model_dump(mode="json"),
            "stable": result.stable.model_dump(mode="json"),
            "challenger": result.challenger.model_dump(mode="json"),
            "replay_manifest_digest": result.replay_manifest_digest,
            "production_mutation_authorized": False,
        },
    )
    _atomic_write_jsonl(
        artifacts["request_manifest"],
        [request.model_dump(mode="json") for request in selected],
    )
    _atomic_write_jsonl(
        artifacts["shadow_ledger"],
        [item.model_dump(mode="json") for item in result.shadow_ledger],
    )
    _atomic_write_jsonl(
        artifacts["assignment_ledger"],
        [item.model_dump(mode="json") for item in result.assignment_ledger],
    )
    atomic_write_json(
        artifacts["metric_windows"],
        result.metric_window.model_dump(mode="json") if result.metric_window else {},
    )
    atomic_write_json(artifacts["decision"], result.decision.model_dump(mode="json"))
    atomic_write_json(artifacts["rollback"], result.rollback.model_dump(mode="json"))
    atomic_write_json(artifacts["report_core"], result.model_dump(mode="json"))

    for role, path in (additional_artifacts or {}).items():
        if role in artifacts:
            raise ValueError(f"duplicate_evidence_role:{role}")
        if not path.is_file():
            raise FileNotFoundError(f"additional evidence is missing: {path}")
        artifacts[role] = path

    index_path = run_root / "evidence-index.json"

    def canonical_uri(path: Path) -> str:
        if canonical_evidence_root is None:
            return str(path.resolve())
        relative = path.relative_to(run_root)
        return str((canonical_evidence_root / result.run_id / relative).as_posix())

    index = {
        "schema_version": "evm.scenario_b_evidence_index.v1",
        "run_id": result.run_id,
        "artifacts": [
            {
                "role": role,
                "uri": canonical_uri(path),
                "sha256": sha256_file(path),
            }
            for role, path in sorted(artifacts.items())
        ],
    }
    atomic_write_json(index_path, index)
    return index_path
