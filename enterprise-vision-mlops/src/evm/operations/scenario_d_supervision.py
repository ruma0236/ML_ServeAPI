from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import sys
import time
import tomllib
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evm.operations.failure_scenarios import atomic_write_json, exclusive_lock


UTC = timezone.utc
ChildName = Literal["lifecycle_worker", "kubernetes_observer"]
ChildState = Literal[
    "starting",
    "live",
    "suspect",
    "recovering",
    "backoff",
    "blocked",
    "circuit_open",
]
ChildAction = Literal["none", "restart_exact"]


def utc_now() -> datetime:
    return datetime.now(UTC)


@lru_cache(maxsize=1)
def current_process_started_at() -> datetime:
    if sys.platform.startswith("linux"):
        stat = Path("/proc/self/stat").read_text(encoding="utf-8")
        fields = stat[stat.rfind(")") + 2 :].split()
        started_ticks = int(fields[19])
        ticks_per_second = int(os.sysconf("SC_CLK_TCK"))
        boot_elapsed = time.clock_gettime(time.CLOCK_BOOTTIME)
        return utc_now() - timedelta(
            seconds=max(0.0, boot_elapsed - (started_ticks / ticks_per_second))
        )
    if os.name != "nt":
        return utc_now()

    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    creation = FileTime()
    exit_time = FileTime()
    kernel = FileTime()
    user = FileTime()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    handle = kernel32.GetCurrentProcess()
    if not kernel32.GetProcessTimes(
        handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        raise OSError(ctypes.get_last_error(), "GetProcessTimes failed")
    ticks = (creation.high << 32) + creation.low
    return datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=ticks // 10)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScenarioDPolicy(StrictModel):
    schema_version: Literal["evm.scenario_d_policy.v1"]
    check_interval_seconds: float = Field(gt=0)
    heartbeat_interval_seconds: float = Field(gt=0)
    heartbeat_stale_seconds: float = Field(gt=0)
    stale_debounce_samples: int = Field(ge=1)
    max_restarts_per_window: int = Field(ge=1)
    restart_window_seconds: float = Field(gt=0)
    restart_backoff_seconds: list[float] = Field(min_length=1)
    max_detection_seconds: float = Field(gt=0)
    max_stale_detection_seconds: float = Field(gt=0)
    max_recovery_seconds: float = Field(gt=0)
    max_heartbeat_p95_seconds: float = Field(gt=0)
    run_claim_ttl_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_detection_margin(self) -> ScenarioDPolicy:
        if self.check_interval_seconds * 2 >= self.max_detection_seconds:
            raise ValueError(
                "check_interval_seconds must leave more than two polling intervals "
                "inside max_detection_seconds"
            )
        return self

    @classmethod
    def from_toml(cls, path: Path) -> ScenarioDPolicy:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(payload["policy"])


class ProcessRecord(StrictModel):
    pid: int = Field(gt=0)
    process_started_at: datetime
    command_matches: bool
    executable: str | None = None
    command_line: str | None = None

    _validate_started_at = field_validator("process_started_at")(ensure_utc)


class ChildIdentity(StrictModel):
    child_name: ChildName
    pid: int = Field(gt=0)
    process_started_at: datetime
    process_instance_id: str = Field(min_length=8)
    source_commit: str = Field(min_length=7)
    supervisor_lease_id: str = Field(min_length=8)
    fencing_token: int = Field(ge=1)

    _validate_started_at = field_validator("process_started_at")(ensure_utc)


class ChildHeartbeat(ChildIdentity):
    observed_at: datetime

    _validate_observed_at = field_validator("observed_at")(ensure_utc)


class ChildObservation(StrictModel):
    schema_version: Literal["evm.scenario_d_child_observation.v1"]
    child_name: ChildName
    observed_at: datetime
    expected_source_commit: str = Field(min_length=7)
    expected_lease_id: str = Field(min_length=8)
    expected_fencing_token: int = Field(ge=1)
    pid_file_pid: int | None = Field(default=None, gt=0)
    pid_file_process_exists: bool = False
    identity: ChildIdentity | None = None
    heartbeat: ChildHeartbeat | None = None
    processes: list[ProcessRecord] = Field(default_factory=list)

    _validate_observed_at = field_validator("observed_at")(ensure_utc)


class ChildDecision(StrictModel):
    schema_version: Literal["evm.scenario_d_child_decision.v1"]
    child_name: ChildName
    state: ChildState
    action: ChildAction
    reason: str
    target_pid: int | None = None
    exact_identity: bool
    heartbeat_age_seconds: float | None = Field(default=None, ge=0)
    process_count: int = Field(ge=0)
    revision_matches: bool
    lease_matches: bool
    fencing_matches: bool
    failed_samples: int = Field(ge=0)
    incident_fingerprint: str


def _fingerprint(observation: ChildObservation, reason: str) -> str:
    payload = {
        "child_name": observation.child_name,
        "reason": reason,
        "pid_file_pid": observation.pid_file_pid,
        "processes": [
            {
                "pid": item.pid,
                "started_at": item.process_started_at.isoformat(),
                "command_matches": item.command_matches,
            }
            for item in observation.processes
        ],
        "identity": observation.identity.model_dump(mode="json") if observation.identity else None,
        "heartbeat": (
            observation.heartbeat.model_dump(mode="json") if observation.heartbeat else None
        ),
        "expected_source_commit": observation.expected_source_commit,
        "expected_lease_id": observation.expected_lease_id,
        "expected_fencing_token": observation.expected_fencing_token,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decision(
    observation: ChildObservation,
    *,
    state: ChildState,
    action: ChildAction,
    reason: str,
    exact_identity: bool,
    failed_samples: int,
    target_pid: int | None = None,
    heartbeat_age_seconds: float | None = None,
    revision_matches: bool = False,
    lease_matches: bool = False,
    fencing_matches: bool = False,
) -> ChildDecision:
    return ChildDecision(
        schema_version="evm.scenario_d_child_decision.v1",
        child_name=observation.child_name,
        state=state,
        action=action,
        reason=reason,
        target_pid=target_pid,
        exact_identity=exact_identity,
        heartbeat_age_seconds=heartbeat_age_seconds,
        process_count=sum(item.command_matches for item in observation.processes),
        revision_matches=revision_matches,
        lease_matches=lease_matches,
        fencing_matches=fencing_matches,
        failed_samples=failed_samples,
        incident_fingerprint=_fingerprint(observation, reason),
    )


def _identity_matches_process(identity: ChildIdentity, process: ProcessRecord) -> bool:
    return (
        identity.pid == process.pid
        and identity.process_started_at == process.process_started_at
        and process.command_matches
    )


def evaluate_child(
    observation: ChildObservation,
    policy: ScenarioDPolicy,
    *,
    prior_failed_samples: int = 0,
) -> ChildDecision:
    matching = [item for item in observation.processes if item.command_matches]
    if len(matching) > 1:
        return _decision(
            observation,
            state="blocked",
            action="none",
            reason="blocked_duplicate",
            exact_identity=False,
            failed_samples=prior_failed_samples,
        )
    if not matching:
        if observation.pid_file_pid and observation.pid_file_process_exists:
            return _decision(
                observation,
                state="blocked",
                action="none",
                reason="blocked_unknown_owner",
                exact_identity=False,
                failed_samples=prior_failed_samples,
            )
        return _decision(
            observation,
            state="recovering",
            action="restart_exact",
            reason="process_missing",
            exact_identity=True,
            failed_samples=0,
        )

    process = matching[0]
    if observation.pid_file_pid != process.pid:
        return _decision(
            observation,
            state="blocked",
            action="none",
            reason="blocked_unknown_owner",
            exact_identity=False,
            failed_samples=prior_failed_samples,
        )
    identity = observation.identity
    if (
        identity is None
        or identity.child_name != observation.child_name
        or not _identity_matches_process(identity, process)
    ):
        return _decision(
            observation,
            state="blocked",
            action="none",
            reason="blocked_identity",
            exact_identity=False,
            failed_samples=prior_failed_samples,
        )

    heartbeat = observation.heartbeat
    if heartbeat is None:
        samples = prior_failed_samples + 1
        restart = samples >= policy.stale_debounce_samples
        return _decision(
            observation,
            state="recovering" if restart else "suspect",
            action="restart_exact" if restart else "none",
            reason="heartbeat_missing",
            exact_identity=True,
            failed_samples=samples,
            target_pid=process.pid if restart else None,
        )
    if (
        heartbeat.child_name != observation.child_name
        or heartbeat.pid != identity.pid
        or heartbeat.process_started_at != identity.process_started_at
        or heartbeat.process_instance_id != identity.process_instance_id
    ):
        return _decision(
            observation,
            state="blocked",
            action="none",
            reason="blocked_identity",
            exact_identity=False,
            failed_samples=prior_failed_samples,
        )

    age = (observation.observed_at - heartbeat.observed_at).total_seconds()
    if age < -policy.check_interval_seconds:
        return _decision(
            observation,
            state="blocked",
            action="none",
            reason="heartbeat_from_future",
            exact_identity=True,
            failed_samples=prior_failed_samples,
        )
    age = max(0.0, age)
    revision_matches = (
        identity.source_commit == observation.expected_source_commit
        and heartbeat.source_commit == observation.expected_source_commit
    )
    lease_matches = (
        identity.supervisor_lease_id == observation.expected_lease_id
        and heartbeat.supervisor_lease_id == observation.expected_lease_id
    )
    fencing_matches = (
        identity.fencing_token == observation.expected_fencing_token
        and heartbeat.fencing_token == observation.expected_fencing_token
    )
    if not revision_matches:
        return _decision(
            observation,
            state="recovering",
            action="restart_exact",
            reason="source_revision_mismatch",
            target_pid=process.pid,
            exact_identity=True,
            heartbeat_age_seconds=age,
            revision_matches=False,
            lease_matches=lease_matches,
            fencing_matches=fencing_matches,
            failed_samples=0,
        )
    if not lease_matches or not fencing_matches:
        return _decision(
            observation,
            state="recovering",
            action="restart_exact",
            reason="supervisor_fence_mismatch",
            target_pid=process.pid,
            exact_identity=True,
            heartbeat_age_seconds=age,
            revision_matches=True,
            lease_matches=lease_matches,
            fencing_matches=fencing_matches,
            failed_samples=0,
        )
    if age > policy.heartbeat_stale_seconds:
        samples = prior_failed_samples + 1
        restart = samples >= policy.stale_debounce_samples
        return _decision(
            observation,
            state="recovering" if restart else "suspect",
            action="restart_exact" if restart else "none",
            reason="heartbeat_stale",
            target_pid=process.pid if restart else None,
            exact_identity=True,
            heartbeat_age_seconds=age,
            revision_matches=True,
            lease_matches=True,
            fencing_matches=True,
            failed_samples=samples,
        )
    return _decision(
        observation,
        state="live",
        action="none",
        reason="healthy",
        target_pid=process.pid,
        exact_identity=True,
        heartbeat_age_seconds=age,
        revision_matches=True,
        lease_matches=True,
        fencing_matches=True,
        failed_samples=0,
    )


class RestartAttempt(StrictModel):
    child_name: ChildName
    incident_fingerprint: str
    attempted_at: datetime
    target_pid: int | None = None
    result: Literal["admitted", "succeeded", "failed"] = "admitted"
    completed_at: datetime | None = None
    message: str | None = None

    _validate_attempted_at = field_validator("attempted_at")(ensure_utc)
    _validate_completed_at = field_validator("completed_at")(
        lambda value: ensure_utc(value) if value else value
    )


class RestartLedgerData(StrictModel):
    schema_version: Literal["evm.scenario_d_restart_ledger.v1"] = (
        "evm.scenario_d_restart_ledger.v1"
    )
    attempts: list[RestartAttempt] = Field(default_factory=list)


class RestartLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> RestartLedgerData:
        if not self.path.exists():
            return RestartLedgerData()
        return RestartLedgerData.model_validate_json(self.path.read_text(encoding="utf-8-sig"))

    def admit(
        self,
        decision: ChildDecision,
        policy: ScenarioDPolicy,
        *,
        now: datetime | None = None,
    ) -> ChildDecision:
        if decision.action != "restart_exact":
            return decision
        observed_at = ensure_utc(now or utc_now())
        with exclusive_lock(self.path.with_suffix(".lock")):
            ledger = self.load()
            if any(
                item.incident_fingerprint == decision.incident_fingerprint
                and item.result in {"admitted", "succeeded"}
                for item in ledger.attempts
            ):
                return decision.model_copy(
                    update={"action": "none", "state": "recovering", "reason": "duplicate_incident_replay"}
                )
            window_start = observed_at - timedelta(seconds=policy.restart_window_seconds)
            recent = [
                item
                for item in ledger.attempts
                if item.child_name == decision.child_name and item.attempted_at >= window_start
            ]
            if len(recent) >= policy.max_restarts_per_window:
                return decision.model_copy(
                    update={"action": "none", "state": "circuit_open", "reason": "restart_budget_exhausted"}
                )
            if recent:
                delay_index = min(len(recent) - 1, len(policy.restart_backoff_seconds) - 1)
                eligible_at = recent[-1].attempted_at + timedelta(
                    seconds=policy.restart_backoff_seconds[delay_index]
                )
                if observed_at < eligible_at:
                    return decision.model_copy(
                        update={"action": "none", "state": "backoff", "reason": "restart_backoff"}
                    )
            ledger.attempts.append(
                RestartAttempt(
                    child_name=decision.child_name,
                    incident_fingerprint=decision.incident_fingerprint,
                    attempted_at=observed_at,
                    target_pid=decision.target_pid,
                )
            )
            atomic_write_json(self.path, ledger.model_dump(mode="json"))
        return decision

    def complete(
        self,
        incident_fingerprint: str,
        *,
        result: Literal["succeeded", "failed"],
        message: str | None = None,
        now: datetime | None = None,
    ) -> RestartAttempt:
        observed_at = ensure_utc(now or utc_now())
        with exclusive_lock(self.path.with_suffix(".lock")):
            ledger = self.load()
            index = next(
                (
                    value
                    for value in range(len(ledger.attempts) - 1, -1, -1)
                    if ledger.attempts[value].incident_fingerprint == incident_fingerprint
                    and ledger.attempts[value].result == "admitted"
                ),
                None,
            )
            if index is None:
                raise RuntimeError("restart_attempt_not_admitted")
            completed = ledger.attempts[index].model_copy(
                update={"result": result, "completed_at": observed_at, "message": message}
            )
            ledger.attempts[index] = completed
            atomic_write_json(self.path, ledger.model_dump(mode="json"))
            return completed


class ChildRuntimeMemory(StrictModel):
    failed_samples: int = Field(default=0, ge=0)
    state: ChildState = "starting"
    reason: str = "not_observed"
    incident_fingerprint: str | None = None


class SupervisorRuntimeState(StrictModel):
    schema_version: Literal["evm.scenario_d_runtime_state.v1"] = (
        "evm.scenario_d_runtime_state.v1"
    )
    updated_at: datetime
    children: dict[str, ChildRuntimeMemory] = Field(default_factory=dict)

    _validate_updated_at = field_validator("updated_at")(ensure_utc)


def _load_runtime_state(path: Path, now: datetime) -> SupervisorRuntimeState:
    if not path.exists():
        return SupervisorRuntimeState(updated_at=now)
    return SupervisorRuntimeState.model_validate_json(path.read_text(encoding="utf-8-sig"))


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(path.with_suffix(".lock")):
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def evaluate_runtime_tick(
    *,
    observation: ChildObservation,
    policy: ScenarioDPolicy,
    state_path: Path,
    ledger_path: Path,
    audit_path: Path,
) -> ChildDecision:
    now = observation.observed_at
    with exclusive_lock(state_path.with_suffix(".lock")):
        state = _load_runtime_state(state_path, now)
        memory = state.children.get(observation.child_name, ChildRuntimeMemory())
        decision = evaluate_child(
            observation,
            policy,
            prior_failed_samples=memory.failed_samples,
        )
        decision = RestartLedger(ledger_path).admit(decision, policy, now=now)
        state.children[observation.child_name] = ChildRuntimeMemory(
            failed_samples=decision.failed_samples,
            state=decision.state,
            reason=decision.reason,
            incident_fingerprint=decision.incident_fingerprint,
        )
        state.updated_at = now
        atomic_write_json(state_path, state.model_dump(mode="json"))
    append_jsonl(
        audit_path,
        {
            "schema_version": "evm.scenario_d_audit_event.v1",
            "observed_at": now.isoformat(),
            "event": "child_decision",
            **decision.model_dump(mode="json"),
        },
    )
    return decision


class LifecycleRunClaim(StrictModel):
    schema_version: Literal["evm.lifecycle_run_claim.v1"] = "evm.lifecycle_run_claim.v1"
    run_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=8)
    claim_epoch: int = Field(ge=1)
    worker_id: str = Field(min_length=1)
    worker_pid: int = Field(gt=0)
    process_instance_id: str = Field(min_length=8)
    source_commit: str = Field(min_length=7)
    supervisor_lease_id: str = Field(min_length=8)
    fencing_token: int = Field(ge=1)
    acquired_at: datetime
    renewed_at: datetime
    expires_at: datetime
    released_at: datetime | None = None

    _validate_acquired_at = field_validator("acquired_at")(ensure_utc)
    _validate_renewed_at = field_validator("renewed_at")(ensure_utc)
    _validate_expires_at = field_validator("expires_at")(ensure_utc)
    _validate_released_at = field_validator("released_at")(lambda value: ensure_utc(value) if value else value)


class ClaimAcquireResult(StrictModel):
    acquired: bool
    reason: str
    claim: LifecycleRunClaim | None = None


class LifecycleRunClaimStore:
    def __init__(self, root: Path, *, ttl_seconds: float = 30.0) -> None:
        self.root = root
        self.ttl_seconds = ttl_seconds

    def _path(self, run_id: str) -> Path:
        safe_prefix = re.sub(r"[^A-Za-z0-9_.-]", "_", run_id)[:80] or "run"
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]
        return self.root / f"{safe_prefix}-{digest}.json"

    def _load(self, path: Path) -> LifecycleRunClaim | None:
        if not path.exists():
            return None
        return LifecycleRunClaim.model_validate_json(path.read_text(encoding="utf-8-sig"))

    def acquire(
        self,
        *,
        run_id: str,
        worker_id: str,
        worker_pid: int,
        process_instance_id: str,
        source_commit: str,
        supervisor_lease_id: str,
        fencing_token: int,
        now: datetime | None = None,
    ) -> ClaimAcquireResult:
        observed_at = ensure_utc(now or utc_now())
        path = self._path(run_id)
        with exclusive_lock(path.with_suffix(".lock")):
            current = self._load(path)
            owner_matches = bool(
                current
                and current.worker_id == worker_id
                and current.worker_pid == worker_pid
                and current.process_instance_id == process_instance_id
                and current.supervisor_lease_id == supervisor_lease_id
                and current.fencing_token == fencing_token
            )
            if current and current.released_at is None and current.expires_at > observed_at:
                if owner_matches:
                    renewed = current.model_copy(
                        update={
                            "renewed_at": observed_at,
                            "expires_at": observed_at + timedelta(seconds=self.ttl_seconds),
                        }
                    )
                    atomic_write_json(path, renewed.model_dump(mode="json"))
                    return ClaimAcquireResult(acquired=True, reason="claim_reused", claim=renewed)
                return ClaimAcquireResult(acquired=False, reason="active_claim_conflict", claim=current)
            if current and current.fencing_token > fencing_token:
                return ClaimAcquireResult(acquired=False, reason="stale_supervisor_fence", claim=current)
            claim = LifecycleRunClaim(
                run_id=run_id,
                claim_id=uuid.uuid4().hex,
                claim_epoch=(current.claim_epoch + 1) if current else 1,
                worker_id=worker_id,
                worker_pid=worker_pid,
                process_instance_id=process_instance_id,
                source_commit=source_commit,
                supervisor_lease_id=supervisor_lease_id,
                fencing_token=fencing_token,
                acquired_at=observed_at,
                renewed_at=observed_at,
                expires_at=observed_at + timedelta(seconds=self.ttl_seconds),
            )
            atomic_write_json(path, claim.model_dump(mode="json"))
            return ClaimAcquireResult(
                acquired=True,
                reason="expired_claim_replaced" if current else "claim_acquired",
                claim=claim,
            )

    def renew(
        self,
        claim: LifecycleRunClaim,
        *,
        now: datetime | None = None,
    ) -> LifecycleRunClaim:
        observed_at = ensure_utc(now or utc_now())
        path = self._path(claim.run_id)
        with exclusive_lock(path.with_suffix(".lock")):
            current = self._load(path)
            if current is None or current.claim_id != claim.claim_id:
                raise RuntimeError("lifecycle_claim_lost")
            if current.released_at is not None or current.expires_at <= observed_at:
                raise RuntimeError("lifecycle_claim_expired")
            renewed = current.model_copy(
                update={
                    "renewed_at": observed_at,
                    "expires_at": observed_at + timedelta(seconds=self.ttl_seconds),
                }
            )
            atomic_write_json(path, renewed.model_dump(mode="json"))
            return renewed

    def release(
        self,
        claim: LifecycleRunClaim,
        *,
        now: datetime | None = None,
    ) -> LifecycleRunClaim:
        observed_at = ensure_utc(now or utc_now())
        path = self._path(claim.run_id)
        with exclusive_lock(path.with_suffix(".lock")):
            current = self._load(path)
            if current is None or current.claim_id != claim.claim_id:
                raise RuntimeError("lifecycle_claim_lost")
            released = current.model_copy(
                update={
                    "renewed_at": observed_at,
                    "expires_at": observed_at,
                    "released_at": observed_at,
                }
            )
            atomic_write_json(path, released.model_dump(mode="json"))
            return released


class TransactionalLifecycleRunClaimStore:
    """PostgreSQL-backed claim store used when S1 transactional mode is enabled."""

    def __init__(self, *, ttl_seconds: float = 30.0) -> None:
        from evm.control_panel.transactional_store import get_transactional_store

        self.store = get_transactional_store()
        if not self.store.enabled:
            raise RuntimeError("transactional_control_plane_store_disabled")
        self.ttl_seconds = ttl_seconds

    def acquire(
        self,
        *,
        run_id: str,
        worker_id: str,
        worker_pid: int,
        process_instance_id: str,
        source_commit: str,
        supervisor_lease_id: str,
        fencing_token: int,
        now: datetime | None = None,
    ) -> ClaimAcquireResult:
        result = self.store.acquire_claim(
            run_id=run_id,
            worker_id=worker_id,
            worker_pid=worker_pid,
            process_instance_id=process_instance_id,
            source_commit=source_commit,
            supervisor_lease_id=supervisor_lease_id,
            fencing_token=fencing_token,
            ttl_seconds=self.ttl_seconds,
            now=now,
        )
        return ClaimAcquireResult(
            acquired=result.acquired,
            reason=result.reason,
            claim=(
                LifecycleRunClaim.model_validate(result.claim)
                if result.claim is not None
                else None
            ),
        )

    def renew(
        self,
        claim: LifecycleRunClaim,
        *,
        now: datetime | None = None,
    ) -> LifecycleRunClaim:
        payload = self.store.renew_claim(
            claim.model_dump(mode="json"),
            ttl_seconds=self.ttl_seconds,
            now=now,
        )
        return LifecycleRunClaim.model_validate(payload)

    def release(
        self,
        claim: LifecycleRunClaim,
        *,
        now: datetime | None = None,
    ) -> LifecycleRunClaim:
        payload = self.store.release_claim(claim.model_dump(mode="json"), now=now)
        return LifecycleRunClaim.model_validate(payload)

    def reconcile_stale(self, *, now: datetime | None = None) -> list[str]:
        return self.store.reconcile_stale_claims(now=now)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Scenario D supervision state.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("--policy", required=True, type=Path)
    evaluate_parser.add_argument("--observation", required=True, type=Path)
    evaluate_parser.add_argument("--state", required=True, type=Path)
    evaluate_parser.add_argument("--ledger", required=True, type=Path)
    evaluate_parser.add_argument("--audit", required=True, type=Path)
    evaluate_parser.add_argument("--output", required=True, type=Path)
    complete_parser = subparsers.add_parser("complete-restart")
    complete_parser.add_argument("--ledger", required=True, type=Path)
    complete_parser.add_argument("--incident-fingerprint", required=True)
    complete_parser.add_argument("--result", choices=("succeeded", "failed"), required=True)
    complete_parser.add_argument("--message")
    args = parser.parse_args()
    if args.command == "complete-restart":
        RestartLedger(args.ledger).complete(
            args.incident_fingerprint,
            result=args.result,
            message=args.message,
        )
        return 0
    decision = evaluate_runtime_tick(
        observation=ChildObservation.model_validate_json(
            args.observation.read_text(encoding="utf-8-sig")
        ),
        policy=ScenarioDPolicy.from_toml(args.policy),
        state_path=args.state,
        ledger_path=args.ledger,
        audit_path=args.audit,
    )
    atomic_write_json(args.output, decision.model_dump(mode="json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
