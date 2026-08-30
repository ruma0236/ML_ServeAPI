from __future__ import annotations

import argparse
import ctypes
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
from typing import Any, Callable

from evm.scale_validation.clock_remediation import (
    ClockRemediationThresholds,
    analyze_remediation_window,
)


ROOT = Path(__file__).resolve().parents[2]
CHILD_CLOCK_SCRIPT = r"""
import json, os, platform, socket, sys, time

domain = sys.argv[1]
count = int(sys.argv[2])
cadence_ns = int(sys.argv[3])
raw_clock = getattr(time, "CLOCK_MONOTONIC_RAW", None)
boottime_clock = getattr(time, "CLOCK_BOOTTIME", None)
if raw_clock is None or boottime_clock is None:
    raise RuntimeError("required Linux raw/boottime clocks are unavailable")
raw_now = lambda: time.clock_gettime_ns(raw_clock)
print(json.dumps({
    "kind": "metadata",
    "domain": domain,
    "hostname": socket.gethostname(),
    "pid": os.getpid(),
    "platform": platform.platform(),
    "clock_realtime": vars(time.get_clock_info("time")),
    "clock_monotonic": vars(time.get_clock_info("monotonic")),
    "clock_monotonic_raw_id": raw_clock,
    "clock_boottime_id": boottime_clock,
}, sort_keys=True), flush=True)
started = raw_now()
for sequence in range(count):
    deadline = started + sequence * cadence_ns
    remaining = deadline - raw_now()
    if remaining > 0:
        time.sleep(remaining / 1_000_000_000)
    raw_before = raw_now()
    realtime = time.clock_gettime_ns(time.CLOCK_REALTIME)
    raw_after = raw_now()
    print(json.dumps({
        "kind": "sample",
        "domain": domain,
        "sequence": sequence,
        "raw_before_ns": raw_before,
        "realtime_unix_ns": realtime,
        "raw_after_ns": raw_after,
        "monotonic_ns": time.clock_gettime_ns(time.CLOCK_MONOTONIC),
        "auxiliary_monotonic_ns": time.clock_gettime_ns(boottime_clock),
    }, sort_keys=True), flush=True)
"""


def _canonical_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")


def _unix_ns(value: datetime) -> int:
    observed = value if value.tzinfo else value.replace(tzinfo=UTC)
    delta = observed.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return ((delta.days * 86_400) + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _windows_clock_sources() -> tuple[Callable[[], int], Callable[[], int], dict[str, Any]]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    frequency = ctypes.c_longlong()
    if not kernel32.QueryPerformanceFrequency(ctypes.byref(frequency)):
        raise ctypes.WinError(ctypes.get_last_error())

    def qpc_ns() -> int:
        counter = ctypes.c_longlong()
        if not kernel32.QueryPerformanceCounter(ctypes.byref(counter)):
            raise ctypes.WinError(ctypes.get_last_error())
        return counter.value * 1_000_000_000 // frequency.value

    kernel32.GetTickCount64.restype = ctypes.c_ulonglong

    def tick_ns() -> int:
        return int(kernel32.GetTickCount64()) * 1_000_000

    return (
        qpc_ns,
        tick_ns,
        {
            "qpc_frequency_hz": frequency.value,
            "qpc_resolution_ns": 1_000_000_000 / frequency.value,
            "get_tick_count_resolution_ns": 1_000_000,
        },
    )


def _collect_windows(thresholds: ClockRemediationThresholds) -> dict[str, Any]:
    raw_now, auxiliary_now, clock_metadata = _windows_clock_sources()
    samples: list[dict[str, Any]] = []
    started = raw_now()
    for sequence in range(thresholds.sample_count):
        remaining = started + sequence * thresholds.cadence_ns - raw_now()
        if remaining > 0:
            time.sleep(remaining / 1_000_000_000)
        raw_before = raw_now()
        realtime = time.time_ns()
        raw_after = raw_now()
        samples.append(
            {
                "domain": "windows_host",
                "sequence": sequence,
                "raw_before_ns": raw_before,
                "realtime_unix_ns": realtime,
                "raw_after_ns": raw_after,
                "monotonic_ns": time.monotonic_ns(),
                "auxiliary_monotonic_ns": auxiliary_now(),
            }
        )
    return {
        "metadata": {
            "kind": "metadata",
            "domain": "windows_host",
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "platform": platform.platform(),
            "clock_realtime": vars(time.get_clock_info("time")),
            "clock_monotonic": vars(time.get_clock_info("monotonic")),
            **clock_metadata,
        },
        "samples": samples,
    }


def _run_child(command: list[str], *, domain: str) -> dict[str, Any]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{domain} collector failed: {result.stderr.strip()}")
    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    metadata = [record for record in records if record.get("kind") == "metadata"]
    samples = [record for record in records if record.get("kind") == "sample"]
    if len(metadata) != 1:
        raise RuntimeError(f"{domain} collector metadata is incomplete")
    return {"metadata": metadata[0], "samples": samples}


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
    thresholds: ClockRemediationThresholds,
) -> dict[str, Any]:
    import psycopg
    from psycopg.rows import dict_row

    raw_now, _, _ = _windows_clock_sources()
    samples: list[dict[str, Any]] = []
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as connection:
        started = raw_now()
        for sequence in range(thresholds.sample_count):
            remaining = started + sequence * thresholds.cadence_ns - raw_now()
            if remaining > 0:
                time.sleep(remaining / 1_000_000_000)
            raw_send_before = raw_now()
            monotonic_send = time.monotonic_ns()
            raw_send_after = raw_now()
            wall_send = time.time_ns()
            row = _timestamp_row(connection)
            wall_receive = time.time_ns()
            raw_receive_before = raw_now()
            monotonic_receive = time.monotonic_ns()
            raw_receive_after = raw_now()
            samples.append(
                {
                    "sequence": sequence,
                    "client_raw_send_before_ns": raw_send_before,
                    "client_monotonic_send_ns": monotonic_send,
                    "client_raw_send_after_ns": raw_send_after,
                    "client_wall_send_unix_ns": wall_send,
                    "database_clock_unix_ns": _unix_ns(row["clock_at"]),
                    "database_statement_unix_ns": _unix_ns(row["statement_at"]),
                    "database_transaction_unix_ns": _unix_ns(row["transaction_at"]),
                    "database_now_unix_ns": _unix_ns(row["now_at"]),
                    "client_wall_receive_unix_ns": wall_receive,
                    "client_raw_receive_before_ns": raw_receive_before,
                    "client_monotonic_receive_ns": monotonic_receive,
                    "client_raw_receive_after_ns": raw_receive_after,
                    "backend_pid": int(row["backend_pid"]),
                }
            )
    return {"samples": samples}


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


def _source_blob(path: str) -> dict[str, Any]:
    content = (ROOT / path).read_bytes()
    return {
        "path": path,
        "working_sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--window-id", required=True)
    parser.add_argument("--mode", choices=("docker-off", "full-stack"), required=True)
    parser.add_argument("--database-url", default=os.getenv("EVM_CONTROL_PLANE_DATABASE_URL"))
    parser.add_argument("--wsl-distribution", default="Ubuntu")
    parser.add_argument("--container", default="evm-api")
    parser.add_argument("--duration-seconds", type=int, default=180)
    parser.add_argument("--cadence-ms", type=int, default=100)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    if args.duration_seconds != 180 or args.cadence_ms != 100:
        raise ValueError("the remediation probe requires exactly 180 seconds at 100 ms cadence")
    if args.wsl_distribution != "Ubuntu" or args.container != "evm-api":
        raise ValueError("the remediation probe requires Ubuntu and evm-api identities")
    if args.mode == "full-stack" and not args.database_url:
        raise RuntimeError("--database-url is required for a full-stack window")
    thresholds = ClockRemediationThresholds(
        sample_count=args.duration_seconds * 1_000 // args.cadence_ms,
        cadence_ns=args.cadence_ms * 1_000_000,
    )
    child_arguments = [str(thresholds.sample_count), str(thresholds.cadence_ns)]
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
        host_future = pool.submit(_collect_windows, thresholds)
        wsl_future = pool.submit(_run_child, wsl_command, domain="wsl_ubuntu")
        docker_future = (
            pool.submit(_run_child, docker_command, domain="docker_evm_api")
            if args.mode == "full-stack"
            else None
        )
        database_future = (
            pool.submit(_collect_database, args.database_url, thresholds)
            if args.mode == "full-stack"
            else None
        )
        host = host_future.result()
        wsl = wsl_future.result()
        docker = docker_future.result() if docker_future else None
        database = database_future.result() if database_future else None
    os_domains = {
        "windows_host": host["samples"],
        "wsl_ubuntu": wsl["samples"],
    }
    if docker is not None:
        os_domains["docker_evm_api"] = docker["samples"]
    analysis = analyze_remediation_window(
        mode=args.mode,
        os_domains=os_domains,
        database_samples=database["samples"] if database else None,
        thresholds=thresholds,
    )
    payload = {
        "schema_version": "evm.s8_v4.x1_clock_remediation_probe.v1",
        "acceptance_credit": False,
        "credit": "non_credit",
        "window_id": args.window_id,
        "mode": args.mode,
        "captured_at": captured_at,
        "source_identity": {
            "base_revision": _git("rev-parse", "HEAD"),
            "base_tree": _git("rev-parse", "HEAD^{tree}"),
            "working_source_blobs": [
                _source_blob(path)
                for path in (
                    "src/evm/scale_validation/clock_remediation.py",
                    "scripts/dev/run_x1_clock_remediation_probe.py",
                )
            ],
            "windows_host": host["metadata"],
            "wsl_ubuntu": wsl["metadata"],
            "docker_evm_api": docker["metadata"] if docker else None,
            "wsl_distribution": args.wsl_distribution,
            "container": args.container if docker else None,
        },
        "contract": {
            "duration_seconds": args.duration_seconds,
            "cadence_ms": args.cadence_ms,
            "sample_count": thresholds.sample_count,
            "step_threshold_ns": thresholds.step_threshold_ns,
            "os_bracket_max_ns": thresholds.os_bracket_max_ns,
            "database_rtt_max_ns": thresholds.database_rtt_max_ns,
            "sampler_gap_extra_ns": thresholds.sampler_gap_extra_ns,
            "cross_clock_delta_tolerance_ns": thresholds.cross_clock_delta_tolerance_ns,
            "raw_offset_formula": "realtime_unix_ns - raw_monotonic_midpoint_ns",
            "database_uncertainty": "client RAW query RTT / 2",
            "cross_domain_raw_comparison": False,
        },
        "raw": {
            "windows_host": host["samples"],
            "wsl_ubuntu": wsl["samples"],
            "docker_evm_api": docker["samples"] if docker else None,
            "postgresql": database["samples"] if database else None,
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
