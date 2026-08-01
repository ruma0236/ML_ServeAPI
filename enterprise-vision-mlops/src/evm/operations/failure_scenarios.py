from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ScenarioStateName = Literal[
    "planned",
    "baseline_validated",
    "non_disruptive_validated",
    "pending_approval",
    "approved",
    "injecting",
    "detected",
    "contained",
    "recovering",
    "verifying",
    "passed",
    "blocked",
    "failed",
]

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "planned": {"baseline_validated", "blocked", "failed"},
    "baseline_validated": {"non_disruptive_validated", "blocked", "failed"},
    "non_disruptive_validated": {"pending_approval", "blocked", "failed"},
    "pending_approval": {"approved", "blocked", "failed"},
    "approved": {"injecting", "blocked", "failed"},
    "injecting": {"detected", "contained", "recovering", "failed"},
    "detected": {"contained", "recovering", "failed"},
    "contained": {"recovering", "failed"},
    "recovering": {"verifying", "failed"},
    "verifying": {"passed", "failed"},
    "passed": set(),
    "blocked": set(),
    "failed": set(),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def exclusive_lock(path: Path, *, timeout_seconds: float = 5.0) -> Iterator[None]:
    deadline = time.monotonic() + timeout_seconds
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"lock_timeout:{path}")
            time.sleep(0.02)
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


class ScenarioStateEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=0)
    state: ScenarioStateName
    at: datetime
    reason: str

    _validate_at = field_validator("at")(_utc)


class ScenarioState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evm.operational_scenario_state.v1"]
    scenario_id: Literal["A", "B", "C", "D", "E", "CROSS"]
    run_id: str
    state: ScenarioStateName
    revision: int = Field(ge=0)
    updated_at: datetime
    history: list[ScenarioStateEvent]

    _validate_updated_at = field_validator("updated_at")(_utc)


class StateTransitionError(RuntimeError):
    pass


class ScenarioStateStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, run_id: str) -> Path:
        return self.root / run_id / "state.json"

    def create(self, *, scenario_id: str, run_id: str, now: datetime | None = None) -> ScenarioState:
        path = self._path(run_id)
        if path.exists():
            raise StateTransitionError(f"state_already_exists:{run_id}")
        observed_at = _utc(now or utc_now())
        state = ScenarioState(
            schema_version="evm.operational_scenario_state.v1",
            scenario_id=scenario_id,
            run_id=run_id,
            state="planned",
            revision=0,
            updated_at=observed_at,
            history=[
                ScenarioStateEvent(
                    sequence=0,
                    state="planned",
                    at=observed_at,
                    reason="scenario_created",
                )
            ],
        )
        with exclusive_lock(path.with_suffix(".lock")):
            if path.exists():
                raise StateTransitionError(f"state_already_exists:{run_id}")
            atomic_write_json(path, state.model_dump(mode="json"))
        return state

    def load(self, run_id: str) -> ScenarioState:
        return ScenarioState.model_validate_json(self._path(run_id).read_text(encoding="utf-8"))

    def transition(
        self,
        run_id: str,
        *,
        next_state: ScenarioStateName,
        expected_revision: int,
        reason: str,
        now: datetime | None = None,
    ) -> ScenarioState:
        path = self._path(run_id)
        with exclusive_lock(path.with_suffix(".lock")):
            current = self.load(run_id)
            if current.revision != expected_revision:
                raise StateTransitionError(
                    f"stale_state_revision:expected={expected_revision},actual={current.revision}"
                )
            if next_state not in ALLOWED_TRANSITIONS[current.state]:
                raise StateTransitionError(f"invalid_transition:{current.state}->{next_state}")
            observed_at = _utc(now or utc_now())
            updated = current.model_copy(
                update={
                    "state": next_state,
                    "revision": current.revision + 1,
                    "updated_at": observed_at,
                    "history": current.history
                    + [
                        ScenarioStateEvent(
                            sequence=current.revision + 1,
                            state=next_state,
                            at=observed_at,
                            reason=reason,
                        )
                    ],
                }
            )
            atomic_write_json(path, updated.model_dump(mode="json"))
            return updated


class TargetRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str = Field(min_length=1)
    name: str = Field(min_length=1)
    uid: str = Field(min_length=1)


class ScenarioLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["evm.operational_scenario_lease.v1"]
    lease_token: str
    run_id: str
    owner: str
    target: TargetRef
    acquired_at: datetime
    expires_at: datetime

    _validate_acquired_at = field_validator("acquired_at")(_utc)
    _validate_expires_at = field_validator("expires_at")(_utc)


class LeaseConflict(RuntimeError):
    pass


class LeaseManager:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, target: TargetRef) -> Path:
        key = hashlib.sha256(
            f"{target.namespace}/{target.name}/{target.uid}".encode("utf-8")
        ).hexdigest()
        return self.root / "leases" / f"{key}.json"

    def acquire(
        self,
        *,
        run_id: str,
        owner: str,
        target: TargetRef,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> ScenarioLease:
        observed_at = _utc(now or utc_now())
        lease = ScenarioLease(
            schema_version="evm.operational_scenario_lease.v1",
            lease_token=uuid.uuid4().hex,
            run_id=run_id,
            owner=owner,
            target=target,
            acquired_at=observed_at,
            expires_at=observed_at + timedelta(seconds=ttl_seconds),
        )
        path = self._path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(lease.model_dump(mode="json"), sort_keys=True).encode("utf-8")
        for _ in range(2):
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                current = ScenarioLease.model_validate_json(path.read_text(encoding="utf-8"))
                if current.expires_at > observed_at:
                    raise LeaseConflict(
                        f"target_lease_held:run={current.run_id},owner={current.owner}"
                    )
                path.unlink(missing_ok=True)
                continue
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            return lease
        raise LeaseConflict("target_lease_race")

    def release(self, lease: ScenarioLease) -> None:
        path = self._path(lease.target)
        if not path.exists():
            return
        current = ScenarioLease.model_validate_json(path.read_text(encoding="utf-8"))
        if current.lease_token != lease.lease_token:
            raise LeaseConflict("lease_token_mismatch")
        path.unlink()


def action_digest(
    *,
    run_id: str,
    action: str,
    target: TargetRef,
    source_revision: str,
) -> str:
    payload = {
        "action": action,
        "run_id": run_id,
        "source_revision": source_revision,
        "target": target.model_dump(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ApprovalBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["evm.operational_approval.v1"]
    approval_id: str
    run_id: str
    target: TargetRef
    action: str
    action_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_revision: str
    approver: str
    issued_at: datetime
    expires_at: datetime
    single_use: Literal[True]

    _validate_issued_at = field_validator("issued_at")(_utc)
    _validate_expires_at = field_validator("expires_at")(_utc)


class ApprovalRejected(RuntimeError):
    pass


class ApprovalStore:
    def __init__(self, root: Path) -> None:
        self.root = root / "approvals"

    def issue(
        self,
        *,
        run_id: str,
        target: TargetRef,
        action: str,
        source_revision: str,
        approver: str,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> ApprovalBinding:
        observed_at = _utc(now or utc_now())
        binding = ApprovalBinding(
            schema_version="evm.operational_approval.v1",
            approval_id=f"approval-{uuid.uuid4().hex}",
            run_id=run_id,
            target=target,
            action=action,
            action_digest=action_digest(
                run_id=run_id,
                action=action,
                target=target,
                source_revision=source_revision,
            ),
            source_revision=source_revision,
            approver=approver,
            issued_at=observed_at,
            expires_at=observed_at + timedelta(seconds=ttl_seconds),
            single_use=True,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{binding.approval_id}.json"
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(binding.model_dump(mode="json"), handle, sort_keys=True, indent=2)
            handle.write("\n")
        return binding

    def consume(
        self,
        approval_id: str,
        *,
        run_id: str,
        target: TargetRef,
        action: str,
        source_revision: str,
        now: datetime | None = None,
    ) -> ApprovalBinding:
        path = self.root / f"{approval_id}.json"
        binding = ApprovalBinding.model_validate_json(path.read_text(encoding="utf-8"))
        observed_at = _utc(now or utc_now())
        expected_digest = action_digest(
            run_id=run_id,
            action=action,
            target=target,
            source_revision=source_revision,
        )
        checks = {
            "run_id": binding.run_id == run_id,
            "target": binding.target == target,
            "action": binding.action == action,
            "action_digest": binding.action_digest == expected_digest,
            "source_revision": binding.source_revision == source_revision,
            "not_expired": binding.expires_at > observed_at,
            "single_use": binding.single_use,
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise ApprovalRejected(f"approval_binding_rejected:{','.join(failed)}")

        receipt = self.root / f"{approval_id}.consumed.json"
        try:
            descriptor = os.open(receipt, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise ApprovalRejected("approval_already_consumed") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                {
                    "approval_id": approval_id,
                    "consumed_at": observed_at.isoformat(),
                    "run_id": run_id,
                    "target_uid": target.uid,
                    "action_digest": expected_digest,
                    "source_revision": source_revision,
                },
                handle,
                sort_keys=True,
                indent=2,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return binding
