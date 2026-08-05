from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import uuid4

from evm.core.mlflow_client import MlflowRestClient


class ModelRuntimeError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ModelRuntimeError(f"manifest_missing:{path}")
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ModelRuntimeError("manifest_record_invalid")
                records.append(payload)
    if not records:
        raise ModelRuntimeError("manifest_empty")
    return records


def split_records(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result = {"train": [], "validation": [], "test": []}
    for record in records:
        split = str(record.get("split") or "")
        if split not in result:
            raise ModelRuntimeError(f"manifest_split_invalid:{split}")
        result[split].append(record)
    return result


def file_uri_path(value: str) -> Path:
    normalized = value.replace("\\", "/")
    if normalized.lower().startswith("file:///"):
        normalized = normalized[8:]
    return Path(normalized)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def payload_sha256(payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def set_reproducible_seed(seed: int) -> None:
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    try:
        import numpy

        numpy.random.seed(seed)
    except ImportError:
        pass
    import torch

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(False)


def runtime_inventory() -> dict[str, Any]:
    import accelerate
    import bitsandbytes
    import peft
    import torch
    import transformers

    if not torch.cuda.is_available():
        raise ModelRuntimeError("cuda_unavailable")
    properties = torch.cuda.get_device_properties(0)
    return {
        "python": os.sys.version.split()[0],
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "peft": peft.__version__,
        "accelerate": accelerate.__version__,
        "bitsandbytes": bitsandbytes.__version__,
        "cuda_runtime": torch.version.cuda,
        "cuda_available": True,
        "gpu_name": properties.name,
        "gpu_total_memory_mib": round(properties.total_memory / 1048576, 3),
        "gpu_capability": ".".join(str(item) for item in torch.cuda.get_device_capability(0)),
    }


def nvidia_smi_snapshot() -> dict[str, Any]:
    command = [
        "nvidia-smi",
        "--query-gpu=uuid,name,memory.total,memory.used,utilization.gpu,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    if completed.returncode != 0:
        return {"status": "unavailable", "error": completed.stderr.strip()}
    values = [item.strip() for item in completed.stdout.strip().split(",")]
    if len(values) != 6:
        return {"status": "invalid", "raw": completed.stdout.strip()}
    return {
        "status": "pass",
        "gpu_uuid": values[0],
        "gpu_name": values[1],
        "memory_total_mib": float(values[2]),
        "memory_used_mib": float(values[3]),
        "utilization_percent": float(values[4]),
        "temperature_c": float(values[5]),
        "observed_at": utc_now(),
    }


def parse_choice_index(output: str, choice_count: int) -> int | None:
    candidates = re.findall(r"(?<!\d)(\d+)(?!\d)", output)
    for candidate in reversed(candidates):
        value = int(candidate)
        if 0 <= value < choice_count:
            return value
    return None


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
    return round(float(ordered[index]), 6)


def metric_summary(results: list[dict[str, Any]]) -> dict[str, float | int]:
    latencies = [float(item["latency_seconds"]) for item in results]
    parsed = [item for item in results if item.get("predicted_index") is not None]
    correct = [item for item in parsed if item.get("correct") is True]
    total = len(results)
    return {
        "record_count": total,
        "parsed_count": len(parsed),
        "correct_count": len(correct),
        "parse_rate": round(len(parsed) / max(total, 1), 6),
        "accuracy": round(len(correct) / max(total, 1), 6),
        "mean_latency_seconds": round(mean(latencies), 6) if latencies else 0.0,
        "p95_latency_seconds": p95(latencies),
    }


def log_mlflow_evidence(
    *,
    tracking_uri: str,
    experiment_name: str,
    run_name: str,
    params: dict[str, Any],
    metrics: dict[str, Any],
    tags: dict[str, str],
    artifact_paths: list[Path] | None = None,
) -> str:
    client = MlflowRestClient(tracking_uri)
    if not client.health():
        raise ModelRuntimeError("mlflow_health_failed")
    experiment_id = client.get_or_create_experiment(experiment_name)
    if not experiment_id:
        raise ModelRuntimeError("mlflow_experiment_missing")
    run_id = client.create_run(experiment_id, run_name, tags=tags)
    if not run_id:
        raise ModelRuntimeError("mlflow_run_missing")
    failures: list[str] = []
    for key, value in sorted(params.items()):
        if not client.log_param(run_id, key, value):
            failures.append(f"param:{key}")
    for key, value in sorted(metrics.items()):
        if isinstance(value, int | float) and not client.log_metric(run_id, key, float(value)):
            failures.append(f"metric:{key}")
    if artifact_paths:
        import mlflow
        from mlflow.tracking import MlflowClient

        # Artifact repositories resolve through MLflow's process-wide tracking URI.
        # Keep it aligned with the explicit metadata client used above.
        mlflow.set_tracking_uri(tracking_uri)
        artifact_client = MlflowClient(tracking_uri=tracking_uri)
        for path in artifact_paths:
            if not path.is_file():
                failures.append(f"artifact_missing:{path.name}")
                continue
            try:
                artifact_client.log_artifact(run_id, str(path), artifact_path="evidence")
            except Exception as exc:  # MLflow clients expose backend-specific exceptions.
                failures.append(
                    f"artifact:{path.name}:{type(exc).__name__}:{str(exc)[:240]}"
                )
    if failures:
        client.terminate_run(run_id, status="KILLED")
        raise ModelRuntimeError(f"mlflow_write_failed:{','.join(failures)}")
    if not client.terminate_run(run_id):
        raise ModelRuntimeError("mlflow_terminate_failed")
    return run_id
