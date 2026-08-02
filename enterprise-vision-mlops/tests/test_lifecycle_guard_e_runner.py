from __future__ import annotations

from evm.operations.lifecycle_guard_e_runner import invariant_diff, stable_replay


def runtime_snapshot() -> dict:
    return {
        "deployment": {"uid": "uid-1", "image": "image@sha256:1", "ready_replicas": 1},
        "ready": {"model_sha256": "a" * 64, "candidate_id": "candidate-1"},
        "inference": {"device": "cuda"},
        "gpu_allocatable": ["1"],
        "device_plugin": [{"uid": "plugin-1", "phase": "Running", "ready": True}],
        "prometheus_targets": {"evm-api": "up", "evm-b0-production": "up"},
        "runtime_supervisor": {
            "source_commit": "b" * 40,
            "status": "online",
            "children": [
                {
                    "name": "lifecycle_worker",
                    "pid": 10,
                    "process_instance_id": "worker-1",
                    "source_commit": "b" * 40,
                    "supervisor_lease_id": "lease-1",
                    "fencing_token": 1,
                    "status": "online",
                },
                {
                    "name": "kubernetes_observer",
                    "pid": 11,
                    "process_instance_id": "observer-1",
                    "source_commit": "b" * 40,
                    "supervisor_lease_id": "lease-1",
                    "fencing_token": 1,
                    "status": "online",
                },
            ],
        },
    }


def side_effects() -> dict:
    return {
        role: {"count": 1, "identity_digest": role, "identities": [role]}
        for role in (
            "kubernetes_jobs",
            "mlflow_runs",
            "model_candidates",
            "deployment_intents",
        )
    }


def test_invariants_require_exact_runtime_and_side_effect_identity() -> None:
    runtime = runtime_snapshot()
    effects = side_effects()

    passed = invariant_diff(runtime, runtime, effects, effects, {"a": "1"}, {"a": "1"})
    changed = runtime_snapshot()
    changed["deployment"]["uid"] = "uid-2"
    blocked = invariant_diff(runtime, changed, effects, effects, {"a": "1"}, {"a": "1"})

    assert passed["passed"] is True
    assert blocked["passed"] is False
    assert blocked["runtime_identity_unchanged"] is False


def test_stable_replay_requires_three_matching_fast_decisions() -> None:
    results = [
        {
            "decision": "blocked",
            "blockers": ["expected"],
            "decision_fingerprint": "fingerprint",
            "elapsed_seconds": 0.1,
        }
        for _ in range(3)
    ]

    assert stable_replay(results, "blocked", {"expected"}) is True
    results[2]["decision_fingerprint"] = "different"
    assert stable_replay(results, "blocked", {"expected"}) is False
