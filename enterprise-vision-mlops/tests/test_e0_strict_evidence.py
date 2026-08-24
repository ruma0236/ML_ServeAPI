from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from evm.scale_validation.e0_runtime import E0RuntimeConfig, canonical, sha256_file
from evm.scale_validation.e0_strict_evidence import (
    E0StrictEvidenceValidationError,
    PROMETHEUS_QUERIES,
    strict_revalidate_e0_runtime,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/s8_v4_e0_environment_v1.toml"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(payload) + "\n", encoding="utf-8", newline="\n")


def _fixture(tmp_path: Path) -> tuple[E0RuntimeConfig, dict[str, Any], dict[str, Any]]:
    config = E0RuntimeConfig.from_path(CONFIG)
    repository = tmp_path / "model-repository"
    artifact = repository / config.model_name / config.model_version / "model.pt"
    model_config = repository / config.model_name / "config.pbtxt"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"deterministic-model")
    model_config.write_text("kind: KIND_GPU\n", encoding="utf-8", newline="\n")
    entries = [
        {
            "path": path.relative_to(repository).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted((artifact, model_config))
    ]
    manifest = {
        "schema_version": "evm.s8_v4.e0_model_repository.v1",
        "model_name": config.model_name,
        "model_version": config.model_version,
        "backend": config.backend,
        "framework": {"name": "fixture"},
        "entries": entries,
        "repository_sha256": hashlib.sha256(canonical(entries).encode("ascii")).hexdigest(),
        "artifact_sha256": sha256_file(artifact),
        "config_sha256": sha256_file(model_config),
    }
    _write_json(repository / "model-repository-manifest.json", manifest)

    gpu_uuid = "GPU-strict-fixture"
    attempts = []
    attempt_ids = []
    for repetition in range(1, 4):
        attempt_id = f"e0-{repetition}-strict-fixture"
        attempt_ids.append(attempt_id)
        raw = {
            "schema_version": "evm.s8_v4.e0_attempt_private.v1",
            "attempt_id": attempt_id,
            "repetition": repetition,
            "credit": "credit",
            "image": {"repo_digest": config.triton_image_digest},
            "environment": {
                "host_gpu": {"uuid": gpu_uuid, "name": config.expected_gpu_name},
                "container_gpu": {"uuid": gpu_uuid, "name": config.expected_gpu_name},
            },
            "model": {
                "name": config.model_name,
                "version": config.model_version,
                "backend": config.backend,
                "repository_sha256": manifest["repository_sha256"],
                "artifact_sha256": manifest["artifact_sha256"],
                "config_sha256": manifest["config_sha256"],
                "runtime_config": {
                    "instance_group": [{"kind": "KIND_GPU", "gpus": [0]}]
                },
            },
            "inference": {
                "transport_ok": True,
                "request_count": 100,
                "output": list(config.expected_output),
                "response": {
                    "id": attempt_id,
                    "model_name": config.model_name,
                    "model_version": config.model_version,
                    "outputs": [{"data": list(config.expected_output)}],
                },
            },
            "profiler": {
                "tool": "cupti",
                "trace_method": "cupti-activity",
                "parseable": True,
                "triton_inference_traced": False,
                "cuda_kernel_count": 1,
            },
            "cleanup": {
                "elapsed_seconds": 5.0,
                "preflight_total_vram_mib": 16376.0,
                "vram_delta_mib": 1.0,
                "container_absent": True,
                "port_listeners_absent": True,
                "gpu_context_absent": True,
                "lease_absent": True,
                "prometheus_target_absent": True,
                "temporary_kubernetes_resources_absent": True,
                "queue_active_zero": True,
                "queue_leased_zero": True,
                "queue_outcome_unknown_zero": True,
                "b0_ready": True,
                "b0_cuda_inference": True,
                "orphan_count": 0,
            },
        }
        attempt_path = tmp_path / "attempts" / f"repetition-{repetition}" / "attempt-private.json"
        _write_json(attempt_path, raw)
        metrics = (
            f'nv_inference_request_success{{model="{config.model_name}",version="{config.model_version}"}} 100\n'
            f'nv_inference_request_failure{{model="{config.model_name}",reason="OTHER",version="{config.model_version}"}} 0\n'
            f'nv_inference_request_failure{{model="{config.model_name}",reason="BACKEND",version="{config.model_version}"}} 0\n'
            f'nv_inference_count{{model="{config.model_name}",version="{config.model_version}"}} 100\n'
            f'nv_gpu_memory_used_bytes{{gpu_uuid="{gpu_uuid}"}} 1024\n'
        )
        (attempt_path.parent / "triton-metrics.txt").write_text(
            metrics, encoding="utf-8", newline="\n"
        )
        profiler = attempt_path.parent / "profiler"
        profiler.mkdir()
        (profiler / "triton.log").write_text(
            f"TRITONBACKEND_ModelInstanceInitialize: {config.model_name}_0_0 (GPU device 0)\n",
            encoding="utf-8",
            newline="\n",
        )
        (profiler / "cupti-gpu-activity-timeline.txt").write_text(
            "E0_CUPTI_KERNEL|name=e0_kernel|start=10|end=20|device=0|stream=7\n"
            "E0_CUPTI_DROPPED=0\n",
            encoding="utf-8",
            newline="\n",
        )
        attempts.append(
            {
                "summary": {"attempt_id": attempt_id, "repetition": repetition},
                "private_evidence": {
                    "path": attempt_path.relative_to(tmp_path).as_posix(),
                    "sha256": sha256_file(attempt_path),
                },
            }
        )
    _write_json(
        tmp_path / "final-cleanup-private.json",
        {
            "holder_image_match": True,
            "holder_uid_match": True,
            "ports_absent": True,
            "target_absent": True,
            "temporary_kubernetes_resources_absent": True,
            "queues": {"active": 0, "leased": 0, "outcome_unknown": 0},
            "prometheus_baseline_before": {"total": 5, "up": 5},
            "prometheus_baseline_after": {"total": 5, "up": 5},
            "b0_cuda_inference": {
                "passed": True,
                "ready": {"cuda_available": True},
            },
        },
    )
    history = {
        "schema_version": "evm.s8_v4.e0_prometheus_history.v1",
        "queries": {},
    }
    metric_names = {
        "target_up": "up",
        "request_success": "nv_inference_request_success",
        "inference_count": "nv_inference_count",
        "gpu_memory": "nv_gpu_memory_used_bytes",
    }
    for key, query in PROMETHEUS_QUERIES.items():
        result = []
        for attempt_id in attempt_ids:
            metric = {
                "__name__": metric_names[key],
                "attempt_id": attempt_id,
                "job": "evm-s8-v4-e0",
            }
            if key in {"request_success", "inference_count"}:
                metric.update(model=config.model_name, version=config.model_version)
            if key == "gpu_memory":
                metric["gpu_uuid"] = gpu_uuid
            value = "1" if key == "target_up" else "1024" if key == "gpu_memory" else "100"
            result.append({"metric": metric, "values": [[1, value]]})
        history["queries"][key] = {
            "query": query,
            "response": {
                "status": "success",
                "data": {"resultType": "matrix", "result": result},
            },
        }
    return config, {"attempts": attempts}, history


def _rewrite_attempt(
    tmp_path: Path, experiment: dict[str, Any], repetition: int, mutate: Any
) -> None:
    item = experiment["attempts"][repetition - 1]
    path = tmp_path / item["private_evidence"]["path"]
    raw = json.loads(path.read_bytes())
    mutate(raw)
    _write_json(path, raw)
    item["private_evidence"]["sha256"] = sha256_file(path)


def test_strict_e0_revalidation_passes_complete_raw_fixture(tmp_path: Path) -> None:
    config, experiment, history = _fixture(tmp_path)
    result = strict_revalidate_e0_runtime(
        experiment,
        config=config,
        private_root=tmp_path,
        prometheus_history=history,
    )
    assert result["evidence_ready"] is True
    assert all(result["acceptance"].values())
    assert [item["direct_metrics"]["request_success_count"] for item in result["attempts"]] == [
        100,
        100,
        100,
    ]


def test_strict_e0_rejects_valid_but_wrong_model_digest(tmp_path: Path) -> None:
    config, experiment, history = _fixture(tmp_path)
    _rewrite_attempt(
        tmp_path,
        experiment,
        2,
        lambda raw: raw["model"].update(artifact_sha256="f" * 64),
    )
    with pytest.raises(E0StrictEvidenceValidationError, match="model:artifact_sha256"):
        strict_revalidate_e0_runtime(
            experiment, config=config, private_root=tmp_path, prometheus_history=history
        )


def test_strict_e0_rejects_model_file_bytes_mismatch(tmp_path: Path) -> None:
    config, experiment, history = _fixture(tmp_path)
    (tmp_path / "model-repository" / config.model_name / config.model_version / "model.pt").write_bytes(
        b"mutated-model"
    )
    with pytest.raises(E0StrictEvidenceValidationError, match="entry_projection"):
        strict_revalidate_e0_runtime(
            experiment, config=config, private_root=tmp_path, prometheus_history=history
        )


@pytest.mark.parametrize("orphan_count", [1, 999])
def test_strict_e0_rejects_any_orphan_count(tmp_path: Path, orphan_count: int) -> None:
    config, experiment, history = _fixture(tmp_path)
    _rewrite_attempt(
        tmp_path,
        experiment,
        1,
        lambda raw: raw["cleanup"].update(orphan_count=orphan_count),
    )
    with pytest.raises(E0StrictEvidenceValidationError, match="cleanup:orphan_count"):
        strict_revalidate_e0_runtime(
            experiment, config=config, private_root=tmp_path, prometheus_history=history
        )


@pytest.mark.parametrize("count", [99, 1, 0])
def test_strict_e0_rejects_request_count_mutation(tmp_path: Path, count: int) -> None:
    config, experiment, history = _fixture(tmp_path)
    _rewrite_attempt(
        tmp_path,
        experiment,
        1,
        lambda raw: raw["inference"].update(request_count=count),
    )
    with pytest.raises(E0StrictEvidenceValidationError, match="attempt:1:inference"):
        strict_revalidate_e0_runtime(
            experiment, config=config, private_root=tmp_path, prometheus_history=history
        )


@pytest.mark.parametrize("metric_name", ["request_success", "inference_count"])
@pytest.mark.parametrize("count", [99, 1, 0])
def test_strict_e0_rejects_prometheus_count_mutation(
    tmp_path: Path, metric_name: str, count: int
) -> None:
    config, experiment, history = _fixture(tmp_path)
    history["queries"][metric_name]["response"]["data"]["result"][0]["values"] = [
        [1, str(count)]
    ]
    with pytest.raises(E0StrictEvidenceValidationError, match=f"prometheus:{metric_name}:count"):
        strict_revalidate_e0_runtime(
            experiment, config=config, private_root=tmp_path, prometheus_history=history
        )


@pytest.mark.parametrize(
    ("metric_name", "prometheus_name"),
    [
        ("nv_inference_request_success", "request_success"),
        ("nv_inference_count", "inference_count"),
    ],
)
@pytest.mark.parametrize("count", [99, 1, 0])
def test_strict_e0_rejects_direct_metric_count_mutation(
    tmp_path: Path, metric_name: str, prometheus_name: str, count: int
) -> None:
    config, experiment, history = _fixture(tmp_path)
    metrics = tmp_path / "attempts/repetition-1/triton-metrics.txt"
    original = metrics.read_text(encoding="utf-8")
    metrics.write_text(
        original.replace(
            f'{metric_name}{{model="{config.model_name}",version="{config.model_version}"}} 100',
            f'{metric_name}{{model="{config.model_name}",version="{config.model_version}"}} {count}',
        ),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(
        E0StrictEvidenceValidationError,
        match=f"direct_metrics:{prometheus_name}:count",
    ):
        strict_revalidate_e0_runtime(
            experiment, config=config, private_root=tmp_path, prometheus_history=history
        )


def test_strict_e0_rejects_direct_failure_count(tmp_path: Path) -> None:
    config, experiment, history = _fixture(tmp_path)
    metrics = tmp_path / "attempts/repetition-1/triton-metrics.txt"
    metrics.write_text(
        metrics.read_text(encoding="utf-8").replace('reason="BACKEND",version="1"} 0', 'reason="BACKEND",version="1"} 1'),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(
        E0StrictEvidenceValidationError,
        match="direct_metrics:request_failure:count",
    ):
        strict_revalidate_e0_runtime(
            experiment, config=config, private_root=tmp_path, prometheus_history=history
        )


@pytest.mark.parametrize("mutation", ["missing", "attempt", "model"])
def test_strict_e0_rejects_prometheus_identity_or_missing(
    tmp_path: Path, mutation: str
) -> None:
    config, experiment, history = _fixture(tmp_path)
    series = history["queries"]["request_success"]["response"]["data"]["result"]
    if mutation == "missing":
        series.pop()
    elif mutation == "attempt":
        series[0]["metric"]["attempt_id"] = "wrong-attempt"
    else:
        series[0]["metric"]["model"] = "wrong-model"
    with pytest.raises(E0StrictEvidenceValidationError, match="prometheus:request_success"):
        strict_revalidate_e0_runtime(
            experiment, config=config, private_root=tmp_path, prometheus_history=history
        )


def test_strict_e0_rejects_cleanup_residue(tmp_path: Path) -> None:
    config, experiment, history = _fixture(tmp_path)
    _rewrite_attempt(
        tmp_path,
        experiment,
        3,
        lambda raw: raw["cleanup"].update(port_listeners_absent=False),
    )
    with pytest.raises(E0StrictEvidenceValidationError, match="cleanup:port_listeners_absent"):
        strict_revalidate_e0_runtime(
            experiment, config=config, private_root=tmp_path, prometheus_history=history
        )


def test_strict_e0_rejects_missing_cupti_stream_field(tmp_path: Path) -> None:
    config, experiment, history = _fixture(tmp_path)
    timeline = tmp_path / "attempts/repetition-1/profiler/cupti-gpu-activity-timeline.txt"
    timeline.write_text(
        "E0_CUPTI_KERNEL|name=e0_kernel|start=10|end=20|device=0\n"
        "E0_CUPTI_DROPPED=0\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(E0StrictEvidenceValidationError, match="profiler:cupti_timeline"):
        strict_revalidate_e0_runtime(
            experiment, config=config, private_root=tmp_path, prometheus_history=history
        )
