from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DRIVER_PATH_PREFIX = "/usr/lib/wsl/drivers/nv_dispi.inf_amd64_"
TextRunner = Callable[[list[str]], str]


class ReconciliationError(RuntimeError):
    pass


def _run_text(args: list[str]) -> str:
    completed = subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout


def discover_wsl_driver_paths(runner: TextRunner = _run_text) -> list[str]:
    output = runner(
        [
            "wsl.exe",
            "-d",
            "docker-desktop",
            "-u",
            "root",
            "--",
            "sh",
            "-lc",
            "find /usr/lib/wsl/drivers -name nvidia-smi -type f -exec dirname {} \\;",
        ]
    )
    return _valid_discovery(output.splitlines())


class DriverPathChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: Literal["hostPath", "volumeMount", "LD_LIBRARY_PATH"]
    current: str
    proposed: str


class DevicePluginReconciliationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evm.device_plugin_reconciliation.v1"]
    decision: Literal["no_change", "change_required", "blocked"]
    namespace: str
    name: str
    uid: str
    current_driver_path: str | None
    discovered_driver_paths: list[str]
    proposed_driver_path: str | None
    changes: list[DriverPathChange] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    mutation_performed: Literal[False] = False

    @model_validator(mode="after")
    def validate_decision(self) -> "DevicePluginReconciliationPlan":
        if self.decision == "blocked" and not self.blockers:
            raise ValueError("blocked reconciliation requires a blocker")
        if self.decision != "blocked" and self.blockers:
            raise ValueError("non-blocked reconciliation cannot contain blockers")
        if self.decision == "change_required" and not self.changes:
            raise ValueError("change_required reconciliation requires an exact plan")
        if self.decision == "no_change" and self.changes:
            raise ValueError("no_change reconciliation cannot contain changes")
        return self


def _metadata(resource: dict[str, Any]) -> tuple[str, str, str]:
    metadata = resource.get("metadata") or {}
    return (
        str(metadata.get("namespace") or ""),
        str(metadata.get("name") or ""),
        str(metadata.get("uid") or ""),
    )


def _driver_volume(resource: dict[str, Any]) -> tuple[str, str]:
    pod_spec = (((resource.get("spec") or {}).get("template") or {}).get("spec") or {})
    volumes = [item for item in pod_spec.get("volumes") or [] if item.get("name") == "wsl-driver"]
    if len(volumes) != 1:
        raise ReconciliationError(f"wsl_driver_volume_cardinality:{len(volumes)}")
    current = str((volumes[0].get("hostPath") or {}).get("path") or "")
    if not current:
        raise ReconciliationError("wsl_driver_host_path_missing")
    return current, str((volumes[0].get("hostPath") or {}).get("type") or "")


def _driver_container(resource: dict[str, Any]) -> tuple[str, str]:
    pod_spec = (((resource.get("spec") or {}).get("template") or {}).get("spec") or {})
    containers = [
        item
        for item in pod_spec.get("containers") or []
        if item.get("name") == "nvidia-device-plugin-ctr"
    ]
    if len(containers) != 1:
        raise ReconciliationError(f"device_plugin_container_cardinality:{len(containers)}")
    container = containers[0]
    mounts = [item for item in container.get("volumeMounts") or [] if item.get("name") == "wsl-driver"]
    if len(mounts) != 1:
        raise ReconciliationError(f"wsl_driver_mount_cardinality:{len(mounts)}")
    env = [item for item in container.get("env") or [] if item.get("name") == "LD_LIBRARY_PATH"]
    if len(env) != 1:
        raise ReconciliationError(f"ld_library_path_cardinality:{len(env)}")
    return str(mounts[0].get("mountPath") or ""), str(env[0].get("value") or "")


def _valid_discovery(paths: list[str]) -> list[str]:
    return sorted(
        {
            path.rstrip("/")
            for path in paths
            if path.rstrip("/").startswith(DRIVER_PATH_PREFIX)
        }
    )


def plan_device_plugin_reconciliation(
    resource: dict[str, Any],
    discovered_driver_paths: list[str],
) -> DevicePluginReconciliationPlan:
    namespace, name, uid = _metadata(resource)
    identity_blockers = []
    if namespace != "kube-system":
        identity_blockers.append("device_plugin_namespace_mismatch")
    if name != "nvidia-device-plugin-daemonset":
        identity_blockers.append("device_plugin_name_mismatch")
    if not uid:
        identity_blockers.append("device_plugin_uid_missing")

    valid_paths = _valid_discovery(discovered_driver_paths)
    if len(valid_paths) != 1:
        return DevicePluginReconciliationPlan(
            schema_version="evm.device_plugin_reconciliation.v1",
            decision="blocked",
            namespace=namespace,
            name=name,
            uid=uid,
            current_driver_path=None,
            discovered_driver_paths=valid_paths,
            proposed_driver_path=None,
            blockers=identity_blockers + [f"driver_path_discovery_cardinality:{len(valid_paths)}"],
        )

    try:
        current_path, host_path_type = _driver_volume(resource)
        mount_path, library_path = _driver_container(resource)
    except ReconciliationError as exc:
        return DevicePluginReconciliationPlan(
            schema_version="evm.device_plugin_reconciliation.v1",
            decision="blocked",
            namespace=namespace,
            name=name,
            uid=uid,
            current_driver_path=None,
            discovered_driver_paths=valid_paths,
            proposed_driver_path=valid_paths[0],
            blockers=identity_blockers + [str(exc)],
        )

    if host_path_type != "Directory":
        identity_blockers.append("wsl_driver_host_path_type_not_directory")
    if not current_path.startswith(DRIVER_PATH_PREFIX):
        identity_blockers.append("current_driver_path_outside_allowlist")
    if mount_path != current_path:
        identity_blockers.append("driver_mount_path_differs_from_host_path")
    if current_path not in library_path.split(":"):
        identity_blockers.append("driver_path_missing_from_ld_library_path")
    if identity_blockers:
        return DevicePluginReconciliationPlan(
            schema_version="evm.device_plugin_reconciliation.v1",
            decision="blocked",
            namespace=namespace,
            name=name,
            uid=uid,
            current_driver_path=current_path,
            discovered_driver_paths=valid_paths,
            proposed_driver_path=valid_paths[0],
            blockers=identity_blockers,
        )

    proposed = valid_paths[0]
    if current_path == proposed:
        return DevicePluginReconciliationPlan(
            schema_version="evm.device_plugin_reconciliation.v1",
            decision="no_change",
            namespace=namespace,
            name=name,
            uid=uid,
            current_driver_path=current_path,
            discovered_driver_paths=valid_paths,
            proposed_driver_path=proposed,
        )

    return DevicePluginReconciliationPlan(
        schema_version="evm.device_plugin_reconciliation.v1",
        decision="change_required",
        namespace=namespace,
        name=name,
        uid=uid,
        current_driver_path=current_path,
        discovered_driver_paths=valid_paths,
        proposed_driver_path=proposed,
        changes=[
            DriverPathChange(field="hostPath", current=current_path, proposed=proposed),
            DriverPathChange(field="volumeMount", current=mount_path, proposed=proposed),
            DriverPathChange(
                field="LD_LIBRARY_PATH",
                current=library_path,
                proposed=library_path.replace(current_path, proposed, 1),
            ),
        ],
    )
