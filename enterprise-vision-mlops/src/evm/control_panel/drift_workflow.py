from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from evm.control_panel.readiness_evaluator import canonical_evidence_uri, runtime_path
from evm.control_panel.schemas import (
    DriftReviewStatus,
    DriftReviewTransition,
    DriftReviewTransitionRequest,
    DriftReviewWorkflow,
)


DEFAULT_DRIFT_ROOT = (
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/drift_review"
)
ALLOWED_TRANSITIONS: dict[DriftReviewStatus, set[DriftReviewStatus]] = {
    "open": {"acknowledged"},
    "acknowledged": {"approved"},
    "approved": {"closed"},
    "closed": set(),
}
_WORKFLOW_LOCK = RLock()


class DriftWorkflowNotFound(RuntimeError):
    pass


class DriftWorkflowConflict(RuntimeError):
    pass


class DriftWorkflowRejected(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def drift_root() -> Path:
    return runtime_path(os.getenv("EVM_DRIFT_REVIEW_ROOT", DEFAULT_DRIFT_ROOT))


def latest_event_path() -> Path:
    configured = os.getenv("EVM_DRIFT_REVIEW_EVENT_PATH")
    return runtime_path(configured) if configured else drift_root() / "latest_review_event.json"


def load_latest_drift_workflow() -> DriftReviewWorkflow:
    path = latest_event_path()
    if not path.exists():
        raise DriftWorkflowNotFound(str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DriftWorkflowNotFound(str(path)) from exc
    if not isinstance(payload, dict) or not payload.get("event_id"):
        raise DriftWorkflowNotFound(str(path))
    return workflow_from_event(payload)


def workflow_from_event(payload: dict[str, Any]) -> DriftReviewWorkflow:
    status = str(payload.get("status") or "open")
    if status not in ALLOWED_TRANSITIONS:
        status = "open"
    transitions = [
        DriftReviewTransition.model_validate(item)
        for item in payload.get("workflow_transitions", [])
        if isinstance(item, dict)
    ]
    root = drift_root()
    return DriftReviewWorkflow(
        schema_version="evm.drift_review.workflow.v1",
        event_id=str(payload.get("event_id")),
        event_type=str(payload.get("event_type") or "review_required"),
        status=status,  # type: ignore[arg-type]
        candidate_id=str(payload.get("candidate_id") or ""),
        dataset_version=str(payload.get("dataset_version") or ""),
        triggered_rules=[str(item) for item in payload.get("triggered_rules", [])],
        review_queue_count=review_queue_count(payload),
        evidence_uri=str(payload.get("evidence_uri") or "") or None,
        label_review_queue_uri=str(payload.get("label_review_queue_uri") or "") or None,
        approval_required=bool(payload.get("approval_required", True)),
        automatic_retraining=False,
        automatic_deployment=False,
        automatic_promotion=False,
        next_actions=sorted(ALLOWED_TRANSITIONS[status]),  # type: ignore[index]
        transitions=transitions,
        updated_at=str(payload.get("updated_at") or payload.get("created_at") or "") or None,
        audit_uri=canonical_evidence_uri(
            root / str(payload.get("event_id")) / "workflow.json"
        ),
    )


def review_queue_count(event: dict[str, Any]) -> int:
    report_path = drift_root() / "latest_drift_report.json"
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            return int(report.get("review_queue_count") or 0)
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    return int(event.get("review_queue_count") or 0)


def transition_drift_review(
    event_id: str,
    request: DriftReviewTransitionRequest,
) -> DriftReviewWorkflow:
    with _WORKFLOW_LOCK:
        path = latest_event_path()
        workflow = load_latest_drift_workflow()
        if workflow.event_id != event_id:
            raise DriftWorkflowNotFound(event_id)
        if workflow.status != request.expected_status:
            raise DriftWorkflowConflict(
                f"expected={request.expected_status} actual={workflow.status}"
            )
        allowed = ALLOWED_TRANSITIONS[workflow.status]
        if request.target_status not in allowed:
            raise DriftWorkflowRejected(
                f"transition {workflow.status}->{request.target_status} is not allowed"
            )
        if request.target_status == "approved":
            acknowledged_by = next(
                (
                    item.actor
                    for item in reversed(workflow.transitions)
                    if item.to_status == "acknowledged"
                ),
                None,
            )
            if acknowledged_by == request.actor:
                raise DriftWorkflowRejected("approval_requires_separation_of_duties")
        if request.dry_run:
            return workflow.model_copy(
                update={"dry_run": True, "projected_status": request.target_status}
            )

        payload = json.loads(path.read_text(encoding="utf-8"))
        timestamp = utc_now()
        transition = DriftReviewTransition(
            from_status=workflow.status,
            to_status=request.target_status,
            actor=request.actor,
            reason=request.reason,
            timestamp=timestamp,
        )
        transitions = [*workflow.transitions, transition]
        payload.update(
            {
                "status": request.target_status,
                "approval_state": approval_state(request.target_status),
                "updated_at": timestamp,
                "updated_by": request.actor,
                "automatic_retraining": False,
                "automatic_deployment": False,
                "automatic_promotion": False,
                "workflow_transitions": [item.model_dump(mode="json") for item in transitions],
            }
        )
        atomic_write_json(path, payload)
        root = drift_root()
        workflow_path = root / event_id / "workflow.json"
        updated = workflow_from_event(payload)
        atomic_write_json(workflow_path, updated.model_dump(mode="json"))
        append_jsonl(
            root / "workflow_events.jsonl",
            {"event_id": event_id, **transition.model_dump(mode="json")},
        )
        return updated


def approval_state(status: DriftReviewStatus) -> str:
    return {
        "open": "pending",
        "acknowledged": "in_review",
        "approved": "approved",
        "closed": "closed",
    }[status]


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(payload, sort_keys=True) + "\n")
