from __future__ import annotations

import os
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evm.core.config import project_root_from, resolve_path
from evm.core.pipeline import utc_now, write_json
from evm.core.torch_efficientnet import (
    EfficientNetCandidateConfig,
    TorchRuntimeConfig,
    load_shard_records,
    split_manifest_snapshot,
    train_candidate,
    validate_acceptance_split,
)


def load_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    with path.open("rb") as fp:
        config = tomllib.load(fp)
    config["_config_path"] = str(path.resolve())
    config["_project_root"] = str(project_root_from(path))
    return config


def candidate_epochs(candidate: dict[str, Any], acceptance: dict[str, Any]) -> int:
    if "epochs" in candidate:
        return int(candidate["epochs"])
    architecture = str(candidate.get("architecture", "efficientnet-b0"))
    if architecture == "efficientnet-b7":
        return int(acceptance.get("min_epochs_b7", 3))
    return int(acceptance.get("min_epochs_b0", 5))


def candidate_config(candidate: dict[str, Any], acceptance: dict[str, Any]) -> EfficientNetCandidateConfig:
    return EfficientNetCandidateConfig(
        candidate_id=str(candidate["candidate_id"]),
        architecture=str(candidate.get("architecture", "efficientnet-b0")),
        backbone=str(candidate.get("backbone", "")),
        input_size=int(candidate.get("input_size", 224)),
        pretrained=bool(candidate.get("pretrained", True)),
        freeze_backbone=bool(candidate.get("freeze_backbone", False)),
        optimizer=str(candidate.get("optimizer", "adamw")),
        learning_rate=float(candidate.get("learning_rate", 0.0003)),
        batch_size=int(candidate.get("batch_size", 32)),
        mixed_precision=bool(candidate.get("mixed_precision", True)),
        resource_profile=str(candidate.get("resource_profile", "gpu-unknown")),
        epochs=candidate_epochs(candidate, acceptance),
    )


def selected_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requested = os.getenv("EVM_EFFICIENTNET_CANDIDATES", "").strip()
    if not requested:
        return candidates
    allowed = {item.strip() for item in requested.split(",") if item.strip()}
    return [candidate for candidate in candidates if str(candidate.get("candidate_id")) in allowed]


def matrix_status(candidate_results: list[dict[str, Any]], configured_candidate_count: int) -> str:
    if not candidate_results:
        return "blocked"
    pass_count = sum(1 for item in candidate_results if item.get("status") == "pass")
    if pass_count == len(candidate_results) == configured_candidate_count:
        return "pass"
    return "warn" if pass_count else "blocked"


def run(config_path: str = "configs/w7_efficientnet_real_test.toml") -> dict[str, Any]:
    config = load_config(config_path)
    project_root = Path(str(config["_project_root"]))
    matrix_cfg = config.get("model_matrix", {})
    resources = config.get("resources", {})
    acceptance = config.get("acceptance", {})
    inputs = config.get("inputs", {})
    execution = config.get("execution", {})

    matrix_id = str(matrix_cfg.get("matrix_id", "w7-efficientnet-real-test-matrix"))
    dataset_version = str(matrix_cfg.get("dataset_version", "unknown"))
    artifact_root = Path(str(resources.get("artifact_root", "artifacts/w7/efficientnet")))
    if not artifact_root.is_absolute():
        artifact_root = project_root / artifact_root
    matrix_dir = artifact_root / matrix_id
    matrix_dir.mkdir(parents=True, exist_ok=True)

    shard_index_path = Path(str(inputs.get("shard_index", "")))
    if not shard_index_path.is_absolute():
        shard_index_path = resolve_path(config, shard_index_path)
    shard_index, splits = load_shard_records(shard_index_path)
    split_manifest = split_manifest_snapshot(
        shard_index,
        splits,
        dataset_version=dataset_version,
        seed=int(acceptance.get("seed", 20260709)),
    )
    split_blockers = validate_acceptance_split(split_manifest, acceptance)
    write_json(matrix_dir / "split_manifest.json", split_manifest)

    runtime = TorchRuntimeConfig(
        seed=int(acceptance.get("seed", 20260709)),
        require_cuda=bool(acceptance.get("require_cuda_available", True)),
        num_workers=int(execution.get("num_workers", 4)),
        pin_memory=bool(execution.get("pin_memory", True)),
        mlflow_tracking_uri=str(inputs.get("mlflow_tracking_uri", "http://localhost:5000")),
        mlflow_experiment_name=str(
            inputs.get("mlflow_experiment_name", "enterprise-vision-w7-efficientnet")
        ),
    )

    candidate_results: list[dict[str, Any]] = []
    all_candidates_cfg = config.get("candidates", [])
    candidates_cfg = selected_candidates(all_candidates_cfg)
    for candidate_payload in candidates_cfg:
        candidate = candidate_config(candidate_payload, acceptance)
        candidate_dir = matrix_dir / candidate.candidate_id
        if split_blockers:
            blocked = {
                "schema_version": "evm.w7.efficientnet_candidate.v1",
                "candidate_id": candidate.candidate_id,
                "status": "blocked",
                "architecture": candidate.architecture,
                "backbone": candidate.backbone,
                "dataset_version": dataset_version,
                "resource_profile": candidate.resource_profile,
                "conditions": {
                    "input_size": candidate.input_size,
                    "pretrained": candidate.pretrained,
                    "freeze_backbone": candidate.freeze_backbone,
                    "optimizer": candidate.optimizer,
                    "learning_rate": candidate.learning_rate,
                    "batch_size": candidate.batch_size,
                    "mixed_precision": candidate.mixed_precision,
                    "epochs": candidate.epochs,
                    "seed": runtime.seed,
                },
                "metrics": {},
                "promotion_blockers": split_blockers,
                "execution_blockers": split_blockers,
                "artifact_uri": str(candidate_dir),
                "created_at": utc_now(),
            }
            candidate_dir.mkdir(parents=True, exist_ok=True)
            write_json(candidate_dir / "candidate_summary.json", blocked)
            candidate_results.append(blocked)
            continue
        try:
            candidate_results.append(
                train_candidate(
                    candidate,
                    splits,
                    split_manifest,
                    acceptance,
                    runtime,
                    candidate_dir,
                )
            )
        except Exception as exc:
            blocked = {
                "schema_version": "evm.w7.efficientnet_candidate.v1",
                "candidate_id": candidate.candidate_id,
                "status": "blocked",
                "architecture": candidate.architecture,
                "backbone": candidate.backbone,
                "dataset_version": dataset_version,
                "resource_profile": candidate.resource_profile,
                "conditions": {
                    "input_size": candidate.input_size,
                    "pretrained": candidate.pretrained,
                    "freeze_backbone": candidate.freeze_backbone,
                    "optimizer": candidate.optimizer,
                    "learning_rate": candidate.learning_rate,
                    "batch_size": candidate.batch_size,
                    "mixed_precision": candidate.mixed_precision,
                    "epochs": candidate.epochs,
                    "seed": runtime.seed,
                },
                "metrics": {},
                "promotion_blockers": [str(exc)],
                "execution_blockers": [str(exc)],
                "artifact_uri": str(candidate_dir),
                "created_at": utc_now(),
            }
            candidate_dir.mkdir(parents=True, exist_ok=True)
            write_json(candidate_dir / "candidate_summary.json", blocked)
            candidate_results.append(blocked)

    summary = {
        "schema_version": "evm.w7.efficientnet_model_matrix.v1",
        "matrix_id": matrix_id,
        "status": matrix_status(candidate_results, len(all_candidates_cfg)),
        "execution_mode": str(matrix_cfg.get("execution_mode", "parallel")),
        "framework": "torch",
        "dataset_version": dataset_version,
        "artifact_root": str(matrix_dir),
        "split_manifest": str(matrix_dir / "split_manifest.json"),
        "split_blockers": split_blockers,
        "candidate_count": len(candidate_results),
        "configured_candidate_count": len(all_candidates_cfg),
        "candidates": candidate_results,
        "created_at": utc_now(),
    }
    write_json(matrix_dir / "model_matrix.json", summary)
    write_json(artifact_root / "latest_model_matrix.json", summary)
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    import json

    config_path = argv[0] if argv else "configs/w7_efficientnet_real_test.toml"
    print(json.dumps(run(config_path), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
