"""Bounded Windows descendant fixture for the r7s7 non-credit candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

_REQUIRED_BOOTSTRAP_FLAGS = {
    "isolated": 1,
    "no_user_site": 1,
    "no_site": 1,
    "dont_write_bytecode": 1,
}
if any(
    getattr(sys.flags, name, None) != expected
    for name, expected in _REQUIRED_BOOTSTRAP_FLAGS.items()
):
    raise RuntimeError("pre_r8_r7s7_fixture_requires_python_i_b_s")
if (
    not sys.pycache_prefix
    or not Path(sys.pycache_prefix).is_absolute()
    or Path(sys.pycache_prefix).exists()
):
    raise RuntimeError("pre_r8_r7s7_fixture_requires_fresh_absolute_pycache_prefix")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from evm.scale_validation.phase_b2_r7s3_process import (  # noqa: E402
    consume_inherited_job_capability,
)

FIXTURE_SCHEMA = "evm.s8-v4.x1.phase-b2.pre-r8-r7s7.windows-fixture-observation.v1"
DESCENDANT_HOLD_SECONDS = 2.5
CHILD_HANDOFF_SECONDS = 0.15
_HEX64 = frozenset("0123456789abcdef")


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True), flush=True)


def _sleep_mode(seconds: float) -> int:
    time.sleep(seconds)
    return 0


def _verify_pin(path: str, sha256: str, label: str) -> str:
    candidate = Path(path)
    if not candidate.is_absolute() or len(sha256) != 64 or not set(sha256) <= _HEX64:
        raise RuntimeError(f"{label}_pin_invalid")
    candidate = candidate.resolve(strict=True)
    if hashlib.sha256(candidate.read_bytes()).hexdigest() != sha256:
        raise RuntimeError(f"{label}_sha256_mismatch")
    return str(candidate)


def _pin_argv(
    pycache_prefix: str,
    interpreter_sha256: str,
    fixture_sha256: str,
    command_processor: str,
    command_processor_sha256: str,
) -> list[str]:
    return [
        "--pycache-prefix",
        pycache_prefix,
        "--interpreter-sha256",
        interpreter_sha256,
        "--fixture-sha256",
        fixture_sha256,
        "--command-processor",
        command_processor,
        "--command-processor-sha256",
        command_processor_sha256,
    ]


def _python_prefix_argv(pycache_prefix: str) -> list[str]:
    return ["-I", "-B", "-S", "-X", f"pycache_prefix={pycache_prefix}"]


def _verify_pycache_prefix(expected: str) -> str:
    candidate = Path(expected)
    if (
        not candidate.is_absolute()
        or not sys.pycache_prefix
        or os.path.normcase(str(candidate)) != os.path.normcase(str(Path(sys.pycache_prefix)))
        or candidate.exists()
    ):
        raise RuntimeError("pycache_prefix_contract_mismatch")
    return str(candidate)


def _spawn_grandchild(
    run_uuid: str,
    pycache_prefix: str,
    interpreter_sha256: str,
    fixture_sha256: str,
    command_processor: str,
    command_processor_sha256: str,
) -> int:
    interpreter = _verify_pin(sys.executable, interpreter_sha256, "interpreter")
    fixture = _verify_pin(__file__, fixture_sha256, "fixture")
    grandchild = subprocess.Popen(
        [
            interpreter,
            *_python_prefix_argv(pycache_prefix),
            fixture,
            "--mode",
            "sleep",
            "--run-uuid",
            run_uuid,
            "--seconds",
            str(DESCENDANT_HOLD_SECONDS),
            *_pin_argv(
                pycache_prefix,
                interpreter_sha256,
                fixture_sha256,
                command_processor,
                command_processor_sha256,
            ),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    _emit({"grandchild_pid": grandchild.pid})
    time.sleep(CHILD_HANDOFF_SECONDS)
    return 0


def _root(
    run_uuid: str,
    pycache_prefix: str,
    interpreter_sha256: str,
    fixture_sha256: str,
    command_processor: str,
    command_processor_sha256: str,
) -> int:
    capability = consume_inherited_job_capability(environment=os.environ)
    interpreter = _verify_pin(sys.executable, interpreter_sha256, "interpreter")
    fixture = _verify_pin(__file__, fixture_sha256, "fixture")
    child = subprocess.Popen(
        [
            interpreter,
            *_python_prefix_argv(pycache_prefix),
            fixture,
            "--mode",
            "spawn-grandchild",
            "--run-uuid",
            run_uuid,
            *_pin_argv(
                pycache_prefix,
                interpreter_sha256,
                fixture_sha256,
                command_processor,
                command_processor_sha256,
            ),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        close_fds=True,
    )
    child_stdout, child_stderr = child.communicate(timeout=3)
    if child.returncode != 0 or child_stderr or len(child_stdout.splitlines()) != 1:
        raise RuntimeError("child_grandchild_handshake_failed")
    grandchild_pid = int(json.loads(child_stdout)["grandchild_pid"])

    interpreter = _verify_pin(sys.executable, interpreter_sha256, "interpreter")
    fixture = _verify_pin(__file__, fixture_sha256, "fixture")
    closed_stdio = subprocess.Popen(
        [
            interpreter,
            *_python_prefix_argv(pycache_prefix),
            fixture,
            "--mode",
            "sleep",
            "--run-uuid",
            run_uuid,
            "--seconds",
            str(DESCENDANT_HOLD_SECONDS),
            *_pin_argv(
                pycache_prefix,
                interpreter_sha256,
                fixture_sha256,
                command_processor,
                command_processor_sha256,
            ),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    command_processor = _verify_pin(
        command_processor, command_processor_sha256, "command_processor"
    )
    console_child = subprocess.Popen(
        [
            command_processor,
            "/d",
            "/c",
            "ping -n 2 127.0.0.1 >NUL",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        close_fds=True,
    )
    breakaway: dict[str, object]
    try:
        command_processor = _verify_pin(
            command_processor, command_processor_sha256, "command_processor"
        )
        escaped = subprocess.Popen(
            [command_processor, "/d", "/c", "exit 0"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB,
            close_fds=True,
        )
    except OSError as exc:
        if exc.winerror != 5:
            raise
        breakaway = {
            "attempted": True,
            "denied": True,
            "error_code": exc.winerror,
            "spawned_pid": None,
        }
    else:
        escaped.wait(timeout=3)
        breakaway = {
            "attempted": True,
            "denied": False,
            "error_code": None,
            "spawned_pid": escaped.pid,
        }
    _emit(
        {
            "schema": FIXTURE_SCHEMA,
            "run_uuid": run_uuid,
            "pycache": {
                "prefix": pycache_prefix,
                "initially_absent": True,
                "absent_before_root_exit": not Path(pycache_prefix).exists(),
                "dont_write_bytecode": sys.dont_write_bytecode,
            },
            "capability": capability,
            "pids": {
                "root": os.getpid(),
                "child": child.pid,
                "grandchild": grandchild_pid,
                "closed_stdio": closed_stdio.pid,
                "console_child": console_child.pid,
            },
            "stdio": {"closed_stdio_child": True, "full_drain_required": True},
            "timing_contract": {
                "descendant_hold_seconds": DESCENDANT_HOLD_SECONDS,
                "child_handoff_seconds": CHILD_HANDOFF_SECONDS,
                "minimum_descendant_margin_seconds": (
                    DESCENDANT_HOLD_SECONDS - CHILD_HANDOFF_SECONDS
                ),
            },
            "breakaway": breakaway,
            "tool_pins": {
                "interpreter": {"path": interpreter, "sha256": interpreter_sha256},
                "fixture": {"path": fixture, "sha256": fixture_sha256},
                "command_processor": {
                    "path": command_processor,
                    "sha256": command_processor_sha256,
                },
            },
        }
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("root", "spawn-grandchild", "sleep"), required=True)
    parser.add_argument("--run-uuid", required=True)
    parser.add_argument("--pycache-prefix", required=True)
    parser.add_argument("--seconds", type=float, default=0.0)
    parser.add_argument("--interpreter-sha256", required=True)
    parser.add_argument("--fixture-sha256", required=True)
    parser.add_argument("--command-processor", required=True)
    parser.add_argument("--command-processor-sha256", required=True)
    args = parser.parse_args(argv)
    parsed = uuid.UUID(args.run_uuid)
    if parsed.version != 4 or str(parsed) != args.run_uuid:
        raise ValueError("canonical UUID4 required")
    pycache_prefix = _verify_pycache_prefix(args.pycache_prefix)
    _verify_pin(sys.executable, args.interpreter_sha256, "interpreter")
    _verify_pin(__file__, args.fixture_sha256, "fixture")
    _verify_pin(
        args.command_processor,
        args.command_processor_sha256,
        "command_processor",
    )
    if args.mode == "root":
        return _root(
            args.run_uuid,
            pycache_prefix,
            args.interpreter_sha256,
            args.fixture_sha256,
            args.command_processor,
            args.command_processor_sha256,
        )
    if args.mode == "spawn-grandchild":
        return _spawn_grandchild(
            args.run_uuid,
            pycache_prefix,
            args.interpreter_sha256,
            args.fixture_sha256,
            args.command_processor,
            args.command_processor_sha256,
        )
    return _sleep_mode(args.seconds)


if __name__ == "__main__":
    raise SystemExit(main())
