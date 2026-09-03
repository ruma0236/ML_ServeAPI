from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from scripts.dev import validate_pre_r8_r7s4_ci_bootstrap as validator


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT.parent / ".github" / "workflows" / "enterprise-vision-mlops-ci.yml"
ATTRIBUTES = ROOT.parent / ".gitattributes"
REQUIREMENTS = ROOT / validator.REQUIREMENTS_RELATIVE_PATH


def _inputs() -> tuple[bytes, bytes, bytes]:
    return WORKFLOW.read_bytes(), REQUIREMENTS.read_bytes(), ATTRIBUTES.read_bytes()


def test_exact_workflow_and_uv_bootstrap_contract_passes_without_external_overclaim() -> None:
    result = validator.validate_ci_bootstrap(*_inputs())

    assert result["status"] == "local_structure_pass_external_trust_unproven"
    assert result["runner"] == "ubuntu-24.04"
    assert result["normalized_workflow_sha256"] == validator.EXPECTED_NORMALIZED_WORKFLOW_SHA256
    assert result["python"] == "3.11.11"
    assert result["node"] == "v22.23.2"
    assert result["npm"] == "10.9.8"
    assert result["uv"] == {
        "version": "0.12.5",
        "url": validator.UV_WHEEL_URL,
        "sha256": validator.UV_WHEEL_SHA256,
        "bytes": 23_657_089,
        "installed_executable_sha256": validator.UV_EXECUTABLE_SHA256,
        "installed_executable_bytes": 59_019_440,
        "installed_executable_path_bound": True,
    }
    assert result["independent_action_provenance_verified"] is False
    assert result["hosted_runner_image_digest_verified"] is False
    assert result["windows_handle_tests_executed_in_ci"] is False
    assert result["windows_handle_tests_require_separate_local_evidence"] is True
    assert result["same_repository_self_consistent_mutation_protected"] is False
    assert result["go_evidence_eligible"] is False


def test_requirements_is_one_exact_url_hash_and_byte_contract() -> None:
    raw = REQUIREMENTS.read_bytes()
    assert raw == validator.CANONICAL_REQUIREMENTS
    assert raw.count(validator.UV_WHEEL_URL.encode("ascii")) == 1
    assert raw.count(validator.UV_WHEEL_SHA256.encode("ascii")) == 1
    assert b"artifact-bytes: 23657089" in raw


@pytest.mark.parametrize(
    ("old", "new", "error"),
    [
        (
            b"actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
            b"actions/checkout@v4",
            "workflow_action_ref_not_full_commit_sha",
        ),
        (
            b"actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
            b"actions/setup-python@" + b"0" * 40,
            "workflow_action_ref_set_mismatch",
        ),
        (
            b"azure/setup-kubectl@776406bce94f63e41d621b960d78ee25c8b76ede",
            b"azure/setup-kubectl@c0c8b32d33a5244f1e5947304550403b63930415",
            "workflow_action_ref_set_mismatch",
        ),
        (b"runs-on: ubuntu-24.04", b"runs-on: ubuntu-latest", "workflow_runner"),
        (
            b'node-version: "22.23.2"',
            b'node-version: "22"',
            "workflow_node_patch_not_exact",
        ),
        (
            b"      - codex/distributed-scale-validation-plan\n",
            b"",
            "canonical_branch_push_trigger_missing",
        ),
        (
            b"python -m pip download --require-hashes",
            b"python -m pip download",
            "workflow_bootstrap_script_not_exact",
        ),
        (b"--no-deps --dest", b"--dest", "workflow_bootstrap_script_not_exact"),
        (
            validator.UV_WHEEL_SHA256.encode("ascii"),
            b"0" * 64,
            "workflow_bootstrap_script_not_exact",
        ),
    ],
)
def test_workflow_mutations_fail_closed(old: bytes, new: bytes, error: str) -> None:
    workflow, requirements, attributes = _inputs()
    assert workflow.count(old) >= 1
    mutated = workflow.replace(old, new, 1)

    with pytest.raises(validator.R7S4CiBootstrapError, match=error):
        validator.validate_ci_bootstrap(mutated, requirements, attributes)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: raw.replace(validator.UV_WHEEL_SHA256.encode("ascii"), b"f" * 64),
        lambda raw: raw.replace(b"artifact-bytes: 23657089", b"artifact-bytes: 1"),
        lambda raw: raw.replace(b"uv @ https://", b"uv==0.12.5 # https://"),
        lambda raw: raw + b"ruff==0.12.2\n",
    ],
)
def test_bootstrap_requirement_mutations_fail_closed(
    mutation: Callable[[bytes], bytes],
) -> None:
    workflow, requirements, attributes = _inputs()
    mutated = mutation(requirements)

    with pytest.raises(
        validator.R7S4CiBootstrapError,
        match="uv_bootstrap_requirements_not_exact",
    ):
        validator.validate_ci_bootstrap(workflow, mutated, attributes)


def test_workflow_scopes_new_r7s4_files_for_both_ruff_commands() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    lint_script = validator._extract_literal_run_script(
        workflow, "Enforce pinned pre-r8 Python formatting and lint"
    )
    for relative in validator.R7S4_RUFF_SCOPE:
        assert lint_script.count(relative) == 2


def test_validator_rejects_missing_r7s4_ruff_scope_entry() -> None:
    workflow, requirements, attributes = _inputs()
    relative = b"src/evm/scale_validation/phase_b2_r7s4_receipt_store.py"
    assert workflow.count(relative) == 2

    with pytest.raises(
        validator.R7S4CiBootstrapError,
        match="workflow_r7s4_ruff_scope_not_exact",
    ):
        validator.validate_ci_bootstrap(
            workflow.replace(relative, b"", 1), requirements, attributes
        )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            b'          "$uv_executable" sync --locked',
            b'          true # "$uv_executable" sync --locked',
        ),
        (
            b"          python -m pip install --require-hashes",
            b"          python -m pip install --require-hashes || true #",
        ),
        (
            b'          test "$("$uv_executable" --version)"',
            b'          test "$(uv --version)"',
        ),
        (
            validator.UV_EXECUTABLE_SHA256.encode("ascii"),
            b"0" * 64,
        ),
        (b'node --version)" = "v22.23.2"', b'node --version)" = "v22.23.1"'),
        (b'npm --version)" = "10.9.8"', b'npm --version)" = "10.9.7"'),
    ],
)
def test_exact_bootstrap_script_rejects_comments_bypass_and_unbound_uv(
    old: bytes,
    new: bytes,
) -> None:
    workflow, requirements, attributes = _inputs()
    workflow = workflow.replace(b"\r\n", b"\n")
    assert workflow.count(old) == 1
    with pytest.raises(
        validator.R7S4CiBootstrapError,
        match="workflow_bootstrap_script_not_exact",
    ):
        validator.validate_ci_bootstrap(workflow.replace(old, new, 1), requirements, attributes)


def test_gitattributes_pins_workflow_and_bootstrap_requirements_to_lf() -> None:
    workflow, requirements, attributes = _inputs()
    validator.validate_ci_bootstrap(workflow, requirements, attributes)
    assert attributes == validator.CANONICAL_ATTRIBUTES
    assert len(validator.ATTRIBUTES_RULES) == 26
    assert workflow.count(b"      - .gitattributes\n") == 2
    for rule in validator.ATTRIBUTES_RULES:
        assert attributes.decode("utf-8").splitlines().count(rule) == 1
    validator.validate_ci_bootstrap(
        workflow,
        requirements,
        attributes.replace(b"\n", b"\r\n"),
    )

    mutated = attributes.replace(b"eol=lf", b"eol=crlf", 1)
    with pytest.raises(
        validator.R7S4CiBootstrapError,
        match="gitattributes_lf_rules_not_canonical",
    ):
        validator.validate_ci_bootstrap(workflow, requirements, mutated)

    broad_override = attributes + b"* -text -eol\n"
    with pytest.raises(
        validator.R7S4CiBootstrapError,
        match="gitattributes_lf_rules_not_canonical",
    ):
        validator.validate_ci_bootstrap(workflow, requirements, broad_override)

    missing_trigger = workflow.replace(b"      - .gitattributes\n", b"", 1)
    with pytest.raises(
        validator.R7S4CiBootstrapError,
        match="gitattributes_ci_path_trigger_not_exact",
    ):
        validator.validate_ci_bootstrap(missing_trigger, requirements, attributes)


def test_workflow_executes_validator_once_before_dependency_bootstrap() -> None:
    workflow, requirements, attributes = _inputs()
    normalized = workflow.replace(b"\r\n", b"\n").decode("utf-8")
    assert (
        validator._extract_literal_run_script(normalized, "Validate immutable r7s4 CI bootstrap")
        == validator.CANONICAL_VALIDATOR_SCRIPT
    )
    assert workflow.count(b"      - name: Validate immutable r7s4 CI bootstrap") == 1
    assert workflow.index(b"Validate immutable r7s4 CI bootstrap") < workflow.index(
        b"Install verified dependencies"
    )
    validator.validate_ci_bootstrap(workflow, requirements, attributes)

    mutated = workflow.replace(
        b"          --attributes .gitattributes",
        b"          --attributes /tmp/attacker-attributes",
        1,
    )
    with pytest.raises(
        validator.R7S4CiBootstrapError,
        match="workflow_validator_call_not_exact",
    ):
        validator.validate_ci_bootstrap(mutated, requirements, attributes)


@pytest.mark.parametrize(
    "injected",
    [
        b"      - name: Bypass before bootstrap\n        run: true\n\n",
        b"      - run: true\n\n",
    ],
)
def test_extra_workflow_step_before_bootstrap_is_rejected(injected: bytes) -> None:
    workflow, requirements, attributes = _inputs()
    anchor = b"      - name: Install verified dependencies\n"
    assert workflow.count(anchor) == 1
    mutated = workflow.replace(anchor, injected + anchor, 1)
    with pytest.raises(
        validator.R7S4CiBootstrapError,
        match="workflow_step_order_not_exact",
    ):
        validator.validate_ci_bootstrap(mutated, requirements, attributes)


@pytest.mark.parametrize(
    "step_name",
    [
        b"Validate immutable r7s4 CI bootstrap",
        b"Install verified dependencies",
        b"Enforce pinned pre-r8 Python formatting and lint",
        b"Run full Python test suite",
    ],
)
def test_critical_step_cannot_be_skipped_or_continue_on_error(step_name: bytes) -> None:
    workflow, requirements, attributes = _inputs()
    next_step = workflow.find(b"      - name: ", workflow.index(step_name) + len(step_name))
    assert next_step > 0
    mutated = (
        workflow[:next_step]
        + (b"        if: false\n        continue-on-error: true\n\n")
        + workflow[next_step:]
    )
    with pytest.raises(
        validator.R7S4CiBootstrapError,
        match="workflow_critical_step_metadata_not_exact",
    ):
        validator.validate_ci_bootstrap(mutated, requirements, attributes)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            b"          python -m ruff check --no-cache \\",
            b"          echo \\",
        ),
        (
            b"          python -m ruff format --check \\",
            b"          echo \\",
        ),
        (b"          python -m pytest -q -ra\n", b"          true\n"),
    ],
)
def test_whole_workflow_hash_rejects_critical_command_body_bypass(
    old: bytes,
    new: bytes,
) -> None:
    workflow, requirements, attributes = _inputs()
    workflow = workflow.replace(b"\r\n", b"\n")
    assert workflow.count(old) == 1
    mutated = workflow.replace(old, new, 1)
    with pytest.raises(
        validator.R7S4CiBootstrapError,
        match="workflow_normalized_sha256_not_exact",
    ):
        validator.validate_ci_bootstrap(mutated, requirements, attributes)
