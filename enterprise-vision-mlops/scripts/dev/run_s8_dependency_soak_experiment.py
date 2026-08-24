from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import requests


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.s8_runtime import (  # noqa: E402
    S8RuntimeError,
    assert_no_lingering_runtime,
    run_s8_experiment,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run S8 dependency faults and the sustained HIGGS capacity soak "
            "through the existing external API and durable queue runtimes."
        )
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops"),
    )
    parser.add_argument(
        "--private-root",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/"
            "artifacts/scale_validation/private/s8"
        ),
    )
    parser.add_argument(
        "--profile-root",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/"
            "artifacts/w7/pipeline_profiles"
        ),
    )
    parser.add_argument(
        "--scenario-config",
        type=Path,
        default=ROOT / "configs/s8_dependency_soak_v1.toml",
    )
    parser.add_argument(
        "--queue-config",
        type=Path,
        default=ROOT / "configs/s8_dependency_soak_v1.toml",
    )
    parser.add_argument(
        "--soak-config",
        type=Path,
        default=ROOT / "configs/s8_soak_capacity_runtime.toml",
    )
    parser.add_argument(
        "--progress",
        type=Path,
        default=ROOT / "docs/status/2026-08-15-distributed-scale-scenario-progress.json",
    )
    parser.add_argument(
        "--trace-path",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/"
            "artifacts/scale_validation/otel/traces.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-dependency-soak-experiment.json",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("EVM_CONTROL_PLANE_DATABASE_URL"),
    )
    return parser.parse_args()


def preflight(args: argparse.Namespace) -> None:
    if not args.database_url:
        raise S8RuntimeError("s8_database_url_environment_required")
    expected_runtime = args.root / "src/evm/scale_validation/s8_runtime.py"
    imported_runtime = Path(
        sys.modules["evm.scale_validation.s8_runtime"].__file__ or ""
    ).resolve()
    if imported_runtime != expected_runtime.resolve():
        raise S8RuntimeError("s8_runtime_source_mismatch")
    docker = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if docker.returncode != 0 or not docker.stdout.strip():
        raise S8RuntimeError("s8_docker_daemon_unavailable")
    if not args.trace_path.is_file():
        raise S8RuntimeError("s8_otel_trace_sink_missing")
    try:
        response = requests.get("http://127.0.0.1:13133/", timeout=3)
    except requests.RequestException as exc:
        raise S8RuntimeError("s8_otel_collector_unhealthy") from exc
    if response.status_code != 200:
        raise S8RuntimeError(f"s8_otel_collector_unhealthy:{response.status_code}")
    try:
        with psycopg.connect(args.database_url, connect_timeout=5) as connection:
            observed = connection.execute("SELECT current_setting('server_version_num')").fetchone()
    except psycopg.Error as exc:
        raise S8RuntimeError("s8_postgresql_unavailable") from exc
    if observed is None or int(observed[0]) < 160000:
        raise S8RuntimeError("s8_requires_postgresql_16")
    for path in (
        args.profile_root,
        args.scenario_config,
        args.queue_config,
        args.soak_config,
        args.progress,
    ):
        if not path.exists():
            raise S8RuntimeError(f"s8_required_path_missing:{path.name}")
    assert_no_lingering_runtime(args.root)


def main() -> int:
    args = parse_args()
    preflight(args)
    result = run_s8_experiment(
        root=args.root,
        data_root=args.data_root,
        private_parent=args.private_root,
        profile_root=args.profile_root,
        database_url=args.database_url,
        queue_config_path=args.queue_config,
        soak_config_path=args.soak_config,
        progress_path=args.progress,
        trace_path=args.trace_path,
        output_path=args.output,
        scenario_config_path=args.scenario_config,
    )
    print(
        json.dumps(
            {
                "runtime_verdict": result["runtime_verdict"],
                "scenario_status": result["scenario_status"],
                "fault_result_count": len(result["fault_results"]),
                "soak_result_count": len(result["soak_results"]),
                "acceptance": result["acceptance"],
                "private_evidence": result["private_evidence"],
            },
            sort_keys=True,
        )
    )
    return 0 if all(result["acceptance"][key] for key in ("S8-AC-01", "S8-AC-02", "S8-AC-03")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
