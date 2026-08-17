from __future__ import annotations

import json
from pathlib import Path
from copy import deepcopy

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
    recompute_s2_acceptance,
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
    assert matrix.version == "s2-external-matrix-v7-20260817-strict-evidence"
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


def _strict_runtime_result(profile_id: str, repetition: int, *, variant=None):
    submitted = 1
    statuses = {"202": 1}
    accepted = 1
    eligible = 1
    no_effect = 0
    assertions = []
    metrics = {"retry": {}, "dlq": {}, "cpu_scale": {}}
    observations = {"variant": variant}
    if profile_id == "B":
        submitted, statuses = 2, {"202": 1, "429": 1}
    elif profile_id == "C":
        submitted, statuses = 3, {"202": 1, "429": 1, "413": 1}
    elif profile_id == "D":
        submitted, statuses = 2, {"202": 1, "429": 1}
        assertions.append(
            {
                "assertion_id": f"D_{variant}_backpressure_is_explicit",
                "passed": False,
                "observed": {
                    "status_counts": statuses,
                    "retry_after": ["2"],
                },
            }
        )
        observations["rss_slope"] = {
            "measured": True,
            "api_bytes_per_minute": 1.0,
            "worker_bytes_per_minute": 1.0,
        }
    elif profile_id == "E":
        assertions.extend(
            [
                {
                    "assertion_id": "E_all_unique_requests_accepted",
                    "passed": False,
                    "observed": {"accepted": 1, "expected": 1},
                },
                {
                    "assertion_id": "E_history_compacted_with_tombstone",
                    "passed": False,
                    "observed": {
                        "history": [{"item_count": 1}],
                        "idempotency": {"tombstones": 1},
                    },
                },
                {
                    "assertion_id": "E_restart_replay_same_task_no_new_effect",
                    "passed": False,
                    "observed": {
                        "worker_replaced": True,
                        "replay_matches": True,
                        "effects_before": 1,
                        "effects_after": 1,
                    },
                },
            ]
        )
    elif profile_id == "F":
        submitted, statuses, accepted, eligible, no_effect = 2, {"202": 2}, 2, 1, 1
        assertions.extend(
            [
                {
                    "assertion_id": "F_expired_without_effect",
                    "passed": False,
                    "observed": {"state": "expired"},
                },
                {
                    "assertion_id": "F_following_healthy_completed",
                    "passed": False,
                    "observed": {"healthy_accepted": 1},
                },
            ]
        )
    elif profile_id == "G":
        submitted, statuses, accepted, eligible, no_effect = 2, {"202": 2}, 2, 1, 1
        metrics = {
            "retry": {"dlq:transient": 2.0},
            "dlq": {"transient": 1.0},
            "cpu_scale": {},
        }
        assertions.extend(
            [
                {
                    "assertion_id": "G_retry_budget_observed",
                    "passed": False,
                    "observed": {
                        "retry_budget": [{"consumed": 1}],
                        "reasons": {"retry_budget_exhausted:test": 1},
                    },
                },
                {
                    "assertion_id": "G_healthy_completed_after_retry_pressure",
                    "passed": False,
                    "observed": {"healthy": 1, "expected": 1},
                },
            ]
        )
    elif profile_id == "H":
        submitted, statuses, accepted, eligible, no_effect = 2, {"202": 2}, 2, 1, 1
        metrics = {"retry": {}, "dlq": {"permanent": 1.0}, "cpu_scale": {}}
        assertions.extend(
            [
                {
                    "assertion_id": "H_poison_quarantined",
                    "passed": False,
                    "observed": {"poison": 1, "states": ["dlq"]},
                },
                {
                    "assertion_id": "H_healthy_not_head_of_line_blocked",
                    "passed": False,
                    "observed": {"healthy": 1, "expected": 1},
                },
            ]
        )
    elif profile_id == "I":
        submitted, statuses, accepted, eligible = 3, {"202": 3}, 3, 3
        assertions.extend(
            [
                {
                    "assertion_id": "I_exact_worker_process_replaced",
                    "passed": False,
                    "observed": {
                        "worker_identity_changed": True,
                        "old_process_dead": True,
                        "stopped_process_count": 2,
                        "orphan_child_count": 0,
                    },
                },
                {
                    "assertion_id": "I_lease_epoch_increased",
                    "passed": False,
                    "observed": {"slow_task_lease_epoch": 2},
                },
                {
                    "assertion_id": "I_timeout_does_not_block_healthy",
                    "passed": False,
                    "observed": {
                        "healthy_terminal_at": "2026-01-01T00:00:01Z",
                        "timeout_terminal_at": "2026-01-01T00:00:02Z",
                    },
                },
                {
                    "assertion_id": "I_slow_item_recovered",
                    "passed": False,
                    "observed": True,
                },
            ]
        )
    elif profile_id == "J":
        assertions.append(
            {
                "assertion_id": "J_existing_gpu_profile_routed_gpu",
                "passed": False,
                "observed": ["gpu"],
            }
        )
    identity_hash = "a" * 64
    effect_hash = "b" * 64
    assertions.append(
        {
            "assertion_id": "postgres_json_mirror_parity",
            "passed": False,
            "observed": {
                "authority_count": accepted,
                "mirror_count": accepted,
                "file_count": accepted,
                "authority_sha256": identity_hash,
                "mirror_sha256": identity_hash,
                "file_sha256": identity_hash,
            },
        }
    )
    retry_delay = (
        {"transient": {"count": 2.0, "sum": 0.5, "max": 0.3}}
        if profile_id == "G"
        else {}
    )
    return {
        "profile_id": profile_id,
        "repetition": repetition,
        "variant": variant,
        "submission": {
            "submitted": submitted,
            "status_counts": statuses,
            "retry_after_values": [2] if "429" in statuses else [],
            "transport_failures": 0,
        },
        "terminal": {
            "accepted_count": accepted,
            "terminal_count": accepted,
            "missing_count": 0,
            "final_state_counts": {"completed": eligible, "dlq": no_effect},
        },
        "peaks": {
            "active_depth": 2,
            "active_bytes": 100,
            "local_depth": 1,
            "local_bytes": 50,
            "worker_in_flight": 1,
            "worker_in_flight_bytes": 50,
            "cpu_downstream_outstanding": 1,
            "gpu_downstream_outstanding": 1 if profile_id == "J" else 0,
            "api_process_tree_rss_bytes": 1000,
            "worker_process_tree_rss_bytes": 1000,
            "executor_children_rss_bytes": 500,
            "ingress_active_requests": 1,
            "ingress_in_flight_bytes": 100,
        },
        "metrics": metrics,
        "prometheus": {"targets": {"api": 1, "worker": 1}},
        "trace": {"task_count": accepted, "complete_count": accepted, "missing": 0},
        "external_effects": {
            "cuda_probe_count": 1 if profile_id == "J" else 0,
            "cuda_nonzero_activity_count": 1 if profile_id == "J" else 0,
            "cuda_failure_count": 0,
            "cuda_peak_allocated_bytes": 1 if profile_id == "J" else 0,
            "max_external_in_flight": {"gpu": 1} if profile_id == "J" else {},
            "max_runtime_concurrency": {"gpu": 1} if profile_id == "J" else {},
        },
        "profile_observations": observations,
        "assertions": assertions,
        "strict_evidence": {
            "identity_closure": {
                "accepted_count": accepted,
                "terminal_count": accepted,
                "accepted_identity_set_sha256": identity_hash,
                "terminal_identity_set_sha256": identity_hash,
                "active_final_depth": 0,
                "outcome_unknown_final": 0,
            },
            "effect_accounting": {
                "eligible_count": eligible,
                "eligible_exactly_once_count": eligible,
                "actual_effect_count": eligible,
                "eligible_identity_set_sha256": effect_hash,
                "actual_effect_identity_set_sha256": effect_hash,
                "no_effect_expected_count": no_effect,
                "no_effect_observed_count": no_effect,
                "duplicate_effect_count": 0,
                "multiple_logical_effect_count": 0,
            },
            "waits": {
                "admission": {"count": 1.0, "sum": 0.01, "max": 0.01},
                "queue": {"count": 1.0, "sum": 0.01, "max": 0.01},
                "load_generator_permit": {
                    "count": submitted,
                    "sum": 0.01,
                    "max": 0.01,
                },
                "retry_delay": retry_delay,
            },
        },
        "cleanup": {
            "schema_dropped": True,
            "marker_processes_remaining": [],
            "errors": [],
        },
        "passed": False,
    }


def _strict_suite():
    results = []
    for repetition in range(1, 4):
        for profile_id in EXPECTED_PROFILE_IDS:
            if profile_id == "D":
                adaptive = _strict_runtime_result("D", repetition, variant="adaptive")
                cpu1 = _strict_runtime_result("D", repetition, variant="cpu1")
                results.append(
                    {
                        "profile_id": "D",
                        "repetition": repetition,
                        "variants": [adaptive, cpu1],
                        "assertions": [
                            {
                                "assertion_id": "D_adaptive_cpu_scaled_non_vacuously",
                                "passed": False,
                                "observed": {
                                    "adaptive_scale_up_events": 1.0,
                                    "cpu1_scale_up_events": 0.0,
                                    "adaptive_runtime_concurrency": 2,
                                    "cpu1_runtime_concurrency": 1,
                                },
                            },
                            {
                                "assertion_id": "D_adaptive_throughput_exceeds_cpu1",
                                "passed": False,
                                "observed": {
                                    "adaptive_accepted_throughput": 2.0,
                                    "cpu1_accepted_throughput": 1.0,
                                },
                            },
                        ],
                        "passed": False,
                    }
                )
            else:
                results.append(_strict_runtime_result(profile_id, repetition))
    return results


def test_strict_s2_recalculation_ignores_passed_booleans_and_uses_raw_values():
    config = load_admission_queue_config()
    acceptance, readiness, _details = recompute_s2_acceptance(
        _strict_suite(), config
    )

    assert set(acceptance.values()) == {True}
    assert set(readiness.values()) == {True}


def test_strict_s2_recalculation_fails_closed_for_mutated_numeric_evidence():
    config = load_admission_queue_config()
    mutations = []
    for mutate in (
        lambda suite: suite[0]["peaks"].__setitem__(
            "local_bytes", config.local_max_bytes + 1
        ),
        lambda suite: suite[0]["strict_evidence"]["identity_closure"].__setitem__(
            "terminal_identity_set_sha256", "c" * 64
        ),
        lambda suite: suite[0]["strict_evidence"]["effect_accounting"].__setitem__(
            "duplicate_effect_count", 1
        ),
        lambda suite: next(
            item for item in suite if item["profile_id"] == "G"
        )["strict_evidence"]["waits"].__setitem__("retry_delay", {}),
    ):
        suite = deepcopy(_strict_suite())
        mutate(suite)
        mutations.append(recompute_s2_acceptance(suite, config)[0])

    assert mutations[0]["S2-AC-01"] is False
    assert mutations[1]["S2-AC-02"] is False
    assert mutations[2]["S2-AC-03"] is False
    assert mutations[3]["S2-AC-04"] is False
