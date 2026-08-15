from __future__ import annotations

from fastapi.testclient import TestClient

from evm.scale_validation.s2_airflow_fixture import app


def test_fixture_is_deterministic_and_counts_one_external_effect():
    client = TestClient(app)
    client.post("/reset")
    payload = {
        "dag_run_id": "run-one",
        "conf": {"s2_failure_mode": "transient_once"},
    }

    assert client.post("/api/v1/dags/dag-one/dagRuns", json=payload).status_code == 503
    assert client.post("/api/v1/dags/dag-one/dagRuns", json=payload).status_code == 200
    assert client.post("/api/v1/dags/dag-one/dagRuns", json=payload).status_code == 409
    assert client.get("/api/v1/dags/dag-one/dagRuns/run-one").status_code == 200
    evidence = client.get("/evidence").json()
    assert evidence["attempts"] == 3
    assert evidence["unique_external_effects"] == 1
    assert evidence["duplicate_external_effects"] == 0
