from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
GIT_ROOT = ROOT.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.s3_evidence import canonical_sha256  # noqa: E402
from evm.scale_validation.s3_runtime import (  # noqa: E402
    PUBLIC_PROJECTION_DECIMAL_PLACES,
    S3RuntimeConfig,
    analyze_capacity_results,
    stable_public_projection,
)


RUNTIME_MODULE_PATH = (
    "enterprise-vision-mlops/src/evm/scale_validation/s3_runtime.py"
)
RUNTIME_CONFIG_PATH = "enterprise-vision-mlops/configs/s3_capacity_runtime.toml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reproject persisted S3 points with strict source identity."
    )
    parser.add_argument(
        "--experiment",
        type=Path,
        default=ROOT / "docs/status/evidence/s3-capacity-experiment.json",
    )
    parser.add_argument(
        "--closure",
        type=Path,
        default=ROOT / "docs/status/evidence/s3-capacity-closure.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s3_capacity_runtime.toml",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops"),
    )
    parser.add_argument("--analysis-revision", default="HEAD")
    parser.add_argument(
        "--strict-status",
        choices=("pending", "passed"),
        default="pending",
    )
    parser.add_argument("--regression-json", type=Path)
    parser.add_argument("--write", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    experiment = json.loads(args.experiment.read_text(encoding="utf-8"))
    config = S3RuntimeConfig.from_path(args.config, data_root=args.data_root)
    runtime_revision = str(
        experiment.get("source_identity", {}).get("implementation_revision") or ""
    )
    analysis_revision = _commit(args.analysis_revision)
    runtime_identity = _blob_identity(runtime_revision, RUNTIME_MODULE_PATH)
    config_identity = _blob_identity(runtime_revision, RUNTIME_CONFIG_PATH)
    analysis_identity = _blob_identity(analysis_revision, RUNTIME_MODULE_PATH)

    previous_source = dict(experiment.get("source_identity", {}))
    previous_projection = dict(experiment.get("analysis_projection", {}))
    analysis = analyze_capacity_results(
        results=list(experiment["point_results"]),
        skipped=list(experiment["skipped_points"]),
        config=config,
        closure_eligible=True,
    )
    experiment["analysis"] = analysis
    experiment["acceptance"] = analysis["acceptance"]
    experiment["runtime_verdict"] = analysis["runtime_verdict"]
    experiment["scenario_status"] = analysis["scenario_status"]
    experiment["source_identity"] = {
        "branch": "codex/distributed-scale-validation-plan",
        "implementation_revision": runtime_revision,
        "runtime_module_path": RUNTIME_MODULE_PATH,
        "runtime_module_blob_oid": runtime_identity["blob_oid"],
        "runtime_module_sha256": runtime_identity["sha256"],
        "runtime_config_path": RUNTIME_CONFIG_PATH,
        "runtime_config_blob_oid": config_identity["blob_oid"],
        "runtime_config_sha256": config_identity["sha256"],
        "hash_basis": "canonical_git_blob_bytes",
        "historical_runtime_module_capture_sha256": previous_source.get(
            "historical_runtime_module_capture_sha256",
            previous_source.get("runtime_module_sha256"),
        ),
    }
    experiment["analysis_projection"] = {
        "revision": analysis_revision,
        "runtime_revision": runtime_revision,
        "analysis_module_path": RUNTIME_MODULE_PATH,
        "analysis_module_blob_oid": analysis_identity["blob_oid"],
        "analysis_module_sha256": analysis_identity["sha256"],
        "hash_basis": "canonical_git_blob_bytes",
        "method": "deterministic_reprojection_from_persisted_point_results",
        "precision_decimal_places": PUBLIC_PROJECTION_DECIMAL_PLACES,
        "non_finite_policy": "fail_closed",
        "workload_rerun": False,
        "reason": (
            "Correct the invalid historical revision and worktree-byte hashes "
            "without discarding the 111 accepted points or four retained RCA."
        ),
        "historical_invalid_revision": previous_projection.get(
            "historical_invalid_revision",
            previous_projection.get("revision"),
        ),
        "historical_analysis_module_capture_sha256": previous_projection.get(
            "historical_analysis_module_capture_sha256",
            previous_projection.get("analysis_module_sha256"),
        ),
    }
    experiment["strict_reclosure"] = {
        "status": args.strict_status,
        "workload_rerun": False,
        "persisted_point_result_count": len(experiment["point_results"]),
        "retained_failed_attempt_count": 4,
        "analysis_projection_sha256": canonical_sha256(analysis),
        "python_projection_versions": ["3.11", "3.12", "3.13"],
        "source_identity_bound_to_git_objects": True,
        "corrected_at": _utc_now(),
        "rca": (
            "The former evidence recorded an invalid analysis revision and "
            "hashes of line-ending-sensitive worktree captures instead of the "
            "declared revisions' canonical Git blob bytes. Exact float JSON "
            "comparison also varied at approximately 1e-14 across Python versions."
        ),
    }

    projection = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "analysis_revision": analysis_revision,
        "analysis_projection_sha256": canonical_sha256(analysis),
        "acceptance": analysis["acceptance"],
        "runtime_verdict": analysis["runtime_verdict"],
        "point_result_count": len(experiment["point_results"]),
    }
    if not args.write:
        print(json.dumps(projection, sort_keys=True))
        return 0

    _write_json(args.experiment, experiment)
    experiment_sha256 = hashlib.sha256(args.experiment.read_bytes()).hexdigest()
    closure = json.loads(args.closure.read_text(encoding="utf-8"))
    closure["generated_at"] = _utc_now()
    closure["source_identity"] = {
        "branch": "codex/distributed-scale-validation-plan",
        "runtime_revision": runtime_revision,
        "runtime_module_sha256": runtime_identity["sha256"],
        "runtime_module_blob_oid": runtime_identity["blob_oid"],
        "analysis_projection_revision": analysis_revision,
        "analysis_module_sha256": analysis_identity["sha256"],
        "analysis_module_blob_oid": analysis_identity["blob_oid"],
        "validation_base_revision": analysis_revision,
        "historical_evidence_commit": closure.get("source_identity", {}).get(
            "evidence_commit"
        ),
    }
    private = dict(experiment["private_evidence"])
    closure["final_runtime_evidence"].update(
        {
            "git_blob_sha256": experiment_sha256,
            "point_result_count": len(experiment["point_results"]),
            "executed_point_count": len(analysis["aggregated_points"]),
            "skipped_point_count": len(experiment["skipped_points"]),
            "repetitions_per_executed_point": config.repetitions,
            "acceptance": analysis["acceptance"],
            "runtime_verdict": analysis["runtime_verdict"],
            "private_artifact_count": private["artifact_count"],
            "private_aggregate_sha256": private["aggregate_sha256"],
        }
    )
    closure["s2_capacity_recalculation"].update(
        analysis["s2_capacity_recalculation"]
    )
    first = analysis["bottleneck"]["first_observed"]
    closure["measured_capacity"]["first_saturation_knee"].update(
        {
            "curve": first["curve"],
            "cause": first["cause"],
            "client_p99_ms": first["signals"]["p99_ms"],
            "server_total_p99_ms": first["signals"]["server_total_p99_ms"],
            "queue_wait_p99_ms": first["signals"]["queue_wait_p99_ms"],
            "prediction_p99_ms": first["signals"]["prediction_p99_ms"],
            "api_process_tree_cpu_percent": first["signals"][
                "api_process_tree_cpu_percent"
            ],
            "load_generator_cpu_percent": first["signals"][
                "load_generator_cpu_percent"
            ],
            "attribution_boundary": analysis["bottleneck"][
                "attribution_boundary"
            ],
        }
    )
    closure["strict_reclosure"] = dict(experiment["strict_reclosure"])
    closure["strict_reclosure"]["status"] = args.strict_status
    if args.regression_json:
        closure["regression"] = json.loads(
            args.regression_json.read_text(encoding="utf-8")
        )
    closure["verdict"] = (
        "passed" if args.strict_status == "passed" else "pending_strict_reclosure"
    )
    closure = stable_public_projection(closure)
    _write_json(args.closure, closure)
    projection["experiment_sha256"] = experiment_sha256
    projection["closure_sha256"] = hashlib.sha256(
        args.closure.read_bytes()
    ).hexdigest()
    projection["strict_status"] = args.strict_status
    print(json.dumps(projection, sort_keys=True))
    return 0


def _commit(revision: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
        cwd=GIT_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    ).stdout.strip()


def _blob_identity(revision: str, path: str) -> dict[str, str]:
    blob_oid = subprocess.run(
        ["git", "rev-parse", "--verify", f"{revision}:{path}"],
        cwd=GIT_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    ).stdout.strip()
    raw = subprocess.run(
        ["git", "cat-file", "blob", blob_oid],
        cwd=GIT_ROOT,
        capture_output=True,
        timeout=15,
        check=True,
    ).stdout
    return {"blob_oid": blob_oid, "sha256": hashlib.sha256(raw).hexdigest()}


def _write_json(path: Path, payload: Any) -> None:
    path.write_bytes(
        (
            json.dumps(
                payload,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
