from __future__ import annotations

import json
import shlex
import subprocess
import time
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evm.core.pipeline import build_context, display_path, utc_now, write_json, write_markdown_report


def _load_workers(path: Path) -> dict[str, dict[str, Any]]:
    with path.open("rb") as fp:
        payload = tomllib.load(fp)
    return payload.get("workers", {})


def _resolve_home_path(value: str) -> Path | None:
    if not value:
        return None
    return Path(value).expanduser()


def _worker_host(worker: dict[str, Any]) -> str:
    return str(worker.get("dns_name") or worker.get("tailscale_ip") or worker.get("host", "")).rstrip(".")


def _ssh_base_args(worker: dict[str, Any], timeout: int) -> list[str]:
    key_path = _resolve_home_path(str(worker.get("ssh_key_path", "")))
    if key_path is None or not key_path.exists():
        raise RuntimeError(f"SSH key is missing for worker: {worker.get('display_name')}")

    user = str(worker.get("ssh_user", ""))
    host = _worker_host(worker)
    if not user or not host:
        raise RuntimeError(f"SSH user or host is missing for worker: {worker.get('display_name')}")

    return [
        "ssh",
        "-i",
        str(key_path),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={timeout}",
        f"{user}@{host}",
    ]


def _run_ssh(
    worker: dict[str, Any],
    command: str,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_ssh_base_args(worker, min(timeout, 30)), command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _remote_home(worker: dict[str, Any]) -> str:
    result = _run_ssh(worker, 'printf "%s" "$HOME"', timeout=20)
    if result.returncode != 0:
        raise RuntimeError(f"Could not resolve remote HOME: {result.stderr.strip()}")
    return result.stdout.strip()


def _expand_remote_path(worker: dict[str, Any], value: str) -> str:
    if value == "~":
        return _remote_home(worker)
    if value.startswith("~/"):
        return f"{_remote_home(worker)}{value[1:]}"
    return value


def _scp_from_remote(
    worker: dict[str, Any],
    remote_path: str,
    local_path: Path,
    timeout: int,
) -> dict[str, Any]:
    key_path = _resolve_home_path(str(worker.get("ssh_key_path", "")))
    if key_path is None:
        raise RuntimeError(f"SSH key is missing for worker: {worker.get('display_name')}")

    user = str(worker.get("ssh_user", ""))
    host = _worker_host(worker)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "scp",
            "-i",
            str(key_path),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            f"{user}@{host}:{remote_path}",
            str(local_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    return {
        "remote_path": remote_path,
        "local_path": str(local_path),
        "copied": result.returncode == 0,
        "exit_code": result.returncode,
        "stderr": result.stderr.strip(),
    }


def _scp_to_remote(
    worker: dict[str, Any],
    local_path: Path,
    remote_path: str,
    timeout: int,
) -> dict[str, Any]:
    key_path = _resolve_home_path(str(worker.get("ssh_key_path", "")))
    if key_path is None:
        raise RuntimeError(f"SSH key is missing for worker: {worker.get('display_name')}")

    user = str(worker.get("ssh_user", ""))
    host = _worker_host(worker)
    result = subprocess.run(
        [
            "scp",
            "-i",
            str(key_path),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            str(local_path),
            f"{user}@{host}:{remote_path}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    return {
        "local_path": str(local_path),
        "remote_path": remote_path,
        "copied": result.returncode == 0,
        "exit_code": result.returncode,
        "stderr": result.stderr.strip(),
    }


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_key_value_output(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _resource_probe_command(remote_project_path: str) -> str:
    project = shlex.quote(remote_project_path)
    return " && ".join(
        [
            'printf "hostname=%s\\n" "$(hostname)"',
            'printf "os_name=%s\\n" "$(uname -s)"',
            'printf "kernel=%s\\n" "$(uname -r)"',
            'printf "architecture=%s\\n" "$(uname -m)"',
            'printf "cpu_count=%s\\n" "$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || printf unknown)"',
            'printf "memory_bytes=%s\\n" "$(sysctl -n hw.memsize 2>/dev/null || awk \'/MemTotal/ {print $2 * 1024}\' /proc/meminfo 2>/dev/null || printf unknown)"',
            'printf "python=%s\\n" "$(python3 --version 2>&1 || printf unavailable)"',
            'printf "uv=%s\\n" "$(PATH=$HOME/.local/bin:$PATH uv --version 2>&1 || printf unavailable)"',
            f'printf "git_branch=%s\\n" "$(cd {project} && git branch --show-current 2>/dev/null || printf unknown)"',
            f'printf "git_commit=%s\\n" "$(cd {project} && git rev-parse --short HEAD 2>/dev/null || printf unknown)"',
        ]
    )


def _remote_eval_script() -> str:
    return r'''from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_step(label: str, command: list[str], cwd: Path, log_path: Path) -> dict[str, object]:
    started = time.perf_counter()
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n$ {' '.join(command)}\n")
        result = subprocess.run(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        log.write(result.stdout)
    return {
        "label": label,
        "command": command,
        "exit_code": result.returncode,
        "duration_seconds": round(time.perf_counter() - started, 3),
    }


def git_value(project_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.stdout.strip() or "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--summary-path", required=True)
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    project_root = Path(args.project_root)
    report_path = Path(args.report_path)
    summary_path = Path(args.summary_path)
    log_path = Path(args.log_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    steps = [
        run_step("compileall", [sys.executable, "-m", "compileall", "src", "scripts"], project_root, log_path),
        run_step(
            "evm-import",
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, 'src'); import evm; import evm.core.pipeline; print('evm_import_ok')",
            ],
            project_root,
            log_path,
        ),
        run_step("pipeline-cli-help", [sys.executable, "scripts/run_pipeline.py", "--help"], project_root, log_path),
    ]
    status = "success" if all(step["exit_code"] == 0 for step in steps) else "failed"
    summary = {
        "schema_version": "evm.remote_eval.v1",
        "status": status,
        "job_name": args.job_name,
        "run_id": args.run_id,
        "observed_at": utc_now(),
        "host": platform.node(),
        "system": platform.system(),
        "release": platform.release(),
        "architecture": platform.machine(),
        "processor": platform.processor(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "cpu_count": os.cpu_count(),
        "project_root": str(project_root),
        "git_branch": git_value(project_root, "branch", "--show-current"),
        "git_commit": git_value(project_root, "rev-parse", "--short", "HEAD"),
        "steps": steps,
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# Remote ARM64 Evaluation Report",
        "",
        f"- Status: `{status}`",
        f"- Job: `{args.job_name}`",
        f"- Run id: `{args.run_id}`",
        f"- Observed at: `{summary['observed_at']}`",
        f"- Host: `{summary['host']}`",
        f"- System: `{summary['system']} {summary['release']}`",
        f"- Architecture: `{summary['architecture']}`",
        f"- Python: `{summary['python']}`",
        f"- Git branch: `{summary['git_branch']}`",
        f"- Git commit: `{summary['git_commit']}`",
        "",
        "## Steps",
        "",
    ]
    for step in steps:
        lines.append(
            f"- `{step['label']}`: exit_code=`{step['exit_code']}`, duration_seconds=`{step['duration_seconds']}`"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _build_job_command(
    remote_project_path: str,
    remote_script_path: str,
    remote_report_path: str,
    remote_summary_path: str,
    remote_log_path: str,
    run_id: str,
    job_name: str,
    python_version: str,
    uv_bin: str,
) -> str:
    return " && ".join(
        [
            f"cd {shlex.quote(remote_project_path)}",
            'export PATH="$HOME/.local/bin:$PATH"',
            (
                f"{shlex.quote(uv_bin)} run --python {shlex.quote(python_version)} "
                f"python {shlex.quote(remote_script_path)} "
                f"--project-root {shlex.quote(remote_project_path)} "
                f"--report-path {shlex.quote(remote_report_path)} "
                f"--summary-path {shlex.quote(remote_summary_path)} "
                f"--log-path {shlex.quote(remote_log_path)} "
                f"--job-name {shlex.quote(job_name)} "
                f"--run-id {shlex.quote(run_id)}"
            ),
        ]
    )


def run(config_path: str = "configs/local.toml") -> dict[str, object]:
    ctx = build_context("remote_job", config_path)
    cfg = ctx.pipeline_config()
    workers_config = ctx.path(str(cfg.get("workers_config", "configs/workers.toml")))
    workers = _load_workers(workers_config)
    worker_id = str(cfg.get("worker_id", "ruma_macmini"))
    worker = workers.get(worker_id)
    if not worker:
        raise RuntimeError(f"Worker is not configured: {worker_id}")

    timeout_seconds = int(cfg.get("timeout_seconds", 300))
    job_name = str(cfg.get("job_name", "mac-mini-arm64-evaluation"))
    python_version = str(cfg.get("python_version", "3.11"))
    uv_bin = str(cfg.get("uv_bin", "uv"))
    remote_project_path = _expand_remote_path(
        worker,
        str(
            worker.get("remote_project_path")
            or cfg.get("remote_project_path", "~/mlops-lab/ML_ServeAPI/enterprise-vision-mlops")
        ),
    )
    remote_run_root = str(cfg.get("remote_run_root", "artifacts/runs/remote_jobs"))
    remote_run_dir = f"{remote_project_path}/{remote_run_root}/{ctx.run_id}"
    remote_script_path = f"{remote_run_dir}/remote_eval_job.py"
    remote_report_path = f"{remote_run_dir}/remote_eval_report.md"
    remote_summary_path = f"{remote_run_dir}/remote_eval_summary.json"
    remote_log_path = f"{remote_run_dir}/remote_eval.log"

    dataset_metadata = _read_json_if_exists(ctx.path("data/validated/dataset_version.json"))
    registry_metadata = _read_json_if_exists(ctx.path("artifacts/registry/vision-baseline/latest.json"))
    resource_command = _resource_probe_command(remote_project_path)
    local_remote_script_path = ctx.run_dir / "remote_eval_job.py"
    local_remote_script_path.write_text(_remote_eval_script(), encoding="utf-8")
    mkdir_result = _run_ssh(worker, f"mkdir -p {shlex.quote(remote_run_dir)}", timeout=30)
    if mkdir_result.returncode != 0:
        raise RuntimeError(f"Could not create remote run directory: {mkdir_result.stderr.strip()}")
    script_upload = _scp_to_remote(worker, local_remote_script_path, remote_script_path, timeout=60)
    if not script_upload["copied"]:
        raise RuntimeError(f"Could not upload remote eval script: {script_upload['stderr']}")

    job_command = _build_job_command(
        remote_project_path,
        remote_script_path,
        remote_report_path,
        remote_summary_path,
        remote_log_path,
        ctx.run_id,
        job_name,
        python_version,
        uv_bin,
    )
    job_spec = {
        "schema_version": "evm.remote_job.v1",
        "created_at": utc_now(),
        "job_name": job_name,
        "worker_id": worker_id,
        "worker_display_name": worker.get("display_name", worker_id),
        "worker_os": worker.get("os", "unknown"),
        "timeout_seconds": timeout_seconds,
        "remote_project_path": remote_project_path,
        "remote_script_path": remote_script_path,
        "command": job_command,
        "environment": {
            "PATH_PREFIX": "$HOME/.local/bin",
            "PYTHON_VERSION": python_version,
            "UV_BIN": uv_bin,
        },
        "inputs": {
            "dataset_version": dataset_metadata.get("dataset_version", ""),
            "validated_parquet_uri": dataset_metadata.get("validated_parquet_uri", ""),
            "model_name": registry_metadata.get("model_name", ""),
            "model_version": registry_metadata.get("version", ""),
            "model_stage": registry_metadata.get("stage", ""),
        },
        "expected_outputs": {
            "remote_report_path": remote_report_path,
            "remote_summary_path": remote_summary_path,
            "remote_log_path": remote_log_path,
        },
        "trace": ctx.trace.to_dict(),
    }
    job_spec_path = ctx.run_dir / "job_spec.json"
    write_json(job_spec_path, job_spec)

    resource_started = time.perf_counter()
    resource_probe = _run_ssh(worker, resource_command, timeout=60)
    resource_duration = round(time.perf_counter() - resource_started, 3)
    resource_report = {
        "worker_id": worker_id,
        "probe_exit_code": resource_probe.returncode,
        "probe_duration_seconds": resource_duration,
        "observed_at": utc_now(),
        "fields": _parse_key_value_output(resource_probe.stdout),
        "stderr": resource_probe.stderr.strip(),
    }
    resource_report_path = ctx.run_dir / "worker_resource_report.json"
    write_json(resource_report_path, resource_report)

    started = time.perf_counter()
    result = _run_ssh(worker, job_command, timeout=timeout_seconds)
    duration_seconds = round(time.perf_counter() - started, 3)
    stdout_path = ctx.run_dir / "remote_stdout.log"
    stderr_path = ctx.run_dir / "remote_stderr.log"
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")

    artifacts_dir = ctx.run_dir / "collected_artifacts"
    collected_artifacts = [
        _scp_from_remote(worker, remote_report_path, artifacts_dir / "remote_eval_report.md", timeout=60),
        _scp_from_remote(worker, remote_summary_path, artifacts_dir / "remote_eval_summary.json", timeout=60),
        _scp_from_remote(worker, remote_log_path, artifacts_dir / "remote_eval.log", timeout=60),
    ]
    artifacts_collected = all(item["copied"] for item in collected_artifacts)
    status = "success" if result.returncode == 0 and resource_probe.returncode == 0 and artifacts_collected else "failed"

    summary = {
        "status": status,
        "job_name": job_name,
        "worker_id": worker_id,
        "worker_display_name": worker.get("display_name", worker_id),
        "exit_code": result.returncode,
        "duration_seconds": duration_seconds,
        "resource_probe_exit_code": resource_probe.returncode,
        "artifacts_collected": artifacts_collected,
        "job_spec_path": display_path(job_spec_path, ctx.project_root),
        "worker_resource_report_path": display_path(resource_report_path, ctx.project_root),
        "remote_stdout_path": display_path(stdout_path, ctx.project_root),
        "remote_stderr_path": display_path(stderr_path, ctx.project_root),
        "remote_report_path": remote_report_path,
        "remote_summary_path": remote_summary_path,
        "remote_log_path": remote_log_path,
        "remote_script_path": remote_script_path,
        "script_upload": script_upload,
        "collected_artifacts": collected_artifacts,
        "resource_report": resource_report,
        "trace_id": ctx.trace.trace_id,
        "pipeline_run_id": ctx.trace.pipeline_run_id,
    }
    write_json(ctx.run_dir / "summary.json", summary)

    lines = [
        "",
        "## Remote Job Contract",
        "",
        f"- Job spec: `{summary['job_spec_path']}`",
        f"- Worker resource report: `{summary['worker_resource_report_path']}`",
        f"- Remote report path: `{remote_report_path}`",
        f"- Remote summary path: `{remote_summary_path}`",
        f"- Remote log path: `{remote_log_path}`",
        "",
        "## Collected Artifacts",
        "",
    ]
    for artifact in collected_artifacts:
        lines.append(
            f"- `{artifact['remote_path']}` -> `{artifact['local_path']}` copied=`{artifact['copied']}`"
        )

    write_markdown_report(
        ctx,
        "Remote Job Pipeline",
        {
            "status": status,
            "job_name": job_name,
            "worker_id": worker_id,
            "exit_code": result.returncode,
            "duration_seconds": duration_seconds,
            "artifacts_collected": artifacts_collected,
        },
        lines,
    )

    if status != "success":
        raise RuntimeError(f"remote job failed: {json.dumps(summary, ensure_ascii=False)}")
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    config_path = argv[0] if argv else "configs/local.toml"
    print(run(config_path))


if __name__ == "__main__":
    main()
