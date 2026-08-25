from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import tempfile
import tomllib
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


CONFIG_SCHEMA_VERSION = "evm.s8_v4.x1_resume_testbed_config.v1"
EVIDENCE_SCHEMA_VERSION = "evm.s8_v4.x1_resume_testbed.v1"
MANIFEST_SCHEMA_VERSION = "evm.s8_v4.x1_resume_model_repository.v1"
CLAIM_CLASS = "preliminary_controlled_testbed"
CREDIT = "non_credit"
DEFAULT_CONFIG_RELATIVE_PATH = "configs/s8_v4_x1_resume_testbed_v1.toml"
EXPECTED_CONFIG_SHA256 = "7333ca51b4ad8dc84c370bd80a5c9050f1d29e2c5b46a326ab6b854e47f6b030"
EXPECTED_MODELS = (
    "higgs_logistic_regression",
    "higgs_gaussian_nb",
    "higgs_tiny_mlp",
    "criteo_dlrm_lite",
)
EXPECTED_REPOSITORY_ENTRY_PATHS = tuple(
    sorted(
        ["testbed-samples.json"]
        + [
            path
            for profile in ("off", "on")
            for model_id in EXPECTED_MODELS
            for path in (
                f"batch-{profile}/{model_id}/1/model.pt",
                f"batch-{profile}/{model_id}/config.pbtxt",
            )
        ]
    )
)
EXPECTED_PROMETHEUS_JOBS = (
    "evm-api",
    "evm-b0-production",
    "evm-otel-collector",
    "evm-task-queue-worker",
    "prometheus",
)
REQUIRED_SOURCE_BLOB_PATHS = (
    ".gitattributes",
    DEFAULT_CONFIG_RELATIVE_PATH,
    "docs/status/2026-08-25-x1-resume-testbed-v1-runbook.md",
    "src/evm/control_panel/scenario_workloads.py",
    "src/evm/scale_validation/x1_resume_testbed.py",
    "scripts/dev/prepare_s8_v4_x1_resume_testbed.py",
    "scripts/dev/run_s8_v4_x1_resume_testbed.py",
    "scripts/dev/validate_s8_v4_x1_resume_testbed.py",
)
GOVERNED_SOURCE_IDENTITIES = {
    "s3_registry": {
        "path": "artifacts/scale_validation/s3/capacity-registry.json",
        "sha256": "6666ef28ea681ee694525e2d5ee1f2e1fefe460f4cc730a6c64fb78fe6bed012",
        "bytes": 2360,
        "schema_version": "evm.s3_capacity_registry.v1",
        "dataset_identity_sha256": "eecb0f824e149c8e9062f216d834e8ccc519ea774c0f79392f23bb11f3f7550d",
        "split_manifest_sha256": "7058c9fd81e06465e64d8be98cfad065aefccea2063c0404775fcaf0d7c19e00",
    },
    "s3_replay": {
        "path": "artifacts/scale_validation/s3/higgs-uci-2014-seed-20260817-v1/splits/replay/features.npy",
        "sha256": "dde408a9a1df8a1cdd0e12ac6cbd0ef655ffa5049781b1d008a449c8b8fbc359",
        "bytes": 22400128,
    },
    "s3_logistic": {
        "path": "artifacts/scale_validation/s3/higgs-uci-2014-seed-20260817-v1/models/logistic.json",
        "sha256": "b4bbd5b76612b18b945b4441682688b759614d12748089e368d5c12b1614e18b",
        "bytes": 2006,
        "schema_version": "evm.s3_capacity_probe_artifact.v1",
    },
    "s3_probabilistic": {
        "path": "artifacts/scale_validation/s3/higgs-uci-2014-seed-20260817-v1/models/probabilistic.json",
        "sha256": "515db1565b4dcbbb7573a37349fa512318683ed3b8be93f588beffe9cca03ab5",
        "bytes": 2549,
        "schema_version": "evm.s3_capacity_probe_artifact.v1",
    },
    "s4_registry": {
        "path": "artifacts/scale_validation/s4/tiny-mlp-v1/registry.json",
        "sha256": "894cfe2f3d2dc7ff0887afe83ffb8e785a1c4df20ee0a81b4fe9a2d4d2aae7bb",
        "bytes": 2784,
        "schema_version": "evm.s4_gpu_batch_registry.v1",
        "model_identity_sha256": "983632acff7cb3a0652e8f9f235c930bf544ef1b45433897d4c30eb391ee1a5a",
        "preprocessing_sha256": "21592db055ccbb603bc10cf65dbda1b11c0361f1299ae306d1f8084c77bf9453",
    },
    "s4_artifact": {
        "path": "artifacts/scale_validation/s4/tiny-mlp-v1/tiny-mlp.pt",
        "sha256": "1ef8416783de782475af6aba8d78b5f323bf97c37f4d54f0b7e851e73d82c894",
        "bytes": 19361,
    },
    "s5_manifest": {
        "path": "datasets/criteo-click-logs/s5/governed/dataset-manifest.json",
        "sha256": "f8bf311584ffeff75197845940fc5699a7c9849aa3c2621157c6597397f5abc0",
        "bytes": 5722,
        "schema_version": "evm.s5_criteo_dataset_manifest.v1",
        "dataset_version": "criteo-click-logs-s5-v1",
        "source_revision": "e11d69ae913b16cdd7387706a2133def0fdc6ced",
        "first_shard_path": "shard-000.parquet",
        "first_shard_sha256": "d99460a3b55cf7ef4ea94c2b721ca2815b2c3ce477435973a4d0d90ce39df054",
        "first_shard_bytes": 56761828,
    },
}


class X1ResumeTestbedError(RuntimeError):
    pass


def require_default_config_path(path: Path, source_root: Path) -> Path:
    expected = (source_root / DEFAULT_CONFIG_RELATIVE_PATH).resolve()
    if path.resolve() != expected or not expected.is_file():
        raise X1ResumeTestbedError("x1_resume_default_config_required")
    return expected


def prometheus_baseline_state(snapshot: Mapping[str, Any], expected_jobs: Sequence[str]) -> str:
    jobs = snapshot.get("jobs")
    total = snapshot.get("total")
    up = snapshot.get("up")
    if (
        not isinstance(jobs, list)
        or any(not isinstance(job, str) for job in jobs)
        or type(total) is not int
        or type(up) is not int
    ):
        return "invalid_snapshot"
    expected = {str(job) for job in expected_jobs}
    if len(jobs) != len(expected) or set(jobs) != expected or total != len(expected):
        return "invalid_snapshot"
    if up == len(expected):
        return "ready"
    if up == len(expected) - 1:
        return "retryable_4_of_5"
    return "invalid_snapshot"


def prometheus_baseline_ready(snapshot: Mapping[str, Any], expected_jobs: Sequence[str]) -> bool:
    return prometheus_baseline_state(snapshot, expected_jobs) == "ready"


def wait_for_prometheus_baseline(
    health_check: Callable[[float], Mapping[str, Any]],
    expected_jobs: Sequence[str],
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    observed_at: Callable[[], str],
) -> tuple[dict[str, Any], float, list[dict[str, Any]], bool, str]:
    if timeout_seconds <= 0 or poll_interval_seconds <= 0:
        raise ValueError("prometheus restore timeout and poll interval must be positive")
    started = monotonic()
    deadline = started + timeout_seconds
    samples: list[dict[str, Any]] = []
    last_snapshot: dict[str, Any] = {}
    while True:
        probe_started = monotonic()
        remaining = deadline - probe_started
        if remaining <= 0:
            return last_snapshot, probe_started - started, samples, False, "timeout"
        timestamp = observed_at()
        try:
            last_snapshot = dict(health_check(remaining))
        except Exception as exc:
            probe_finished = monotonic()
            samples.append(
                {
                    "observed_at": timestamp,
                    "error": f"{type(exc).__name__}:{exc}",
                    "probe_budget_seconds": remaining,
                    "probe_finished_elapsed_seconds": probe_finished - started,
                    "probe_started_elapsed_seconds": probe_started - started,
                    "state": "probe_error",
                }
            )
            reason = "deadline_exceeded" if probe_finished > deadline else "probe_error"
            return last_snapshot, probe_finished - started, samples, False, reason
        probe_finished = monotonic()
        state = prometheus_baseline_state(last_snapshot, expected_jobs)
        samples.append(
            {
                "observed_at": timestamp,
                "probe_budget_seconds": remaining,
                "probe_finished_elapsed_seconds": probe_finished - started,
                "probe_started_elapsed_seconds": probe_started - started,
                "snapshot": last_snapshot,
                "state": state,
            }
        )
        if probe_finished > deadline:
            return last_snapshot, probe_finished - started, samples, False, "deadline_exceeded"
        if state == "ready":
            return last_snapshot, probe_finished - started, samples, True, "ready"
        if state != "retryable_4_of_5":
            return last_snapshot, probe_finished - started, samples, False, state
        remaining = deadline - probe_finished
        if remaining <= 0:
            return last_snapshot, probe_finished - started, samples, False, "timeout"
        sleep(min(poll_interval_seconds, remaining))


def _validate_prometheus_restore_evidence(
    final_checks: Mapping[str, Any], *, timeout_seconds: float
) -> None:
    samples = final_checks.get("prometheus_restore_samples")
    elapsed = final_checks.get("prometheus_restore_seconds")
    if (
        not isinstance(samples, list)
        or not samples
        or not isinstance(elapsed, (int, float))
        or isinstance(elapsed, bool)
        or not math.isfinite(float(elapsed))
        or float(elapsed) < 0
        or float(elapsed) > timeout_seconds
        or final_checks.get("prometheus_restore_ready") is not True
        or final_checks.get("prometheus_restore_terminal_reason") != "ready"
    ):
        raise X1ResumeTestbedError("x1_resume_private_prometheus_cleanup")

    previous_finished = 0.0
    states: list[str] = []
    for sample in samples:
        if not isinstance(sample, Mapping) or "error" in sample:
            raise X1ResumeTestbedError("x1_resume_private_prometheus_cleanup")
        snapshot = sample.get("snapshot")
        started = sample.get("probe_started_elapsed_seconds")
        finished = sample.get("probe_finished_elapsed_seconds")
        budget = sample.get("probe_budget_seconds")
        if not isinstance(snapshot, Mapping) or not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (started, finished, budget)
        ):
            raise X1ResumeTestbedError("x1_resume_private_prometheus_cleanup")
        started_value = float(started)
        finished_value = float(finished)
        budget_value = float(budget)
        state = prometheus_baseline_state(snapshot, EXPECTED_PROMETHEUS_JOBS)
        if (
            not str(sample.get("observed_at") or "")
            or not all(
                math.isfinite(value) for value in (started_value, finished_value, budget_value)
            )
            or started_value < previous_finished
            or finished_value < started_value
            or finished_value > timeout_seconds
            or budget_value <= 0
            or budget_value > timeout_seconds - started_value + 1e-9
            or sample.get("state") != state
        ):
            raise X1ResumeTestbedError("x1_resume_private_prometheus_cleanup")
        states.append(state)
        previous_finished = finished_value

    terminal_snapshot = dict(samples[-1].get("snapshot", {}))
    if (
        any(state != "retryable_4_of_5" for state in states[:-1])
        or states[-1] != "ready"
        or not math.isclose(float(elapsed), previous_finished, rel_tol=0.0, abs_tol=1e-9)
        or dict(final_checks.get("prometheus", {})) != terminal_snapshot
    ):
        raise X1ResumeTestbedError("x1_resume_private_prometheus_cleanup")


def _validate_b0_holder(raw: Any, *, label: str) -> dict[str, Any]:
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"uid", "image", "replicas"}
        or not isinstance(raw.get("uid"), str)
        or not raw.get("uid")
        or not isinstance(raw.get("image"), str)
        or not raw.get("image")
        or type(raw.get("replicas")) is not int
        or raw.get("replicas") != 1
    ):
        raise X1ResumeTestbedError(f"x1_resume_b0_holder:{label}")
    return dict(raw)


def _validate_b0_cuda(raw: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {"passed", "ready", "prediction"}:
        raise X1ResumeTestbedError(f"x1_resume_b0_cuda_schema:{label}")
    ready = raw.get("ready")
    prediction = raw.get("prediction")
    ready_fields = {
        "architecture",
        "candidate_id",
        "class_names",
        "cuda_available",
        "dataset_version",
        "decision_threshold",
        "device",
        "input_size",
        "model_loaded",
        "model_path",
        "model_sha256",
        "service",
        "status",
    }
    prediction_fields = {
        "candidate_id",
        "confidence",
        "dataset_version",
        "decision_threshold",
        "device",
        "image_uri",
        "latency_ms",
        "model_sha256",
        "prediction",
        "scores",
    }
    if (
        raw.get("passed") is not True
        or not isinstance(ready, Mapping)
        or set(ready) != ready_fields
        or not isinstance(prediction, Mapping)
        or set(prediction) != prediction_fields
    ):
        raise X1ResumeTestbedError(f"x1_resume_b0_cuda_schema:{label}")
    ready_strings = (
        "architecture",
        "candidate_id",
        "dataset_version",
        "model_path",
        "model_sha256",
        "service",
    )
    prediction_strings = (
        "candidate_id",
        "dataset_version",
        "image_uri",
        "model_sha256",
        "prediction",
    )
    numeric = (
        ready.get("decision_threshold"),
        prediction.get("decision_threshold"),
        prediction.get("confidence"),
        prediction.get("latency_ms"),
    )
    scores = prediction.get("scores")
    if (
        any(not isinstance(ready.get(key), str) or not ready.get(key) for key in ready_strings)
        or any(
            not isinstance(prediction.get(key), str) or not prediction.get(key)
            for key in prediction_strings
        )
        or not re.fullmatch(r"[0-9a-f]{64}", str(ready.get("model_sha256") or ""))
        or not re.fullmatch(r"[0-9a-f]{64}", str(prediction.get("model_sha256") or ""))
        or ready.get("status") != "ok"
        or ready.get("model_loaded") is not True
        or ready.get("cuda_available") is not True
        or ready.get("device") != "cuda"
        or prediction.get("device") != "cuda"
        or type(ready.get("input_size")) is not int
        or int(ready["input_size"]) <= 0
        or not isinstance(ready.get("class_names"), list)
        or not ready["class_names"]
        or any(not isinstance(value, str) or not value for value in ready["class_names"])
        or any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            for value in numeric
        )
        or not 0 <= float(prediction["confidence"]) <= 1
        or float(prediction["latency_ms"]) < 0
        or not isinstance(scores, Mapping)
        or not scores
        or any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or not 0 <= float(value) <= 1
            for value in scores.values()
        )
    ):
        raise X1ResumeTestbedError(f"x1_resume_b0_cuda_runtime:{label}")
    identity = {
        "architecture": ready["architecture"],
        "candidate_id": ready["candidate_id"],
        "class_names": list(ready["class_names"]),
        "dataset_version": ready["dataset_version"],
        "decision_threshold": float(ready["decision_threshold"]),
        "device": ready["device"],
        "input_size": ready["input_size"],
        "model_path": ready["model_path"],
        "model_sha256": ready["model_sha256"],
        "service": ready["service"],
        "prediction_candidate_id": prediction["candidate_id"],
        "prediction_dataset_version": prediction["dataset_version"],
        "prediction_decision_threshold": float(prediction["decision_threshold"]),
        "prediction_device": prediction["device"],
        "prediction_image_uri": prediction["image_uri"],
        "prediction_model_sha256": prediction["model_sha256"],
    }
    if (
        identity["candidate_id"] != identity["prediction_candidate_id"]
        or identity["dataset_version"] != identity["prediction_dataset_version"]
        or identity["decision_threshold"] != identity["prediction_decision_threshold"]
        or identity["model_sha256"] != identity["prediction_model_sha256"]
    ):
        raise X1ResumeTestbedError(f"x1_resume_b0_cuda_identity:{label}")
    return identity


def _validate_cleanup_evidence(
    payload: Mapping[str, Any],
    final_checks: Any,
    *,
    config: X1ResumeConfig,
) -> None:
    required_final_checks = {
        "holder",
        "b0_cuda",
        "queues",
        "gpu",
        "gpu_after_vram_wait",
        "vram_restore_seconds",
        "triton_processes",
        "containers",
        "ports",
        "gpu_lease",
        "prometheus",
        "prometheus_restore_seconds",
        "prometheus_restore_samples",
        "prometheus_restore_ready",
        "prometheus_restore_terminal_reason",
    }
    if not isinstance(final_checks, Mapping) or set(final_checks) != required_final_checks:
        raise X1ResumeTestbedError("x1_resume_private_cleanup_schema")
    environment = payload.get("environment")
    before = environment.get("b0_before") if isinstance(environment, Mapping) else None
    gpu_before = environment.get("gpu_before") if isinstance(environment, Mapping) else None
    if not isinstance(before, Mapping) or not isinstance(gpu_before, Mapping):
        raise X1ResumeTestbedError("x1_resume_private_cleanup_environment")

    holder = final_checks.get("holder")
    b0_cuda = final_checks.get("b0_cuda")
    queues = final_checks.get("queues")
    containers = final_checks.get("containers")
    ports = final_checks.get("ports")
    gpu_lease = final_checks.get("gpu_lease")
    gpu = final_checks.get("gpu")
    gpu_after = final_checks.get("gpu_after_vram_wait")
    before_holder = _validate_b0_holder(before.get("holder"), label="before")
    final_holder = _validate_b0_holder(holder, label="final")
    before_cuda_identity = _validate_b0_cuda(before.get("cuda"), label="before")
    final_cuda_identity = _validate_b0_cuda(b0_cuda, label="final")
    expected_container_names = [
        "evm-x1-resume-q0",
        *[f"evm-x1-resume-{profile}" for profile in config.batching],
    ]
    expected_ports = [config.http_port, config.grpc_port, config.metrics_port]
    if (
        final_holder != before_holder
        or final_cuda_identity != before_cuda_identity
        or not isinstance(queues, Mapping)
        or set(queues) != {"active", "leased", "outcome_unknown"}
        or any(type(queues.get(key)) is not int for key in queues)
        or not isinstance(containers, Mapping)
        or set(containers) != {"expected_names", "present_names"}
        or containers.get("expected_names") != expected_container_names
        or containers.get("present_names") != []
        or not isinstance(ports, Mapping)
        or set(ports) != {"expected_ports", "listening_ports"}
        or ports.get("expected_ports") != expected_ports
        or ports.get("listening_ports") != []
        or not isinstance(gpu_lease, Mapping)
        or set(gpu_lease) != {"active"}
        or gpu_lease.get("active") is not None
        or final_checks.get("triton_processes") != []
    ):
        raise X1ResumeTestbedError("x1_resume_private_cleanup_runtime")
    if not isinstance(gpu, Mapping) or not isinstance(gpu_after, Mapping):
        raise X1ResumeTestbedError("x1_resume_private_cleanup_gpu")
    numeric_gpu_fields = ("memory_used_mib", "memory_total_mib", "utilization_percent")
    if any(
        not isinstance(snapshot.get(field), (int, float))
        or isinstance(snapshot.get(field), bool)
        or not math.isfinite(float(snapshot[field]))
        for snapshot in (gpu_before, gpu, gpu_after)
        for field in numeric_gpu_fields
    ):
        raise X1ResumeTestbedError("x1_resume_private_cleanup_gpu")
    gpu_identity_restored = all(
        (snapshot.get("uuid"), snapshot.get("name"))
        == (config.expected_gpu_uuid, config.expected_gpu_name)
        for snapshot in (gpu_before, gpu, gpu_after)
    ) and all(
        math.isclose(
            float(snapshot["memory_total_mib"]),
            float(gpu_before["memory_total_mib"]),
            rel_tol=0.0,
            abs_tol=0.0,
        )
        for snapshot in (gpu, gpu_after)
    )
    vram_seconds = final_checks.get("vram_restore_seconds")
    tolerance_mib = max(256.0, float(gpu_before["memory_total_mib"]) * 0.05)
    gpu_vram_restored = (
        isinstance(vram_seconds, (int, float))
        and not isinstance(vram_seconds, bool)
        and math.isfinite(float(vram_seconds))
        and 0 <= float(vram_seconds) <= config.cleanup_timeout_seconds
        and abs(float(gpu_after["memory_used_mib"]) - float(gpu_before["memory_used_mib"]))
        <= tolerance_mib
    )
    _validate_prometheus_restore_evidence(
        final_checks, timeout_seconds=config.cleanup_timeout_seconds
    )
    prometheus_ready = prometheus_baseline_ready(
        dict(final_checks.get("prometheus", {})), EXPECTED_PROMETHEUS_JOBS
    )
    recomputed_cleanup = {
        "container_absent": True,
        "ports_absent": True,
        "gpu_lease_absent": True,
        "triton_gpu_process_residue": [],
        "b0_identity_restored": True,
        "b0_cuda_restored": True,
        "queue_active_zero": queues.get("active") == 0,
        "queue_leased_zero": queues.get("leased") == 0,
        "queue_outcome_unknown_zero": queues.get("outcome_unknown") == 0,
        "gpu_identity_restored": gpu_identity_restored,
        "gpu_vram_restored": gpu_vram_restored,
        "prometheus_5_of_5": prometheus_ready,
        "prometheus_exact_jobs_restored": prometheus_ready,
        "errors": [],
    }
    if payload.get("cleanup") != recomputed_cleanup or not all(
        value is True
        for key, value in recomputed_cleanup.items()
        if key not in {"triton_gpu_process_residue", "errors"}
    ):
        raise X1ResumeTestbedError("x1_resume_private_cleanup_recompute")


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("ascii")).hexdigest()


def canonical_file_sha256(value: Any) -> str:
    return hashlib.sha256((canonical(value) + "\n").encode("ascii")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(payload) + "\n", encoding="ascii", newline="\n")


def canonical_write_once(path: Path, payload: Any) -> None:
    """Publish canonical evidence atomically without replacing an existing target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    material = (canonical(payload) + "\n").encode("ascii")
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary_name = handle.name
            handle.write(material)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_name, path)
    except FileExistsError as exc:
        raise X1ResumeTestbedError(f"x1_resume_output_exists:{path}") from exc
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def canonical_output_identity(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def ensure_distinct_output_targets(*paths: Path) -> None:
    identities = [canonical_output_identity(path) for path in paths]
    if len(set(identities)) != len(identities):
        raise X1ResumeTestbedError("x1_resume_public_output_collision")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise X1ResumeTestbedError(f"x1_resume_json_duplicate_key:{key}")
        result[key] = value
    return result


def strict_json_loads(raw: bytes, *, label: str) -> Any:
    try:
        text = raw.decode("ascii")
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise X1ResumeTestbedError(f"x1_resume_json_invalid:{label}") from exc


def load_canonical_json(path: Path, *, label: str) -> Any:
    raw = path.read_bytes()
    payload = strict_json_loads(raw, label=label)
    if raw != (canonical(payload) + "\n").encode("ascii"):
        raise X1ResumeTestbedError(f"x1_resume_json_not_canonical:{label}")
    return payload


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    display_name: str
    dataset: str
    source_kind: str
    source_key: str
    input_width: int


@dataclass(frozen=True)
class CellSpec:
    cell_id: str
    repetitions: int
    model_mix: Mapping[str, float]
    batching: str
    client_lanes: int
    client_workers: int
    analytical_roles: tuple[str, ...]


@dataclass(frozen=True)
class X1ResumeConfig:
    path: Path
    sha256: str
    seed: int
    triton_image: str
    triton_image_digest: str
    expected_gpu_name: str
    expected_gpu_uuid: str
    http_port: int
    grpc_port: int
    metrics_port: int
    readiness_timeout_seconds: int
    cleanup_timeout_seconds: int
    input_paths: Mapping[str, str]
    sample_rows_per_dataset: int
    warmup_seconds: int
    measurement_seconds: int
    offered_rps: int
    minimum_offered_rate_attainment: float
    matched_load_relative_tolerance: float
    queue_depth_per_api: int
    request_timeout_seconds: int
    sample_gpu_interval_ms: int
    q0_activity_seconds_per_model: int
    q0_workers: int
    q0_request_batch_size: int
    models: tuple[ModelSpec, ...]
    cells: tuple[CellSpec, ...]
    batching: Mapping[str, Mapping[str, Any]]
    profiler: Mapping[str, Any]
    claim_boundary: str

    @classmethod
    def from_path(cls, path: Path) -> "X1ResumeConfig":
        raw = path.read_bytes()
        payload = tomllib.loads(raw.decode("utf-8"))
        runtime = dict(payload.get("runtime", {}))
        inputs = dict(payload.get("inputs", {}))
        load = dict(payload.get("load", {}))
        q0 = dict(payload.get("q0", {}))
        config = cls(
            path=path,
            sha256=hashlib.sha256(raw).hexdigest(),
            seed=int(payload.get("seed", 0)),
            triton_image=str(runtime.get("triton_image") or ""),
            triton_image_digest=str(runtime.get("triton_image_digest") or ""),
            expected_gpu_name=str(runtime.get("expected_gpu_name") or ""),
            expected_gpu_uuid=str(runtime.get("expected_gpu_uuid") or ""),
            http_port=int(runtime.get("http_port", 0)),
            grpc_port=int(runtime.get("grpc_port", 0)),
            metrics_port=int(runtime.get("metrics_port", 0)),
            readiness_timeout_seconds=int(runtime.get("readiness_timeout_seconds", 0)),
            cleanup_timeout_seconds=int(runtime.get("cleanup_timeout_seconds", 0)),
            input_paths={
                key: str(value) for key, value in inputs.items() if key != "sample_rows_per_dataset"
            },
            sample_rows_per_dataset=int(inputs.get("sample_rows_per_dataset", 0)),
            warmup_seconds=int(load.get("warmup_seconds", 0)),
            measurement_seconds=int(load.get("measurement_seconds", 0)),
            offered_rps=int(load.get("offered_requests_per_second", 0)),
            minimum_offered_rate_attainment=float(load.get("minimum_offered_rate_attainment", 0)),
            matched_load_relative_tolerance=float(load.get("matched_load_relative_tolerance", 0)),
            queue_depth_per_api=int(load.get("queue_depth_per_api", 0)),
            request_timeout_seconds=int(load.get("request_timeout_seconds", 0)),
            sample_gpu_interval_ms=int(load.get("sample_gpu_interval_ms", 0)),
            q0_activity_seconds_per_model=int(q0.get("activity_seconds_per_model", 0)),
            q0_workers=int(q0.get("workers", 0)),
            q0_request_batch_size=int(q0.get("request_batch_size", 0)),
            models=tuple(ModelSpec(**item) for item in payload.get("models", [])),
            cells=tuple(
                CellSpec(
                    cell_id=str(item.get("cell_id") or ""),
                    repetitions=int(item.get("repetitions", 0)),
                    model_mix={
                        str(key): float(value)
                        for key, value in dict(item.get("model_mix", {})).items()
                    },
                    batching=str(item.get("batching") or ""),
                    client_lanes=int(item.get("client_lanes", 0)),
                    client_workers=int(item.get("client_workers", 0)),
                    analytical_roles=tuple(
                        str(value) for value in item.get("analytical_roles", [])
                    ),
                )
                for item in payload.get("cells", [])
            ),
            batching={key: dict(value) for key, value in dict(payload.get("batching", {})).items()},
            profiler=dict(payload.get("profiler", {})),
            claim_boundary=str(dict(payload.get("claim", {})).get("boundary") or ""),
        )
        config.validate(payload)
        return config

    def validate(self, raw: Mapping[str, Any] | None = None) -> None:
        if raw is not None and raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
            raise X1ResumeTestbedError("x1_resume_config_schema")
        if raw is not None and (raw.get("claim_class"), raw.get("credit")) != (CLAIM_CLASS, CREDIT):
            raise X1ResumeTestbedError("x1_resume_config_claim_class")
        if self.sha256 != EXPECTED_CONFIG_SHA256:
            raise X1ResumeTestbedError("x1_resume_config_digest")
        expected_models = (
            (
                "higgs_logistic_regression",
                "HIGGS LogisticRegression",
                "HIGGS",
                "s3_json",
                "logistic",
                28,
            ),
            ("higgs_gaussian_nb", "HIGGS GaussianNB", "HIGGS", "s3_json", "probabilistic", 28),
            ("higgs_tiny_mlp", "HIGGS TinyMLP", "HIGGS", "s4_checkpoint", "tiny-mlp", 28),
            (
                "criteo_dlrm_lite",
                "Criteo DLRM-lite",
                "Criteo Display Advertising Challenge",
                "s5_governed_sample_seeded_test_model",
                "dlrm-lite",
                39,
            ),
        )
        observed_models = tuple(
            (
                item.model_id,
                item.display_name,
                item.dataset,
                item.source_kind,
                item.source_key,
                item.input_width,
            )
            for item in self.models
        )
        if observed_models != expected_models:
            raise X1ResumeTestbedError("x1_resume_model_contract")
        balanced = {model_id: 0.25 for model_id in EXPECTED_MODELS}
        expected_cells = (
            ("solo-logistic", 1, {EXPECTED_MODELS[0]: 1.0}, "off", 1, 1, ("solo",)),
            ("solo-gaussian-nb", 1, {EXPECTED_MODELS[1]: 1.0}, "off", 1, 1, ("solo",)),
            ("solo-tiny-mlp", 1, {EXPECTED_MODELS[2]: 1.0}, "off", 1, 1, ("solo",)),
            ("solo-dlrm-lite", 1, {EXPECTED_MODELS[3]: 1.0}, "off", 1, 1, ("solo",)),
            ("balanced-serial", 3, balanced, "off", 1, 1, ("serial_balanced",)),
            (
                "balanced-concurrent-batch-off",
                3,
                balanced,
                "off",
                1,
                8,
                ("concurrent_balanced", "batching_off"),
            ),
            ("balanced-concurrent-batch-on", 3, balanced, "on", 1, 8, ("batching_on",)),
            ("balanced-driver-l1w1", 3, balanced, "on", 1, 1, ("client_driver_l1w1",)),
            ("balanced-driver-l2w4", 3, balanced, "on", 2, 4, ("client_driver_l2w4",)),
            (
                "hot-dlrm-l2w4",
                3,
                {
                    "higgs_logistic_regression": 0.10,
                    "higgs_gaussian_nb": 0.10,
                    "higgs_tiny_mlp": 0.10,
                    "criteo_dlrm_lite": 0.70,
                },
                "on",
                2,
                4,
                ("hot_mix",),
            ),
        )
        observed_cells = tuple(
            (
                item.cell_id,
                item.repetitions,
                dict(item.model_mix),
                item.batching,
                item.client_lanes,
                item.client_workers,
                item.analytical_roles,
            )
            for item in self.cells
        )
        if observed_cells != expected_cells:
            raise X1ResumeTestbedError("x1_resume_cell_contract")
        if dict(self.batching) != {
            "off": {
                "enabled": False,
                "preferred_batch_sizes": [],
                "max_queue_delay_microseconds": 0,
            },
            "on": {
                "enabled": True,
                "preferred_batch_sizes": [4, 8],
                "max_queue_delay_microseconds": 10_000,
            },
        }:
            raise X1ResumeTestbedError("x1_resume_batching_contract")
        if self.input_paths != {
            "s3_registry": "artifacts/scale_validation/s3/capacity-registry.json",
            "s3_replay_features": "artifacts/scale_validation/s3/higgs-uci-2014-seed-20260817-v1/splits/replay/features.npy",
            "s4_registry": "artifacts/scale_validation/s4/tiny-mlp-v1/registry.json",
            "s5_manifest": "datasets/criteo-click-logs/s5/governed/dataset-manifest.json",
        }:
            raise X1ResumeTestbedError("x1_resume_input_contract")
        if (
            self.seed,
            self.triton_image,
            self.triton_image_digest,
            self.expected_gpu_name,
            self.expected_gpu_uuid,
            self.http_port,
            self.grpc_port,
            self.metrics_port,
            self.readiness_timeout_seconds,
            self.cleanup_timeout_seconds,
            self.sample_rows_per_dataset,
            self.warmup_seconds,
            self.measurement_seconds,
            self.offered_rps,
            self.minimum_offered_rate_attainment,
            self.matched_load_relative_tolerance,
            self.queue_depth_per_api,
            self.request_timeout_seconds,
            self.sample_gpu_interval_ms,
            self.q0_activity_seconds_per_model,
            self.q0_workers,
            self.q0_request_batch_size,
        ) != (
            20260826,
            "nvcr.io/nvidia/tritonserver:25.08-py3",
            "sha256:f836551575df7c9fb71144073845c6b3911de57db91a8c95e0687a4d2ac9f7a5",
            "NVIDIA GeForce RTX 4080 SUPER",
            "GPU-4eea4bfc-f15e-bd25-c1b8-ed53ade9ad1d",
            18300,
            18301,
            18302,
            60,
            120,
            256,
            10,
            30,
            800,
            0.90,
            0.05,
            64,
            5,
            200,
            5,
            8,
            32,
        ):
            raise X1ResumeTestbedError("x1_resume_runtime_contract")
        if dict(self.profiler) != {
            "enabled_if_available": True,
            "repetitions": 1,
            "capture_seconds": 10,
            "claim_kernel_overlap_only_when_directly_observed": True,
        }:
            raise X1ResumeTestbedError("x1_resume_profiler_contract")
        if (
            CLAIM_CLASS not in self.claim_boundary.lower()
            or "non-credit" not in self.claim_boundary.lower()
        ):
            raise X1ResumeTestbedError("x1_resume_claim_boundary")

    @property
    def immutable_image(self) -> str:
        return f"{self.triton_image.rsplit(':', 1)[0]}@{self.triton_image_digest}"

    @property
    def expected_physical_runs(self) -> int:
        return sum(cell.repetitions for cell in self.cells)


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def jain_fairness(values: Sequence[float]) -> float:
    finite = [float(value) for value in values if math.isfinite(float(value)) and float(value) >= 0]
    if not finite or sum(value * value for value in finite) == 0:
        return 0.0
    return sum(finite) ** 2 / (len(finite) * sum(value * value for value in finite))


def deterministic_model_schedule(model_mix: Mapping[str, float]) -> tuple[str, ...]:
    """Build a deterministic 100-slot smooth weighted round-robin schedule."""
    weights = {
        model_id: round(float(model_mix.get(model_id, 0)) * 100) for model_id in EXPECTED_MODELS
    }
    if sum(weights.values()) != 100:
        raise X1ResumeTestbedError(f"x1_resume_mix_schedule:{weights}")
    current = {model_id: 0 for model_id in EXPECTED_MODELS}
    slots: list[str] = []
    for _ in range(100):
        for model_id in EXPECTED_MODELS:
            current[model_id] += weights[model_id]
        selected = max(EXPECTED_MODELS, key=lambda model_id: current[model_id])
        current[selected] -= 100
        slots.append(selected)
    if Counter(slots) != Counter(weights):
        raise X1ResumeTestbedError("x1_resume_mix_schedule_counts")
    return tuple(slots)


def request_interval_overlap(
    records: Sequence[Mapping[str, Any]],
    *,
    measurement_start_ns: int | None = None,
    measurement_end_ns: int | None = None,
) -> dict[str, Any]:
    events: list[tuple[int, int, str]] = []
    for item in records:
        if item.get("outcome") != "completed":
            continue
        started_ns = int(item["started_ns"])
        finished_ns = int(item["finished_ns"])
        if finished_ns <= started_ns or float(item.get("latency_ms", 0)) <= 0:
            raise X1ResumeTestbedError("x1_resume_request_interval_invalid")
        clipped_start = max(
            started_ns,
            measurement_start_ns if measurement_start_ns is not None else started_ns,
        )
        clipped_finish = min(
            finished_ns,
            measurement_end_ns if measurement_end_ns is not None else finished_ns,
        )
        if clipped_start >= clipped_finish:
            continue
        # Finishes sort before starts at an equal endpoint, so touching intervals do not overlap.
        events.append((clipped_finish, 0, str(item["model_id"])))
        events.append((clipped_start, 1, str(item["model_id"])))
    active: Counter[str] = Counter()
    pairs: set[tuple[str, str]] = set()
    for _timestamp, kind, model_id in sorted(events):
        if kind == 1:
            for other, count in active.items():
                if count > 0 and other != model_id:
                    pairs.add(tuple(sorted((model_id, other))))
            active[model_id] += 1
        else:
            active[model_id] -= 1
    return {
        "observed": bool(pairs),
        "distinct_model_pairs": [list(pair) for pair in sorted(pairs)],
        "scope": "client request intervals overlap; not CUDA kernel-overlap evidence",
    }


def triton_trace_compute_counts(path: Path) -> dict[str, int]:
    counts = {model_id: 0 for model_id in EXPECTED_MODELS}
    if not path.is_file():
        return counts
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise X1ResumeTestbedError("x1_resume_trace_utf8") from exc
    values: list[Any] = []
    try:
        values.append(json.loads(text))
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        offset = 0
        while offset < len(text):
            while offset < len(text) and (text[offset].isspace() or text[offset] == ","):
                offset += 1
            if offset >= len(text):
                break
            try:
                value, offset = decoder.raw_decode(text, offset)
            except json.JSONDecodeError as exc:
                raise X1ResumeTestbedError("x1_resume_trace_json") from exc
            values.append(value)

    model_by_id: dict[str, str] = {}
    compute_by_id: Counter[str] = Counter()

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, Mapping):
            return
        trace_id = str(value.get("id") or value.get("trace_id") or "")
        model_name = str(value.get("model_name") or "")
        if trace_id and model_name in EXPECTED_MODELS:
            if trace_id in model_by_id and model_by_id[trace_id] != model_name:
                raise X1ResumeTestbedError(f"x1_resume_trace_model_conflict:{trace_id}")
            model_by_id[trace_id] = model_name
        timestamps = value.get("timestamps", [])
        if trace_id and isinstance(timestamps, list):
            compute_by_id[trace_id] += sum(
                isinstance(item, Mapping) and str(item.get("name") or "").upper() == "COMPUTE_START"
                for item in timestamps
            )
        for key, item in value.items():
            if key != "timestamps" and isinstance(item, (list, Mapping)):
                visit(item)

    for value in values:
        visit(value)
    for trace_id, amount in compute_by_id.items():
        model_id = model_by_id.get(trace_id)
        if amount and not model_id:
            raise X1ResumeTestbedError(f"x1_resume_trace_unbound_compute:{trace_id}")
        if model_id:
            counts[model_id] += int(amount)
    return counts


def summarize_requests(
    *,
    offered: int,
    admitted: int,
    local_admission_rejected: int,
    records: Sequence[Mapping[str, Any]],
    measurement_seconds: float,
    measurement_start_ns: int,
    measurement_end_ns: int,
    drain_seconds: float,
    model_mix: Mapping[str, float],
) -> dict[str, Any]:
    terminal_ids = [str(item.get("request_id") or "") for item in records]
    duplicate = sum(count - 1 for count in Counter(terminal_ids).values() if count > 1)
    cohort_completed = sum(item.get("outcome") == "completed" for item in records)
    cohort_failures_5xx = sum(item.get("outcome") == "5xx" for item in records)
    cohort_other_errors = sum(item.get("outcome") == "error" for item in records)
    cohort_terminal = cohort_completed + cohort_failures_5xx + cohort_other_errors
    window_records = [
        item
        for item in records
        if measurement_start_ns
        <= int(item.get("finished_ns", measurement_end_ns + 1))
        <= measurement_end_ns
    ]
    window_completed = sum(item.get("outcome") == "completed" for item in window_records)
    window_failures_5xx = sum(item.get("outcome") == "5xx" for item in window_records)
    window_other_errors = sum(item.get("outcome") == "error" for item in window_records)
    latencies = [
        float(item["latency_ms"]) for item in window_records if item.get("outcome") == "completed"
    ]
    queue_waits = [
        float(item["queue_wait_ms"])
        for item in window_records
        if item.get("outcome") == "completed"
    ]
    per_model = {}
    for model_id in EXPECTED_MODELS:
        model_records = [item for item in records if item.get("model_id") == model_id]
        model_cohort_completed = sum(item.get("outcome") == "completed" for item in model_records)
        model_window_records = [
            item
            for item in model_records
            if measurement_start_ns
            <= int(item.get("finished_ns", measurement_end_ns + 1))
            <= measurement_end_ns
        ]
        model_window_completed = sum(
            item.get("outcome") == "completed" for item in model_window_records
        )
        model_latencies = [
            float(item["latency_ms"])
            for item in model_window_records
            if item.get("outcome") == "completed"
        ]
        per_model[model_id] = {
            "window_completed": model_window_completed,
            "admitted_cohort_completed": model_cohort_completed,
            "throughput_rps": model_window_completed / max(measurement_seconds, 1e-9),
            "p99_ms": percentile(model_latencies, 0.99),
        }
    raw_rates = [per_model[item]["throughput_rps"] for item in EXPECTED_MODELS]
    actual_offered_rps = offered / max(measurement_seconds, 1e-9)
    attainment = [
        per_model[item]["throughput_rps"]
        / max(float(model_mix.get(item, 0.0)) * actual_offered_rps, 1e-9)
        for item in EXPECTED_MODELS
        if float(model_mix.get(item, 0.0)) > 0
    ]
    return {
        "offered": offered,
        "admitted": admitted,
        "local_admission_rejected": local_admission_rejected,
        "window_completed": window_completed,
        "window_http_5xx": window_failures_5xx,
        "window_other_errors": window_other_errors,
        "admitted_cohort_completed": cohort_completed,
        "admitted_cohort_http_5xx": cohort_failures_5xx,
        "admitted_cohort_other_errors": cohort_other_errors,
        "tail_completed": cohort_completed - window_completed,
        "loss": max(0, admitted - cohort_terminal),
        "duplicates": duplicate,
        "throughput_rps": window_completed / max(measurement_seconds, 1e-9),
        "actual_offered_rps": actual_offered_rps,
        "drain_seconds": drain_seconds,
        "throughput_scope": "completions whose terminal timestamp is inside the fixed measurement window",
        "terminal_scope": "all requests admitted during the fixed measurement window after bounded drain",
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
        },
        "queue_wait_ms": {
            "p50": percentile(queue_waits, 0.50),
            "p95": percentile(queue_waits, 0.95),
            "p99": percentile(queue_waits, 0.99),
        },
        "per_model": per_model,
        "fairness_target_basis": "model_mix * actual_window_offered_rps",
        "raw_throughput_jain_fairness": jain_fairness(raw_rates),
        "normalized_attainment_jain_fairness": jain_fairness(attainment),
    }


def _bound_file(root: Path, identity: Mapping[str, Any], label: str) -> Path:
    relative = Path(str(identity.get("path") or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise X1ResumeTestbedError(f"x1_resume_private_path:{label}")
    resolved_root = root.resolve()
    path = (resolved_root / relative).resolve()
    try:
        path.relative_to(resolved_root)
    except ValueError as exc:
        raise X1ResumeTestbedError(f"x1_resume_private_containment:{label}") from exc
    if (
        not path.is_file()
        or path.stat().st_size != int(identity.get("bytes", -1))
        or sha256_file(path) != identity.get("sha256")
    ):
        raise X1ResumeTestbedError(f"x1_resume_private_digest:{label}:{relative}")
    return path


def validate_repository_entries(
    manifest: Mapping[str, Any], model_repository_root: Path
) -> list[dict[str, Any]]:
    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list) or len(raw_entries) != len(
        EXPECTED_REPOSITORY_ENTRY_PATHS
    ):
        raise X1ResumeTestbedError("x1_resume_private_repository_entry_set")
    entries: list[dict[str, Any]] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, Mapping) or set(raw_entry) != {
            "path",
            "bytes",
            "sha256",
        }:
            raise X1ResumeTestbedError("x1_resume_private_repository_entry_schema")
        entries.append(dict(raw_entry))
    if tuple(str(item.get("path") or "") for item in entries) != (EXPECTED_REPOSITORY_ENTRY_PATHS):
        raise X1ResumeTestbedError("x1_resume_private_repository_entry_set")
    for item in entries:
        _bound_file(model_repository_root, item, "repository_entry")
    sample_entry = next(item for item in entries if item["path"] == "testbed-samples.json")
    sample_path = _bound_file(model_repository_root, sample_entry, "testbed_samples")
    if (
        manifest.get("samples_sha256") != sample_entry["sha256"]
        or sample_entry["sha256"] != sha256_file(sample_path)
        or int(sample_entry["bytes"]) != sample_path.stat().st_size
    ):
        raise X1ResumeTestbedError("x1_resume_private_repository_samples_binding")
    return entries


def _governed_file(data_root: Path, identity: Mapping[str, Any], *, label: str) -> Path:
    relative = Path(str(identity["path"]))
    resolved_root = data_root.resolve()
    path = (resolved_root / relative).resolve()
    try:
        path.relative_to(resolved_root)
    except ValueError as exc:
        raise X1ResumeTestbedError(f"x1_resume_governed_containment:{label}") from exc
    if (
        not path.is_file()
        or path.stat().st_size != identity.get("bytes")
        or sha256_file(path) != identity.get("sha256")
    ):
        raise X1ResumeTestbedError(f"x1_resume_governed_identity:{label}")
    return path


def _governed_json(data_root: Path, identity: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    path = _governed_file(data_root, identity, label=label)
    payload = strict_json_loads(path.read_bytes(), label=label)
    if not isinstance(payload, dict):
        raise X1ResumeTestbedError(f"x1_resume_governed_json:{label}")
    return payload


def validate_governed_source_bindings(
    manifest: Mapping[str, Any], *, data_root: Path, config: X1ResumeConfig
) -> None:
    bindings = manifest.get("source_bindings")
    framework = manifest.get("framework")
    if (
        not isinstance(bindings, Mapping)
        or set(bindings) != set(EXPECTED_MODELS)
        or not isinstance(framework, Mapping)
        or set(framework) != {"torch", "cuda_build"}
        or not re.fullmatch(
            r"[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[^\s]*)", str(framework.get("torch") or "")
        )
        or not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", str(framework.get("cuda_build") or ""))
    ):
        raise X1ResumeTestbedError("x1_resume_governed_binding_schema")

    s3_registry_identity = GOVERNED_SOURCE_IDENTITIES["s3_registry"]
    s3_registry = _governed_json(data_root, s3_registry_identity, label="s3_registry")
    if any(
        s3_registry.get(key) != s3_registry_identity[key]
        for key in ("schema_version", "dataset_identity_sha256", "split_manifest_sha256")
    ):
        raise X1ResumeTestbedError("x1_resume_governed_s3_registry")
    probes = dict(s3_registry.get("probes", {}))
    replay_identity = GOVERNED_SOURCE_IDENTITIES["s3_replay"]
    _governed_file(data_root, replay_identity, label="s3_replay")
    shared_replay = {
        "registry_path": s3_registry_identity["path"],
        "registry_sha256": s3_registry_identity["sha256"],
        "registry_bytes": s3_registry_identity["bytes"],
        "replay_path": replay_identity["path"],
        "replay_sha256": replay_identity["sha256"],
        "replay_bytes": replay_identity["bytes"],
        "replay_shape": [100000, 28],
        "sample_shape": [config.sample_rows_per_dataset, 28],
        "dataset_identity_sha256": s3_registry_identity["dataset_identity_sha256"],
        "split_manifest_sha256": s3_registry_identity["split_manifest_sha256"],
    }
    for model_id, probe_key, identity_key in (
        ("higgs_logistic_regression", "logistic", "s3_logistic"),
        ("higgs_gaussian_nb", "probabilistic", "s3_probabilistic"),
    ):
        source_identity = GOVERNED_SOURCE_IDENTITIES[identity_key]
        source = _governed_json(data_root, source_identity, label=identity_key)
        probe = dict(probes.get(probe_key, {}))
        expected = {
            "source_schema": source_identity["schema_version"],
            "source_path": source_identity["path"],
            "source_sha256": source_identity["sha256"],
            "source_bytes": source_identity["bytes"],
            "dataset_identity_sha256": s3_registry_identity["dataset_identity_sha256"],
            "replay": shared_replay,
        }
        if (
            dict(bindings.get(model_id, {})) != expected
            or source.get("schema_version") != source_identity["schema_version"]
            or source.get("dataset_identity_sha256")
            != s3_registry_identity["dataset_identity_sha256"]
            or probe.get("artifact_uri")
            != source_identity["path"].removeprefix("artifacts/scale_validation/s3/")
            or probe.get("artifact_sha256") != source_identity["sha256"]
        ):
            raise X1ResumeTestbedError(f"x1_resume_governed_s3_binding:{model_id}")

    s4_registry_identity = GOVERNED_SOURCE_IDENTITIES["s4_registry"]
    s4_artifact_identity = GOVERNED_SOURCE_IDENTITIES["s4_artifact"]
    s4_registry = _governed_json(data_root, s4_registry_identity, label="s4_registry")
    _governed_file(data_root, s4_artifact_identity, label="s4_artifact")
    expected_s4 = {
        "source_schema": s4_registry_identity["schema_version"],
        "source_path": s4_artifact_identity["path"],
        "source_sha256": s4_artifact_identity["sha256"],
        "source_bytes": s4_artifact_identity["bytes"],
        "model_identity_sha256": s4_registry_identity["model_identity_sha256"],
        "registry_sha256": s4_registry_identity["sha256"],
        "registry_path": s4_registry_identity["path"],
        "registry_bytes": s4_registry_identity["bytes"],
        "preprocessing_sha256": s4_registry_identity["preprocessing_sha256"],
        "dataset_identity_sha256": s3_registry_identity["dataset_identity_sha256"],
        "split_manifest_sha256": s3_registry_identity["split_manifest_sha256"],
        "replay": shared_replay,
    }
    if (
        dict(bindings.get("higgs_tiny_mlp", {})) != expected_s4
        or s4_registry.get("schema_version") != s4_registry_identity["schema_version"]
        or s4_registry.get("artifact_sha256") != s4_artifact_identity["sha256"]
        or s4_registry.get("model_identity_sha256") != s4_registry_identity["model_identity_sha256"]
        or s4_registry.get("preprocessing_sha256") != s4_registry_identity["preprocessing_sha256"]
        or s4_registry.get("dataset_identity_sha256")
        != s3_registry_identity["dataset_identity_sha256"]
    ):
        raise X1ResumeTestbedError("x1_resume_governed_s4_binding")

    s5_identity = GOVERNED_SOURCE_IDENTITIES["s5_manifest"]
    s5_manifest = _governed_json(data_root, s5_identity, label="s5_manifest")
    first_shard_identity = {
        "path": str(Path(s5_identity["path"]).parent / s5_identity["first_shard_path"]),
        "sha256": s5_identity["first_shard_sha256"],
        "bytes": s5_identity["first_shard_bytes"],
    }
    _governed_file(data_root, first_shard_identity, label="s5_first_shard")
    expected_s5 = {
        "manifest_path": s5_identity["path"],
        "manifest_sha256": s5_identity["sha256"],
        "manifest_bytes": s5_identity["bytes"],
        "dataset_version": s5_identity["dataset_version"],
        "source_revision": s5_identity["source_revision"],
        "shard_path": s5_identity["first_shard_path"],
        "shard_sha256": s5_identity["first_shard_sha256"],
        "shard_bytes": s5_identity["first_shard_bytes"],
        "sample_rows": config.sample_rows_per_dataset,
        "categorical_hash": "sha256-first-u64-mod-4096",
        "dense_transform": "log1p(max(value,0))",
        "parameter_origin": "deterministic_seeded_testbed_initialization",
        "training_or_quality_claim": False,
        "seed": config.seed,
    }
    shards = s5_manifest.get("shards")
    if not isinstance(shards, list) or not shards or not isinstance(shards[0], Mapping):
        raise X1ResumeTestbedError("x1_resume_governed_s5_binding")
    first_shard = dict(shards[0])
    if (
        dict(bindings.get("criteo_dlrm_lite", {})) != expected_s5
        or s5_manifest.get("schema_version") != s5_identity["schema_version"]
        or s5_manifest.get("dataset_version") != s5_identity["dataset_version"]
        or s5_manifest.get("source_revision") != s5_identity["source_revision"]
        or first_shard.get("governed_path") != s5_identity["first_shard_path"]
        or first_shard.get("governed_sha256") != s5_identity["first_shard_sha256"]
    ):
        raise X1ResumeTestbedError("x1_resume_governed_s5_binding")


def validate_sample_payload(sample_path: Path, config: X1ResumeConfig) -> dict[str, Any]:
    payload = load_canonical_json(sample_path, label="testbed_samples")
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"schema_version", "seed", "samples", "oracle"}
        or payload.get("schema_version") != "evm.s8_v4.x1_resume_samples.v1"
        or payload.get("seed") != config.seed
        or not isinstance(payload.get("samples"), Mapping)
        or not isinstance(payload.get("oracle"), Mapping)
        or set(payload["samples"]) != set(EXPECTED_MODELS)
        or set(payload["oracle"]) != set(EXPECTED_MODELS)
    ):
        raise X1ResumeTestbedError("x1_resume_private_samples_schema")
    for model in config.models:
        samples = payload["samples"][model.model_id]
        oracle = payload["oracle"][model.model_id]
        if (
            not isinstance(samples, list)
            or len(samples) != config.sample_rows_per_dataset
            or any(not isinstance(row, list) or len(row) != model.input_width for row in samples)
            or not isinstance(oracle, Mapping)
            or set(oracle)
            != {"input_width", "sample_count", "first_output", "output_sha256", "outputs"}
            or oracle.get("input_width") != model.input_width
            or oracle.get("sample_count") != config.sample_rows_per_dataset
            or not isinstance(oracle.get("outputs"), list)
            or len(oracle["outputs"]) != config.sample_rows_per_dataset
            or canonical_sha256([float(value) for value in oracle["outputs"]])
            != oracle.get("output_sha256")
            or float(oracle["outputs"][0]) != float(oracle.get("first_output"))
        ):
            raise X1ResumeTestbedError(f"x1_resume_private_samples_lineage:{model.model_id}")
    return dict(payload)


def validate_gpu_samples(
    raw_samples: Any, config: X1ResumeConfig, *, label: str
) -> dict[str, float | int]:
    if not isinstance(raw_samples, list) or not raw_samples:
        raise X1ResumeTestbedError(f"x1_resume_gpu_samples:{label}")
    required_fields = {
        "uuid",
        "name",
        "memory_used_mib",
        "memory_total_mib",
        "utilization_percent",
    }
    utilization_values: list[float] = []
    memory_used_values: list[float] = []
    for sample in raw_samples:
        if not isinstance(sample, Mapping) or set(sample) != required_fields:
            raise X1ResumeTestbedError(f"x1_resume_gpu_sample_schema:{label}")
        if (sample.get("uuid"), sample.get("name")) != (
            config.expected_gpu_uuid,
            config.expected_gpu_name,
        ):
            raise X1ResumeTestbedError(f"x1_resume_gpu_sample_identity:{label}")
        numeric = [
            sample.get("memory_used_mib"),
            sample.get("memory_total_mib"),
            sample.get("utilization_percent"),
        ]
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            for value in numeric
        ):
            raise X1ResumeTestbedError(f"x1_resume_gpu_sample_numeric:{label}")
        memory_used, memory_total, utilization = (float(value) for value in numeric)
        if (
            memory_used < 0
            or memory_total <= 0
            or memory_used > memory_total
            or not 0 <= utilization <= 100
        ):
            raise X1ResumeTestbedError(f"x1_resume_gpu_sample_range:{label}")
        utilization_values.append(utilization)
        memory_used_values.append(memory_used)
    return {
        "sample_count": len(raw_samples),
        "busy_sample_count": sum(value > 0 for value in utilization_values),
        "utilization_max_percent": max(utilization_values),
        "vram_max_mib": max(memory_used_values),
    }


def _protobuf_integer_exact(value: Any, expected: int) -> bool:
    if isinstance(value, bool):
        return False
    if type(value) is int:
        return value == expected
    return isinstance(value, str) and bool(re.fullmatch(r"-?\d+", value)) and int(value) == expected


def triton_gpu_instance_exact(
    config_readback: Any,
    *,
    model_id: str,
    input_width: int,
    dynamic_batching_enabled: bool,
) -> bool:
    if not isinstance(config_readback, Mapping):
        return False
    groups = config_readback.get("instance_group")
    if not isinstance(groups, list) or len(groups) != 1 or not isinstance(groups[0], Mapping):
        return False
    group = groups[0]
    gpus = group.get("gpus")
    inputs = config_readback.get("input")
    outputs = config_readback.get("output")
    if (
        group.get("kind") != "KIND_GPU"
        or not _protobuf_integer_exact(group.get("count"), 1)
        or not isinstance(gpus, list)
        or len(gpus) != 1
        or not _protobuf_integer_exact(gpus[0], 0)
        or config_readback.get("name") != model_id
        or config_readback.get("backend") != "pytorch"
        or not _protobuf_integer_exact(config_readback.get("max_batch_size"), 32)
        or bool(config_readback.get("dynamic_batching")) != dynamic_batching_enabled
        or not isinstance(inputs, list)
        or len(inputs) != 1
        or not isinstance(inputs[0], Mapping)
        or inputs[0].get("name") != "FEATURES__0"
        or inputs[0].get("data_type") != "TYPE_FP32"
        or not isinstance(inputs[0].get("dims"), list)
        or len(inputs[0]["dims"]) != 1
        or not _protobuf_integer_exact(inputs[0]["dims"][0], input_width)
        or not isinstance(outputs, list)
        or len(outputs) != 1
        or not isinstance(outputs[0], Mapping)
        or outputs[0].get("name") != "SCORE__0"
        or outputs[0].get("data_type") != "TYPE_FP32"
        or not isinstance(outputs[0].get("dims"), list)
        or len(outputs[0]["dims"]) != 1
        or not _protobuf_integer_exact(outputs[0]["dims"][0], 1)
    ):
        return False
    return True


def _validate_manifest_contract(manifest: Any, config: X1ResumeConfig) -> None:
    required_keys = {
        "schema_version",
        "claim_class",
        "credit",
        "config_sha256",
        "source_revision",
        "source_tree_sha",
        "source_blobs",
        "triton_image",
        "backend",
        "instance_kind",
        "cpu_fallback_allowed",
        "model_ids",
        "source_bindings",
        "framework",
        "samples_sha256",
        "profile_identities",
        "model_identities",
        "entries",
        "repository_sha256",
        "claim_boundary",
    }
    if (
        not isinstance(manifest, Mapping)
        or set(manifest) != required_keys
        or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION
        or manifest.get("claim_class") != CLAIM_CLASS
        or manifest.get("credit") != CREDIT
        or manifest.get("config_sha256") != config.sha256
        or manifest.get("triton_image") != config.immutable_image
        or manifest.get("backend") != "pytorch"
        or manifest.get("instance_kind") != "KIND_GPU"
        or manifest.get("cpu_fallback_allowed") is not False
        or tuple(manifest.get("model_ids", [])) != EXPECTED_MODELS
        or manifest.get("claim_boundary") != config.claim_boundary
    ):
        raise X1ResumeTestbedError("x1_resume_private_manifest_contract")


def _validate_environment_contract(
    payload: Mapping[str, Any], *, config: X1ResumeConfig, revision: str
) -> None:
    environment = payload.get("environment")
    required_keys = {
        "gpu_before",
        "triton_processes_before",
        "triton_image",
        "repository_manifest_sha256",
        "repository_sha256",
        "b0_before",
        "gpu_lease",
    }
    if not isinstance(environment, Mapping) or set(environment) != required_keys:
        raise X1ResumeTestbedError("x1_resume_private_environment_schema")
    gpu_before = environment.get("gpu_before")
    b0_before = environment.get("b0_before")
    gpu_lease = environment.get("gpu_lease")
    if not isinstance(b0_before, Mapping) or set(b0_before) != {"holder", "cuda"}:
        raise X1ResumeTestbedError("x1_resume_private_environment_contract")
    _validate_b0_holder(b0_before.get("holder"), label="environment")
    _validate_b0_cuda(b0_before.get("cuda"), label="environment")
    validate_gpu_samples([gpu_before], config, label="environment")
    if (
        not isinstance(gpu_before, Mapping)
        or (gpu_before.get("uuid"), gpu_before.get("name"))
        != (config.expected_gpu_uuid, config.expected_gpu_name)
        or environment.get("triton_image") != config.immutable_image
        or environment.get("triton_processes_before") != []
        or not isinstance(gpu_lease, Mapping)
        or set(gpu_lease)
        != {
            "lease_id",
            "run_id",
            "scenario_id",
            "model_family",
            "purpose",
            "source_commit",
            "fencing_token_sha256",
        }
        or not re.fullmatch(r"gpu-lease-[0-9a-f]{32}", str(gpu_lease.get("lease_id") or ""))
        or gpu_lease.get("run_id") != payload.get("suite_id")
        or gpu_lease.get("scenario_id") != "X1-RESUME"
        or gpu_lease.get("model_family") != "tabular"
        or gpu_lease.get("purpose") != "scale_validation_inference"
        or gpu_lease.get("source_commit") != revision
        or not re.fullmatch(r"[0-9a-f]{64}", str(gpu_lease.get("fencing_token_sha256") or ""))
    ):
        raise X1ResumeTestbedError("x1_resume_private_environment_contract")


def _validate_released_lease(
    payload: Mapping[str, Any],
    released: Any,
    archived: Any,
    identity: Mapping[str, Any],
    archive_identity: Mapping[str, Any],
) -> None:
    environment = dict(payload.get("environment", {}))
    active_identity = dict(environment.get("gpu_lease", {}))
    required = {
        "schema_version",
        "lease_id",
        "fencing_token",
        "run_id",
        "scenario_id",
        "model_family",
        "lease_purpose",
        "owner_pid",
        "source_commit",
        "acquired_at",
        "expires_at",
        "state",
        "released_at",
        "release_reason",
    }
    suite_id = str(payload.get("suite_id") or "")
    timestamps: dict[str, datetime] = {}
    if isinstance(released, Mapping):
        try:
            for key in ("acquired_at", "expires_at", "released_at"):
                value = released.get(key)
                if type(value) is not str or not value.endswith("Z"):
                    raise ValueError(key)
                parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
                if parsed.tzinfo is None:
                    raise ValueError(key)
                timestamps[key] = parsed.astimezone(UTC)
        except (TypeError, ValueError):
            timestamps = {}
    if (
        not isinstance(released, Mapping)
        or set(released) != required
        or released.get("schema_version") != "evm.scenario_gpu_lease.v1"
        or not re.fullmatch(r"gpu-lease-[0-9a-f]{32}", str(released.get("lease_id") or ""))
        or released.get("lease_id") != active_identity.get("lease_id")
        or not re.fullmatch(r"x1-resume-\d{8}T\d{6}Z-[0-9a-f]{8}", suite_id)
        or released.get("run_id") != suite_id
        or released.get("scenario_id") != "X1-RESUME"
        or released.get("model_family") != "tabular"
        or released.get("lease_purpose") != "scale_validation_inference"
        or not re.fullmatch(r"[0-9a-f]{40}", str(released.get("source_commit") or ""))
        or released.get("source_commit") != active_identity.get("source_commit")
        or not re.fullmatch(r"[0-9a-f]{32}", str(released.get("fencing_token") or ""))
        or canonical_sha256(released.get("fencing_token"))
        != active_identity.get("fencing_token_sha256")
        or type(released.get("owner_pid")) is not int
        or int(released["owner_pid"]) <= 0
        or released.get("state") != "released"
        or released.get("release_reason") != f"{suite_id} finished"
        or set(timestamps) != {"acquired_at", "expires_at", "released_at"}
        or not (
            timestamps.get("acquired_at")
            < timestamps.get("released_at")
            <= timestamps.get("expires_at")
        )
        or identity.get("lease_id") != released.get("lease_id")
        or identity.get("run_id") != suite_id
        or identity.get("state") != "released"
        or identity.get("release_reason") != f"{suite_id} finished"
        or set(identity)
        != {"path", "bytes", "sha256", "lease_id", "run_id", "state", "release_reason"}
        or set(archive_identity) != {"path", "bytes", "sha256"}
        or canonical(archived) != canonical(released)
    ):
        raise X1ResumeTestbedError("x1_resume_private_released_lease")


def _triton_metrics_for_model(text: str, model_id: str) -> dict[str, float]:
    fields = {
        "nv_inference_request_success": "success",
        "nv_inference_compute_infer_duration_us": "compute_us",
        "nv_inference_count": "inference_count",
        "nv_inference_exec_count": "execution_count",
    }
    result = {field: 0.0 for field in fields.values()}
    seen: set[str] = set()
    for line in text.splitlines():
        if f'model="{model_id}"' not in line:
            continue
        for metric, field in fields.items():
            if line.startswith(metric):
                try:
                    value = float(line.rsplit(" ", 1)[1])
                except (IndexError, ValueError):
                    raise X1ResumeTestbedError(
                        f"x1_resume_private_metric_parse:{model_id}:{metric}"
                    ) from None
                if not math.isfinite(value) or value < 0:
                    raise X1ResumeTestbedError(
                        f"x1_resume_private_metric_value:{model_id}:{metric}"
                    )
                result[field] += value
                seen.add(field)
    if seen != set(result):
        raise X1ResumeTestbedError(f"x1_resume_private_metric_missing:{model_id}")
    for field in ("success", "inference_count", "execution_count"):
        if not float(result[field]).is_integer():
            raise X1ResumeTestbedError(f"x1_resume_private_metric_integer:{model_id}:{field}")
    return result


def _triton_metric_deltas(
    before: Mapping[str, float], after: Mapping[str, float], *, model_id: str
) -> dict[str, float]:
    expected_fields = {"success", "compute_us", "inference_count", "execution_count"}
    if set(before) != expected_fields or set(after) != expected_fields:
        raise X1ResumeTestbedError(f"x1_resume_private_metric_fields:{model_id}")
    deltas: dict[str, float] = {}
    for field in sorted(expected_fields):
        before_value = before[field]
        after_value = after[field]
        if (
            isinstance(before_value, bool)
            or isinstance(after_value, bool)
            or not isinstance(before_value, (int, float))
            or not isinstance(after_value, (int, float))
            or not math.isfinite(float(before_value))
            or not math.isfinite(float(after_value))
            or float(before_value) < 0
            or float(after_value) < float(before_value)
        ):
            raise X1ResumeTestbedError(f"x1_resume_private_metric_counter:{model_id}:{field}")
        delta = float(after_value) - float(before_value)
        if not math.isfinite(delta) or delta < 0:
            raise X1ResumeTestbedError(f"x1_resume_private_metric_delta:{model_id}:{field}")
        if field in {"success", "inference_count", "execution_count"} and not delta.is_integer():
            raise X1ResumeTestbedError(f"x1_resume_private_metric_delta_integer:{model_id}:{field}")
        deltas[field] = delta
    return deltas


def _attempt_id_pattern(suite_id: str, cell_id: str, repetition: int) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(suite_id)}-{re.escape(cell_id)}-r{repetition}-[0-9a-f]{{8}}$")


def _validate_attempt_records(
    records: Any,
    terminal_records: Any,
    admission_ledger: Any,
    *,
    attempt_id: str,
    model_mix: Mapping[str, float],
    warmup_seconds: int,
    measurement_start_ns: int,
    measurement_end_ns: int,
    admission: Mapping[str, Any],
) -> tuple[Counter[str], Counter[str], set[str], dict[str, Any]]:
    if (
        not isinstance(records, list)
        or not isinstance(terminal_records, list)
        or not isinstance(admission_ledger, list)
        or not admission_ledger
    ):
        raise X1ResumeTestbedError("x1_resume_private_attempt_records")
    required_fields = {
        "request_id",
        "model_id",
        "worker_id",
        "outcome",
        "status",
        "enqueued_ns",
        "started_ns",
        "finished_ns",
        "queue_wait_ms",
        "latency_ms",
    }
    terminal_fields = required_fields | {"global_sequence", "phase"}
    ledger_fields = {
        "global_sequence",
        "request_id",
        "model_id",
        "phase",
        "enqueued_ns",
        "decision_ns",
        "decision",
        "reason",
    }
    schedule = deterministic_model_schedule(model_mix)
    attempt_start_ns = measurement_start_ns - warmup_seconds * 1_000_000_000
    if attempt_start_ns <= 0:
        raise X1ResumeTestbedError("x1_resume_private_attempt_window")
    ledger_by_id: dict[str, dict[str, Any]] = {}
    measured_offered_by_model: Counter[str] = Counter()
    measured_accepted_by_model: Counter[str] = Counter()
    measured_rejected_by_model: Counter[str] = Counter()
    for expected_sequence, ledger_item in enumerate(admission_ledger):
        if not isinstance(ledger_item, Mapping) or set(ledger_item) != ledger_fields:
            raise X1ResumeTestbedError("x1_resume_private_admission_ledger_schema")
        sequence = ledger_item.get("global_sequence")
        request_id = ledger_item.get("request_id")
        model_id = ledger_item.get("model_id")
        enqueued_ns = ledger_item.get("enqueued_ns")
        decision_ns = ledger_item.get("decision_ns")
        phase = ledger_item.get("phase")
        decision = ledger_item.get("decision")
        if (
            type(sequence) is not int
            or sequence != expected_sequence
            or request_id != f"{attempt_id}-{sequence}"
            or request_id in ledger_by_id
            or model_id != schedule[sequence % len(schedule)]
            or type(enqueued_ns) is not int
            or type(decision_ns) is not int
            or not attempt_start_ns <= enqueued_ns < measurement_end_ns
            or decision_ns < enqueued_ns
            or phase != ("warmup" if enqueued_ns < measurement_start_ns else "measured")
            or decision not in {"accepted", "rejected"}
            or ledger_item.get("reason")
            != ("local_queue_capacity" if decision == "accepted" else "local_queue_full")
        ):
            raise X1ResumeTestbedError("x1_resume_private_admission_ledger_identity")
        projected = dict(ledger_item)
        ledger_by_id[str(request_id)] = projected
        if phase == "measured":
            measured_offered_by_model[str(model_id)] += 1
            if decision == "accepted":
                measured_accepted_by_model[str(model_id)] += 1
            else:
                measured_rejected_by_model[str(model_id)] += 1

    terminal_by_id: dict[str, dict[str, Any]] = {}
    all_completed: Counter[str] = Counter()
    measured_projected: list[dict[str, Any]] = []
    for terminal in terminal_records:
        if not isinstance(terminal, Mapping) or set(terminal) != terminal_fields:
            raise X1ResumeTestbedError("x1_resume_private_terminal_record_schema")
        request_id = str(terminal.get("request_id") or "")
        ledger_item = ledger_by_id.get(request_id)
        worker_id = terminal.get("worker_id")
        outcome = terminal.get("outcome")
        status = terminal.get("status")
        enqueued_ns = terminal.get("enqueued_ns")
        started_ns = terminal.get("started_ns")
        finished_ns = terminal.get("finished_ns")
        queue_wait_ms = terminal.get("queue_wait_ms")
        latency_ms = terminal.get("latency_ms")
        if (
            ledger_item is None
            or ledger_item["decision"] != "accepted"
            or request_id in terminal_by_id
            or terminal.get("global_sequence") != ledger_item["global_sequence"]
            or terminal.get("phase") != ledger_item["phase"]
            or terminal.get("model_id") != ledger_item["model_id"]
            or enqueued_ns != ledger_item["enqueued_ns"]
            or type(worker_id) is not int
            or int(worker_id) < 0
            or outcome not in {"completed", "5xx", "error"}
            or type(status) is not int
            or type(started_ns) is not int
            or type(finished_ns) is not int
            or int(started_ns) < int(ledger_item["decision_ns"])
            or int(finished_ns) <= int(started_ns)
            or not isinstance(queue_wait_ms, (int, float))
            or isinstance(queue_wait_ms, bool)
            or not isinstance(latency_ms, (int, float))
            or isinstance(latency_ms, bool)
            or not math.isfinite(float(queue_wait_ms))
            or not math.isfinite(float(latency_ms))
            or float(latency_ms) <= 0
            or not math.isclose(
                float(queue_wait_ms),
                (int(started_ns) - int(enqueued_ns)) / 1e6,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            or not math.isclose(
                float(latency_ms),
                (int(finished_ns) - int(started_ns)) / 1e6,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            or (outcome == "completed" and status != 200)
            or (outcome == "5xx" and int(status) < 500)
            or (outcome == "error" and (status == 200 or int(status) >= 500))
        ):
            raise X1ResumeTestbedError("x1_resume_private_terminal_record_binding")
        terminal_by_id[request_id] = dict(terminal)
        if terminal.get("outcome") == "completed":
            all_completed[str(terminal.get("model_id"))] += 1
        if terminal.get("phase") == "measured":
            measured_projected.append({key: terminal[key] for key in required_fields})
    accepted_ids = {
        request_id for request_id, item in ledger_by_id.items() if item["decision"] == "accepted"
    }
    if set(terminal_by_id) != accepted_ids:
        raise X1ResumeTestbedError("x1_resume_private_terminal_identity_set")

    request_ids: set[str] = set()
    completed: Counter[str] = Counter()
    for record in records:
        if not isinstance(record, Mapping) or set(record) != required_fields:
            raise X1ResumeTestbedError("x1_resume_private_attempt_record_schema")
        request_id = record.get("request_id")
        model_id = record.get("model_id")
        outcome = record.get("outcome")
        status = record.get("status")
        worker_id = record.get("worker_id")
        timestamps = [record.get(key) for key in ("enqueued_ns", "started_ns", "finished_ns")]
        if (
            not isinstance(request_id, str)
            or not request_id
            or request_id in request_ids
            or model_id not in EXPECTED_MODELS
            or outcome not in {"completed", "5xx", "error"}
            or type(status) is not int
            or type(worker_id) is not int
            or worker_id < 0
            or any(type(value) is not int for value in timestamps)
        ):
            raise X1ResumeTestbedError("x1_resume_private_attempt_record_identity")
        request_ids.add(request_id)
        enqueued_ns, started_ns, finished_ns = (int(value) for value in timestamps)
        queue_wait_ms = record.get("queue_wait_ms")
        latency_ms = record.get("latency_ms")
        if (
            not measurement_start_ns <= enqueued_ns < measurement_end_ns
            or started_ns < enqueued_ns
            or finished_ns <= started_ns
            or not isinstance(queue_wait_ms, (int, float))
            or isinstance(queue_wait_ms, bool)
            or not isinstance(latency_ms, (int, float))
            or isinstance(latency_ms, bool)
            or not math.isfinite(float(queue_wait_ms))
            or not math.isfinite(float(latency_ms))
            or float(latency_ms) <= 0
            or not math.isclose(
                float(queue_wait_ms),
                (started_ns - enqueued_ns) / 1e6,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            or not math.isclose(
                float(latency_ms),
                (finished_ns - started_ns) / 1e6,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            or (outcome == "completed" and status != 200)
            or (outcome == "5xx" and status < 500)
            or (outcome == "error" and (status == 200 or status >= 500))
        ):
            raise X1ResumeTestbedError("x1_resume_private_attempt_record_timing")
        if outcome == "completed":
            completed[str(model_id)] += 1
    if sorted(records, key=lambda item: str(item["request_id"])) != sorted(
        measured_projected, key=lambda item: str(item["request_id"])
    ):
        raise X1ResumeTestbedError("x1_resume_private_measured_terminal_projection")
    offered = admission.get("offered")
    admitted = admission.get("admitted")
    rejected = admission.get("local_admission_rejected")
    measured_ledger = [item for item in ledger_by_id.values() if item["phase"] == "measured"]
    if (
        any(type(value) is not int for value in (offered, admitted, rejected))
        or min(int(offered), int(admitted), int(rejected)) < 0
        or int(offered) != int(admitted) + int(rejected)
        or int(admitted) != len(records)
        or int(offered) != len(measured_ledger)
        or int(admitted) != sum(item["decision"] == "accepted" for item in measured_ledger)
        or int(rejected) != sum(item["decision"] == "rejected" for item in measured_ledger)
    ):
        raise X1ResumeTestbedError("x1_resume_private_attempt_admission")
    admission_proof = {
        "issued_count": len(admission_ledger),
        "warmup_offered": sum(item["phase"] == "warmup" for item in ledger_by_id.values()),
        "measured_offered": len(measured_ledger),
        "measured_accepted": int(admitted),
        "measured_rejected": int(rejected),
        "measured_offered_by_model": {
            model_id: measured_offered_by_model[model_id] for model_id in EXPECTED_MODELS
        },
        "measured_accepted_by_model": {
            model_id: measured_accepted_by_model[model_id] for model_id in EXPECTED_MODELS
        },
        "measured_rejected_by_model": {
            model_id: measured_rejected_by_model[model_id] for model_id in EXPECTED_MODELS
        },
        "ledger_sha256": canonical_sha256(admission_ledger),
        "terminal_records_sha256": canonical_sha256(terminal_records),
    }
    return completed, all_completed, set(ledger_by_id), admission_proof


def _git_bytes(repository_root: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repository_root,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise X1ResumeTestbedError("x1_resume_source_git") from exc


def _source_blob_path(source_root: Path, raw_path: Any) -> tuple[str, Path]:
    if not isinstance(raw_path, str) or not raw_path:
        raise X1ResumeTestbedError("x1_resume_source_blob_path")
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise X1ResumeTestbedError(f"x1_resume_source_blob_path:{raw_path}")
    resolved_root = source_root.resolve()
    resolved = (resolved_root / relative).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise X1ResumeTestbedError(f"x1_resume_source_blob_containment:{raw_path}") from exc
    if not resolved.is_file():
        raise X1ResumeTestbedError(f"x1_resume_source_blob_missing:{raw_path}")
    return raw_path, resolved


def _validate_source_blob_collection(
    raw_items: Any,
    *,
    label: str,
    source_root: Path,
    repository_root: Path,
    revision: str,
) -> list[dict[str, str]]:
    if not isinstance(raw_items, list) or len(raw_items) != len(REQUIRED_SOURCE_BLOB_PATHS):
        raise X1ResumeTestbedError(f"x1_resume_source_blob_count:{label}")
    expected_keys = {
        "path",
        "source_revision",
        "blob_oid",
        "sha256",
        "working_sha256",
    }
    items: dict[str, dict[str, str]] = {}
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping) or set(raw_item) != expected_keys:
            raise X1ResumeTestbedError(f"x1_resume_source_blob_fields:{label}")
        path_value, path = _source_blob_path(source_root, raw_item.get("path"))
        if path_value in items:
            raise X1ResumeTestbedError(f"x1_resume_source_blob_duplicate:{label}:{path_value}")
        if raw_item.get("source_revision") != revision:
            raise X1ResumeTestbedError(f"x1_resume_source_blob_revision:{label}:{path_value}")
        try:
            source_prefix = source_root.relative_to(repository_root)
        except ValueError as exc:
            raise X1ResumeTestbedError(
                f"x1_resume_source_blob_repository:{label}:{path_value}"
            ) from exc
        repository_relative = (source_prefix / Path(path_value)).as_posix()
        blob_oid = (
            _git_bytes(repository_root, "rev-parse", f"{revision}:{repository_relative}")
            .decode("ascii")
            .strip()
        )
        git_bytes = _git_bytes(repository_root, "show", f"{revision}:{repository_relative}")
        git_sha256 = hashlib.sha256(git_bytes).hexdigest()
        working_sha256 = sha256_file(path)
        if (
            raw_item.get("blob_oid") != blob_oid
            or raw_item.get("sha256") != git_sha256
            or raw_item.get("working_sha256") != working_sha256
            or working_sha256 != git_sha256
        ):
            raise X1ResumeTestbedError(f"x1_resume_source_blob_identity:{label}:{path_value}")
        items[path_value] = {key: str(raw_item[key]) for key in expected_keys}
    if set(items) != set(REQUIRED_SOURCE_BLOB_PATHS):
        raise X1ResumeTestbedError(f"x1_resume_source_blob_set:{label}")
    return [items[path] for path in REQUIRED_SOURCE_BLOB_PATHS]


def _validate_source_provenance(
    payload: Mapping[str, Any],
    manifest: Mapping[str, Any],
    source_root: Path,
    *,
    config: X1ResumeConfig,
) -> None:
    source_identity = payload.get("source_identity")
    if not isinstance(source_identity, Mapping):
        raise X1ResumeTestbedError("x1_resume_source_identity")
    revision = str(source_identity.get("revision") or "")
    tree_sha = str(source_identity.get("tree_sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(r"[0-9a-f]{40}", tree_sha):
        raise X1ResumeTestbedError("x1_resume_source_identity")
    repository_root = Path(
        _git_bytes(source_root, "rev-parse", "--show-toplevel").decode().strip()
    ).resolve()
    resolved_source_root = source_root.resolve()
    try:
        resolved_source_root.relative_to(repository_root)
    except ValueError as exc:
        raise X1ResumeTestbedError("x1_resume_source_root_repository") from exc
    actual_tree = (
        _git_bytes(repository_root, "rev-parse", f"{revision}^{{tree}}").decode("ascii").strip()
    )
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", revision, "HEAD"],
        cwd=repository_root,
        check=False,
        capture_output=True,
    )
    environment = payload.get("environment")
    gpu_lease = environment.get("gpu_lease") if isinstance(environment, Mapping) else None
    if (
        ancestry.returncode != 0
        or actual_tree != tree_sha
        or manifest.get("source_revision") != revision
        or manifest.get("source_tree_sha") != tree_sha
        or not isinstance(gpu_lease, Mapping)
        or gpu_lease.get("source_commit") != revision
    ):
        raise X1ResumeTestbedError("x1_resume_source_binding")
    payload_blobs = _validate_source_blob_collection(
        payload.get("source_blobs"),
        label="payload",
        source_root=resolved_source_root,
        repository_root=repository_root,
        revision=revision,
    )
    manifest_blobs = _validate_source_blob_collection(
        manifest.get("source_blobs"),
        label="manifest",
        source_root=resolved_source_root,
        repository_root=repository_root,
        revision=revision,
    )
    if payload_blobs != manifest_blobs:
        raise X1ResumeTestbedError("x1_resume_source_blob_projection")
    config_blob = next(
        item for item in payload_blobs if item["path"] == DEFAULT_CONFIG_RELATIVE_PATH
    )
    expected_config_path = (resolved_source_root / DEFAULT_CONFIG_RELATIVE_PATH).resolve()
    if (
        config.path.resolve() != expected_config_path
        or config.sha256 != EXPECTED_CONFIG_SHA256
        or config_blob["sha256"] != config.sha256
        or config_blob["working_sha256"] != config.sha256
        or manifest.get("config_sha256") != config.sha256
        or payload.get("config_sha256") != config.sha256
    ):
        raise X1ResumeTestbedError("x1_resume_source_config_binding")


def validate_private_evidence(
    payload: Mapping[str, Any],
    *,
    config: X1ResumeConfig,
    private_suite_root: Path,
    model_repository_root: Path,
    source_root: Path,
    data_root: Path,
) -> dict[str, Any]:
    """Recompute private evidence bindings before a report is used for resume claims."""
    manifest_path = model_repository_root / "model-repository-manifest.json"
    try:
        manifest = load_canonical_json(manifest_path, label="model_repository_manifest")
    except (OSError, X1ResumeTestbedError) as exc:
        raise X1ResumeTestbedError("x1_resume_private_manifest") from exc
    environment = dict(payload.get("environment", {}))
    if sha256_file(manifest_path) != environment.get("repository_manifest_sha256"):
        raise X1ResumeTestbedError("x1_resume_private_manifest_digest")
    _validate_manifest_contract(manifest, config)
    validate_governed_source_bindings(manifest, data_root=data_root, config=config)
    _validate_source_provenance(payload, manifest, source_root, config=config)
    revision = str(dict(payload.get("source_identity", {})).get("revision") or "")
    _validate_environment_contract(payload, config=config, revision=revision)
    entries = validate_repository_entries(manifest, model_repository_root)
    validate_sample_payload(model_repository_root / "testbed-samples.json", config)
    if manifest.get("repository_sha256") != environment.get(
        "repository_sha256"
    ) or canonical_sha256(entries) != manifest.get("repository_sha256"):
        raise X1ResumeTestbedError("x1_resume_private_repository_aggregate")
    profile_identities = dict(manifest.get("profile_identities", {}))
    model_identities = dict(manifest.get("model_identities", {}))
    for profile in ("off", "on"):
        selected = [
            item for item in entries if str(item.get("path", "")).startswith(f"batch-{profile}/")
        ]
        if dict(profile_identities.get(profile, {})) != {
            "entry_count": len(selected),
            "repository_sha256": canonical_sha256(selected),
        }:
            raise X1ResumeTestbedError(f"x1_resume_private_profile:{profile}")
        selected_by_path = {str(item.get("path")): item for item in selected}
        for model_id in EXPECTED_MODELS:
            artifact = dict(selected_by_path.get(f"batch-{profile}/{model_id}/1/model.pt", {}))
            model_config = dict(
                selected_by_path.get(f"batch-{profile}/{model_id}/config.pbtxt", {})
            )
            if dict(model_identities.get(f"{profile}:{model_id}", {})) != {
                "artifact_sha256": artifact.get("sha256"),
                "config_sha256": model_config.get("sha256"),
            } or not all((artifact, model_config)):
                raise X1ResumeTestbedError(f"x1_resume_private_profile_model:{profile}:{model_id}")
    q0_evidence = dict(dict(payload.get("profile_evidence", {})).get("q0_isolated", {}))
    trace_path = _bound_file(private_suite_root, dict(q0_evidence.get("trace", {})), "q0_trace")
    log_path = _bound_file(private_suite_root, dict(q0_evidence.get("log", {})), "q0_log")
    log_text = log_path.read_text(encoding="utf-8", errors="strict")
    trace_counts = triton_trace_compute_counts(trace_path)
    if trace_counts != q0_evidence.get("compute_start_counts"):
        raise X1ResumeTestbedError("x1_resume_private_q0_trace_counts")

    for item in payload.get("q0", []):
        model_id = str(item.get("model_id") or "")
        expected = dict(model_identities.get(f"off:{model_id}", {}))
        artifact_path = model_repository_root / f"batch-off/{model_id}/1/model.pt"
        config_path = model_repository_root / f"batch-off/{model_id}/config.pbtxt"
        if (
            not artifact_path.is_file()
            or not config_path.is_file()
            or sha256_file(artifact_path) != item.get("artifact_sha256")
            or sha256_file(config_path) != item.get("config_sha256")
            or expected
            != {
                "artifact_sha256": item.get("artifact_sha256"),
                "config_sha256": item.get("config_sha256"),
            }
        ):
            raise X1ResumeTestbedError(f"x1_resume_private_q0_model:{model_id}")
        raw_path = _bound_file(private_suite_root, dict(item.get("private_raw", {})), "q0_raw")
        raw = load_canonical_json(raw_path, label=f"q0:{model_id}")
        gpu_samples = raw.get("gpu_samples")
        gpu_summary = validate_gpu_samples(gpu_samples, config, label=f"q0:{model_id}")
        before_metrics = _triton_metrics_for_model(str(raw.get("metrics_before", "")), model_id)
        after_metrics = _triton_metrics_for_model(str(raw.get("metrics_after", "")), model_id)
        metric_deltas = _triton_metric_deltas(before_metrics, after_metrics, model_id=model_id)
        gpu_lines = [str(line) for line in raw.get("gpu_log_lines", [])]
        request_count = raw.get("isolated_request_count")
        request_batch_size = item.get("request_batch_size")
        if (
            raw.get("model_id") != model_id
            or canonical_sha256(raw.get("metrics_before", "")) != item.get("metrics_before_sha256")
            or canonical_sha256(raw.get("metrics_after", "")) != item.get("metrics_after_sha256")
            or gpu_summary["sample_count"] != item.get("isolated_gpu_sample_count")
            or gpu_summary["busy_sample_count"] != item.get("isolated_gpu_busy_samples")
            or raw.get("isolated_request_count") != item.get("isolated_request_count")
            or type(request_count) is not int
            or type(request_batch_size) is not int
            or request_count < 64
            or request_batch_size != config.q0_request_batch_size
            or metric_deltas["success"] != request_count
            or metric_deltas["inference_count"] != request_count * request_batch_size
            or metric_deltas["execution_count"] != request_count
            or metric_deltas["compute_us"] <= 0
            or any(not math.isfinite(float(value)) or value < 0 for value in metric_deltas.values())
            or metric_deltas["success"] != item.get("triton_success_delta")
            or metric_deltas["compute_us"] != item.get("triton_compute_delta")
            or metric_deltas["inference_count"] != item.get("triton_inference_count_delta")
            or metric_deltas["execution_count"] != item.get("triton_execution_count_delta")
            or not triton_gpu_instance_exact(
                item.get("triton_config_readback"),
                model_id=model_id,
                input_width=next(
                    (model.input_width for model in config.models if model.model_id == model_id),
                    -1,
                ),
                dynamic_batching_enabled=False,
            )
            or [canonical_sha256(line) for line in gpu_lines] != item.get("gpu_log_line_sha256")
            or any(line not in log_text.splitlines() for line in gpu_lines)
            or trace_counts.get(model_id, 0)
            != int(item.get("triton_trace_compute_start_count", -1))
        ):
            raise X1ResumeTestbedError(f"x1_resume_private_q0_raw:{model_id}")
    if any(
        trace_counts.get(str(item.get("model_id")), 0)
        != int(item.get("triton_trace_compute_start_count", -1))
        for item in payload.get("q0", [])
    ):
        raise X1ResumeTestbedError("x1_resume_private_q0_trace_binding")

    for profile in ("off", "on"):
        profile_evidence = dict(dict(payload.get("profile_evidence", {})).get(profile, {}))
        _bound_file(
            private_suite_root,
            dict(profile_evidence.get("log", {})),
            f"profile_log:{profile}",
        )

    cells_by_id = {cell.cell_id: cell for cell in config.cells}
    suite_id = str(payload.get("suite_id") or "")
    observed_attempt_ids: set[str] = set()
    observed_private_paths: set[str] = set()
    observed_request_ids: set[str] = set()
    for item in payload.get("runs", []):
        attempt_id = str(item.get("attempt_id") or "")
        cell_id = str(item.get("cell_id") or "")
        repetition = item.get("repetition")
        expected_cell = cells_by_id.get(cell_id)
        private_identity = dict(item.get("private_raw", {}))
        private_path = str(private_identity.get("path") or "")
        if (
            expected_cell is None
            or type(repetition) is not int
            or not _attempt_id_pattern(suite_id, cell_id, repetition).fullmatch(attempt_id)
            or attempt_id in observed_attempt_ids
            or private_path != f"attempts/{attempt_id}.json"
            or private_path in observed_private_paths
        ):
            raise X1ResumeTestbedError("x1_resume_private_attempt_identity")
        observed_attempt_ids.add(attempt_id)
        observed_private_paths.add(private_path)
        raw_path = _bound_file(private_suite_root, private_identity, "attempt")
        raw = load_canonical_json(raw_path, label=f"attempt:{attempt_id}")
        raw_cell = dict(raw.get("cell", {}))
        raw_mix = {
            str(key): float(value) for key, value in dict(raw_cell.get("model_mix", {})).items()
        }
        window = dict(raw.get("measurement_window", {}))
        admission = dict(raw.get("admission", {}))
        records = raw.get("records")
        if (
            set(window) != {"start_ns", "end_ns", "seconds"}
            or type(window.get("start_ns")) is not int
            or type(window.get("end_ns")) is not int
            or type(window.get("seconds")) is not int
            or window.get("seconds") != config.measurement_seconds
            or int(window["end_ns"]) - int(window["start_ns"])
            != config.measurement_seconds * 1_000_000_000
            or int(window["start_ns"]) <= 0
        ):
            raise X1ResumeTestbedError(f"x1_resume_private_attempt_window:{item.get('attempt_id')}")
        (
            completed_by_model,
            all_completed_by_model,
            attempt_request_ids,
            admission_proof,
        ) = _validate_attempt_records(
            records,
            raw.get("terminal_records"),
            raw.get("admission_ledger"),
            attempt_id=attempt_id,
            model_mix=raw_mix,
            warmup_seconds=config.warmup_seconds,
            measurement_start_ns=int(window["start_ns"]),
            measurement_end_ns=int(window["end_ns"]),
            admission=admission,
        )
        if observed_request_ids.intersection(attempt_request_ids):
            raise X1ResumeTestbedError("x1_resume_private_global_request_identity")
        observed_request_ids.update(attempt_request_ids)
        gpu_summary = validate_gpu_samples(
            raw.get("gpu_samples"), config, label=f"attempt:{item.get('attempt_id')}"
        )
        recomputed_metrics = summarize_requests(
            offered=int(admission.get("offered", -1)),
            admitted=int(admission.get("admitted", -1)),
            local_admission_rejected=int(admission.get("local_admission_rejected", -1)),
            records=records,
            measurement_seconds=float(window.get("seconds", 0)),
            measurement_start_ns=int(window.get("start_ns", -1)),
            measurement_end_ns=int(window.get("end_ns", -1)),
            drain_seconds=float(raw.get("drain_seconds", float("nan"))),
            model_mix=raw_mix,
        )
        before_text = str(raw.get("metrics_before", ""))
        after_text = str(raw.get("metrics_after", ""))
        recomputed_deltas = {}
        for model_id in EXPECTED_MODELS:
            before = _triton_metrics_for_model(before_text, model_id)
            after = _triton_metrics_for_model(after_text, model_id)
            recomputed_deltas[model_id] = _triton_metric_deltas(before, after, model_id=model_id)
            values = recomputed_deltas[model_id]
            terminal_completed = all_completed_by_model[model_id]
            if (
                float(values["success"]) != terminal_completed
                or float(values["inference_count"]) != terminal_completed
                or float(values["execution_count"]) > float(values["inference_count"])
                or (terminal_completed > 0 and float(values["execution_count"]) <= 0)
                or (terminal_completed > 0 and float(values["compute_us"]) <= 0)
                or (terminal_completed == 0 and any(float(value) != 0 for value in values.values()))
            ):
                raise X1ResumeTestbedError(
                    f"x1_resume_private_attempt_triton_arithmetic:{item.get('attempt_id')}:{model_id}"
                )
        active_models = {model_id for model_id, fraction in raw_mix.items() if fraction > 0}
        inference_count = sum(
            recomputed_deltas[model_id]["inference_count"] for model_id in active_models
        )
        execution_count = sum(
            recomputed_deltas[model_id]["execution_count"] for model_id in active_models
        )
        formed_batch_size = inference_count / execution_count if execution_count > 0 else 0.0
        if not 0 < formed_batch_size <= 32:
            raise X1ResumeTestbedError(
                f"x1_resume_private_attempt_batch_arithmetic:{item.get('attempt_id')}"
            )
        recomputed_batching = {
            "inference_count_delta": inference_count,
            "execution_count_delta": execution_count,
            "formed_mean_batch_size": formed_batch_size,
            "formed_batch_observed": formed_batch_size > 1.0,
        }
        recomputed_overlap = request_interval_overlap(
            records,
            measurement_start_ns=int(window["start_ns"]),
            measurement_end_ns=int(window["end_ns"]),
        )
        if (
            raw.get("attempt_id") != item.get("attempt_id")
            or raw_cell.get("cell_id") != item.get("cell_id")
            or int(raw.get("repetition", -1)) != int(item.get("repetition", -2))
            or raw_mix != item.get("model_mix")
            or expected_cell is None
            or raw_cell
            != {
                "cell_id": expected_cell.cell_id,
                "repetitions": expected_cell.repetitions,
                "model_mix": dict(expected_cell.model_mix),
                "batching": expected_cell.batching,
                "client_lanes": expected_cell.client_lanes,
                "client_workers": expected_cell.client_workers,
                "analytical_roles": list(expected_cell.analytical_roles),
            }
            or recomputed_metrics != item.get("metrics")
            or admission_proof != item.get("admission_proof")
            or raw.get("admission_proof") != admission_proof
            or recomputed_deltas != item.get("triton_metric_deltas")
            or recomputed_overlap != item.get("cross_model_request_overlap")
            or recomputed_batching != item.get("batching_proof")
            or {
                "sample_count": gpu_summary["sample_count"],
                "utilization_max_percent": gpu_summary["utilization_max_percent"],
                "vram_max_mib": gpu_summary["vram_max_mib"],
            }
            != item.get("gpu")
            or raw.get("metrics") != item.get("metrics")
            or raw.get("triton_metric_deltas") != item.get("triton_metric_deltas")
            or raw.get("cross_model_request_overlap") != item.get("cross_model_request_overlap")
            or raw.get("batching_proof") != item.get("batching_proof")
        ):
            raise X1ResumeTestbedError(f"x1_resume_private_attempt:{item.get('attempt_id')}")
    cleanup_path = _bound_file(
        private_suite_root, dict(payload.get("cleanup_evidence", {})), "cleanup"
    )
    cleanup_raw = load_canonical_json(cleanup_path, label="cleanup")
    if cleanup_raw.get("cleanup") != payload.get("cleanup") or canonical_sha256(
        cleanup_raw.get("final_checks", {})
    ) != dict(payload.get("cleanup_evidence", {})).get("final_checks_sha256"):
        raise X1ResumeTestbedError("x1_resume_private_cleanup")
    _validate_cleanup_evidence(
        payload,
        cleanup_raw.get("final_checks"),
        config=config,
    )
    cleanup_identity = dict(payload.get("cleanup_evidence", {}))
    released_identity = dict(cleanup_identity.get("released_gpu_lease") or {})
    archive_identity = dict(cleanup_identity.get("released_gpu_lease_archive") or {})
    released_path = _bound_file(private_suite_root, released_identity, "released_gpu_lease")
    released_payload = load_canonical_json(released_path, label="released_gpu_lease")
    archive_path = _bound_file(private_suite_root, archive_identity, "released_gpu_lease_archive")
    archived_payload = strict_json_loads(
        archive_path.read_bytes(), label="released_gpu_lease_archive"
    )
    _validate_released_lease(
        payload,
        released_payload,
        archived_payload,
        released_identity,
        archive_identity,
    )
    return {
        "private_artifacts_valid": True,
        "private_attempt_count": len(payload.get("runs", [])),
        "repository_entry_count": len(entries),
    }


def validate_evidence(
    payload: Mapping[str, Any],
    config: X1ResumeConfig,
    *,
    private_suite_root: Path | None = None,
    model_repository_root: Path | None = None,
    source_root: Path | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        errors.append("schema_version")
    if payload.get("claim_class") != CLAIM_CLASS or payload.get("credit") != CREDIT:
        errors.append("claim_class")
    if payload.get("canonical_x1") is not False or payload.get("acceptance_credit") is not False:
        errors.append("canonical_or_credit")
    if payload.get("config_sha256") != config.sha256:
        errors.append("config_sha256")
    if payload.get("claim_boundary") != config.claim_boundary:
        errors.append("claim_boundary")
    status = str(payload.get("status") or "")
    if status not in {"running", "complete", "failed"}:
        errors.append("status")
    q0 = payload.get("q0", [])
    runs = payload.get("runs", [])
    if not isinstance(q0, list) or not isinstance(runs, list):
        errors.append("run_collections")
        q0, runs = [], []
    if status == "complete":
        suite_id = str(payload.get("suite_id") or "")
        if not re.fullmatch(r"x1-resume-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", suite_id):
            errors.append("suite_id")
        if Counter(str(item.get("model_id")) for item in q0) != Counter(EXPECTED_MODELS):
            errors.append("q0_model_set")
        for item in q0:
            if (
                item.get("cuda_activity_observed") is not True
                or item.get("cpu_fallback_observed") is not False
            ):
                errors.append("q0_cuda_contract")
            if item.get("triton_gpu_instance_proof") is not True:
                errors.append("q0_gpu_instance_proof")
            model_id = str(item.get("model_id") or "")
            if not triton_gpu_instance_exact(
                item.get("triton_config_readback"),
                model_id=model_id,
                input_width=next(
                    (model.input_width for model in config.models if model.model_id == model_id),
                    -1,
                ),
                dynamic_batching_enabled=False,
            ):
                errors.append("q0_gpu_instance_readback")
            if float(item.get("triton_compute_delta", 0)) <= 0:
                errors.append("q0_compute_delta")
            request_count = int(item.get("isolated_request_count", 0))
            request_batch_size = int(item.get("request_batch_size", 0))
            if (
                request_batch_size != config.q0_request_batch_size
                or float(item.get("triton_success_delta", -1)) != request_count
                or float(item.get("triton_inference_count_delta", -1))
                != request_count * request_batch_size
                or float(item.get("triton_execution_count_delta", -1)) != request_count
            ):
                errors.append("q0_request_metric_arithmetic")
            if int(item.get("isolated_gpu_busy_samples", 0)) <= 0:
                errors.append("q0_gpu_busy_samples")
            if int(item.get("isolated_request_count", 0)) < 64:
                errors.append("q0_trace_sampling_opportunity")
            if int(item.get("triton_trace_compute_start_count", 0)) <= 0:
                errors.append("q0_trace_compute_start")
            if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("artifact_sha256") or "")):
                errors.append("q0_artifact_sha256")
            if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("config_sha256") or "")):
                errors.append("q0_config_sha256")
        observed = Counter(
            (str(item.get("cell_id")), int(item.get("repetition", 0))) for item in runs
        )
        expected = Counter(
            (cell.cell_id, repetition)
            for cell in config.cells
            for repetition in range(1, cell.repetitions + 1)
        )
        if observed != expected:
            errors.append("physical_run_matrix")
        if len(runs) != config.expected_physical_runs:
            errors.append("physical_run_count")
        attempt_ids: set[str] = set()
        for run in runs:
            attempt_id = str(run.get("attempt_id") or "")
            cell_id = str(run.get("cell_id") or "")
            repetition = run.get("repetition")
            if (
                type(repetition) is not int
                or not _attempt_id_pattern(suite_id, cell_id, repetition).fullmatch(attempt_id)
                or attempt_id in attempt_ids
            ):
                errors.append("attempt_identity")
            attempt_ids.add(attempt_id)
            metrics = run.get("metrics", {})
            if not isinstance(metrics, Mapping):
                errors.append("metrics_mapping")
                continue
            offered = int(metrics.get("offered", -1))
            admitted = int(metrics.get("admitted", -1))
            local_rejected = int(metrics.get("local_admission_rejected", -1))
            window_completed = int(metrics.get("window_completed", -1))
            cohort_completed = int(metrics.get("admitted_cohort_completed", -1))
            cohort_5xx = int(metrics.get("admitted_cohort_http_5xx", -1))
            cohort_errors = int(metrics.get("admitted_cohort_other_errors", -1))
            loss = int(metrics.get("loss", -1))
            if (
                offered != admitted + local_rejected
                or admitted != cohort_completed + cohort_5xx + cohort_errors + loss
                or int(metrics.get("tail_completed", -1)) != cohort_completed - window_completed
                or window_completed > cohort_completed
            ):
                errors.append("request_arithmetic")
            if (
                int(metrics.get("duplicates", -1)) != 0
                or cohort_5xx != 0
                or cohort_errors != 0
                or loss != 0
                or int(metrics.get("window_http_5xx", -1)) != 0
                or int(metrics.get("window_other_errors", -1)) != 0
            ):
                errors.append("resume_success_errors_or_loss")
            per_model = dict(metrics.get("per_model", {}))
            if set(per_model) != set(EXPECTED_MODELS):
                errors.append("per_model_set")
            else:
                model_window_sum = sum(
                    int(dict(per_model[model_id]).get("window_completed", -1))
                    for model_id in EXPECTED_MODELS
                )
                model_cohort_sum = sum(
                    int(dict(per_model[model_id]).get("admitted_cohort_completed", -1))
                    for model_id in EXPECTED_MODELS
                )
                if model_window_sum != window_completed or model_cohort_sum != cohort_completed:
                    errors.append("per_model_arithmetic")
                for model_id in EXPECTED_MODELS:
                    model_metrics = dict(per_model[model_id])
                    expected_rate = int(model_metrics.get("window_completed", -1)) / max(
                        config.measurement_seconds, 1e-9
                    )
                    if not math.isclose(
                        float(model_metrics.get("throughput_rps", float("nan"))),
                        expected_rate,
                        rel_tol=1e-9,
                        abs_tol=1e-9,
                    ):
                        errors.append("per_model_throughput_recompute")
                    p99 = float(model_metrics.get("p99_ms", float("nan")))
                    if not math.isfinite(p99) or p99 < 0:
                        errors.append("per_model_percentile")
            throughput = float(metrics.get("throughput_rps", float("nan")))
            actual_offered_rps = float(metrics.get("actual_offered_rps", float("nan")))
            drain_seconds = float(metrics.get("drain_seconds", float("nan")))
            if (
                not math.isfinite(throughput)
                or throughput < 0
                or not math.isclose(
                    throughput,
                    window_completed / max(config.measurement_seconds, 1e-9),
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                )
                or not math.isclose(
                    actual_offered_rps,
                    offered / max(config.measurement_seconds, 1e-9),
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                )
                or not math.isfinite(drain_seconds)
                or not 0 <= drain_seconds <= config.cleanup_timeout_seconds
            ):
                errors.append("window_metric_recompute")
            offered_rate_attainment = actual_offered_rps / max(config.offered_rps, 1e-9)
            if (
                not math.isfinite(offered_rate_attainment)
                or offered_rate_attainment < config.minimum_offered_rate_attainment
                or offered_rate_attainment > 1 + config.matched_load_relative_tolerance
            ):
                errors.append("offered_load_attainment")
            for percentile_key in ("latency_ms", "queue_wait_ms"):
                percentiles = dict(metrics.get(percentile_key, {}))
                values = [
                    float(percentiles.get(name, float("nan"))) for name in ("p50", "p95", "p99")
                ]
                if not all(
                    math.isfinite(value) and value >= 0 for value in values
                ) or values != sorted(values):
                    errors.append("percentile_order")
            fairness_values = [
                float(metrics.get("raw_throughput_jain_fairness", float("nan"))),
                float(metrics.get("normalized_attainment_jain_fairness", float("nan"))),
            ]
            if not all(math.isfinite(value) and 0 <= value <= 1 for value in fairness_values):
                errors.append("fairness_bounds")
            if run.get("cpu_fallback_observed") is not False:
                errors.append("run_cpu_fallback")
            if run.get("triton_execution_proved") is not True:
                errors.append("run_triton_execution")
            cell = next((item for item in config.cells if item.cell_id == run.get("cell_id")), None)
            overlap_required = bool(
                cell
                and cell.client_workers > 1
                and sum(value > 0 for value in cell.model_mix.values()) > 1
            )
            if cell:
                topology = dict(run.get("client_topology", {}))
                load_contract = dict(run.get("load_contract", {}))
                if (
                    run.get("batching") != cell.batching
                    or topology.get("lanes") != cell.client_lanes
                    or topology.get("workers") != cell.client_workers
                    or run.get("model_mix") != dict(cell.model_mix)
                    or load_contract
                    != {
                        "target_offered_rps": config.offered_rps,
                        "minimum_offered_rate_attainment": config.minimum_offered_rate_attainment,
                        "matched_load_relative_tolerance": config.matched_load_relative_tolerance,
                        "warmup_seconds": config.warmup_seconds,
                        "measurement_seconds": config.measurement_seconds,
                    }
                ):
                    errors.append("run_frozen_load_topology")
                if set(per_model) == set(EXPECTED_MODELS):
                    if any(
                        int(dict(per_model[model_id]).get("window_completed", 0)) <= 0
                        for model_id, fraction in cell.model_mix.items()
                        if fraction > 0
                    ):
                        errors.append("active_model_window_progress")
                    raw_rates = [
                        float(dict(per_model[model_id])["throughput_rps"])
                        for model_id in EXPECTED_MODELS
                    ]
                    target_total_rps = offered / max(config.measurement_seconds, 1e-9)
                    attainment = [
                        float(dict(per_model[model_id])["throughput_rps"])
                        / max(float(cell.model_mix.get(model_id, 0)) * target_total_rps, 1e-9)
                        for model_id in EXPECTED_MODELS
                        if float(cell.model_mix.get(model_id, 0)) > 0
                    ]
                    if not math.isclose(
                        fairness_values[0], jain_fairness(raw_rates), rel_tol=1e-9, abs_tol=1e-9
                    ) or not math.isclose(
                        fairness_values[1],
                        jain_fairness(attainment),
                        rel_tol=1e-9,
                        abs_tol=1e-9,
                    ):
                        errors.append("fairness_recompute")
            if overlap_required and (
                run.get("cross_model_request_overlap_required") is not True
                or dict(run.get("cross_model_request_overlap", {})).get("observed") is not True
            ):
                errors.append("cross_model_request_overlap")
            if run.get("cell_id") == "balanced-concurrent-batch-on" and (
                dict(run.get("batching_proof", {})).get("formed_batch_observed") is not True
                or float(dict(run.get("batching_proof", {})).get("formed_mean_batch_size", 0))
                <= 1.0
            ):
                errors.append("batch_not_formed")
            if run.get("cell_id") == "hot-dlrm-l2w4":
                progress = dict(metrics.get("per_model", {}))
                if any(
                    int(dict(progress.get(model_id, {})).get("window_completed", 0)) <= 0
                    for model_id in EXPECTED_MODELS[:-1]
                ):
                    errors.append("hot_non_hot_progress")
        comparison_cells = (
            "balanced-serial",
            "balanced-concurrent-batch-off",
            "balanced-concurrent-batch-on",
        )
        comparison_rates = {
            cell_id: [
                float(dict(run.get("metrics", {})).get("actual_offered_rps", float("nan")))
                for run in runs
                if run.get("cell_id") == cell_id
            ]
            for cell_id in comparison_cells
        }
        tolerance_rps = config.offered_rps * config.matched_load_relative_tolerance
        if any(
            not rates
            or not all(math.isfinite(rate) for rate in rates)
            or max(rates) - min(rates) > tolerance_rps
            for rates in comparison_rates.values()
        ):
            errors.append("matched_load_repetition_tolerance")
        if all(comparison_rates.values()):
            comparison_medians = [statistics.median(rates) for rates in comparison_rates.values()]
            if max(comparison_medians) - min(comparison_medians) > tolerance_rps:
                errors.append("matched_load_median_tolerance")
    cleanup = payload.get("cleanup", {})
    if status == "complete":
        required_cleanup_true = (
            "container_absent",
            "ports_absent",
            "gpu_lease_absent",
            "b0_identity_restored",
            "b0_cuda_restored",
            "queue_active_zero",
            "queue_leased_zero",
            "queue_outcome_unknown_zero",
            "gpu_identity_restored",
            "gpu_vram_restored",
            "prometheus_5_of_5",
            "prometheus_exact_jobs_restored",
        )
        if (
            not isinstance(cleanup, Mapping)
            or any(cleanup.get(key) is not True for key in required_cleanup_true)
            or cleanup.get("triton_gpu_process_residue") != []
            or cleanup.get("errors") != []
        ):
            errors.append("cleanup")
        cleanup_evidence = dict(payload.get("cleanup_evidence", {}))
        if (
            not cleanup_evidence.get("path")
            or int(cleanup_evidence.get("bytes", 0)) <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(cleanup_evidence.get("sha256") or ""))
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(cleanup_evidence.get("final_checks_sha256") or "")
            )
            or not isinstance(cleanup_evidence.get("released_gpu_lease"), Mapping)
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(dict(cleanup_evidence.get("released_gpu_lease", {})).get("sha256") or ""),
            )
            or not isinstance(cleanup_evidence.get("released_gpu_lease_archive"), Mapping)
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(
                    dict(cleanup_evidence.get("released_gpu_lease_archive", {})).get("sha256") or ""
                ),
            )
        ):
            errors.append("cleanup_evidence")
        profiler = payload.get("profiler", {})
        if not isinstance(profiler, Mapping) or profiler.get("kernel_overlap_proved") is not False:
            errors.append("profiler_claim_boundary")
    if errors:
        raise X1ResumeTestbedError("x1_resume_evidence_invalid:" + ",".join(sorted(set(errors))))
    result = {
        "valid": True,
        "status": status,
        "claim_class": CLAIM_CLASS,
        "credit": CREDIT,
        "q0_count": len(q0),
        "physical_run_count": len(runs),
        "expected_physical_run_count": config.expected_physical_runs,
    }
    private_paths = (private_suite_root, model_repository_root, source_root, data_root)
    if any(value is not None for value in private_paths):
        if any(value is None for value in private_paths):
            raise X1ResumeTestbedError("x1_resume_private_validation_paths_incomplete")
        result.update(
            validate_private_evidence(
                payload,
                config=config,
                private_suite_root=private_suite_root,
                model_repository_root=model_repository_root,
                source_root=source_root,
                data_root=data_root,
            )
        )
    return result


def generate_report(
    payload: Mapping[str, Any],
    config: X1ResumeConfig,
    *,
    evidence_path: Path,
    private_suite_root: Path,
    model_repository_root: Path,
    source_root: Path,
    data_root: Path,
) -> dict[str, Any]:
    evidence_payload = load_canonical_json(evidence_path, label="evidence")
    if canonical(evidence_payload) != canonical(payload):
        raise X1ResumeTestbedError("x1_resume_report_evidence_payload")
    evidence_file_sha256 = sha256_file(evidence_path)
    validation = validate_evidence(
        payload,
        config,
        private_suite_root=private_suite_root,
        model_repository_root=model_repository_root,
        source_root=source_root,
        data_root=data_root,
    )
    if validation["status"] != "complete":
        raise X1ResumeTestbedError("x1_resume_report_requires_complete_evidence")
    runs = list(payload["runs"])
    source_identity = dict(payload.get("source_identity", {}))
    cleanup_evidence = dict(payload.get("cleanup_evidence", {}))
    private_marker = {
        "private_artifacts_valid": validation.get("private_artifacts_valid"),
        "private_attempt_count": validation.get("private_attempt_count"),
        "repository_entry_count": validation.get("repository_entry_count"),
        "source_revision": source_identity.get("revision"),
        "source_tree_sha": source_identity.get("tree_sha"),
    }

    def summarize_distribution(values: Sequence[float]) -> dict[str, Any]:
        return {
            "n": len(values),
            "median": statistics.median(values),
            "min": min(values),
            "max": max(values),
        }

    def distribution_for(cell_id: str, field: str) -> dict[str, Any]:
        values = [
            float(dict(item["metrics"])[field]) for item in runs if item["cell_id"] == cell_id
        ]
        return summarize_distribution(values)

    serial_distribution = distribution_for("balanced-serial", "throughput_rps")
    concurrent_distribution = distribution_for("balanced-concurrent-batch-off", "throughput_rps")
    batch_on_distribution = distribution_for("balanced-concurrent-batch-on", "throughput_rps")
    offered_load_distributions = {
        cell_id: distribution_for(cell_id, "actual_offered_rps")
        for cell_id in (
            "balanced-serial",
            "balanced-concurrent-batch-off",
            "balanced-concurrent-batch-on",
        )
    }
    serial = float(serial_distribution["median"])
    concurrent = float(concurrent_distribution["median"])
    batch_on = float(batch_on_distribution["median"])
    batch_on_runs = [item for item in runs if item["cell_id"] == "balanced-concurrent-batch-on"]
    batch_size_distribution = summarize_distribution(
        [float(item["batching_proof"]["formed_mean_batch_size"]) for item in batch_on_runs]
    )
    batch_on_mean_size = float(batch_size_distribution["median"])
    hot_runs = [item for item in runs if item["cell_id"] == "hot-dlrm-l2w4"]
    hot_fairness_distribution = summarize_distribution(
        [float(item["metrics"]["normalized_attainment_jain_fairness"]) for item in hot_runs]
    )
    hot_fairness = float(hot_fairness_distribution["median"])
    concurrent_delta = ((concurrent / serial) - 1.0) * 100 if serial > 0 else 0.0
    batching_delta = ((batch_on / concurrent) - 1.0) * 100 if concurrent > 0 else 0.0
    service_errors = sum(
        int(item["metrics"]["admitted_cohort_http_5xx"])
        + int(item["metrics"]["admitted_cohort_other_errors"])
        + int(item["metrics"]["loss"])
        for item in runs
    )
    admission_rejections = sum(int(item["metrics"]["local_admission_rejected"]) for item in runs)
    bullet = (
        "Built and measured a preliminary single-node Triton/RTX 4080 testbed for four "
        "named seeded CUDA test models using governed HIGGS/Criteo inputs across "
        f"{len(runs)} physical runs; fixed-window balanced concurrent throughput was n=3 median "
        f"{concurrent:.2f} req/s "
        f"[{concurrent_distribution['min']:.2f}, {concurrent_distribution['max']:.2f}] "
        f"({concurrent_delta:+.1f}% vs serial median); batch-on throughput was n=3 median "
        f"{batch_on:.2f} req/s [{batch_on_distribution['min']:.2f}, "
        f"{batch_on_distribution['max']:.2f}] ({batching_delta:+.1f}% vs batch-off) and formed "
        f"mean batch size median {batch_on_mean_size:.2f} "
        f"[{batch_size_distribution['min']:.2f}, {batch_size_distribution['max']:.2f}]; "
        f"and the 70% hot-model mix observed n=3 median normalized-attainment Jain fairness "
        f"{hot_fairness:.3f} [{hot_fairness_distribution['min']:.3f}, "
        f"{hot_fairness_distribution['max']:.3f}]; "
        f"service errors/loss={service_errors}, local admission rejections={admission_rejections}. "
        "The Criteo DLRM-lite path used deterministic seeded test parameters and makes no "
        "training-quality or model-accuracy claim."
    )
    return {
        "schema_version": "evm.s8_v4.x1_resume_report.v1",
        "claim_class": CLAIM_CLASS,
        "credit": CREDIT,
        "evidence_suite_id": payload.get("suite_id"),
        "provenance": {
            "evidence_canonical_payload_sha256": canonical_sha256(payload),
            "evidence_canonical_file_sha256": evidence_file_sha256,
            "evidence_file_sha256": evidence_file_sha256,
            "config_sha256": config.sha256,
            "source_revision": source_identity.get("revision"),
            "source_tree_sha": source_identity.get("tree_sha"),
            "cleanup_evidence_sha256": cleanup_evidence.get("sha256"),
            "private_validation": private_marker,
            "private_validation_marker_sha256": canonical_sha256(private_marker),
        },
        "measured": {
            "physical_runs": len(runs),
            "throughput_scope": "fixed 30-second measurement-window completions",
            "offered_load_contract": {
                "target_rps": config.offered_rps,
                "minimum_attainment": config.minimum_offered_rate_attainment,
                "matched_relative_tolerance": config.matched_load_relative_tolerance,
                "comparison_distributions": offered_load_distributions,
            },
            "serial_throughput_rps": serial_distribution,
            "concurrent_throughput_rps": concurrent_distribution,
            "concurrent_vs_serial_percent": concurrent_delta,
            "batch_on_throughput_rps": batch_on_distribution,
            "batch_on_vs_off_percent": batching_delta,
            "batch_on_formed_mean_batch_size": batch_size_distribution,
            "hot_mix_normalized_attainment_jain_fairness": hot_fairness_distribution,
            "service_errors_or_loss": service_errors,
            "local_admission_rejections": admission_rejections,
            "criteo_dlrm_lite_parameter_origin": "deterministic_seeded_testbed_initialization",
            "model_accuracy_claim": False,
            "topology_comparison_scope": "compound client-driver L1W1-to-L2W4 topology; not deployed API replica or service-worker causality",
        },
        "resume_bullets": [bullet],
        "mandatory_disclosure": config.claim_boundary,
    }


def validate_report_binding(
    report: Mapping[str, Any],
    payload: Mapping[str, Any],
    config: X1ResumeConfig,
    *,
    evidence_path: Path,
    private_suite_root: Path,
    model_repository_root: Path,
    source_root: Path,
    data_root: Path,
) -> None:
    regenerated = generate_report(
        payload,
        config,
        evidence_path=evidence_path,
        private_suite_root=private_suite_root,
        model_repository_root=model_repository_root,
        source_root=source_root,
        data_root=data_root,
    )
    if canonical(report) != canonical(regenerated):
        raise X1ResumeTestbedError("x1_resume_report_binding")


def validate_result_git_binding(
    *,
    evidence_path: Path,
    report_path: Path,
    source_root: Path,
    source_revision: str,
    result_revision: str = "HEAD",
) -> dict[str, Any]:
    repository_root = Path(
        _git_bytes(source_root, "rev-parse", "--show-toplevel").decode().strip()
    ).resolve()
    result_commit = (
        _git_bytes(repository_root, "rev-parse", f"{result_revision}^{{commit}}")
        .decode("ascii")
        .strip()
    )
    result_tree = (
        _git_bytes(repository_root, "rev-parse", f"{result_commit}^{{tree}}")
        .decode("ascii")
        .strip()
    )
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", source_revision, result_commit],
        cwd=repository_root,
        check=False,
        capture_output=True,
    )
    if ancestry.returncode != 0:
        raise X1ResumeTestbedError("x1_resume_result_source_ancestry")
    bindings: dict[str, Any] = {}
    for label, path in (("evidence", evidence_path), ("report", report_path)):
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(repository_root).as_posix()
        except ValueError as exc:
            raise X1ResumeTestbedError(f"x1_resume_result_path_containment:{label}") from exc
        git_bytes = _git_bytes(repository_root, "show", f"{result_commit}:{relative}")
        working_bytes = resolved.read_bytes()
        if git_bytes != working_bytes:
            raise X1ResumeTestbedError(f"x1_resume_result_working_blob:{label}")
        bindings[label] = {
            "path": relative,
            "blob_oid": (
                _git_bytes(repository_root, "rev-parse", f"{result_commit}:{relative}")
                .decode("ascii")
                .strip()
            ),
            "sha256": hashlib.sha256(git_bytes).hexdigest(),
        }
    return {
        "result_revision": result_commit,
        "result_tree_sha": result_tree,
        "source_revision": source_revision,
        "files": bindings,
    }
