from __future__ import annotations

import asyncio
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
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)

    payload = {
        "dag_id": dag_id,
        "dag_run_id": request.dag_run_id,
        "state": "queued",
        "conf": request.conf,
    }
    with _LOCK:
        if key in _RUNS:
            raise HTTPException(status_code=409, detail="dag run already exists")
        _RUNS[key] = payload
        _UNIQUE_EFFECTS[key] += 1
    return payload


@app.get("/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}")
def get_dag_run(dag_id: str, dag_run_id: str) -> dict[str, Any]:
    with _LOCK:
        payload = _RUNS.get((dag_id, dag_run_id))
    if payload is None:
        raise HTTPException(status_code=404, detail="dag run not found")
    return dict(payload)


@app.get("/evidence")
def evidence() -> dict[str, Any]:
    with _LOCK:
        attempts = sum(_ATTEMPTS.values())
        unique_effects = sum(_UNIQUE_EFFECTS.values())
        duplicate_effects = sum(max(0, count - 1) for count in _UNIQUE_EFFECTS.values())
        runs = len(_RUNS)
    return {
        "schema_version": "evm.s2_airflow_fixture_evidence.v1",
        "attempts": attempts,
        "runs": runs,
        "unique_external_effects": unique_effects,
        "duplicate_external_effects": duplicate_effects,
    }


@app.post("/reset")
def reset() -> dict[str, int]:
    with _LOCK:
        _RUNS.clear()
        _ATTEMPTS.clear()
        _UNIQUE_EFFECTS.clear()
    return {"runs": 0, "attempts": 0}
