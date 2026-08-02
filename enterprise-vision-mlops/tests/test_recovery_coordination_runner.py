from pathlib import Path

from evm.operations.recovery_coordination_runner import run_proof


REVISION = "c0bf42277ec4e227b9a38e0326e638eada736026"


def test_recovery_coordination_proof_runs_three_independent_series(tmp_path: Path) -> None:
    proof = run_proof(
        output=tmp_path,
        source_revision=REVISION,
        recovery_policy_path=Path("configs/operations/recovery_coordination.toml"),
        correlation_policy_path=Path("configs/operations/cross_scenario_correlation.toml"),
    )

    assert proof.passed
    assert proof.total_series == 3
    assert proof.total_authorized_recommendations == 3
    assert proof.total_mutation_intents == 0
    assert proof.production_mutation_count == 0
    assert all(item.coordinator_restart_count == 1 for item in proof.series)
    assert all(item.higher_fence_count == 1 for item in proof.series)


def test_recovery_coordination_proof_closes_artifact_index(tmp_path: Path) -> None:
    run_proof(
        output=tmp_path,
        source_revision=REVISION,
        recovery_policy_path=Path("configs/operations/recovery_coordination.toml"),
        correlation_policy_path=Path("configs/operations/cross_scenario_correlation.toml"),
        series_count=1,
    )

    assert (tmp_path / "series-1" / "incident-plane.json").is_file()
    assert (tmp_path / "validation-report.json").is_file()
    assert (tmp_path / "policy-decision.json").is_file()
    assert (tmp_path / "artifact-index.json").is_file()
