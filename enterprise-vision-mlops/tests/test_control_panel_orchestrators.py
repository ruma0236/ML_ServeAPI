from __future__ import annotations

from apps.api.control_panel_orchestrators import http_connection


class Response:
    ok = True
    status_code = 200
    text = "ok"

    @staticmethod
    def json():
        return {
            "metadatabase": {"status": "healthy"},
            "scheduler": {"status": "healthy"},
        }


def test_airflow_health_probe_returns_live_connection(monkeypatch):
    monkeypatch.setattr(
        "apps.api.control_panel_orchestrators.requests.get",
        lambda *_args, **_kwargs: Response(),
    )

    connection = http_connection(
        orchestrator="airflow",
        mode="external-compose",
        control_mode="rest-api",
        base_url="http://airflow:8080/api/v1",
        health_path="/health",
        auth=("admin", "admin"),
        supported_actions=["trigger_dag"],
    )

    assert connection.status == "pass"
    assert connection.blockers == []
    assert connection.supported_actions == ["trigger_dag"]
