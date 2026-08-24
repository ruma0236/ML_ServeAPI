from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


ALLOWED_STATES = {
    "planned",
    "contract_frozen",
    "ready",
    "running",
    "review_pending",
    "remediation_required",
    "verified",
}


class V4LedgerError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def calculate_event_hash(event: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in event.items() if key != "event_hash"}
    return hashlib.sha256(canonical(payload).encode("ascii")).hexdigest()


def read_events(path: Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise V4LedgerError("v4_ledger_not_canonical_lf")
    events = [json.loads(line) for line in raw.splitlines() if line]
    verify_events(events)
    return events


def verify_events(events: Sequence[Mapping[str, Any]]) -> None:
    previous: str | None = None
    seen: set[str] = set()
    for event in events:
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in seen:
            raise V4LedgerError(f"v4_ledger_event_id:{event_id}")
        seen.add(event_id)
        if event.get("previous_event_hash") != previous:
            raise V4LedgerError(f"v4_ledger_previous_hash:{event_id}")
        observed = str(event.get("event_hash") or "")
        expected = calculate_event_hash(event)
        if observed != expected:
            raise V4LedgerError(f"v4_ledger_event_hash:{event_id}")
        if event.get("to_status") not in ALLOWED_STATES:
            raise V4LedgerError(f"v4_ledger_state:{event_id}")
        previous = observed


def append_event(path: Path, event: dict[str, Any]) -> dict[str, Any]:
    events = read_events(path)
    previous = events[-1]["event_hash"] if events else None
    event["previous_event_hash"] = previous
    event["event_hash"] = calculate_event_hash(event)
    verify_events([*events, event])
    with path.open("ab") as handle:
        handle.write(canonical(event).encode("ascii") + b"\n")
    return event
