from __future__ import annotations

import copy
import json
import hashlib
from pathlib import Path

import pytest

import evm.scale_validation.s8_evidence as evidence
from evm.scale_validation.s8_evidence import S8EvidenceValidationError
from evm.scale_validation.s8_evidence import validate_fault_private_evidence
from evm.scale_validation.s8_runtime import (
    FAULT_PROFILE_IDS,
    SCHEMA_VERSION,
    S8RuntimeConfig,
    analyze_fault_results,
    analyze_soak_results,
)


ROOT = Path(__file__).resolve().parents[1]


def _payload(monkeypatch: pytest.MonkeyPatch) -> tuple[dict, S8RuntimeConfig]:
    config = S8RuntimeConfig.from_path(ROOT / "configs/s8_dependency_soak_v6.toml")
    faults = []
    for profile in FAULT_PROFILE_IDS:
        for repetition in range(1, 4):
            faults.append(
                {
                    "profile_id": profile,
                    "repetition": repetition,
                    "terminal": {"accepted_count": 4, "elapsed_seconds": 2.0},
                    "profile_observations": {
                        "fault_recovery_elapsed_seconds": 2.0,
                        "mttr_seconds": 2.0,
                        "mttr_basis": "fault_profile_execution_to_terminal",
                    },
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
    soak = [
        {
            "evidence_valid": True,
            "load": {
                "service_rate_per_second": 35.0,
                "latency_ms": {"p99": 10.0},
                "error_rate": 0.0,
            },
            "assertions": {"terminal_gauges_zero": True, "cleanup_complete": True},
        }
        for _ in range(3)
    ]
    soak_private = [
        {
            "passed": True,
            "cpu_seconds": 10.0,
            "requests_per_cpu_second": 3.5,
        }
        for _ in range(3)
    ]
    fault_analysis = analyze_fault_results(faults, config)
    soak_analysis = analyze_soak_results(soak, soak_private, config)
    blob = {"path": "value", "blob_oid": "b" * 40, "sha256": "c" * 64}
    monkeypatch.setattr(evidence, "git_blob_identity", lambda *_args: blob)
    monkeypatch.setattr(evidence, "_is_ancestor", lambda *_args: True)
    monkeypatch.setattr(
        evidence,
        "validate_fault_private_evidence",
        lambda *_args, **_kwargs: ([], [{} for _ in faults]),
    )
    monkeypatch.setattr(
        evidence,
        "validate_soak_private_evidence",
        lambda *_args, **_kwargs: ([], soak_private),
    )
    monkeypatch.setattr(
        evidence,
        "validate_private_index",
        lambda *_args, **_kwargs: (
            {"artifact_count": 1, "aggregate_sha256": "d" * 64},
            [],
        ),
    )
    return (
        {
            "schema_version": SCHEMA_VERSION,
            "source_identity": {
                "implementation_revision": "a" * 40,
                "runtime_module_sha256": "c" * 64,
                "git_blobs": {
                    label: blob
                    for label in (
                        "runtime",
                        "runner",
                        "s2_runtime",
                        "s3_runtime",
                        "admission",
                        "worker",
                        "store",
                        "scenario_config",
                        "soak_config",
                    )
                },
            },
            "config": {
                "scenario_sha256": config.sha256,
                "seed": config.seed,
                "repetitions": config.repetitions,
            },
            "fault_results": faults,
            "fault_analysis": fault_analysis,
            "soak_results": soak,
            "soak_analysis": soak_analysis,
            "resource_efficiency": {"gpu_reference": {"scope": "reference"}},
            "private_evidence": {
                "artifact_count": 1,
                "aggregate_sha256": "d" * 64,
            },
            "acceptance": {
                "S8-AC-01": True,
                "S8-AC-02": True,
                "S8-AC-03": True,
                "S8-AC-04": False,
            },
            "runtime_verdict": "exercised_pending_hash_closure",
        },
        config,
    )


def test_s8_validator_recomputes_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    payload, config = _payload(monkeypatch)

    result = evidence.validate_s8_experiment(
        payload,
        config=config,
        private_root=ROOT,
        project_root=ROOT,
    )

    assert result["valid"] is True
    assert result["fault_result_count"] == 21


def test_s8_validator_rejects_non_finite_evidence_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, config = _payload(monkeypatch)
    payload["fault_results"][0]["metrics"]["observed_upper_bound"] = float("inf")

    with pytest.raises(S8EvidenceValidationError, match="non_finite"):
        evidence.validate_s8_experiment(
            payload,
            config=config,
            private_root=ROOT,
            project_root=ROOT,
        )


def test_fault_private_projection_rejects_mttr_mutation(tmp_path: Path) -> None:
    profile = "worker-loss"
    repetition = 1
    private_path = (
        tmp_path
        / "faults"
        / profile
        / f"repetition-{repetition}"
        / "profile-result-private.json"
    )
    private_path.parent.mkdir(parents=True)
    assertions = [{"assertion_id": "worker_recovered", "passed": True}]
    private = {
        "terminal": {
            "accepted_count": 1,
            "terminal_seen_count": 1,
            "missing_terminal_count": 0,
            "elapsed_seconds": 3.0,
            "final": {"state_counts": {"completed": 1}},
        },
        "fixture": {
            "attempts": 1,
            "unique_external_effects": 1,
            "duplicate_external_effects": 0,
            "tasks_with_multiple_logical_effects": 0,
            "max_runtime_concurrency": {"cpu": 1},
            "max_external_in_flight": {"cpu": 1},
        },
        "metrics": {"summary": {}},
        "extra": {
            "fault_recovery_elapsed_seconds": 63.234,
            "worker_recovery_elapsed_seconds": 18.0,
            "mttr_seconds": 18.0,
            "mttr_basis": "worker_pid_failure_to_reclaimed_task_terminal",
        },
        "assertions": assertions,
        "passed": True,
    }
    private_path.write_text(json.dumps(private), encoding="utf-8")
    public = {
        "profile_id": profile,
        "repetition": repetition,
        "private_evidence_sha256": hashlib.sha256(private_path.read_bytes()).hexdigest(),
        "terminal": {
            "accepted_count": 1,
            "terminal_count": 1,
            "missing_count": 0,
            "elapsed_seconds": 3.0,
            "final_state_counts": {"completed": 1},
        },
        "external_effects": {
            "attempts": 1,
            "unique": 1,
            "duplicates": 0,
            "multiple_logical": 0,
            "max_runtime_concurrency": {"cpu": 1},
            "max_external_in_flight": {"cpu": 1},
            "cuda_probe_count": 0,
            "cuda_failure_count": 0,
            "cuda_nonzero_activity_count": 0,
            "cuda_peak_allocated_bytes": 0,
        },
        "metrics": {},
        "profile_observations": dict(private["extra"]),
        "assertions": assertions,
        "passed": True,
    }

    errors, _ = validate_fault_private_evidence([public], private_root=tmp_path)
    assert errors == ["fault_matrix_identity"]

    public["profile_observations"]["mttr_seconds"] = 1.0
    errors, _ = validate_fault_private_evidence([public], private_root=tmp_path)
    assert "fault_observation_projection:worker-loss:1" in errors


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["fault_analysis"].__setitem__("passed", False),
        lambda value: value["acceptance"].__setitem__("S8-AC-01", False),
        lambda value: value["soak_analysis"]["checks"].__setitem__(
            "terminal_cleanup", False
        ),
    ],
)
def test_s8_validator_rejects_summary_mutations(
    monkeypatch: pytest.MonkeyPatch, mutation
) -> None:
    payload, config = _payload(monkeypatch)
    mutated = copy.deepcopy(payload)
    mutation(mutated)

    with pytest.raises(S8EvidenceValidationError):
        evidence.validate_s8_experiment(
            mutated,
            config=config,
            private_root=ROOT,
            project_root=ROOT,
        )
