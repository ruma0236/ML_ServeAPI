from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.dev import run_s8_v4_x1_calibration as runner
from scripts.dev.run_s8_v4_x1_calibration import (
    X1ExperimentError,
    _iter_otlp_entries,
    canonical_write,
    deterministic_model_schedule,
    remove_prometheus_targets,
    validate_warmup,
)


def test_x1_model_schedule_is_deterministic_and_preserves_frozen_mix() -> None:
    weights = {
        "higgs_logistic_regression": 0.1,
        "higgs_gaussian_nb": 0.1,
        "higgs_tiny_mlp": 0.1,
        "criteo_dlrm_lite": 0.7,
    }
    first = deterministic_model_schedule(weights, 1000)
    second = deterministic_model_schedule(weights, 1000)
    assert first == second
    assert {model_id: first.count(model_id) for model_id in weights} == {
        "higgs_logistic_regression": 100,
        "higgs_gaussian_nb": 100,
        "higgs_tiny_mlp": 100,
        "criteo_dlrm_lite": 700,
    }


def test_x1_warmup_requires_completed_accepted_effect_exact_join() -> None:
    window = {
        "requests": [
            {
                "request_id": "x1-warmup-1",
                "admission_outcome": "accepted",
                "status_code": 200,
                "terminal_outcome": "completed",
                "outcome_unknown": False,
                "oom_detected": False,
            }
        ]
    }
    effects = [{"payload": {"request_id": "x1-warmup-1"}}]
    validate_warmup(window, effects)
    effects[0]["payload"]["request_id"] = "x1-warmup-other"
    with pytest.raises(X1ExperimentError, match="x1_warmup_effect_join"):
        validate_warmup(window, effects)


def test_x1_canonical_write_is_write_once(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    canonical_write(path, {"value": 1})
    assert path.read_bytes() == b'{"value":1}\n'
    with pytest.raises(FileExistsError):
        canonical_write(path, {"value": 2})


def test_x1_otlp_reader_skips_partial_record_at_snapshot_offset(tmp_path: Path) -> None:
    path = tmp_path / "traces.json"
    prefix = b'{"resourceSpans":[]}'
    path.write_bytes(prefix)
    offset = len(prefix)
    batch = {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "scope": {"name": "unit"},
                        "spans": [{"traceId": "a" * 32, "spanId": "b" * 16}],
                    }
                ],
            }
        ]
    }
    with path.open("ab") as handle:
        handle.write(b"\n" + json.dumps(batch).encode("ascii") + b"\n")
    entries = _iter_otlp_entries(path, offset=offset)
    assert len(entries) == 1
    assert entries[0]["span"]["traceId"] == "a" * 32


def test_x1_prometheus_cleanup_drains_target_set_before_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triton = tmp_path / "triton.json"
    api = tmp_path / "api.json"
    triton.write_text('[{"targets":["triton:8002"]}]\n', encoding="ascii")
    api.write_text('[{"targets":["api:8000"]}]\n', encoding="ascii")
    monkeypatch.setattr(runner, "TRITON_TARGET", triton)
    monkeypatch.setattr(runner, "API_TARGET", api)
    reload_snapshots: list[tuple[bytes | None, bytes | None]] = []
    waits: list[bool] = []

    def capture_reload() -> None:
        reload_snapshots.append(
            (
                triton.read_bytes() if triton.exists() else None,
                api.read_bytes() if api.exists() else None,
            )
        )

    def capture_wait(*, present: bool, timeout: float = 60) -> dict[str, object]:
        del timeout
        waits.append(present)
        return {"observed": [], "healthy": []}

    monkeypatch.setattr(runner, "_prometheus_reload", capture_reload)
    monkeypatch.setattr(runner, "wait_x1_prometheus", capture_wait)

    remove_prometheus_targets()

    empty = b'[{"targets":[]}]\n'
    assert reload_snapshots == [(empty, empty), (None, None)]
    assert waits == [False, False]
    assert not triton.exists()
    assert not api.exists()
