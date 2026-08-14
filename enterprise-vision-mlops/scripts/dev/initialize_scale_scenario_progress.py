from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evm.scale_validation.catalog import SCENARIO_DEFINITIONS, validate_catalog  # noqa: E402
from evm.scale_validation.contracts import (  # noqa: E402
    BENCHMARK_SCHEMA_VERSION,
    BenchmarkEvidence,
    PRIVATE_INDEX_SCHEMA_VERSION,
    PROGRESS_SCHEMA_VERSION,
    SCENARIO_TITLES,
    AcceptanceCriterion,
    ChangedComponent,
    PrivateEvidenceIndex,
    ScenarioProgress,
    ScenarioProgressLedger,
    render_progress_markdown,
)


AUTHORITATIVE_PLAN = "docs/agenda/2026-08-15-distributed-scale-operational-validation-plan-v3.md"


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
        is_s0 = scenario_id == "S0"
        changed_components = []
        implementation_summary = ["Implementation has not started."]
        status = "planned"
        if is_s0:
            changed_components = [
                ChangedComponent(
                    component="Serving readiness contract",
                    files=["apps/api/main.py", "tests/test_api_metrics.py"],
                ),
                ChangedComponent(
                    component="Scale-validation evidence contracts",
                    files=[
                        "src/evm/scale_validation/contracts.py",
                        "src/evm/scale_validation/catalog.py",
                        "scripts/dev/initialize_scale_scenario_progress.py",
                        "scripts/dev/validate_scale_scenario_progress.py",
                        "tests/test_scale_scenario_progress.py",
                    ],
                ),
            ]
            implementation_summary = [
                "Readiness now maps degraded dependency state to HTTP 503.",
                "Strict progress and benchmark evidence contracts are being implemented.",
            ]
            status = "implementing"

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
                experiment_environment=(
                    "Not exercised. Planned scope is a generalized local single-node container "
                    "and Kubernetes runtime with one accelerator where required."
                ),
                test_or_experiment_steps=definition["steps"],
                acceptance_criteria=criteria,
                observed_result=None,
                evidence_artifacts=[],
                status=status,
                claim_boundary=(
                    "No production, customer traffic, multi-zone HA, or physical multi-node "
                    "claim is allowed from this scenario until separately exercised."
                ),
                unresolved_items=list(definition["acceptance"]),
                next_action=definition["next_action"],
                architecture_before=definition["before"],
                architecture_after=definition["after"],
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
