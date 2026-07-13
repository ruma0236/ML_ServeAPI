from __future__ import annotations

import hashlib
import json
import os
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from evm.core.config import get_nested, map_runtime_data_path, project_root_from, resolve_path
from evm.core.pipeline import utc_now, write_json
from evm.core.readiness_snapshot import capture_readiness_snapshot
from evm.core.torch_efficientnet import (
    EfficientNetCandidateConfig,
    TorchRuntimeConfig,
    load_shard_records,
    split_manifest_snapshot,
    train_candidate,
    validate_acceptance_split,
)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while chunk := fp.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_config_path(config: dict[str, Any], value: str | Path) -> Path:
    mapped = map_runtime_data_path(value)
    if mapped.is_absolute():
        return mapped
    return resolve_path(config, mapped)


def source_digest_blockers(actual: str, expected: str) -> list[str]:
    if not expected:
        return []
    if actual.lower() != expected.lower():
        return [
            "shard_index_sha256_mismatch:"
            f"expected={expected.lower()},actual={actual.lower()}"
        ]
    return []


def load_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.suffix.lower() == ".json":
        config = json.loads(path.read_text(encoding="utf-8"))
    else:
        with path.open("rb") as fp:
            config = tomllib.load(fp)
    if not isinstance(config, dict):
        raise ValueError(f"Config root must be an object: {path}")
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
    early_stop_value = candidate.get(
        "early_stop_accuracy",
        acceptance.get("early_stop_accuracy"),
    )
    return EfficientNetCandidateConfig(
        candidate_id=str(candidate["candidate_id"]),
        architecture=str(candidate.get("architecture", "efficientnet-b0")),
        backbone=str(candidate.get("backbone", "")),
        input_size=int(candidate.get("input_size", 224)),
        pretrained=bool(candidate.get("pretrained", True)),
        freeze_backbone=bool(candidate.get("freeze_backbone", False)),
        optimizer=str(candidate.get("optimizer", "adamw")),
        learning_rate=float(candidate.get("learning_rate", 0.0003)),
        weight_decay=float(candidate.get("weight_decay", 0.0)),
        batch_size=int(candidate.get("batch_size", 32)),
        mixed_precision=bool(candidate.get("mixed_precision", True)),
        resource_profile=str(candidate.get("resource_profile", "gpu-unknown")),
        epochs=candidate_epochs(candidate, acceptance),
        early_stop_accuracy=(
            float(early_stop_value)
            if isinstance(early_stop_value, int | float)
            else None
        ),
        early_stop_min_epochs=int(
            candidate.get(
                "early_stop_min_epochs",
                acceptance.get("early_stop_min_epochs", 1),
            )
        ),
        class_weighted_loss=bool(candidate.get("class_weighted_loss", True)),
    )


def selected_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requested = os.getenv("EVM_EFFICIENTNET_CANDIDATES", "").strip()
    if not requested:
        return candidates
    allowed = {item.strip() for item in requested.split(",") if item.strip()}
    return [candidate for candidate in candidates if str(candidate.get("candidate_id")) in allowed]


def candidate_artifact_root(matrix_dir: Path, execution_run_id: str) -> Path:
    return matrix_dir / "runs" / execution_run_id if execution_run_id else matrix_dir


def matrix_status(candidate_results: list[dict[str, Any]], configured_candidate_count: int) -> str:
    if not candidate_results:
        return "blocked"
    pass_count = sum(1 for item in candidate_results if item.get("status") == "pass")
    if pass_count == len(candidate_results) == configured_candidate_count:
        return "pass"
    return "warn" if pass_count else "blocked"


def merge_candidate_results(
    existing_matrix: dict[str, Any],
    new_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in existing_matrix.get("candidates", []):
        if isinstance(item, dict) and item.get("candidate_id"):
            merged[str(item["candidate_id"])] = item
    for item in new_results:
        if item.get("candidate_id"):
            merged[str(item["candidate_id"])] = item
    return list(merged.values())


def required_candidate_blockers(summary: dict[str, Any]) -> list[str]:
    requested = os.getenv("EVM_EFFICIENTNET_CANDIDATES", "").strip()
    required_ids = {item.strip() for item in requested.split(",") if item.strip()}
    candidates = {
        str(item.get("candidate_id")): item
        for item in summary.get("candidates", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }
    if not required_ids:
        required_ids = set(candidates)

    blockers: list[str] = []
    for candidate_id in sorted(required_ids):
        candidate = candidates.get(candidate_id)
        if candidate is None:
            blockers.append(f"required_candidate_missing:{candidate_id}")
            continue
        if candidate.get("status") != "pass":
            blockers.append(f"required_candidate_not_pass:{candidate_id}")
        if candidate.get("execution_blockers"):
            blockers.append(f"required_candidate_execution_blocked:{candidate_id}")
        if candidate.get("promotion_blockers"):
            blockers.append(f"required_candidate_promotion_blocked:{candidate_id}")
        if not candidate.get("mlflow_run_id"):
            blockers.append(f"required_candidate_mlflow_run_missing:{candidate_id}")
    return blockers


def run(
    config_path: str = "configs/w7_efficientnet_real_test.toml",
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    project_root = Path(str(config["_project_root"]))
    matrix_cfg = config.get("model_matrix", {})
    resources = config.get("resources", {})
    acceptance = config.get("acceptance", {})
    inputs = config.get("inputs", {})
    execution = config.get("execution", {})
    execution_seed = int(
        os.getenv("EVM_EFFICIENTNET_SEED", str(acceptance.get("seed", 20260709)))
    )

    matrix_id = str(matrix_cfg.get("matrix_id", "w7-efficientnet-real-test-matrix"))
    selected_candidate_id = str(
        matrix_cfg.get(
            "selected_candidate_id",
            "effnet-b7-img600-finetune-adamw",
        )
    )
    execution_run_id = os.getenv("EVM_EFFICIENTNET_RUN_ID", "").strip()
    dataset_version = str(matrix_cfg.get("dataset_version", "unknown"))
    artifact_root = runtime_config_path(
        config,
        str(resources.get("artifact_root", "artifacts/w7/efficientnet")),
    )
    matrix_dir = artifact_root / matrix_id
    matrix_dir.mkdir(parents=True, exist_ok=True)
    candidate_root = candidate_artifact_root(matrix_dir, execution_run_id)

    shard_index_path = runtime_config_path(config, str(inputs.get("shard_index", "")))
    shard_index, splits = load_shard_records(shard_index_path)
    shard_index_file_sha256 = file_sha256(shard_index_path)
    shard_index_identity_sha256 = str(shard_index.get("identity_sha256") or "")
    expected_identity_sha256 = str(
        os.getenv("EVM_TRAINING_VIEW_IDENTITY")
        or inputs.get("shard_identity_sha256", "")
    ).strip()
    expected_shard_index_sha256 = (
        expected_identity_sha256
        or str(inputs.get("shard_index_sha256", "")).strip()
    )
    shard_index_sha256 = (
        shard_index_identity_sha256
        if expected_identity_sha256
        else shard_index_file_sha256
    )
    split_manifest = split_manifest_snapshot(
        shard_index,
        splits,
        dataset_version=dataset_version,
        seed=execution_seed,
    )
    split_manifest["source_shard_index_sha256"] = shard_index_sha256
    split_manifest["source_shard_file_sha256"] = shard_index_file_sha256
    split_manifest["source_shard_identity_sha256"] = shard_index_identity_sha256
    split_manifest["expected_shard_index_sha256"] = expected_shard_index_sha256
    split_acceptance = dict(acceptance)
    evaluation_split = str(execution.get("evaluation_split") or "test")
    if str(shard_index.get("training_data_scope") or "") == "development-only":
        split_acceptance["min_total_records"] = len(splits["train"]) + len(
            splits["validation"]
        )
        split_acceptance["min_test_images"] = 0
        if evaluation_split != "validation":
            evaluation_split = "validation"
    split_blockers = source_digest_blockers(
        shard_index_sha256,
        expected_shard_index_sha256,
    ) + validate_acceptance_split(split_manifest, split_acceptance)
    write_json(matrix_dir / "split_manifest.json", split_manifest)

    readiness_snapshot_path: Path | None = None
    readiness_snapshot_digest = ""
    readiness_snapshot_blockers: list[str] = []
    base_config_value = str(inputs.get("base_config") or "").strip()
    if base_config_value:
        try:
            base_config_path = Path(base_config_value)
            if not base_config_path.is_absolute():
                base_config_path = project_root / base_config_path
            base_config = load_config(base_config_path)
            dataset_metadata_path = runtime_config_path(
                base_config,
                str(
                    get_nested(
                        base_config,
                        "pipelines.data_validation.dataset_metadata",
                        "data/validated/visa/dataset_version.json",
                    )
                ),
            )
            quality_report_path = runtime_config_path(
                base_config,
                str(
                    get_nested(
                        base_config,
                        "pipelines.image_quality.report_path",
                        "data/validated/visa/mvi_quality_report.json",
                    )
                ),
            )
            readiness_snapshot_path = capture_readiness_snapshot(
                output_dir=candidate_root / "_readiness_inputs",
                candidate_id=selected_candidate_id,
                dataset_version=dataset_version,
                expected_record_count=int(
                    acceptance.get("min_total_records")
                    or matrix_cfg.get("minimum_records")
                    or 0
                ),
                expected_source_record_count=int(
                    split_acceptance.get("min_total_records") or 0
                ),
                expected_source_digest=expected_shard_index_sha256,
                dataset_metadata_path=dataset_metadata_path,
                quality_report_path=quality_report_path,
                source_shard_path=shard_index_path,
            )
            readiness_snapshot_digest = file_sha256(readiness_snapshot_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            readiness_snapshot_blockers.append(f"readiness_snapshot_capture_failed:{exc}")
    elif bool(matrix_cfg.get("require_readiness_snapshot", False)):
        readiness_snapshot_blockers.append("readiness_snapshot_base_config_missing")
    if bool(matrix_cfg.get("require_readiness_snapshot", False)):
        split_blockers += readiness_snapshot_blockers

    runtime = TorchRuntimeConfig(
        seed=execution_seed,
        require_cuda=bool(acceptance.get("require_cuda_available", True)),
        num_workers=int(execution.get("num_workers", 4)),
        pin_memory=bool(execution.get("pin_memory", True)),
        mlflow_tracking_uri=str(inputs.get("mlflow_tracking_uri", "http://localhost:5000")),
        mlflow_experiment_name=str(
            inputs.get("mlflow_experiment_name", "enterprise-vision-w7-efficientnet")
        ),
        evaluation_split=evaluation_split,
        parent_run_id=(
            os.getenv("EVM_MLFLOW_PARENT_RUN_ID")
            or str(execution.get("mlflow_parent_run_id") or "")
            or None
        ),
        run_tags={
            "evm.run_role": str(
                os.getenv("EVM_MLFLOW_RUN_ROLE")
                or execution.get("mlflow_run_role")
                or "candidate"
            ),
            "evm.lifecycle_run_id": str(
                os.getenv("EVM_LIFECYCLE_RUN_ID")
                or execution.get("lifecycle_run_id")
                or ""
            ),
        },
        progress_callback=progress_callback,
    )

    candidate_results: list[dict[str, Any]] = []
    all_candidates_cfg = config.get("candidates", [])
    candidates_cfg = selected_candidates(all_candidates_cfg)
    for candidate_payload in candidates_cfg:
        candidate = candidate_config(candidate_payload, acceptance)
        candidate_dir = candidate_root / candidate.candidate_id
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
                    "early_stop_accuracy": candidate.early_stop_accuracy,
                    "early_stop_min_epochs": candidate.early_stop_min_epochs,
                    "class_weighted_loss": candidate.class_weighted_loss,
                    "seed": runtime.seed,
                },
                "metrics": {},
                "promotion_blockers": split_blockers,
                "execution_blockers": split_blockers,
                "artifact_uri": str(candidate_dir),
                "created_at": utc_now(),
            }
            candidate_dir.mkdir(parents=True, exist_ok=True)
            if readiness_snapshot_path and candidate.candidate_id == selected_candidate_id:
                blocked["readiness_snapshot_manifest"] = str(readiness_snapshot_path)
                blocked["readiness_snapshot_manifest_sha256"] = readiness_snapshot_digest
            write_json(candidate_dir / "candidate_summary.json", blocked)
            candidate_results.append(blocked)
            continue
        try:
            result = train_candidate(
                candidate,
                splits,
                split_manifest,
                acceptance,
                runtime,
                candidate_dir,
            )
            if readiness_snapshot_path and candidate.candidate_id == selected_candidate_id:
                result["readiness_snapshot_manifest"] = str(readiness_snapshot_path)
                result["readiness_snapshot_manifest_sha256"] = readiness_snapshot_digest
                write_json(candidate_dir / "candidate_summary.json", result)
            candidate_results.append(result)
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
                    "early_stop_accuracy": candidate.early_stop_accuracy,
                    "early_stop_min_epochs": candidate.early_stop_min_epochs,
                    "class_weighted_loss": candidate.class_weighted_loss,
                    "seed": runtime.seed,
                },
                "metrics": {},
                "promotion_blockers": [str(exc)],
                "execution_blockers": [str(exc)],
                "artifact_uri": str(candidate_dir),
                "created_at": utc_now(),
            }
            candidate_dir.mkdir(parents=True, exist_ok=True)
            if readiness_snapshot_path and candidate.candidate_id == selected_candidate_id:
                blocked["readiness_snapshot_manifest"] = str(readiness_snapshot_path)
                blocked["readiness_snapshot_manifest_sha256"] = readiness_snapshot_digest
            write_json(candidate_dir / "candidate_summary.json", blocked)
            candidate_results.append(blocked)

    latest_matrix_path = artifact_root / "latest_model_matrix.json"
    merged_candidate_results = merge_candidate_results(
        read_json(latest_matrix_path),
        candidate_results,
    )

    summary = {
        "schema_version": "evm.w7.efficientnet_model_matrix.v1",
        "matrix_id": matrix_id,
        "status": matrix_status(merged_candidate_results, len(all_candidates_cfg)),
        "execution_mode": str(matrix_cfg.get("execution_mode", "parallel")),
        "framework": "torch",
        "dataset_version": dataset_version,
        "artifact_root": str(matrix_dir),
        "execution_run_id": execution_run_id or None,
        "execution_seed": execution_seed,
        "split_manifest": str(matrix_dir / "split_manifest.json"),
        "source_shard_index_sha256": shard_index_sha256,
        "source_shard_file_sha256": shard_index_file_sha256,
        "source_shard_identity_sha256": shard_index_identity_sha256,
        "split_blockers": split_blockers,
        "readiness_snapshot_manifest": (
            str(readiness_snapshot_path) if readiness_snapshot_path else None
        ),
        "readiness_snapshot_manifest_sha256": readiness_snapshot_digest or None,
        "readiness_snapshot_blockers": readiness_snapshot_blockers,
        "candidate_count": len(merged_candidate_results),
        "configured_candidate_count": len(all_candidates_cfg),
        "candidates": merged_candidate_results,
        "created_at": utc_now(),
    }
    write_json(matrix_dir / "model_matrix.json", summary)
    write_json(latest_matrix_path, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    import sys

    arguments = list(sys.argv[1:] if argv is None else argv)
    require_pass = "--require-pass" in arguments
    positional = [argument for argument in arguments if not argument.startswith("--")]
    config_path = positional[0] if positional else "configs/w7_efficientnet_real_test.toml"
    summary = run(config_path)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if require_pass and (blockers := required_candidate_blockers(summary)):
        print(json.dumps({"require_pass_blockers": blockers}), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
