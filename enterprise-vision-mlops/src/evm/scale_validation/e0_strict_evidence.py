from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evm.scale_validation.e0_runtime import E0RuntimeConfig, canonical, sha256_file


EXPECTED_REQUEST_COUNT = 100
STRICT_SCHEMA_VERSION = "evm.s8_v4.e0_strict_revalidation.v2"
PROMETHEUS_SCHEMA_VERSION = "evm.s8_v4.e0_prometheus_history.v1"
PROMETHEUS_QUERIES = {
    "target_up": 'up{job="evm-s8-v4-e0"}',
    "request_success": (
        'nv_inference_request_success{job="evm-s8-v4-e0",'
        'model="e0_cuda_linear",version="1"}'
    ),
    "inference_count": (
        'nv_inference_count{job="evm-s8-v4-e0",'
        'model="e0_cuda_linear",version="1"}'
    ),
    "gpu_memory": 'nv_gpu_memory_used_bytes{job="evm-s8-v4-e0"}',
}
ATTEMPT_CLEANUP_TRUE_FIELDS = (
    "container_absent",
    "port_listeners_absent",
    "gpu_context_absent",
    "lease_absent",
    "prometheus_target_absent",
    "temporary_kubernetes_resources_absent",
    "queue_active_zero",
    "queue_leased_zero",
    "queue_outcome_unknown_zero",
    "b0_ready",
    "b0_cuda_inference",
)
METRIC_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?\s+"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)$"
)
LABEL_PATTERN = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="((?:\\.|[^"\\])*)"')
CUPTI_KERNEL_PATTERN = re.compile(
    r"^E0_CUPTI_KERNEL\|name=(?P<name>[^|]+)\|start=(?P<start>\d+)"
    r"\|end=(?P<end>\d+)\|device=(?P<device>\d+)\|stream=(?P<stream>\d+)$"
)


class E0StrictEvidenceValidationError(RuntimeError):
    pass


def _finite(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise E0StrictEvidenceValidationError(f"{field}:not_numeric") from exc
    if not math.isfinite(number):
        raise E0StrictEvidenceValidationError(f"{field}:non_finite")
    return number


def _load_json(path: Path, *, field: str) -> dict[str, Any]:
    if not path.is_file():
        raise E0StrictEvidenceValidationError(f"{field}:missing")
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise E0StrictEvidenceValidationError(f"{field}:invalid_json") from exc
    if not isinstance(payload, dict):
        raise E0StrictEvidenceValidationError(f"{field}:not_object")
    return payload


def _parse_metric_samples(text: str, name: str) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = METRIC_PATTERN.fullmatch(line.strip())
        if match is None or match.group("name") != name:
            continue
        labels = {
            key: bytes(value, "utf-8").decode("unicode_escape")
            for key, value in LABEL_PATTERN.findall(match.group("labels") or "")
        }
        samples.append(
            {
                "labels": labels,
                "value": _finite(match.group("value"), field=f"metric:{name}"),
            }
        )
    return samples


def _exact_model_samples(
    samples: Sequence[Mapping[str, Any]], *, model: str, version: str, field: str
) -> list[Mapping[str, Any]]:
    matching = [
        sample
        for sample in samples
        if sample.get("labels", {}).get("model") == model
        and sample.get("labels", {}).get("version") == version
    ]
    if len(matching) != len(samples) or not matching:
        raise E0StrictEvidenceValidationError(f"{field}:model_identity")
    return matching


def validate_model_repository(
    private_root: Path, config: E0RuntimeConfig
) -> dict[str, Any]:
    repository = private_root / "model-repository"
    manifest_path = repository / "model-repository-manifest.json"
    manifest = _load_json(manifest_path, field="model_manifest")
    if manifest.get("schema_version") != "evm.s8_v4.e0_model_repository.v1":
        raise E0StrictEvidenceValidationError("model_manifest:schema")
    expected_identity = {
        "model_name": config.model_name,
        "model_version": config.model_version,
        "backend": config.backend,
    }
    for field, expected in expected_identity.items():
        if manifest.get(field) != expected:
            raise E0StrictEvidenceValidationError(f"model_manifest:{field}")
    observed_entries: list[dict[str, Any]] = []
    for entry in manifest.get("entries", []):
        if not isinstance(entry, Mapping):
            raise E0StrictEvidenceValidationError("model_manifest:entry")
        relative = str(entry.get("path") or "")
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise E0StrictEvidenceValidationError("model_manifest:entry_path")
        path = repository / relative
        if not path.is_file():
            raise E0StrictEvidenceValidationError(f"model_manifest:entry_missing:{relative}")
        observed_entries.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    if canonical(observed_entries) != canonical(manifest.get("entries")):
        raise E0StrictEvidenceValidationError("model_manifest:entry_projection")
    repository_sha = hashlib.sha256(canonical(observed_entries).encode("ascii")).hexdigest()
    if manifest.get("repository_sha256") != repository_sha:
        raise E0StrictEvidenceValidationError("model_manifest:repository_sha256")
    expected_paths = {
        f"{config.model_name}/{config.model_version}/model.pt": "artifact_sha256",
        f"{config.model_name}/config.pbtxt": "config_sha256",
    }
    entries_by_path = {entry["path"]: entry for entry in observed_entries}
    if set(entries_by_path) != set(expected_paths):
        raise E0StrictEvidenceValidationError("model_manifest:entry_set")
    for path, field in expected_paths.items():
        if manifest.get(field) != entries_by_path[path]["sha256"]:
            raise E0StrictEvidenceValidationError(f"model_manifest:{field}")
    return {
        "manifest_sha256": sha256_file(manifest_path),
        "repository_sha256": repository_sha,
        "artifact_sha256": manifest["artifact_sha256"],
        "config_sha256": manifest["config_sha256"],
        **expected_identity,
    }


def _prometheus_series(history: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    if history.get("schema_version") != PROMETHEUS_SCHEMA_VERSION:
        raise E0StrictEvidenceValidationError("prometheus:schema")
    node = history.get("queries", {}).get(key)
    if not isinstance(node, Mapping) or node.get("query") != PROMETHEUS_QUERIES[key]:
        raise E0StrictEvidenceValidationError(f"prometheus:{key}:query")
    response = node.get("response")
    if not isinstance(response, Mapping) or response.get("status") != "success":
        raise E0StrictEvidenceValidationError(f"prometheus:{key}:response")
    data = response.get("data")
    if not isinstance(data, Mapping) or data.get("resultType") != "matrix":
        raise E0StrictEvidenceValidationError(f"prometheus:{key}:result_type")
    result = data.get("result")
    if not isinstance(result, list):
        raise E0StrictEvidenceValidationError(f"prometheus:{key}:result")
    return result


def _prometheus_attempt_projection(
    history: Mapping[str, Any],
    *,
    attempt_id: str,
    expected_attempt_ids: set[str],
    model_name: str,
    model_version: str,
    gpu_uuid: str,
) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key in PROMETHEUS_QUERIES:
        series = _prometheus_series(history, key)
        observed_attempt_ids = {
            str(item.get("metric", {}).get("attempt_id") or "") for item in series
        }
        if observed_attempt_ids != expected_attempt_ids:
            raise E0StrictEvidenceValidationError(f"prometheus:{key}:attempt_set")
        matching = [
            item for item in series if item.get("metric", {}).get("attempt_id") == attempt_id
        ]
        if len(matching) != 1:
            raise E0StrictEvidenceValidationError(f"prometheus:{key}:attempt_identity")
        item = matching[0]
        metric = dict(item.get("metric", {}))
        if metric.get("job") != "evm-s8-v4-e0":
            raise E0StrictEvidenceValidationError(f"prometheus:{key}:job_identity")
        if key in {"request_success", "inference_count"} and (
            metric.get("model") != model_name or metric.get("version") != model_version
        ):
            raise E0StrictEvidenceValidationError(f"prometheus:{key}:model_identity")
        if key == "gpu_memory" and metric.get("gpu_uuid") != gpu_uuid:
            raise E0StrictEvidenceValidationError("prometheus:gpu_memory:gpu_identity")
        values = item.get("values")
        if not isinstance(values, list) or not values:
            raise E0StrictEvidenceValidationError(f"prometheus:{key}:values")
        numbers = [
            _finite(value[1], field=f"prometheus:{key}:value")
            for value in values
            if isinstance(value, list) and len(value) == 2
        ]
        if len(numbers) != len(values):
            raise E0StrictEvidenceValidationError(f"prometheus:{key}:sample_shape")
        maximum = max(numbers)
        if key == "target_up" and maximum != 1:
            raise E0StrictEvidenceValidationError("prometheus:target_up:value")
        if key in {"request_success", "inference_count"} and (
            maximum != EXPECTED_REQUEST_COUNT or any(value != EXPECTED_REQUEST_COUNT for value in numbers)
        ):
            raise E0StrictEvidenceValidationError(f"prometheus:{key}:count")
        if key == "gpu_memory" and maximum <= 0:
            raise E0StrictEvidenceValidationError("prometheus:gpu_memory:value")
        projected[key] = {
            "metric_identity": metric,
            "sample_count": len(numbers),
            "maximum": maximum,
        }
    return projected


def _validate_direct_metrics(
    text: str, *, model_name: str, model_version: str, gpu_uuid: str
) -> dict[str, Any]:
    success = _exact_model_samples(
        _parse_metric_samples(text, "nv_inference_request_success"),
        model=model_name,
        version=model_version,
        field="direct_metrics:request_success",
    )
    inference = _exact_model_samples(
        _parse_metric_samples(text, "nv_inference_count"),
        model=model_name,
        version=model_version,
        field="direct_metrics:inference_count",
    )
    failures = _exact_model_samples(
        _parse_metric_samples(text, "nv_inference_request_failure"),
        model=model_name,
        version=model_version,
        field="direct_metrics:request_failure",
    )
    if len(success) != 1 or success[0]["value"] != EXPECTED_REQUEST_COUNT:
        raise E0StrictEvidenceValidationError("direct_metrics:request_success:count")
    if len(inference) != 1 or inference[0]["value"] != EXPECTED_REQUEST_COUNT:
        raise E0StrictEvidenceValidationError("direct_metrics:inference_count:count")
    if sum(float(item["value"]) for item in failures) != 0:
        raise E0StrictEvidenceValidationError("direct_metrics:request_failure:count")
    gpu = _parse_metric_samples(text, "nv_gpu_memory_used_bytes")
    if (
        len(gpu) != 1
        or gpu[0]["labels"].get("gpu_uuid") != gpu_uuid
        or gpu[0]["value"] <= 0
    ):
        raise E0StrictEvidenceValidationError("direct_metrics:gpu_memory")
    return {
        "request_success_count": int(success[0]["value"]),
        "inference_count": int(inference[0]["value"]),
        "request_failure_count": int(sum(float(item["value"]) for item in failures)),
        "triton_direct_gpu_memory_used_bytes": int(gpu[0]["value"]),
    }


def _validate_profiler(
    raw: Mapping[str, Any], *, timeline: str, triton_log: str, model_name: str
) -> dict[str, Any]:
    profiler = dict(raw.get("profiler", {}))
    records = []
    for line in timeline.splitlines():
        match = CUPTI_KERNEL_PATTERN.fullmatch(line.strip())
        if match is None:
            continue
        record = {
            "kernel_name": match.group("name"),
            "start": int(match.group("start")),
            "end": int(match.group("end")),
            "device": int(match.group("device")),
            "stream": int(match.group("stream")),
        }
        if record["end"] <= record["start"] or record["device"] < 0 or record["stream"] < 0:
            raise E0StrictEvidenceValidationError("profiler:kernel_record")
        records.append(record)
    if not records or "E0_CUPTI_DROPPED=0" not in timeline:
        raise E0StrictEvidenceValidationError("profiler:cupti_timeline")
    if not re.search(
        rf"ModelInstanceInitialize:\s+{re.escape(model_name)}_\d+_\d+\s+\(GPU device 0\)",
        triton_log,
    ):
        raise E0StrictEvidenceValidationError("profiler:triton_gpu_instance")
    if (
        profiler.get("tool") != "cupti"
        or profiler.get("trace_method") != "cupti-activity"
        or profiler.get("parseable") is not True
        or profiler.get("triton_inference_traced") is not False
        or int(profiler.get("cuda_kernel_count", 0)) != len(records)
    ):
        raise E0StrictEvidenceValidationError("profiler:contract")
    return {
        "qualification_method": "cupti-activity",
        "kernel_records": records,
        "context_collected": False,
        "triton_inference_traced": False,
        "claim_boundary": (
            "standalone same-container CUDA qualification only; not a Triton inference "
            "trace and not CUDA overlap evidence"
        ),
    }


def _validate_cleanup(raw: Mapping[str, Any], config: E0RuntimeConfig) -> dict[str, Any]:
    cleanup = dict(raw.get("cleanup", {}))
    for field in ATTEMPT_CLEANUP_TRUE_FIELDS:
        if cleanup.get(field) is not True:
            raise E0StrictEvidenceValidationError(f"cleanup:{field}")
    if int(cleanup.get("orphan_count", -1)) != 0:
        raise E0StrictEvidenceValidationError("cleanup:orphan_count")
    elapsed = _finite(cleanup.get("elapsed_seconds"), field="cleanup:elapsed_seconds")
    total_vram = _finite(
        cleanup.get("preflight_total_vram_mib"), field="cleanup:preflight_total_vram_mib"
    )
    vram_delta = _finite(cleanup.get("vram_delta_mib"), field="cleanup:vram_delta_mib")
    tolerance = max(config.vram_tolerance_mib, total_vram * config.vram_tolerance_ratio)
    if elapsed > config.cleanup_timeout_seconds:
        raise E0StrictEvidenceValidationError("cleanup:timeout")
    if abs(vram_delta) > tolerance:
        raise E0StrictEvidenceValidationError("cleanup:vram_restore")
    return {
        "elapsed_seconds": elapsed,
        "orphan_count": 0,
        "vram_delta_mib": vram_delta,
        "vram_tolerance_mib": tolerance,
        "residue_dimensions_zero": list(ATTEMPT_CLEANUP_TRUE_FIELDS),
    }


def _validate_final_cleanup(private_root: Path) -> dict[str, Any]:
    cleanup = _load_json(private_root / "final-cleanup-private.json", field="final_cleanup")
    required_true = (
        "holder_image_match",
        "holder_uid_match",
        "ports_absent",
        "target_absent",
        "temporary_kubernetes_resources_absent",
    )
    for field in required_true:
        if cleanup.get(field) is not True:
            raise E0StrictEvidenceValidationError(f"final_cleanup:{field}")
    queues = dict(cleanup.get("queues", {}))
    if queues != {"active": 0, "leased": 0, "outcome_unknown": 0}:
        raise E0StrictEvidenceValidationError("final_cleanup:queues")
    for field in ("prometheus_baseline_before", "prometheus_baseline_after"):
        health = dict(cleanup.get(field, {}))
        if int(health.get("total", 0)) < 1 or health.get("up") != health.get("total"):
            raise E0StrictEvidenceValidationError(f"final_cleanup:{field}")
    b0 = dict(cleanup.get("b0_cuda_inference", {}))
    if b0.get("passed") is not True or b0.get("ready", {}).get("cuda_available") is not True:
        raise E0StrictEvidenceValidationError("final_cleanup:b0_cuda")
    return {
        "queues": queues,
        "prometheus_targets": cleanup["prometheus_baseline_after"],
        "b0_cuda_restored": True,
        "temporary_resources_absent": True,
    }


def strict_revalidate_e0_runtime(
    experiment: Mapping[str, Any],
    *,
    config: E0RuntimeConfig,
    private_root: Path,
    prometheus_history: Mapping[str, Any],
) -> dict[str, Any]:
    model = validate_model_repository(private_root, config)
    attempts = list(experiment.get("attempts", []))
    if len(attempts) != config.repetitions:
        raise E0StrictEvidenceValidationError("attempts:count")
    attempt_ids = {
        str(item.get("summary", {}).get("attempt_id") or "") for item in attempts
    }
    if "" in attempt_ids or len(attempt_ids) != config.repetitions:
        raise E0StrictEvidenceValidationError("attempts:identity_set")
    projected: list[dict[str, Any]] = []
    for public in sorted(attempts, key=lambda item: int(item["summary"]["repetition"])):
        reference = dict(public.get("private_evidence", {}))
        relative = str(reference.get("path") or "")
        path = private_root / relative
        if (
            not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not path.is_file()
            or sha256_file(path) != reference.get("sha256")
        ):
            raise E0StrictEvidenceValidationError("attempts:private_reference")
        raw = _load_json(path, field="attempt")
        attempt_id = str(raw.get("attempt_id") or "")
        repetition = int(raw.get("repetition", 0))
        if (
            attempt_id != public.get("summary", {}).get("attempt_id")
            or repetition != public.get("summary", {}).get("repetition")
            or raw.get("credit") != "credit"
        ):
            raise E0StrictEvidenceValidationError("attempts:public_private_identity")
        raw_model = dict(raw.get("model", {}))
        for field in (
            "model_name",
            "model_version",
            "backend",
            "repository_sha256",
            "artifact_sha256",
            "config_sha256",
        ):
            raw_field = field.replace("model_", "") if field in {"model_name", "model_version"} else field
            if raw_model.get(raw_field) != model[field]:
                raise E0StrictEvidenceValidationError(f"attempt:{repetition}:model:{raw_field}")
        if raw.get("image", {}).get("repo_digest") != config.triton_image_digest:
            raise E0StrictEvidenceValidationError(f"attempt:{repetition}:image_digest")
        environment = dict(raw.get("environment", {}))
        host_gpu = dict(environment.get("host_gpu", {}))
        container_gpu = dict(environment.get("container_gpu", {}))
        gpu_uuid = str(host_gpu.get("uuid") or "")
        if (
            not gpu_uuid
            or container_gpu.get("uuid") != gpu_uuid
            or host_gpu.get("name") != config.expected_gpu_name
            or container_gpu.get("name") != config.expected_gpu_name
        ):
            raise E0StrictEvidenceValidationError(f"attempt:{repetition}:gpu_identity")
        inference = dict(raw.get("inference", {}))
        response = dict(inference.get("response", {}))
        response_outputs = list(response.get("outputs", []))
        if (
            inference.get("transport_ok") is not True
            or int(inference.get("request_count", -1)) != EXPECTED_REQUEST_COUNT
            or inference.get("output") != list(config.expected_output)
            or response.get("id") != attempt_id
            or response.get("model_name") != config.model_name
            or response.get("model_version") != config.model_version
            or len(response_outputs) != 1
            or response_outputs[0].get("data") != list(config.expected_output)
        ):
            raise E0StrictEvidenceValidationError(f"attempt:{repetition}:inference")
        runtime_groups = list(raw_model.get("runtime_config", {}).get("instance_group", []))
        if (
            len(runtime_groups) != 1
            or runtime_groups[0].get("kind") != "KIND_GPU"
            or runtime_groups[0].get("gpus") != [0]
        ):
            raise E0StrictEvidenceValidationError(f"attempt:{repetition}:gpu_instance_config")
        attempt_root = path.parent
        direct_metrics = _validate_direct_metrics(
            (attempt_root / "triton-metrics.txt").read_text(encoding="utf-8"),
            model_name=config.model_name,
            model_version=config.model_version,
            gpu_uuid=gpu_uuid,
        )
        prometheus = _prometheus_attempt_projection(
            prometheus_history,
            attempt_id=attempt_id,
            expected_attempt_ids=attempt_ids,
            model_name=config.model_name,
            model_version=config.model_version,
            gpu_uuid=gpu_uuid,
        )
        profiler = _validate_profiler(
            raw,
            timeline=(attempt_root / "profiler/cupti-gpu-activity-timeline.txt").read_text(
                encoding="utf-8"
            ),
            triton_log=(attempt_root / "profiler/triton.log").read_text(encoding="utf-8"),
            model_name=config.model_name,
        )
        cleanup = _validate_cleanup(raw, config)
        projected.append(
            {
                "attempt_id": attempt_id,
                "repetition": repetition,
                "credit": "credit",
                "model_identity": model,
                "gpu_uuid": gpu_uuid,
                "request_count": EXPECTED_REQUEST_COUNT,
                "output_correct": True,
                "gpu_instance_proven": True,
                "direct_metrics": direct_metrics,
                "prometheus": prometheus,
                "profiler": profiler,
                "cleanup": cleanup,
                "acceptance": {
                    "E0-AC-01": True,
                    "E0-AC-02": True,
                    "E0-AC-03": True,
                    "E0-AC-04": True,
                },
            }
        )
    if [item["repetition"] for item in projected] != list(range(1, config.repetitions + 1)):
        raise E0StrictEvidenceValidationError("attempts:repetition_set")
    final_cleanup = _validate_final_cleanup(private_root)
    acceptance = {
        criterion: all(item["acceptance"][criterion] for item in projected)
        for criterion in ("E0-AC-01", "E0-AC-02", "E0-AC-03", "E0-AC-04")
    }
    return {
        "schema_version": STRICT_SCHEMA_VERSION,
        "status": "review_pending",
        "acceptance_credit": False,
        "reviewer_sign_off": "pending",
        "strict_contract": {
            "expected_request_count": EXPECTED_REQUEST_COUNT,
            "model_manifest_sha256": model["manifest_sha256"],
            "model_artifact_sha256": model["artifact_sha256"],
            "prometheus_identity": "attempt_id + model + version",
            "gpu_memory_semantics": {
                "triton_direct_gpu_memory_used_bytes": "direct Triton endpoint gauge",
                "prometheus_attempt_gpu_memory_used_bytes": "attempt-labelled Prometheus gauge",
                "legacy_gpu_memory_used_bytes": "historical max-combined field; excluded from strict AC calculation",
            },
            "profiler_gate": "Nsight OR CUPTI; this evidence uses standalone CUPTI CUDA activity",
        },
        "attempts": projected,
        "acceptance": acceptance,
        "evidence_ready": all(acceptance.values()),
        "final_cleanup": final_cleanup,
        "claim_boundary": (
            "one Windows/WSL2 physical node, one RTX 4080, controlled traffic; "
            "CUPTI is a standalone CUDA qualification, not a Triton trace or overlap proof"
        ),
    }


def public_strict_projection(strict: Mapping[str, Any]) -> dict[str, Any]:
    attempts = []
    for item in strict.get("attempts", []):
        prometheus = {
            name: {
                "sample_count": evidence["sample_count"],
                "maximum": evidence["maximum"],
            }
            for name, evidence in sorted(item["prometheus"].items())
        }
        records = list(item["profiler"]["kernel_records"])
        attempts.append(
            {
                "attempt_identity_sha256": hashlib.sha256(
                    str(item["attempt_id"]).encode("utf-8")
                ).hexdigest(),
                "repetition": item["repetition"],
                "credit": item["credit"],
                "model_identity": item["model_identity"],
                "gpu_identity_consistent": True,
                "request_count": item["request_count"],
                "output_correct": item["output_correct"],
                "gpu_instance_proven": item["gpu_instance_proven"],
                "direct_metrics": item["direct_metrics"],
                "prometheus": prometheus,
                "profiler": {
                    "qualification_method": item["profiler"]["qualification_method"],
                    "kernel_record_count": len(records),
                    "devices": sorted({record["device"] for record in records}),
                    "streams": sorted({record["stream"] for record in records}),
                    "context_collected": item["profiler"]["context_collected"],
                    "triton_inference_traced": item["profiler"][
                        "triton_inference_traced"
                    ],
                    "claim_boundary": item["profiler"]["claim_boundary"],
                },
                "cleanup": item["cleanup"],
                "acceptance": item["acceptance"],
            }
        )
    return {
        "schema_version": strict["schema_version"],
        "strict_contract": strict["strict_contract"],
        "attempts": attempts,
        "acceptance": strict["acceptance"],
        "evidence_ready": strict["evidence_ready"],
        "final_cleanup": strict["final_cleanup"],
        "claim_boundary": strict["claim_boundary"],
    }
