from __future__ import annotations

import json
import os
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evm.control_panel.cdct import build_cdct_gate
from evm.control_panel.drift import build_drift_state
from evm.control_panel.schemas import (
    AirflowRef,
    ArtifactRef,
    CycleRun,
    DatasetVersion,
    MLflowRef,
    Metric,
    ModelCandidate,
    ModelExperimentMatrix,
    ModelVersion,
    PipelineStage,
    PromotionGate,
    RealTestPolicy,
    ResourceRef,
    ServingState,
    State,
)
from evm.control_panel.environment import build_environment_ref
from evm.control_panel.org_context import build_default_org_context
from evm.control_panel.readiness_evaluator import (
    ReadinessInputs,
    evaluate_artifact_readiness,
    runtime_path,
)
from evm.core.config import get_nested, load_config, resolve_path


DEFAULT_CONFIG_PATH = "configs/local_visa.toml"
DEFAULT_EFFICIENTNET_CONFIG_PATH = "configs/w7_efficientnet_real_test.toml"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def existing_artifact(name: str, path: Path, artifact_type: str = "json") -> ArtifactRef | None:
    if not path.exists():
        return None
    return ArtifactRef(
        name=name,
        uri=str(path),
        artifact_type=artifact_type,
        mime_type="application/json" if artifact_type == "json" else None,
    )


def status_from_exists(path: Path, pass_status: State = "pass", missing_status: State = "unknown") -> State:
    return pass_status if path.exists() else missing_status


def sanitize_cycle_part(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-")
    return cleaned or "unknown"


def metric_status(value: float, threshold: float | None) -> State:
    if threshold is None:
        return "unknown"
    return "pass" if value >= threshold else "blocked"


def metric_list(metrics: dict[str, Any], thresholds: dict[str, Any] | None = None) -> list[Metric]:
    thresholds = thresholds or {}
    result: list[Metric] = []
    for name in ("accuracy", "precision", "recall", "f1", "auroc"):
        value = metrics.get(name)
        if isinstance(value, int | float):
            threshold = thresholds.get(name)
            result.append(
                Metric(
                    name=name,
                    value=round(float(value), 6),
                    threshold=float(threshold) if isinstance(threshold, int | float) else None,
                    status=metric_status(
                        float(value),
                        float(threshold) if isinstance(threshold, int | float) else None,
                    ),
                )
            )
    return result


def candidate_metric_list(metrics: dict[str, Any], acceptance: dict[str, Any]) -> list[Metric]:
    threshold_map = {
        "accuracy": acceptance.get("promotion_min_accuracy"),
        "f1": acceptance.get("promotion_min_f1"),
        "auroc": acceptance.get("promotion_min_auroc"),
    }
    result: list[Metric] = []
    for name in (
        "accuracy",
        "precision",
        "recall",
        "f1",
        "auroc",
        "latency_p95_ms",
        "gpu_memory_peak_mb",
    ):
        value = metrics.get(name)
        if not isinstance(value, int | float):
            continue
        threshold = threshold_map.get(name)
        unit = "ms" if name.endswith("_ms") else "MB" if name.endswith("_mb") else None
        result.append(
            Metric(
                name=name,
                value=round(float(value), 6),
                unit=unit,
                threshold=float(threshold) if isinstance(threshold, int | float) else None,
                status=metric_status(
                    float(value),
                    float(threshold) if isinstance(threshold, int | float) else None,
                )
                if isinstance(threshold, int | float)
                else "pass",
            )
        )
    return result


def model_matrix_evidence(config: dict[str, Any], matrix_id: str) -> dict[str, Any]:
    resources = config.get("resources", {}) if isinstance(config.get("resources"), dict) else {}
    artifact_root = resources.get("artifact_root")
    if not artifact_root:
        return {}
    root = runtime_path(str(artifact_root))
    if not root.is_absolute():
        config_path = Path(str(config.get("_config_path", "")))
        root = config_path.parent.parent / root if config_path else root
    latest_path = root / "latest_model_matrix.json"
    matrix_path = root / matrix_id / "model_matrix.json"
    return read_json(latest_path) or read_json(matrix_path)


def latest_kubernetes_evidence(root: Path) -> Path | None:
    if not root.exists():
        return None
    candidates = [
        path
        for path in root.glob("w7-k8s-b7-*/evidence_index.json")
        if path.is_file()
    ]
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.parent.name)) if candidates else None


def split_counts(source_model: dict[str, Any]) -> dict[str, int]:
    mapping = {
        "train": source_model.get("training_records"),
        "validation": source_model.get("validation_records"),
        "test": source_model.get("test_records"),
    }
    return {key: int(value) for key, value in mapping.items() if isinstance(value, int)}


def load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fp:
        return tomllib.load(fp)


def build_model_matrix(config_path: Path, dataset_version: str) -> ModelExperimentMatrix:
    cfg = load_toml(config_path)
    matrix_cfg = cfg.get("model_matrix", {}) if isinstance(cfg.get("model_matrix"), dict) else {}
    acceptance = cfg.get("acceptance", {}) if isinstance(cfg.get("acceptance"), dict) else {}
    candidates_cfg = cfg.get("candidates", []) if isinstance(cfg.get("candidates"), list) else []
    evidence = model_matrix_evidence(cfg, str(matrix_cfg.get("matrix_id", "w7-efficientnet-real-test-matrix")))
    evidence_candidates = {
        str(item.get("candidate_id")): item
        for item in evidence.get("candidates", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }

    minimum_records = acceptance.get("min_total_records") or matrix_cfg.get("minimum_records")
    candidates: list[ModelCandidate] = []
    for item in candidates_cfg:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidate_id", "unknown-candidate"))
        architecture = str(item.get("architecture", "efficientnet-b0"))
        if architecture not in {"efficientnet-b0", "efficientnet-b7"}:
            architecture = "efficientnet-b0"
        conditions = {
            key: item.get(key)
            for key in (
                "input_size",
                "pretrained",
                "freeze_backbone",
                "optimizer",
                "learning_rate",
                "batch_size",
                "mixed_precision",
                "max_parallel_jobs",
            )
            if key in item
        }
        candidate_evidence = evidence_candidates.get(candidate_id, {})
        evidence_metrics = (
            candidate_evidence.get("metrics")
            if isinstance(candidate_evidence.get("metrics"), dict)
            else {}
        )
        promotion_blockers = candidate_evidence.get("promotion_blockers")
        if not isinstance(promotion_blockers, list):
            promotion_blockers = ["not_executed_yet"]
        candidates.append(
            ModelCandidate(
                candidate_id=candidate_id,
                framework="torch",
                architecture=architecture,  # type: ignore[arg-type]
                backbone=str(item.get("backbone", "")),
                status=str(candidate_evidence.get("status") or "queued"),  # type: ignore[arg-type]
                dataset_version=str(matrix_cfg.get("dataset_version") or dataset_version),
                run_uri=candidate_evidence.get("run_uri"),
                artifact_uri=candidate_evidence.get("artifact_uri"),
                resource_profile=str(item.get("resource_profile", "unknown")),
                conditions=conditions,
                metrics=candidate_metric_list(evidence_metrics, acceptance),
                promotion_blockers=[str(item) for item in promotion_blockers],
            )
        )

    matrix_status: State = str(evidence.get("status") or ("queued" if candidates else "blocked"))  # type: ignore[assignment]
    execution_mode = str(matrix_cfg.get("execution_mode", "parallel"))
    if execution_mode not in {"parallel", "sequential", "blocked"}:
        execution_mode = "parallel"
    return ModelExperimentMatrix(
        matrix_id=str(matrix_cfg.get("matrix_id", "w7-efficientnet-real-test-matrix")),
        status=matrix_status,
        execution_mode=execution_mode,  # type: ignore[arg-type]
        framework="torch",
        real_test_policy=RealTestPolicy(
            mock_allowed=bool(matrix_cfg.get("mock_allowed", False)),
            smoke_allowed=bool(matrix_cfg.get("smoke_allowed", False)),
            requires_real_dataset=bool(matrix_cfg.get("requires_real_dataset", True)),
            requires_real_training=bool(matrix_cfg.get("requires_real_training", True)),
            minimum_records=int(minimum_records) if isinstance(minimum_records, int) else None,
            dataset_version=str(matrix_cfg.get("dataset_version") or dataset_version),
            notes=(
                "W7 real-test evidence requires real VisA records and Torch training; "
                "mock adapters and smoke-only runs are not completion evidence."
            ),
        ),
        candidates=candidates,
    )


def path_from_config(config: dict[str, Any], key: str, default: str | Path) -> Path:
    return resolve_path(config, get_nested(config, key, default))


def build_latest_cycle(
    config_path: str | Path | None = None,
    efficientnet_config_path: str | Path | None = None,
) -> CycleRun:
    config_path = config_path or os.getenv("EVM_CONTROL_PANEL_CONFIG", DEFAULT_CONFIG_PATH)
    config = load_config(config_path)
    project_root = Path(str(config["_project_root"]))
    artifacts_root = resolve_path(config, get_nested(config, "paths.artifacts_root", "artifacts"))
    registry_root = resolve_path(config, get_nested(config, "paths.registry_root", "artifacts/registry"))
    model_name = str(get_nested(config, "pipelines.model_registry.model_name", "vision-baseline"))
    registry_path = Path(os.getenv("MODEL_REGISTRY_PATH", str(registry_root / model_name / "latest.json")))
    if not registry_path.is_absolute():
        registry_path = project_root / registry_path

    dataset_metadata_path = path_from_config(
        config,
        "pipelines.data_validation.dataset_metadata",
        "data/validated/visa/dataset_version.json",
    )
    quality_report_path = path_from_config(
        config,
        "pipelines.image_quality.report_path",
        "data/validated/visa/mvi_quality_report.json",
    )
    curation_state_path = path_from_config(
        config,
        "pipelines.curation_workflow.state_path",
        "data/validated/visa/curation/curation_state.json",
    )
    lakehouse_probe_path = path_from_config(
        config,
        "pipelines.lakehouse_probe.probe_report",
        "artifacts/lakehouse/visa/lakehouse_probe.json",
    )
    lifecycle_dir = path_from_config(
        config,
        "pipelines.model_lifecycle.output_dir",
        artifacts_root / "lifecycle" / model_name,
    )
    lifecycle_dashboard_path = lifecycle_dir / "lifecycle_dashboard.json"
    drift_queue_path = lifecycle_dir / "drift_special_case_queue.json"

    registry = read_json(registry_path)
    source_model = registry.get("source_model") if isinstance(registry.get("source_model"), dict) else {}
    source_model = source_model if isinstance(source_model, dict) else {}
    dataset = read_json(dataset_metadata_path) or source_model.get("dataset") or {}
    dataset = dataset if isinstance(dataset, dict) else {}
    quality = read_json(quality_report_path)
    lifecycle = read_json(lifecycle_dashboard_path)
    drift_queue = read_json_list(drift_queue_path)
    lakehouse = read_json(lakehouse_probe_path)
    curation_state = read_json(curation_state_path)

    source_dataset = source_model.get("dataset") if isinstance(source_model.get("dataset"), dict) else {}
    dataset_version = str(dataset.get("dataset_version") or source_dataset.get("dataset_version", "") or "unknown")
    record_count = int(dataset.get("record_count") or source_model.get("records_seen") or 0)
    splits = split_counts(source_model)
    metrics = source_model.get("metrics") if isinstance(source_model.get("metrics"), dict) else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    lifecycle_policy = lifecycle.get("promotion_policy") if isinstance(lifecycle.get("promotion_policy"), dict) else {}
    thresholds = (
        lifecycle_policy.get("thresholds")
        if isinstance(lifecycle_policy.get("thresholds"), dict)
        else source_model.get("lifecycle", {}).get("thresholds", {})
    )
    thresholds = thresholds if isinstance(thresholds, dict) else {}
    blockers = lifecycle.get("blockers") if isinstance(lifecycle.get("blockers"), list) else []
    if not blockers and isinstance(source_model.get("lifecycle"), dict):
        blockers = source_model["lifecycle"].get("blockers", [])
    blockers = [str(item) for item in blockers] if isinstance(blockers, list) else []

    quality_status: State = "pass" if quality.get("status") == "pass" else status_from_exists(quality_report_path)
    registry_status: State = "pass" if registry else "blocked"
    promotion_status: State = "blocked" if blockers else ("pass" if registry else "unknown")
    model_stage = str(registry.get("stage") or "unknown")
    model_version = str(registry.get("version") or "")
    model_type = str(source_model.get("model_type") or "unknown")

    efficientnet_path = Path(efficientnet_config_path or DEFAULT_EFFICIENTNET_CONFIG_PATH)
    if not efficientnet_path.is_absolute():
        efficientnet_path = project_root / efficientnet_path
    model_matrix = build_model_matrix(efficientnet_path, dataset_version)
    efficientnet_config = load_toml(efficientnet_path)
    matrix_config = (
        efficientnet_config.get("model_matrix", {})
        if isinstance(efficientnet_config.get("model_matrix"), dict)
        else {}
    )
    matrix_resources = (
        efficientnet_config.get("resources", {})
        if isinstance(efficientnet_config.get("resources"), dict)
        else {}
    )
    matrix_inputs = (
        efficientnet_config.get("inputs", {})
        if isinstance(efficientnet_config.get("inputs"), dict)
        else {}
    )
    matrix_acceptance = (
        efficientnet_config.get("acceptance", {})
        if isinstance(efficientnet_config.get("acceptance"), dict)
        else {}
    )

    stage_artifacts = [
        artifact
        for artifact in [
            existing_artifact("dataset_metadata", dataset_metadata_path),
            existing_artifact("quality_report", quality_report_path),
            existing_artifact("curation_state", curation_state_path),
            existing_artifact("lakehouse_probe", lakehouse_probe_path),
            existing_artifact("model_registry_latest", registry_path),
            existing_artifact("lifecycle_dashboard", lifecycle_dashboard_path),
            existing_artifact("drift_special_case_queue", drift_queue_path),
        ]
        if artifact is not None
    ]

    def stage(
        stage_id: str,
        name: str,
        status: State,
        artifact: ArtifactRef | None,
        metric: Metric | None = None,
        resource: ResourceRef | None = None,
    ) -> PipelineStage:
        return PipelineStage(
            stage_id=stage_id,
            name=name,
            status=status,
            started_at=dataset.get("created_at") or registry.get("registered_at") or utc_now(),
            finished_at=str(registry.get("registered_at")) if status in {"pass", "blocked"} and registry.get("registered_at") else None,
            current_step=None if status in {"pass", "blocked"} else "waiting_for_evidence",
            progress=1.0 if status == "pass" else 0.0,
            failure_reason=None if status == "pass" else "missing_or_blocked_evidence",
            artifacts=[artifact] if artifact is not None else [],
            metrics=[metric] if metric is not None else [],
            resources=[resource] if resource is not None else [],
        )

    stages = [
        stage(
            "data-validation",
            "Data Validation",
            "pass" if dataset_metadata_path.exists() else "blocked",
            existing_artifact("dataset_metadata", dataset_metadata_path),
            Metric(name="record_count", value=float(record_count), unit="records", status="pass" if record_count else "blocked"),
            ResourceRef(namespace="evm-pipelines", kind="Job", name="data_validation"),
        ),
        stage(
            "image-quality",
            "Image Quality",
            quality_status,
            existing_artifact("quality_report", quality_report_path),
            Metric(name="blocking_count", value=float(get_nested(quality, "gate_decision.blocking_count", 0) or 0), status=quality_status),
            ResourceRef(namespace="evm-pipelines", kind="Job", name="image_quality"),
        ),
        stage(
            "curation-workflow",
            "Curation Workflow",
            "pass" if curation_state_path.exists() else "unknown",
            existing_artifact("curation_state", curation_state_path),
            Metric(name="hitl_queue_count", value=float(curation_state.get("hitl_queue_count", 0) or 0), status="pass" if curation_state else "unknown"),
            ResourceRef(namespace="evm-pipelines", kind="Job", name="evm-curation-workflow"),
        ),
        stage(
            "lakehouse-probe",
            "Lakehouse Probe",
            "pass" if lakehouse.get("status") == "pass" else status_from_exists(lakehouse_probe_path),
            existing_artifact("lakehouse_probe", lakehouse_probe_path),
            Metric(name="row_count", value=float(lakehouse.get("row_count", record_count) or 0), status="pass" if lakehouse else "unknown"),
            ResourceRef(namespace="evm-pipelines", kind="Job", name="evm-lakehouse-probe"),
        ),
        stage(
            "model-registry",
            "Model Registry",
            registry_status,
            existing_artifact("model_registry_latest", registry_path),
            Metric(name="model_version", value=float(model_version) if model_version.isdigit() else 0.0, status=registry_status),
            ResourceRef(namespace="evm-platform", kind="Deployment", name="evm-mlflow"),
        ),
        stage(
            "model-lifecycle",
            "Model Lifecycle",
            promotion_status,
            existing_artifact("lifecycle_dashboard", lifecycle_dashboard_path),
            Metric(name="blocker_count", value=float(len(blockers)), status=promotion_status),
            ResourceRef(namespace="evm-platform", kind="Deployment", name="evm-api"),
        ),
        stage(
            "efficientnet-real-test",
            "EfficientNet Real Test Matrix",
            model_matrix.status,
            None,
            Metric(name="candidate_count", value=float(len(model_matrix.candidates)), status=model_matrix.status),
            ResourceRef(namespace="evm-pipelines", kind="Job", name="evm-efficientnet-training"),
        ),
    ]

    metrics_list = metric_list(metrics, thresholds)
    org_context = build_default_org_context()
    contract_path = project_root / "domain_packs/manufacturing_visual_inspection/data_contract.toml"
    selected_candidate_id = str(
        matrix_config.get("selected_candidate_id", "effnet-b7-img600-finetune-adamw")
    )
    matrix_id = str(matrix_config.get("matrix_id", "w7-efficientnet-real-test-matrix"))
    matrix_artifact_root = runtime_path(
        str(matrix_resources.get("artifact_root", artifacts_root / "w7" / "efficientnet"))
    )
    matrix_dir = matrix_artifact_root / matrix_id
    candidate_dir = matrix_dir / selected_candidate_id
    source_shard_index_value = str(matrix_inputs.get("shard_index", "")).strip()
    source_shard_index_path = (
        Path(source_shard_index_value)
        if source_shard_index_value
        else matrix_dir / "__missing_source_shard_index__.json"
    )
    readiness_report_path = artifacts_root / "w7" / "readiness" / "latest_readiness_evaluation.json"
    readiness_result = evaluate_artifact_readiness(
        ReadinessInputs(
            contract_path=contract_path,
            dataset_metadata_path=dataset_metadata_path,
            quality_report_path=quality_report_path,
            source_shard_index_path=source_shard_index_path,
            split_manifest_path=matrix_dir / "split_manifest.json",
            lineage_path=candidate_dir / "lineage.json",
            candidate_summary_path=candidate_dir / "candidate_summary.json",
            model_card_path=candidate_dir / "model_card.md",
            registry_path=registry_path,
            real_test_validation_path=(
                artifacts_root
                / "w7"
                / "real_test_evidence"
                / "evm-238-b-real-test-evidence-report.json"
            ),
            kubernetes_evidence_path=latest_kubernetes_evidence(
                artifacts_root / "w7" / "kubernetes_b7"
            ),
            mlflow_tracking_uri=os.getenv(
                "MLFLOW_TRACKING_URI",
                str(matrix_inputs.get("mlflow_tracking_uri", "http://localhost:5000")),
            ),
            candidate_id=selected_candidate_id,
            dataset_version=str(matrix_config.get("dataset_version", dataset_version)),
            expected_record_count=int(
                matrix_acceptance.get("min_total_records")
                or matrix_config.get("minimum_records")
                or 0
            ),
            expected_source_digest=str(matrix_inputs.get("shard_index_sha256", "")),
            metric_thresholds={
                name: float(matrix_acceptance[key])
                for name, key in {
                    "accuracy": "promotion_min_accuracy",
                    "f1": "promotion_min_f1",
                    "auroc": "promotion_min_auroc",
                }.items()
                if isinstance(matrix_acceptance.get(key), int | float)
            },
            report_uri=str(readiness_report_path),
        ),
        org_context,
    )
    data_readiness = readiness_result.data_pipeline
    experiment_readiness = readiness_result.experiment_pipeline
    stages.append(
        stage(
            "artifact-readiness",
            "Artifact Readiness",
            readiness_result.evaluation.status,
            existing_artifact("readiness_evaluation", readiness_report_path),
            Metric(
                name="blocker_count",
                value=float(len(readiness_result.evaluation.blockers)),
                status=readiness_result.evaluation.status,
            ),
            ResourceRef(namespace="evm-platform", kind="Deployment", name="evm-api"),
        )
    )
    environment_ref = build_environment_ref(blockers, os.getenv("GIT_COMMIT", ""))

    drift_state = build_drift_state(
        drift_queue=drift_queue,
        drift_queue_path=drift_queue_path,
        dataset_version=dataset_version,
        promotion_blockers=blockers,
    )
    cdct_gate = build_cdct_gate(
        promotion_blockers=blockers,
        drift=drift_state,
        quality_status=quality_status,
        pipeline_run_uri="https://github.com/ruma0236/ML_ServeAPI/actions",
        gate_report_uri=str(lifecycle_dashboard_path) if lifecycle_dashboard_path.exists() else None,
    )
    cycle = CycleRun(
        cycle_id=(
            f"cycle-w7-{sanitize_cycle_part(dataset_version)}-"
            f"{sanitize_cycle_part(model_name)}-v{sanitize_cycle_part(model_version)}"
        ),
        status="running" if model_matrix.status == "queued" else promotion_status,
        started_at=str(dataset.get("created_at") or registry.get("registered_at") or utc_now()),
        finished_at=None,
        owner_issue="EVM-224",
        tenant=org_context,
        environment=environment_ref,
        airflow=AirflowRef(
            mode="external-compose",
            control_mode="rest-api",
            dag_id="enterprise_vision_mlops_daily",
            dag_run_id="latest-known-local-cycle",
            contract_config_map="evm-airflow-control-contract",
            connection_status="unknown",
            url="http://localhost:8080",
        ),
        mlflow=MLflowRef(
            experiment_id=str(get_nested(config, "mlflow.experiment_name", "")),
            run_id=str(source_model.get("trace", {}).get("pipeline_run_id", "")) if isinstance(source_model.get("trace"), dict) else "",
            model_name=model_name,
            model_version=model_version,
            url=str(get_nested(config, "mlflow.tracking_uri", "http://localhost:5000")),
        ),
        data_pipeline=data_readiness,
        experiment_pipeline=experiment_readiness,
        readiness_evaluation=readiness_result.evaluation,
        dataset=DatasetVersion(
            dataset_id=str(dataset.get("dataset_name") or "visa-open-data"),
            version=dataset_version,
            domain_pack="manufacturing_visual_inspection",
            record_count=record_count,
            split=splits,
            storage_uri=str(dataset.get("validated_parquet_uri") or dataset.get("validated_parquet") or ""),
            schema_valid_rate=1.0 if dataset_metadata_path.exists() else None,
            quality_status=quality_status,
        ),
        model=ModelVersion(
            model_name=model_name,
            version=model_version,
            stage=model_stage,
            model_type=model_type,
            registry_uri=str(registry_path),
            source_run_id=str(source_model.get("trace", {}).get("pipeline_run_id", "")) if isinstance(source_model.get("trace"), dict) else "",
            dataset_version=dataset_version,
        ),
        model_matrix=model_matrix,
        metrics=metrics_list,
        promotion_gate=PromotionGate(
            decision=str(registry.get("promotion_decision") or lifecycle_policy.get("decision") or "unknown"),
            status=promotion_status,
            blockers=blockers,
            thresholds={key: float(value) for key, value in thresholds.items() if isinstance(value, int | float)},
        ),
        drift=drift_state,
        cdct_gate=cdct_gate,
        serving=ServingState(
            status="pass" if registry else "blocked",
            endpoint=str(get_nested(config, "serving.api_url", "http://localhost:8000")),
            model_loaded=bool(registry),
            model_version=model_version,
            placeholder=False if registry else None,
            p95_latency_ms=None,
            healthy_targets=2,
        ),
        stages=stages,
        resources=[
            ResourceRef(namespace="evm-platform", kind="Deployment", name="evm-api"),
            ResourceRef(namespace="evm-platform", kind="Deployment", name="evm-mlflow"),
            ResourceRef(namespace="evm-platform", kind="Deployment", name="evm-minio"),
            ResourceRef(namespace="evm-pipelines", kind="Job", name="evm-curation-workflow"),
            ResourceRef(namespace="evm-pipelines", kind="Job", name="evm-lakehouse-probe"),
        ],
        artifacts=stage_artifacts,
    )
    return cycle
