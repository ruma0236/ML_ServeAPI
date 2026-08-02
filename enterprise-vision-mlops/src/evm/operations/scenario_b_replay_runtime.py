from __future__ import annotations

import json
import os
import time
import tomllib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import requests

from evm.operations.failure_evidence import sha256_file
from evm.operations.failure_scenarios import atomic_write_json
from evm.operations.scenario_b_canary import (
    CanaryPolicy,
    ControlledReplayResult,
    InferenceObservation,
    ModelIdentity,
    QualityMetrics,
    ReplayRequest,
    build_assignment_routes,
    run_controlled_replay,
    write_controlled_replay_evidence,
)


@dataclass(frozen=True)
class ReplayRecord:
    request: ReplayRequest
    challenger_image_path: Path


def load_runtime_config(path: Path) -> tuple[CanaryPolicy, ModelIdentity, dict[str, Any]]:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    return (
        CanaryPolicy.model_validate(payload["policy"]),
        ModelIdentity.model_validate(payload["stable"]),
        dict(payload["replay"]),
    )


def source_image_uri(relative_path: str, host_data_root: str) -> str:
    normalized = PurePosixPath(relative_path.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError("unsafe_replay_relative_path")
    root = host_data_root.rstrip("/")
    return f"file:///{root}/data/raw/industrial/visa/{normalized.as_posix()}"


def load_replay_records(
    manifest_path: Path,
    *,
    count: int,
    host_data_root: str,
    verify_content: bool,
) -> list[ReplayRecord]:
    records: list[ReplayRecord] = []
    with manifest_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if len(records) >= count:
                break
            payload = json.loads(line)
            metadata = payload.get("metadata") or {}
            relative_path = str(metadata.get("relative_path") or "")
            request_id = str(payload.get("ct_record_id") or payload.get("id") or "")
            content_digest = str(payload.get("content_sha256") or "").lower()
            image_path = Path(str(payload.get("image_path") or ""))
            if not request_id or not relative_path or not image_path.is_absolute():
                raise ValueError("replay_manifest_record_incomplete")
            if verify_content:
                if not image_path.is_file():
                    raise FileNotFoundError(f"replay image is missing: {image_path}")
                if sha256_file(image_path) != content_digest:
                    raise ValueError(f"replay_content_digest_mismatch:{request_id}")
            records.append(
                ReplayRecord(
                    request=ReplayRequest(
                        request_id=request_id,
                        content_digest=content_digest,
                        image_uri=source_image_uri(relative_path, host_data_root),
                        expected_label=str(payload.get("label") or payload.get("label_type") or ""),
                    ),
                    challenger_image_path=image_path,
                )
            )
    if len(records) != count:
        raise ValueError(f"replay_manifest_count_mismatch:expected={count},actual={len(records)}")
    if len({record.request.request_id for record in records}) != count:
        raise ValueError("replay_manifest_duplicate_request_id")
    return records


def load_candidate_identity(
    *,
    candidate_summary_path: Path,
    model_path: Path,
    image_digest: str,
) -> tuple[ModelIdentity, QualityMetrics, dict[str, Any]]:
    summary = json.loads(candidate_summary_path.read_text(encoding="utf-8"))
    observed_model_digest = sha256_file(model_path)
    expected_model_digest = str(summary.get("model_sha256") or "").lower()
    if not expected_model_digest or observed_model_digest != expected_model_digest:
        raise ValueError("candidate_model_digest_mismatch")
    identity = ModelIdentity(
        candidate_id=str(summary.get("candidate_id") or ""),
        architecture=str(summary.get("architecture") or ""),
        dataset_version=str(summary.get("dataset_version") or ""),
        model_digest=observed_model_digest,
        image_digest=image_digest,
    )
    metrics = summary["metrics"]
    return (
        identity,
        QualityMetrics(
            accuracy=float(metrics["accuracy"]),
            f1=float(metrics["f1"]),
            auroc=float(metrics["auroc"]),
        ),
        summary,
    )


def fetch_stable_runtime(
    *,
    readiness_url: str,
    prometheus_targets_url: str,
    prometheus_job: str,
    prometheus_instance: str,
    timeout_seconds: float = 10,
) -> dict[str, Any]:
    readiness = requests.get(readiness_url, timeout=timeout_seconds)
    readiness.raise_for_status()
    readiness_payload = readiness.json()
    targets_response = requests.get(prometheus_targets_url, timeout=timeout_seconds)
    targets_response.raise_for_status()
    targets = targets_response.json().get("data", {}).get("activeTargets", [])
    matches = [
        target
        for target in targets
        if (target.get("labels") or {}).get("job") == prometheus_job
        and (target.get("labels") or {}).get("instance") == prometheus_instance
    ]
    if len(matches) != 1:
        raise ValueError(f"prometheus_target_cardinality_failed:{len(matches)}")
    return {
        "readiness": readiness_payload,
        "prometheus": {
            "job": prometheus_job,
            "instance": prometheus_instance,
            "health": matches[0].get("health"),
            "last_error": matches[0].get("lastError"),
        },
    }


def collect_stable_observations(
    records: list[ReplayRecord],
    *,
    predict_url: str,
    expected: ModelIdentity,
    timeout_seconds: float = 30,
) -> list[InferenceObservation]:
    session = requests.Session()
    observations: list[InferenceObservation] = []
    for record in records:
        started = time.perf_counter()
        try:
            response = session.post(
                predict_url,
                json={"image_uri": record.request.image_uri},
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            observations.append(
                InferenceObservation(
                    request_id=record.request.request_id,
                    model_digest=str(payload.get("model_sha256") or ""),
                    latency_ms=float(payload.get("latency_ms") or 0),
                    succeeded=True,
                    prediction=str(payload.get("prediction") or ""),
                    confidence=float(payload.get("confidence") or 0),
                )
            )
        except Exception as exc:
            observations.append(
                InferenceObservation(
                    request_id=record.request.request_id,
                    model_digest=expected.model_digest,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    succeeded=False,
                    failure_code=f"stable_http_error:{type(exc).__name__}",
                )
            )
    return observations


def collect_cuda_observations(
    records: list[ReplayRecord],
    *,
    candidate: ModelIdentity,
    model_path: Path,
    warmup_requests: int,
) -> tuple[list[InferenceObservation], dict[str, Any]]:
    import torch
    import torchvision
    from PIL import Image
    from torch import nn
    from torchvision import models, transforms

    if not torch.cuda.is_available():
        raise RuntimeError("scenario B real replay requires CUDA")
    device = torch.device("cuda")
    checkpoint = torch.load(model_path, map_location=device, weights_only=True)
    checkpoint_identity = {
        "candidate_id": str(checkpoint.get("candidate_id") or ""),
        "architecture": str(checkpoint.get("architecture") or ""),
        "dataset_version": str(checkpoint.get("dataset_version") or ""),
    }
    expected_identity = {
        "candidate_id": candidate.candidate_id,
        "architecture": candidate.architecture,
        "dataset_version": candidate.dataset_version,
    }
    if checkpoint_identity != expected_identity:
        raise ValueError("candidate_checkpoint_identity_mismatch")
    class_names = [str(item) for item in checkpoint.get("class_names") or []]
    input_size = int(checkpoint.get("input_size") or 0)
    if not class_names or input_size <= 0:
        raise ValueError("candidate_checkpoint_contract_incomplete")
    builder_name = {
        "efficientnet-b0": "efficientnet_b0",
        "efficientnet-b7": "efficientnet_b7",
    }.get(candidate.architecture)
    if not builder_name:
        raise ValueError("candidate_architecture_unsupported")
    model = getattr(models, builder_name)(weights=None)
    model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, len(class_names))
    model.load_state_dict(checkpoint["state_dict"])
    model.to(device).eval()
    transform = transforms.Compose(
        [
            transforms.Resize((input_size, input_size)),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225],
            ),
        ]
    )
    threshold = float(checkpoint.get("decision_threshold", 0.5))

    def infer(record: ReplayRecord) -> InferenceObservation:
        with Image.open(record.challenger_image_path) as image:
            tensor = transform(image.convert("RGB")).unsqueeze(0).to(device)
        torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.inference_mode():
            probabilities = torch.softmax(model(tensor), dim=1)[0]
        torch.cuda.synchronize()
        latency_ms = (time.perf_counter() - started) * 1000
        scores = {
            label: float(probabilities[index].detach().cpu().item())
            for index, label in enumerate(class_names)
        }
        anomaly_score = scores.get("anomaly", 0.0)
        prediction = "anomaly" if anomaly_score >= threshold else "normal"
        return InferenceObservation(
            request_id=record.request.request_id,
            model_digest=candidate.model_digest,
            latency_ms=latency_ms,
            succeeded=True,
            prediction=prediction,
            confidence=scores[prediction],
        )

    for record in records[:warmup_requests]:
        infer(record)
    torch.cuda.reset_peak_memory_stats(device)
    observations = [infer(record) for record in records]
    runtime = {
        "device": str(device),
        "cuda_device_name": torch.cuda.get_device_name(device),
        "torch_version": torch.__version__,
        "torchvision_version": torchvision.__version__,
        "model_digest": candidate.model_digest,
        "input_size": input_size,
        "warmup_requests": warmup_requests,
        "observed_requests": len(observations),
        "gpu_memory_peak_mb": torch.cuda.max_memory_allocated(device) / (1024 * 1024),
    }
    return observations, runtime


def inject_controlled_errors(
    observations: list[InferenceObservation],
    *,
    requests: list[ReplayRequest],
    policy: CanaryPolicy,
    count: int,
) -> tuple[list[InferenceObservation], dict[str, Any]]:
    if count < 0 or count > policy.challenger_requests:
        raise ValueError("controlled_error_count_out_of_bounds")
    routes = build_assignment_routes(requests, policy=policy)
    ranked_challenger_ids = sorted(
        (
            score,
            request_id,
        )
        for request_id, (route, score) in routes.items()
        if route == "challenger"
    )
    injected_ids = {request_id for _, request_id in ranked_challenger_ids[:count]}
    effective = [
        observation.model_copy(
            update={
                "succeeded": False,
                "prediction": None,
                "confidence": None,
                "failure_code": "controlled_transport_failure",
            }
        )
        if observation.request_id in injected_ids
        else observation
        for observation in observations
    ]
    return effective, {
        "schema_version": "evm.scenario_b_failure_injection.v1",
        "method": "effective_observation_overlay",
        "raw_observations_mutated": False,
        "production_endpoint_mutated": False,
        "failure_code": "controlled_transport_failure",
        "injected_count": count,
        "request_ids": sorted(injected_ids),
    }


def _write_jsonl(path: Path, values: list[InferenceObservation]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            for value in values:
                handle.write(json.dumps(value.model_dump(mode="json"), sort_keys=True))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def execute_real_replay(
    *,
    run_id: str,
    config_path: Path,
    candidate_summary_path: Path,
    model_path: Path,
    manifest_path: Path,
    evidence_root: Path,
    stable_readiness_url: str,
    stable_predict_url: str,
    prometheus_targets_url: str,
    prometheus_job: str,
    prometheus_instance: str,
    host_data_root: str,
    warmup_requests: int,
    inject_error_count: int,
) -> tuple[ControlledReplayResult, Path]:
    policy, stable, replay_config = load_runtime_config(config_path)
    challenger, quality, summary = load_candidate_identity(
        candidate_summary_path=candidate_summary_path,
        model_path=model_path,
        image_digest=stable.image_digest,
    )
    if sha256_file(manifest_path) != str(
        replay_config.get("manifest_sha256") or ""
    ):
        raise ValueError("replay_manifest_digest_mismatch")
    records = load_replay_records(
        manifest_path,
        count=policy.total_replay_requests,
        host_data_root=host_data_root,
        verify_content=True,
    )
    before = fetch_stable_runtime(
        readiness_url=stable_readiness_url,
        prometheus_targets_url=prometheus_targets_url,
        prometheus_job=prometheus_job,
        prometheus_instance=prometheus_instance,
    )
    if before["readiness"].get("model_sha256") != stable.model_digest:
        raise ValueError("stable_readiness_identity_mismatch")
    if before["prometheus"].get("health") != "up":
        raise ValueError("stable_prometheus_target_not_up")

    collected_at = datetime.now(timezone.utc)
    stable_observations = collect_stable_observations(
        records,
        predict_url=stable_predict_url,
        expected=stable,
    )
    raw_challenger, cuda_runtime = collect_cuda_observations(
        records,
        candidate=challenger,
        model_path=model_path,
        warmup_requests=warmup_requests,
    )
    effective_challenger, injection = inject_controlled_errors(
        raw_challenger,
        requests=[record.request for record in records],
        policy=policy,
        count=inject_error_count,
    )
    detection_started = time.monotonic()
    result = run_controlled_replay(
        run_id=run_id,
        policy=policy,
        stable=stable,
        challenger=challenger,
        requests=[record.request for record in records],
        stable_observations=stable_observations,
        challenger_observations=effective_challenger,
        challenger_quality=quality,
        started_at=collected_at,
        stop_seconds=0,
        rollback_seconds=0,
    )
    measured_decision_seconds = time.monotonic() - detection_started
    result = result.model_copy(
        update={
            "decision": result.decision.model_copy(
                update={"stop_seconds": measured_decision_seconds}
            ),
            "rollback": result.rollback.model_copy(
                update={"duration_seconds": measured_decision_seconds}
            ),
        }
    )
    after = fetch_stable_runtime(
        readiness_url=stable_readiness_url,
        prometheus_targets_url=prometheus_targets_url,
        prometheus_job=prometheus_job,
        prometheus_instance=prometheus_instance,
    )
    if after["readiness"].get("model_sha256") != stable.model_digest:
        raise ValueError("post_replay_stable_identity_mismatch")
    if after["prometheus"].get("health") != "up":
        raise ValueError("post_replay_prometheus_target_not_up")

    run_root = evidence_root / run_id
    stable_path = run_root / "stable-observations.jsonl"
    raw_path = run_root / "challenger-observations-raw.jsonl"
    effective_path = run_root / "challenger-observations-effective.jsonl"
    injection_path = run_root / "failure-injection.json"
    runtime_path = run_root / "runtime.json"
    summary_path = run_root / "candidate-summary-reference.json"
    _write_jsonl(stable_path, stable_observations)
    _write_jsonl(raw_path, raw_challenger)
    _write_jsonl(effective_path, effective_challenger)
    atomic_write_json(injection_path, injection)
    atomic_write_json(
        runtime_path,
        {
            "schema_version": "evm.scenario_b_runtime.v1",
            "collected_at": collected_at.isoformat(),
            "stable_before": before,
            "stable_after": after,
            "stable_identity_unchanged": before["readiness"].get("model_sha256")
            == after["readiness"].get("model_sha256")
            == stable.model_digest,
            "prometheus_recovered": after["prometheus"].get("health") == "up",
            "cuda": cuda_runtime,
            "decision_seconds": measured_decision_seconds,
            "production_mutated": False,
        },
    )
    atomic_write_json(summary_path, summary)
    index_path = write_controlled_replay_evidence(
        root=evidence_root,
        result=result,
        requests=[record.request for record in records],
        additional_artifacts={
            "stable_observations": stable_path,
            "challenger_observations_raw": raw_path,
            "challenger_observations_effective": effective_path,
            "failure_injection": injection_path,
            "runtime": runtime_path,
            "candidate_summary_reference": summary_path,
        },
        canonical_evidence_root=Path(str(replay_config["evidence_root"])),
    )
    return result, index_path
