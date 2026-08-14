from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from evm.scale_validation.contracts import (
    BENCHMARK_SCHEMA_VERSION,
    PROGRESS_SCHEMA_VERSION,
    SCENARIO_TITLES,
    AcceptanceCriterion,
    BenchmarkClosure,
    BenchmarkControlRun,
    BenchmarkEnvironment,
    BenchmarkEvidence,
    BenchmarkIdentity,
    EvidenceArtifact,
    LoadProfile,
    MetricObservation,
    ScenarioProgress,
    ScenarioProgressLedger,
    TracePropagationEvidence,
    render_progress_markdown,
)


NOW = datetime(2026, 8, 15, tzinfo=UTC)
ARTIFACT = EvidenceArtifact(
    path="docs/status/evidence/s0-summary.json",
    sha256="a" * 64,
    generated_at=NOW,
    claim="A generalized validation result.",
)


def scenario(scenario_id: str, *, status: str = "planned") -> ScenarioProgress:
    criterion_status = "passed" if status == "verified" else "pending"
    evidence = [ARTIFACT] if status in {"exercised", "verified"} else []
    evidence_refs = [ARTIFACT.path] if status == "verified" else []
    return ScenarioProgress(
        scenario_id=scenario_id,
        title=SCENARIO_TITLES[scenario_id],
        engineering_question="Can the declared engineering boundary be measured?",
        why_now="The current implementation lacks accepted scale evidence.",
        observed_gap="The result has not been exercised.",
        proposed_design=["Add a bounded and observable validation path."],
        changed_components=[],
        implementation_summary=["Implementation has not started."],
        experiment_environment="Generalized local single-node environment.",
        test_or_experiment_steps=["Run a deterministic validation."],
        acceptance_criteria=[
            AcceptanceCriterion(
                criterion_id=f"{scenario_id}-AC-01",
                description="The deterministic validation passes.",
                status=criterion_status,
                evidence_refs=evidence_refs,
            )
        ],
        observed_result="The validation passed." if evidence else None,
        evidence_artifacts=evidence,
        status=status,
        claim_boundary="No production or multi-node claim.",
        unresolved_items=[] if status == "verified" else ["Run the validation."],
        next_action="Execute the declared test.",
        architecture_before="No verified boundary.",
        architecture_after="A machine-verified boundary.",
    )


def ledger() -> ScenarioProgressLedger:
    return ScenarioProgressLedger(
        schema_version=PROGRESS_SCHEMA_VERSION,
        generated_at=NOW,
        authoritative_plan="docs/agenda/distributed-scale-plan.md",
        public_record=True,
        claim_boundary="Local development evidence only.",
        scenarios=[scenario(scenario_id) for scenario_id in SCENARIO_TITLES],
    )


def test_progress_ledger_preserves_authoritative_scenario_order() -> None:
    progress = ledger()

    assert [item.scenario_id for item in progress.scenarios] == list(SCENARIO_TITLES)
    assert "## S8: Dependency Soak & Resource-efficiency Closure" in render_progress_markdown(
        progress
    )


def test_progress_rejects_title_drift_and_absolute_public_paths() -> None:
    payload = scenario("S0").model_dump()
    payload["title"] = "Renumbered scenario"
    with pytest.raises(ValidationError, match="title must remain"):
        ScenarioProgress.model_validate(payload)

    artifact = ARTIFACT.model_dump()
    artifact["path"] = "C:/private/evidence.json"
    with pytest.raises(ValidationError, match="repository-relative"):
        EvidenceArtifact.model_validate(artifact)


def test_verified_progress_requires_passed_criteria_and_hashed_evidence() -> None:
    payload = scenario("S0").model_dump()
    payload["status"] = "verified"
    payload["observed_result"] = "Claimed pass without proof."
    with pytest.raises(ValidationError, match="require a result and evidence"):
        ScenarioProgress.model_validate(payload)

    verified = scenario("S0", status="verified")
    assert verified.status == "verified"


def metric(name: str, statistics: dict[str, float]) -> MetricObservation:
    return MetricObservation(
        metric=name,
        unit="unit",
        sample_count=10,
        statistics=statistics,
        query=f"query_for_{name}",
    )


def control_run(repetition: int) -> BenchmarkControlRun:
    return BenchmarkControlRun(
        repetition=repetition,
        started_at=NOW + timedelta(minutes=repetition),
        finished_at=NOW + timedelta(minutes=repetition, seconds=30),
        metrics=[
            metric("request_latency_seconds", {"p50": 0.01, "p95": 0.02, "p99": 0.03}),
            metric("request_throughput_per_second", {"mean": 10.0}),
            metric("queue_depth", {"max": 1.0}),
            metric("queue_oldest_age_seconds", {"p99": 0.01}),
            metric("worker_active_count", {"mean": 1.0}),
            metric("cpu_utilization_ratio", {"mean": 0.2}),
            metric("memory_working_set_bytes", {"max": 1024.0}),
            metric("gpu_utilization_ratio", {"mean": 0.3}),
            metric("gpu_memory_used_bytes", {"max": 2048.0}),
            metric("connection_pool_wait_seconds", {"p99": 0.001}),
            metric("retry_attempt_total", {"sum": 0.0}),
        ],
        evidence_artifacts=[ARTIFACT.model_copy(update={"path": f"evidence/s0/run-{repetition}.json"})],
    )


def benchmark_payload() -> dict[str, object]:
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "scenario_id": "S0",
        "benchmark_suite_id": "generalized-control-suite",
        "generated_at": NOW,
        "identity": BenchmarkIdentity(
            source_revision="a" * 40,
            data_digest="b" * 64,
            model_digest="c" * 64,
            runtime_digest="d" * 64,
        ),
        "environment": BenchmarkEnvironment(
            physical_nodes=1,
            cpu_logical_count=8,
            ram_gib=16,
            gpu_count=1,
            gpu_class="single-consumer-accelerator",
            load_generator_placement="co_located",
            environment_scope="local_single_node",
        ),
        "load_profile": LoadProfile(
            mode="low_load_control",
            concurrency=1,
            target_requests_per_second=1,
            warmup_seconds=1,
            duration_seconds=30,
            seed=7,
        ),
        "control_runs": [control_run(1), control_run(2), control_run(3)],
        "trace_propagation": TracePropagationEvidence(
            required_stages=["api", "queue", "worker", "spark", "mlflow", "serving"],
            observed_stages=["api", "queue", "worker", "spark", "mlflow", "serving"],
            missing_propagation_count=0,
            metric_labels_bounded=True,
            trace_artifact=ARTIFACT.model_copy(
                update={"path": "evidence/s0/trace-propagation.json"}
            ),
        ),
        "variance": {"request_latency_p95_cv": 0.02, "request_throughput_cv": 0.01},
        "closure": BenchmarkClosure(decision="passed", blockers=[], completed_at=NOW),
    }


def test_passed_s0_benchmark_requires_three_complete_control_runs() -> None:
    payload = benchmark_payload()
    payload["control_runs"] = [control_run(1), control_run(2)]
    with pytest.raises(ValidationError, match="at least three independent"):
        BenchmarkEvidence.model_validate(payload)

    evidence = BenchmarkEvidence.model_validate(benchmark_payload())
    assert evidence.closure.decision == "passed"
    assert len(evidence.control_runs) == 3
