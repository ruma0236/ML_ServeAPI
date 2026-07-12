from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from evm.control_panel.readiness_evaluator import runtime_path
from evm.control_panel.schemas import (
    DecisionRecord,
    DecisionRecordList,
    DecisionRecordRequest,
    DecisionState,
    DecisionTransition,
    DecisionTransitionRequest,
)


DEFAULT_DECISION_ROOT = (
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/governance/decisions"
)
ALLOWED_TRANSITIONS: dict[DecisionState, set[DecisionState]] = {
    "draft": {"review"},
    "review": {"approved", "rejected"},
    "approved": set(),
    "rejected": {"draft"},
}
_REGISTRY_LOCK = RLock()


class DecisionNotFound(RuntimeError):
    pass


class DecisionVersionConflict(RuntimeError):
    pass


class DecisionTransitionRejected(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def decision_root() -> Path:
    return runtime_path(os.getenv("EVM_DECISION_REGISTRY_ROOT", DEFAULT_DECISION_ROOT))


def registry_path() -> Path:
    return decision_root() / "decision_registry.json"


def read_decisions() -> DecisionRecordList:
    path = registry_path()
    if not path.exists():
        return DecisionRecordList()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DecisionRecordList(status="blocked", blockers=["decision_registry_malformed"])
    if not isinstance(payload, list):
        return DecisionRecordList(status="blocked", blockers=["decision_registry_not_array"])
    try:
        return DecisionRecordList(
            decisions=[DecisionRecord.model_validate(item) for item in payload]
        )
    except ValueError:
        return DecisionRecordList(status="blocked", blockers=["decision_registry_schema_invalid"])


def write_decisions(registry: DecisionRecordList) -> None:
    if registry.status != "pass" or registry.blockers:
        raise DecisionTransitionRejected("decision_registry_not_writable")
    atomic_write_json(
        registry_path(),
        [item.model_dump(mode="json") for item in registry.decisions],
    )


def create_decision(request: DecisionRecordRequest) -> DecisionRecord:
    with _REGISTRY_LOCK:
        registry = read_decisions()
        if registry.status != "pass":
            raise DecisionTransitionRejected(",".join(registry.blockers))
        timestamp = utc_now()
        decision = DecisionRecord(
            **request.model_dump(),
            decision_id=f"decision-{timestamp[:10].replace('-', '')}-{uuid4().hex[:10]}",
            state="draft",
            version=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
        registry.decisions.insert(0, decision)
        write_decisions(registry)
        persist_record(decision)
        return decision


def transition_decision(
    decision_id: str,
    request: DecisionTransitionRequest,
) -> DecisionRecord:
    with _REGISTRY_LOCK:
        registry = read_decisions()
        if registry.status != "pass":
            raise DecisionTransitionRejected(",".join(registry.blockers))
        index = next(
            (i for i, item in enumerate(registry.decisions) if item.decision_id == decision_id),
            None,
        )
        if index is None:
            raise DecisionNotFound(decision_id)
        current = registry.decisions[index]
        if current.version != request.expected_version:
            raise DecisionVersionConflict(
                f"expected={request.expected_version} actual={current.version}"
            )
        if request.target_state not in ALLOWED_TRANSITIONS[current.state]:
            raise DecisionTransitionRejected(
                f"transition {current.state}->{request.target_state} is not allowed"
            )
        if request.target_state == "approved" and request.actor == current.owner:
            raise DecisionTransitionRejected("approval_requires_separation_of_duties")
        timestamp = utc_now()
        transition = DecisionTransition(
            from_state=current.state,
            to_state=request.target_state,
            actor=request.actor,
            reason=request.reason,
            timestamp=timestamp,
        )
        updated = current.model_copy(
            update={
                "state": request.target_state,
                "version": current.version + 1,
                "updated_at": timestamp,
                "transitions": [*current.transitions, transition],
            }
        )
        registry.decisions[index] = updated
        write_decisions(registry)
        persist_record(updated)
        append_jsonl(
            decision_root() / "decision_events.jsonl",
            {"decision_id": decision_id, **transition.model_dump(mode="json")},
        )
        return updated


def persist_record(record: DecisionRecord) -> None:
    atomic_write_json(
        decision_root() / record.decision_id / "decision.json",
        record.model_dump(mode="json"),
    )


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(payload, sort_keys=True) + "\n")
