from __future__ import annotations

import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import requests

from evm.model_runtime.x1_serving import X1InferenceRequest
from scripts.dev import run_s8_v4_x1_calibration as runner
from scripts.dev.run_s8_v4_x1_calibration import (
    X1ExperimentError,
    _iter_otlp_entries,
    canonical_attempt_id,
    canonical_write,
    deterministic_model_schedule,
    remove_prometheus_targets,
    validate_and_persist_warmup,
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


def test_x1_runtime_attempt_id_matches_api_schema() -> None:
    attempt_id = canonical_attempt_id(
        "solo_calibration-r1-w1-higgs_logistic_regression-disabled-rep1"
    )
    request_id = f"{attempt_id}-w00-00000000"
    request = X1InferenceRequest(
        schema_version="evm.s8_v4.x1_inference_request.v1",
        suite_id="x1-canonical-20260830t001821z-12bffcd6",
        attempt_id=f"{attempt_id}-warmup-00",
        request_id=request_id,
        traceparent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
        model_id="higgs_logistic_regression",
        model_version="1",
        artifact_sha256="c" * 64,
        config_sha256="d" * 64,
        features=[0.0] * 28,
        deadline_unix_ns=1,
        lease_id="lease-id",
        fencing_token="fencing-token-123",
    )

    assert "_" not in attempt_id
    assert request.attempt_id.endswith("-warmup-00")


def test_x1_failure_detail_preserves_bounded_private_rca_message() -> None:
    payload = {
        "detail": {
            "error": "x1_durable_effect_commit_failed",
            "message": "post-commit readback failed",
        }
    }

    assert runner._response_error(payload) == "x1_durable_effect_commit_failed"
    assert runner._response_failure_detail(payload) == "post-commit readback failed"
    payload["detail"]["message"] = "x" * 600
    assert runner._response_failure_detail(payload) == "x" * 500


def test_x1_transport_failure_preserves_bounded_private_rca_message() -> None:
    message = "connection reset: " + ("x" * 600)
    headers: dict[str, str] = {}

    class FailingSession:
        def post(self, *_args: object, **kwargs: object) -> None:
            headers.update(kwargs["headers"])
            raise requests.ConnectionError(message)

    class SessionPool:
        def current(self) -> FailingSession:
            return FailingSession()

    result = runner._send_request(
        suite_id="x1-canonical-test",
        runtime_attempt_id="x1-solo-calibration-test",
        request_id="x1-solo-calibration-test-s00-00000000",
        model_id="higgs_logistic_regression",
        features=[0.0] * 28,
        identity={"artifact_sha256": "a" * 64, "config_sha256": "b" * 64},
        lease=SimpleNamespace(lease_id="lease-id", fencing_token="fencing-token"),
        enqueued_ns=runner.time.perf_counter_ns(),
        deadline_seconds=1.0,
        session_pool=SessionPool(),
    )

    assert result["failure_reason"] == "ConnectionError"
    assert result["failure_detail"] == message[:500]
    assert result["outcome_unknown"] is True
    assert set(headers) == {"traceparent"}


def test_x1_window_http_sessions_are_thread_local_reused_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[object] = []

    class Session:
        trust_env = True

        def __init__(self) -> None:
            self.closed = False
            self.mounts: list[tuple[str, object]] = []
            created.append(self)

        def mount(self, prefix: str, adapter: object) -> None:
            self.mounts.append((prefix, adapter))

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(runner.requests, "Session", Session)
    pool = runner._ThreadLocalHttpSessions()
    barrier = threading.Barrier(4)

    def use_session() -> tuple[int, int]:
        first = pool.current()
        barrier.wait(timeout=2)
        second = pool.current()
        return id(first), id(second)

    with runner.concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        identities = list(executor.map(lambda _index: use_session(), range(4)))
    pool.close()

    assert len(created) == 4
    assert len({first for first, _second in identities}) == 4
    assert all(first == second for first, second in identities)
    assert all(session.trust_env is False for session in created)
    assert all(
        [prefix for prefix, _adapter in session.mounts] == ["http://", "https://"]
        for session in created
    )
    assert all(
        all(adapter.max_retries.total == 0 for _prefix, adapter in session.mounts)
        for session in created
    )
    assert all(
        all(
            adapter.poolmanager.connection_pool_kw["maxsize"] == 1
            and adapter.poolmanager.connection_pool_kw["block"] is True
            for _prefix, adapter in session.mounts
        )
        for session in created
    )
    assert all(session.closed for session in created)


def test_x1_failed_warmup_is_preserved_before_rejection(tmp_path: Path) -> None:
    window = {
        "phase": "warmup",
        "requests": [
            {
                "request_id": "x1-attempt-w00-00000000",
                "admission_outcome": "accepted",
                "status_code": 422,
                "terminal_outcome": "failed",
                "outcome_unknown": False,
                "oom_detected": False,
            }
        ],
    }
    attempt_id = "x1-attempt-warmup-00"

    with pytest.raises(X1ExperimentError, match="x1_warmup_terminal_invariant"):
        validate_and_persist_warmup(
            suite_root=tmp_path,
            warmup_attempt_id=attempt_id,
            window=window,
            effects=[],
        )

    preserved = json.loads((tmp_path / "failed-warmups" / f"{attempt_id}.json").read_text())
    assert preserved == {**window, "durable_effects": []}


def test_x1_canonical_write_is_write_once(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    canonical_write(path, {"value": 1})
    assert path.read_bytes() == b'{"value":1}\n'
    with pytest.raises(FileExistsError):
        canonical_write(path, {"value": 2})


def test_x1_raw_calibration_is_preserved_before_projection_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = {"attempt_id": "x1-calibration-attempt", "steps": [{"offered_rps": 25}]}

    def fail_projection(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("projection-failed")

    monkeypatch.setattr(runner, "project_calibration_attempt", fail_projection)
    with pytest.raises(RuntimeError, match="projection-failed"):
        runner._persist_calibration_result(
            suite_root=tmp_path,
            mode="solo_calibration",
            cell_id="solo-cell-rep1",
            raw=raw,
            contract=object(),  # type: ignore[arg-type]
            batch_candidate=None,
        )

    raw_path = tmp_path / "calibration/solo_calibration/solo-cell-rep1.json"
    assert json.loads(raw_path.read_text(encoding="utf-8")) == raw
    assert not (tmp_path / "projections/solo_calibration/solo-cell-rep1.json").exists()


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


def test_x1_runner_repository_index_accepts_omitted_empty_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, str]]:
            return [
                {"name": model_id, "version": "1", "state": "READY"}
                for model_id in runner.MODEL_IDS
            ]

    monkeypatch.setattr(runner.requests, "post", lambda *args, **kwargs: Response())
    records = runner.repository_index()
    assert len(records) == 4
    assert all(record["reason"] == "" for record in records)


@pytest.mark.parametrize("mutation", ["nonempty_reason", "extra_key"])
def test_x1_runner_repository_index_rejects_optional_field_mutation(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, str]]:
            records = [
                {"name": model_id, "version": "1", "state": "READY"}
                for model_id in runner.MODEL_IDS
            ]
            records[0]["reason" if mutation == "nonempty_reason" else "extra"] = "invalid"
            return records

    monkeypatch.setattr(runner.requests, "post", lambda *args, **kwargs: Response())
    expected = (
        "x1_repository_index_reason"
        if mutation == "nonempty_reason"
        else "x1_repository_index_record"
    )
    with pytest.raises(X1ExperimentError, match=expected):
        runner.repository_index()


def test_x1_q0_reuses_one_bounded_no_proxy_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions: list[object] = []
    observed_sessions: list[object] = []

    class Session:
        trust_env = True

        def __enter__(self) -> Session:
            sessions.append(self)
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def direct_infer(
        model_id: str,
        features: list[float],
        *,
        request_id: str,
        session: object,
    ) -> float:
        assert model_id == "model"
        assert features == [1.0]
        assert request_id.startswith("suite-q0-model-")
        observed_sessions.append(session)
        return 1.0

    monkeypatch.setattr(runner, "MODEL_IDS", ("model",))
    monkeypatch.setattr(runner.requests, "Session", Session)
    monkeypatch.setattr(runner, "repository_index", lambda: [])
    monkeypatch.setattr(
        runner,
        "_oracle",
        lambda *args: {
            "input": [[1.0]] * 64,
            "output": [[1.0]] * 64,
            "relative_tolerance": 0.0,
            "absolute_tolerance": 0.0,
        },
    )
    monkeypatch.setattr(runner, "triton_config_readback", lambda model_id: {})
    monkeypatch.setattr(runner, "validate_triton_runtime_config", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "triton_metrics_text", lambda: "")
    monkeypatch.setattr(
        runner,
        "capture_gpu",
        lambda: {"uuid": runner.GPU_UUID, "name": "gpu"},
    )
    monkeypatch.setattr(
        runner,
        "_metric_delta",
        lambda *args: {
            "success_count": 64,
            "inference_count": 64,
            "execution_count": 64,
            "compute_duration_us": 1.0,
        },
    )
    monkeypatch.setattr(runner, "sha256_file", lambda path: "a" * 64)
    monkeypatch.setattr(runner, "validate_q0_bundle", lambda *args: {"passed": True})
    monkeypatch.setattr(runner, "_direct_infer", direct_infer)
    manifest = {"models": {"model": {"artifact_sha256": "b" * 64}}}

    bundle = runner.run_q0(
        object(),
        suite_id="suite",
        artifact_root=tmp_path,
        artifact_manifest=manifest,
    )

    assert bundle["projection"] == {"passed": True}
    assert len(sessions) == 1
    assert sessions[0].trust_env is False
    assert len(observed_sessions) == 64
    assert all(session is sessions[0] for session in observed_sessions)


def test_x1_q0_transport_failure_is_contextual_and_fail_closed() -> None:
    class Session:
        def post(self, *args: object, **kwargs: object) -> None:
            raise requests.ReadTimeout("bounded timeout")

    with pytest.raises(
        X1ExperimentError,
        match="x1_q0_transport:higgs_gaussian_nb:request-7:ReadTimeout",
    ):
        runner._direct_infer(
            "higgs_gaussian_nb",
            [1.0],
            request_id="request-7",
            session=Session(),
        )


def test_x1_q0_transport_failure_writes_non_credit_diagnostic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Session:
        trust_env = True

        def __enter__(self) -> Session:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(runner, "MODEL_IDS", ("model",))
    monkeypatch.setattr(runner.requests, "Session", Session)
    monkeypatch.setattr(runner, "repository_index", lambda: [])
    monkeypatch.setattr(
        runner,
        "_oracle",
        lambda *args: {
            "input": [[1.0]],
            "output": [[1.0]],
            "relative_tolerance": 0.0,
            "absolute_tolerance": 0.0,
        },
    )
    monkeypatch.setattr(runner, "triton_config_readback", lambda model_id: {})
    monkeypatch.setattr(runner, "validate_triton_runtime_config", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "triton_metrics_text", lambda: "")
    monkeypatch.setattr(runner, "capture_gpu", lambda: {"uuid": runner.GPU_UUID})

    def fail_transport(*args: object, **kwargs: object) -> float:
        raise X1ExperimentError("x1_q0_transport:model:suite-q0-model-0000:ReadTimeout")

    captured: dict[str, object] = {}

    def diagnostic(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "schema_version": "evm.s8_v4.x1_q0_transport_diagnostic.v1",
            "credit": "zero_credit",
            "acceptance_credit": False,
        }

    monkeypatch.setattr(runner, "_direct_infer", fail_transport)
    monkeypatch.setattr(runner, "capture_q0_transport_diagnostic", diagnostic)
    artifact_root = tmp_path / "artifacts"
    manifest = {"models": {"model": {"artifact_sha256": "b" * 64}}}

    with pytest.raises(X1ExperimentError, match="x1_q0_transport"):
        runner.run_q0(
            object(),
            suite_id="suite",
            artifact_root=artifact_root,
            artifact_manifest=manifest,
        )

    written = json.loads((tmp_path / "q0-transport-diagnostic.json").read_text())
    assert written["credit"] == "zero_credit"
    assert written["acceptance_credit"] is False
    assert captured["request_id"] == "suite-q0-model-0000"
    assert captured["features"] == [1.0]


def test_x1_q0_infer_payload_is_versioned_and_identity_bound() -> None:
    assert runner._direct_infer_payload([1.0, 2.0], request_id="request-9") == {
        "id": "request-9",
        "inputs": [
            {
                "name": "INPUT__0",
                "shape": [2],
                "datatype": "FP32",
                "data": [1.0, 2.0],
            }
        ],
        "outputs": [{"name": "OUTPUT__0"}],
    }
