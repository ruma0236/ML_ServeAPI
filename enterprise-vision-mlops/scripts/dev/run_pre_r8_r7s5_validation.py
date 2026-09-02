from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPT_PROJECT_ROOT))
sys.path.insert(0, str(SCRIPT_PROJECT_ROOT / "src"))

from scripts.dev import publish_pre_r8_r7s5_review as publisher  # noqa: E402


SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s5.validation-runner.v1"
VALIDATION_OBSERVATION_SCOPE = (
    "validation_orchestrator_exact_command_plan_only_not_descendant_os_telemetry"
)


class ValidationRunnerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    argv: tuple[str, ...]
    expected_exit_code: int = 0
    required_output_tokens: tuple[str, ...] = ()


def canonical_json_bytes(value: Any) -> bytes:
    return publisher.canonical_json_bytes(value)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return publisher.sha256_file(path)


def exclusive_durable_write(path: Path, raw: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise ValidationRunnerError(f"no_overwrite:{path.name}") from exc


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise ValidationRunnerError(f"git_{args[0]}_failed:{result.returncode}")
    return result.stdout.strip()


def _selected_validation_files(project_root: Path) -> list[str]:
    files = [
        "scripts/dev/publish_pre_r8_r7s5_review.py",
        "scripts/dev/run_pre_r8_r7s5_validation.py",
        "scripts/dev/validate_pre_r8_r7s5_ci.py",
        "tests/test_phase_b2_r7s1.py",
        "tests/test_publish_pre_r8_r7s5_review.py",
        "tests/test_scenario_workload_production.py",
        "tests/test_task_queue_process_safety.py",
    ]
    files.extend(
        item.relative_to(project_root).as_posix()
        for item in sorted(
            (project_root / "src" / "evm" / "scale_validation").glob("phase_b2_r7s5_*.py")
        )
    )
    files.extend(
        item.relative_to(project_root).as_posix()
        for item in sorted((project_root / "tests").glob("*r7s5*.py"))
    )
    result = sorted(set(files))
    if not result or any(not (project_root / item).is_file() for item in result):
        raise ValidationRunnerError("selected_validation_file_missing")
    return result


def build_command_specs(
    *,
    repository: Path,
    project_root: Path,
    python_general: Path,
    python_host: Path,
    python_ruff: Path,
) -> tuple[CommandSpec, ...]:
    files = _selected_validation_files(project_root)
    r7s5_tests = sorted(
        item.relative_to(project_root).as_posix()
        for item in (project_root / "tests").glob("*r7s5*.py")
    )
    focused = sorted(
        set(
            r7s5_tests
            + [
                "tests/test_publish_pre_r8_r7s5_review.py",
                "tests/test_scenario_workload_production.py",
                "tests/test_task_queue_process_safety.py",
            ]
        )
    )
    host_tests = (
        "tests/test_pre_r8_r7s2_contract_stager.py",
        "tests/test_pre_r8_r7s2_outer_launcher.py",
        "tests/test_pre_r8_r7s2_wsl_qualification.py",
    )
    powershell_root = str(repository).replace("'", "''")
    ast_script = (
        f"$ErrorActionPreference='Stop'; $root=[IO.Path]::GetFullPath('{powershell_root}'); "
        "$files=@(& git -C $root ls-files -- '*.ps1'); $count=0; "
        "foreach($relative in $files){$tokens=$null;$errors=$null;"
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        "[IO.Path]::Combine($root,$relative),[ref]$tokens,[ref]$errors);"
        "if($errors.Count -ne 0){throw ('ast_error:'+ $relative)};$count++};"
        "Write-Output ('powershell_ast_files='+$count);Write-Output 'powershell_ast_errors=0'"
    )
    common_pytest = ("-m", "pytest", "-q", "-p", "no:cacheprovider")
    specs = (
        CommandSpec(
            "r7s5-focused-pytest-py311",
            (str(python_general), *common_pytest, *focused),
        ),
        CommandSpec(
            "full-general-pytest-py311",
            (
                str(python_general),
                *common_pytest,
                "tests",
                *(f"--ignore={item}" for item in host_tests),
            ),
        ),
        CommandSpec(
            "pinned-host-pytest-py313",
            (str(python_host), *common_pytest, *host_tests),
        ),
        CommandSpec("ruff-check-0.12.2", (str(python_ruff), "-m", "ruff", "check", *files)),
        CommandSpec(
            "ruff-format-check-0.12.2",
            (str(python_ruff), "-m", "ruff", "format", "--check", *files),
        ),
        CommandSpec("py-compile-py311", (str(python_general), "-m", "py_compile", *files)),
        CommandSpec(
            "powershell-ast",
            (
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                ast_script,
            ),
        ),
        CommandSpec("git-diff-check", ("git", "-C", str(repository), "diff", "--check", "HEAD")),
        CommandSpec(
            "ci-manifest-validator",
            (
                str(python_general),
                "scripts/dev/validate_pre_r8_r7s5_ci.py",
                "manifest",
                "--manifest",
                "ci/pre-r8-r7s5-test-lanes.json",
                "--project-root",
                ".",
                "--lane",
                "portable",
            ),
            required_output_tokens=(
                '"status":"manual_intervention_required"',
                '"go_evidence_eligible":false',
            ),
        ),
        CommandSpec(
            "ci-active-workflow-required-rejection",
            (
                str(python_general),
                "scripts/dev/validate_pre_r8_r7s5_ci.py",
                "workflow",
                "--manifest",
                "ci/pre-r8-r7s5-test-lanes.json",
                "--project-root",
                ".",
                "--workflow",
                "../.github/workflows/enterprise-vision-mlops-ci.yml",
            ),
            expected_exit_code=2,
            required_output_tokens=(
                "workflow_action_ref_inventory_mismatch",
                '"status":"rejected"',
            ),
        ),
        CommandSpec(
            "ci-mutation-pytest",
            (
                str(python_general),
                *common_pytest,
                "tests/test_phase_b2_r7s5_ci.py",
            ),
        ),
    )
    if {item.name for item in specs} != publisher.REQUIRED_VALIDATION_COMMANDS:
        raise ValidationRunnerError("required_validation_command_set_mismatch")
    return specs


def _resolved_executable(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve(strict=True)
    located = shutil.which(value)
    if located is None:
        raise ValidationRunnerError(f"command_executable_not_found:{value}")
    return Path(located).resolve(strict=True)


def _tool_version_argv(spec: CommandSpec, executable: Path) -> tuple[str, ...]:
    if spec.name.startswith("ruff-"):
        return (str(executable), "-m", "ruff", "--version")
    if "pytest" in spec.name:
        return (str(executable), "-m", "pytest", "--version")
    if spec.name == "powershell-ast":
        return (
            str(executable),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$PSVersionTable.PSVersion.ToString()",
        )
    if spec.name == "git-diff-check":
        return (str(executable), "--version")
    return (str(executable), "--version")


def command_tool_identity(spec: CommandSpec) -> dict[str, Any]:
    executable = _resolved_executable(spec.argv[0])
    version_argv = _tool_version_argv(spec, executable)
    result = subprocess.run(
        version_argv,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValidationRunnerError(f"command_tool_version_failed:{spec.name}")
    version = (result.stdout + result.stderr).decode("utf-8", errors="replace").strip()
    if not version:
        raise ValidationRunnerError(f"command_tool_version_empty:{spec.name}")
    runtime_version_argv = (
        version_argv
        if spec.name in {"powershell-ast", "git-diff-check"}
        else (str(executable), "--version")
    )
    runtime_result = subprocess.run(
        runtime_version_argv,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    runtime_version = (
        (runtime_result.stdout + runtime_result.stderr).decode("utf-8", errors="replace").strip()
    )
    if runtime_result.returncode != 0 or not runtime_version:
        raise ValidationRunnerError(f"command_runtime_version_failed:{spec.name}")
    if spec.name.startswith("ruff-") and version != "ruff 0.12.2":
        raise ValidationRunnerError("ruff_version_not_exact_0.12.2")
    if "py311" in spec.name and not runtime_version.startswith("Python 3.11."):
        raise ValidationRunnerError(f"py311_runtime_version_mismatch:{spec.name}")
    if "py313" in spec.name and not runtime_version.startswith("Python 3.13."):
        raise ValidationRunnerError(f"py313_runtime_version_mismatch:{spec.name}")
    return {
        "path": str(executable),
        "bytes": executable.stat().st_size,
        "sha256": sha256_file(executable),
        "version_argv": list(version_argv),
        "version": version,
        "runtime_version_argv": list(runtime_version_argv),
        "runtime_version": runtime_version,
    }


def command_plan(
    *,
    repository: Path,
    project_root: Path,
    head: str,
    tree: str,
    specs: Sequence[CommandSpec],
) -> dict[str, Any]:
    commands = []
    for spec in specs:
        commands.append(
            {
                "name": spec.name,
                "argv": list(spec.argv),
                "cwd": str(project_root),
                "expected_exit_code": spec.expected_exit_code,
                "required_output_tokens": list(spec.required_output_tokens),
                "tool": command_tool_identity(spec),
            }
        )
    payload = {
        "repository": str(repository),
        "project_root": str(project_root),
        "head": head,
        "tree": tree,
        "commands": commands,
        "observation_scope": VALIDATION_OBSERVATION_SCOPE,
    }
    return {
        **payload,
        "sha256": sha256_bytes(canonical_json_bytes(payload)),
    }


def _tail(raw: bytes, limit: int = 16_384) -> str:
    return raw[-limit:].decode("utf-8", errors="replace")


def run_validation(args: argparse.Namespace) -> dict[str, Any]:
    repository = args.repository.resolve(strict=True)
    project_root = args.project_root.resolve(strict=True)
    if project_root != SCRIPT_PROJECT_ROOT:
        raise ValidationRunnerError("validation_script_project_origin_mismatch")
    publisher_origin = Path(publisher.__file__).resolve(strict=True)
    if SCRIPT_PROJECT_ROOT not in publisher_origin.parents:
        raise ValidationRunnerError("validation_publisher_module_origin_mismatch")
    if repository not in project_root.parents:
        raise ValidationRunnerError("project_root_not_inside_repository")
    for interpreter in (args.python_general, args.python_host, args.python_ruff):
        if not interpreter.resolve(strict=True).is_file():
            raise ValidationRunnerError("interpreter_file_required")
    if _git(repository, "rev-parse", "HEAD") != args.expected_head:
        raise ValidationRunnerError("head_mismatch")
    if _git(repository, "rev-parse", "HEAD^{tree}") != args.expected_tree:
        raise ValidationRunnerError("tree_mismatch")
    if _git(repository, "status", "--porcelain=v1", "--untracked-files=no"):
        raise ValidationRunnerError("tracked_changes_present")

    parent = args.output_parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    output = parent / args.output_leaf
    try:
        output.mkdir()
    except FileExistsError as exc:
        raise ValidationRunnerError("validation_output_exists") from exc

    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command_refs: list[dict[str, Any]] = []
    all_passed = True
    specs = build_command_specs(
        repository=repository,
        project_root=project_root,
        python_general=args.python_general.resolve(strict=True),
        python_host=args.python_host.resolve(strict=True),
        python_ruff=args.python_ruff.resolve(strict=True),
    )
    plan = command_plan(
        repository=repository,
        project_root=project_root,
        head=args.expected_head,
        tree=args.expected_tree,
        specs=specs,
    )
    for index, (spec, planned) in enumerate(zip(specs, plan["commands"], strict=True), start=1):
        before_head = _git(repository, "rev-parse", "HEAD")
        before_tree = _git(repository, "rev-parse", "HEAD^{tree}")
        before_clean = not _git(repository, "status", "--porcelain=v1", "--untracked-files=no")
        started = datetime.now(UTC)
        start_ns = time.monotonic_ns()
        result = subprocess.run(
            spec.argv,
            cwd=project_root,
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        duration_ns = time.monotonic_ns() - start_ns
        ended = datetime.now(UTC)
        after_head = _git(repository, "rev-parse", "HEAD")
        after_tree = _git(repository, "rev-parse", "HEAD^{tree}")
        after_clean = not _git(repository, "status", "--porcelain=v1", "--untracked-files=no")
        identity_stable = (
            before_head == after_head == args.expected_head
            and before_tree == after_tree == args.expected_tree
            and before_clean
            and after_clean
        )
        combined_output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        passed = (
            result.returncode == spec.expected_exit_code
            and identity_stable
            and all(token in combined_output for token in spec.required_output_tokens)
        )
        all_passed = all_passed and passed
        record = {
            "schema": publisher.COMMAND_EVIDENCE_SCHEMA,
            "name": spec.name,
            "status": "PASS" if passed else "FAIL",
            "exit_code": result.returncode,
            "expected_exit_code": spec.expected_exit_code,
            "argv": list(spec.argv),
            "cwd": str(project_root),
            "repository": str(repository),
            "repository_head_before": before_head,
            "repository_head_after": after_head,
            "repository_tree_before": before_tree,
            "repository_tree_after": after_tree,
            "tracked_clean_before": before_clean,
            "tracked_clean_after": after_clean,
            "command_plan_sha256": plan["sha256"],
            "tool": planned["tool"],
            "started_at_utc": started.isoformat().replace("+00:00", "Z"),
            "ended_at_utc": ended.isoformat().replace("+00:00", "Z"),
            "duration_ns": duration_ns,
            "stdout_bytes": len(result.stdout),
            "stdout_sha256": sha256_bytes(result.stdout),
            "stdout_tail": _tail(result.stdout),
            "stderr_bytes": len(result.stderr),
            "stderr_sha256": sha256_bytes(result.stderr),
            "stderr_tail": _tail(result.stderr),
            "automatic_retry_count": 0,
            "orchestrator_prohibited_live_command_calls": 0,
            "live_call_observation_scope": VALIDATION_OBSERVATION_SCOPE,
        }
        raw = canonical_json_bytes(record)
        path = output / f"{index:02d}-{spec.name}.json"
        exclusive_durable_write(path, raw)
        command_refs.append(
            {
                "name": spec.name,
                "status": record["status"],
                "exit_code": result.returncode,
                "expected_exit_code": spec.expected_exit_code,
                "evidence_path": str(path),
                "evidence_bytes": len(raw),
                "evidence_sha256": sha256_bytes(raw),
            }
        )

    summary = {
        "schema": publisher.VALIDATION_SCHEMA,
        "status": "PASS" if all_passed else "FAIL",
        "repository": str(repository),
        "project_root": str(project_root),
        "head": args.expected_head,
        "tree": args.expected_tree,
        "command_plan": plan,
        "command_plan_sha256": plan["sha256"],
        "commands": command_refs,
        "live_call_counts": {name: 0 for name in publisher.REQUIRED_ZERO_LIVE_CALLS},
        "live_call_observation_scope": VALIDATION_OBSERVATION_SCOPE,
        "completion_marker_created": False,
        "success_marker_created": False,
        "r8_authorized": False,
    }
    raw_summary = canonical_json_bytes(summary)
    summary_path = output / "code-validation-summary.json"
    exclusive_durable_write(summary_path, raw_summary)
    result_record = {
        "schema": SCHEMA,
        "status": summary["status"],
        "output_directory": str(output),
        "summary_path": str(summary_path),
        "summary_bytes": len(raw_summary),
        "summary_sha256": sha256_bytes(raw_summary),
        "automatic_retry_count": 0,
        "orchestrator_prohibited_live_command_calls": 0,
        "live_call_observation_scope": VALIDATION_OBSERVATION_SCOPE,
        "completion_marker_created": False,
    }
    print(json.dumps(result_record, ensure_ascii=False, sort_keys=True))
    return result_record


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run append-only offline r7s5 code validation")
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output-parent", type=Path, required=True)
    parser.add_argument("--output-leaf", required=True)
    parser.add_argument("--python-general", type=Path, required=True)
    parser.add_argument("--python-host", type=Path, required=True)
    parser.add_argument("--python-ruff", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-tree", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    result = run_validation(parse_args(argv))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
