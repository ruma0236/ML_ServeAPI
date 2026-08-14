from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path


PIPELINES = {
    "scenario-intake",
    "dataset-intake-audit",
    "domain-pack-check",
    "object-store-bootstrap",
    "data-validate",
    "lakehouse-probe",
    "spark-runtime-probe",
    "image-quality",
    "curation-workflow",
    "dataset-shards",
    "vlm-contract",
    "vlm-batch-eval",
    "vlm-reliability",
    "vlm-rca",
    "vlm-observability",
    "train",
    "register-model",
    "model-lifecycle",
    "deploy-check",
    "monitor-check",
}
MODEL_PIPELINES = {
    "train",
    "register-model",
    "model-lifecycle",
    "deploy-check",
    "monitor-check",
}
LIFECYCLE_DATA_PIPELINES = {
    "dataset-intake-audit",
    "domain-pack-check",
    "object-store-bootstrap",
    "data-validate",
    "lakehouse-probe",
    "image-quality",
    "curation-workflow",
    "dataset-shards",
    "spark-runtime-probe",
}
SKIP_EXIT_CODE = 99


def project_root() -> Path:
    configured = os.getenv("EVM_PROJECT_ROOT", "").strip()
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[1]


def allowed_roots() -> list[Path]:
    configured = os.getenv("EVM_ALLOWED_PIPELINE_CONFIG_ROOTS", "").strip()
    values = [item for item in configured.split(os.pathsep) if item] if configured else []
    values.extend(
        [
            str(project_root() / "configs"),
            "/mnt/evm-data/artifacts/w7/pipeline_profiles",
            "/mnt/evm-data/artifacts/w7/lifecycle_runs",
        ]
    )
    return [Path(item).resolve() for item in values]


def resolve_config() -> Path:
    raw = os.getenv("EVM_RUN_CONFIG", "").strip()
    if not raw:
        raise RuntimeError("pipeline_config_uri_missing")
    path = Path(raw).resolve()
    if path.suffix.lower() not in {".toml", ".json"}:
        raise RuntimeError("pipeline_config_format_not_allowed")
    if not any(path.is_relative_to(root) for root in allowed_roots()):
        raise RuntimeError("pipeline_config_path_not_allowed")
    if not path.is_file():
        raise RuntimeError("pipeline_config_not_found")
    return path


def execution_scope(path: Path) -> str:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError("pipeline_config_root_invalid")
    control_plane = payload.get("control_plane")
    if not isinstance(control_plane, dict):
        return "full_lifecycle"
    return str(control_plane.get("execution_scope") or "full_lifecycle")


def pipeline_stage_scope(path: Path) -> str:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError("pipeline_config_root_invalid")
    control_plane = payload.get("control_plane")
    if not isinstance(control_plane, dict):
        return "all"
    return str(control_plane.get("pipeline_stage_scope") or "all")


def should_skip_pipeline(path: Path, pipeline: str) -> bool:
    stage_scope = pipeline_stage_scope(path)
    if stage_scope == "data":
        return pipeline not in LIFECYCLE_DATA_PIPELINES
    return pipeline in MODEL_PIPELINES and execution_scope(path) == "data_cycle"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an allow-listed pipeline with a validated profile config.")
    parser.add_argument("pipeline")
    args = parser.parse_args()
    if args.pipeline not in PIPELINES:
        raise RuntimeError("pipeline_name_not_allowed")
    config = resolve_config()
    if should_skip_pipeline(config, args.pipeline):
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "reason": "pipeline_profile_data_stage_scope",
                    "pipeline": args.pipeline,
                    "config": str(config),
                },
                indent=2,
            )
        )
        return SKIP_EXIT_CODE
    command = [
        sys.executable,
        str(project_root() / "scripts" / "run_pipeline.py"),
        args.pipeline,
        "--config",
        str(config),
    ]
    return subprocess.run(command, cwd=project_root(), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
