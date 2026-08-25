from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.s1_runtime import canonical_write  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run exact S8 closure regressions.")
    parser.add_argument("--private-closure-root", type=Path, required=True)
    return parser.parse_args()


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def python_command(*args: str) -> list[str]:
    return [sys.executable, *args]


def run_suite(
    *,
    suite_id: str,
    commands: list[list[str]],
    public_command: str,
    output_root: Path,
    revision: str,
    environment: dict[str, str],
) -> None:
    started_at = now()
    output = bytearray()
    exit_code = 0
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            check=False,
            timeout=900,
        )
        output.extend(completed.stdout)
        output.extend(completed.stderr)
        exit_code = completed.returncode
        if exit_code:
            break
    log_path = output_root / f"{suite_id}.log"
    log_path.write_bytes(bytes(output))
    canonical_write(
        output_root / f"{suite_id}.meta.json",
        {
            "schema_version": "evm.s8_regression_command.v1",
            "suite_id": suite_id,
            "source_revision": revision,
            "public_command": public_command,
            "started_at": started_at,
            "ended_at": now(),
            "exit_code": exit_code,
        },
    )
    if exit_code:
        raise RuntimeError(f"s8_regression_failed:{suite_id}:{exit_code}")


def main() -> int:
    args = parse_args()
    if not os.getenv("EVM_TEST_CONTROL_PLANE_DATABASE_URL"):
        raise RuntimeError("EVM_TEST_CONTROL_PLANE_DATABASE_URL is required")
    if subprocess.run(["git", "diff", "--quiet"], cwd=ROOT, check=False).returncode:
        raise RuntimeError("s8_regressions_require_clean_tracked_worktree")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    output_root = args.private_closure_root / "regressions"
    output_root.mkdir(parents=True, exist_ok=True)
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise RuntimeError("npm executable is required")
    ruff = shutil.which("ruff.exe") or shutil.which("ruff")
    if not ruff:
        raise RuntimeError("ruff executable is required")
    environment = dict(os.environ)
    lifecycle_tests = [
        str(path.relative_to(ROOT))
        for pattern in ("test_lifecycle*.py", "test_host_runtime*.py")
        for path in sorted((ROOT / "tests").glob(pattern))
    ]
    suites = [
        (
            "changed_file_lint",
            [
                [
                    ruff,
                    "check",
                    "src/evm/scale_validation/s8_evidence.py",
                    "src/evm/scale_validation/s8_closure.py",
                    "scripts/dev/run_s8_current_revision_smoke.py",
                    "scripts/dev/run_s8_closure_regressions.py",
                    "scripts/dev/close_s8_dependency_soak.py",
                    "scripts/dev/validate_s8_dependency_soak_evidence.py",
                    "tests/test_s8_evidence.py",
                    "tests/test_s8_closure.py",
                    "src/evm/model_runtime/triton_blue_green.py",
                    "src/evm/scale_validation/s6bm_observability.py",
                    "src/evm/scale_validation/s6bm_runtime.py",
                    "scripts/dev/run_s8_v4_s6bm_experiment.py",
                    "scripts/dev/validate_s8_v4_s6bm.py",
                    "scripts/dev/write_s8_v4_s6bm_review.py",
                    "tests/test_s6bm_experiment_runner.py",
                    "tests/test_s6bm_observability.py",
                    "tests/test_s6bm_runtime.py",
                    "tests/test_triton_blue_green.py",
                ]
            ],
            "ruff check <S8 closure changed files>",
        ),
        (
            "focused_s6bm",
            [
                python_command(
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_s6bm_experiment_runner.py",
                    "tests/test_s6bm_observability.py",
                    "tests/test_s6bm_runtime.py",
                    "tests/test_triton_blue_green.py",
                    "tests/test_v4_progress_ledger.py",
                )
            ],
            "python -m pytest -q <focused S6B-M runtime/validator/ledger tests>",
        ),
        (
            "focused_s8_closure",
            [
                python_command(
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_s8_runtime.py",
                    "tests/test_s8_evidence.py",
                    "tests/test_s8_reprojection.py",
                    "tests/test_s8_closure.py",
                    "tests/test_api_container_contract.py",
                )
            ],
            "python -m pytest -q <focused S8 runtime/evidence/closure/API container tests>",
        ),
        (
            "real_postgresql",
            [
                python_command(
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_transactional_control_plane.py",
                    "tests/test_bounded_task_queue.py",
                    "tests/test_task_queue_reconciliation.py",
                )
            ],
            "EVM_TEST_CONTROL_PLANE_DATABASE_URL=<local-secret> python -m pytest -q <real PostgreSQL control-plane tests>",
        ),
        (
            "lifecycle_host_e2e",
            [python_command("-m", "pytest", "-q", *lifecycle_tests)],
            "python -m pytest -q tests/test_lifecycle*.py tests/test_host_runtime*.py",
        ),
        (
            "s0_s8_status_evidence",
            [
                python_command(
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_scale_validation_evidence.py",
                    "tests/test_scale_scenario_progress.py",
                    "tests/test_s3_capacity_evidence.py",
                    "tests/test_s4_gpu_batching_evidence.py",
                    "tests/test_s5_evidence.py",
                    "tests/test_s6_evidence.py",
                    "tests/test_s7_evidence.py",
                    "tests/test_s8_evidence.py",
                    "tests/test_s8_closure.py",
                )
            ],
            "python -m pytest -q <S0-S8 status and evidence validators>",
        ),
        (
            "full_python",
            [python_command("-m", "pytest", "-q")],
            "EVM_TEST_CONTROL_PLANE_DATABASE_URL=<local-secret> python -m pytest -q",
        ),
        (
            "control_panel",
            [
                python_command(
                    "-m",
                    "pytest",
                    "-q",
                    *[
                        str(path.relative_to(ROOT))
                        for path in sorted((ROOT / "tests").glob("test_control_panel*.py"))
                    ],
                ),
                [npm, "--prefix", "apps/control-panel", "run", "test", "--", "--run"],
            ],
            "python -m pytest -q tests/test_control_panel*.py && npm --prefix apps/control-panel run test -- --run",
        ),
        (
            "frontend_production_build",
            [[npm, "--prefix", "apps/control-panel", "run", "build"]],
            "npm --prefix apps/control-panel run build",
        ),
    ]
    for suite_id, commands, public_command in suites:
        run_suite(
            suite_id=suite_id,
            commands=commands,
            public_command=public_command,
            output_root=output_root,
            revision=revision,
            environment=environment,
        )
        print(f"passed:{suite_id}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
