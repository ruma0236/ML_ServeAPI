from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel.admission_queue import (
    canonical_payload_size,
    load_admission_queue_config,
)
from evm.scale_validation.s2_runtime import (
    EXPECTED_PROFILE_IDS,
    FULL_TRACE_NAMES,
    S2MatrixConfig,
    aggregate_acceptance,
    build_task_payload,
    payload_digest,
    process_tree_rss_slope,
    progress_verdict,
    profile_payloads,
    summarize_submission,
    trace_summary,
)


ROOT = Path(__file__).resolve().parents[1]


def test_s2_matrix_is_frozen_with_exact_a_to_j_profiles():
    matrix = S2MatrixConfig.from_path(ROOT / "configs" / "s2_experiment_matrix_v1.toml")

    assert tuple(sorted(matrix.profiles)) == EXPECTED_PROFILE_IDS
    assert matrix.repetitions == 3
    assert matrix.version == "s2-external-matrix-v2-20260817"
    assert matrix.rss_slope_measurement_seconds == 30.0
    assert matrix.profiles["D"]["arrival_duration_seconds"] == 45.0
    assert matrix.profiles["D"]["request_count"] == 360
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
