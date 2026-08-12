from __future__ import annotations

from types import SimpleNamespace

from evm.control_panel import scenario_workload_worker as worker


def test_applied_production_reconciliation_returns_worker_heartbeat_to_online(monkeypatch) -> None:
    intent = SimpleNamespace(intent_id="scenario-deploy-1", state="applied")
    heartbeats: list[dict[str, object]] = []

    monkeypatch.setattr(worker, "recover_incomplete_production_intents", lambda: [])
    monkeypatch.setattr(worker, "current_production_intent", lambda: intent)
    monkeypatch.setattr(worker, "reconcile_applied_intent", lambda value: value)
    monkeypatch.setattr(worker, "queued_intent_ids", lambda: [])
    monkeypatch.setattr(worker, "write_heartbeat", lambda **values: heartbeats.append(values))

    assert worker.run_worker(once=True, poll_interval=0.5, worker_id="worker-test") == 0
    assert heartbeats[-2]["current_intent_id"] == intent.intent_id
    assert heartbeats[-1]["current_intent_id"] is None
