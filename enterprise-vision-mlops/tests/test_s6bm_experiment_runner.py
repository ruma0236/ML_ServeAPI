from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from evm.scale_validation import s6bm_trace_receipt_collector as trace_collector


ROOT = Path(__file__).resolve().parents[1]
GIT_ROOT = ROOT.parent
RUNNER = ROOT / "scripts/dev/run_s8_v4_s6bm_experiment.py"
VALIDATOR = ROOT / "scripts/dev/validate_s8_v4_s6bm.py"
REVIEW_WRITER = ROOT / "scripts/dev/write_s8_v4_s6bm_review.py"
CONTINUITY_VALIDATOR = ROOT / "scripts/dev/validate_s8_v4_s6bm_continuity_qualification.py"
STRICT_V4_VALIDATOR = ROOT / "scripts/dev/validate_s8_v4_s6bm_strict_v4_qualification.py"
CONFIG = ROOT / "configs/s8_v4_s6bm_blue_green_v1.toml"


def load_runner():
    spec = importlib.util.spec_from_file_location("s6bm_experiment_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_continuity_validator():
    spec = importlib.util.spec_from_file_location("s6bm_continuity_validator", CONTINUITY_VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_strict_v4_validator():
    spec = importlib.util.spec_from_file_location("s6bm_strict_v4_validator", STRICT_V4_VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_strict_v4_nonminimum_mutation_uses_actual_nonminimum_candidate() -> None:
    validator = load_strict_v4_validator()
    candidates = [
        {
            "sequence": 1,
            "monotonic_before_ns": 100,
            "monotonic_after_ns": 1_100,
            "database_clock_timestamp": "2026-08-29T00:00:00Z",
        },
        {
            "sequence": 2,
            "monotonic_before_ns": 2_000,
            "monotonic_after_ns": 2_100,
            "database_clock_timestamp": "2026-08-29T00:00:01Z",
        },
    ]
    raw = {
        "request_records": [
            {
                "durable_effect": {
                    "database_clock_anchor_candidates": candidates,
                    "database_clock_anchor": copy.deepcopy(candidates[1]),
                    "database_clock_anchor_selection": {
                        "selected_sequence": 2,
                    },
                    "commit_timestamp_observed_at": candidates[1]["database_clock_timestamp"],
                }
            }
        ]
    }

    validator._candidate_nonminimum(Path(), raw)

    receipt = raw["request_records"][0]["durable_effect"]
    assert receipt["database_clock_anchor_selection"]["selected_sequence"] == 1
    assert receipt["database_clock_anchor"] == candidates[0]
    assert receipt["commit_timestamp_observed_at"] == candidates[0]["database_clock_timestamp"]


def test_strict_v4_unload_mutation_preserves_anchor_order() -> None:
    validator = load_strict_v4_validator()

    def anchor(sequence: int, before: int, phase: str) -> dict[str, object]:
        return {
            "sequence": sequence,
            "phase": phase,
            "monotonic_before_ns": before,
            "monotonic_after_ns": before + 100,
            "unix_ns": before + 1_000_000_000,
            "previous_anchor_hash": None,
            "anchor_hash": "",
        }

    raw = {
        "request_records": [{"completed_monotonic": 10.001}],
        "phase_timeline": [
            {
                "phase": "blue_draining",
                "monotonic_seconds": 10.0,
                "clock_anchor": anchor(1, 10_000_000_000, "blue_draining"),
            },
            {
                "phase": "green_only",
                "monotonic_seconds": 11.0,
                "clock_anchor": anchor(2, 11_000_000_000, "green_only"),
            },
            {
                "phase": "rolled_back",
                "monotonic_seconds": 12.0,
                "clock_anchor": anchor(3, 12_000_000_000, "rolled_back"),
            },
        ],
    }
    validator._rehash_runner_anchor_chain(raw)

    validator._unload_before_last_effect(Path(), raw)

    green = raw["phase_timeline"][1]
    green_ns = int(float(green["monotonic_seconds"]) * 1_000_000_000)
    completion_ns = int(10.001 * 1_000_000_000)
    assert raw["phase_timeline"][0]["clock_anchor"]["monotonic_after_ns"] < green_ns
    assert green_ns < completion_ns
    assert (
        green["clock_anchor"]["monotonic_before_ns"]
        < (raw["phase_timeline"][2]["clock_anchor"]["monotonic_before_ns"])
    )


def test_continuity_mutation_contract_is_exact_and_frozen() -> None:
    validator = load_continuity_validator()
    assert len(validator.CASE_CONTRACT) == len(validator.MUTATIONS) == 24
    assert len({case_id for case_id, _reason in validator.CASE_CONTRACT}) == 24
    assert validator.canonical_sha256(validator.CASE_CONTRACT) == (
        "9613d11a2f68b801780d88fd9fe7197ce48685a5879e3e47c45d21c00a432500"
    )
    assert validator.canonical_sha256(validator.SUPERSEDED_CASE_CONTRACT) == (
        "d75b6bbc0e396151dc581b05b86fffc5f59ae4681f34a42b60e560f99c85d886"
    )
    assert validator.canonical_sha256(validator.PREVIOUS_CASE_CONTRACT) == (
        "230ff21035dace5eb498d649d69a1bb063c377c95808d8d9bcae5903edd13a6e"
    )
    assert validator.canonical_sha256(validator.HISTORICAL_CASE_CONTRACT) == (
        "c42a6245d1e48152d06c6f1bd31c7fca8de58f201129c99bb7c59abe9356d4d7"
    )


def test_all_bridge_requests_are_pinned_to_the_observed_blue_generation() -> None:
    runner = load_runner()
    config = runner.S6BMConfig.from_path(ROOT / "configs/s8_v4_s6bm_blue_green_v4.toml")
    plan = runner.build_continuity_plan(config, "s6bm-success-generation-unit")
    bodies = runner.request_bodies_from_plan(
        config,
        SimpleNamespace(lease_id="lease-unit", fencing_token="fence-unit"),
        "s6bm-run-generation-unit",
        "s6bm-success-generation-unit",
        plan["roles"]["bridge"],
        route_generation=7,
    )

    assert len(bodies) == 40
    assert {body["expected_route_generation"] for body in bodies} == {7}
    assert {body["lease_id"] for body in bodies} == {"lease-unit"}
    assert {body["fencing_token"] for body in bodies} == {"fence-unit"}


def test_continuity_mutation_rebinds_switch_event_and_readback_chain(tmp_path: Path) -> None:
    validator = load_continuity_validator()
    request_id = "bridge-0001"
    hold_id = "hold-0001"
    stage = "api_server_handler_entry"
    start_payload = {"actor_start_unix_ns": 100}
    start_event = {
        "event_type": stage,
        "request_id": request_id,
        "causal_sequence": 1,
        "transaction_id": "1001",
        "payload": start_payload,
        "payload_sha256": validator.canonical_sha256(start_payload),
    }
    switch_payload = {
        "schema_version": "evm.s6bm.route_switch_fence.v2",
        "transition_id": "1" * 64,
        "fence_id": "2" * 64,
        "old_route_generation": 2,
        "new_route_generation": 3,
        "source_payload_sha256": "3" * 64,
        "cell_id": "attempt-unit",
        "replica_id": "replica-unit",
        "pending_crossover_request_ids": [hold_id, request_id],
        "continuity_receipt_sequences": {request_id: {stage: 1}},
        "continuity_receipt_payload_sha256": {request_id: {stage: start_event["payload_sha256"]}},
        "continuity_receipt_transaction_ids": {request_id: {stage: "1001"}},
    }
    switch_event = {
        "event_type": "blue_to_green_switch_commit",
        "attempt_id": "attempt-unit",
        "run_id": "run-unit",
        "request_id": hold_id,
        "causal_sequence": 2,
        "transaction_id": "1002",
        "database_recorded_at": "2026-08-25T00:00:00Z",
        "payload": switch_payload,
        "payload_sha256": validator.canonical_sha256(switch_payload),
    }
    observed = validator.observed_transition_from_switch(switch_event)
    effect_events = []
    effects = []
    records = []
    for sequence, crossover_id in enumerate((hold_id, request_id), start=3):
        event_payload = {
            "requires_switch_before_effect": True,
            "observed_transition": copy.deepcopy(observed),
        }
        effect_event = {
            "event_type": "durable_terminal_effect_commit",
            "request_id": crossover_id,
            "causal_sequence": sequence,
            "transaction_id": str(1000 + sequence),
            "payload": event_payload,
            "payload_sha256": validator.canonical_sha256(event_payload),
        }
        stored = {
            "observed_transition": copy.deepcopy(observed),
            "durable_commit": {
                "observed_transition": copy.deepcopy(observed),
                "causal_payload_sha256": effect_event["payload_sha256"],
            },
        }
        effect_events.append(effect_event)
        effects.append({"idempotency_key": crossover_id, "payload": stored})
        records.append(
            {
                "request_id": crossover_id,
                "durable_effect": {
                    "observed_transition": copy.deepcopy(observed),
                    "causal_payload_sha256": effect_event["payload_sha256"],
                    "stored_payload_sha256": validator.canonical_sha256(stored),
                },
            }
        )
    causal_path = tmp_path / "causal-events.json"
    validator.canonical_write(
        causal_path,
        {
            "event_count": 4,
            "events": [start_event, switch_event, *effect_events],
        },
    )
    effects_path = tmp_path / "durable-effects.json"
    validator.canonical_write(
        effects_path,
        {"effect_count": 2, "effects": effects},
    )
    causal_reference = {"path": causal_path.name}
    validator.refresh(causal_reference, causal_path)
    effects_reference = {"path": effects_path.name}
    validator.refresh(effects_reference, effects_path)
    fence_receipt = {
        "payload": copy.deepcopy(switch_payload),
        "payload_sha256": switch_event["payload_sha256"],
        "fence_payload_sha256": switch_event["payload_sha256"],
    }
    raw = {
        "causal_proof": {
            "causal_event_export": causal_reference,
            "durable_effect_export": effects_reference,
            "route_transition_receipt": {
                "fence_receipt": fence_receipt,
                "fence_receipt_sha256": validator.canonical_sha256(fence_receipt),
                "fence_payload_sha256": switch_event["payload_sha256"],
            },
        },
        "request_records": records,
    }

    mutated = validator.read_json(causal_path)
    mutated_start = mutated["events"][0]
    mutated_start["payload"]["actor_start_unix_ns"] = 200
    mutated_start["payload_sha256"] = validator.canonical_sha256(mutated_start["payload"])
    validator.canonical_write(causal_path, mutated)
    validator.refresh(causal_reference, causal_path)
    validator.refresh_switch_receipt_binding(
        tmp_path,
        raw,
        request_id=request_id,
        stage=stage,
    )

    rebound = validator.read_json(causal_path)
    rebound_start, rebound_switch, *rebound_effects = rebound["events"]
    transition = raw["causal_proof"]["route_transition_receipt"]
    assert (
        rebound_switch["payload"]["continuity_receipt_payload_sha256"][request_id][stage]
        == (rebound_start["payload_sha256"])
    )
    assert transition["fence_receipt"]["payload"] == rebound_switch["payload"]
    assert transition["fence_receipt"]["payload_sha256"] == rebound_switch["payload_sha256"]
    assert transition["fence_receipt_sha256"] == validator.canonical_sha256(
        transition["fence_receipt"]
    )
    expected_transition = validator.observed_transition_from_switch(rebound_switch)
    assert len(rebound_effects) == 2
    assert all(
        item["payload"]["observed_transition"] == expected_transition for item in rebound_effects
    )
    rebound_stored = validator.read_json(effects_path)["effects"]
    assert all(
        item["payload"]["observed_transition"] == expected_transition for item in rebound_stored
    )
    assert all(
        item["durable_effect"]["observed_transition"] == expected_transition
        for item in raw["request_records"]
    )


def load_validator():
    spec = importlib.util.spec_from_file_location("s6bm_evidence_validator", VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_review_writer():
    spec = importlib.util.spec_from_file_location("s6bm_review_writer", REVIEW_WRITER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_git_blob_hash_uses_parent_repository_root() -> None:
    runner = load_runner()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=GIT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    repository_path = CONFIG.relative_to(GIT_ROOT).as_posix()
    blob = subprocess.run(
        ["git", "show", f"{revision}:{repository_path}"],
        cwd=GIT_ROOT,
        check=True,
        capture_output=True,
    ).stdout

    assert runner.git_blob_sha256(revision, CONFIG) == hashlib.sha256(blob).hexdigest()


def test_validator_reads_config_blob_from_parent_repository_root() -> None:
    validator = load_validator()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=GIT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    repository_path = CONFIG.relative_to(GIT_ROOT).as_posix()
    blob = subprocess.run(
        ["git", "show", f"{revision}:{repository_path}"],
        cwd=GIT_ROOT,
        check=True,
        capture_output=True,
    ).stdout

    assert validator.git_bytes(revision, CONFIG) == blob


def test_review_writer_separates_python_and_ui_pass_counts() -> None:
    writer = load_review_writer()
    log = "77 passed in 10.0s\nTest Files 17 passed\nTests 59 passed (59)\n"

    assert writer.pytest_counts(log, occurrence=0) == (77, 0)
    assert writer.pytest_counts(log) == (59, 0)


def test_send_batch_reuses_bounded_per_worker_sessions(monkeypatch) -> None:
    runner = load_runner()
    created = []
    seen: list[int] = []
    seen_lock = threading.Lock()

    class FakeSession:
        def __init__(self) -> None:
            self.closed = False
            created.append(self)

        def mount(self, _prefix: str, _adapter: object) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    def fake_send(_config, body, *, session=None):
        time.sleep(0.002)
        with seen_lock:
            seen.append(id(session))
        return {"request_id": body["request_id"]}

    monkeypatch.setattr(runner.requests, "Session", FakeSession)
    monkeypatch.setattr(runner, "send_request", fake_send)

    class Model:
        model_name = "s6bm_blue"
        model_version = "1"
        artifact_sha256 = "a" * 64

    class Config:
        blue = Model()
        green = Model()

    records, bodies = runner.send_batch(
        Config(),
        SimpleNamespace(lease_id="lease-unit", fencing_token="fence-unit"),
        "run-id",
        "attempt-id",
        "batch",
        24,
        3,
        expected_model_role="blue",
    )

    assert len(records) == len(bodies) == 24
    assert 1 <= len(created) <= 3
    assert set(seen) == {id(session) for session in created}
    assert all(session.closed for session in created)


def test_fixed_bridge_producer_preserves_schedule_and_bounded_capacity(monkeypatch) -> None:
    runner = load_runner()
    actor_receipts_ready = threading.Event()
    switch_released = threading.Event()
    observed_request_ids: set[str] = set()
    observed_lock = threading.Lock()

    class FakeSession:
        def mount(self, _prefix: str, _adapter: object) -> None:
            return None

        def close(self) -> None:
            return None

    def fake_send(_config, body, *, session=None):
        assert session is not None
        attempted = time.perf_counter()
        with observed_lock:
            observed_request_ids.add(body["request_id"])
            if len(observed_request_ids) == 3:
                actor_receipts_ready.set()
        if body.get("causal_crossover"):
            assert switch_released.wait(timeout=2)
        time.sleep(0.003)
        return {
            "run_id": body["run_id"],
            "attempt_id": body["attempt_id"],
            "request_id": body["request_id"],
            "model_role": "blue",
            "model_name": body["expected_model_name"],
            "model_version": body["expected_model_version"],
            "artifact_sha256": body["expected_artifact_sha256"],
            "route_generation": body["expected_route_generation"],
            "durable_effect": {"readback_visible": True},
            "attempted_monotonic": attempted,
            "completed_monotonic": time.perf_counter(),
            "outcome": "completed",
            "status_code": 200,
        }

    monkeypatch.setattr(runner.requests, "Session", FakeSession)
    monkeypatch.setattr(runner, "send_request", fake_send)
    monkeypatch.setattr(
        runner,
        "wait_route_switch_deadline",
        lambda _config, *, owner_request_id: {
            "owner_request_id": owner_request_id,
            "started_monotonic_ns": time.perf_counter_ns(),
            "deadline_monotonic_ns": time.perf_counter_ns() + 2_000_000_000,
            "timeout_seconds": 2.0,
            "source": "api_control_plane_designated_crossover_registration",
        },
    )
    config = SimpleNamespace(
        continuity={
            "producer_workers": 2,
            "max_in_flight_requests": 2,
            "max_request_payload_bytes": 4096,
            "max_in_flight_payload_bytes": 8192,
            "route_switch_barrier_timeout_seconds": 2,
        }
    )
    schedule = [
        {"request_id": f"bridge-{index}", "scheduled_offset_ms": index * 5, "hold_ms": 0}
        for index in range(3)
    ]
    bodies = [
        {
            "request_id": item["request_id"],
            "hold_ms": 0,
            "value": index,
            "causal_crossover": index == 0,
            "run_id": "run-fixed-bridge",
            "attempt_id": "attempt-fixed-bridge",
            "expected_model_name": "s6bm_blue",
            "expected_model_version": "1",
            "expected_artifact_sha256": "a" * 64,
            "expected_route_generation": 2,
        }
        for index, item in enumerate(schedule)
    ]

    def collect_receipts(_deadline_monotonic_ns):
        assert actor_receipts_ready.wait(timeout=2)
        return {"receipt": "actor"}

    def transition(receipt_proof, terminal_gate, _deadline_monotonic_ns):
        assert receipt_proof == {"receipt": "actor"}
        assert terminal_gate["expected_terminal_request_ids"] == [
            "bridge-1",
            "bridge-2",
        ]
        switch_released.set()
        return {
            "transition_receipt_observed_monotonic": time.perf_counter(),
            "receipt": "actor",
            "observed_request_ids": sorted(observed_request_ids),
        }

    records, evidence, receipt = runner.run_fixed_bridge_producer(
        config, bodies, schedule, collect_receipts, transition
    )

    assert [item["request_id"] for item in records] == [item["request_id"] for item in schedule]
    assert receipt["receipt"] == "actor"
    assert receipt["observed_request_ids"] == ["bridge-0", "bridge-1", "bridge-2"]
    assert evidence["adaptive_pacing"] is False
    assert evidence["switch_gate_basis"] == (
        "all40_schedule_plus_exact39_blue_terminal_plus_exact4x3_receipts_"
        "plus_exact2_pending_crossovers"
    )
    assert evidence["pre_switch_terminal_gate"]["observed_terminal_count"] == 2
    assert evidence["max_reserved_requests_observed"] <= 2
    assert evidence["max_reserved_payload_bytes_observed"] <= 8192
    assert evidence["reserved_requests_at_finish"] == 0
    assert evidence["reserved_payload_bytes_at_finish"] == 0
    assert (
        evidence["producer_finished_monotonic"] >= evidence["transition_receipt_observed_monotonic"]
    )


def test_bridge_switch_waits_for_every_non_crossover_terminal_after_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = load_runner()
    all_started = threading.Event()
    release_last = threading.Event()
    release_crossover = threading.Event()
    switch_called = threading.Event()
    started: set[str] = set()
    lock = threading.Lock()

    class FakeSession:
        def mount(self, _prefix: str, _adapter: object) -> None:
            return None

        def close(self) -> None:
            return None

    def fake_send(_config, body, *, session=None):
        assert session is not None
        attempted = time.perf_counter()
        with lock:
            started.add(body["request_id"])
            if len(started) == 4:
                all_started.set()
        if body["request_id"] == "bridge-3":
            assert release_last.wait(timeout=2)
        if body.get("causal_crossover"):
            assert release_crossover.wait(timeout=2)
        return {
            "run_id": body["run_id"],
            "attempt_id": body["attempt_id"],
            "request_id": body["request_id"],
            "model_role": "blue",
            "model_name": body["expected_model_name"],
            "model_version": body["expected_model_version"],
            "artifact_sha256": body["expected_artifact_sha256"],
            "route_generation": body["expected_route_generation"],
            "durable_effect": {"readback_visible": True},
            "attempted_monotonic": attempted,
            "completed_monotonic": time.perf_counter(),
            "outcome": "completed",
            "status_code": 200,
        }

    monkeypatch.setattr(runner.requests, "Session", FakeSession)
    monkeypatch.setattr(runner, "send_request", fake_send)
    monkeypatch.setattr(
        runner,
        "wait_route_switch_deadline",
        lambda _config, *, owner_request_id: {
            "owner_request_id": owner_request_id,
            "started_monotonic_ns": time.perf_counter_ns(),
            "deadline_monotonic_ns": time.perf_counter_ns() + 2_000_000_000,
            "timeout_seconds": 2.0,
            "source": "api_control_plane_designated_crossover_registration",
        },
    )
    config = SimpleNamespace(
        continuity={
            "producer_workers": 4,
            "max_in_flight_requests": 4,
            "max_request_payload_bytes": 4096,
            "max_in_flight_payload_bytes": 16384,
            "route_switch_barrier_timeout_seconds": 2,
        }
    )
    schedule = [
        {"request_id": f"bridge-{index}", "scheduled_offset_ms": index, "hold_ms": 0}
        for index in range(4)
    ]
    bodies = [
        {
            "request_id": item["request_id"],
            "hold_ms": 0,
            "causal_crossover": index == 0,
            "run_id": "run-exact-terminal",
            "attempt_id": "attempt-exact-terminal",
            "expected_model_name": "s6bm_blue",
            "expected_model_version": "1",
            "expected_artifact_sha256": "a" * 64,
            "expected_route_generation": 2,
        }
        for index, item in enumerate(schedule)
    ]

    def switch(_receipts, terminal_gate, _deadline_monotonic_ns):
        assert terminal_gate["observed_terminal_request_ids"] == [
            "bridge-1",
            "bridge-2",
            "bridge-3",
        ]
        switch_called.set()
        release_crossover.set()
        return {"transition_receipt_observed_monotonic": time.perf_counter()}

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            runner.run_fixed_bridge_producer,
            config,
            bodies,
            schedule,
            lambda _deadline_monotonic_ns: {"receipts": "ready"},
            switch,
        )
        assert all_started.wait(timeout=2)
        assert not switch_called.wait(timeout=0.05)
        release_last.set()
        records, evidence, _receipt = future.result(timeout=3)

    assert switch_called.is_set()
    assert len(records) == 4
    assert evidence["pre_switch_terminal_gate"]["observed_terminal_count"] == 3


def test_bridge_receipt_gate_preserves_exact_pre_switch_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = load_runner()
    attempt_id = "s6bm-success-bridge-readback"
    request_ids = [f"bridge-{index}" for index in range(4)]
    stages = (
        "api_server_handler_entry",
        "controller_entry",
        "triton_backend_compute_entry",
    )
    rows = []
    for sequence, (request_id, stage) in enumerate(
        ((request_id, stage) for request_id in request_ids for stage in stages),
        start=1,
    ):
        payload = {
            "attempt_id": attempt_id,
            "run_id": "run-bridge-readback",
            "request_id": request_id,
            "request_nonce": hashlib.sha256(request_id.encode("ascii")).hexdigest()[:32],
            "trace_id": f"{sequence:032x}",
            "effect_id": f"{sequence:064x}",
            "model_role": "blue",
            "model_name": "s6bm_blue",
            "model_version": "1",
            "artifact_sha256": "a" * 64,
            "route_generation": 2,
        }
        rows.append(
            {
                "causal_sequence": sequence,
                "event_type": stage,
                **payload,
                "actor_identity": f"actor:{stage}",
                "payload_sha256": runner.canonical_sha256(payload),
                "payload": payload,
                "transaction_id": str(10_000 + sequence),
                "database_recorded_at": f"2026-08-25T00:00:00.{sequence:06d}Z",
                "captured_at": "2026-08-25T00:00:01.000000Z",
            }
        )
    export = {
        "schema_version": "evm.s8_v4.s6bm_causal_event_export.v1",
        "attempt_id": attempt_id,
        "event_count": len(rows),
        "events": rows,
    }

    class Response:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, object]:
            return export

    monkeypatch.setattr(runner.requests, "get", lambda *_args, **_kwargs: Response())
    gate = runner.read_required_bridge_start_receipts(
        SimpleNamespace(ports={"api": 1}),
        suite_root=tmp_path,
        attempt_id=attempt_id,
        required_request_ids=request_ids,
        route_generation=2,
        deadline_monotonic_ns=time.perf_counter_ns() + 1_000_000_000,
    )

    readback_path = tmp_path / gate["raw_readback_export"]["path"]
    assert json.loads(readback_path.read_text(encoding="utf-8")) == export
    assert gate["visible_event_count"] == 12
    assert gate["selected_event_set_sha256"] == runner.canonical_sha256(gate["events"])


def _terminal_gate_fixture(runner, count: int = 2):
    attempt_id = "s6bm-success-terminal-gate"
    run_id = "run-terminal-gate"
    blue_identity_sha256 = "b" * 64
    green_identity_sha256 = "c" * 64
    active_identity_sha256 = runner.canonical_sha256(
        {
            "routes": [
                {
                    "role": "blue",
                    "weight": 100,
                    "identity_sha256": blue_identity_sha256,
                }
            ]
        }
    )
    route_payload = {
        "schema_version": "evm.s6bm.route_revision.v1",
        "run_id": run_id,
        "source_revision": "d" * 40,
        "control_generation": 2,
        "route_generation": 2,
        "phase": "blue_active_rollback",
        "route_weights": {"blue": 100, "green": 0},
        "loaded_roles": ["blue", "green"],
        "active_route_identity_sha256": active_identity_sha256,
        "blue_identity_sha256": blue_identity_sha256,
        "green_identity_sha256": green_identity_sha256,
        "image_digest": "sha256:" + "e" * 64,
        "gpu_uuid": "GPU-terminal-gate",
        "action": "blue_switched",
        "approval_id": "approval-terminal-gate",
        "used_approvals": ["approval-terminal-gate"],
        "route_changed": True,
        "lease_id": "lease-terminal-gate",
        "fencing_token_sha256": "f" * 64,
        "transition_id": None,
        "transition_new_route_generation": None,
    }
    observed_route_revision = {
        "schema_version": "evm.s6bm.observed_route_revision.v1",
        "run_id": run_id,
        "route_generation": 2,
        "route_source_control_generation": 2,
        "route_source_action": route_payload["action"],
        "route_source_phase": route_payload["phase"],
        "route_source_payload_sha256": runner.canonical_sha256(route_payload),
        "route_source_transaction_id": "199",
        "route_source_database_recorded_at": "2026-08-25T00:00:00Z",
        "route_source_payload": route_payload,
        "active_route_identity_sha256": active_identity_sha256,
        "blue_identity_sha256": blue_identity_sha256,
        "green_identity_sha256": green_identity_sha256,
        "transition_id": None,
        "transition_new_route_generation": None,
        "lease_binding_control_generation": 2,
        "lease_binding_payload_sha256": runner.canonical_sha256(route_payload),
        "lease_binding_transaction_id": "199",
        "lease_binding_payload": route_payload,
        "lease_id": route_payload["lease_id"],
        "fencing_token_sha256": route_payload["fencing_token_sha256"],
    }
    bodies: dict[str, dict[str, object]] = {}
    responses: dict[str, dict[str, object]] = {}
    effects: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    for index in range(count):
        request_id = f"bridge-terminal-{index:02d}"
        trace_id = f"{index + 1:032x}"
        effect_id = f"{index + 1:064x}"
        result_sha = hashlib.sha256(f"result:{request_id}".encode("ascii")).hexdigest()
        request_sha = hashlib.sha256(f"request:{request_id}".encode("ascii")).hexdigest()
        causal_payload = {
            "attempt_id": attempt_id,
            "run_id": run_id,
            "request_id": request_id,
            "request_nonce": f"nonce-{index:02d}",
            "trace_id": trace_id,
            "effect_id": effect_id,
            "model_role": "blue",
            "model_name": "s6bm_blue",
            "model_version": "1",
            "artifact_sha256": "a" * 64,
            "route_generation": 2,
            "route_identity_sha256": blue_identity_sha256,
            "route_revision_binding_required": True,
            "lease_id": route_payload["lease_id"],
            "fencing_token_sha256": route_payload["fencing_token_sha256"],
            "result_sha256": result_sha,
            "requires_switch_before_effect": False,
            "observed_route_revision": copy.deepcopy(observed_route_revision),
        }
        causal_sha = runner.canonical_sha256(causal_payload)
        payload = {
            "schema_version": "evm.s8_v4.s6bm_terminal_effect.v1",
            "run_id": run_id,
            "attempt_id": attempt_id,
            "request_id": request_id,
            "trace_id": trace_id,
            "effect_id": effect_id,
            "served_identity": {
                "model_role": "blue",
                "model_name": "s6bm_blue",
                "model_version": "1",
                "artifact_sha256": "a" * 64,
            },
            "route_generation": 2,
            "route_identity_sha256": blue_identity_sha256,
            "result_sha256": result_sha,
            "terminal_outcome": "completed",
            "durable_commit": {
                "causal_sequence": 100 + index,
                "transaction_id": str(200 + index),
                "observed_route_revision": copy.deepcopy(observed_route_revision),
            },
            "observed_route_revision": copy.deepcopy(observed_route_revision),
        }
        stored_sha = runner.canonical_sha256(payload)
        bodies[request_id] = {
            "run_id": run_id,
            "attempt_id": attempt_id,
            "request_id": request_id,
            "traceparent": f"00-{trace_id}-{index + 1:016x}-01",
            "expected_model_name": "s6bm_blue",
            "expected_model_version": "1",
            "expected_artifact_sha256": "a" * 64,
            "expected_route_generation": 2,
        }
        responses[request_id] = {
            "run_id": run_id,
            "attempt_id": attempt_id,
            "request_id": request_id,
            "trace_id": trace_id,
            "effect_id": effect_id,
            "model_role": "blue",
            "model_name": "s6bm_blue",
            "model_version": "1",
            "artifact_sha256": "a" * 64,
            "route_generation": 2,
            "route_identity_sha256": blue_identity_sha256,
            "result_sha256": result_sha,
            "outcome": "completed",
            "status_code": 200,
            "durable_effect": {
                "entity_id": effect_id,
                "request_sha256": request_sha,
                "stored_payload_sha256": stored_sha,
                "causal_sequence": 100 + index,
                "causal_payload_sha256": causal_sha,
                "transaction_id": str(200 + index),
                "readback_visible": True,
                "observed_route_revision": copy.deepcopy(observed_route_revision),
                "route_revision_readback_visible": True,
            },
        }
        effects.append(
            {
                "entity_id": effect_id,
                "state": "completed",
                "payload": payload,
                "scope": f"s6bm.terminal-effect.{attempt_id}",
                "idempotency_key": request_id,
                "request_sha256": request_sha,
            }
        )
        events.append(
            {
                "event_type": "durable_terminal_effect_commit",
                **causal_payload,
                "payload": causal_payload,
                "payload_sha256": causal_sha,
                "causal_sequence": 100 + index,
                "transaction_id": str(200 + index),
            }
        )
    return attempt_id, bodies, responses, effects, events


def test_pre_switch_terminal_gate_binds_online_response_to_durable_exports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = load_runner()
    attempt_id, bodies, responses, effects, events = _terminal_gate_fixture(runner)

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self.payload

    def fake_get(url: str, **_kwargs):
        if "/effects/" in url:
            return Response(
                {
                    "schema_version": "evm.s8_v4.s6bm_terminal_effect_export.v1",
                    "attempt_id": attempt_id,
                    "effect_count": len(effects),
                    "effects": effects,
                }
            )
        return Response(
            {
                "schema_version": "evm.s8_v4.s6bm_causal_event_export.v1",
                "attempt_id": attempt_id,
                "event_count": len(events),
                "events": events,
            }
        )

    monkeypatch.setattr(runner.requests, "get", fake_get)
    ids = sorted(bodies)
    gate = {
        "crossover_request_id": "bridge-crossover",
        "expected_terminal_request_ids": ids,
        "expected_terminal_count": len(ids),
    }
    result = runner.bind_pre_switch_terminal_gate(
        SimpleNamespace(
            continuity={"pre_switch_terminal_bridge_count": len(ids)},
            ports={"api": 1},
        ),
        suite_root=tmp_path,
        attempt_id=attempt_id,
        expected_bodies=bodies,
        response_records=responses,
        terminal_gate=gate,
        deadline_monotonic_ns=time.perf_counter_ns() + 1_000_000_000,
    )

    assert result["schema_version"] == "evm.s8_v4.s6bm_pre_switch_bridge_terminal_gate.v2"
    assert result["observed_terminal_request_ids"] == ids
    assert result["durable_readback_complete"] is True
    assert result["terminal_records_sha256"] == runner.canonical_sha256(result["terminal_records"])
    assert (tmp_path / result["raw_effect_export"]["path"]).is_file()
    assert (tmp_path / result["raw_event_export"]["path"]).is_file()


@pytest.mark.parametrize(
    "mutation",
    ("wrong_response_id", "stale_generation", "wrong_artifact", "missing_readback"),
)
def test_pre_switch_terminal_gate_rejects_online_identity_mutations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str
) -> None:
    runner = load_runner()
    attempt_id, bodies, responses, effects, events = _terminal_gate_fixture(runner)
    first = responses[sorted(responses)[0]]
    if mutation == "wrong_response_id":
        first["request_id"] = "attacker-request"
    elif mutation == "stale_generation":
        first["route_generation"] = 1
    elif mutation == "wrong_artifact":
        first["artifact_sha256"] = "f" * 64
    else:
        first["durable_effect"]["readback_visible"] = False

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self.payload

    monkeypatch.setattr(
        runner.requests,
        "get",
        lambda url, **_kwargs: Response(
            {
                "schema_version": (
                    "evm.s8_v4.s6bm_terminal_effect_export.v1"
                    if "/effects/" in url
                    else "evm.s8_v4.s6bm_causal_event_export.v1"
                ),
                "attempt_id": attempt_id,
                **(
                    {"effect_count": len(effects), "effects": effects}
                    if "/effects/" in url
                    else {"event_count": len(events), "events": events}
                ),
            }
        ),
    )
    ids = sorted(bodies)
    with pytest.raises(runner.S6BMExperimentError, match="online_identity"):
        runner.bind_pre_switch_terminal_gate(
            SimpleNamespace(
                continuity={"pre_switch_terminal_bridge_count": len(ids)},
                ports={"api": 1},
            ),
            suite_root=tmp_path,
            attempt_id=attempt_id,
            expected_bodies=bodies,
            response_records=responses,
            terminal_gate={
                "crossover_request_id": "bridge-crossover",
                "expected_terminal_request_ids": ids,
                "expected_terminal_count": len(ids),
            },
            deadline_monotonic_ns=time.perf_counter_ns() + 1_000_000_000,
        )


def test_expected_attempt_trace_counts_include_exact_controller_only_replay() -> None:
    runner = load_runner()
    attempt = {
        "attempt_id": "s6bm-success-1-unit",
        "request_records": [
            {"request_id": "request-0001"},
            {"request_id": "request-0002"},
        ],
        "idempotent_replay": {
            "request_id": "request-0001",
            "replayed": True,
            "unique_count_before": 2,
            "unique_count_after": 2,
            "record": {"request_id": "request-0001", "replayed": True},
        },
    }

    assert runner.expected_attempt_trace_counts(attempt) == {
        "controller": 3,
        "inference": 2,
    }

    attempt["idempotent_replay"]["unique_count_after"] = 3
    with pytest.raises(runner.S6BMExperimentError, match="otlp_replay_identity"):
        runner.expected_attempt_trace_counts(attempt)


def test_expected_attempt_trace_counts_support_non_replay_qualification() -> None:
    runner = load_runner()
    attempt = {
        "attempt_id": "s6bm-causal-qualification-unit",
        "request_records": [{"request_id": "request-0001"}],
    }

    assert runner.expected_attempt_trace_counts(attempt) == {
        "controller": 1,
        "inference": 1,
    }

    attempt["request_records"].append({"request_id": "request-0001"})
    with pytest.raises(runner.S6BMExperimentError, match="otlp_replay_identity"):
        runner.expected_attempt_trace_counts(attempt)


def test_prometheus_direct_comparison_reports_exact_series_failure() -> None:
    runner = load_runner()
    config = SimpleNamespace(
        blue=SimpleNamespace(model_name="s6bm_blue", model_version="1"),
        green=SimpleNamespace(model_name="s6bm_green", model_version="1"),
    )
    api_metrics = "\n".join(
        [
            'evm_s6bm_requests_total{model_name="s6bm_blue",model_role="blue",model_version="1",outcome="completed"} 10',
            'evm_s6bm_terminal_effects_total{model_name="s6bm_blue",model_role="blue",model_version="1",outcome="committed"} 10',
            'evm_s6bm_requests_total{model_name="s6bm_green",model_role="green",model_version="1",outcome="completed"} 0',
            'evm_s6bm_terminal_effects_total{model_name="s6bm_green",model_role="green",model_version="1",outcome="committed"} 0',
        ]
    )
    triton_metrics = "\n".join(
        [
            'nv_inference_request_success{model="s6bm_blue",version="1"} 10',
            'nv_inference_request_success{model="s6bm_green",version="1"} 0',
            'nv_inference_request_success{gpu_uuid="GPU-unit",model="s6bm_green",version="1"} 0',
        ]
    )
    common = {"scenario": "s8-v4-s6bm", "attempt_id": "attempt-unit"}

    def query(metric: dict[str, str], value: int) -> dict[str, object]:
        return {
            "response": {
                "status": "success",
                "data": {"result": [{"metric": {**common, **metric}, "value": [1, str(value)]}]},
            }
        }

    snapshot = {
        "queries": {
            "api_blue_completed": query(
                {
                    "model_name": "s6bm_blue",
                    "model_role": "blue",
                    "model_version": "1",
                    "outcome": "completed",
                },
                10,
            ),
            "api_blue_effect": query(
                {
                    "model_name": "s6bm_blue",
                    "model_role": "blue",
                    "model_version": "1",
                    "outcome": "committed",
                },
                10,
            ),
            "triton_blue_success": query({"model": "s6bm_blue", "version": "1"}, 10),
            "api_green_completed": query(
                {
                    "model_name": "s6bm_green",
                    "model_role": "green",
                    "model_version": "1",
                    "outcome": "completed",
                },
                0,
            ),
            "api_green_effect": query(
                {
                    "model_name": "s6bm_green",
                    "model_role": "green",
                    "model_version": "1",
                    "outcome": "committed",
                },
                0,
            ),
            "triton_green_success": {
                "response": {
                    "status": "success",
                    "data": {
                        "result": [
                            {
                                "metric": {
                                    **common,
                                    "model": "s6bm_green",
                                    "version": "1",
                                },
                                "value": [1, "0"],
                            },
                            {
                                "metric": {
                                    **common,
                                    "model": "s6bm_green",
                                    "version": "1",
                                    "gpu_uuid": "GPU-unit",
                                },
                                "value": [1, "0"],
                            },
                        ]
                    },
                }
            },
        }
    }

    assert runner.prometheus_direct_comparison(
        config, snapshot, api_metrics, triton_metrics, "attempt-unit"
    )["passed"]

    missing_green = triton_metrics.splitlines()[0]
    failed = runner.prometheus_direct_comparison(
        config, snapshot, api_metrics, missing_green, "attempt-unit"
    )
    assert failed["passed"] is False
    assert any("s6bm_direct_metric_aggregate" in error for error in failed["errors"])


def test_prometheus_direct_comparison_accepts_only_explicit_unloaded_blue() -> None:
    runner = load_runner()
    config = SimpleNamespace(
        blue=SimpleNamespace(model_name="s6bm_blue", model_version="1"),
        green=SimpleNamespace(model_name="s6bm_green", model_version="1"),
    )
    api_metrics = "\n".join(
        [
            'evm_s6bm_requests_total{model_name="s6bm_blue",model_role="blue",model_version="1",outcome="completed"} 1',
            'evm_s6bm_terminal_effects_total{model_name="s6bm_blue",model_role="blue",model_version="1",outcome="committed"} 1',
            'evm_s6bm_requests_total{model_name="s6bm_green",model_role="green",model_version="1",outcome="completed"} 0',
            'evm_s6bm_terminal_effects_total{model_name="s6bm_green",model_role="green",model_version="1",outcome="committed"} 0',
        ]
    )
    triton_metrics = 'nv_inference_request_success{model="s6bm_green",version="1"} 20'
    common = {"scenario": "s8-v4-s6bm", "attempt_id": "attempt-unloaded"}

    def query(metric: dict[str, str], value: int) -> dict[str, object]:
        return {
            "response": {
                "status": "success",
                "data": {"result": [{"metric": {**common, **metric}, "value": [1, str(value)]}]},
            }
        }

    snapshot = {
        "queries": {
            "api_blue_completed": query(
                {
                    "model_name": "s6bm_blue",
                    "model_role": "blue",
                    "model_version": "1",
                    "outcome": "completed",
                },
                1,
            ),
            "api_blue_effect": query(
                {
                    "model_name": "s6bm_blue",
                    "model_role": "blue",
                    "model_version": "1",
                    "outcome": "committed",
                },
                1,
            ),
            "triton_blue_success": {"response": {"status": "success", "data": {"result": []}}},
            "api_green_completed": query(
                {
                    "model_name": "s6bm_green",
                    "model_role": "green",
                    "model_version": "1",
                    "outcome": "completed",
                },
                0,
            ),
            "api_green_effect": query(
                {
                    "model_name": "s6bm_green",
                    "model_role": "green",
                    "model_version": "1",
                    "outcome": "committed",
                },
                0,
            ),
            "triton_green_success": query(
                {"model": "s6bm_green", "version": "1"},
                20,
            ),
        }
    }

    passed = runner.prometheus_direct_comparison(
        config,
        snapshot,
        api_metrics,
        triton_metrics,
        "attempt-unloaded",
        expected_absent_triton_roles=frozenset({"blue"}),
    )
    assert passed["passed"] is True

    contaminated = triton_metrics + (
        '\nnv_inference_request_success{model="s6bm_blue",version="1"} 1'
    )
    failed = runner.prometheus_direct_comparison(
        config,
        snapshot,
        api_metrics,
        contaminated,
        "attempt-unloaded",
        expected_absent_triton_roles=frozenset({"blue"}),
    )
    assert failed["passed"] is False
    assert "s6bm_triton_expected_absent:blue" in failed["errors"]


def test_triton_receipt_wait_uses_frozen_drain_bound(monkeypatch, tmp_path: Path) -> None:
    spec = {
        "schema_version": "evm.s8_v4.s6bm_trace_collector_spec.v1",
        "runner_process_id": os.getppid(),
        "timeout_seconds": 15,
        "deadline_monotonic_ns": 115_000_000_000,
        "otel_trace_path": str(tmp_path / "traces.json"),
        "trace_start_offset": 0,
        "source_revision": "a" * 40,
        "suite_id": "suite-unit",
        "attempt_id": "s6bm-success-1-unit",
        "request_id": "request-unit",
        "request_nonce": "nonce-unit-00000001",
        "trace_id": "1" * 32,
        "model_name": "s6bm_blue",
        "model_version": "1",
    }
    spec_path = tmp_path / "collector-spec.json"
    spec_path.write_text(
        json.dumps(spec, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    observed_deadlines: list[float] = []

    monotonic_values = iter((100.0, 114.99, 115.01))
    monkeypatch.setattr(trace_collector.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(trace_collector.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        trace_collector,
        "find_triton_compute_start",
        lambda *_args, **_kwargs: observed_deadlines.append(1.0) or None,
    )

    with pytest.raises(
        trace_collector.S6BMTraceCollectorError,
        match="triton_compute_start_trace_timeout",
    ):
        trace_collector.collect(spec_path)

    assert len(observed_deadlines) == 1


def test_telemetry_snapshot_waits_for_exact_attempt_targets(monkeypatch) -> None:
    runner = load_runner()
    config = SimpleNamespace(
        ports={"triton_metrics": 18002},
        telemetry={
            "prometheus_job_api": "evm-s8-v4-s6bm-api",
            "prometheus_job_triton": "evm-s8-v4-s6bm-triton",
        },
    )

    def targets(attempt_id: str) -> list[dict[str, object]]:
        return [
            {
                "health": "up",
                "labels": {
                    "job": job,
                    "scenario": "s8-v4-s6bm",
                    "suite_id": "suite-unit",
                    "attempt_id": attempt_id,
                },
            }
            for job in ("evm-s8-v4-s6bm-api", "evm-s8-v4-s6bm-triton")
        ]

    snapshots = [targets("stale-attempt"), targets("current-attempt")]
    monkeypatch.setattr(
        runner,
        "prometheus_targets",
        lambda: snapshots.pop(0) if snapshots else targets("current-attempt"),
    )
    monkeypatch.setattr(runner, "prometheus_query", lambda _query: 1.0)
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        runner.requests,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(text="nv_inference_request_success 1"),
    )

    snapshot = runner.telemetry_snapshot(
        config,
        suite_id="suite-unit",
        attempt_id="current-attempt",
        timeout=1,
    )

    assert snapshot["target_count"] == 2
    assert snapshot["attempt_id"] == "current-attempt"
    assert {item["attempt_id"] for item in snapshot["target_labels"]} == {"current-attempt"}
