"""Static fail-closed validator for the bounded r7s4 CI bootstrap contract.

This validator proves only that repository text matches the reviewed local
contract.  It cannot authenticate action owners, the hosted runner image, or
the externally supplied action/artifact digests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


UV_VERSION = "0.12.5"
UV_WHEEL_URL = (
    "https://files.pythonhosted.org/packages/93/22/"
    "dacc9a0bc8604187a1ba954a3aef8329e4104eb0af772d2c3c634893bd9b/"
    "uv-0.12.5-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
)
UV_WHEEL_SHA256 = "3e195ccf1ed60c8bb24a6447ce306441a4181d54b602407e09bc56e963911c15"
UV_WHEEL_BYTES = 23_657_089
UV_EXECUTABLE_SHA256 = "b65f23a420c4acc96427efb30e5ed9bc0f7e25d2d712000f6ede77c1a0de5f46"
UV_EXECUTABLE_BYTES = 59_019_440
EXPECTED_NORMALIZED_WORKFLOW_SHA256 = (
    "59480d0e050c690517ba4c5fb4adb00f62b4bfe574b73e7134241e7fbba2579f"
)
REQUIREMENTS_RELATIVE_PATH = "ci/pre-r8-r7s4-uv-bootstrap.txt"
ATTRIBUTES_RULES = (
    "/.github/workflows/enterprise-vision-mlops-ci.yml text eol=lf",
    "/enterprise-vision-mlops/ci/pre-r8-r7s4-uv-bootstrap.txt text eol=lf",
)
CANONICAL_ATTRIBUTES = ("\n".join(ATTRIBUTES_RULES) + "\n").encode("utf-8")
CANONICAL_REQUIREMENTS = (
    "# schema: evm.pre-r8-r7s4.uv-bootstrap.v1\n"
    f"# artifact-bytes: {UV_WHEEL_BYTES}\n"
    f"uv @ {UV_WHEEL_URL} --hash=sha256:{UV_WHEEL_SHA256}\n"
).encode("utf-8")
CANONICAL_BOOTSTRAP_SCRIPT = f'''set -euo pipefail
bootstrap_directory="$RUNNER_TEMP/pre-r8-r7s4-uv-bootstrap"
mkdir -p "$bootstrap_directory"
python -m pip download --require-hashes --only-binary=:all: --no-deps --dest "$bootstrap_directory" -r {REQUIREMENTS_RELATIVE_PATH}
uv_wheel="$bootstrap_directory/uv-0.12.5-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
uv_wheel_bytes="{UV_WHEEL_BYTES}"
uv_wheel_sha256="{UV_WHEEL_SHA256}"
test -f "$uv_wheel"
test "$(stat -c '%s' "$uv_wheel")" = "$uv_wheel_bytes"
printf '%s  %s\\n' "$uv_wheel_sha256" "$uv_wheel" | sha256sum --check --strict
local_requirements="$RUNNER_TEMP/pre-r8-r7s4-uv-local.txt"
printf 'uv @ file://%s --hash=sha256:%s\\n' "$uv_wheel" "$uv_wheel_sha256" > "$local_requirements"
python -m pip install --require-hashes --only-binary=:all: --no-deps --no-index -r "$local_requirements"
python_scripts_directory="$(python -c 'import pathlib,sysconfig; print(pathlib.Path(sysconfig.get_path("scripts")).resolve(strict=True))')"
uv_candidate="$python_scripts_directory/uv"
uv_executable="$(python -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$uv_candidate")"
test "$uv_executable" = "$uv_candidate"
uv_executable_bytes="{UV_EXECUTABLE_BYTES}"
uv_executable_sha256="{UV_EXECUTABLE_SHA256}"
test -f "$uv_executable"
test "$(stat -c '%s' "$uv_executable")" = "$uv_executable_bytes"
printf '%s  %s\\n' "$uv_executable_sha256" "$uv_executable" | sha256sum --check --strict
test "$("$uv_executable" --version)" = "uv 0.12.5 (210d1f678 2026-08-14)"
"$uv_executable" sync --locked --extra test --python "3.11.11" --no-python-downloads
echo "$PWD/.venv/bin" >> "$GITHUB_PATH"
test "$(node --version)" = "v22.23.2"
test "$(npm --version)" = "10.9.8"
npm --prefix apps/control-panel ci
'''
CANONICAL_VALIDATOR_SCRIPT = """set -euo pipefail
python enterprise-vision-mlops/scripts/dev/validate_pre_r8_r7s4_ci_bootstrap.py \\
  --workflow .github/workflows/enterprise-vision-mlops-ci.yml \\
  --requirements enterprise-vision-mlops/ci/pre-r8-r7s4-uv-bootstrap.txt \\
  --attributes .gitattributes
"""

EXPECTED_ACTION_REFS = Counter(
    {
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262": 1,
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065": 1,
        "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020": 1,
        "azure/setup-kubectl@776406bce94f63e41d621b960d78ee25c8b76ede": 1,
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02": 2,
    }
)
R7S4_RUFF_SCOPE = (
    "scripts/dev/launch_pre_r8_r7s4_root.py",
    "scripts/dev/validate_pre_r8_r7s4_ci_bootstrap.py",
    "src/evm/scale_validation/phase_b2_r7s4_authority.py",
    "src/evm/scale_validation/phase_b2_r7s4_evidence.py",
    "src/evm/scale_validation/phase_b2_r7s4_handle_io.py",
    "src/evm/scale_validation/phase_b2_r7s4_process.py",
    "src/evm/scale_validation/phase_b2_r7s4_receipt_store.py",
    "tests/test_phase_b2_r7s4_authority.py",
    "tests/test_phase_b2_r7s4_evidence.py",
    "tests/test_phase_b2_r7s4_handle_io.py",
    "tests/test_phase_b2_r7s4_process.py",
    "tests/test_pre_r8_r7s4_ci_bootstrap.py",
)
EXPECTED_STEP_NAMES = (
    "Checkout immutable source",
    "Set up Python",
    "Validate immutable r7s4 CI bootstrap",
    "Set up Node.js",
    "Set up kubectl",
    "Install verified dependencies",
    "Enforce pinned pre-r8 Python formatting and lint",
    "Run full Python test suite",
    "Upload Python test diagnostics",
    "Run Control Panel type and contract tests",
    "Render deployment configuration",
    "Validate CycleRun and real-test policy contracts",
    "Build immutable CI evidence bundle",
    "Upload immutable CI evidence",
)
ACTION_LINE_RE = re.compile(r"(?m)^\s*uses:\s*([^\s#]+)\s*(?:#.*)?$")
FULL_ACTION_SHA_RE = re.compile(r"[^/@\s]+/[^/@\s]+@[0-9a-f]{40}")


class R7S4CiBootstrapError(RuntimeError):
    """Raised when workflow or bootstrap requirements are not exact."""


def _extract_literal_run_script(workflow: str, step_name: str) -> str:
    lines = workflow.splitlines(keepends=True)
    marker = f"      - name: {step_name}\n"
    positions = [index for index, line in enumerate(lines) if line == marker]
    if len(positions) != 1:
        raise R7S4CiBootstrapError("workflow_bootstrap_step_not_unique")
    index = positions[0]
    run_positions: list[int] = []
    for candidate in range(index + 1, len(lines)):
        line = lines[candidate]
        if line.startswith("      - name: ") or line == "\n":
            break
        if not line.startswith("        "):
            raise R7S4CiBootstrapError("workflow_bootstrap_step_structure_invalid")
        if line == "        run: |\n":
            run_positions.append(candidate)
            break
    if len(run_positions) != 1:
        raise R7S4CiBootstrapError("workflow_bootstrap_step_structure_invalid")
    run_index = run_positions[0]
    script_lines: list[str] = []
    for line in lines[run_index + 1 :]:
        if line.startswith("      - name: "):
            break
        if line == "\n":
            break
        if not line.startswith("          "):
            raise R7S4CiBootstrapError("workflow_bootstrap_script_indentation_invalid")
        script_lines.append(line[10:])
    if not script_lines:
        raise R7S4CiBootstrapError("workflow_bootstrap_script_missing")
    return "".join(script_lines)


def _validate_lf_attributes(attributes_raw: bytes) -> None:
    without_crlf = attributes_raw.replace(b"\r\n", b"")
    if b"\r" in without_crlf or not attributes_raw.endswith(b"\n"):
        raise R7S4CiBootstrapError("gitattributes_line_endings_invalid")
    if attributes_raw.replace(b"\r\n", b"\n") != CANONICAL_ATTRIBUTES:
        raise R7S4CiBootstrapError("gitattributes_lf_rules_not_canonical")


def _validate_exact_step_order(workflow: str) -> None:
    marker = "    steps:\n"
    if workflow.count(marker) != 1:
        raise R7S4CiBootstrapError("workflow_steps_section_not_unique")
    steps = workflow.split(marker, 1)[1]
    list_entries = [line for line in steps.splitlines() if line.startswith("      - ")]
    expected = [f"      - name: {name}" for name in EXPECTED_STEP_NAMES]
    if list_entries != expected:
        raise R7S4CiBootstrapError("workflow_step_order_not_exact")


def _step_block(workflow: str, step_name: str) -> list[str]:
    lines = workflow.splitlines()
    marker = f"      - name: {step_name}"
    positions = [index for index, line in enumerate(lines) if line == marker]
    if len(positions) != 1:
        raise R7S4CiBootstrapError("workflow_critical_step_not_unique")
    start = positions[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("      - name: "):
            end = index
            break
    return lines[start:end]


def _validate_critical_step_metadata(workflow: str) -> None:
    expected = {
        "Validate immutable r7s4 CI bootstrap": [
            "        shell: bash",
            "        working-directory: ${{ github.workspace }}",
            "        run: |",
        ],
        "Install verified dependencies": ["        shell: bash", "        run: |"],
        "Enforce pinned pre-r8 Python formatting and lint": ["        run: |"],
        "Run full Python test suite": ["        run: >-"],
    }
    for step_name, expected_metadata in expected.items():
        block = _step_block(workflow, step_name)
        metadata = [
            line
            for line in block[1:]
            if line.startswith("        ") and not line.startswith("          ")
        ]
        if metadata != expected_metadata:
            raise R7S4CiBootstrapError(f"workflow_critical_step_metadata_not_exact:{step_name}")


def validate_ci_bootstrap(
    workflow_raw: bytes,
    requirements_raw: bytes,
    attributes_raw: bytes,
) -> dict[str, Any]:
    without_crlf = workflow_raw.replace(b"\r\n", b"")
    if b"\r" in without_crlf or not workflow_raw.endswith(b"\n"):
        raise R7S4CiBootstrapError("workflow_line_endings_invalid")
    normalized_workflow_raw = workflow_raw.replace(b"\r\n", b"\n")
    try:
        workflow = normalized_workflow_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise R7S4CiBootstrapError("workflow_not_utf8") from exc

    action_refs = ACTION_LINE_RE.findall(workflow)
    if not action_refs or any(FULL_ACTION_SHA_RE.fullmatch(item) is None for item in action_refs):
        raise R7S4CiBootstrapError("workflow_action_ref_not_full_commit_sha")
    if Counter(action_refs) != EXPECTED_ACTION_REFS:
        raise R7S4CiBootstrapError("workflow_action_ref_set_mismatch")
    if "ubuntu-latest" in workflow or workflow.count("runs-on: ubuntu-24.04") != 1:
        raise R7S4CiBootstrapError("workflow_runner_not_exact_ubuntu_24_04")
    if workflow.count("      - codex/distributed-scale-validation-plan") != 1:
        raise R7S4CiBootstrapError("canonical_branch_push_trigger_missing")
    if workflow.count("      - .gitattributes\n") != 2:
        raise R7S4CiBootstrapError("gitattributes_ci_path_trigger_not_exact")
    if 'python-version: "3.11.11"' not in workflow:
        raise R7S4CiBootstrapError("workflow_python_patch_not_exact")
    if 'node-version: "22.23.2"' not in workflow:
        raise R7S4CiBootstrapError("workflow_node_patch_not_exact")
    _validate_exact_step_order(workflow)
    _validate_critical_step_metadata(workflow)
    bootstrap_script = _extract_literal_run_script(workflow, "Install verified dependencies")
    if bootstrap_script != CANONICAL_BOOTSTRAP_SCRIPT:
        raise R7S4CiBootstrapError("workflow_bootstrap_script_not_exact")
    validator_header = (
        "      - name: Validate immutable r7s4 CI bootstrap\n"
        "        shell: bash\n"
        "        working-directory: ${{ github.workspace }}\n"
        "        run: |\n"
    )
    if workflow.count(validator_header) != 1:
        raise R7S4CiBootstrapError("workflow_validator_step_structure_not_exact")
    validator_script = _extract_literal_run_script(workflow, "Validate immutable r7s4 CI bootstrap")
    if validator_script != CANONICAL_VALIDATOR_SCRIPT:
        raise R7S4CiBootstrapError("workflow_validator_call_not_exact")
    forbidden_tokens = (
        'pip install "uv==',
        "pip install --upgrade pip",
        "--no-verify-hashes",
        "UV_INSECURE_HOST",
        "|| true",
        "command -v uv",
        "\nuv --version",
        "\nuv sync",
    )
    for token in forbidden_tokens:
        if token in workflow:
            raise R7S4CiBootstrapError(f"workflow_bootstrap_forbidden_token:{token}")
    lint_script = _extract_literal_run_script(
        workflow, "Enforce pinned pre-r8 Python formatting and lint"
    )
    for relative_path in R7S4_RUFF_SCOPE:
        if lint_script.count(relative_path) != 2:
            raise R7S4CiBootstrapError(f"workflow_r7s4_ruff_scope_not_exact:{relative_path}")
    if requirements_raw != CANONICAL_REQUIREMENTS:
        raise R7S4CiBootstrapError("uv_bootstrap_requirements_not_exact")
    _validate_lf_attributes(attributes_raw)
    normalized_workflow_sha256 = hashlib.sha256(normalized_workflow_raw).hexdigest()
    if normalized_workflow_sha256 != EXPECTED_NORMALIZED_WORKFLOW_SHA256:
        raise R7S4CiBootstrapError("workflow_normalized_sha256_not_exact")

    return {
        "schema": "evm.pre-r8-r7s4.ci-bootstrap-validation.v1",
        "status": "local_structure_pass_external_trust_unproven",
        "action_refs": action_refs,
        "runner": "ubuntu-24.04",
        "normalized_workflow_sha256": normalized_workflow_sha256,
        "python": "3.11.11",
        "node": "v22.23.2",
        "npm": "10.9.8",
        "uv": {
            "version": UV_VERSION,
            "url": UV_WHEEL_URL,
            "sha256": UV_WHEEL_SHA256,
            "bytes": UV_WHEEL_BYTES,
            "installed_executable_sha256": UV_EXECUTABLE_SHA256,
            "installed_executable_bytes": UV_EXECUTABLE_BYTES,
            "installed_executable_path_bound": True,
        },
        "r7s4_ruff_scope": list(R7S4_RUFF_SCOPE),
        "independent_action_provenance_verified": False,
        "hosted_runner_image_digest_verified": False,
        "windows_handle_tests_executed_in_ci": False,
        "windows_handle_tests_require_separate_local_evidence": True,
        "same_repository_self_consistent_mutation_protected": False,
        "go_evidence_eligible": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--attributes", type=Path, required=True)
    args = parser.parse_args(argv)
    result = validate_ci_bootstrap(
        args.workflow.read_bytes(),
        args.requirements.read_bytes(),
        args.attributes.read_bytes(),
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
