from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from evm.operations.failure_evidence import sha256_file
from evm.operations.scenario_b_canary import (
    CanaryPolicy,
    InferenceObservation,
    ModelIdentity,
    QualityMetrics,
    ReplayRequest,
    build_assignment_routes,
    run_controlled_replay,
    write_controlled_replay_evidence,
)


FIXTURES = Path(__file__).parent / "fixtures" / "operations"


def _payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _policy(**updates: object) -> CanaryPolicy:
    payload: dict[str, object] = {
        "schema_version": "evm.scenario_b_policy.v1",
        "policy_id": "scenario-b-test-v1",
        "assignment_seed": "fixed-seed",
        "min_shadow_requests": 500,
        "total_replay_requests": 1000,
        "challenger_requests": 100,
        "max_challenger_fraction": 0.1,
        "min_accuracy": 0.8,
        "min_f1": 0.75,
        "min_auroc": 0.8,
        "max_latency_p95_ms": 30.0,
        "max_error_rate": 0.01,
        "stop_budget_seconds": 30.0,
        "rollback_budget_seconds": 300.0,
        "signal_precedence": ["identity", "error_rate", "latency", "quality"],
    }
    payload.update(updates)
    return CanaryPolicy.model_validate(payload)


def _stable() -> ModelIdentity:
    payload = _payload("scenario_b_known_good_rollback.json")
    return ModelIdentity.model_validate({key: payload[key] for key in ModelIdentity.model_fields})


def _challenger() -> ModelIdentity:
    payload = _payload("scenario_b_invalid_candidate.json")
    return ModelIdentity.model_validate({key: payload[key] for key in ModelIdentity.model_fields})


def _requests() -> list[ReplayRequest]:
    return [
        ReplayRequest(
            request_id=f"request-{index:04d}",
            content_digest=f"{index:064x}",
            image_uri=f"file:///F:/visa/{index:04d}.png",
            expected_label="normal" if index % 5 else "anomaly",
        )
        for index in range(1000)
    ]


def _observations(
    model: ModelIdentity,
    *,
    failures: set[str] | None = None,
    latency_ms: float = 5.0,
) -> list[InferenceObservation]:
    failed = failures or set()
    return [
        InferenceObservation(
            request_id=request.request_id,
            model_digest=model.model_digest,
            latency_ms=latency_ms,
            succeeded=request.request_id not in failed,
            prediction="normal" if request.request_id not in failed else None,
            confidence=0.9 if request.request_id not in failed else None,
            failure_code="fixture_injected_error" if request.request_id in failed else None,
        )
        for request in _requests()
    ]


def test_policy_rejects_allocation_above_bound() -> None:
    with pytest.raises(ValidationError, match="allocation bound"):
        _policy(challenger_requests=101)


def test_router_assigns_exactly_one_hundred_requests_deterministically() -> None:
    first = build_assignment_routes(_requests(), policy=_policy())
    second = build_assignment_routes(_requests(), policy=_policy())

    assert first == second
    assert sum(route == "challenger" for route, _ in first.values()) == 100
    assert sum(route == "stable" for route, _ in first.values()) == 900


def test_real_invalid_candidate_is_blocked_before_canary() -> None:
    fixture = _payload("scenario_b_invalid_candidate.json")
    stable = _stable()
    challenger = _challenger()
    result = run_controlled_replay(
        run_id="scenario-b-quality-block",
        policy=_policy(),
        stable=stable,
        challenger=challenger,
        requests=_requests(),
        stable_observations=_observations(stable),
        challenger_observations=_observations(challenger, latency_ms=13.0),
        challenger_quality=QualityMetrics.model_validate(fixture["quality"]),
        started_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )

    assert result.status == "blocked"
    assert result.decision.state == "blocked_admission"
    assert result.decision.blocker_codes == [fixture["expected_blocker"]]
    assert len(result.shadow_ledger) == 500
    assert result.assignment_ledger == []
    assert result.rollback.action == "stable_route_retained"
    assert result.rollback.restored_model_digest == stable.model_digest
    assert result.production_mutated is False


def test_failed_stable_shadow_observation_blocks_result() -> None:
    stable = _stable()
    stable_observations = _observations(stable)
    stable_observations[0] = stable_observations[0].model_copy(
        update={
            "succeeded": False,
            "prediction": None,
            "confidence": None,
            "failure_code": "stable_http_error:HTTPError",
        }
    )

    with pytest.raises(ValidationError, match="stable authoritative"):
        run_controlled_replay(
            run_id="scenario-b-stable-shadow-failure",
            policy=_policy(),
            stable=stable,
            challenger=_challenger(),
            requests=_requests(),
            stable_observations=stable_observations,
            challenger_observations=_observations(_challenger()),
            challenger_quality=QualityMetrics(accuracy=0.7, f1=0.6, auroc=0.9),
        )


def test_runtime_error_breach_stops_allocation_and_restores_exact_stable_route() -> None:
    stable = _stable()
    challenger = _challenger()
    routes = build_assignment_routes(_requests(), policy=_policy())
    challenger_ids = [
        request_id for request_id, (route, _) in routes.items() if route == "challenger"
    ]
    failures = set(challenger_ids[:2])
    result = run_controlled_replay(
        run_id="scenario-b-runtime-rollback",
        policy=_policy(),
        stable=stable,
        challenger=challenger,
        requests=_requests(),
        stable_observations=_observations(stable),
        challenger_observations=_observations(challenger, failures=failures),
        challenger_quality=QualityMetrics(accuracy=0.9, f1=0.8, auroc=0.9),
        started_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        stop_seconds=0.2,
        rollback_seconds=0.4,
    )

    assert result.status == "rolled_back"
    assert result.decision.blocker_codes == ["runtime_error_rate_exceeded"]
    assert result.decision.challenger_allocation_after == 0
    assert result.metric_window is not None
    assert result.metric_window.challenger_error_rate == 0.02
    assert result.metric_window.challenger_requests == 100
    assert result.rollback.exact_identity_restored is True
    assert result.rollback.restored_model_digest == stable.model_digest


def test_identity_mismatch_fails_before_runtime_decision() -> None:
    observations = _observations(_challenger())
    observations[0] = observations[0].model_copy(update={"model_digest": "f" * 64})

    with pytest.raises(ValueError, match="observation_model_identity_mismatch"):
        run_controlled_replay(
            run_id="scenario-b-identity-block",
            policy=_policy(),
            stable=_stable(),
            challenger=_challenger(),
            requests=_requests(),
            stable_observations=_observations(_stable()),
            challenger_observations=observations,
            challenger_quality=QualityMetrics(accuracy=0.9, f1=0.8, auroc=0.9),
        )


def test_known_good_window_reaches_bounded_canary_without_mutation() -> None:
    result = run_controlled_replay(
        run_id="scenario-b-pass-fixture",
        policy=_policy(),
        stable=_stable(),
        challenger=_challenger(),
        requests=_requests(),
        stable_observations=_observations(_stable()),
        challenger_observations=_observations(_challenger(), latency_ms=12.0),
        challenger_quality=QualityMetrics(accuracy=0.9, f1=0.8, auroc=0.9),
    )

    assert result.status == "passed"
    assert result.decision.state == "canary_passed"
    assert result.metric_window is not None
    assert result.metric_window.challenger_fraction == 0.1
    assert result.metric_window.identity_match_fraction == 1
    assert result.production_mutated is False


def test_evidence_writer_indexes_every_artifact(tmp_path: Path) -> None:
    result = run_controlled_replay(
        run_id="scenario-b-evidence",
        policy=_policy(),
        stable=_stable(),
        challenger=_challenger(),
        requests=_requests(),
        stable_observations=_observations(_stable()),
        challenger_observations=_observations(_challenger()),
        challenger_quality=QualityMetrics(accuracy=0.9, f1=0.8, auroc=0.9),
    )
    index_path = write_controlled_replay_evidence(
        root=tmp_path,
        result=result,
        requests=_requests(),
        canonical_evidence_root=Path("F:/canonical-evidence"),
    )
    index = json.loads(index_path.read_text(encoding="utf-8"))

    assert len(index["artifacts"]) == 8
    for artifact in index["artifacts"]:
        assert artifact["uri"].startswith("F:/canonical-evidence/scenario-b-evidence/")
        relative = Path(artifact["uri"]).relative_to(
            Path("F:/canonical-evidence/scenario-b-evidence")
        )
        path = tmp_path / "scenario-b-evidence" / relative
        assert path.is_file()
        assert sha256_file(path) == artifact["sha256"]
