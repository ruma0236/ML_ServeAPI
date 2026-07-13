from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import statistics
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from evm.control_panel.experiment_runs import (
    ExperimentRun,
    FoldResult,
    ModelQualityReview,
    TrainingTelemetry,
    TrialResult,
    cancellation_requested,
    experiment_dir,
    utc_now,
    write_experiment,
)
from evm.core.mlflow_client import MlflowRestClient
from evm.core.pipeline import write_json
from evm.core.torch_efficientnet import (
    EfficientNetCandidateConfig,
    TorchRuntimeConfig,
    load_shard_records,
    normalized_label,
    split_manifest_snapshot,
    train_candidate,
)
from evm.pipelines.efficientnet_training.run import (
    candidate_config,
    file_sha256,
    load_config,
    required_candidate_blockers,
    run as run_final_training,
    runtime_config_path,
    source_digest_blockers,
)


Trainer = Callable[
    [
        EfficientNetCandidateConfig,
        dict[str, list[dict[str, Any]]],
        dict[str, Any],
        dict[str, Any],
        TorchRuntimeConfig,
        Path,
    ],
    dict[str, Any],
]
MlflowFactory = Callable[[str], MlflowRestClient]
FinalTrainer = Callable[..., dict[str, Any]]


_PROMOTION_GATE_PATTERN = re.compile(
    r"^(?P<metric>[A-Za-z][A-Za-z0-9_]*)"
    r"(?P<operator><=|>=|<|>)"
    r"(?P<threshold>-?(?:\d+(?:\.\d*)?|\.\d+))$"
)


class ExperimentCancelled(RuntimeError):
    pass


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def update_training_telemetry(
    state: ExperimentRun,
    payload: dict[str, Any],
    *,
    unit_role: str,
    trial_id: str | None = None,
    repeat: int | None = None,
    fold: int | None = None,
) -> None:
    unit_progress = max(0.0, min(1.0, float(payload.get("unit_progress") or 0.0)))
    validation_metrics = {
        str(key): float(value)
        for key, value in object_value(payload, "validation_metrics").items()
        if isinstance(value, int | float)
    }
    state.training_telemetry = TrainingTelemetry(
        unit_role=unit_role,  # type: ignore[arg-type]
        phase=str(payload.get("phase") or "preparing"),  # type: ignore[arg-type]
        trial_id=trial_id,
        repeat=repeat,
        fold=fold,
        epoch=max(0, int(payload.get("epoch") or 0)),
        epochs=max(0, int(payload.get("epochs") or 0)),
        step=max(0, int(payload.get("step") or 0)),
        steps=max(0, int(payload.get("steps") or 0)),
        optimizer_steps=max(0, int(payload.get("optimizer_steps") or 0)),
        unit_progress=unit_progress,
        train_loss=(
            float(payload["train_loss"])
            if isinstance(payload.get("train_loss"), int | float)
            else None
        ),
        validation_metrics=validation_metrics,
        updated_at=utc_now(),
    )
    state.progress = min(
        1.0,
        (state.completed_units + unit_progress) / state.total_units,
    )
    write_experiment(state)


def build_quality_review(
    state: ExperimentRun,
    final_matrix: dict[str, Any],
    *,
    candidate_id: str,
    root: Path,
) -> ModelQualityReview | None:
    candidate = next(
        (
            item
            for item in final_matrix.get("candidates", [])
            if isinstance(item, dict) and str(item.get("candidate_id")) == candidate_id
        ),
        None,
    )
    if not isinstance(candidate, dict):
        return None
    failed_gates = sorted(
        str(item)
        for item in candidate.get("promotion_blockers", [])
        if str(item).strip()
    )
    if not failed_gates:
        return None
    observed_metrics = {
        str(key): float(value)
        for key, value in object_value(candidate, "metrics").items()
        if isinstance(value, int | float)
    }
    thresholds: dict[str, float] = {}
    failed_metrics: set[str] = set()
    for gate in failed_gates:
        match = _PROMOTION_GATE_PATTERN.fullmatch(gate)
        if match is None:
            continue
        metric = match.group("metric")
        failed_metrics.add(metric)
        thresholds[metric] = float(match.group("threshold"))
    recommendations = quality_recommendations(failed_metrics)
    fingerprint = canonical_sha256(
        {
            "profile_digest": state.profile_digest,
            "dataset_version": state.dataset_version,
            "source_manifest_sha256": state.source_manifest_sha256,
            "selected_trial_id": state.selected_trial_id,
            "selected_parameters": state.selected_parameters,
            "candidate_id": candidate_id,
            "failed_gates": failed_gates,
        }
    )
    review_path = root / "model_quality_review.json"
    review = ModelQualityReview(
        event_id=f"model-quality-{fingerprint[:16]}",
        state="review_required",
        fingerprint=fingerprint,
        source_profile_digest=state.profile_digest,
        dataset_version=state.dataset_version,
        selected_trial_id=state.selected_trial_id,
        selected_parameters=state.selected_parameters,
        candidate_id=candidate_id,
        observed_metrics=observed_metrics,
        policy_thresholds=thresholds,
        failed_gates=failed_gates,
        recommendations=recommendations,
        evidence_uri=str(review_path),
        created_at=utc_now(),
    )
    write_json(review_path, review.model_dump(mode="json"))
    return review


def quality_recommendations(failed_metrics: set[str]) -> list[str]:
    recommendations = ["revise_blueprint_before_retry"]
    if failed_metrics.intersection({"f1", "precision", "recall"}):
        recommendations.extend(
            [
                "unfreeze_backbone",
                "tune_class_balance_or_focal_loss",
                "recalibrate_decision_threshold",
            ]
        )
    if failed_metrics.intersection({"accuracy", "f1", "auroc"}):
        recommendations.extend(
            ["expand_learning_rate_search", "increase_epoch_budget"]
        )
    if "latency_p95_ms" in failed_metrics:
        recommendations.append("profile_inference_optimization")
    return list(dict.fromkeys(recommendations))


def record_id(record: dict[str, Any]) -> str:
    source = next(
        (
            str(record.get(key) or "")
            for key in ("record_id", "image_id", "image_uri", "path", "source_uri")
            if record.get(key)
        ),
        "",
    )
    if not source:
        source = canonical_sha256(
            {key: value for key, value in record.items() if not key.startswith("_")}
        )
    return hashlib.sha256(
        f"{source}|{normalized_label(record)}".encode("utf-8")
    ).hexdigest()


def records_sha256(records: list[dict[str, Any]]) -> str:
    return canonical_sha256(sorted(record_id(record) for record in records))


def build_fold_partitions(
    records: list[dict[str, Any]],
    *,
    folds: int,
    seed: int,
) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    from sklearn.model_selection import StratifiedKFold

    labels = [normalized_label(record) for record in records]
    splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    partitions: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
    for train_indices, validation_indices in splitter.split(records, labels):
        partitions.append(
            (
                [records[int(index)] for index in train_indices],
                [records[int(index)] for index in validation_indices],
            )
        )
    return partitions


def grid_parameters(
    base_candidate: dict[str, Any],
    search_space: dict[str, Any],
    max_trials: int,
) -> list[dict[str, Any]]:
    dimensions = [
        ("learning_rate", values(search_space, "learning_rates", base_candidate["learning_rate"])),
        ("weight_decay", values(search_space, "weight_decays", base_candidate.get("weight_decay", 0.0))),
        ("batch_size", values(search_space, "batch_sizes", base_candidate["batch_size"])),
        ("optimizer", values(search_space, "optimizers", base_candidate["optimizer"])),
        (
            "freeze_backbone",
            values(
                search_space,
                "freeze_backbone_options",
                base_candidate.get("freeze_backbone", False),
            ),
        ),
    ]
    combinations = [
        dict(zip((name for name, _ in dimensions), combination, strict=True))
        for combination in itertools.product(*(items for _, items in dimensions))
    ]
    combinations.sort(key=lambda item: canonical_sha256(item))
    return combinations[:max_trials]


def manual_parameters(base_candidate: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "learning_rate": float(base_candidate["learning_rate"]),
            "weight_decay": float(base_candidate.get("weight_decay", 0.0)),
            "batch_size": int(base_candidate["batch_size"]),
            "optimizer": str(base_candidate["optimizer"]),
            "freeze_backbone": bool(base_candidate.get("freeze_backbone", False)),
        }
    ]


def values(search_space: dict[str, Any], key: str, fallback: Any) -> list[Any]:
    observed = search_space.get(key)
    if isinstance(observed, list) and observed:
        return list(dict.fromkeys(observed))
    return [fallback]


def aggregate_fold_metrics(folds: list[FoldResult]) -> dict[str, float]:
    names = sorted({name for fold in folds for name in fold.metrics})
    aggregate: dict[str, float] = {}
    for name in names:
        samples = [fold.metrics[name] for fold in folds if name in fold.metrics]
        if not samples:
            continue
        aggregate[f"{name}_mean"] = float(statistics.mean(samples))
        aggregate[f"{name}_std"] = (
            float(statistics.pstdev(samples)) if len(samples) > 1 else 0.0
        )
    return aggregate


def trial_score(trial: TrialResult, primary_metric: str) -> float:
    return float(trial.aggregate_metrics.get(f"{primary_metric}_mean", 0.0))


def run(
    config_path: str = "configs/w7_efficientnet_real_test.toml",
    *,
    trainer: Trainer = train_candidate,
    mlflow_factory: MlflowFactory = MlflowRestClient,
    final_trainer: FinalTrainer = run_final_training,
) -> dict[str, Any]:
    config = load_config(config_path)
    search = object_value(config, "experiment_search")
    if not bool(search.get("enabled")):
        raise ValueError("experiment_search_not_enabled")
    control_plane = object_value(config, "control_plane")
    profile_model = object_value(control_plane, "model")
    profile_data = object_value(control_plane, "data")
    profile_split = object_value(profile_data, "split")
    experiment_profile = object_value(control_plane, "experiment")
    resources_profile = object_value(control_plane, "resources")
    inputs = object_value(config, "inputs")
    acceptance = object_value(config, "acceptance")
    execution = object_value(config, "execution")
    matrix = object_value(config, "model_matrix")
    candidates = config.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict):
        raise ValueError("experiment_search_requires_one_base_candidate")
    base_candidate = dict(candidates[0])

    experiment_id = (
        os.getenv("EVM_LIFECYCLE_RUN_ID", "").strip()
        or os.getenv("EVM_EXPERIMENT_RUN_ID", "").strip()
        or f"experiment-{utc_now().replace(':', '').replace('-', '').replace('Z', '')}-{canonical_sha256(control_plane)[:8]}"
    )
    root = experiment_dir(experiment_id)
    root.mkdir(parents=True, exist_ok=True)
    mode = str(search.get("mode") or profile_model.get("tuning_mode") or "manual")
    if mode not in {"manual", "grid", "bayesian"}:
        raise ValueError(f"unsupported_search_mode:{mode}")
    folds = int(search.get("folds") or profile_split.get("cross_validation_folds") or 5)
    repeats = int(search.get("repeats") or experiment_profile.get("repeats") or 1)
    seed = int(search.get("seed") or profile_split.get("seed") or 20260706)
    primary_metric = str(
        search.get("primary_metric") or experiment_profile.get("primary_metric") or "f1"
    )
    max_trials = int(search.get("max_trials") or profile_model.get("max_trials") or 1)
    gpu_quota = int(search.get("gpu_quota") or resources_profile.get("gpu_count") or 0)
    requested_parallelism = int(
        search.get("max_parallel_trials")
        or resources_profile.get("max_parallel_trials")
        or 1
    )
    if gpu_quota < 1:
        raise ValueError("experiment_search_gpu_quota_missing")
    if requested_parallelism > gpu_quota:
        raise ValueError("experiment_search_parallelism_exceeds_gpu_quota")
    if bool(search.get("allow_holdout_in_search")):
        raise ValueError("experiment_search_holdout_access_forbidden")
    holdout_split = str(search.get("holdout_split") or profile_split.get("holdout_split") or "test")
    if holdout_split != "test":
        raise ValueError("experiment_search_requires_test_holdout")

    shard_index_path = runtime_config_path(config, str(inputs.get("shard_index") or ""))
    shard_index, splits = load_shard_records(shard_index_path)
    actual_source_sha = str(shard_index.get("identity_sha256") or file_sha256(shard_index_path))
    expected_source_sha = str(
        os.getenv("EVM_TRAINING_VIEW_IDENTITY")
        or inputs.get("shard_identity_sha256")
        or inputs.get("shard_index_sha256")
        or ""
    )
    source_blockers = source_digest_blockers(actual_source_sha, expected_source_sha)
    exposed_holdout_shards = [
        str(shard.get("shard_id") or "unknown")
        for shard in shard_index.get("shards", [])
        if isinstance(shard, dict) and str(shard.get("split") or "") == holdout_split
    ]
    if exposed_holdout_shards:
        source_blockers.append("experiment_search_ct_shard_exposed")
    if str(shard_index.get("training_data_scope") or "") != "development-only":
        source_blockers.append("experiment_search_development_view_required")
    if shard_index.get("ct_evidence_exposed") is not False:
        source_blockers.append("experiment_search_ct_exposure_contract_missing")
    development_records = [*splits["train"], *splits["validation"]]
    if not development_records:
        source_blockers.append("experiment_search_development_split_empty")
    if source_blockers:
        raise ValueError(",".join(source_blockers))

    search_space = object_value(search, "search_space")
    parameter_sets = (
        manual_parameters(base_candidate)
        if mode == "manual"
        else grid_parameters(base_candidate, search_space, max_trials)
        if mode == "grid"
        else []
    )
    trial_count = len(parameter_sets) if mode != "bayesian" else max_trials
    final_refit = bool(search.get("final_refit", True))
    now = utc_now()
    state = ExperimentRun(
        experiment_id=experiment_id,
        lifecycle_run_id=experiment_id,
        profile_name=str(control_plane.get("profile_name") or "unknown-profile"),
        profile_digest=str(
            control_plane.get("profile_digest") or canonical_sha256(control_plane)
        ),
        dataset_version=str(matrix.get("dataset_version") or "unknown"),
        source_manifest_sha256=actual_source_sha,
        holdout_split=holdout_split,
        holdout_sha256="",
        holdout_access_policy="isolated_control_plane_only",
        ct_evidence_exposed=False,
        mode=mode,  # type: ignore[arg-type]
        primary_metric=primary_metric,  # type: ignore[arg-type]
        seed=seed,
        folds=folds,
        repeats=repeats,
        requested_trials=trial_count,
        total_units=(trial_count * folds * repeats) + (1 if final_refit else 0),
        state="planned",
        gpu_quota=gpu_quota,
        scheduled_parallelism=1,
        created_at=now,
        updated_at=now,
    )
    write_experiment(state)

    client = mlflow_factory(str(inputs.get("mlflow_tracking_uri") or "http://localhost:5000"))
    parent_run_id: str | None = None
    try:
        if not client.health():
            raise RuntimeError("mlflow_health_failed")
        experiment_name = str(
            inputs.get("mlflow_experiment_name")
            or experiment_profile.get("mlflow_experiment_name")
            or "enterprise-vision-profile-runs"
        )
        mlflow_experiment_id = client.get_or_create_experiment(experiment_name)
        if not mlflow_experiment_id:
            raise RuntimeError("mlflow_experiment_missing")
        parent_run_id = client.create_run(
            mlflow_experiment_id,
            f"{state.profile_name}-{mode}-search",
            tags={
                "evm.run_role": "experiment_search_parent",
                "evm.lifecycle_run_id": experiment_id,
                "evm.profile_digest": state.profile_digest,
            },
        )
        if not parent_run_id:
            raise RuntimeError("mlflow_parent_run_missing")
        state.parent_mlflow_run_id = parent_run_id
        state.state = "running"
        state.started_at = utc_now()
        write_experiment(state)
        log_parent_params(client, parent_run_id, state, search_space)

        fold_assignments: list[dict[str, Any]] = []
        partitions_by_repeat: list[list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]] = []
        for repeat in range(repeats):
            partitions = build_fold_partitions(
                development_records,
                folds=folds,
                seed=seed + repeat,
            )
            partitions_by_repeat.append(partitions)
            for fold, (_, validation_records) in enumerate(partitions):
                fold_assignments.extend(
                    {
                        "record_id": record_id(record),
                        "label": normalized_label(record),
                        "repeat": repeat,
                        "fold": fold,
                    }
                    for record in validation_records
                )
        fold_manifest = {
            "schema_version": "evm.experiment_fold_manifest.v1",
            "experiment_id": experiment_id,
            "dataset_version": state.dataset_version,
            "source_manifest_sha256": actual_source_sha,
            "development_records": len(development_records),
            "holdout_split": holdout_split,
            "holdout_access_policy": "isolated_control_plane_only",
            "holdout_used_for_selection": False,
            "ct_evidence_exposed": False,
            "folds": folds,
            "repeats": repeats,
            "seed": seed,
            "assignments": fold_assignments,
        }
        fold_manifest["assignment_sha256"] = canonical_sha256(fold_assignments)
        fold_manifest_path = root / "fold_manifest.json"
        write_json(fold_manifest_path, fold_manifest)
        state.fold_manifest_uri = str(fold_manifest_path)
        write_experiment(state)

        def evaluate(parameters: dict[str, Any], trial_number: int) -> float:
            trial = execute_trial(
                state,
                base_candidate=base_candidate,
                parameters=parameters,
                trial_number=trial_number,
                partitions_by_repeat=partitions_by_repeat,
                fold_manifest=fold_manifest,
                acceptance=acceptance,
                execution=execution,
                inputs=inputs,
                root=root,
                trainer=trainer,
            )
            write_comparison_matrix(state, root)
            write_experiment(state)
            if trial.state != "completed":
                raise RuntimeError(trial.blocker or f"trial_failed:{trial.trial_id}")
            return trial_score(trial, primary_metric)

        if mode == "bayesian":
            import optuna

            sampler = optuna.samplers.TPESampler(seed=seed)
            study = optuna.create_study(direction="maximize", sampler=sampler)

            def objective(optuna_trial: Any) -> float:
                parameters = {
                    "learning_rate": optuna_trial.suggest_categorical(
                        "learning_rate",
                        values(search_space, "learning_rates", base_candidate["learning_rate"]),
                    ),
                    "weight_decay": optuna_trial.suggest_categorical(
                        "weight_decay",
                        values(
                            search_space,
                            "weight_decays",
                            base_candidate.get("weight_decay", 0.0),
                        ),
                    ),
                    "batch_size": optuna_trial.suggest_categorical(
                        "batch_size",
                        values(search_space, "batch_sizes", base_candidate["batch_size"]),
                    ),
                    "optimizer": optuna_trial.suggest_categorical(
                        "optimizer",
                        values(search_space, "optimizers", base_candidate["optimizer"]),
                    ),
                    "freeze_backbone": optuna_trial.suggest_categorical(
                        "freeze_backbone",
                        values(
                            search_space,
                            "freeze_backbone_options",
                            base_candidate.get("freeze_backbone", False),
                        ),
                    ),
                }
                score = evaluate(parameters, optuna_trial.number)
                optuna_trial.set_user_attr("experiment_id", experiment_id)
                return score

            study.optimize(objective, n_trials=max_trials, n_jobs=1)
        else:
            for trial_number, parameters in enumerate(parameter_sets):
                evaluate(parameters, trial_number)

        completed_trials = [trial for trial in state.trials if trial.state == "completed"]
        if not completed_trials:
            raise RuntimeError("experiment_search_no_completed_trials")
        selected = sorted(
            completed_trials,
            key=lambda trial: (-trial_score(trial, primary_metric), trial.trial_id),
        )[0]
        state.selected_trial_id = selected.trial_id
        state.selected_parameters = dict(selected.parameters)
        write_comparison_matrix(state, root)
        write_experiment(state)

        if cancellation_requested(experiment_id):
            raise ExperimentCancelled("experiment_cancelled_before_final_refit")
        if final_refit:
            final_matrix = execute_final_refit(
                config,
                base_candidate,
                selected,
                state,
                root,
                final_trainer,
            )
            blockers = required_candidate_blockers(final_matrix)
            state.final_model_matrix_uri = str(
                runtime_config_path(
                    config,
                    object_value(config, "resources").get("artifact_root", ""),
                )
                / "latest_model_matrix.json"
            )
            state.completed_units += 1
            state.progress = min(1.0, state.completed_units / state.total_units)
            if blockers:
                state.quality_review = build_quality_review(
                    state,
                    final_matrix,
                    candidate_id=str(base_candidate["candidate_id"]),
                    root=root,
                )
                review_blocker = (
                    f"model_quality_review_required:{state.quality_review.fingerprint}"
                    if state.quality_review is not None
                    else None
                )
                state.blockers = sorted(
                    set([*blockers, *([review_blocker] if review_blocker else [])])
                )
                state.state = "blocked"
            else:
                state.state = "completed"
        else:
            state.state = "completed"
            state.progress = 1.0
        state.finished_at = utc_now()
        client.log_param(parent_run_id, "selected_trial_id", state.selected_trial_id)
        client.log_param(
            parent_run_id,
            "selected_parameters",
            json.dumps(state.selected_parameters, sort_keys=True),
        )
        client.log_metric(
            parent_run_id,
            f"selected_{primary_metric}_mean",
            trial_score(selected, primary_metric),
        )
        client.terminate_run(
            parent_run_id,
            status="FINISHED" if state.state == "completed" else "FAILED",
        )
        write_experiment(state)
        return state.model_dump(mode="json")
    except ExperimentCancelled as exc:
        state.state = "cancelled"
        state.blockers = [str(exc)]
        state.finished_at = utc_now()
        if parent_run_id:
            client.terminate_run(parent_run_id, status="KILLED")
        write_experiment(state)
        return state.model_dump(mode="json")
    except Exception as exc:
        state.state = "failed"
        state.blockers = sorted(set([*state.blockers, str(exc)]))
        state.finished_at = utc_now()
        if parent_run_id:
            client.terminate_run(parent_run_id, status="FAILED")
        write_experiment(state)
        return state.model_dump(mode="json")


def execute_trial(
    state: ExperimentRun,
    *,
    base_candidate: dict[str, Any],
    parameters: dict[str, Any],
    trial_number: int,
    partitions_by_repeat: list[list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]],
    fold_manifest: dict[str, Any],
    acceptance: dict[str, Any],
    execution: dict[str, Any],
    inputs: dict[str, Any],
    root: Path,
    trainer: Trainer,
) -> TrialResult:
    trial_id = f"trial-{trial_number + 1:03d}"
    trial = TrialResult(
        trial_id=trial_id,
        state="running",
        parameters=parameters,
    )
    state.trials.append(trial)
    write_experiment(state)
    for repeat, partitions in enumerate(partitions_by_repeat):
        for fold, (train_records, validation_records) in enumerate(partitions):
            if cancellation_requested(state.experiment_id):
                trial.state = "cancelled"
                trial.blocker = "experiment_cancellation_requested"
                write_experiment(state)
                raise ExperimentCancelled(trial.blocker)
            fold_seed = state.seed + (repeat * state.folds) + fold
            candidate_payload = dict(base_candidate)
            candidate_payload.update(parameters)
            candidate_payload["candidate_id"] = (
                f"{base_candidate['candidate_id']}--{trial_id}-r{repeat + 1}-f{fold + 1}"
            )
            candidate = candidate_config(candidate_payload, acceptance)
            fold_splits = {
                "train": train_records,
                "validation": validation_records,
                "test": validation_records,
            }
            split_manifest = split_manifest_snapshot(
                {},
                fold_splits,
                dataset_version=state.dataset_version,
                seed=fold_seed,
            )
            split_manifest.update(
                {
                    "schema_version": "evm.experiment_fold_split.v1",
                    "experiment_id": state.experiment_id,
                    "trial_id": trial_id,
                    "repeat": repeat,
                    "fold": fold,
                    "role": "development_cross_validation",
                    "source_manifest_sha256": state.source_manifest_sha256,
                    "fold_assignment_sha256": fold_manifest["assignment_sha256"],
                    "immutable_holdout_used": False,
                    "holdout_access_policy": "isolated_control_plane_only",
                    "ct_evidence_exposed": False,
                }
            )
            fold_dir = root / "trials" / trial_id / f"repeat-{repeat + 1}" / f"fold-{fold + 1}"

            def report_fold_progress(payload: dict[str, Any]) -> None:
                update_training_telemetry(
                    state,
                    payload,
                    unit_role="cross_validation",
                    trial_id=trial_id,
                    repeat=repeat,
                    fold=fold,
                )

            runtime = TorchRuntimeConfig(
                seed=fold_seed,
                require_cuda=True,
                num_workers=int(execution.get("num_workers") or 4),
                pin_memory=bool(execution.get("pin_memory", True)),
                mlflow_tracking_uri=str(inputs.get("mlflow_tracking_uri") or "http://localhost:5000"),
                mlflow_experiment_name=str(
                    inputs.get("mlflow_experiment_name")
                    or "enterprise-vision-profile-runs"
                ),
                parent_run_id=state.parent_mlflow_run_id,
                run_tags={
                    "evm.run_role": "cross_validation_fold",
                    "evm.lifecycle_run_id": state.lifecycle_run_id,
                    "evm.experiment_id": state.experiment_id,
                    "evm.trial_id": trial_id,
                    "evm.repeat": str(repeat),
                    "evm.fold": str(fold),
                    "evm.holdout_used": "false",
                },
                progress_callback=report_fold_progress,
            )
            try:
                summary = trainer(
                    candidate,
                    fold_splits,
                    split_manifest,
                    acceptance,
                    runtime,
                    fold_dir,
                )
                if summary.get("status") != "pass":
                    raise RuntimeError(
                        ",".join(str(item) for item in summary.get("execution_blockers", []))
                        or "fold_training_not_pass"
                    )
                metrics = {
                    key: float(value)
                    for key, value in object_value(summary, "metrics").items()
                    if isinstance(value, int | float)
                }
                result = FoldResult(
                    repeat=repeat,
                    fold=fold,
                    state="completed",
                    seed=fold_seed,
                    train_records=len(train_records),
                    validation_records=len(validation_records),
                    metrics=metrics,
                    mlflow_run_id=str(summary.get("mlflow_run_id") or "") or None,
                    artifact_uri=str(fold_dir),
                )
            except Exception as exc:
                result = FoldResult(
                    repeat=repeat,
                    fold=fold,
                    state="blocked",
                    seed=fold_seed,
                    train_records=len(train_records),
                    validation_records=len(validation_records),
                    artifact_uri=str(fold_dir),
                    blocker=str(exc),
                )
                trial.folds.append(result)
                trial.state = "blocked"
                trial.blocker = str(exc)
                write_experiment(state)
                return trial
            trial.folds.append(result)
            state.completed_units += 1
            state.progress = min(1.0, state.completed_units / state.total_units)
            write_experiment(state)
    trial.aggregate_metrics = aggregate_fold_metrics(trial.folds)
    trial.score = trial_score(trial, state.primary_metric)
    trial.state = "completed"
    write_experiment(state)
    return trial


def execute_final_refit(
    config: dict[str, Any],
    base_candidate: dict[str, Any],
    selected: TrialResult,
    state: ExperimentRun,
    root: Path,
    final_trainer: FinalTrainer,
) -> dict[str, Any]:
    derived = {
        key: value
        for key, value in config.items()
        if not str(key).startswith("_")
    }
    selected_candidate = dict(base_candidate)
    selected_candidate.update(selected.parameters)
    derived["candidates"] = [selected_candidate]
    derived["experiment_search"] = {
        **object_value(config, "experiment_search"),
        "enabled": False,
        "selected_trial_id": selected.trial_id,
    }
    execution = object_value(derived, "execution")
    execution.update(
        {
            "mlflow_parent_run_id": state.parent_mlflow_run_id,
            "mlflow_run_role": "selected_final_refit",
            "lifecycle_run_id": state.lifecycle_run_id,
            "evaluation_split": "validation",
            "training_data_scope": "development-only",
        }
    )
    derived["execution"] = execution
    path = root / "selected_final_refit.runtime.json"
    write_json(path, derived)

    def report_final_progress(payload: dict[str, Any]) -> None:
        update_training_telemetry(
            state,
            payload,
            unit_role="final_refit",
            trial_id=selected.trial_id,
        )

    report_final_progress(
        {
            "phase": "final_refit",
            "epoch": 0,
            "epochs": 0,
            "step": 0,
            "steps": 0,
            "optimizer_steps": 0,
            "unit_progress": 0.0,
        }
    )
    with temporary_environment(
        EVM_EFFICIENTNET_RUN_ID=state.lifecycle_run_id,
        EVM_EFFICIENTNET_CANDIDATES=str(base_candidate["candidate_id"]),
        EVM_MLFLOW_PARENT_RUN_ID=state.parent_mlflow_run_id or "",
        EVM_MLFLOW_RUN_ROLE="selected_final_refit",
    ):
        matrix = final_trainer(path, progress_callback=report_final_progress)
    comparison_uri = str(root / "comparison_matrix.json")
    for candidate in matrix.get("candidates", []):
        if not isinstance(candidate, dict) or candidate.get("candidate_id") != base_candidate["candidate_id"]:
            continue
        candidate.update(
            {
                "experiment_run_id": state.experiment_id,
                "search_mode": state.mode,
                "selected_trial_id": selected.trial_id,
                "selected_parameters": selected.parameters,
                "cv_metrics": selected.aggregate_metrics,
                "comparison_matrix_uri": comparison_uri,
                "parent_mlflow_run_id": state.parent_mlflow_run_id,
                "selection_holdout_used": False,
                "holdout_access_policy": "isolated_control_plane_only",
                "ct_evidence_exposed": False,
            }
        )
        candidate_artifact_uri = str(candidate.get("artifact_uri") or "").strip()
        if candidate_artifact_uri:
            candidate_dir = Path(candidate_artifact_uri)
            write_json(candidate_dir / "candidate_summary.json", candidate)
    matrix.update(
        {
            "experiment_run_id": state.experiment_id,
            "search_mode": state.mode,
            "selected_trial_id": selected.trial_id,
            "comparison_matrix_uri": comparison_uri,
            "selection_holdout_used": False,
            "holdout_access_policy": "isolated_control_plane_only",
            "ct_evidence_exposed": False,
        }
    )
    resources = object_value(config, "resources")
    artifact_root = runtime_config_path(config, str(resources.get("artifact_root") or ""))
    matrix_dir = artifact_root / str(object_value(config, "model_matrix").get("matrix_id") or "")
    write_json(matrix_dir / "model_matrix.json", matrix)
    write_json(artifact_root / "latest_model_matrix.json", matrix)
    return matrix


def write_comparison_matrix(state: ExperimentRun, root: Path) -> None:
    payload = {
        "schema_version": "evm.experiment_comparison_matrix.v1",
        "experiment_id": state.experiment_id,
        "profile_digest": state.profile_digest,
        "dataset_version": state.dataset_version,
        "source_manifest_sha256": state.source_manifest_sha256,
        "holdout_used_for_selection": False,
        "holdout_access_policy": "isolated_control_plane_only",
        "ct_evidence_exposed": False,
        "mode": state.mode,
        "primary_metric": state.primary_metric,
        "seed": state.seed,
        "selected_trial_id": state.selected_trial_id,
        "trials": [trial.model_dump(mode="json") for trial in state.trials],
        "created_at": utc_now(),
    }
    path = root / "comparison_matrix.json"
    write_json(path, payload)
    state.comparison_matrix_uri = str(path)


def log_parent_params(
    client: MlflowRestClient,
    run_id: str,
    state: ExperimentRun,
    search_space: dict[str, Any],
) -> None:
    params = {
        "experiment_id": state.experiment_id,
        "profile_digest": state.profile_digest,
        "dataset_version": state.dataset_version,
        "source_manifest_sha256": state.source_manifest_sha256,
        "holdout_used_for_selection": False,
        "holdout_access_policy": "isolated_control_plane_only",
        "ct_evidence_exposed": False,
        "mode": state.mode,
        "folds": state.folds,
        "repeats": state.repeats,
        "requested_trials": state.requested_trials,
        "seed": state.seed,
        "primary_metric": state.primary_metric,
        "gpu_quota": state.gpu_quota,
        "scheduled_parallelism": state.scheduled_parallelism,
        "search_space": json.dumps(search_space, sort_keys=True),
    }
    failed = [key for key, value in params.items() if not client.log_param(run_id, key, value)]
    if failed:
        raise RuntimeError(f"mlflow_parent_param_write_failed:{','.join(failed)}")


def object_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return dict(value) if isinstance(value, dict) else {}


@contextmanager
def temporary_environment(**values_to_set: str):
    previous = {key: os.environ.get(key) for key in values_to_set}
    try:
        for key, value in values_to_set.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main(argv: Sequence[str] | None = None) -> None:
    import sys

    arguments = list(sys.argv[1:] if argv is None else argv)
    require_pass = "--require-pass" in arguments
    positional = [argument for argument in arguments if not argument.startswith("--")]
    config_path = positional[0] if positional else "configs/w7_efficientnet_real_test.toml"
    summary = run(config_path)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if require_pass and summary.get("state") != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
