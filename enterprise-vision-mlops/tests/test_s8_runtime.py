from __future__ import annotations

import json
from pathlib import Path

from evm.scale_validation.s8_runtime import (
    FAULT_PROFILE_IDS,
    S8RuntimeConfig,
    analyze_fault_results,
    analyze_soak_private,
    linear_slope,
    statistics,
)


ROOT = Path(__file__).resolve().parents[1]


def test_s8_config_freezes_fault_and_soak_contract() -> None:
    config = S8RuntimeConfig.from_path(ROOT / "configs/s8_dependency_soak_v1.toml")

    assert config.repetitions == 3
    assert config.soak_requests_per_second == 35.0
    assert config.soak_measurement_seconds == 1800.0
    assert config.soak_resource_sample_interval_seconds == 1.0
    assert tuple(profile for profile in config.fault_matrix().profiles if profile != "I") == FAULT_PROFILE_IDS
    assert config.fault_matrix().profiles["I"] == config.fault_matrix().profiles["worker-loss"]


def _fault_results(config: S8RuntimeConfig) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for profile in FAULT_PROFILE_IDS:
        for repetition in range(1, config.repetitions + 1):
            results.append(
                {
                    "profile_id": profile,
                    "repetition": repetition,
                    "terminal": {"accepted_count": 4, "elapsed_seconds": 2.0},
                    "external_effects": {"attempts": 4, "duplicates": 0},
                    "metrics": {
                        "dependency_circuit": {
                            "opens": 1 if profile == "retry-budget" else 0
                        },
                        "control_plane_pool": {
                            "api": {"timeouts": 0},
                            "worker": {"timeouts": 0},
                        },
                    },
                    "cleanup": {
                        "schema_dropped": True,
                        "marker_processes_remaining": [],
                        "errors": [],
                    },
                    "passed": True,
                }
            )
    return results


def test_s8_fault_projection_is_recomputed_and_fail_closed() -> None:
    config = S8RuntimeConfig.from_path(ROOT / "configs/s8_dependency_soak_v1.toml")
    results = _fault_results(config)

    assert analyze_fault_results(results, config)["passed"] is True

    results[0]["external_effects"] = {"attempts": 13, "duplicates": 0}
    projection = analyze_fault_results(results, config)
    assert projection["passed"] is False
    assert projection["checks"]["retry_amplification_bounded"] is False


def test_s8_soak_private_recomputes_finite_resource_slopes(tmp_path: Path) -> None:
    config = S8RuntimeConfig.from_path(ROOT / "configs/s8_dependency_soak_v1.toml")
    samples = [
        {
            "offset_seconds": float(index),
            "api_process_tree_rss_bytes": 100_000_000 + index * 100,
            "load_generator_rss_bytes": 20_000_000,
            "api_process_tree_open_handles": 20,
            "load_generator_open_handles": 5,
            "api_process_tree_cpu_percent": 25.0,
            "load_generator_cpu_percent": 5.0,
            "artifact_bytes": index * 100,
            "evm_control_plane_db_pool_in_use": 1,
            "evm_control_plane_db_pool_waiting": 0,
            "evm_s3_capacity_executor_queue_depth": 0,
        }
        for index in range(1621)
    ]
    target = tmp_path / "point-evidence-private.json"
    target.write_text(
        json.dumps(
            {
                "resource_samples": samples,
                "measurement": {"observations": [{"status_code": 200}] * 100},
                "evidence_valid": True,
            }
        ),
        encoding="utf-8",
    )

    projection = analyze_soak_private(target, config)

    assert projection["passed"] is True
    assert projection["resource_sample_count"] == 1621
    assert projection["requests_per_cpu_second"] > 0


def test_s8_numeric_helpers_never_publish_non_finite_statistics() -> None:
    assert statistics([float("inf")]) == {
        "count": 1,
        "finite": False,
        "min": None,
        "max": None,
        "mean": None,
    }
    assert linear_slope(
        [{"offset_seconds": 0, "value": 1}, {"offset_seconds": 60, "value": 2}],
        lambda item: item["value"],
    ) == 1.0
