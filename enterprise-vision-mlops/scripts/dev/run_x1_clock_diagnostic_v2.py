from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evm.scale_validation.clock_diagnostic import (
    ClockDiagnosticThresholds,
    analyze_clock_window,
    capture_os_clock_sample,
)


CHILD_CLOCK_SCRIPT = r"""
import json, os, platform, socket, sys, time

domain = sys.argv[1]
count = int(sys.argv[2])
cadence_ns = int(sys.argv[3])
clock_boottime = getattr(time, "CLOCK_BOOTTIME", None)
print(json.dumps({
    "kind": "metadata",
    "domain": domain,
    "hostname": socket.gethostname(),
    "pid": os.getpid(),
    "platform": platform.platform(),
    "monotonic": vars(time.get_clock_info("monotonic")),
    "time": vars(time.get_clock_info("time")),
}, sort_keys=True), flush=True)
started = time.monotonic_ns()
for sequence in range(count):
    deadline = started + sequence * cadence_ns
    remaining = deadline - time.monotonic_ns()
    if remaining > 0:
        time.sleep(remaining / 1_000_000_000)
    before = time.monotonic_ns()
    wall = time.time_ns()
    after = time.monotonic_ns()
    boottime = time.clock_gettime_ns(clock_boottime) if clock_boottime is not None else None
    print(json.dumps({
        "kind": "sample",
        "domain": domain,
        "sequence": sequence,
        "monotonic_before_ns": before,
        "wall_unix_ns": wall,
        "monotonic_after_ns": after,
        "boottime_ns": boottime,
    }, sort_keys=True), flush=True)
"""


def _unix_ns(value: datetime) -> int:
    observed = value if value.tzinfo else value.replace(tzinfo=UTC)
    delta = observed.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return ((delta.days * 86_400) + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _canonical_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")


def _run_child(command: list[str], *, domain: str) -> dict[str, Any]:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{domain} clock collector failed: {result.stderr.strip()}")
    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    metadata = [record for record in records if record.get("kind") == "metadata"]
    samples = [record for record in records if record.get("kind") == "sample"]
    if len(metadata) != 1:
        raise RuntimeError(f"{domain} clock collector metadata is incomplete")
    return {"metadata": metadata[0], "samples": samples}


def _collect_host(thresholds: ClockDiagnosticThresholds) -> dict[str, Any]:
    metadata = {
        "kind": "metadata",
        "domain": "windows_host",
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "platform": platform.platform(),
        "monotonic": vars(time.get_clock_info("monotonic")),
        "time": vars(time.get_clock_info("time")),
    }
    samples: list[dict[str, Any]] = []
    started = time.monotonic_ns()
    for sequence in range(thresholds.sample_count):
        remaining = started + sequence * thresholds.cadence_ns - time.monotonic_ns()
        if remaining > 0:
            time.sleep(remaining / 1_000_000_000)
        samples.append(
            capture_os_clock_sample(
                domain="windows_host",
                sequence=sequence,
                monotonic_ns=time.monotonic_ns,
                wall_ns=time.time_ns,
            )
        )
    return {"metadata": metadata, "samples": samples}


def _timestamp_row(connection: Any) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT clock_timestamp() AS clock_at,
               statement_timestamp() AS statement_at,
               transaction_timestamp() AS transaction_at,
               now() AS now_at,
               pg_backend_pid() AS backend_pid
        """
    ).fetchone()
    return dict(row)


def _collect_database(
    database_url: str,
    thresholds: ClockDiagnosticThresholds,
) -> dict[str, Any]:
    import psycopg
    from psycopg.rows import dict_row

    samples: list[dict[str, Any]] = []
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as connection:
        started = time.monotonic_ns()
        for sequence in range(thresholds.sample_count):
            remaining = started + sequence * thresholds.cadence_ns - time.monotonic_ns()
            if remaining > 0:
                time.sleep(remaining / 1_000_000_000)
            send_monotonic = time.monotonic_ns()
            send_wall = time.time_ns()
            row = _timestamp_row(connection)
            receive_wall = time.time_ns()
            receive_monotonic = time.monotonic_ns()
            samples.append(
                {
                    "sequence": sequence,
                    "client_monotonic_send_ns": send_monotonic,
                    "client_wall_send_unix_ns": send_wall,
                    "database_clock_unix_ns": _unix_ns(row["clock_at"]),
                    "database_statement_unix_ns": _unix_ns(row["statement_at"]),
                    "database_transaction_unix_ns": _unix_ns(row["transaction_at"]),
                    "database_now_unix_ns": _unix_ns(row["now_at"]),
                    "client_wall_receive_unix_ns": receive_wall,
                    "client_monotonic_receive_ns": receive_monotonic,
                    "backend_pid": int(row["backend_pid"]),
                }
            )
        with connection.transaction():
            first = _timestamp_row(connection)
            time.sleep(0.05)
            second = _timestamp_row(connection)
    semantics = {
        "first_clock_unix_ns": _unix_ns(first["clock_at"]),
        "first_statement_unix_ns": _unix_ns(first["statement_at"]),
        "first_transaction_unix_ns": _unix_ns(first["transaction_at"]),
        "first_now_unix_ns": _unix_ns(first["now_at"]),
        "second_clock_unix_ns": _unix_ns(second["clock_at"]),
        "second_statement_unix_ns": _unix_ns(second["statement_at"]),
        "second_transaction_unix_ns": _unix_ns(second["transaction_at"]),
        "second_now_unix_ns": _unix_ns(second["now_at"]),
    }
    return {"samples": samples, "transaction_timestamp_semantics": semantics}


def _git(command: str) -> str:
    result = subprocess.run(
        ["git", *command.split()], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return result.stdout.strip()


def _source_blob(path: str) -> dict[str, Any]:
    content = Path(path).read_bytes()
    return {
        "path": path.replace("\\", "/"),
        "working_sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--window-id", required=True)
    parser.add_argument("--database-url", default=os.getenv("EVM_CONTROL_PLANE_DATABASE_URL"))
    parser.add_argument("--wsl-distribution", default="Ubuntu")
    parser.add_argument("--container", default="evm-api")
    parser.add_argument("--duration-seconds", type=int, default=120)
    parser.add_argument("--cadence-ms", type=int, default=100)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.database_url:
        raise RuntimeError("--database-url is required")
    if args.output.exists():
        raise FileExistsError(args.output)
    if args.duration_seconds != 120 or args.cadence_ms != 100:
        raise ValueError("the frozen diagnostic requires exactly 120 seconds at 100 ms cadence")
    if args.wsl_distribution != "Ubuntu" or args.container != "evm-api":
        raise ValueError("the frozen diagnostic requires Ubuntu and evm-api clock domains")
    sample_count = args.duration_seconds * 1_000 // args.cadence_ms
    thresholds = ClockDiagnosticThresholds(
        sample_count=sample_count,
        cadence_ns=args.cadence_ms * 1_000_000,
    )
    child_arguments = [str(sample_count), str(thresholds.cadence_ns)]
    wsl_command = [
        "wsl.exe",
        "-d",
        args.wsl_distribution,
        "--",
        "python3",
        "-u",
        "-c",
        CHILD_CLOCK_SCRIPT,
        "wsl_ubuntu",
        *child_arguments,
    ]
    docker_command = [
        "docker",
        "exec",
        args.container,
        "python",
        "-u",
        "-c",
        CHILD_CLOCK_SCRIPT,
        "docker_evm_api",
        *child_arguments,
    ]
    captured_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with ThreadPoolExecutor(max_workers=4) as pool:
        host_future = pool.submit(_collect_host, thresholds)
        wsl_future = pool.submit(_run_child, wsl_command, domain="wsl_ubuntu")
        docker_future = pool.submit(_run_child, docker_command, domain="docker_evm_api")
        database_future = pool.submit(_collect_database, args.database_url, thresholds)
        host = host_future.result()
        wsl = wsl_future.result()
        docker = docker_future.result()
        database = database_future.result()
    analysis = analyze_clock_window(
        os_domains={
            "windows_host": host["samples"],
            "wsl_ubuntu": wsl["samples"],
            "docker_evm_api": docker["samples"],
        },
        database_samples=database["samples"],
        transaction_semantics=database["transaction_timestamp_semantics"],
        thresholds=thresholds,
    )
    payload = {
        "schema_version": "evm.s8_v4.x1_clock_diagnostic.v2",
        "acceptance_credit": False,
        "credit": "non_credit",
        "window_id": args.window_id,
        "captured_at": captured_at,
        "source_identity": {
            "base_revision": _git("rev-parse HEAD"),
            "base_tree": _git("rev-parse HEAD^{tree}"),
            "working_source_blobs": [
                _source_blob(path)
                for path in (
                    "src/evm/control_panel/transactional_store.py",
                    "src/evm/scale_validation/clock_diagnostic.py",
                    "scripts/dev/run_x1_clock_diagnostic_v2.py",
                )
            ],
            "windows_host": host["metadata"],
            "wsl_ubuntu": wsl["metadata"],
            "docker_evm_api": docker["metadata"],
            "wsl_distribution": args.wsl_distribution,
            "container": args.container,
        },
        "contract": {
            "duration_seconds": args.duration_seconds,
            "cadence_ms": args.cadence_ms,
            "sample_count": sample_count,
            "step_threshold_ns": thresholds.step_threshold_ns,
            "os_bracket_max_ns": thresholds.os_bracket_max_ns,
            "database_rtt_max_ns": thresholds.database_rtt_max_ns,
            "max_uncertain_fraction": thresholds.max_uncertain_fraction,
            "suspend_threshold_ns": thresholds.suspend_threshold_ns,
            "offset_formula": "wall_unix_ns - ((monotonic_before_ns + monotonic_after_ns) // 2)",
            "scheduler_delay_formula": "actual_midpoint_delta_ns - cadence_ns",
            "database_uncertainty": "client query RTT / 2",
            "cross_domain_monotonic_comparison": False,
        },
        "raw": {
            "windows_host": host["samples"],
            "wsl_ubuntu": wsl["samples"],
            "docker_evm_api": docker["samples"],
            "postgresql": database["samples"],
            "transaction_timestamp_semantics": database["transaction_timestamp_semantics"],
        },
        "analysis": analysis,
        "passed": analysis["passed"],
    }
    encoded = _canonical_bytes(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as handle:
        handle.write(encoded)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "passed": payload["passed"],
                "analysis": analysis,
            },
            sort_keys=True,
        )
    )
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
