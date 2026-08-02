from __future__ import annotations

import json
import os
import tomllib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from evm.operations.correlation import (
    REVISION_PATTERN,
    SHA256_PATTERN,
    IncidentRecord,
    StrictModel,
    require_utc,
    stable_digest,
    uuid7,
)
from evm.operations.failure_scenarios import atomic_write_json, exclusive_lock


TargetClass = Literal[
    "production-b0",
    "staging-b7",
    "lifecycle-control",
    "model-release",
    "data-artifact",
]
LeaseState = Literal["active", "released", "expired", "fenced"]


class RecoveryCoordinationPolicy(StrictModel):
    schema_version: Literal["evm.recovery_coordination_policy.v1"] = (
        "evm.recovery_coordination_policy.v1"
    )
    policy_version: str = Field(min_length=1)
    source_revision: str = Field(pattern=REVISION_PATTERN)
    lease_ttl_seconds: int = Field(default=20, gt=0)
    renewal_interval_seconds: int = Field(default=5, gt=0)
    approval_ttl_seconds: int = Field(default=300, gt=0)
    allowed_actions: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_timing(self) -> "RecoveryCoordinationPolicy":
        if self.renewal_interval_seconds >= self.lease_ttl_seconds:
            raise ValueError("renewal interval must be shorter than lease TTL")
        return self


def load_recovery_policy(
    path: Path,
    *,
    source_revision: str | None = None,
) -> RecoveryCoordinationPolicy:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    policy_payload = dict(payload["recovery_coordination"])
    if source_revision:
        policy_payload["source_revision"] = source_revision
    return RecoveryCoordinationPolicy.model_validate(policy_payload)


class ExactRecoveryTarget(StrictModel):
    schema_version: Literal["evm.exact_recovery_target.v1"] = (
        "evm.exact_recovery_target.v1"
    )
    target_class: TargetClass
    identity: dict[str, str] = Field(min_length=1)
    identity_digest: str = Field(pattern=SHA256_PATTERN)
    match_count: int = Field(ge=0)

    @field_validator("identity")
    @classmethod
    def validate_identity(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or not item.strip() for key, item in value.items()):
            raise ValueError("target identity requires non-empty keys and values")
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def validate_digest(self) -> "ExactRecoveryTarget":
        if stable_digest(self.identity) != self.identity_digest:
            raise ValueError("target identity digest mismatch")
        return self


def exact_target(
    target_class: TargetClass,
    identity: dict[str, str],
    *,
    match_count: int = 1,
) -> ExactRecoveryTarget:
    normalized = dict(sorted(identity.items()))
    return ExactRecoveryTarget(
        target_class=target_class,
        identity=normalized,
        identity_digest=stable_digest(normalized),
        match_count=match_count,
    )


class LeaseAcquireRequest(StrictModel):
    incident_id: str = Field(min_length=8)
    correlation_id: str = Field(min_length=8)
    target: ExactRecoveryTarget
    owner_id: str = Field(min_length=3)
    source_revision: str = Field(pattern=REVISION_PATTERN)
    policy_version: str = Field(min_length=1)
    observed_at_utc: datetime
    evidence_fresh_until_utc: datetime

    _validate_observed = field_validator("observed_at_utc")(require_utc)
    _validate_fresh = field_validator("evidence_fresh_until_utc")(require_utc)


class RecoveryLease(StrictModel):
    schema_version: Literal["evm.recovery_lease.v1"] = "evm.recovery_lease.v1"
    lease_id: str = Field(min_length=8)
    incident_id: str = Field(min_length=8)
    correlation_id: str = Field(min_length=8)
    target: ExactRecoveryTarget
    owner_id: str = Field(min_length=3)
    fencing_token: int = Field(ge=1)
    state: LeaseState
    source_revision: str = Field(pattern=REVISION_PATTERN)
    policy_version: str = Field(min_length=1)
    acquired_at_utc: datetime
    renewed_at_utc: datetime
    expires_at_utc: datetime

    _validate_acquired = field_validator("acquired_at_utc")(require_utc)
    _validate_renewed = field_validator("renewed_at_utc")(require_utc)
    _validate_expires = field_validator("expires_at_utc")(require_utc)


class LeaseDecision(StrictModel):
    schema_version: Literal["evm.recovery_lease_decision.v1"] = (
        "evm.recovery_lease_decision.v1"
    )
    result: Literal["acquired", "deduped", "renewed", "released", "blocked"]
    admitted: bool
    lease: RecoveryLease | None = None
    blockers: list[str] = Field(default_factory=list)
    decided_at_utc: datetime
    mutation_intent_count: Literal[0] = 0

    _validate_decided = field_validator("decided_at_utc")(require_utc)


class RecoveryApproval(StrictModel):
    schema_version: Literal["evm.recovery_approval.v1"] = "evm.recovery_approval.v1"
    approval_id: str = Field(min_length=8)
    incident_id: str = Field(min_length=8)
    correlation_id: str = Field(min_length=8)
    target_identity_digest: str = Field(pattern=SHA256_PATTERN)
    action_digest: str = Field(pattern=SHA256_PATTERN)
    source_revision: str = Field(pattern=REVISION_PATTERN)
    policy_version: str = Field(min_length=1)
    actor: str = Field(min_length=3)
    nonce: str = Field(min_length=8)
    issued_at_utc: datetime
    expires_at_utc: datetime
    binding_digest: str = Field(pattern=SHA256_PATTERN)
    single_use: Literal[True] = True

    _validate_issued = field_validator("issued_at_utc")(require_utc)
    _validate_expires = field_validator("expires_at_utc")(require_utc)

    @model_validator(mode="after")
    def validate_binding(self) -> "RecoveryApproval":
        if self.expires_at_utc <= self.issued_at_utc:
            raise ValueError("approval expiry must follow issue time")
        if stable_digest(approval_binding_payload(self)) != self.binding_digest:
            raise ValueError("approval binding digest mismatch")
        return self


def approval_binding_payload(approval: RecoveryApproval) -> dict[str, Any]:
    return {
        "approval_id": approval.approval_id,
        "incident_id": approval.incident_id,
        "correlation_id": approval.correlation_id,
        "target_identity_digest": approval.target_identity_digest,
        "action_digest": approval.action_digest,
        "source_revision": approval.source_revision,
        "policy_version": approval.policy_version,
        "actor": approval.actor,
        "nonce": approval.nonce,
        "issued_at_utc": approval.issued_at_utc,
        "expires_at_utc": approval.expires_at_utc,
        "single_use": approval.single_use,
    }


def recovery_approval(
    *,
    approval_id: str,
    incident_id: str,
    correlation_id: str,
    target_identity_digest: str,
    action_digest: str,
    source_revision: str,
    policy_version: str,
    actor: str,
    nonce: str,
    issued_at_utc: datetime,
    expires_at_utc: datetime,
) -> RecoveryApproval:
    payload = {
        "approval_id": approval_id,
        "incident_id": incident_id,
        "correlation_id": correlation_id,
        "target_identity_digest": target_identity_digest,
        "action_digest": action_digest,
        "source_revision": source_revision,
        "policy_version": policy_version,
        "actor": actor,
        "nonce": nonce,
        "issued_at_utc": issued_at_utc,
        "expires_at_utc": expires_at_utc,
        "single_use": True,
    }
    return RecoveryApproval(**payload, binding_digest=stable_digest(payload))


class ApprovalDecision(StrictModel):
    schema_version: Literal["evm.recovery_approval_decision.v1"] = (
        "evm.recovery_approval_decision.v1"
    )
    result: Literal["recorded", "deduped", "blocked"]
    admitted: bool
    approval_id: str
    blockers: list[str] = Field(default_factory=list)
    decided_at_utc: datetime
    mutation_intent_count: Literal[0] = 0

    _validate_decided = field_validator("decided_at_utc")(require_utc)


class RecoveryActionRequest(StrictModel):
    incident_id: str = Field(min_length=8)
    correlation_id: str = Field(min_length=8)
    target: ExactRecoveryTarget
    owner_id: str = Field(min_length=3)
    lease_id: str = Field(min_length=8)
    fencing_token: int = Field(ge=1)
    action: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    action_digest: str = Field(pattern=SHA256_PATTERN)
    approval_id: str = Field(min_length=8)
    source_revision: str = Field(pattern=REVISION_PATTERN)
    policy_version: str = Field(min_length=1)
    observed_at_utc: datetime

    _validate_observed = field_validator("observed_at_utc")(require_utc)

    @model_validator(mode="after")
    def validate_action_digest(self) -> "RecoveryActionRequest":
        expected = stable_digest({"action": self.action, "parameters": self.parameters})
        if expected != self.action_digest:
            raise ValueError("action digest mismatch")
        return self


def action_digest(action: str, parameters: dict[str, Any] | None = None) -> str:
    return stable_digest({"action": action, "parameters": parameters or {}})


class RecoveryActionRecord(StrictModel):
    schema_version: Literal["evm.recovery_action_record.v1"] = (
        "evm.recovery_action_record.v1"
    )
    action_key: str = Field(pattern=SHA256_PATTERN)
    incident_id: str = Field(min_length=8)
    correlation_id: str = Field(min_length=8)
    target_class: TargetClass
    target_identity_digest: str = Field(pattern=SHA256_PATTERN)
    owner_id: str = Field(min_length=3)
    lease_id: str = Field(min_length=8)
    fencing_token: int = Field(ge=1)
    action: str = Field(min_length=1)
    action_digest: str = Field(pattern=SHA256_PATTERN)
    approval_id: str = Field(min_length=8)
    source_revision: str = Field(pattern=REVISION_PATTERN)
    policy_version: str = Field(min_length=1)
    state: Literal["authorized_recommendation"] = "authorized_recommendation"
    recorded_at_utc: datetime
    external_mutation_dispatched: Literal[False] = False

    _validate_recorded = field_validator("recorded_at_utc")(require_utc)


class RecoveryActionDecision(StrictModel):
    schema_version: Literal["evm.recovery_action_decision.v1"] = (
        "evm.recovery_action_decision.v1"
    )
    result: Literal["authorized", "deduped", "blocked"]
    admitted: bool
    action: RecoveryActionRecord | None = None
    blockers: list[str] = Field(default_factory=list)
    decided_at_utc: datetime
    recommendation_count: int = Field(ge=0, le=1)
    mutation_intent_count: Literal[0] = 0

    _validate_decided = field_validator("decided_at_utc")(require_utc)


class RecoveryCoordinationState(StrictModel):
    schema_version: Literal["evm.recovery_coordination_store.v1"] = (
        "evm.recovery_coordination_store.v1"
    )
    target_fences: dict[str, int] = Field(default_factory=dict)
    active_lease_index: dict[str, str] = Field(default_factory=dict)
    leases: dict[str, RecoveryLease] = Field(default_factory=dict)
    approvals: dict[str, RecoveryApproval] = Field(default_factory=dict)
    nonce_index: dict[str, str] = Field(default_factory=dict)
    consumed_nonce_index: dict[str, str] = Field(default_factory=dict)
    actions: dict[str, RecoveryActionRecord] = Field(default_factory=dict)
    blocked_decision_count: int = Field(default=0, ge=0)
    last_blockers: list[str] = Field(default_factory=list)


class IncidentTiming(StrictModel):
    collection_delay_ms: float | None = Field(default=None, ge=0)
    correlation_overhead_ms: float | None = Field(default=None, ge=0)
    containment_seconds: float | None = Field(default=None, ge=0)
    recovery_seconds: float | None = Field(default=None, ge=0)


class IncidentPlaneRecord(StrictModel):
    incident_id: str = Field(min_length=8)
    correlation_id: str = Field(min_length=8)
    state: str = Field(min_length=1)
    root_fingerprint: str = Field(pattern=SHA256_PATTERN)
    event_count: int = Field(ge=1)
    causal_edge_count: int = Field(ge=0)
    blockers: list[str] = Field(default_factory=list)
    target_class: TargetClass | None = None
    target_identity_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    owner_id: str | None = None
    fencing_token: int | None = Field(default=None, ge=1)
    lease_expires_at_utc: datetime | None = None
    authorized_recommendation_count: int = Field(default=0, ge=0)
    timing: IncidentTiming = Field(default_factory=IncidentTiming)
    child_evidence_uris: list[str] = Field(default_factory=list)
    created_at_utc: datetime
    updated_at_utc: datetime

    _validate_lease_expiry = field_validator("lease_expires_at_utc")(
        lambda value: require_utc(value) if value else value
    )
    _validate_created = field_validator("created_at_utc")(require_utc)
    _validate_updated = field_validator("updated_at_utc")(require_utc)


class IncidentPlaneSnapshot(StrictModel):
    schema_version: Literal["evm.guard_incident_plane.v1"] = (
        "evm.guard_incident_plane.v1"
    )
    status: Literal["live", "stale", "unavailable"]
    generated_at_utc: datetime
    source_revision: str = Field(pattern=REVISION_PATTERN)
    policy_version: str = Field(min_length=1)
    mutation_endpoint_available: Literal[False] = False
    incidents: list[IncidentPlaneRecord] = Field(default_factory=list)
    leases: list[RecoveryLease] = Field(default_factory=list)
    actions: list[RecoveryActionRecord] = Field(default_factory=list)
    blocked_decision_count: int = Field(default=0, ge=0)
    active_blockers: list[str] = Field(default_factory=list)
    evidence_root: str | None = None

    _validate_generated = field_validator("generated_at_utc")(require_utc)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


class RecoveryCoordinationStore:
    def __init__(self, root: Path, policy: RecoveryCoordinationPolicy) -> None:
        self.root = root
        self.policy = policy
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "coordination-state.json"
        self.lock_path = self.root / ".coordination.lock"

    def snapshot(self) -> RecoveryCoordinationState:
        if not self.state_path.is_file():
            return RecoveryCoordinationState()
        return RecoveryCoordinationState.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )

    def _write_state(self, state: RecoveryCoordinationState) -> None:
        atomic_write_json(self.state_path, state.model_dump(mode="json"))

    def _base_blockers(
        self,
        *,
        target: ExactRecoveryTarget,
        source_revision: str,
        policy_version: str,
        observed_at: datetime,
        fresh_until: datetime | None = None,
    ) -> list[str]:
        blockers: list[str] = []
        if target.match_count == 0:
            blockers.append("target_missing")
        elif target.match_count != 1:
            blockers.append("target_ambiguous")
        if source_revision != self.policy.source_revision:
            blockers.append("revision_mismatch")
        if policy_version != self.policy.policy_version:
            blockers.append("policy_mismatch")
        if fresh_until is not None and fresh_until <= observed_at:
            blockers.append("evidence_stale")
        return blockers

    def _blocked_lease(
        self,
        state: RecoveryCoordinationState,
        blockers: list[str],
        now: datetime,
        request: LeaseAcquireRequest,
    ) -> LeaseDecision:
        state.blocked_decision_count += 1
        state.last_blockers = sorted(set(blockers))
        self._write_state(state)
        decision = LeaseDecision(
            result="blocked",
            admitted=False,
            blockers=state.last_blockers,
            decided_at_utc=now,
        )
        _append_jsonl(
            self.root / "owner-ledger.jsonl",
            {
                **decision.model_dump(mode="json"),
                "incident_id": request.incident_id,
                "target_identity_digest": request.target.identity_digest,
                "owner_id": request.owner_id,
            },
        )
        return decision

    def acquire(self, request: LeaseAcquireRequest) -> LeaseDecision:
        now = require_utc(request.observed_at_utc)
        with exclusive_lock(self.lock_path, timeout_seconds=30):
            state = self.snapshot()
            blockers = self._base_blockers(
                target=request.target,
                source_revision=request.source_revision,
                policy_version=request.policy_version,
                observed_at=now,
                fresh_until=request.evidence_fresh_until_utc,
            )
            if blockers:
                return self._blocked_lease(state, blockers, now, request)

            target_digest = request.target.identity_digest
            active_id = state.active_lease_index.get(target_digest)
            active = state.leases.get(active_id or "")
            if active and active.state == "active" and active.expires_at_utc <= now:
                active = active.model_copy(update={"state": "expired"})
                state.leases[active.lease_id] = active
                state.active_lease_index.pop(target_digest, None)
                active = None

            if active and active.state == "active":
                if (
                    active.owner_id == request.owner_id
                    and active.incident_id == request.incident_id
                    and active.correlation_id == request.correlation_id
                ):
                    return LeaseDecision(
                        result="deduped",
                        admitted=True,
                        lease=active,
                        decided_at_utc=now,
                    )
                return self._blocked_lease(state, ["owner_conflict"], now, request)

            fencing_token = state.target_fences.get(target_digest, 0) + 1
            lease = RecoveryLease(
                lease_id=f"lease-{uuid7(now)}",
                incident_id=request.incident_id,
                correlation_id=request.correlation_id,
                target=request.target,
                owner_id=request.owner_id,
                fencing_token=fencing_token,
                state="active",
                source_revision=request.source_revision,
                policy_version=request.policy_version,
                acquired_at_utc=now,
                renewed_at_utc=now,
                expires_at_utc=now + timedelta(seconds=self.policy.lease_ttl_seconds),
            )
            state.target_fences[target_digest] = fencing_token
            state.active_lease_index[target_digest] = lease.lease_id
            state.leases[lease.lease_id] = lease
            state.last_blockers = []
            self._write_state(state)
            decision = LeaseDecision(
                result="acquired",
                admitted=True,
                lease=lease,
                decided_at_utc=now,
            )
            _append_jsonl(self.root / "owner-ledger.jsonl", decision.model_dump(mode="json"))
            return decision

    def renew(
        self,
        lease_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        observed_at_utc: datetime,
    ) -> LeaseDecision:
        now = require_utc(observed_at_utc)
        with exclusive_lock(self.lock_path, timeout_seconds=30):
            state = self.snapshot()
            lease = state.leases.get(lease_id)
            blockers: list[str] = []
            if lease is None:
                blockers.append("lease_missing")
            elif lease.state != "active":
                blockers.append("lease_inactive")
            else:
                if lease.owner_id != owner_id:
                    blockers.append("owner_mismatch")
                if lease.fencing_token != fencing_token:
                    blockers.append("fence_mismatch")
                if lease.expires_at_utc <= now:
                    blockers.append("lease_expired")
                if state.active_lease_index.get(lease.target.identity_digest) != lease_id:
                    blockers.append("lease_fenced")
            if blockers:
                state.blocked_decision_count += 1
                state.last_blockers = sorted(set(blockers))
                self._write_state(state)
                decision = LeaseDecision(
                    result="blocked",
                    admitted=False,
                    lease=lease,
                    blockers=state.last_blockers,
                    decided_at_utc=now,
                )
                _append_jsonl(self.root / "owner-ledger.jsonl", decision.model_dump(mode="json"))
                return decision
            assert lease is not None
            renewed = lease.model_copy(
                update={
                    "renewed_at_utc": now,
                    "expires_at_utc": now
                    + timedelta(seconds=self.policy.lease_ttl_seconds),
                }
            )
            state.leases[lease_id] = renewed
            state.last_blockers = []
            self._write_state(state)
            decision = LeaseDecision(
                result="renewed",
                admitted=True,
                lease=renewed,
                decided_at_utc=now,
            )
            _append_jsonl(self.root / "owner-ledger.jsonl", decision.model_dump(mode="json"))
            return decision

    def release(
        self,
        lease_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        observed_at_utc: datetime,
    ) -> LeaseDecision:
        now = require_utc(observed_at_utc)
        with exclusive_lock(self.lock_path, timeout_seconds=30):
            state = self.snapshot()
            lease = state.leases.get(lease_id)
            blockers: list[str] = []
            if lease is None:
                blockers.append("lease_missing")
            elif lease.owner_id != owner_id:
                blockers.append("owner_mismatch")
            elif lease.fencing_token != fencing_token:
                blockers.append("fence_mismatch")
            elif lease.state != "active":
                blockers.append("lease_inactive")
            if blockers:
                state.blocked_decision_count += 1
                state.last_blockers = sorted(set(blockers))
                self._write_state(state)
                decision = LeaseDecision(
                    result="blocked",
                    admitted=False,
                    lease=lease,
                    blockers=state.last_blockers,
                    decided_at_utc=now,
                )
                _append_jsonl(self.root / "owner-ledger.jsonl", decision.model_dump(mode="json"))
                return decision
            assert lease is not None
            released = lease.model_copy(update={"state": "released"})
            state.leases[lease_id] = released
            if state.active_lease_index.get(lease.target.identity_digest) == lease_id:
                state.active_lease_index.pop(lease.target.identity_digest, None)
            state.last_blockers = []
            self._write_state(state)
            decision = LeaseDecision(
                result="released",
                admitted=True,
                lease=released,
                decided_at_utc=now,
            )
            _append_jsonl(self.root / "owner-ledger.jsonl", decision.model_dump(mode="json"))
            return decision

    def record_approval(
        self,
        approval: RecoveryApproval,
        *,
        observed_at_utc: datetime,
    ) -> ApprovalDecision:
        now = require_utc(observed_at_utc)
        with exclusive_lock(self.lock_path, timeout_seconds=30):
            state = self.snapshot()
            blockers: list[str] = []
            if approval.source_revision != self.policy.source_revision:
                blockers.append("revision_mismatch")
            if approval.policy_version != self.policy.policy_version:
                blockers.append("policy_mismatch")
            if approval.expires_at_utc <= now:
                blockers.append("approval_expired")
            if approval.expires_at_utc - approval.issued_at_utc > timedelta(
                seconds=self.policy.approval_ttl_seconds
            ):
                blockers.append("approval_ttl_exceeded")
            existing_approval_id = state.nonce_index.get(approval.nonce)
            if existing_approval_id:
                existing = state.approvals[existing_approval_id]
                if existing.binding_digest == approval.binding_digest:
                    return ApprovalDecision(
                        result="deduped",
                        admitted=True,
                        approval_id=existing.approval_id,
                        decided_at_utc=now,
                    )
                blockers.append("approval_nonce_conflict")
            if blockers:
                state.blocked_decision_count += 1
                state.last_blockers = sorted(set(blockers))
                self._write_state(state)
                decision = ApprovalDecision(
                    result="blocked",
                    admitted=False,
                    approval_id=approval.approval_id,
                    blockers=state.last_blockers,
                    decided_at_utc=now,
                )
                _append_jsonl(
                    self.root / "approval-ledger.jsonl",
                    decision.model_dump(mode="json"),
                )
                return decision
            state.approvals[approval.approval_id] = approval
            state.nonce_index[approval.nonce] = approval.approval_id
            state.last_blockers = []
            self._write_state(state)
            decision = ApprovalDecision(
                result="recorded",
                admitted=True,
                approval_id=approval.approval_id,
                decided_at_utc=now,
            )
            _append_jsonl(
                self.root / "approval-ledger.jsonl",
                {**approval.model_dump(mode="json"), **decision.model_dump(mode="json")},
            )
            return decision

    def authorize(self, request: RecoveryActionRequest) -> RecoveryActionDecision:
        now = require_utc(request.observed_at_utc)
        action_key = stable_digest(
            {
                "incident_id": request.incident_id,
                "target_identity_digest": request.target.identity_digest,
                "fencing_token": request.fencing_token,
                "action_digest": request.action_digest,
                "source_revision": request.source_revision,
                "policy_version": request.policy_version,
            }
        )
        with exclusive_lock(self.lock_path, timeout_seconds=30):
            state = self.snapshot()
            existing = state.actions.get(action_key)
            if existing is not None:
                return RecoveryActionDecision(
                    result="deduped",
                    admitted=True,
                    action=existing,
                    decided_at_utc=now,
                    recommendation_count=1,
                )

            blockers = self._base_blockers(
                target=request.target,
                source_revision=request.source_revision,
                policy_version=request.policy_version,
                observed_at=now,
            )
            if request.action not in self.policy.allowed_actions:
                blockers.append("action_not_allowed")

            lease = state.leases.get(request.lease_id)
            if lease is None:
                blockers.append("lease_missing")
            else:
                if lease.state != "active":
                    blockers.append("lease_inactive")
                if lease.expires_at_utc <= now:
                    blockers.append("lease_expired")
                if lease.owner_id != request.owner_id:
                    blockers.append("owner_mismatch")
                if lease.fencing_token != request.fencing_token:
                    blockers.append("fence_mismatch")
                if lease.incident_id != request.incident_id:
                    blockers.append("incident_mismatch")
                if lease.correlation_id != request.correlation_id:
                    blockers.append("correlation_mismatch")
                if lease.target.identity_digest != request.target.identity_digest:
                    blockers.append("target_identity_mismatch")
                if (
                    state.active_lease_index.get(request.target.identity_digest)
                    != request.lease_id
                ):
                    blockers.append("lease_fenced")

            approval = state.approvals.get(request.approval_id)
            if approval is None:
                blockers.append("approval_missing")
            else:
                if approval.expires_at_utc <= now:
                    blockers.append("approval_expired")
                if approval.incident_id != request.incident_id:
                    blockers.append("approval_incident_mismatch")
                if approval.correlation_id != request.correlation_id:
                    blockers.append("approval_correlation_mismatch")
                if approval.target_identity_digest != request.target.identity_digest:
                    blockers.append("approval_target_mismatch")
                if approval.action_digest != request.action_digest:
                    blockers.append("approval_action_mismatch")
                if approval.source_revision != request.source_revision:
                    blockers.append("approval_revision_mismatch")
                if approval.policy_version != request.policy_version:
                    blockers.append("approval_policy_mismatch")
                consumed_by = state.consumed_nonce_index.get(approval.nonce)
                if consumed_by and consumed_by != action_key:
                    blockers.append("approval_replayed")

            if blockers:
                state.blocked_decision_count += 1
                state.last_blockers = sorted(set(blockers))
                self._write_state(state)
                decision = RecoveryActionDecision(
                    result="blocked",
                    admitted=False,
                    blockers=state.last_blockers,
                    decided_at_utc=now,
                    recommendation_count=0,
                )
                _append_jsonl(
                    self.root / "action-decision-ledger.jsonl",
                    {
                        **decision.model_dump(mode="json"),
                        "action_key": action_key,
                        "incident_id": request.incident_id,
                        "target_identity_digest": request.target.identity_digest,
                    },
                )
                return decision

            assert approval is not None
            record = RecoveryActionRecord(
                action_key=action_key,
                incident_id=request.incident_id,
                correlation_id=request.correlation_id,
                target_class=request.target.target_class,
                target_identity_digest=request.target.identity_digest,
                owner_id=request.owner_id,
                lease_id=request.lease_id,
                fencing_token=request.fencing_token,
                action=request.action,
                action_digest=request.action_digest,
                approval_id=request.approval_id,
                source_revision=request.source_revision,
                policy_version=request.policy_version,
                recorded_at_utc=now,
            )
            state.actions[action_key] = record
            state.consumed_nonce_index[approval.nonce] = action_key
            state.last_blockers = []
            self._write_state(state)
            _append_jsonl(self.root / "action-ledger.jsonl", record.model_dump(mode="json"))
            decision = RecoveryActionDecision(
                result="authorized",
                admitted=True,
                action=record,
                decided_at_utc=now,
                recommendation_count=1,
            )
            _append_jsonl(
                self.root / "action-decision-ledger.jsonl",
                decision.model_dump(mode="json"),
            )
            return decision


def build_incident_plane_snapshot(
    *,
    correlation_root: Path,
    coordination_store: RecoveryCoordinationStore,
    generated_at_utc: datetime,
    evidence_root: str,
    timing_by_incident: dict[str, IncidentTiming] | None = None,
    child_evidence_by_incident: dict[str, list[str]] | None = None,
) -> IncidentPlaneSnapshot:
    now = require_utc(generated_at_utc)
    state = coordination_store.snapshot()
    timing_by_incident = timing_by_incident or {}
    child_evidence_by_incident = child_evidence_by_incident or {}
    incidents: list[IncidentPlaneRecord] = []
    for path in sorted((correlation_root / "incidents").glob("*.json")):
        incident = IncidentRecord.model_validate_json(path.read_text(encoding="utf-8"))
        leases = [item for item in state.leases.values() if item.incident_id == incident.incident_id]
        lease = sorted(leases, key=lambda item: item.fencing_token)[-1] if leases else None
        actions = [item for item in state.actions.values() if item.incident_id == incident.incident_id]
        incidents.append(
            IncidentPlaneRecord(
                incident_id=incident.incident_id,
                correlation_id=incident.correlation_id,
                state=incident.state,
                root_fingerprint=incident.root_fingerprint,
                event_count=len(incident.event_ids),
                causal_edge_count=len(incident.edges),
                blockers=incident.blockers,
                target_class=lease.target.target_class if lease else None,
                target_identity_digest=lease.target.identity_digest if lease else None,
                owner_id=lease.owner_id if lease else None,
                fencing_token=lease.fencing_token if lease else None,
                lease_expires_at_utc=lease.expires_at_utc if lease else None,
                authorized_recommendation_count=len(actions),
                timing=timing_by_incident.get(incident.incident_id, IncidentTiming()),
                child_evidence_uris=child_evidence_by_incident.get(incident.incident_id, []),
                created_at_utc=incident.created_at,
                updated_at_utc=incident.updated_at,
            )
        )
    return IncidentPlaneSnapshot(
        status="live",
        generated_at_utc=now,
        source_revision=coordination_store.policy.source_revision,
        policy_version=coordination_store.policy.policy_version,
        incidents=incidents,
        leases=sorted(state.leases.values(), key=lambda item: item.acquired_at_utc),
        actions=sorted(state.actions.values(), key=lambda item: item.recorded_at_utc),
        blocked_decision_count=state.blocked_decision_count,
        active_blockers=state.last_blockers,
        evidence_root=evidence_root,
    )


def write_incident_plane_snapshot(path: Path, snapshot: IncidentPlaneSnapshot) -> None:
    atomic_write_json(path, snapshot.model_dump(mode="json"))
