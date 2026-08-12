from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/"
    "artifacts/scenario_workloads/_production/local-ci-evidence.json"
)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(command: list[str], *, root: Path, name: str) -> dict[str, Any]:
    started = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout = result.stdout[-12000:]
    stderr = result.stderr[-12000:]
    material = (stdout + "\n" + stderr).encode("utf-8")
    return {
        "name": name,
        "command": command,
        "status": "pass" if result.returncode == 0 else "fail",
        "exit_code": result.returncode,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "output_sha256": hashlib.sha256(material).hexdigest(),
        "stdout_tail": stdout,
        "stderr_tail": stderr,
    }


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def scoped_status(root: Path) -> list[str]:
    output = git(root, "status", "--porcelain", "--", ".")
    return [line for line in output.splitlines() if line.strip()]


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run exact-revision local CI admission for transformer workloads."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    source_commit = git(root, "rev-parse", "HEAD")
    source_branch = git(root, "branch", "--show-current") or "detached"
    dirty = scoped_status(root)
    commands = [
        (
            "python-contract-tests",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_scenario_workload_production.py",
                "tests/test_workload_gpu_handoff.py",
                "tests/test_scenario_workloads.py",
                "tests/test_control_panel_scenario_workloads_api.py",
            ],
        ),
        (
            "python-source-compile",
            [
                sys.executable,
                "-m",
                "compileall",
                "-q",
                "src/evm/control_panel",
                "src/evm/model_runtime",
                "apps/api",
            ],
        ),
        ("frontend-typecheck", ["npm", "--prefix", "apps/control-panel", "run", "lint"]),
        ("frontend-tests", ["npm", "--prefix", "apps/control-panel", "run", "test", "--", "--run"]),
        ("frontend-production-build", ["npm", "--prefix", "apps/control-panel", "run", "build"]),
        ("compose-contract", ["docker", "compose", "config", "--quiet"]),
    ]
    started_at = utc_now()
    results: list[dict[str, Any]] = []
    for name, command in commands:
        result = run(command, root=root, name=name)
        results.append(result)
        print(f"{name}: {result['status']} ({result['duration_seconds']}s)", flush=True)
        if result["status"] != "pass":
            break
    blockers = []
    if dirty and not args.allow_dirty:
        blockers.append("scenario_local_ci_scoped_worktree_dirty")
    blockers.extend(
        f"scenario_local_ci_command_failed:{item['name']}"
        for item in results
        if item["status"] != "pass"
    )
    payload = {
        "schema_version": "evm.scenario_local_ci_evidence.v1",
        "status": "pass" if not blockers and len(results) == len(commands) else "fail",
        "source_commit": source_commit,
        "source_branch": source_branch,
        "scoped_worktree": str(root),
        "scoped_worktree_dirty": bool(dirty),
        "scoped_status": dirty,
        "started_at": started_at,
        "finished_at": utc_now(),
        "commands": results,
        "blockers": blockers,
        "claim_boundary": (
            "Local exact-revision CI admission on one Windows host; this is not a remote "
            "hosted CI, multi-runner, or supply-chain attestation claim."
        ),
    }
    atomic_write_json(args.output, payload)
    print(args.output)
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
