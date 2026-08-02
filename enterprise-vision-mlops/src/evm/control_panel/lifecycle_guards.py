from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from evm.control_panel.readiness_evaluator import runtime_path
from evm.control_panel.schemas import ContractModel


GuardDecisionState = Literal["pass", "blocked"]
SideEffectState = Literal["reserved", "completed", "failed", "reconciled"]


STAGE_GUARD_AUTHORITIES: dict[str, tuple[str, ...]] = {
    "profile_snapshot": ("D", "E"),
    "data_pipeline": ("E",),
    "model_training": ("D", "E", "C"),
    "model_evaluation": ("E", "C", "B"),
    "artifact_readiness": ("E", "D"),
    "ci_ct_gate": ("E", "D", "C", "B"),
    "approval": ("E", "D", "C", "B"),
    "deployment": ("E", "D", "B"),
    "serving_validation": ("E", "D", "B", "A"),
    "monitoring": ("D", "C", "A"),
}


class ComponentRevision(ContractModel):
    component: str
    expected_revision: str
    observed_revision: str | None = None
    state: Literal["declared", "match", "mismatch", "unavailable"] = "declared"
    reason: str


class LifecycleIdentityEnvelope(ContractModel):
    schema_version: Literal["evm.lifecycle_identity_envelope.v1"] = (
        "evm.lifecycle_identity_envelope.v1"
    )
    lifecycle_series_id: str = Field(min_length=8)
    lifecycle_run_id: str = Field(min_length=8)
    attempt_id: str = Field(min_length=8)
    correlation_id: str = Field(min_length=8)
    created_at: str
    source_commit: str
    source_branch: str | None = None
    dirty_state_digest: str
    profile_id: str
    profile_version: int = Field(ge=1)
    profile_digest: str
    policy_digest: str
    effective_config_digest: str
    source_manifest_uri: str
    source_manifest_sha256: str
    split_manifest_uri: str
    split_manifest_sha256: str
    declared_split_identity: str
    target_environment: str
    target_namespace: str
    target_kind: str = "Deployment"
    target_name: str
    stable_before_identity: str | None = None
    rollback_identity: str | None = None
    component_revision_map_uri: str
    profile_snapshot_sha256: str
    airflow_config_sha256: str
    model_config_sha256: str
    envelope_digest: str = ""


class LifecycleGuardDecision(ContractModel):
    schema_version: Literal["evm.lifecycle_guard_decision.v1"] = (
        "evm.lifecycle_guard_decision.v1"
    )
    decision_id: str
    lifecycle_series_id: str
    lifecycle_run_id: str
    attempt_id: str
    correlation_id: str
    stage_id: str
    transition: str
    authorities: list[str]
    decision: GuardDecisionState
    blockers: list[str] = Field(default_factory=list)
    identity_digest: str
    decided_at: str


class LifecycleGuardState(ContractModel):
    schema_version: Literal["evm.lifecycle_guard_state.v1"] = (
        "evm.lifecycle_guard_state.v1"
    )
    lifecycle_run_id: str
    current_decision: GuardDecisionState = "pass"
    current_stage: str = "profile_snapshot"
    current_authorities: list[str] = Field(default_factory=lambda: ["D", "E"])
    blockers: list[str] = Field(default_factory=list)
    updated_at: str
    decisions: list[LifecycleGuardDecision] = Field(default_factory=list)


class LifecycleSideEffect(ContractModel):
    schema_version: Literal["evm.lifecycle_side_effect.v1"] = (
        "evm.lifecycle_side_effect.v1"
    )
    side_effect_key: str
    lifecycle_series_id: str
    lifecycle_run_id: str
    attempt_id: str
    correlation_id: str
    stage_id: str
    action: str
    action_digest: str
    state: SideEffectState
    runtime_id: str | None = None
    evidence_uri: str | None = None
    reserved_at: str
    updated_at: str


class LifecycleSideEffectLedger(ContractModel):
    schema_version: Literal["evm.lifecycle_side_effect_ledger.v1"] = (
        "evm.lifecycle_side_effect_ledger.v1"
    )
    lifecycle_run_id: str
    entries: list[LifecycleSideEffect] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identity_uniqueness(self):
        keys = [entry.side_effect_key for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("side_effect_key_duplicate")
        if any(entry.lifecycle_run_id != self.lifecycle_run_id for entry in self.entries):
            raise ValueError("side_effect_run_identity_mismatch")
        return self


class LifecycleGuardBlocked(RuntimeError):
    def __init__(self, blockers: list[str]):
        self.blockers = sorted(set(blockers))
        super().__init__(", ".join(self.blockers))


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleGuardBlocked([f"guard_artifact_invalid:{path.name}"]) from exc
    if not isinstance(payload, dict):
        raise LifecycleGuardBlocked([f"guard_artifact_invalid:{path.name}"])
    return payload


def _required_file(uri: str, *, label: str) -> Path:
    path = runtime_path(uri)
    if not path.is_file():
        raise LifecycleGuardBlocked([f"identity_{label}_missing"])
    return path


def seal_lifecycle_guard_artifacts(
    *,
    directory: Path,
    run_id: str,
    profile_id: str,
    profile_version: int,
    profile_digest: str,
    effective_config_digest: str,
    source_commit: str,
    source_branch: str | None,
    profile_snapshot_uri: str,
    airflow_config_uri: str,
    model_config_uri: str,
    dirty_state_digest: str | None = None,
) -> LifecycleIdentityEnvelope:
    profile_path = _required_file(profile_snapshot_uri, label="profile_snapshot")
    airflow_path = _required_file(airflow_config_uri, label="airflow_config")
    model_path = _required_file(model_config_uri, label="model_config")
    profile = read_json(profile_path)
    model = read_json(model_path)
    data = profile.get("data") if isinstance(profile.get("data"), dict) else {}
    gates = profile.get("gates") if isinstance(profile.get("gates"), dict) else {}
    product = model.get("product") if isinstance(model.get("product"), dict) else {}
    source_manifest_uri = str(data.get("source_manifest_uri") or "")
    split_manifest_uri = str(data.get("split_manifest_uri") or "")
    source_manifest = _required_file(source_manifest_uri, label="source_manifest")
    split_manifest = _required_file(split_manifest_uri, label="split_manifest")
    lifecycle_series_id = f"series-{uuid4().hex}"
    attempt_id = f"attempt-{uuid4().hex}"
    correlation_id = f"correlation-{uuid4().hex}"
    revision_map_path = directory / "component_revision_map.json"
    revisions = [
        ComponentRevision(
            component="control_plane",
            expected_revision=source_commit,
            observed_revision=os.getenv("EVM_GIT_COMMIT") or source_commit,
            state=(
                "match"
                if (os.getenv("EVM_GIT_COMMIT") or source_commit) == source_commit
                else "mismatch"
            ),
            reason="Lifecycle control-plane source revision",
        ),
        ComponentRevision(
            component="lifecycle_worker",
            expected_revision=source_commit,
            reason="Must match before executable stage dispatch",
        ),
        ComponentRevision(
            component="kubernetes_observer",
            expected_revision=source_commit,
            reason="Must match before executable stage dispatch",
        ),
    ]
    atomic_write(
        revision_map_path,
        {
            "schema_version": "evm.lifecycle_component_revision_map.v1",
            "lifecycle_run_id": run_id,
            "source_commit": source_commit,
            "components": [item.model_dump(mode="json") for item in revisions],
        },
    )
    envelope = LifecycleIdentityEnvelope(
        lifecycle_series_id=lifecycle_series_id,
        lifecycle_run_id=run_id,
        attempt_id=attempt_id,
        correlation_id=correlation_id,
        created_at=utc_now(),
        source_commit=source_commit,
        source_branch=source_branch,
        dirty_state_digest=dirty_state_digest or canonical_digest({"dirty": False}),
        profile_id=profile_id,
        profile_version=profile_version,
        profile_digest=profile_digest,
        policy_digest=canonical_digest(gates),
        effective_config_digest=effective_config_digest,
        source_manifest_uri=str(source_manifest),
        source_manifest_sha256=file_digest(source_manifest),
        split_manifest_uri=str(split_manifest),
        split_manifest_sha256=file_digest(split_manifest),
        declared_split_identity=str(data.get("split_manifest_sha256") or ""),
        target_environment=str(gates.get("target_environment") or "staging"),
        target_namespace=str(gates.get("target_namespace") or "evm-staging"),
        target_name=str(product.get("target_deployment") or ""),
        component_revision_map_uri=str(revision_map_path),
        profile_snapshot_sha256=file_digest(profile_path),
        airflow_config_sha256=file_digest(airflow_path),
        model_config_sha256=file_digest(model_path),
    )
    envelope.envelope_digest = canonical_digest(
        envelope.model_dump(mode="json", exclude={"envelope_digest"})
    )
    atomic_write(directory / "identity.envelope.json", envelope.model_dump(mode="json"))
    state = LifecycleGuardState(
        lifecycle_run_id=run_id,
        updated_at=envelope.created_at,
        decisions=[
            LifecycleGuardDecision(
                decision_id=f"decision-{uuid4().hex}",
                lifecycle_series_id=lifecycle_series_id,
                lifecycle_run_id=run_id,
                attempt_id=attempt_id,
                correlation_id=correlation_id,
                stage_id="profile_snapshot",
                transition="seal",
                authorities=["D", "E"],
                decision="pass",
                blockers=[],
                identity_digest=envelope.envelope_digest,
                decided_at=envelope.created_at,
            )
        ],
    )
    atomic_write(directory / "guard_state.json", state.model_dump(mode="json"))
    atomic_write(
        directory / "side_effect_ledger.json",
        LifecycleSideEffectLedger(lifecycle_run_id=run_id).model_dump(mode="json"),
    )
    return envelope


def load_identity_envelope(path: Path) -> LifecycleIdentityEnvelope:
    try:
        envelope = LifecycleIdentityEnvelope.model_validate(read_json(path))
    except ValueError as exc:
        raise LifecycleGuardBlocked(["identity_envelope_invalid"]) from exc
    expected = canonical_digest(
        envelope.model_dump(mode="json", exclude={"envelope_digest"})
    )
    if envelope.envelope_digest != expected:
        raise LifecycleGuardBlocked(["identity_envelope_digest_mismatch"])
    return envelope


def validate_lifecycle_identity(
    *,
    envelope_path: Path,
    run_id: str,
    profile_id: str,
    profile_version: int,
    profile_digest: str,
    effective_config_digest: str,
    source_commit: str | None,
    profile_snapshot_uri: str,
    airflow_config_uri: str,
    model_config_uri: str,
    runtime_revisions: dict[str, str | None] | None = None,
    require_runtime_match: bool = False,
) -> tuple[LifecycleIdentityEnvelope, list[str]]:
    envelope = load_identity_envelope(envelope_path)
    blockers: list[str] = []
    expected_values = {
        "lifecycle_run_id": run_id,
        "profile_id": profile_id,
        "profile_version": profile_version,
        "profile_digest": profile_digest,
        "effective_config_digest": effective_config_digest,
        "source_commit": source_commit or "",
    }
    for field, expected in expected_values.items():
        if getattr(envelope, field) != expected:
            blockers.append(f"identity_{field}_mismatch")
    immutable_files = {
        "profile_snapshot": (runtime_path(profile_snapshot_uri), envelope.profile_snapshot_sha256),
        "airflow_config": (runtime_path(airflow_config_uri), envelope.airflow_config_sha256),
        "model_config": (runtime_path(model_config_uri), envelope.model_config_sha256),
        "source_manifest": (runtime_path(envelope.source_manifest_uri), envelope.source_manifest_sha256),
        "split_manifest": (runtime_path(envelope.split_manifest_uri), envelope.split_manifest_sha256),
    }
    for label, (path, expected_digest) in immutable_files.items():
        if not path.is_file():
            blockers.append(f"identity_{label}_missing")
        elif file_digest(path) != expected_digest:
            blockers.append(f"identity_{label}_digest_mismatch")
    if require_runtime_match:
        observations = runtime_revisions or {}
        for component in ("lifecycle_worker", "kubernetes_observer"):
            observed = observations.get(component)
            if not observed:
                blockers.append(f"runtime_revision_unavailable:{component}")
            elif observed != envelope.source_commit:
                blockers.append(f"runtime_revision_mismatch:{component}")
    return envelope, sorted(set(blockers))


def dispatch_lifecycle_guard(
    *,
    directory: Path,
    stage_id: str,
    transition: str,
    run_identity: dict[str, Any],
    runtime_revisions: dict[str, str | None] | None = None,
    require_runtime_match: bool = False,
) -> LifecycleGuardDecision:
    envelope_path = directory / "identity.envelope.json"
    validation_identity = {
        key: run_identity[key]
        for key in (
            "run_id",
            "profile_id",
            "profile_version",
            "profile_digest",
            "effective_config_digest",
            "source_commit",
            "profile_snapshot_uri",
            "airflow_config_uri",
            "model_config_uri",
        )
    }
    try:
        envelope, blockers = validate_lifecycle_identity(
            envelope_path=envelope_path,
            runtime_revisions=runtime_revisions,
            require_runtime_match=require_runtime_match,
            **validation_identity,
        )
    except LifecycleGuardBlocked as exc:
        blockers = exc.blockers
        envelope = None
    authorities = list(STAGE_GUARD_AUTHORITIES.get(stage_id, ("D", "E")))
    decision = LifecycleGuardDecision(
        decision_id=f"decision-{uuid4().hex}",
        lifecycle_series_id=(
            envelope.lifecycle_series_id
            if envelope is not None
            else str(run_identity.get("lifecycle_series_id") or "series-unknown")
        ),
        lifecycle_run_id=str(run_identity["run_id"]),
        attempt_id=(
            envelope.attempt_id
            if envelope is not None
            else str(run_identity.get("attempt_id") or "attempt-unknown")
        ),
        correlation_id=(
            envelope.correlation_id
            if envelope is not None
            else str(run_identity.get("correlation_id") or "correlation-unknown")
        ),
        stage_id=stage_id,
        transition=transition,
        authorities=authorities,
        decision="blocked" if blockers else "pass",
        blockers=blockers,
        identity_digest=(
            envelope.envelope_digest if envelope is not None else "unavailable"
        ),
        decided_at=utc_now(),
    )
    state_path = directory / "guard_state.json"
    try:
        state = LifecycleGuardState.model_validate(read_json(state_path))
    except (LifecycleGuardBlocked, ValueError):
        state = LifecycleGuardState(
            lifecycle_run_id=str(run_identity["run_id"]),
            updated_at=decision.decided_at,
        )
    state.current_decision = decision.decision
    state.current_stage = stage_id
    state.current_authorities = authorities
    state.blockers = blockers
    state.updated_at = decision.decided_at
    state.decisions.append(decision)
    atomic_write(state_path, state.model_dump(mode="json"))
    if blockers:
        raise LifecycleGuardBlocked(blockers)
    return decision


def reserve_side_effect(
    *,
    directory: Path,
    lifecycle_series_id: str,
    run_id: str,
    attempt_id: str,
    correlation_id: str,
    stage_id: str,
    action: str,
    action_payload: object,
) -> tuple[LifecycleSideEffect, bool]:
    action_digest = canonical_digest(action_payload)
    side_effect_key = canonical_digest(
        {
            "lifecycle_series_id": lifecycle_series_id,
            "run_id": run_id,
            "attempt_id": attempt_id,
            "stage_id": stage_id,
            "action": action,
            "action_digest": action_digest,
        }
    )
    path = directory / "side_effect_ledger.json"
    try:
        ledger = LifecycleSideEffectLedger.model_validate(read_json(path))
    except (LifecycleGuardBlocked, ValueError) as exc:
        raise LifecycleGuardBlocked(["side_effect_ledger_invalid"]) from exc
    existing = next(
        (item for item in ledger.entries if item.side_effect_key == side_effect_key),
        None,
    )
    if existing is not None:
        return existing, False
    now = utc_now()
    entry = LifecycleSideEffect(
        side_effect_key=side_effect_key,
        lifecycle_series_id=lifecycle_series_id,
        lifecycle_run_id=run_id,
        attempt_id=attempt_id,
        correlation_id=correlation_id,
        stage_id=stage_id,
        action=action,
        action_digest=action_digest,
        state="reserved",
        reserved_at=now,
        updated_at=now,
    )
    ledger.entries.append(entry)
    atomic_write(path, ledger.model_dump(mode="json"))
    return entry, True


def complete_side_effect(
    *,
    directory: Path,
    side_effect_key: str,
    state: SideEffectState,
    runtime_id: str | None = None,
    evidence_uri: str | None = None,
) -> LifecycleSideEffect:
    path = directory / "side_effect_ledger.json"
    try:
        ledger = LifecycleSideEffectLedger.model_validate(read_json(path))
    except (LifecycleGuardBlocked, ValueError) as exc:
        raise LifecycleGuardBlocked(["side_effect_ledger_invalid"]) from exc
    for index, entry in enumerate(ledger.entries):
        if entry.side_effect_key != side_effect_key:
            continue
        allowed_transitions: dict[SideEffectState, set[SideEffectState]] = {
            "reserved": {"completed", "failed", "reconciled"},
            "reconciled": {"reconciled", "completed", "failed"},
            "completed": {"completed"},
            "failed": {"failed"},
        }
        if state not in allowed_transitions[entry.state]:
            raise LifecycleGuardBlocked(
                [f"side_effect_state_transition_invalid:{entry.state}:{state}"]
            )
        next_runtime_id = runtime_id or entry.runtime_id
        next_evidence_uri = evidence_uri or entry.evidence_uri
        if (
            state == entry.state
            and next_runtime_id == entry.runtime_id
            and next_evidence_uri == entry.evidence_uri
        ):
            return entry
        updated = entry.model_copy(
            update={
                "state": state,
                "runtime_id": next_runtime_id,
                "evidence_uri": next_evidence_uri,
                "updated_at": utc_now(),
            }
        )
        ledger.entries[index] = updated
        atomic_write(path, ledger.model_dump(mode="json"))
        return updated
    raise LifecycleGuardBlocked(["side_effect_reservation_missing"])
