from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import shutil
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score
from sklearn.naive_bayes import GaussianNB
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier


HIGGS_SOURCE_URI = (
    "https://archive.ics.uci.edu/ml/machine-learning-databases/00280/HIGGS.csv.gz"
)
HIGGS_SOURCE_DOI = "10.24432/C5V312"
HIGGS_LICENSE = "CC BY 4.0"
HIGGS_OBSERVED_SOURCE_SHA256 = (
    "ea302c18164d4e3d916a1e2e83a9a8d07069fa6ebc7771e4c0540d54e593b698"
)
PROBE_FAMILIES = (
    "logistic",
    "probabilistic",
    "online-linear",
    "branch-heavy",
    "incremental",
)


class HiggsPreparationError(RuntimeError):
    pass


@dataclass(frozen=True)
class HiggsPreparationConfig:
    source_path: Path
    output_root: Path
    registry_path: Path
    source_revision: str
    source_branch: str
    experiment_config_sha256: str
    source_sha256: str = HIGGS_OBSERVED_SOURCE_SHA256
    dataset_version: str = "uci-higgs-2014-s3-v1"
    total_rows: int = 11_000_000
    official_test_rows: int = 500_000
    train_sample_rows: int = 250_000
    validation_sample_rows: int = 50_000
    test_sample_rows: int = 50_000
    replay_sample_rows: int = 200_000
    seed: int = 20260817
    incremental_epochs: int = 3
    incremental_batch_rows: int = 8192
    logistic_max_iter: int = 100
    online_linear_max_iter: int = 20
    tree_max_depth: int = 12
    tree_min_samples_leaf: int = 64


def load_higgs_preparation_config(
    path: Path,
    *,
    data_root: Path,
    source_revision: str,
    source_branch: str,
) -> HiggsPreparationConfig:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    if payload.get("schema_version") != "evm.s3_capacity_experiment.v1":
        raise HiggsPreparationError("experiment_config_schema_invalid")
    dataset = payload.get("dataset")
    preparation = payload.get("preparation")
    models = payload.get("models")
    if not all(isinstance(value, dict) for value in (dataset, preparation, models)):
        raise HiggsPreparationError("experiment_config_section_missing")
    assert isinstance(dataset, dict)
    assert isinstance(preparation, dict)
    assert isinstance(models, dict)
    return HiggsPreparationConfig(
        source_path=data_root / str(dataset["source_relative_path"]),
        output_root=data_root / str(preparation["output_relative_root"]),
        registry_path=data_root / str(preparation["registry_relative_path"]),
        source_revision=source_revision,
        source_branch=source_branch,
        experiment_config_sha256=file_sha256(path),
        source_sha256=str(dataset["source_sha256"]),
        dataset_version=str(dataset["dataset_version"]),
        total_rows=int(dataset["total_rows"]),
        official_test_rows=int(dataset["official_test_rows"]),
        train_sample_rows=int(preparation["train_sample_rows"]),
        validation_sample_rows=int(preparation["validation_sample_rows"]),
        test_sample_rows=int(preparation["test_sample_rows"]),
        replay_sample_rows=int(preparation["replay_sample_rows"]),
        seed=int(preparation["seed"]),
        incremental_epochs=int(models["incremental_epochs"]),
        incremental_batch_rows=int(models["incremental_batch_rows"]),
        logistic_max_iter=int(models["logistic_max_iter"]),
        online_linear_max_iter=int(models["online_linear_max_iter"]),
        tree_max_depth=int(models["tree_max_depth"]),
        tree_min_samples_leaf=int(models["tree_min_samples_leaf"]),
    )


def prepare_higgs_capacity(config: HiggsPreparationConfig) -> dict[str, Any]:
    _validate_config(config)
    source_sha256 = file_sha256(config.source_path)
    if source_sha256 != config.source_sha256:
        raise HiggsPreparationError(
            f"source_digest_mismatch:{source_sha256}:{config.source_sha256}"
        )
    if config.output_root.exists():
        raise HiggsPreparationError(f"immutable_output_exists:{config.output_root}")

    temporary_root = config.output_root.with_name(
        f"{config.output_root.name}.building-{uuid4().hex[:8]}"
    )
    temporary_root.mkdir(parents=True)
    try:
        selection = _select_rows(config)
        arrays = _extract_selected_rows(config, selection)
        split_files = _write_split_arrays(temporary_root, selection, arrays)
        data_identity = payload_sha256(
            {
                "schema_version": "evm.s3_higgs_data_identity.v1",
                "source_sha256": source_sha256,
                "total_rows": config.total_rows,
                "official_train_rows": config.total_rows - config.official_test_rows,
                "official_test_rows": config.official_test_rows,
                "feature_count": 28,
                "seed": config.seed,
                "split_files": split_files,
            }
        )
        source_manifest = _source_manifest(config, source_sha256)
        source_manifest_path = temporary_root / "source-manifest.json"
        write_json(source_manifest_path, source_manifest)
        split_manifest = _split_manifest(
            config,
            data_identity=data_identity,
            split_files=split_files,
        )
        split_manifest_path = temporary_root / "split-manifest.json"
        write_json(split_manifest_path, split_manifest)
        split_manifest_sha256 = file_sha256(split_manifest_path)
        model_result = _train_and_export_models(
            config,
            temporary_root=temporary_root,
            arrays=arrays,
            data_identity=data_identity,
        )
        summary = {
            "schema_version": "evm.s3_higgs_preparation_summary.v1",
            "dataset_version": config.dataset_version,
            "dataset_identity_sha256": data_identity,
            "source_manifest_sha256": file_sha256(source_manifest_path),
            "split_manifest_sha256": split_manifest_sha256,
            "source_sha256": source_sha256,
            "row_count": config.total_rows,
            "feature_count": 28,
            "seed": config.seed,
            "preparation_source_revision": config.source_revision,
            "preparation_source_branch": config.source_branch,
            "experiment_config_sha256": config.experiment_config_sha256,
            "sample_counts": {
                "train": config.train_sample_rows,
                "validation": config.validation_sample_rows,
                "test": config.test_sample_rows,
                "replay": config.replay_sample_rows,
            },
            "models": model_result["models"],
            "claim_boundary": (
                "Full-source integrity and deterministic bounded samples for local "
                "single-node S3 capacity work; not full-corpus training or production proof."
            ),
        }
        write_json(temporary_root / "preparation-summary.json", summary)
        config.output_root.parent.mkdir(parents=True, exist_ok=True)
        temporary_root.replace(config.output_root)
        registry = _build_registry(
            config,
            data_identity=data_identity,
            split_manifest_sha256=split_manifest_sha256,
            models=model_result["registry_models"],
        )
        write_json_atomic(config.registry_path, registry)
        summary["registry_sha256"] = file_sha256(config.registry_path)
        summary["output_inventory_sha256"] = directory_inventory_sha256(
            config.output_root
        )
        return summary
    except Exception:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise


def _validate_config(config: HiggsPreparationConfig) -> None:
    if not config.source_path.is_file():
        raise HiggsPreparationError(f"source_missing:{config.source_path}")
    if config.total_rows <= config.official_test_rows:
        raise HiggsPreparationError("official_split_invalid")
    official_train_rows = config.total_rows - config.official_test_rows
    if config.train_sample_rows + config.validation_sample_rows > official_train_rows:
        raise HiggsPreparationError("train_validation_sample_too_large")
    if config.test_sample_rows + config.replay_sample_rows > config.official_test_rows:
        raise HiggsPreparationError("test_replay_sample_too_large")
    if min(
        config.train_sample_rows,
        config.validation_sample_rows,
        config.test_sample_rows,
        config.replay_sample_rows,
        config.incremental_epochs,
        config.incremental_batch_rows,
    ) <= 0:
        raise HiggsPreparationError("sample_or_incremental_config_invalid")
    if config.registry_path.parent != config.output_root.parent:
        raise HiggsPreparationError("registry_and_output_must_share_root")
    if re.fullmatch(r"[a-f0-9]{40}", config.source_revision) is None:
        raise HiggsPreparationError("source_revision_invalid")
    if re.fullmatch(r"[a-f0-9]{64}", config.experiment_config_sha256) is None:
        raise HiggsPreparationError("experiment_config_digest_invalid")
    if not config.source_branch.strip():
        raise HiggsPreparationError("source_branch_missing")


def _select_rows(config: HiggsPreparationConfig) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(config.seed)
    official_train_rows = config.total_rows - config.official_test_rows
    train_selection = rng.choice(
        official_train_rows,
        size=config.train_sample_rows + config.validation_sample_rows,
        replace=False,
    )
    test_selection = rng.choice(
        config.official_test_rows,
        size=config.test_sample_rows + config.replay_sample_rows,
        replace=False,
    )
    return {
        "train": np.sort(train_selection[: config.train_sample_rows]),
        "validation": np.sort(train_selection[config.train_sample_rows :]),
        "test": np.sort(
            official_train_rows + test_selection[: config.test_sample_rows]
        ),
        "replay": np.sort(
            official_train_rows + test_selection[config.test_sample_rows :]
        ),
    }


def _extract_selected_rows(
    config: HiggsPreparationConfig,
    selection: dict[str, np.ndarray],
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    role_names = ("train", "validation", "test", "replay")
    roles = np.zeros(config.total_rows, dtype=np.uint8)
    offsets = np.zeros(config.total_rows, dtype=np.int32)
    arrays: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for role_id, role in enumerate(role_names, start=1):
        indices = selection[role]
        roles[indices] = role_id
        offsets[indices] = np.arange(len(indices), dtype=np.int32)
        arrays[role] = (
            np.empty((len(indices), 28), dtype=np.float32),
            np.empty(len(indices), dtype=np.uint8),
        )

    row_count = 0
    with gzip.open(config.source_path, "rb") as source:
        for row_index, line in enumerate(source):
            row_count = row_index + 1
            if row_index >= config.total_rows:
                raise HiggsPreparationError("source_has_more_rows_than_contract")
            if line.count(b",") != 28:
                raise HiggsPreparationError(f"source_column_count_invalid:{row_index}")
            role_id = int(roles[row_index])
            if role_id == 0:
                continue
            values = np.fromstring(line.decode("ascii"), sep=",", dtype=np.float64)
            if values.shape != (29,) or not np.isfinite(values).all():
                raise HiggsPreparationError(f"selected_row_invalid:{row_index}")
            label = int(values[0])
            if label not in {0, 1} or values[0] != label:
                raise HiggsPreparationError(f"selected_label_invalid:{row_index}")
            role = role_names[role_id - 1]
            target_offset = int(offsets[row_index])
            arrays[role][0][target_offset] = values[1:].astype(np.float32)
            arrays[role][1][target_offset] = label
    if row_count != config.total_rows:
        raise HiggsPreparationError(
            f"source_row_count_mismatch:{row_count}:{config.total_rows}"
        )
    for role, (_, labels) in arrays.items():
        if set(np.unique(labels).tolist()) != {0, 1}:
            raise HiggsPreparationError(f"selected_class_coverage_invalid:{role}")
    return arrays


def _write_split_arrays(
    root: Path,
    selection: dict[str, np.ndarray],
    arrays: dict[str, tuple[np.ndarray, np.ndarray]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for role in ("train", "validation", "test", "replay"):
        split_root = root / "splits" / role
        split_root.mkdir(parents=True)
        features_path = split_root / "features.npy"
        labels_path = split_root / "labels.npy"
        indices_path = split_root / "row-indices.npy"
        _write_npy(features_path, arrays[role][0])
        _write_npy(labels_path, arrays[role][1])
        _write_npy(indices_path, selection[role])
        result[role] = {
            "row_count": len(selection[role]),
            "features_uri": f"splits/{role}/features.npy",
            "features_sha256": file_sha256(features_path),
            "labels_uri": f"splits/{role}/labels.npy",
            "labels_sha256": file_sha256(labels_path),
            "row_indices_uri": f"splits/{role}/row-indices.npy",
            "row_indices_sha256": file_sha256(indices_path),
        }
    return result


def _source_manifest(
    config: HiggsPreparationConfig,
    source_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "evm.s3_higgs_source_manifest.v1",
        "dataset_id": "uci-higgs",
        "dataset_version": config.dataset_version,
        "source_uri": HIGGS_SOURCE_URI,
        "source_doi": HIGGS_SOURCE_DOI,
        "license": HIGGS_LICENSE,
        "source_sha256": source_sha256,
        "source_bytes": config.source_path.stat().st_size,
        "row_count": config.total_rows,
        "feature_count": 28,
        "label_column": 0,
        "official_test_rows": config.official_test_rows,
        "preparation_source_revision": config.source_revision,
        "preparation_source_branch": config.source_branch,
        "experiment_config_sha256": config.experiment_config_sha256,
    }


def _split_manifest(
    config: HiggsPreparationConfig,
    *,
    data_identity: str,
    split_files: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    official_train_rows = config.total_rows - config.official_test_rows
    return {
        "schema_version": "evm.s3_higgs_split_manifest.v1",
        "dataset_id": "uci-higgs",
        "dataset_version": config.dataset_version,
        "dataset_identity_sha256": data_identity,
        "seed": config.seed,
        "preparation_source_revision": config.source_revision,
        "preparation_source_branch": config.source_branch,
        "experiment_config_sha256": config.experiment_config_sha256,
        "official_ranges": {
            "train": {"start_inclusive": 0, "end_exclusive": official_train_rows},
            "test": {
                "start_inclusive": official_train_rows,
                "end_exclusive": config.total_rows,
            },
        },
        "samples": split_files,
    }


def _train_and_export_models(
    config: HiggsPreparationConfig,
    *,
    temporary_root: Path,
    arrays: dict[str, tuple[np.ndarray, np.ndarray]],
    data_identity: str,
) -> dict[str, Any]:
    train_x, train_y = arrays["train"]
    validation_x, validation_y = arrays["validation"]
    test_x, test_y = arrays["test"]
    scaler = StandardScaler()
    scaled_train = scaler.fit_transform(train_x)
    scaled_validation = scaler.transform(validation_x)
    scaled_test = scaler.transform(test_x)
    models_root = temporary_root / "models"
    models_root.mkdir()
    summaries: dict[str, Any] = {}
    registry_models: dict[str, Any] = {}

    logistic = LogisticRegression(
        solver="lbfgs",
        max_iter=config.logistic_max_iter,
        random_state=config.seed,
    )
    summaries["logistic"], registry_models["logistic"] = _fit_export_linear(
        family="logistic",
        estimator=logistic,
        train_x=scaled_train,
        train_y=train_y,
        validation_x=scaled_validation,
        validation_y=validation_y,
        test_x=scaled_test,
        test_y=test_y,
        scaler=scaler,
        data_identity=data_identity,
        models_root=models_root,
    )

    probabilistic = GaussianNB()
    summaries["probabilistic"], registry_models["probabilistic"] = (
        _fit_export_gaussian(
            estimator=probabilistic,
            train_x=train_x,
            train_y=train_y,
            validation_x=validation_x,
            validation_y=validation_y,
            test_x=test_x,
            test_y=test_y,
            data_identity=data_identity,
            models_root=models_root,
        )
    )

    online_linear = SGDClassifier(
        loss="log_loss",
        max_iter=config.online_linear_max_iter,
        tol=1e-4,
        random_state=config.seed,
        average=False,
    )
    summaries["online-linear"], registry_models["online-linear"] = _fit_export_linear(
        family="online-linear",
        estimator=online_linear,
        train_x=scaled_train,
        train_y=train_y,
        validation_x=scaled_validation,
        validation_y=validation_y,
        test_x=scaled_test,
        test_y=test_y,
        scaler=scaler,
        data_identity=data_identity,
        models_root=models_root,
    )

    branch_heavy = DecisionTreeClassifier(
        max_depth=config.tree_max_depth,
        min_samples_leaf=config.tree_min_samples_leaf,
        random_state=config.seed,
    )
    summaries["branch-heavy"], registry_models["branch-heavy"] = _fit_export_tree(
        estimator=branch_heavy,
        train_x=train_x,
        train_y=train_y,
        validation_x=validation_x,
        validation_y=validation_y,
        test_x=test_x,
        test_y=test_y,
        data_identity=data_identity,
        models_root=models_root,
    )

    incremental = SGDClassifier(
        loss="log_loss",
        random_state=config.seed,
        average=True,
        learning_rate="optimal",
    )
    started = time.perf_counter()
    for _ in range(config.incremental_epochs):
        for start in range(0, len(scaled_train), config.incremental_batch_rows):
            stop = min(start + config.incremental_batch_rows, len(scaled_train))
            incremental.partial_fit(
                scaled_train[start:stop],
                train_y[start:stop],
                classes=np.array([0, 1], dtype=np.uint8),
            )
    summaries["incremental"], registry_models["incremental"] = _export_linear(
        family="incremental",
        estimator=incremental,
        fit_seconds=time.perf_counter() - started,
        validation_x=scaled_validation,
        validation_y=validation_y,
        test_x=scaled_test,
        test_y=test_y,
        scaler=scaler,
        data_identity=data_identity,
        models_root=models_root,
    )
    return {"models": summaries, "registry_models": registry_models}


def _fit_export_linear(
    *,
    family: str,
    estimator: Any,
    train_x: np.ndarray,
    train_y: np.ndarray,
    validation_x: np.ndarray,
    validation_y: np.ndarray,
    test_x: np.ndarray,
    test_y: np.ndarray,
    scaler: StandardScaler,
    data_identity: str,
    models_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    estimator.fit(train_x, train_y)
    return _export_linear(
        family=family,
        estimator=estimator,
        fit_seconds=time.perf_counter() - started,
        validation_x=validation_x,
        validation_y=validation_y,
        test_x=test_x,
        test_y=test_y,
        scaler=scaler,
        data_identity=data_identity,
        models_root=models_root,
    )


def _export_linear(
    *,
    family: str,
    estimator: Any,
    fit_seconds: float,
    validation_x: np.ndarray,
    validation_y: np.ndarray,
    test_x: np.ndarray,
    test_y: np.ndarray,
    scaler: StandardScaler,
    data_identity: str,
    models_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = {
        "schema_version": "evm.s3_capacity_probe_artifact.v1",
        "probe_family": family,
        "model_type": "linear_logit",
        "feature_count": 28,
        "dataset_identity_sha256": data_identity,
        "transform": {
            "kind": "standardize",
            "mean": scaler.mean_.tolist(),
            "scale": scaler.scale_.tolist(),
        },
        "model": {
            "weights": estimator.coef_[0].tolist(),
            "intercept": float(estimator.intercept_[0]),
        },
    }
    return _write_model_result(
        family=family,
        artifact=artifact,
        algorithm="linear_logit",
        fit_seconds=fit_seconds,
        estimator=estimator,
        validation_x=validation_x,
        validation_y=validation_y,
        test_x=test_x,
        test_y=test_y,
        data_identity=data_identity,
        models_root=models_root,
    )


def _fit_export_gaussian(
    *,
    estimator: GaussianNB,
    train_x: np.ndarray,
    train_y: np.ndarray,
    validation_x: np.ndarray,
    validation_y: np.ndarray,
    test_x: np.ndarray,
    test_y: np.ndarray,
    data_identity: str,
    models_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    estimator.fit(train_x, train_y)
    artifact = {
        "schema_version": "evm.s3_capacity_probe_artifact.v1",
        "probe_family": "probabilistic",
        "model_type": "gaussian_nb",
        "feature_count": 28,
        "dataset_identity_sha256": data_identity,
        "transform": {"kind": "identity"},
        "model": {
            "theta": estimator.theta_.tolist(),
            "variance": estimator.var_.tolist(),
            "class_log_prior": np.log(estimator.class_prior_).tolist(),
        },
    }
    return _write_model_result(
        family="probabilistic",
        artifact=artifact,
        algorithm="gaussian_nb",
        fit_seconds=time.perf_counter() - started,
        estimator=estimator,
        validation_x=validation_x,
        validation_y=validation_y,
        test_x=test_x,
        test_y=test_y,
        data_identity=data_identity,
        models_root=models_root,
    )


def _fit_export_tree(
    *,
    estimator: DecisionTreeClassifier,
    train_x: np.ndarray,
    train_y: np.ndarray,
    validation_x: np.ndarray,
    validation_y: np.ndarray,
    test_x: np.ndarray,
    test_y: np.ndarray,
    data_identity: str,
    models_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    estimator.fit(train_x, train_y)
    tree = estimator.tree_
    nodes: list[dict[str, Any]] = []
    for index in range(tree.node_count):
        if tree.children_left[index] == tree.children_right[index]:
            counts = tree.value[index][0]
            total = float(np.sum(counts))
            nodes.append(
                {
                    "positive_probability": (
                        float(counts[1] / total) if total else 0.5
                    )
                }
            )
        else:
            nodes.append(
                {
                    "feature": int(tree.feature[index]),
                    "threshold": float(tree.threshold[index]),
                    "left": int(tree.children_left[index]),
                    "right": int(tree.children_right[index]),
                }
            )
    artifact = {
        "schema_version": "evm.s3_capacity_probe_artifact.v1",
        "probe_family": "branch-heavy",
        "model_type": "decision_tree",
        "feature_count": 28,
        "dataset_identity_sha256": data_identity,
        "transform": {"kind": "identity"},
        "model": {"nodes": nodes},
    }
    return _write_model_result(
        family="branch-heavy",
        artifact=artifact,
        algorithm="decision_tree",
        fit_seconds=time.perf_counter() - started,
        estimator=estimator,
        validation_x=validation_x,
        validation_y=validation_y,
        test_x=test_x,
        test_y=test_y,
        data_identity=data_identity,
        models_root=models_root,
    )


def _write_model_result(
    *,
    family: str,
    artifact: dict[str, Any],
    algorithm: str,
    fit_seconds: float,
    estimator: Any,
    validation_x: np.ndarray,
    validation_y: np.ndarray,
    test_x: np.ndarray,
    test_y: np.ndarray,
    data_identity: str,
    models_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact_path = models_root / f"{family}.json"
    write_json(artifact_path, artifact)
    artifact_sha256 = file_sha256(artifact_path)
    model_identity = capacity_model_identity(
        family=family,
        dataset_identity_sha256=data_identity,
        artifact_sha256=artifact_sha256,
        algorithm=algorithm,
    )
    validation_probability = estimator.predict_proba(validation_x)[:, 1]
    test_probability = estimator.predict_proba(test_x)[:, 1]
    metrics = {
        "fit_seconds": fit_seconds,
        "validation_accuracy": float(
            accuracy_score(validation_y, validation_probability >= 0.5)
        ),
        "validation_log_loss": float(log_loss(validation_y, validation_probability)),
        "validation_roc_auc": float(
            roc_auc_score(validation_y, validation_probability)
        ),
        "test_accuracy": float(accuracy_score(test_y, test_probability >= 0.5)),
        "test_log_loss": float(log_loss(test_y, test_probability)),
        "test_roc_auc": float(roc_auc_score(test_y, test_probability)),
    }
    if any(not math.isfinite(value) for value in metrics.values()):
        raise HiggsPreparationError(f"model_metric_non_finite:{family}")
    summary = {
        "algorithm": algorithm,
        "artifact_sha256": artifact_sha256,
        "model_identity_sha256": model_identity,
        "metrics": metrics,
    }
    registry = {
        "algorithm": algorithm,
        "artifact_uri": f"{models_root.parent.name}/models/{family}.json",
        "artifact_sha256": artifact_sha256,
        "model_identity_sha256": model_identity,
    }
    return summary, registry


def _build_registry(
    config: HiggsPreparationConfig,
    *,
    data_identity: str,
    split_manifest_sha256: str,
    models: dict[str, Any],
) -> dict[str, Any]:
    if set(models) != set(PROBE_FAMILIES):
        raise HiggsPreparationError("registry_model_set_invalid")
    registry_models = {
        family: {
            **entry,
            "artifact_uri": f"{config.output_root.name}/models/{family}.json",
        }
        for family, entry in models.items()
    }
    return {
        "schema_version": "evm.s3_capacity_registry.v1",
        "dataset_id": "uci-higgs",
        "dataset_version": config.dataset_version,
        "dataset_identity_sha256": data_identity,
        "split_manifest_sha256": split_manifest_sha256,
        "source_uri": HIGGS_SOURCE_URI,
        "source_doi": HIGGS_SOURCE_DOI,
        "license": HIGGS_LICENSE,
        "feature_count": 28,
        "seed": config.seed,
        "preparation_source_revision": config.source_revision,
        "preparation_source_branch": config.source_branch,
        "experiment_config_sha256": config.experiment_config_sha256,
        "model_config": {
            "logistic_max_iter": config.logistic_max_iter,
            "online_linear_max_iter": config.online_linear_max_iter,
            "tree_max_depth": config.tree_max_depth,
            "tree_min_samples_leaf": config.tree_min_samples_leaf,
            "incremental_epochs": config.incremental_epochs,
            "incremental_batch_rows": config.incremental_batch_rows,
        },
        "probes": registry_models,
    }


def capacity_model_identity(
    *,
    family: str,
    dataset_identity_sha256: str,
    artifact_sha256: str,
    algorithm: str,
) -> str:
    return payload_sha256(
        {
            "schema_version": "evm.s3_capacity_model_identity.v1",
            "probe_family": family,
            "dataset_identity_sha256": dataset_identity_sha256,
            "artifact_sha256": artifact_sha256,
            "algorithm": algorithm,
        }
    )


def payload_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def directory_inventory_sha256(root: Path) -> str:
    inventory = [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    return payload_sha256(inventory)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_bytes(canonical_json_bytes(value) + b"\n")
    temporary.replace(path)


def _write_npy(path: Path, value: np.ndarray) -> None:
    with path.open("wb") as handle:
        np.save(handle, value, allow_pickle=False)
