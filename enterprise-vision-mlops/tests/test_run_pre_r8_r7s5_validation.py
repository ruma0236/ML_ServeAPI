from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scripts.dev import publish_pre_r8_r7s5_review as publisher
from scripts.dev import run_pre_r8_r7s5_validation as runner


PROJECT_ROOT = Path(__file__).parents[1]
REPOSITORY = PROJECT_ROOT.parent


def test_validation_plan_is_exact_offline_and_has_no_retry_or_live_command() -> None:
    specs = runner.build_command_specs(
        repository=REPOSITORY,
        project_root=PROJECT_ROOT,
        python_general=Path(sys.executable),
        python_host=Path(sys.executable),
        python_ruff=Path(sys.executable),
    )
    assert {item.name for item in specs} == publisher.REQUIRED_VALIDATION_COMMANDS
    assert len(specs) == len(publisher.REQUIRED_VALIDATION_COMMANDS)
    manifest_spec = next(item for item in specs if item.name == "ci-manifest-validator")
    assert manifest_spec.argv[-2:] == ("--lane", "portable")
    assert '"status":"manual_intervention_required"' in manifest_spec.required_output_tokens
    workflow_spec = next(
        item for item in specs if item.name == "ci-active-workflow-required-rejection"
    )
    assert workflow_spec.expected_exit_code == 2
    assert "workflow_action_ref_inventory_mismatch" in workflow_spec.required_output_tokens
    rendered = "\n".join(" ".join(item.argv).lower() for item in specs)
    for forbidden in (
        "docker compose",
        "wsl --shutdown",
        "logman",
        "taskkill",
        "fresh_phase_b2",
        "integrated_v4",
    ):
        assert forbidden not in rendered


def test_validation_evidence_write_is_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    runner.exclusive_durable_write(path, b"first\n")
    with pytest.raises(runner.ValidationRunnerError, match="no_overwrite"):
        runner.exclusive_durable_write(path, b"second\n")
    assert path.read_bytes() == b"first\n"


def test_selected_validation_files_include_runner_publisher_and_all_r7s5_modules() -> None:
    files = runner._selected_validation_files(PROJECT_ROOT)
    assert "scripts/dev/run_pre_r8_r7s5_validation.py" in files
    assert "scripts/dev/publish_pre_r8_r7s5_review.py" in files
    assert "src/evm/scale_validation/phase_b2_r7s5_evidence.py" in files
    assert "tests/test_phase_b2_r7s5_evidence.py" in files
    assert len(files) == len(set(files))
