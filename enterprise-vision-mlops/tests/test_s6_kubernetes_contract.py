from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_canonical_api_manifest_declares_zero_unavailable_drain_contract() -> None:
    payload = (ROOT / "infra/kubernetes/local/api.yaml").read_text(encoding="utf-8")

    assert "replicas: 2" in payload
    assert "type: RollingUpdate" in payload
    assert "maxUnavailable: 0" in payload
    assert "maxSurge: 1" in payload
    assert "startupProbe:" in payload
    assert "readinessProbe:" in payload
    assert "livenessProbe:" in payload
    assert "terminationGracePeriodSeconds: 40" in payload
    assert "evm.control_panel.api_rollout" in payload


def test_isolated_s6_manifest_preserves_existing_api_and_serving_names() -> None:
    payload = (
        ROOT / "infra/kubernetes/scale-validation/s6/api-rolling.yaml"
    ).read_text(encoding="utf-8")

    assert "name: evm-s6-api" in payload
    assert "nodePort: 31060" in payload
    assert "name: evm-s6-control-plane" in payload
    assert "value: evm_s6_api" in payload
    assert "app.kubernetes.io/name: evm-api\n" not in payload
    assert "evm-b0-production" not in payload
