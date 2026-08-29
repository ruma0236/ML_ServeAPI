from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from evm.scale_validation.x1_contract import MODEL_IDS, X1Contract


TRITON_IMAGE = (
    "nvcr.io/nvidia/tritonserver:25.08-py3@"
    "sha256:f836551575df7c9fb71144073845c6b3911de57db91a8c95e0687a4d2ac9f7a5"
)
TRITON_IMAGE_DIGEST = "sha256:f836551575df7c9fb71144073845c6b3911de57db91a8c95e0687a4d2ac9f7a5"
NAMESPACE = "evm-platform"
API_NAME = "evm-x1-api"
TRITON_NAME = "evm-x1-triton"


class X1TopologyError(RuntimeError):
    pass


def kubernetes_resource_list(
    contract: X1Contract,
    *,
    suite_id: str,
    source_revision: str,
    api_image: str,
    api_replicas: int,
    cpu_workers: int,
    profile_relative_root: str,
    runtime_manifest_relative_path: str,
    database_url: str,
    database_schema: str,
    lease_id: str,
    fencing_token: str,
) -> dict[str, Any]:
    contract.assert_unchanged()
    if api_replicas not in {1, 2} or cpu_workers not in {1, 2, 4}:
        raise X1TopologyError("x1_topology_axis")
    if len(source_revision) != 40 or not suite_id.startswith("x1-"):
        raise X1TopologyError("x1_topology_source_identity")
    if not profile_relative_root or Path(profile_relative_root).is_absolute():
        raise X1TopologyError("x1_topology_profile_path")
    if not runtime_manifest_relative_path or Path(runtime_manifest_relative_path).is_absolute():
        raise X1TopologyError("x1_topology_manifest_path")
    labels = {
        "app.kubernetes.io/part-of": "enterprise-vision-mlops",
        "evm.openai.local/scenario": "s8-v4-x1",
        "evm.openai.local/suite": suite_id,
    }
    triton_labels = {**labels, "app.kubernetes.io/name": TRITON_NAME}
    api_labels = {**labels, "app.kubernetes.io/name": API_NAME}
    triton_args = [
        "tritonserver",
        f"--model-repository=/mnt/evm-data/{profile_relative_root}",
        "--model-control-mode=explicit",
        *[f"--load-model={model_id}" for model_id in MODEL_IDS],
        "--strict-readiness=true",
        "--exit-on-error=true",
        "--allow-metrics=true",
        "--allow-gpu-metrics=true",
        "--trace-config=mode=opentelemetry",
        "--trace-config=opentelemetry,url=http://host.docker.internal:4318/v1/traces",
        "--trace-config=level=TIMESTAMPS",
        "--trace-config=rate=1",
        "--trace-config=count=-1",
    ]
    items = [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": TRITON_NAME, "namespace": NAMESPACE, "labels": triton_labels},
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": {"app.kubernetes.io/name": TRITON_NAME}},
                "template": {
                    "metadata": {"labels": triton_labels},
                    "spec": {
                        "terminationGracePeriodSeconds": 30,
                        "containers": [
                            {
                                "name": "triton",
                                "image": TRITON_IMAGE,
                                "imagePullPolicy": "IfNotPresent",
                                "command": triton_args,
                                "ports": [
                                    {"name": "http", "containerPort": 8000},
                                    {"name": "metrics", "containerPort": 8002},
                                ],
                                "env": [
                                    {"name": "NVIDIA_VISIBLE_DEVICES", "value": "all"},
                                    {
                                        "name": "NVIDIA_DRIVER_CAPABILITIES",
                                        "value": "compute,utility",
                                    },
                                ],
                                "resources": {
                                    "requests": {
                                        "cpu": "1",
                                        "memory": "2Gi",
                                        "nvidia.com/gpu": "1",
                                    },
                                    "limits": {"cpu": "8", "memory": "12Gi", "nvidia.com/gpu": "1"},
                                },
                                "startupProbe": {
                                    "httpGet": {"path": "/v2/health/ready", "port": "http"},
                                    "periodSeconds": 2,
                                    "failureThreshold": 60,
                                },
                                "readinessProbe": {
                                    "httpGet": {"path": "/v2/health/ready", "port": "http"},
                                    "periodSeconds": 2,
                                    "failureThreshold": 3,
                                },
                                "volumeMounts": [
                                    {
                                        "name": "large-data",
                                        "mountPath": "/mnt/evm-data",
                                        "readOnly": True,
                                    }
                                ],
                            }
                        ],
                        "volumes": [
                            {
                                "name": "large-data",
                                "persistentVolumeClaim": {
                                    "claimName": "evm-large-data",
                                    "readOnly": True,
                                },
                            }
                        ],
                    },
                },
            },
        },
        _service(
            TRITON_NAME,
            triton_labels,
            [
                {"name": "http", "port": 8000, "targetPort": "http", "nodePort": 31121},
                {
                    "name": "metrics",
                    "port": 8002,
                    "targetPort": "metrics",
                    "nodePort": 31122,
                },
            ],
        ),
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": API_NAME, "namespace": NAMESPACE, "labels": api_labels},
            "spec": {
                "replicas": api_replicas,
                "strategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
                },
                "selector": {"matchLabels": {"app.kubernetes.io/name": API_NAME}},
                "template": {
                    "metadata": {"labels": api_labels},
                    "spec": {
                        "terminationGracePeriodSeconds": 40,
                        "containers": [
                            {
                                "name": "api",
                                "image": api_image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": ["python", "-m", "uvicorn"],
                                "args": [
                                    "main:app",
                                    "--host",
                                    "0.0.0.0",
                                    "--port",
                                    "8000",
                                    "--workers",
                                    str(cpu_workers),
                                ],
                                "ports": [{"name": "http", "containerPort": 8000}],
                                "env": _api_environment(
                                    suite_id=suite_id,
                                    source_revision=source_revision,
                                    api_replicas=api_replicas,
                                    cpu_workers=cpu_workers,
                                    runtime_manifest_relative_path=runtime_manifest_relative_path,
                                    database_url=database_url,
                                    database_schema=database_schema,
                                    lease_id=lease_id,
                                    fencing_token=fencing_token,
                                ),
                                "resources": {
                                    "requests": {"cpu": "500m", "memory": "512Mi"},
                                    "limits": {"cpu": "8", "memory": "4Gi"},
                                },
                                "startupProbe": {
                                    "httpGet": {
                                        "path": "/control-panel/v1/scenario-workloads/x1/ready",
                                        "port": "http",
                                    },
                                    "periodSeconds": 2,
                                    "failureThreshold": 60,
                                },
                                "readinessProbe": {
                                    "httpGet": {
                                        "path": "/control-panel/v1/scenario-workloads/x1/ready",
                                        "port": "http",
                                    },
                                    "periodSeconds": 2,
                                    "failureThreshold": 3,
                                },
                                "lifecycle": {
                                    "preStop": {
                                        "exec": {
                                            "command": [
                                                "python",
                                                "-m",
                                                "evm.control_panel.api_rollout",
                                                "--timeout-seconds",
                                                "20",
                                            ]
                                        }
                                    }
                                },
                                "volumeMounts": [
                                    {
                                        "name": "large-data",
                                        "mountPath": "/mnt/evm-data",
                                        "readOnly": True,
                                    },
                                    {"name": "runtime", "mountPath": "/app/runtime"},
                                ],
                            }
                        ],
                        "volumes": [
                            {
                                "name": "large-data",
                                "persistentVolumeClaim": {
                                    "claimName": "evm-large-data",
                                    "readOnly": True,
                                },
                            },
                            {"name": "runtime", "emptyDir": {}},
                        ],
                    },
                },
            },
        },
        _service(
            API_NAME,
            api_labels,
            [{"name": "http", "port": 8000, "targetPort": "http", "nodePort": 31120}],
        ),
    ]
    bundle = {"apiVersion": "v1", "kind": "List", "items": items}
    validate_kubernetes_resource_list(
        bundle,
        suite_id=suite_id,
        source_revision=source_revision,
        api_image=api_image,
        api_replicas=api_replicas,
        cpu_workers=cpu_workers,
    )
    return bundle


def validate_kubernetes_resource_list(
    bundle: Mapping[str, Any],
    *,
    suite_id: str,
    source_revision: str,
    api_image: str,
    api_replicas: int,
    cpu_workers: int,
) -> None:
    if bundle.get("apiVersion") != "v1" or bundle.get("kind") != "List":
        raise X1TopologyError("x1_kubernetes_bundle_schema")
    items = bundle.get("items")
    if not isinstance(items, list) or len(items) != 4:
        raise X1TopologyError("x1_kubernetes_resource_set")
    keyed = {(item.get("kind"), item.get("metadata", {}).get("name")): item for item in items}
    expected = {
        ("Deployment", TRITON_NAME),
        ("Service", TRITON_NAME),
        ("Deployment", API_NAME),
        ("Service", API_NAME),
    }
    if set(keyed) != expected:
        raise X1TopologyError("x1_kubernetes_resource_identity")
    triton = keyed[("Deployment", TRITON_NAME)]
    triton_container = triton["spec"]["template"]["spec"]["containers"]
    if not isinstance(triton_container, list) or len(triton_container) != 1:
        raise X1TopologyError("x1_triton_container_count")
    triton_container = triton_container[0]
    if triton_container.get("image") != TRITON_IMAGE:
        raise X1TopologyError("x1_triton_image")
    command = triton_container.get("command")
    if not isinstance(command, list) or set(
        item for item in command if isinstance(item, str) and item.startswith("--load-model=")
    ) != {f"--load-model={model_id}" for model_id in MODEL_IDS}:
        raise X1TopologyError("x1_triton_explicit_models")
    if triton_container["resources"]["limits"].get("nvidia.com/gpu") != "1":
        raise X1TopologyError("x1_triton_gpu_limit")
    api = keyed[("Deployment", API_NAME)]
    if api["spec"].get("replicas") != api_replicas:
        raise X1TopologyError("x1_api_replica_readback")
    api_container = api["spec"]["template"]["spec"]["containers"]
    if not isinstance(api_container, list) or len(api_container) != 1:
        raise X1TopologyError("x1_api_container_count")
    api_container = api_container[0]
    if api_container.get("image") != api_image:
        raise X1TopologyError("x1_api_image")
    args = api_container.get("args")
    if not isinstance(args, list) or args[-2:] != ["--workers", str(cpu_workers)]:
        raise X1TopologyError("x1_server_worker_command")
    env = _env_map(api_container.get("env"))
    if (
        env.get("GIT_COMMIT") != source_revision
        or env.get("EVM_X1_SUITE_ID") != suite_id
        or env.get("EVM_X1_API_REPLICAS") != str(api_replicas)
        or env.get("EVM_X1_CPU_WORKERS") != str(cpu_workers)
    ):
        raise X1TopologyError("x1_api_environment_identity")


def validate_runtime_topology_readback(
    snapshot: Mapping[str, Any], *, expected_replicas: int, expected_workers: int
) -> None:
    if snapshot.get("triton_pods_ready") != 1 or snapshot.get("triton_gpu_limits") != 1:
        raise X1TopologyError("x1_runtime_triton_topology")
    if snapshot.get("api_pods_ready") != expected_replicas:
        raise X1TopologyError("x1_runtime_api_replicas")
    pod_uids = snapshot.get("observed_api_pod_uids")
    if (
        not isinstance(pod_uids, list)
        or len(pod_uids) != expected_replicas
        or len(set(pod_uids)) != len(pod_uids)
    ):
        raise X1TopologyError("x1_runtime_api_pod_attribution")
    workers = snapshot.get("observed_worker_slots_by_pod")
    if not isinstance(workers, Mapping) or set(workers) != set(pod_uids):
        raise X1TopologyError("x1_runtime_worker_attribution")
    if any(
        not isinstance(slots, list)
        or len(slots) != expected_workers
        or len(set(slots)) != expected_workers
        for slots in workers.values()
    ):
        raise X1TopologyError("x1_runtime_worker_count")
    if snapshot.get("client_lanes_are_server_workers") is not False:
        raise X1TopologyError("x1_client_server_topology_conflated")


def _service(name: str, labels: Mapping[str, str], ports: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": NAMESPACE, "labels": dict(labels)},
        "spec": {
            "type": "NodePort",
            "selector": {"app.kubernetes.io/name": name},
            "ports": ports,
        },
    }


def _api_environment(
    *,
    suite_id: str,
    source_revision: str,
    api_replicas: int,
    cpu_workers: int,
    runtime_manifest_relative_path: str,
    database_url: str,
    database_schema: str,
    lease_id: str,
    fencing_token: str,
) -> list[dict[str, Any]]:
    direct = {
        "APP_NAME": "enterprise-vision-mlops-api-x1",
        "GIT_COMMIT": source_revision,
        "GIT_BRANCH": "codex/distributed-scale-validation-plan",
        "EVM_X1_ENABLED": "1",
        "EVM_X1_SUITE_ID": suite_id,
        "EVM_X1_API_REPLICAS": str(api_replicas),
        "EVM_X1_CPU_WORKERS": str(cpu_workers),
        "EVM_X1_TRITON_URL": f"http://{TRITON_NAME}:8000",
        "EVM_X1_RUNTIME_MANIFEST": f"/mnt/evm-data/{runtime_manifest_relative_path}",
        "EVM_X1_DATABASE_URL": database_url,
        "EVM_X1_DATABASE_SCHEMA": database_schema,
        "EVM_X1_LEASE_ID": lease_id,
        "EVM_X1_FENCING_TOKEN": fencing_token,
        "EVM_X1_MAX_OUTSTANDING_PER_WORKER": "32",
        "EVM_X1_ADMISSION_WAIT_SECONDS": "0.05",
        "EVM_OTEL_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://host.docker.internal:4318/v1/traces",
        "OTEL_SERVICE_NAMESPACE": "enterprise-mlops-x1",
        "EVM_TASK_ADMISSION_MODE": "legacy",
    }
    env = [{"name": key, "value": value} for key, value in sorted(direct.items())]
    env.extend(
        [
            {
                "name": "EVM_POD_UID",
                "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}},
            },
            {
                "name": "EVM_POD_NAME",
                "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}},
            },
            {
                "name": "OTEL_SERVICE_INSTANCE_ID",
                "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}},
            },
        ]
    )
    return env


def _env_map(records: Any) -> dict[str, str]:
    if not isinstance(records, list):
        raise X1TopologyError("x1_api_environment_schema")
    result: dict[str, str] = {}
    for record in records:
        if not isinstance(record, Mapping) or not isinstance(record.get("name"), str):
            raise X1TopologyError("x1_api_environment_record")
        name = str(record["name"])
        if name in result:
            raise X1TopologyError("x1_api_environment_duplicate")
        if "value" in record:
            result[name] = str(record["value"])
    return result
