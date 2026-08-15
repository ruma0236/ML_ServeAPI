from __future__ import annotations

import asyncio
import time
from collections import Counter
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class DagRunRequest(BaseModel):
    dag_run_id: str = Field(min_length=1, max_length=256)
    conf: dict[str, Any] = Field(default_factory=dict)


app = FastAPI(title="S2 deterministic Airflow-compatible validation fixture")
_LOCK = Lock()
_RUNS: dict[tuple[str, str], dict[str, Any]] = {}
_ATTEMPTS: Counter[tuple[str, str]] = Counter()
_UNIQUE_EFFECTS: Counter[tuple[str, str]] = Counter()
_TERMINAL_AT: dict[tuple[str, str], float] = {}
_TERMINAL_STATE: dict[tuple[str, str], str] = {}
_TASK_EFFECTS: dict[str, set[tuple[str, str]]] = {}
_TRACE_CONTEXT_TASKS: set[str] = set()
_MISSING_TRACE_CONTEXT_TASKS: set[str] = set()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.post("/api/v1/dags/{dag_id}/dagRuns", status_code=200)
async def create_dag_run(dag_id: str, request: DagRunRequest) -> dict[str, Any]:
    key = (dag_id, request.dag_run_id)
    with _LOCK:
        _ATTEMPTS[key] += 1
        attempt = _ATTEMPTS[key]
        existing = _RUNS.get(key)
    if existing is not None:
        raise HTTPException(status_code=409, detail="dag run already exists")

    mode = str(request.conf.get("s2_failure_mode", "healthy"))
    if mode == "permanent":
        raise HTTPException(status_code=422, detail="deterministic permanent failure")
    if mode == "transient_once" and attempt == 1:
        raise HTTPException(status_code=503, detail="deterministic transient failure")
    if mode == "always_transient":
        raise HTTPException(status_code=503, detail="deterministic persistent transient failure")
    delay_seconds = float(request.conf.get("s2_delay_seconds", 0.0) or 0.0)
    if mode == "timeout_once" and attempt == 1:
        await asyncio.sleep(delay_seconds)
        raise HTTPException(status_code=503, detail="deterministic delayed transient failure")
    if mode == "timeout_once":
        delay_seconds = 0.0
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)

    payload = {
        "dag_id": dag_id,
        "dag_run_id": request.dag_run_id,
        "state": "queued",
        "conf": request.conf,
    }
    terminal_state = str(request.conf.get("s2_terminal_state", "queued"))
    terminal_after = float(request.conf.get("s2_terminal_after_seconds", 0.0) or 0.0)
    if terminal_state not in {"queued", "success", "failed"}:
        raise HTTPException(status_code=422, detail="invalid deterministic terminal state")
    with _LOCK:
        if key in _RUNS:
            raise HTTPException(status_code=409, detail="dag run already exists")
        _RUNS[key] = payload
        _UNIQUE_EFFECTS[key] += 1
        task_identity = str(request.conf.get("control_panel_task_id") or "unknown")
        _TASK_EFFECTS.setdefault(task_identity, set()).add(key)
        if request.conf.get("trace_id") and request.conf.get("traceparent"):
            _TRACE_CONTEXT_TASKS.add(task_identity)
        else:
            _MISSING_TRACE_CONTEXT_TASKS.add(task_identity)
        if terminal_state != "queued":
            _TERMINAL_AT[key] = time.monotonic() + terminal_after
            _TERMINAL_STATE[key] = terminal_state
    return _current_run(key)


@app.get("/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}")
def get_dag_run(dag_id: str, dag_run_id: str) -> dict[str, Any]:
    with _LOCK:
        exists = (dag_id, dag_run_id) in _RUNS
    if not exists:
        raise HTTPException(status_code=404, detail="dag run not found")
    return _current_run((dag_id, dag_run_id))


def _current_run(key: tuple[str, str]) -> dict[str, Any]:
    with _LOCK:
        payload = dict(_RUNS[key])
        terminal_at = _TERMINAL_AT.get(key)
        if terminal_at is not None and time.monotonic() >= terminal_at:
            payload["state"] = _TERMINAL_STATE[key]
            _RUNS[key] = dict(payload)
    return payload


@app.get("/evidence")
def evidence() -> dict[str, Any]:
    with _LOCK:
        attempts = sum(_ATTEMPTS.values())
        unique_effects = sum(_UNIQUE_EFFECTS.values())
        duplicate_effects = sum(max(0, count - 1) for count in _UNIQUE_EFFECTS.values())
        runs = len(_RUNS)
        logical_effects = {
            task_id: sorted(f"{dag_id}/{dag_run_id}" for dag_id, dag_run_id in effects)
            for task_id, effects in _TASK_EFFECTS.items()
        }
        tasks_with_multiple_effects = sum(
            1 for effects in _TASK_EFFECTS.values() if len(effects) > 1
        )
        trace_context_tasks = len(_TRACE_CONTEXT_TASKS)
        missing_trace_context_tasks = len(_MISSING_TRACE_CONTEXT_TASKS)
    return {
        "schema_version": "evm.s2_airflow_fixture_evidence.v1",
        "attempts": attempts,
        "runs": runs,
        "unique_external_effects": unique_effects,
        "duplicate_external_effects": duplicate_effects,
        "logical_task_effects": logical_effects,
        "tasks_with_multiple_logical_effects": tasks_with_multiple_effects,
        "trace_context_tasks": trace_context_tasks,
        "missing_trace_context_tasks": missing_trace_context_tasks,
    }


@app.post("/reset")
def reset() -> dict[str, int]:
    with _LOCK:
        _RUNS.clear()
        _ATTEMPTS.clear()
        _UNIQUE_EFFECTS.clear()
        _TERMINAL_AT.clear()
        _TERMINAL_STATE.clear()
        _TASK_EFFECTS.clear()
        _TRACE_CONTEXT_TASKS.clear()
        _MISSING_TRACE_CONTEXT_TASKS.clear()
    return {"runs": 0, "attempts": 0}
