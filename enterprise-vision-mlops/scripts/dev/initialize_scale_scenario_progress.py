from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evm.scale_validation.catalog import (  # noqa: E402
    SCENARIO_DEFINITIONS,
    SCENARIO_IN_PLACE_CONTRACTS,
    validate_catalog,
)
from evm.scale_validation.contracts import (  # noqa: E402
    BENCHMARK_SCHEMA_VERSION,
    ArchitectureDelta,
    BenchmarkEvidence,
    ChronologicalUpdate,
    ExperimentContract,
    ImplementationDelta,
    PRIVATE_INDEX_SCHEMA_VERSION,
    PROGRESS_SCHEMA_VERSION,
    SCENARIO_TITLES,
    AcceptanceCriterion,
    ChangedComponent,
    EvidenceArtifact,
    PrivateEvidenceIndex,
    ScenarioExecutionEvidence,
    ScenarioProgress,
    ScenarioProgressLedger,
    VerdictAndClaimBoundary,
    render_progress_markdown,
)


AUTHORITATIVE_PLAN = "docs/agenda/2026-08-15-distributed-scale-operational-validation-plan-v3.md"
PLAN_REVIEWED_AT = datetime(2026, 8, 14, 19, 34, tzinfo=UTC)
PUBLIC_CLAIM_BOUNDARY = (
    "No production, customer traffic, multi-zone HA, or physical multi-node claim is allowed "
    "from this scenario. A scenario pass does not replace final cross-scenario system validation."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize Scenario S0-S8 progress contracts.")
    parser.add_argument(
        "--progress",
        type=Path,
        default=ROOT / "docs/status/2026-08-15-distributed-scale-scenario-progress.json",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=ROOT / "docs/status/2026-08-15-distributed-scale-scenario-progress.md",
    )
    parser.add_argument(
        "--contracts-dir",
        type=Path,
        default=ROOT / "contracts/distributed-scale",
    )
    parser.add_argument("--private-index", type=Path)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def build_ledger(generated_at: datetime) -> ScenarioProgressLedger:
    validate_catalog()
    scenarios: list[ScenarioProgress] = []
    for scenario_id, definition in SCENARIO_DEFINITIONS.items():
        in_place = SCENARIO_IN_PLACE_CONTRACTS[scenario_id]
        is_s0 = scenario_id == "S0"
        changed_components = []
        checkpoint_evidence: list[EvidenceArtifact] = []
        implementation_summary = ["Implementation has not started."]
        experiment_environment = (
            "Not exercised. Planned scope is a generalized local single-node container "
            "and Kubernetes runtime with one accelerator where required."
        )
        observed_result: str | None = None
        status = "planned"
        updates = [
            ChronologicalUpdate(
                occurred_at=PLAN_REVIEWED_AT,
                phase="design",
                status="planned",
                summary=(
                    "The authoritative in-place scenario contract was reviewed against the "
                    "existing ML Serve API system."
                ),
            )
        ]
        if is_s0:
            checkpoint_evidence = [
                EvidenceArtifact(
                    path="docs/status/evidence/s0-otel-implementation-checkpoint.json",
                    sha256=(
                        "fbef5bc797a91097e136a2510bb4740fd85c9efc5255e9cd5501e68c1fd046d7"
                    ),
                    generated_at=datetime(2026, 8, 14, 21, 9, 10, tzinfo=UTC),
                    claim=(
                        "Collector configuration, one OTLP probe, and 583-test regression passed; "
                        "runtime-wide S0 acceptance remains pending."
                    ),
                ),
                EvidenceArtifact(
                    path=(
                        "docs/status/evidence/"
                        "s0-in-place-telemetry-boundary-checkpoint.json"
                    ),
                    sha256=(
                        "65e86d95d63869f6c47e80f35c14b713d66c7ee56bbe81ce95d1bcabe44ef55e"
                    ),
                    generated_at=datetime(2026, 8, 14, 21, 39, 16, tzinfo=UTC),
                    claim=(
                        "Bounded telemetry, desired-state target discovery, local Spark stage, "
                        "and 588-test regression passed at contract level; runtime S0 "
                        "acceptance remains pending."
                    ),
                ),
                EvidenceArtifact(
                    path=(
                        "docs/status/evidence/"
                        "s0-spark-runtime-path-remediation-checkpoint.json"
                    ),
                    sha256=(
                        "a30afb6ac712b82e6d2e6e768e36f2fce4ce1af738a4b3b90e273fd65e51a9e1"
                    ),
                    generated_at=datetime(2026, 8, 14, 21, 58, 52, tzinfo=UTC),
                    claim=(
                        "A real local Spark computation exposed JVM and cross-runtime path "
                        "gaps; both were remediated with 590-test regression, but a fresh "
                        "accepted Spark evidence run remains pending."
                    ),
                ),
                EvidenceArtifact(
                    path=(
                        "docs/status/evidence/"
                        "s0-spark-runtime-component-checkpoint.json"
                    ),
                    sha256=(
                        "11a31ffe92c5b1b5bd6c4fa1e6ae2e4230ad6497b91b0bbc79c9da766270b62f"
                    ),
                    generated_at=datetime(2026, 8, 14, 22, 11, 4, tzinfo=UTC),
                    claim=(
                        "One real bounded local Spark component run persisted through the "
                        "existing data mount and exported linked OTLP spans; full S0 "
                        "lifecycle acceptance remains pending."
                    ),
                ),
                EvidenceArtifact(
                    path=(
                        "docs/status/evidence/"
                        "s0-serving-runtime-identity-contract-checkpoint.json"
                    ),
                    sha256=(
                        "d47a2b0d7a8d5c47cc3ee7948b85aada58cd40f54f6dea65a131054b9af7dd2a"
                    ),
                    generated_at=datetime(2026, 8, 14, 22, 30, 7, tzinfo=UTC),
                    claim=(
                        "Model-source and serving-runtime revisions are now separate in the "
                        "existing serving contract; 593 tests passed, while live serving "
                        "revision alignment and S0 runtime acceptance remain pending."
                    ),
                ),
                EvidenceArtifact(
                    path=(
                        "docs/status/evidence/"
                        "s0-low-load-control-runner-checkpoint.json"
                    ),
                    sha256=(
                        "f0bd6847bfec46694b4917c463a0774b121b4ba704d5288a1031f35ef4f9b89e"
                    ),
                    generated_at=datetime(2026, 8, 14, 22, 59, 31, tzinfo=UTC),
                    claim=(
                        "An in-place low-load control runner and runtime-wide revision labels "
                        "passed 600 tests; live controls and S0 acceptance remain pending."
                    ),
                ),
                EvidenceArtifact(
                    path=(
                        "docs/status/evidence/"
                        "s0-first-control-rca-checkpoint.json"
                    ),
                    sha256=(
                        "c3f80a305f377e301a591f9758f05032b4a20056acb6da68ca574543d48d7b3e"
                    ),
                    generated_at=datetime(2026, 8, 14, 23, 25, 46, tzinfo=UTC),
                    claim=(
                        "One fresh control traversed Airflow, Spark, MLflow, and real CUDA "
                        "inference but failed closed because the serving OTLP stage was absent; "
                        "the RCA remediation passed 603 tests and acceptance remains pending."
                    ),
                ),
            ]
            changed_components = [
                ChangedComponent(
                    component="Serving readiness contract",
                    files=["apps/api/main.py", "tests/test_api_metrics.py"],
                ),
                ChangedComponent(
                    component="In-place scale-validation evidence contracts",
                    files=[
                        "src/evm/scale_validation/contracts.py",
                        "src/evm/scale_validation/catalog.py",
                        "scripts/dev/initialize_scale_scenario_progress.py",
                        "scripts/dev/validate_scale_scenario_progress.py",
                        "tests/test_scale_scenario_progress.py",
                    ],
                ),
                ChangedComponent(
                    component="W3C trace propagation across existing runtime boundaries",
                    files=[
                        "src/evm/observability/trace_context.py",
                        "src/evm/observability/otel.py",
                        "apps/api/main.py",
                        "apps/api/efficientnet_serving.py",
                        "src/evm/control_panel/lifecycle_runs.py",
                        "src/evm/control_panel/lifecycle_orchestrator.py",
                        "src/evm/control_panel/lifecycle_worker.py",
                        "src/evm/control_panel/operations.py",
                        "src/evm/core/pipeline.py",
                        "src/evm/core/http.py",
                        "src/evm/core/mlflow_client.py",
                    ],
                ),
                ChangedComponent(
                    component="Existing local telemetry runtime",
                    files=[
                        "docker-compose.yml",
                        "monitoring/opentelemetry/collector.yaml",
                        "monitoring/prometheus/prometheus.yml",
                        "scripts/dev/start_lifecycle_worker.ps1",
                        "scripts/dev/start_kubernetes_observer.ps1",
                        "scripts/dev/start_local_stack.ps1",
                        "src/evm/control_panel/kubernetes_observer.py",
                    ],
                ),
                ChangedComponent(
                    component="Bounded serving telemetry and exact endpoint verification",
                    files=[
                        "src/evm/model_runtime/serving.py",
                        "src/evm/model_runtime/workload_runner.py",
                        "src/evm/model_runtime/scenario_workload_production.py",
                        "src/evm/control_panel/lifecycle_orchestrator.py",
                        "tests/test_scenario_model_serving.py",
                        "tests/test_scenario_workload_production.py",
                    ],
                ),
                ChangedComponent(
                    component="Existing Airflow data path Spark boundary",
                    files=[
                        "infra/docker/airflow/Dockerfile",
                        "orchestration/airflow/dags/enterprise_vision_mlops_daily.py",
                        "scripts/run_pipeline.py",
                        "scripts/run_profile_pipeline.py",
                        "src/evm/pipelines/spark_runtime_probe/run.py",
                    ],
                ),
                ChangedComponent(
                    component="Cross-runtime data-root resolution",
                    files=[
                        "src/evm/core/config.py",
                        "tests/test_data_pipeline_empty_guards.py",
                    ],
                ),
                ChangedComponent(
                    component="Existing-runtime S0 low-load control runner",
                    files=[
                        "src/evm/scale_validation/s0_runtime.py",
                        "scripts/dev/run_s0_low_load_control.py",
                        "src/evm/model_runtime/scenario_workload_production.py",
                        "tests/test_s0_runtime.py",
                    ],
                ),
                ChangedComponent(
                    component="Runtime revision and serving OTLP closure",
                    files=[
                        "apps/api/Dockerfile",
                        "apps/api/main.py",
                        "docker-compose.yml",
                        "scripts/dev/start_local_stack.ps1",
                        "scripts/dev/start_scenario_workload_worker.ps1",
                        "src/evm/model_runtime/scenario_workload_production.py",
                        "src/evm/observability/otel.py",
                    ],
                ),
            ]
            implementation_summary = [
                "Readiness now maps degraded dependency state to HTTP 503.",
                "W3C trace identity is propagated through the existing lifecycle contracts.",
                "OTLP export is runtime-accepted for the local Spark component only; lifecycle-wide trace acceptance remains pending.",
                "Prometheus discovery excludes intentionally inactive B0 and uses bounded labels.",
                "The existing Airflow data DAG includes a bounded real local Spark stage.",
                "The Airflow image includes Java 17 and shared path resolution preserves the mounted evidence root across Windows and POSIX runtimes.",
                "Serving readiness now distinguishes immutable model source from the executing serving runtime revision without removing the compatibility source field.",
                "A fail-closed runner now drives the existing stepwise lifecycle, exact CUDA serving, MLflow, scoped queue and worker metrics, and OTLP evidence contract.",
                "A failed fresh control exposed missing serving OTLP configuration and stale API image identity; required serving telemetry and immutable image revision checks are now implemented.",
                "Strict public progress, scenario evidence, and benchmark evidence contracts are implemented at schema level.",
            ]
            experiment_environment = (
                "Partially exercised in the existing generalized local single-node Airflow "
                "runtime. One fresh bounded control traversed Airflow, Spark, MLflow, and real "
                "CUDA inference but was rejected because the serving trace stage was absent. "
                "No accepted full lifecycle control or repeated-control result exists yet."
            )
            status = "implementing"
            updates.extend(
                [
                    ChronologicalUpdate(
                        occurred_at=datetime(2026, 8, 14, 20, 10, tzinfo=UTC),
                        phase="implementation",
                        status="implementing",
                        summary=(
                            "Readiness semantics and strict evidence-contract scaffolding were "
                            "applied to the existing API and repository."
                        ),
                    ),
                    ChronologicalUpdate(
                        occurred_at=datetime(2026, 8, 14, 20, 34, tzinfo=UTC),
                        phase="implementation",
                        status="implementing",
                        summary=(
                            "W3C trace identity was propagated through the existing API, "
                            "lifecycle, queue, and Airflow configuration boundaries; focused "
                            "regression passed, but runtime trace acceptance remains unexecuted."
                        ),
                    ),
                    ChronologicalUpdate(
                        occurred_at=datetime(2026, 8, 14, 21, 40, 31, tzinfo=UTC),
                        phase="implementation",
                        status="implementing",
                        summary=(
                            "Bounded telemetry, active-target reconciliation, and the local Spark "
                            "boundary were implemented in the existing runtime path; 588 tests "
                            "passed, but no live cross-runtime trace or control run is claimed."
                        ),
                        evidence_refs=[checkpoint_evidence[1].path],
                    ),
                    ChronologicalUpdate(
                        occurred_at=datetime(2026, 8, 14, 22, 6, 35, tzinfo=UTC),
                        phase="implementation",
                        status="implementing",
                        summary=(
                            "A real local Spark attempt exposed a missing JVM and then an "
                            "invalid cross-runtime evidence path. Java 17 and shared data-root "
                            "resolution were added; 590 tests and the path contract passed, "
                            "while a fresh accepted Spark run remains pending."
                        ),
                        evidence_refs=[checkpoint_evidence[2].path],
                    ),
                    ChronologicalUpdate(
                        occurred_at=datetime(2026, 8, 14, 22, 14, 32, tzinfo=UTC),
                        phase="experiment",
                        status="implementing",
                        summary=(
                            "One real bounded Spark component run completed in the existing "
                            "Airflow runtime, persisted its report through the shared mount, "
                            "and exported linked parent-child spans. Full lifecycle trace and "
                            "three-control acceptance remain unexecuted."
                        ),
                        evidence_refs=[checkpoint_evidence[3].path],
                    ),
                    ChronologicalUpdate(
                        occurred_at=datetime(2026, 8, 14, 22, 32, 37, tzinfo=UTC),
                        phase="implementation",
                        status="implementing",
                        summary=(
                            "The existing scenario serving contract now separates immutable "
                            "model source from executing runtime source. Focused tests, static "
                            "analysis, and 593-test regression passed; the active service has "
                            "not yet been restarted or accepted as S0 runtime evidence."
                        ),
                        evidence_refs=[checkpoint_evidence[4].path],
                    ),
                    ChronologicalUpdate(
                        occurred_at=datetime(2026, 8, 14, 22, 59, 31, tzinfo=UTC),
                        phase="implementation",
                        status="implementing",
                        summary=(
                            "The existing runtime now has a fail-closed low-load control runner "
                            "and revision-aware OTLP resources. Focused checks and 600-test "
                            "regression passed; no live control or S0 acceptance is claimed."
                        ),
                        evidence_refs=[checkpoint_evidence[5].path],
                    ),
                    ChronologicalUpdate(
                        occurred_at=datetime(2026, 8, 14, 23, 25, 46, tzinfo=UTC),
                        phase="experiment",
                        status="implementing",
                        summary=(
                            "The first fresh control completed the bounded functional path but "
                            "failed closed with the serving trace stage absent. RCA found an "
                            "unconfigured serving child and stale API image identity; the "
                            "in-place remediation passed 603 tests, while accepted controls "
                            "remain at zero."
                        ),
                        evidence_refs=[checkpoint_evidence[6].path],
                    ),
                ]
            )

        criteria = [
            AcceptanceCriterion(
                criterion_id=f"{scenario_id}-AC-{index:02d}",
                description=description,
                status="pending",
                evidence_refs=[],
            )
            for index, description in enumerate(definition["acceptance"], start=1)
        ]
        scenarios.append(
            ScenarioProgress(
                scenario_id=scenario_id,
                title=SCENARIO_TITLES[scenario_id],
                engineering_question=definition["engineering_question"],
                why_now=definition["why_now"],
                observed_gap=definition["observed_gap"],
                proposed_design=definition["proposed_design"],
                changed_components=changed_components,
                implementation_summary=implementation_summary,
                experiment_environment=experiment_environment,
                test_or_experiment_steps=definition["steps"],
                acceptance_criteria=criteria,
                observed_result=observed_result,
                evidence_artifacts=checkpoint_evidence,
                status=status,
                claim_boundary=PUBLIC_CLAIM_BOUNDARY,
                unresolved_items=list(definition["acceptance"]),
                next_action=definition["next_action"],
                architecture_before=definition["before"],
                architecture_after=definition["after"],
                existing_system_baseline=in_place["existing_system_baseline"],
                affected_existing_components=[
                    ChangedComponent.model_validate(item)
                    for item in in_place["affected_components"]
                ],
                engineering_gap_and_reason=definition["observed_gap"],
                architecture_delta=ArchitectureDelta(
                    before=definition["before"],
                    after=definition["after"],
                    selection_reasons=in_place["selection_reasons"],
                    alternatives_and_tradeoffs=in_place["alternatives"],
                ),
                implementation_delta=ImplementationDelta(
                    modified_existing_components=changed_components,
                    compatibility=in_place["compatibility"],
                    migration=in_place["migration"],
                ),
                experiment_contract=ExperimentContract(
                    preconditions=in_place["preconditions"],
                    workload_input=in_place["workload_input"],
                    procedure=definition["steps"],
                    controlled_variables=in_place["controlled_variables"],
                    signals=in_place["signals"],
                    acceptance_criteria=definition["acceptance"],
                    stop_conditions=in_place["stop_conditions"],
                    recovery_conditions=in_place["recovery_conditions"],
                    existing_system_regression_required=True,
                    lifecycle_e2e_regression_required=True,
                ),
                evidence_index=checkpoint_evidence,
                verdict_and_claim_boundary=VerdictAndClaimBoundary(
                    verdict="not_run",
                    claim_boundary=PUBLIC_CLAIM_BOUNDARY,
                    final_system_validation_required=True,
                ),
                chronological_updates=updates,
            )
        )
    return ScenarioProgressLedger(
        schema_version=PROGRESS_SCHEMA_VERSION,
        generated_at=generated_at,
        authoritative_plan=AUTHORITATIVE_PLAN,
        public_record=True,
        claim_boundary=(
            "This ledger reports local development evidence only. Planned or implementing work "
            "is not benchmark, availability, scale, or production proof."
        ),
        scenarios=scenarios,
    )


def write_new(path: Path, content: str, *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def main() -> int:
    args = parse_args()
    generated_at = datetime.now(UTC).replace(microsecond=0)
    ledger = build_ledger(generated_at)
    write_new(
        args.progress,
        json.dumps(ledger.model_dump(mode="json"), indent=2, ensure_ascii=True) + "\n",
        force=args.force,
    )
    write_new(args.markdown, render_progress_markdown(ledger), force=args.force)

    args.contracts_dir.mkdir(parents=True, exist_ok=True)
    schema_targets = {
        args.contracts_dir / "scenario-progress.schema.json": ledger.model_json_schema(),
        args.contracts_dir
        / "scenario-evidence.schema.json": ScenarioExecutionEvidence.model_json_schema(),
        args.contracts_dir
        / "benchmark-evidence.schema.json": BenchmarkEvidence.model_json_schema(),
    }
    for path, schema in schema_targets.items():
        write_new(path, json.dumps(schema, indent=2, ensure_ascii=True) + "\n", force=args.force)

    if args.private_index:
        index = PrivateEvidenceIndex(
            schema_version=PRIVATE_INDEX_SCHEMA_VERSION,
            generated_at=generated_at,
            authoritative_plan=AUTHORITATIVE_PLAN,
            evidence_root=str(args.private_index.parent),
            entries=[],
        )
        write_new(
            args.private_index,
            json.dumps(index.model_dump(mode="json"), indent=2, ensure_ascii=True) + "\n",
            force=args.force,
        )

    print(
        json.dumps(
            {
                "status": "initialized",
                "progress_schema": PROGRESS_SCHEMA_VERSION,
                "benchmark_schema": BENCHMARK_SCHEMA_VERSION,
                "scenario_count": len(ledger.scenarios),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
