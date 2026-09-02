from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"
CI_WORKFLOW = ROOT.parent / ".github" / "workflows" / "enterprise-vision-mlops-ci.yml"
THIS_TEST = Path(__file__).resolve()
EXPECTED_RUFF_VERSION = "0.12.2"
EXPECTED_RUFF_REQUIREMENT = f"=={EXPECTED_RUFF_VERSION}"


def _toml(path: Path) -> dict[str, object]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _ruff_executable() -> str | None:
    return shutil.which("ruff.exe") or shutil.which("ruff")


def test_ruff_command_version_is_exactly_pinned() -> None:
    pyproject = _toml(PYPROJECT)

    assert pyproject["tool"]["ruff"]["required-version"] == EXPECTED_RUFF_REQUIREMENT


def test_ruff_is_an_exact_lock_managed_test_dependency() -> None:
    lock = _toml(UV_LOCK)
    packages = lock["package"]
    project = next(package for package in packages if package["name"] == "enterprise-vision-mlops")
    test_dependencies = project["optional-dependencies"]["test"]

    ruff_packages = [package for package in packages if package["name"] == "ruff"]
    assert len(ruff_packages) == 1
    assert ruff_packages[0]["version"] == EXPECTED_RUFF_VERSION
    assert [dependency for dependency in test_dependencies if dependency["name"] == "ruff"] == [
        {"name": "ruff"}
    ]
    requirements = project["metadata"]["requires-dist"]
    assert [requirement for requirement in requirements if requirement["name"] == "ruff"] == [
        {"name": "ruff", "marker": "extra == 'test'", "specifier": "==0.12.2"}
    ]


def test_ci_installs_test_extra_and_mandates_scoped_ruff_checks() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert 'python-version: "3.11.11"' in workflow
    assert "python -m pip download --require-hashes --only-binary=:all: --no-deps" in workflow
    assert "ci/pre-r8-r7s4-uv-bootstrap.txt" in workflow
    assert "pip install --require-hashes --only-binary=:all: --no-deps --no-index" in workflow
    assert 'pip install "uv==0.12.5"' not in workflow
    assert (
        '"$uv_executable" sync --locked --extra test --python "3.11.11" --no-python-downloads'
    ) in workflow
    assert 'echo "$PWD/.venv/bin" >> "$GITHUB_PATH"' in workflow
    assert "pip install --upgrade pip" not in workflow
    assert "python -m ruff check --no-cache" in workflow
    assert "python -m ruff format --check" in workflow
    for relative in (
        "scripts/dev/inventory_pre_r8_r7s3_python_tcb.py",
        "scripts/dev/render_pre_r8_r7s3_oob_candidate.py",
        "src/evm/scale_validation/phase_b2_r7s3_handle_io.py",
        "src/evm/scale_validation/phase_b2_r7s3_process.py",
        "tests/test_phase_b2_r7s3_handle_io.py",
        "tests/test_phase_b2_r7s3_job_capability.py",
        "tests/test_pre_r8_r7s3_oob_candidate.py",
        "tests/test_pre_r8_r7s3_python_tcb_inventory.py",
        "tests/test_pre_r8_r7s3_toolchain_pin.py",
    ):
        assert workflow.count(relative) == 2


def test_local_ruff_accepts_exact_pin_and_rejects_mismatch_without_file_changes() -> None:
    ruff = _ruff_executable()
    if ruff is None:
        pytest.skip("local Ruff executable is not installed; static command pin remains enforced")

    version = subprocess.run(
        [ruff, "--version"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert version.returncode == 0
    assert version.stdout.strip() == f"ruff {EXPECTED_RUFF_VERSION}"
    assert version.stderr == ""

    exact = subprocess.run(
        [ruff, "check", "--no-cache", str(THIS_TEST)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert exact.returncode == 0, exact.stdout + exact.stderr

    mismatched_requirement = "==0.12.3"
    mismatch = subprocess.run(
        [
            ruff,
            "check",
            "--no-cache",
            "--config",
            f"required-version = '{mismatched_requirement}'",
            str(THIS_TEST),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    output = mismatch.stdout + mismatch.stderr
    assert mismatch.returncode == 2
    assert f"Required version `{mismatched_requirement}`" in output
    assert f"running version `{EXPECTED_RUFF_VERSION}`" in output
