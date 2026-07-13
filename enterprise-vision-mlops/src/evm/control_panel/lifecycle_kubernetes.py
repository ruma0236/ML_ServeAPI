from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from evm.control_panel.lifecycle_runs import LifecycleRun, lifecycle_deployment_name
from evm.control_panel.readiness_evaluator import file_sha256, runtime_path
from evm.control_panel.schemas import CycleRun, TaskAssignment


Runner = Callable[..., subprocess.CompletedProcess[str]]
NODE_PORTS = {
    "dev": 30811,
    "test": 30812,
    "staging": 30813,
    "pre-production": 30814,
    "production": 30800,
}


class LifecycleKubernetesError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrainingBundle:
    manifest_dir: Path
    namespace: str
    job_name: str
    candidate_id: str
    image: str


@dataclass(frozen=True)
class ServingBundle:
    manifest_dir: Path
    namespace: str
    deployment_name: str
    endpoint: str
    image: str


def materialize_training_bundle(run: LifecycleRun) -> TrainingBundle:
    profile_path = runtime_path(run.profile_snapshot_uri)
    model_path = runtime_path(run.model_config_uri)
    profile = read_json(profile_path)
    model = read_json(model_path)
    candidate = selected_candidate(model)
    candidate_id = str(candidate["candidate_id"])
    resources = object_value(profile, "resources")
    gpu_count = int(resources.get("gpu_count") or 0)
    if gpu_count < 1:
        raise LifecycleKubernetesError("efficientnet_training_requires_gpu")
    cpu_request = int(resources.get("cpu_request") or 1)
    memory_gb = int(resources.get("memory_gb") or 2)
    image = pinned_image(
        "EVM_LIFECYCLE_TRAINING_IMAGE",
        str(candidate.get("training_image") or ""),
    )
    namespace = "evm-training"
    job_name = f"evm-lifecycle-train-{short_run_id(run.run_id)}"
    directory = profile_path.parent / "kubernetes" / "training"
    directory.mkdir(parents=True, exist_ok=True)

    write_json(directory / "namespace.json", namespace_resource(namespace))
    storage = storage_resources(namespace, read_only=False)
    write_json(directory / "storage-pv.json", storage[0])
    write_json(directory / "storage-pvc.json", storage[1])
    experiment_search_enabled = bool(
        object_value(model, "experiment_search").get("enabled")
    )
    command = training_command(
        run.model_runtime_uri,
        experiment_search=experiment_search_enabled,
    )
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "labels": lifecycle_labels(run, candidate_id),
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": int(
                os.getenv("EVM_LIFECYCLE_TRAINING_TIMEOUT_SECONDS", "7200")
            ),
            "template": {
                "metadata": {"labels": lifecycle_labels(run, candidate_id)},
                "spec": {
                    "restartPolicy": "Never",
                    "terminationGracePeriodSeconds": 30,
                    "securityContext": pod_security_context(),
                    "containers": [
                        {
                            "name": "trainer",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["/bin/sh", "-ec", command],
                            "env": [
                                env("EVM_LIFECYCLE_RUN_ID", run.run_id),
                                env("EVM_EFFICIENTNET_CANDIDATES", candidate_id),
                                env("EVM_PROJECT_ROOT", "/app"),
                                env("EVM_HOST_DATA_ROOT", host_data_root()),
                                env("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data"),
                                env(
                                    "EVM_EXPERIMENT_RUN_ROOT",
                                    "/mnt/evm-data/artifacts/w8/experiment_runs",
                                ),
                                env("HOME", "/tmp"),
                                env("MPLCONFIGDIR", "/tmp/matplotlib"),
                                env("TORCH_HOME", "/mnt/evm-data/cache/torch"),
                                env("NVIDIA_VISIBLE_DEVICES", "all"),
                                env("NVIDIA_DRIVER_CAPABILITIES", "compute,utility"),
                            ],
                            "resources": {
                                "requests": {
                                    "cpu": str(cpu_request),
                                    "memory": f"{memory_gb}Gi",
                                    "nvidia.com/gpu": str(gpu_count),
                                },
                                "limits": {
                                    "cpu": str(min(64, max(cpu_request, cpu_request * 2))),
                                    "memory": f"{min(256, max(memory_gb, memory_gb * 2))}Gi",
                                    "nvidia.com/gpu": str(gpu_count),
                                },
                            },
                            "securityContext": container_security_context(),
                            "volumeMounts": common_volume_mounts(read_only_data=False),
                        }
                    ],
                    "volumes": common_volumes(),
                },
            },
        },
    }
    write_json(directory / "training-job.json", job)
    write_kustomization(
        directory,
        ["namespace.json", "storage-pv.json", "storage-pvc.json", "training-job.json"],
    )
    return TrainingBundle(directory, namespace, job_name, candidate_id, image)


def materialize_serving_bundle(run: LifecycleRun, cycle: CycleRun) -> ServingBundle:
    profile_path = runtime_path(run.profile_snapshot_uri)
    model_path = runtime_path(run.model_config_uri)
    profile = read_json(profile_path)
    model = read_json(model_path)
    gates = object_value(profile, "gates")
    model_profile = object_value(profile, "model")
    environment = str(gates.get("target_environment") or "staging")
    namespace = str(gates.get("target_namespace") or "evm-staging")
    architecture = str(model_profile.get("architecture") or "efficientnet-b0")
    candidate_id, artifact_uri, digest, dataset_version = cycle_model_identity(cycle)
    candidate = selected_candidate(model)
    if str(candidate["candidate_id"]) != candidate_id:
        raise LifecycleKubernetesError("selected_candidate_cycle_mismatch")
    deployment_name = str(
        object_value(model, "product").get("target_deployment")
        or lifecycle_deployment_name(architecture, environment)
    )
    image = pinned_image(
        "EVM_LIFECYCLE_SERVING_IMAGE",
        str(candidate.get("serving_image") or ""),
    )
    node_port = NODE_PORTS.get(environment)
    if node_port is None:
        raise LifecycleKubernetesError(f"unsupported_target_environment:{environment}")
    directory = profile_path.parent / "kubernetes" / "serving"
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "namespace.json", namespace_resource(namespace))
    storage = storage_resources(namespace, read_only=True)
    write_json(directory / "storage-pv.json", storage[0])
    write_json(directory / "storage-pvc.json", storage[1])
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": deployment_name,
            "namespace": namespace,
            "labels": lifecycle_labels(run, candidate_id),
        },
        "spec": {
            "replicas": 1,
            "revisionHistoryLimit": 3,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app.kubernetes.io/name": deployment_name}},
            "template": {
                "metadata": {
                    "labels": {
                        **lifecycle_labels(run, candidate_id),
                        "app.kubernetes.io/name": deployment_name,
                    }
                },
                "spec": {
                    "terminationGracePeriodSeconds": 30,
                    "securityContext": pod_security_context(),
                    "containers": [
                        {
                            "name": "serving",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["/bin/sh", "-ec", serving_command()],
                            "ports": [{"name": "http", "containerPort": 8000}],
                            "env": [
                                env("APP_NAME", deployment_name),
                                env("EVM_MODEL_PATH", model_mount_path(artifact_uri)),
                                env("EVM_MODEL_SHA256", digest),
                                env("EVM_MODEL_CANDIDATE_ID", candidate_id),
                                env("EVM_DATASET_VERSION", dataset_version),
                                env("EVM_REQUIRE_CUDA", "true"),
                                env("EVM_HOST_DATA_ROOT", host_data_root()),
                                env("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data"),
                                env("NVIDIA_VISIBLE_DEVICES", "all"),
                                env("NVIDIA_DRIVER_CAPABILITIES", "compute,utility"),
                            ],
                            "resources": {
                                "requests": {"cpu": "2", "memory": "4Gi", "nvidia.com/gpu": "1"},
                                "limits": {"cpu": "8", "memory": "12Gi", "nvidia.com/gpu": "1"},
                            },
                            "startupProbe": probe("/ready", 5, 60),
                            "readinessProbe": probe("/ready", 10, 3),
                            "livenessProbe": probe("/health", 20, 3),
                            "securityContext": container_security_context(),
                            "volumeMounts": common_volume_mounts(read_only_data=True),
                        }
                    ],
                    "volumes": common_volumes(),
                },
            },
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": deployment_name, "namespace": namespace},
        "spec": {
            "type": "NodePort",
            "selector": {"app.kubernetes.io/name": deployment_name},
            "ports": [
                {"name": "http", "port": 8000, "targetPort": "http", "nodePort": node_port}
            ],
        },
    }
    write_json(directory / "deployment.json", deployment)
    write_json(directory / "service.json", service)
    write_kustomization(
        directory,
        [
            "namespace.json",
            "storage-pv.json",
            "storage-pvc.json",
            "deployment.json",
            "service.json",
        ],
    )
    return ServingBundle(
        directory,
        namespace,
        deployment_name,
        f"http://127.0.0.1:{node_port}",
        image,
    )


def build_training_evidence(
    run: LifecycleRun,
    task: TaskAssignment,
    bundle: TrainingBundle,
    *,
    runner: Runner = subprocess.run,
) -> tuple[dict[str, Any], Path]:
    model = read_json(runtime_path(run.model_config_uri))
    configured_candidate = selected_candidate(model)
    matrix = latest_matrix(model)
    candidate = next(
        (
            item
            for item in matrix.get("candidates", [])
            if isinstance(item, dict) and item.get("candidate_id") == bundle.candidate_id
        ),
        {},
    )
    candidate_dir_value = str(candidate.get("artifact_uri") or "")
    candidate_dir = runtime_path(candidate_dir_value) if candidate_dir_value else Path()
    summary_path = candidate_dir / "candidate_summary.json"
    summary = read_json(summary_path) if summary_path.is_file() else {}
    model_artifact_value = str(summary.get("model_artifact") or "")
    model_artifact = runtime_path(model_artifact_value) if model_artifact_value else Path()
    blockers: list[str] = []
    if task.status != "done":
        blockers.append(f"kubernetes_task_{task.status}")
    if summary.get("status") != "pass":
        blockers.append("candidate_summary_not_pass")
    if not model_artifact_value or not model_artifact.is_file():
        blockers.append("trained_model_artifact_missing")
        trained_digest = ""
    else:
        trained_digest = file_sha256(model_artifact)
        expected_digest = str(summary.get("model_sha256") or "")
        if expected_digest and expected_digest != trained_digest:
            blockers.append("trained_model_digest_mismatch")
    job_payload, job_error = kubectl_json(
        runner,
        ["kubectl", "get", "job", bundle.job_name, "-n", bundle.namespace, "-o", "json"],
    )
    if job_error:
        blockers.append(job_error)
    gpu_payload, gpu_error = kubectl_json(runner, ["kubectl", "get", "nodes", "-o", "json"])
    if gpu_error:
        blockers.append(gpu_error)
    gpu_allocatable = total_gpu_allocatable(gpu_payload)
    if gpu_allocatable < 1:
        blockers.append("kubernetes_gpu_not_allocatable")
    evidence = {
        "schema_version": "evm.lifecycle_kubernetes_evidence.v1",
        "run_id": run.run_id,
        "status": "pass" if not blockers else "blocked",
        "completion_claim_allowed": not blockers,
        "candidate_id": bundle.candidate_id,
        "dataset_version": str(summary.get("dataset_version") or ""),
        "mlflow_run_id": str(summary.get("mlflow_run_id") or ""),
        "trained_model_sha256": trained_digest,
        "gpu_allocatable": str(gpu_allocatable),
        "training_image_digest": bundle.image,
        "serving_image_digest": pinned_image(
            "EVM_LIFECYCLE_SERVING_IMAGE",
            str(configured_candidate.get("serving_image") or ""),
        ),
        "namespace": bundle.namespace,
        "job_name": bundle.job_name,
        "job_uid": str(object_value(job_payload, "metadata").get("uid") or ""),
        "job_status": object_value(job_payload, "status"),
        "task_id": task.task_id,
        "task_evidence_uri": task.runtime_evidence_uri,
        "candidate_summary_uri": str(summary_path),
        "model_artifact_uri": model_artifact_value,
        "manifest_dir": str(bundle.manifest_dir),
        "manifest_digest": directory_digest(bundle.manifest_dir),
        "blockers": sorted(set(blockers)),
    }
    control_plane = object_value(model, "control_plane")
    runtime_evidence = object_value(control_plane, "runtime_evidence")
    evidence_path = runtime_path(str(runtime_evidence.get("kubernetes") or ""))
    if not str(runtime_evidence.get("kubernetes") or ""):
        evidence_path = Path(run.artifact_root) / "kubernetes" / "evidence_index.json"
    write_json(evidence_path, evidence)
    return evidence, evidence_path


def selected_candidate_id(model: dict[str, Any]) -> str:
    candidate_id = str(object_value(model, "model_matrix").get("selected_candidate_id") or "")
    if not candidate_id:
        raise LifecycleKubernetesError("selected_candidate_id_missing")
    return candidate_id


def selected_candidate(model: dict[str, Any]) -> dict[str, Any]:
    candidate_id = selected_candidate_id(model)
    candidates = model.get("candidates")
    if not isinstance(candidates, list):
        raise LifecycleKubernetesError("model_candidates_missing")
    candidate = next(
        (
            item
            for item in candidates
            if isinstance(item, dict) and str(item.get("candidate_id") or "") == candidate_id
        ),
        None,
    )
    if candidate is None:
        raise LifecycleKubernetesError("selected_candidate_definition_missing")
    return candidate


def latest_matrix(model: dict[str, Any]) -> dict[str, Any]:
    resources = object_value(model, "resources")
    root = runtime_path(str(resources.get("artifact_root") or ""))
    path = root / "latest_model_matrix.json"
    if not path.is_file():
        raise LifecycleKubernetesError(f"model_matrix_evidence_missing:{path}")
    return read_json(path)


def cycle_model_identity(cycle: CycleRun) -> tuple[str, str, str, str]:
    evaluation = cycle.readiness_evaluation
    if evaluation is None:
        raise LifecycleKubernetesError("readiness_evaluation_missing")
    model_check = next(
        (item for item in evaluation.checks if item.check_id == "model_artifact"),
        None,
    )
    if model_check is None or model_check.status != "pass":
        raise LifecycleKubernetesError("model_artifact_not_ready")
    artifact_uri = str(model_check.evidence_uri or "")
    digest = str(model_check.observed.get("actual_sha256") or "")
    if not artifact_uri or not digest:
        raise LifecycleKubernetesError("model_artifact_identity_missing")
    return evaluation.candidate_id, artifact_uri, digest, evaluation.dataset_version


def namespace_resource(namespace: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": namespace, "labels": {"app.kubernetes.io/part-of": "enterprise-vision-mlops"}},
    }


def storage_resources(namespace: str, *, read_only: bool) -> list[dict[str, Any]]:
    suffix = safe_name(namespace).removeprefix("evm-")
    volume_name = f"evm-{suffix}-large-data"
    mode = os.getenv(
        "EVM_LIFECYCLE_SERVING_ACCESS_MODE"
        if read_only
        else "EVM_LIFECYCLE_TRAINING_ACCESS_MODE",
        "ReadOnlyMany" if read_only else "ReadWriteOnce",
    )
    if mode not in {"ReadWriteOnce", "ReadWriteMany", "ReadOnlyMany"}:
        raise LifecycleKubernetesError(f"unsupported_storage_access_mode:{mode}")
    return [
        {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {"name": volume_name},
            "spec": {
                "capacity": {"storage": "1Ti"},
                "accessModes": [mode],
                "persistentVolumeReclaimPolicy": "Retain",
                "storageClassName": "evm-local-hostpath",
                "claimRef": {"namespace": namespace, "name": "evm-large-data"},
                "hostPath": {"path": docker_desktop_data_path(), "type": "Directory"},
            },
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "evm-large-data", "namespace": namespace},
            "spec": {
                "accessModes": [mode],
                "resources": {"requests": {"storage": "1Ti"}},
                "storageClassName": "evm-local-hostpath",
                "volumeName": volume_name,
            },
        },
    ]


def common_volumes() -> list[dict[str, Any]]:
    return [
        {"name": "wsl-lib", "hostPath": {"path": "/usr/lib/wsl/lib", "type": "Directory"}},
        {"name": "wsl-drivers", "hostPath": {"path": "/usr/lib/wsl/drivers", "type": "Directory"}},
        {"name": "large-data", "persistentVolumeClaim": {"claimName": "evm-large-data"}},
        {"name": "dshm", "emptyDir": {"medium": "Memory", "sizeLimit": "2Gi"}},
        {"name": "tmp", "emptyDir": {}},
    ]


def common_volume_mounts(*, read_only_data: bool) -> list[dict[str, Any]]:
    return [
        {"name": "wsl-lib", "mountPath": "/usr/lib/wsl/lib", "readOnly": True},
        {"name": "wsl-drivers", "mountPath": "/usr/lib/wsl/drivers", "readOnly": True},
        {"name": "large-data", "mountPath": "/mnt/evm-data", "readOnly": read_only_data},
        {"name": "dshm", "mountPath": "/dev/shm"},
        {"name": "tmp", "mountPath": "/tmp"},
    ]


def pod_security_context() -> dict[str, Any]:
    return {
        "runAsNonRoot": True,
        "runAsUser": 10001,
        "runAsGroup": 10001,
        "fsGroup": 10001,
        "seccompProfile": {"type": "RuntimeDefault"},
    }


def container_security_context() -> dict[str, Any]:
    return {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }


def training_command(config_uri: str, *, experiment_search: bool = False) -> str:
    module = (
        "evm.pipelines.experiment_search.run"
        if experiment_search
        else "evm.pipelines.efficientnet_training.run"
    )
    return (
        'DRIVER_DIR=""; '
        'for candidate in /usr/lib/wsl/drivers/*; do '
        'if [ -f "$candidate/libcuda.so.1.1" ]; then DRIVER_DIR="$candidate"; break; fi; '
        'done; test -n "$DRIVER_DIR"; '
        'export LD_LIBRARY_PATH="$DRIVER_DIR:/usr/lib/wsl/lib"; '
        'export PATH="$DRIVER_DIR:/usr/lib/wsl/lib:$PATH"; '
        f"exec python -m {module} {config_uri} --require-pass"
    )


def serving_command() -> str:
    return (
        'DRIVER_DIR=""; '
        'for candidate in /usr/lib/wsl/drivers/*; do '
        'if [ -f "$candidate/libcuda.so.1.1" ]; then DRIVER_DIR="$candidate"; break; fi; '
        'done; test -n "$DRIVER_DIR"; '
        'export LD_LIBRARY_PATH="$DRIVER_DIR:/usr/lib/wsl/lib"; '
        'export PATH="$DRIVER_DIR:/usr/lib/wsl/lib:$PATH"; '
        "exec uvicorn apps.api.efficientnet_serving:app --host 0.0.0.0 --port 8000"
    )


def lifecycle_labels(run: LifecycleRun, candidate_id: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/part-of": "enterprise-vision-mlops",
        "evm.openai.local/lifecycle-run": short_run_id(run.run_id),
        "evm.openai.local/candidate-id": safe_name(candidate_id)[:63],
    }


def probe(path: str, period: int, failures: int) -> dict[str, Any]:
    return {
        "httpGet": {"path": path, "port": "http"},
        "periodSeconds": period,
        "failureThreshold": failures,
    }


def env(name: str, value: str) -> dict[str, str]:
    return {"name": name, "value": value}


def pinned_image(name: str, default: str) -> str:
    image = os.getenv(name, default).strip()
    if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", image):
        raise LifecycleKubernetesError(f"container_image_not_pinned:{name}")
    return image


def model_mount_path(artifact_uri: str) -> str:
    normalized = artifact_uri.replace("\\", "/")
    host_root = host_data_root().replace("\\", "/").rstrip("/")
    mount_root = "/mnt/evm-data"
    if normalized.lower().startswith(host_root.lower()):
        return f"{mount_root}{normalized[len(host_root):]}"
    if normalized.lower().startswith(mount_root.lower()):
        return normalized
    raise LifecycleKubernetesError("model_artifact_outside_data_root")


def host_data_root() -> str:
    return os.getenv(
        "EVM_HOST_DATA_ROOT",
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops",
    ).replace("\\", "/").rstrip("/")


def docker_desktop_data_path() -> str:
    return os.getenv(
        "EVM_DOCKER_DESKTOP_DATA_PATH",
        "/run/desktop/mnt/host/f/EnterpriseMLOps_Data/enterprise-vision-mlops",
    )


def short_run_id(run_id: str) -> str:
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]


def safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-") or "evm"


def object_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleKubernetesError(f"invalid_json:{path}") from exc
    if not isinstance(payload, dict):
        raise LifecycleKubernetesError(f"json_root_not_object:{path}")
    return payload


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_kustomization(directory: Path, resources: list[str]) -> None:
    lines = [
        "apiVersion: kustomize.config.k8s.io/v1beta1",
        "kind: Kustomization",
        "resources:",
        *(f"  - {resource}" for resource in resources),
        "",
    ]
    (directory / "kustomization.yaml").write_text("\n".join(lines), encoding="utf-8")


def kubectl_json(runner: Runner, command: list[str]) -> tuple[dict[str, Any], str | None]:
    result = runner(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        return {}, f"kubectl_query_failed:{command[2]}"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}, f"kubectl_query_invalid:{command[2]}"
    return (payload, None) if isinstance(payload, dict) else ({}, f"kubectl_query_invalid:{command[2]}")


def total_gpu_allocatable(payload: dict[str, Any]) -> int:
    total = 0
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        status = object_value(item, "status")
        allocatable = object_value(status, "allocatable")
        try:
            total += int(str(allocatable.get("nvidia.com/gpu") or "0"))
        except ValueError:
            continue
    return total


def directory_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in directory.iterdir() if item.is_file()):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
