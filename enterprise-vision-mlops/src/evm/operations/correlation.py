from __future__ import annotations

import hashlib
import json
import os
import tomllib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evm.operations.failure_scenarios import atomic_write_json, exclusive_lock


SHA256_PATTERN = r"^[a-f0-9]{64}$"
REVISION_PATTERN = r"^[a-f0-9]{7,40}$"
ScenarioId = Literal["A", "B", "C", "D", "E", "CROSS"]
IncidentState = Literal[
    "observed",
    "normalized",
    "correlated",
    "held",
    "blocked",
    "contained",
    "recovery_pending",
    "recovery_owned",
    "recovered",
    "validated",
    "closed",
    "rollback_pending",
    "rolled_back",
]


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


def canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def stable_digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def uuid7(observed_at: datetime | None = None) -> str:
    """Create an RFC 9562 UUIDv7 without relying on Python 3.14."""

    now = require_utc(observed_at or datetime.now(UTC))
    milliseconds = int(now.timestamp() * 1_000) & ((1 << 48) - 1)
    random_value = int.from_bytes(os.urandom(10), "big") & ((1 << 76) - 1)
    value = (milliseconds << 80) | (0x7 << 76) | random_value
    value &= ~(0b11 << 62)
    value |= 0b10 << 62
    return str(uuid.UUID(int=value))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubjectScope(StrictModel):
    lifecycle_series_id: str = Field(min_length=8)
    lifecycle_run_id: str = Field(min_length=8)
    attempt_id: str = Field(min_length=4)
    bindings: dict[str, str] = Field(min_length=1)

    @field_validator("bindings")
    @classmethod
    def validate_bindings(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not str(key).strip() or not str(item).strip() for key, item in value.items()):
            raise ValueError("subject bindings require non-empty keys and values")
        return dict(sorted(value.items()))


class DataSubject(SubjectScope):
    kind: Literal["data"] = "data"
    dataset_version: str = Field(min_length=1)
    source_manifest_digest: str = Field(pattern=SHA256_PATTERN)
    split_manifest_digest: str = Field(pattern=SHA256_PATTERN)
    ct_digest: str = Field(pattern=SHA256_PATTERN)
    lineage_root: str = Field(pattern=SHA256_PATTERN)
    record_identity_digest: str = Field(pattern=SHA256_PATTERN)
    shard_identity_digest: str = Field(pattern=SHA256_PATTERN)


class ModelSubject(SubjectScope):
    kind: Literal["model"] = "model"
    mlflow_run_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    model_artifact_digest: str = Field(pattern=SHA256_PATTERN)
    image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    policy_digest: str = Field(pattern=SHA256_PATTERN)
    role: Literal["stable", "challenger", "candidate", "rollback"]
    environment: str = Field(min_length=1)


class KubernetesSubject(SubjectScope):
    kind: Literal["kubernetes"] = "kubernetes"
    cluster: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    resource_kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    uid: str = Field(min_length=8)
    pod_uid: str = Field(min_length=8)
    container_name: str = Field(min_length=1)
    image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    expected_replica_identity: str = Field(min_length=1)


class LifecycleSubject(SubjectScope):
    kind: Literal["lifecycle"] = "lifecycle"
    host: str = Field(min_length=1)
    supervisor_revision: str = Field(pattern=REVISION_PATTERN)
    process_role: Literal["supervisor", "lifecycle_worker", "kubernetes_observer"]
    pid: int = Field(gt=0)
    process_started_at: datetime
    command_digest: str = Field(pattern=SHA256_PATTERN)
    lease_id: str = Field(min_length=8)
    fencing_token: int = Field(ge=1)

    _validate_process_started_at = field_validator("process_started_at")(require_utc)


class EvidenceSubject(SubjectScope):
    kind: Literal["evidence"] = "evidence"
    scenario_run_id: str = Field(min_length=8)
    source_revision: str = Field(pattern=REVISION_PATTERN)
    evidence_schema_version: str = Field(min_length=1)
    artifact_index_digest: str = Field(pattern=SHA256_PATTERN)
    validation_report_digest: str = Field(pattern=SHA256_PATTERN)


SubjectIdentity = Annotated[
    DataSubject | ModelSubject | KubernetesSubject | LifecycleSubject | EvidenceSubject,
    Field(discriminator="kind"),
]


class CorrelationPolicy(StrictModel):
    schema_version: Literal["evm.cross_scenario_correlation_policy.v1"]
    policy_version: str = Field(min_length=1)
    collector_cadence_ms: int = Field(gt=0)
    freshness_seconds: int = Field(gt=0)
    decision_deadline_seconds: int = Field(gt=0)
    dedupe_ttl_seconds: int = Field(gt=0)
    recurrence_window_seconds: int = Field(gt=0)
    clock_tolerance_seconds: int = Field(ge=0)
    component_revisions: dict[str, str] = Field(min_length=1)
    non_mutating_actions: list[str] = Field(min_length=1)

    @field_validator("component_revisions")
    @classmethod
    def validate_revisions(cls, value: dict[str, str]) -> dict[str, str]:
        for component, revision in value.items():
            if not component or not (7 <= len(revision) <= 40):
                raise ValueError("component revisions require a component and 7-40 char revision")
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def validate_timing(self) -> "CorrelationPolicy":
        if self.decision_deadline_seconds < self.freshness_seconds:
            raise ValueError("decision deadline cannot be shorter than freshness")
        if self.recurrence_window_seconds < self.dedupe_ttl_seconds:
            raise ValueError("recurrence window cannot be shorter than dedupe TTL")
        return self


def load_policy(path: Path, *, revision: str | None = None) -> CorrelationPolicy:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    policy_payload = dict(payload["correlation"])
    revisions = dict(payload.get("component_revisions", {}))
    if revision:
        revisions = {name: revision for name in revisions}
    policy_payload["component_revisions"] = revisions
    policy_payload["non_mutating_actions"] = list(
        payload.get("actions", {}).get("non_mutating", [])
    )
    return CorrelationPolicy.model_validate(policy_payload)


class NormalizedEvent(StrictModel):
    schema_version: Literal["evm.cross_scenario_event.v1"]
    event_id: str = Field(min_length=8)
    correlation_id: str | None = None
    causation_id: str | None = None
    parent_incident_id: str | None = None
    scenario_id: ScenarioId
    event_type: str = Field(min_length=1)
    cause_code: str = Field(min_length=1)
    severity: Literal["info", "warning", "critical"]
    observed_at_utc: datetime
    monotonic_elapsed_ms: int = Field(ge=0)
    collector_cadence_ms: int = Field(gt=0)
    fresh_until_utc: datetime
    producer_boot_id: str = Field(min_length=8)
    producer_sequence: int = Field(ge=1)
    source_component: str = Field(min_length=1)
    source_revision: str = Field(pattern=REVISION_PATTERN)
    policy_version: str = Field(min_length=1)
    evidence_digest: str = Field(pattern=SHA256_PATTERN)
    semantic_identity_digest: str = Field(pattern=SHA256_PATTERN)
    decision_inputs: dict[str, Any] = Field(min_length=1)
    subject_identity: SubjectIdentity
    target_match_count: int = Field(ge=0)
    actor_or_controller: str = Field(min_length=1)
    recommended_action: str = Field(min_length=1)

    _validate_observed_at = field_validator("observed_at_utc")(require_utc)
    _validate_fresh_until = field_validator("fresh_until_utc")(require_utc)

    @model_validator(mode="after")
    def validate_event(self) -> "NormalizedEvent":
        if self.fresh_until_utc <= self.observed_at_utc:
            raise ValueError("fresh_until_utc must follow observed_at_utc")
        if bool(self.causation_id) != bool(self.parent_incident_id):
            raise ValueError("causation_id and parent_incident_id must be set together")
        if stable_digest(self.decision_inputs) != self.semantic_identity_digest:
            raise ValueError("semantic_identity_digest does not match decision_inputs")
        return self


class CausalEdge(StrictModel):
    parent_event_id: str = Field(min_length=8)
    child_event_id: str = Field(min_length=8)
    dependency_rule: str = Field(min_length=1)
    identity_compatible: bool


class IncidentRecord(StrictModel):
    schema_version: Literal["evm.cross_scenario_incident.v1"]
    incident_id: str = Field(min_length=8)
    correlation_id: str = Field(min_length=8)
    root_fingerprint: str = Field(pattern=SHA256_PATTERN)
    root_event_id: str = Field(min_length=8)
    state: IncidentState
    event_ids: list[str] = Field(min_length=1)
    edges: list[CausalEdge] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None

    _validate_created_at = field_validator("created_at")(require_utc)
    _validate_updated_at = field_validator("updated_at")(require_utc)
    _validate_closed_at = field_validator("closed_at")(
        lambda value: require_utc(value) if value else value
    )


class DedupeRecord(StrictModel):
    fingerprint: str = Field(pattern=SHA256_PATTERN)
    incident_id: str = Field(min_length=8)
    retained_event_id: str = Field(min_length=8)
    source_event_ids: list[str] = Field(min_length=1)
    evidence_digests: list[str] = Field(min_length=1)
    first_observed_at: datetime
    last_observed_at: datetime
    count: int = Field(ge=1)

    _validate_first_observed_at = field_validator("first_observed_at")(require_utc)
    _validate_last_observed_at = field_validator("last_observed_at")(require_utc)


class CorrelationStoreState(StrictModel):
    schema_version: Literal["evm.cross_scenario_store.v1"] = "evm.cross_scenario_store.v1"
    root_index: dict[str, str] = Field(default_factory=dict)
    event_index: dict[str, str] = Field(default_factory=dict)
    event_fingerprints: dict[str, str] = Field(default_factory=dict)
    dedupe: dict[str, DedupeRecord] = Field(default_factory=dict)
    action_index: dict[str, str] = Field(default_factory=dict)


class CorrelationDecision(StrictModel):
    schema_version: Literal["evm.cross_scenario_decision.v1"]
    outcome: Literal["new", "deduped", "recurrence", "held", "blocked"]
    incident_id: str
    correlation_id: str
    source_event_id: str
    retained_event_id: str
    fingerprint: str = Field(pattern=SHA256_PATTERN)
    duplicate_count: int = Field(ge=1)
    action_key: str | None = Field(default=None, pattern=SHA256_PATTERN)
    action_emitted: bool
    blockers: list[str] = Field(default_factory=list)
    ingested_at_utc: datetime
    coordinator_monotonic_elapsed_ms: int = Field(ge=0)

    _validate_ingested_at = field_validator("ingested_at_utc")(require_utc)


class CorrelationError(RuntimeError):
    pass


def event_fingerprint(event: NormalizedEvent) -> str:
    return stable_digest(
        {
            "policy_version": event.policy_version,
            "scenario_id": event.scenario_id,
            "event_type": event.event_type,
            "cause_code": event.cause_code,
            "subject_identity": event.subject_identity.model_dump(mode="json"),
            "semantic_identity_digest": event.semantic_identity_digest,
            "source_revision": event.source_revision,
        }
    )


def identities_compatible(parent: SubjectIdentity, child: SubjectIdentity) -> bool:
    if parent.lifecycle_series_id != child.lifecycle_series_id:
        return False
    if parent.lifecycle_run_id != child.lifecycle_run_id:
        return False
    if parent.attempt_id != child.attempt_id:
        return False
    shared = set(parent.bindings) & set(child.bindings)
    if not shared:
        return False
    return all(parent.bindings[key] == child.bindings[key] for key in shared)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


class CorrelationStore:
    def __init__(self, root: Path, policy: CorrelationPolicy) -> None:
        self.root = root
        self.policy = policy
        self.state_path = root / "store.json"
        self.lock_path = root / "store.lock"

    def _load_state(self) -> CorrelationStoreState:
        if not self.state_path.exists():
            return CorrelationStoreState()
        return CorrelationStoreState.model_validate_json(
            self.state_path.read_text(encoding="utf-8-sig")
        )

    def _incident_path(self, incident_id: str) -> Path:
        return self.root / "incidents" / f"{incident_id}.json"

    def _event_path(self, event_id: str) -> Path:
        return self.root / "events" / f"{event_id}.json"

    def read_incident(self, incident_id: str) -> IncidentRecord:
        path = self._incident_path(incident_id)
        if not path.is_file():
            raise CorrelationError(f"incident_not_found:{incident_id}")
        return IncidentRecord.model_validate_json(path.read_text(encoding="utf-8-sig"))

    def read_event(self, event_id: str) -> NormalizedEvent:
        path = self._event_path(event_id)
        if not path.is_file():
            raise CorrelationError(f"event_not_found:{event_id}")
        return NormalizedEvent.model_validate_json(path.read_text(encoding="utf-8-sig"))

    def _precondition_blockers(
        self,
        event: NormalizedEvent,
        *,
        raw_evidence: dict[str, Any],
        ingested_at: datetime,
    ) -> list[str]:
        blockers: list[str] = []
        if stable_digest(raw_evidence) != event.evidence_digest:
            blockers.append("evidence_digest_mismatch")
        if event.policy_version != self.policy.policy_version:
            blockers.append("policy_version_mismatch")
        expected_revision = self.policy.component_revisions.get(event.source_component)
        if expected_revision is None:
            blockers.append("source_component_unknown")
        elif event.source_revision != expected_revision:
            blockers.append("source_revision_mismatch")
        if event.collector_cadence_ms != self.policy.collector_cadence_ms:
            blockers.append("collector_cadence_mismatch")
        if ingested_at > event.fresh_until_utc:
            blockers.append("event_stale")
        if event.observed_at_utc - ingested_at > timedelta(
            seconds=self.policy.clock_tolerance_seconds
        ):
            blockers.append("wall_clock_offset_exceeded")
        if (
            event.recommended_action not in self.policy.non_mutating_actions
            and event.target_match_count != 1
        ):
            blockers.append(
                "target_missing" if event.target_match_count == 0 else "target_ambiguous"
            )
        return blockers

    def _new_incident(
        self,
        event: NormalizedEvent,
        fingerprint: str,
        now: datetime,
        blockers: list[str],
    ) -> IncidentRecord:
        correlation_id = uuid7(now)
        incident_id = f"inc-{uuid7(now)}"
        if event.correlation_id and event.correlation_id != correlation_id:
            blockers.append("producer_correlation_override_denied")
        state: IncidentState = "blocked" if blockers else "correlated"
        return IncidentRecord(
            schema_version="evm.cross_scenario_incident.v1",
            incident_id=incident_id,
            correlation_id=correlation_id,
            root_fingerprint=fingerprint,
            root_event_id=event.event_id,
            state=state,
            event_ids=[event.event_id],
            blockers=sorted(set(blockers)),
            created_at=now,
            updated_at=now,
        )

    def _would_cycle(
        self,
        edges: list[CausalEdge],
        parent_event_id: str,
        child_event_id: str,
    ) -> bool:
        adjacency: dict[str, set[str]] = {}
        for edge in edges:
            adjacency.setdefault(edge.parent_event_id, set()).add(edge.child_event_id)
        adjacency.setdefault(parent_event_id, set()).add(child_event_id)
        pending = [child_event_id]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == parent_event_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency.get(current, set()))
        return False

    def ingest(
        self,
        event: NormalizedEvent,
        *,
        raw_evidence: dict[str, Any],
        ingested_at: datetime | None = None,
        coordinator_monotonic_elapsed_ms: int = 0,
    ) -> CorrelationDecision:
        now = require_utc(ingested_at or datetime.now(UTC))
        fingerprint = event_fingerprint(event)
        with exclusive_lock(self.lock_path, timeout_seconds=30):
            state = self._load_state()
            blockers = self._precondition_blockers(
                event,
                raw_evidence=raw_evidence,
                ingested_at=now,
            )

            prior_fingerprint = state.event_fingerprints.get(event.event_id)
            if prior_fingerprint and prior_fingerprint != fingerprint:
                blockers.append("event_id_identity_conflict")

            record = state.dedupe.get(fingerprint)
            dedupe_deadline = event.observed_at_utc - timedelta(
                seconds=self.policy.dedupe_ttl_seconds
            )
            is_dedupe = bool(record and record.last_observed_at >= dedupe_deadline)
            outcome: Literal["new", "deduped", "recurrence", "held", "blocked"]

            if is_dedupe and record is not None:
                incident = self.read_incident(record.incident_id)
                record = record.model_copy(
                    update={
                        "source_event_ids": list(
                            dict.fromkeys([*record.source_event_ids, event.event_id])
                        ),
                        "evidence_digests": list(
                            dict.fromkeys([*record.evidence_digests, event.evidence_digest])
                        ),
                        "last_observed_at": max(
                            record.last_observed_at,
                            event.observed_at_utc,
                        ),
                        "count": record.count + 1,
                    }
                )
                state.dedupe[fingerprint] = record
                state.event_index[event.event_id] = incident.incident_id
                state.event_fingerprints[event.event_id] = fingerprint
                outcome = "blocked" if incident.state == "blocked" else "deduped"
                retained_event_id = record.retained_event_id
            else:
                recurrence = record is not None
                causal_parent: NormalizedEvent | None = None
                causal_incident: IncidentRecord | None = None
                if event.causation_id:
                    parent_incident_id = state.event_index.get(event.causation_id)
                    if parent_incident_id is None:
                        blockers.append("causation_parent_unknown")
                    elif parent_incident_id != event.parent_incident_id:
                        blockers.append("parent_incident_mismatch")
                    else:
                        causal_incident = self.read_incident(parent_incident_id)
                        causal_parent = self.read_event(event.causation_id)
                        if not identities_compatible(
                            causal_parent.subject_identity,
                            event.subject_identity,
                        ):
                            blockers.append("causal_identity_mismatch")

                if causal_incident is not None and not blockers:
                    incident = causal_incident
                    if self._would_cycle(incident.edges, event.causation_id or "", event.event_id):
                        blockers.append("causal_cycle")
                    else:
                        incident.edges.append(
                            CausalEdge(
                                parent_event_id=event.causation_id or "",
                                child_event_id=event.event_id,
                                dependency_rule="explicit_causation",
                                identity_compatible=True,
                            )
                        )
                        incident.event_ids.append(event.event_id)
                        incident.updated_at = now
                else:
                    incident = self._new_incident(event, fingerprint, now, blockers)

                if blockers:
                    incident.state = "blocked" if "causal_cycle" in blockers else "held"
                    if any(
                        blocker
                        not in {"causation_parent_unknown", "parent_incident_mismatch"}
                        for blocker in blockers
                    ):
                        incident.state = "blocked"
                    incident.blockers = sorted(set([*incident.blockers, *blockers]))
                    incident.updated_at = now

                atomic_write_json(
                    self._incident_path(incident.incident_id),
                    incident.model_dump(mode="json"),
                )
                atomic_write_json(
                    self._event_path(event.event_id),
                    event.model_dump(mode="json"),
                )
                state.root_index[fingerprint] = incident.incident_id
                state.event_index[event.event_id] = incident.incident_id
                state.event_fingerprints[event.event_id] = fingerprint
                record = DedupeRecord(
                    fingerprint=fingerprint,
                    incident_id=incident.incident_id,
                    retained_event_id=event.event_id,
                    source_event_ids=[event.event_id],
                    evidence_digests=[event.evidence_digest],
                    first_observed_at=event.observed_at_utc,
                    last_observed_at=event.observed_at_utc,
                    count=1,
                )
                state.dedupe[fingerprint] = record
                retained_event_id = event.event_id
                if blockers:
                    outcome = "blocked" if incident.state == "blocked" else "held"
                else:
                    outcome = "recurrence" if recurrence else "new"

            action_key: str | None = None
            action_emitted = False
            if not blockers and incident.state not in {"blocked", "held"}:
                action_key = stable_digest(
                    {
                        "incident_id": incident.incident_id,
                        "target": event.subject_identity.model_dump(mode="json"),
                        "recommended_action": event.recommended_action,
                        "policy_version": event.policy_version,
                        "source_revision": event.source_revision,
                    }
                )
                if action_key not in state.action_index:
                    state.action_index[action_key] = incident.incident_id
                    action_emitted = True
                    _append_jsonl(
                        self.root / "action-ledger.jsonl",
                        {
                            "schema_version": "evm.cross_scenario_action.v1",
                            "incident_id": incident.incident_id,
                            "correlation_id": incident.correlation_id,
                            "event_id": retained_event_id,
                            "action_key": action_key,
                            "action": event.recommended_action,
                            "state": "planned",
                            "recorded_at": now.isoformat(),
                        },
                    )

            atomic_write_json(self.state_path, state.model_dump(mode="json"))
            _append_jsonl(
                self.root / "events.jsonl",
                {
                    **event.model_dump(mode="json"),
                    "ingested_at_utc": now.isoformat(),
                    "retained_event_id": retained_event_id,
                    "fingerprint": fingerprint,
                },
            )
            _append_jsonl(
                self.root / "dedupe-ledger.jsonl",
                {
                    **record.model_dump(mode="json"),
                    "source_event_id": event.event_id,
                    "outcome": outcome,
                    "recorded_at": now.isoformat(),
                },
            )
            decision = CorrelationDecision(
                schema_version="evm.cross_scenario_decision.v1",
                outcome=outcome,
                incident_id=incident.incident_id,
                correlation_id=incident.correlation_id,
                source_event_id=event.event_id,
                retained_event_id=retained_event_id,
                fingerprint=fingerprint,
                duplicate_count=record.count,
                action_key=action_key,
                action_emitted=action_emitted,
                blockers=sorted(set([*incident.blockers, *blockers])),
                ingested_at_utc=now,
                coordinator_monotonic_elapsed_ms=coordinator_monotonic_elapsed_ms,
            )
            _append_jsonl(
                self.root / "decision-ledger.jsonl",
                decision.model_dump(mode="json"),
            )
            return decision

    def close_incident(
        self,
        incident_id: str,
        *,
        now: datetime | None = None,
    ) -> IncidentRecord:
        observed_at = require_utc(now or datetime.now(UTC))
        with exclusive_lock(self.lock_path, timeout_seconds=30):
            incident = self.read_incident(incident_id)
            if incident.state != "validated":
                raise CorrelationError("incident_must_be_validated_before_close")
            incident.state = "closed"
            incident.updated_at = observed_at
            incident.closed_at = observed_at
            atomic_write_json(
                self._incident_path(incident_id),
                incident.model_dump(mode="json"),
            )
            return incident

    def set_incident_state(
        self,
        incident_id: str,
        state: IncidentState,
        *,
        now: datetime | None = None,
    ) -> IncidentRecord:
        observed_at = require_utc(now or datetime.now(UTC))
        with exclusive_lock(self.lock_path, timeout_seconds=30):
            incident = self.read_incident(incident_id)
            incident.state = state
            incident.updated_at = observed_at
            atomic_write_json(
                self._incident_path(incident_id),
                incident.model_dump(mode="json"),
            )
            return incident

    def add_causal_edge(
        self,
        incident_id: str,
        *,
        parent_event_id: str,
        child_event_id: str,
        dependency_rule: str,
        now: datetime | None = None,
    ) -> IncidentRecord:
        observed_at = require_utc(now or datetime.now(UTC))
        with exclusive_lock(self.lock_path, timeout_seconds=30):
            incident = self.read_incident(incident_id)
            if parent_event_id not in incident.event_ids or child_event_id not in incident.event_ids:
                incident.state = "held"
                incident.blockers = sorted(set([*incident.blockers, "causal_event_unknown"]))
                incident.updated_at = observed_at
                atomic_write_json(
                    self._incident_path(incident_id),
                    incident.model_dump(mode="json"),
                )
                raise CorrelationError("causal_event_unknown")
            parent = self.read_event(parent_event_id)
            child = self.read_event(child_event_id)
            if not identities_compatible(parent.subject_identity, child.subject_identity):
                incident.state = "blocked"
                incident.blockers = sorted(set([*incident.blockers, "causal_identity_mismatch"]))
                incident.updated_at = observed_at
                atomic_write_json(
                    self._incident_path(incident_id),
                    incident.model_dump(mode="json"),
                )
                raise CorrelationError("causal_identity_mismatch")
            if self._would_cycle(incident.edges, parent_event_id, child_event_id):
                incident.state = "blocked"
                incident.blockers = sorted(set([*incident.blockers, "causal_cycle"]))
                incident.updated_at = observed_at
                atomic_write_json(
                    self._incident_path(incident_id),
                    incident.model_dump(mode="json"),
                )
                raise CorrelationError("causal_cycle")
            edge = CausalEdge(
                parent_event_id=parent_event_id,
                child_event_id=child_event_id,
                dependency_rule=dependency_rule,
                identity_compatible=True,
            )
            if edge not in incident.edges:
                incident.edges.append(edge)
            incident.updated_at = observed_at
            atomic_write_json(
                self._incident_path(incident_id),
                incident.model_dump(mode="json"),
            )
            return incident

    def snapshot(self) -> CorrelationStoreState:
        with exclusive_lock(self.lock_path, timeout_seconds=30):
            return self._load_state()
