from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel.admission_queue import (
    canonical_payload_size,
    load_admission_queue_config,
)
from evm.scale_validation import s2_runtime
from evm.scale_validation.s2_runtime import (
    EXPECTED_PROFILE_IDS,
    FULL_TRACE_NAMES,
    S2MatrixConfig,
    aggregate_acceptance,
    build_task_payload,
    executor_process_tree_rss_peak,
    merge_terminal_results,
    payload_digest,
    process_tree_rss_slope,
    progress_verdict,
    profile_payloads,
    summarize_submission,
    terminal_failure_reasons,
    trace_summary,
    worker_executor_rss_peak,
)


ROOT = Path(__file__).resolve().parents[1]


def test_s2_matrix_is_frozen_with_exact_a_to_j_profiles():
    matrix = S2MatrixConfig.from_path(ROOT / "configs" / "s2_experiment_matrix_v1.toml")

    assert tuple(sorted(matrix.profiles)) == EXPECTED_PROFILE_IDS
    assert matrix.repetitions == 3
    assert matrix.version == "s2-external-matrix-v6-20260817"
    assert matrix.drain_timeout_seconds == 270.0
    assert matrix.rss_slope_measurement_seconds == 30.0
    assert matrix.profiles["D"]["arrival_duration_seconds"] == 45.0
    assert matrix.profiles["D"]["request_count"] == 360
    assert matrix.profiles["E"]["request_count"] == 520
    assert matrix.profiles["E"]["batch_size"] == 52
    assert matrix.profiles["E"]["rejected_retry_max_rounds"] == 3
    assert len(matrix.sha256) == 64


def test_progress_verdict_reads_current_nested_contract_and_legacy_field():
    assert progress_verdict(
        {"verdict_and_claim_boundary": {"verdict": "passed"}}
    ) == "passed"
    assert progress_verdict({"verdict": "failed"}) == "failed"
    assert progress_verdict({}) is None


def test_partial_profile_run_cannot_vacuously_pass_cross_profile_gates():
    matrix = S2MatrixConfig.from_path(ROOT / "configs" / "s2_experiment_matrix_v1.toml")
    acceptance, readiness = aggregate_acceptance(
        [
            {
                "profile_id": "A",
                "passed": True,
                "peaks": {
                    "active_depth": 1,
                    "active_bytes": 1,
                    "api_process_tree_rss_bytes": 1,
                    "worker_process_tree_rss_bytes": 1,
                },
                "metrics": {"rss_slope_bytes_per_minute": 0},
                "submission": {"transport_failures": 0},
                "assertions": [],
            }
        ],
        load_admission_queue_config(),
    )

    assert matrix.repetitions == 3
    assert acceptance["S2-AC-01"] is False
    assert acceptance["S2-AC-04"] is False
    assert readiness["RG-08-retry-dlq-backpressure-observed"] is False
    assert readiness["RG-09-real-worker-loss-recovery"] is False
    assert readiness["RG-10-trusted-cuda-bound-to-effect"] is False


def test_process_tree_rss_slope_uses_declared_tail_window():
    samples = [
        {
            "monotonic": float(index),
            "api_rss": {"total": 1000 + index * 10},
            "worker_rss": {"total": 2000 - index * 5},
        }
        for index in range(31)
    ]

    measured = process_tree_rss_slope(samples, window_seconds=30.0)

    assert measured["measured"] is True
    assert measured["window_seconds"] == 30.0
    assert measured["api_bytes_per_minute"] == 600.0
    assert measured["worker_bytes_per_minute"] == -300.0


def test_worker_executor_rss_peak_reads_retained_heartbeat_value(tmp_path):
    heartbeat = tmp_path / "worker-heartbeat.json"
    heartbeat.write_text(
        json.dumps({"executor_process_tree_rss_peak_bytes": 12_345}) + "\n",
        encoding="utf-8",
    )

    assert worker_executor_rss_peak(heartbeat) == 12_345

    heartbeat.write_text("{invalid", encoding="utf-8")
    assert worker_executor_rss_peak(heartbeat) == 0


def test_executor_rss_peak_uses_heartbeat_when_os_sample_misses_short_child():
    assert executor_process_tree_rss_peak(
        [
            {
                "worker_rss": {"children": 0},
                "executor_process_tree_rss_peak_bytes": 12_345,
            }
        ]
    ) == 12_345


def test_terminal_batches_preserve_exact_identity_after_compaction_boundary():
    first = {
        "closed": True,
        "elapsed_seconds": 1.0,
        "terminal_seen_task_ids": ["task-a"],
        "peaks": {},
        "samples": [],
        "final": {"active_depth": 0},
    }
    second = {
        "closed": True,
        "elapsed_seconds": 2.0,
        "terminal_seen_task_ids": ["task-b"],
        "peaks": {},
        "samples": [],
        "final": {"active_depth": 0},
    }

    merged = merge_terminal_results([first, second], {"task-a", "task-b"})

    assert merged["closed"] is True
    assert merged["terminal_seen_task_ids"] == ["task-a", "task-b"]
    assert merged["accepted_count"] == merged["terminal_seen_count"] == 2


def test_large_byte_profile_stays_below_single_item_limit():
    payload = build_task_payload(
        profile_id="C",
        repetition=1,
        index=0,
        idempotency_key="s2-c-large-item",
        padding_chunks=60,
        padding_chunk_bytes=2000,
    )

    assert 100_000 < canonical_payload_size(payload) < 262_144


def test_fixed_seed_payload_digest_is_stable_across_repetitions():
    first, _ = profile_payloads(
        profile_id="A",
        repetition=1,
        count=12,
        seed=20260816,
    )
    second, _ = profile_payloads(
        profile_id="A",
        repetition=2,
        count=12,
        seed=20260816,
    )

    assert payload_digest(first) == payload_digest(second)


def test_submission_summary_preserves_integer_retry_after():
    summary = summarize_submission(
        {
            "peak_in_flight": 2,
            "status_counts": {"202": 1, "429": 1},
            "results": [
                {"status_code": 202, "retry_after": None, "elapsed_seconds": 0.1},
                {"status_code": 429, "retry_after": "2", "elapsed_seconds": 0.2},
            ],
        }
    )

    assert summary["retry_after_values"] == [2]
    assert summary["transport_failures"] == 0


def test_retry_rejected_payloads_reuses_original_identity(monkeypatch):
    calls = []

    def fake_submit_payloads(**kwargs):
        calls.append(kwargs)
        return {
            "results": [
                {
                    "index": index,
                    "status_code": 202,
                    "body": {"task_id": f"task-{payload['idempotency_key']}"},
                    "retry_after": None,
                    "trace_id": f"trace-{index}",
                }
                for index, payload in enumerate(kwargs["payloads"])
            ],
            "peak_in_flight": 1,
            "status_counts": {"202": len(kwargs["payloads"])},
        }

    monkeypatch.setattr(s2_runtime, "submit_payloads", fake_submit_payloads)
    monkeypatch.setattr(s2_runtime.time, "sleep", lambda _seconds: None)
    payloads = [{"idempotency_key": f"key-{index}"} for index in range(3)]
    retries, pending = s2_runtime.retry_rejected_payloads(
        api_url="http://runtime.invalid",
        payloads=payloads,
        trace_seeds=["a", "b", "c"],
        initial_submission={
            "results": [
                {"index": 0, "status_code": 202, "retry_after": None},
                {"index": 1, "status_code": 429, "retry_after": "2"},
                {"index": 2, "status_code": 429, "retry_after": "2"},
            ]
        },
        max_rounds=3,
        concurrency=1,
        retry_after_cap_seconds=2,
    )

    assert pending == set()
    assert [item["idempotency_key"] for item in calls[0]["payloads"]] == ["key-1", "key-2"]
    assert [item["original_index"] for item in retries[0]["results"]] == [1, 2]


def test_terminal_failure_reasons_preserves_retry_budget_rca_after_reconciliation():
    reasons = terminal_failure_reasons(
        [
            {
                "task_id": "task-a",
                "last_failure_class": "retry_budget_exhausted:airflow_api_rejected",
                "terminal_reason": "external_effect_not_found_after_timeout",
            },
            {
                "task_id": "task-b",
                "last_failure_class": None,
                "terminal_reason": "permanent:invalid_payload",
            },
        ],
        {"task-a", "task-b"},
    )

    assert reasons == {
        "retry_budget_exhausted:airflow_api_rejected": 1,
        "permanent:invalid_payload": 1,
    }


def test_trace_summary_requires_full_existing_runtime_chain(tmp_path):
    trace_identity = "a" * 32
    spans = [{"traceId": trace_identity, "name": name} for name in FULL_TRACE_NAMES]
    trace_path = tmp_path / "traces.json"
    trace_path.write_text(
        json.dumps(
            {
                "resourceSpans": [
                    {"scopeSpans": [{"scope": {"name": "test"}, "spans": spans}]}
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = trace_summary(trace_path, 0, {"private-task": trace_identity})

    assert result["complete_count"] == 1
    assert result["missing"] == 0
    assert len(result["raw_tail_sha256"]) == 64
