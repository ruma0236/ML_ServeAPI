from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.s3_runtime import (  # noqa: E402
    S3RuntimeConfig,
    S3RuntimeError,
    run_capacity_suite,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the S3 HIGGS CPU/API capacity matrix through the existing "
            "external FastAPI runtime."
        )
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops"),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs" / "s3_capacity_runtime.toml",
    )
    parser.add_argument(
        "--private-root",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/"
            "artifacts/scale_validation/private/s3"
        ),
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
        default=(
            ROOT
            / "docs"
            / "status"
            / "evidence"
            / "s3-capacity-experiment.json"
        ),
    )
    parser.add_argument(
        "--point-id",
        action="append",
        default=[],
        help="Run only an exact frozen point ID; repeat for multiple pilot points.",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=None,
        help="Pilot-only repetition override; full closure remains fixed at three.",
    )
    parser.add_argument("--list-points", action="store_true")
    parser.add_argument(
        "--allow-dirty-pilot",
        action="store_true",
        help="Allow a selected non-closure pilot on a dirty implementation tree.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = S3RuntimeConfig.from_path(args.config, data_root=args.data_root)
    if args.list_points:
        for point in config.points():
            print(point.point_id)
        return 0
    if args.repetitions is not None and not args.point_id:
        raise S3RuntimeError("s3_full_matrix_repetition_override_forbidden")
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=args.root,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    ).stdout.strip()
    if dirty and not (args.point_id and args.allow_dirty_pilot):
        raise S3RuntimeError("s3_experiment_requires_clean_revision")
    _runtime_preflight(args.trace_path)
    result = run_capacity_suite(
        root=args.root,
        data_root=args.data_root,
        config_path=args.config,
        private_parent=args.private_root,
        trace_path=args.trace_path,
        output_path=args.output,
        selected_point_ids=args.point_id,
        repetitions_override=args.repetitions,
    )
    print(
        json.dumps(
            {
                "runtime_verdict": result["runtime_verdict"],
                "scenario_status": result["scenario_status"],
                "acceptance": result["acceptance"],
                "point_result_count": len(result["point_results"]),
                "private_evidence": result["private_evidence"],
            },
            sort_keys=True,
        )
    )
    if args.point_id:
        return (
            0
            if result["point_results"]
            and all(
                bool(item.get("evidence_valid"))
                for item in result["point_results"]
            )
            else 2
        )
    return 0 if all(result["acceptance"].values()) else 2


def _runtime_preflight(trace_path: Path) -> None:
    docker = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if docker.returncode != 0 or not docker.stdout.strip():
        raise S3RuntimeError("s3_docker_daemon_unavailable")
    if not trace_path.is_file():
        raise S3RuntimeError("s3_otel_trace_sink_missing")
    try:
        response = requests.get("http://127.0.0.1:13133/", timeout=3)
    except requests.RequestException as exc:
        raise S3RuntimeError("s3_otel_collector_unhealthy") from exc
    if response.status_code != 200:
        raise S3RuntimeError(
            f"s3_otel_collector_unhealthy:{response.status_code}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
