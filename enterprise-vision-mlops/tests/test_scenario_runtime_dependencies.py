from __future__ import annotations

from pathlib import Path


def test_scenario_runtime_pins_and_preflights_otel_exporter() -> None:
    requirements = Path(
        "infra/runtime/scenario-transformers/requirements.txt"
    ).read_text(encoding="utf-8")
    startup = Path("scripts/dev/start_scenario_workload_worker.ps1").read_text(
        encoding="utf-8"
    )

    assert "opentelemetry-api==1.44.0" in requirements
    assert "opentelemetry-exporter-otlp-proto-http==1.44.0" in requirements
    assert "opentelemetry-sdk==1.44.0" in requirements
    assert "googleapis-common-protos==1.66.0" in requirements
    assert "protobuf==5.29.6" in requirements
    assert "opentelemetry.exporter.otlp.proto.http.trace_exporter" in startup
