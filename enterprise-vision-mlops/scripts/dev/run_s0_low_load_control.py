from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from evm.observability.otel import shutdown_tracing  # noqa: E402
from evm.scale_validation.s0_runtime import S0RuntimeConfig, execute_suite  # noqa: E402


def git_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run three real low-load controls through the existing ML Serve lifecycle."
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--serving-requests", type=int, default=3)
    parser.add_argument("--lifecycle-timeout", type=float, default=900)
    parser.add_argument("--trace-timeout", type=float, default=45)
    parser.add_argument("--poll-interval", type=float, default=2)
    args = parser.parse_args()

    revision = git_revision()
    os.environ.setdefault("EVM_GIT_COMMIT", revision)
    os.environ.setdefault("EVM_OTEL_ENABLED", "true")
    os.environ.setdefault(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "http://127.0.0.1:4318/v1/traces",
    )
    os.environ.setdefault("OTEL_SERVICE_INSTANCE_ID", "s0-local-control-runner")
    config = S0RuntimeConfig(
        repetitions=args.repetitions,
        serving_requests_per_run=args.serving_requests,
        lifecycle_timeout_seconds=args.lifecycle_timeout,
        trace_timeout_seconds=args.trace_timeout,
        poll_interval_seconds=args.poll_interval,
    )
    try:
        evidence = execute_suite(config, source_revision=revision)
    finally:
        shutdown_tracing()
    print(json.dumps(evidence.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
