from __future__ import annotations

import json
from pathlib import Path

from evm.control_panel.admission_queue import canonical_payload_size
from evm.scale_validation.s2_runtime import (
    EXPECTED_PROFILE_IDS,
    FULL_TRACE_NAMES,
    S2MatrixConfig,
    build_task_payload,
    payload_digest,
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
    assert matrix.version == "s2-external-matrix-v1-20260816"
    assert len(matrix.sha256) == 64


def test_progress_verdict_reads_current_nested_contract_and_legacy_field():
    assert progress_verdict(
        {"verdict_and_claim_boundary": {"verdict": "passed"}}
    ) == "passed"
    assert progress_verdict({"verdict": "failed"}) == "failed"
    assert progress_verdict({}) is None


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
