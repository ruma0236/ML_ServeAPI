from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evm.control_panel.schemas import (
    AcceleratorTelemetry,
    ComputeTelemetry,
    KubernetesResourceSnapshot,
    RuntimeResource,
    RuntimeResourceList,
    State,
)
from evm.operations.scenario_d_supervision import current_process_started_at


KubectlRunner = Callable[[list[str]], dict[str, Any]]
TextCommandRunner = Callable[[list[str]], str]
DEFAULT_NAMESPACES = ("evm-training", "evm-staging", "evm-production")
PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA = 0x800007D2
PDH_VALID_DATA = {0, 1}
GPU_ENGINE_PATTERN = re.compile(
    r"(?P<adapter>luid_0x[0-9a-f]+_0x[0-9a-f]+_phys_\d+)_eng_(?P<engine>\d+)_engtype_(?P<type>.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WindowsGpuEngineSample:
    adapter_luid: str
    utilization_percent: float
    busiest_engine: str
    dedicated_memory_mib: float


class _PdhValueUnion(ctypes.Union):
    _fields_ = [
        ("long_value", wintypes.LONG),
        ("double_value", ctypes.c_double),
        ("large_value", ctypes.c_longlong),
        ("wide_string_value", wintypes.LPWSTR),
    ]


class _PdhFormattedCounterValue(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("status", wintypes.DWORD), ("value", _PdhValueUnion)]


class _PdhFormattedCounterValueItem(ctypes.Structure):
    _fields_ = [("name", wintypes.LPWSTR), ("value", _PdhFormattedCounterValue)]


class WindowsPdhGpuSampler:
    def __init__(self, *, warmup_seconds: float = 1.0) -> None:
        if os.name != "nt":
            raise OSError("Windows PDH GPU counters are only available on Windows.")
        self._warmup_seconds = warmup_seconds
        self._pdh = ctypes.WinDLL("pdh")
        self._query = wintypes.HANDLE()
        self._engine_counter = wintypes.HANDLE()
        self._memory_counter = wintypes.HANDLE()
        self._primed = False
        self._configure_signatures()
        self._check(self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)), "PdhOpenQueryW")
        try:
            self._add_counter(r"\GPU Engine(*)\Utilization Percentage", self._engine_counter)
            self._add_counter(r"\GPU Adapter Memory(*)\Dedicated Usage", self._memory_counter)
        except OSError:
            self.close()
            raise

    def _configure_signatures(self) -> None:
        self._pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
        self._pdh.PdhAddEnglishCounterW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            ctypes.c_size_t,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self._pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        self._pdh.PdhGetFormattedCounterArrayW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self._pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]

    def _add_counter(self, path: str, target: wintypes.HANDLE) -> None:
        self._check(
            self._pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(target)),
            f"PdhAddEnglishCounterW:{path}",
        )

    def sample(self) -> list[WindowsGpuEngineSample]:
        if not self._primed:
            self._collect()
            time.sleep(max(0.1, self._warmup_seconds))
            self._primed = True
        self._collect()
        engine_values = self._read_array(self._engine_counter)
        memory_values = self._read_array(self._memory_counter)
        return aggregate_windows_gpu_engine_samples(engine_values, memory_values)

    def _collect(self) -> None:
        self._check(self._pdh.PdhCollectQueryData(self._query), "PdhCollectQueryData")

    def _read_array(self, counter: wintypes.HANDLE) -> list[tuple[str, float]]:
        buffer_size = wintypes.DWORD(0)
        item_count = wintypes.DWORD(0)
        status = self._status(
            self._pdh.PdhGetFormattedCounterArrayW(
                counter,
                PDH_FMT_DOUBLE,
                ctypes.byref(buffer_size),
                ctypes.byref(item_count),
                None,
            )
        )
        if status != PDH_MORE_DATA:
            self._check(status, "PdhGetFormattedCounterArrayW:size")
        if buffer_size.value == 0:
            return []
        buffer = ctypes.create_string_buffer(buffer_size.value)
        items = ctypes.cast(buffer, ctypes.POINTER(_PdhFormattedCounterValueItem))
        self._check(
            self._pdh.PdhGetFormattedCounterArrayW(
                counter,
                PDH_FMT_DOUBLE,
                ctypes.byref(buffer_size),
                ctypes.byref(item_count),
                items,
            ),
            "PdhGetFormattedCounterArrayW:data",
        )
        return [
            (items[index].name, float(items[index].value.double_value))
            for index in range(item_count.value)
            if items[index].name and items[index].value.status in PDH_VALID_DATA
        ]

    def close(self) -> None:
        if self._query:
            self._pdh.PdhCloseQuery(self._query)
            self._query = wintypes.HANDLE()

    @staticmethod
    def _status(value: int) -> int:
        return value & 0xFFFFFFFF

    @classmethod
    def _check(cls, value: int, operation: str) -> None:
        status = cls._status(value)
        if status != 0:
            raise OSError(f"{operation} failed with PDH status 0x{status:08X}")


_windows_gpu_sampler: WindowsPdhGpuSampler | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def run_kubectl_json(arguments: list[str], *, kubectl: str = "kubectl") -> dict[str, Any]:
    completed = subprocess.run(
        [kubectl, *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def run_text_command(arguments: list[str]) -> str:
    completed = subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=5,
    )
    return completed.stdout


def aggregate_windows_gpu_engine_samples(
    engine_values: Iterable[tuple[str, float]],
    memory_values: Iterable[tuple[str, float]],
) -> list[WindowsGpuEngineSample]:
    dedicated_memory = {
        name.lower(): max(0.0, value / 1024**2)
        for name, value in memory_values
    }
    grouped_engines: dict[tuple[str, str, str], float] = {}
    for name, value in engine_values:
        match = GPU_ENGINE_PATTERN.search(name)
        if match is None:
            continue
        key = (
            match.group("adapter").lower(),
            match.group("engine"),
            match.group("type").replace("_", " "),
        )
        grouped_engines[key] = grouped_engines.get(key, 0.0) + max(0.0, value)

    samples: list[WindowsGpuEngineSample] = []
    adapter_ids = set(dedicated_memory) | {key[0] for key in grouped_engines}
    for adapter_id in sorted(adapter_ids):
        candidates = [
            (engine_type, utilization)
            for (candidate_adapter, _, engine_type), utilization in grouped_engines.items()
            if candidate_adapter == adapter_id
        ]
        busiest_engine, utilization = max(candidates, key=lambda item: item[1], default=("unavailable", 0.0))
        samples.append(
            WindowsGpuEngineSample(
                adapter_luid=adapter_id,
                utilization_percent=round(max(0.0, min(100.0, utilization)), 2),
                busiest_engine=busiest_engine,
                dedicated_memory_mib=round(dedicated_memory.get(adapter_id, 0.0), 2),
            )
        )
    return samples


def sample_windows_gpu_engine_utilization() -> list[WindowsGpuEngineSample]:
    global _windows_gpu_sampler
    if _windows_gpu_sampler is None:
        _windows_gpu_sampler = WindowsPdhGpuSampler()
    return _windows_gpu_sampler.sample()


def attach_windows_gpu_engine_utilization(
    accelerators: list[AcceleratorTelemetry],
    engine_samples: list[WindowsGpuEngineSample],
) -> list[AcceleratorTelemetry]:
    if not accelerators or not engine_samples:
        return accelerators
    remaining = list(engine_samples)
    enriched: list[AcceleratorTelemetry] = []
    for accelerator in accelerators:
        if len(accelerators) == 1:
            match = max(remaining, key=lambda sample: sample.dedicated_memory_mib)
        elif accelerator.memory_used_mib is not None:
            match = min(
                remaining,
                key=lambda sample: abs(sample.dedicated_memory_mib - accelerator.memory_used_mib),
            )
        else:
            enriched.append(accelerator)
            continue
        remaining.remove(match)
        enriched.append(
            accelerator.model_copy(
                update={
                    "engine_utilization_percent": match.utilization_percent,
                    "engine_utilization_source": "windows_pdh",
                    "busiest_engine": match.busiest_engine,
                }
            )
        )
        if not remaining:
            enriched.extend(accelerators[len(enriched) :])
            break
    return enriched


def collect_host_compute_telemetry(
    *,
    now: datetime | None = None,
    gpu_runner: TextCommandRunner = run_text_command,
    cpu_sampler: Callable[[], float | None] | None = None,
    memory_reader: Callable[[], tuple[int, int] | None] | None = None,
    gpu_engine_sampler: Callable[[], list[WindowsGpuEngineSample]] | None = None,
) -> ComputeTelemetry:
    observed_at = isoformat_z(now or utc_now())
    errors: list[str] = []
    cpu_percent: float | None = None
    memory_used: int | None = None
    memory_total: int | None = None
    accelerators: list[AcceleratorTelemetry] = []

    try:
        cpu_percent = (cpu_sampler or sample_cpu_utilization)()
    except (OSError, ValueError) as exc:
        errors.append(f"cpu:{sanitize_error(exc)}")
    try:
        memory = (memory_reader or read_memory_usage)()
        if memory is not None:
            memory_used, memory_total = memory
    except (OSError, ValueError) as exc:
        errors.append(f"memory:{sanitize_error(exc)}")
    try:
        accelerators = collect_nvidia_telemetry(gpu_runner=gpu_runner)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        errors.append(f"gpu:{sanitize_error(exc)}")
    should_sample_windows_engine = gpu_engine_sampler is not None or (
        os.name == "nt" and gpu_runner is run_text_command
    )
    if accelerators and should_sample_windows_engine:
        try:
            engine_samples = (gpu_engine_sampler or sample_windows_gpu_engine_utilization)()
            if engine_samples:
                accelerators = attach_windows_gpu_engine_utilization(accelerators, engine_samples)
            else:
                errors.append("gpu_engine:no Windows GPU engine samples")
        except (OSError, ValueError) as exc:
            errors.append(f"gpu_engine:{sanitize_error(exc)}")

    memory_percent = None
    if memory_used is not None and memory_total:
        memory_percent = round(max(0.0, min(100.0, memory_used / memory_total * 100)), 2)
    available = cpu_percent is not None or memory_total is not None or bool(accelerators)
    if available:
        message = "Host CPU, memory, and accelerator telemetry collected."
        if errors:
            message = f"Partial host telemetry: {'; '.join(errors)}"
    else:
        message = f"Host telemetry unavailable: {'; '.join(errors)}" if errors else "Host telemetry unavailable."
    return ComputeTelemetry(
        status="live" if available else "unavailable",
        observed_at=observed_at,
        cpu_utilization_percent=round(cpu_percent, 2) if cpu_percent is not None else None,
        memory_utilization_percent=memory_percent,
        memory_used_bytes=memory_used,
        memory_total_bytes=memory_total,
        accelerators=accelerators,
        message=message,
    )


def collect_nvidia_telemetry(*, gpu_runner: TextCommandRunner = run_text_command) -> list[AcceleratorTelemetry]:
    output = gpu_runner(
        [
            "nvidia-smi",
            "--query-gpu=index,name,uuid,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit",
            "--format=csv,noheader,nounits",
        ]
    )
    accelerators: list[AcceleratorTelemetry] = []
    for row in csv.reader(output.splitlines(), skipinitialspace=True):
        if not row:
            continue
        if len(row) != 9:
            raise ValueError(f"nvidia_smi_field_count:{len(row)}")
        accelerators.append(
            AcceleratorTelemetry(
                index=int(row[0].strip()),
                name=row[1].strip(),
                uuid=string_or_none(row[2].strip()),
                utilization_percent=optional_float(row[3]),
                memory_used_mib=optional_float(row[4]),
                memory_total_mib=optional_float(row[5]),
                temperature_c=optional_float(row[6]),
                power_draw_w=optional_float(row[7]),
                power_limit_w=optional_float(row[8]),
            )
        )
    return accelerators


def optional_float(value: str) -> float | None:
    normalized = value.strip()
    if not normalized or normalized.lower() in {"n/a", "na", "not supported", "[not supported]"}:
        return None
    return float(normalized)


def sample_cpu_utilization(*, interval_seconds: float = 0.1) -> float | None:
    first = read_cpu_times()
    if first is None:
        return None
    time.sleep(max(0.01, interval_seconds))
    second = read_cpu_times()
    if second is None:
        return None
    idle_delta = second[0] - first[0]
    total_delta = second[1] - first[1]
    if total_delta <= 0:
        return None
    return max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))


def read_cpu_times() -> tuple[int, int] | None:
    if os.name == "nt":
        idle = ctypes.c_ulonglong()
        kernel = ctypes.c_ulonglong()
        user = ctypes.c_ulonglong()
        if not ctypes.windll.kernel32.GetSystemTimes(  # type: ignore[attr-defined]
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            raise OSError("GetSystemTimes failed")
        return idle.value, kernel.value + user.value
    if sys.platform.startswith("linux"):
        values = [int(value) for value in Path("/proc/stat").read_text(encoding="ascii").splitlines()[0].split()[1:]]
        if len(values) < 4:
            return None
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return idle, sum(values)
    return None


def read_memory_usage() -> tuple[int, int] | None:
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            raise OSError("GlobalMemoryStatusEx failed")
        return status.ullTotalPhys - status.ullAvailPhys, status.ullTotalPhys
    if sys.platform.startswith("linux"):
        fields: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            name, value = line.split(":", 1)
            fields[name] = int(value.strip().split()[0]) * 1024
        total = fields.get("MemTotal")
        available = fields.get("MemAvailable")
        if total is None or available is None:
            return None
        return total - available, total
    return None


def collect_kubernetes_snapshot(
    *,
    namespaces: Iterable[str] = DEFAULT_NAMESPACES,
    cluster_context: str = "docker-desktop",
    runner: KubectlRunner = run_kubectl_json,
    now: datetime | None = None,
    compute_telemetry: ComputeTelemetry | None = None,
) -> KubernetesResourceSnapshot:
    observed = now or utc_now()
    observed_at = isoformat_z(observed)
    resources: list[RuntimeResource] = []
    try:
        node_payload = runner(["--context", cluster_context, "get", "nodes", "-o", "json"])
        resources.extend(
            resource_from_kubernetes(item, observed_at=observed_at, cluster_context=cluster_context)
            for item in node_payload.get("items", [])
        )
        for namespace in namespaces:
            payload = runner(
                [
                    "--context",
                    cluster_context,
                    "get",
                    "jobs,deployments,pods,services,persistentvolumeclaims",
                    "-n",
                    namespace,
                    "-o",
                    "json",
                ]
            )
            resources.extend(
                resource_from_kubernetes(item, observed_at=observed_at, cluster_context=cluster_context)
                for item in payload.get("items", [])
            )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return KubernetesResourceSnapshot(
            schema_version="evm.w7.kubernetes_resource_snapshot.v1",
            cluster_context=cluster_context,
            observed_at=observed_at,
            collection_status="fail",
            resource_status="fail",
            message=sanitize_error(exc),
            resources=[],
            compute_telemetry=compute_telemetry,
        )

    status = aggregate_resource_status(resource.status for resource in resources)
    return KubernetesResourceSnapshot(
        schema_version="evm.w7.kubernetes_resource_snapshot.v1",
        cluster_context=cluster_context,
        observed_at=observed_at,
        collection_status="pass",
        resource_status=status,
        message=f"Observed {len(resources)} sanitized Kubernetes resources.",
        resources=resources,
        compute_telemetry=compute_telemetry,
    )


def resource_from_kubernetes(
    item: dict[str, Any], *, observed_at: str, cluster_context: str
) -> RuntimeResource:
    kind = str(item.get("kind", "Unknown"))
    metadata = item.get("metadata", {})
    status_payload = item.get("status", {})
    namespace = str(metadata.get("namespace") or "_cluster")
    name = str(metadata.get("name") or "unnamed")
    labels = metadata.get("labels", {}) or {}
    status, readiness, reason, message = state_for(kind, item)
    pod_spec = workload_pod_spec(kind, item)
    requests = container_requests(pod_spec)
    desired_replicas, ready_replicas = replica_counts(kind, item)
    gpu_capacity = None
    if kind.lower() == "node":
        gpu_capacity = string_or_none(status_payload.get("capacity", {}).get("nvidia.com/gpu"))
    owner_issue = string_or_none(labels.get("evm.openai.local/owner-issue"))
    if owner_issue is None and name.startswith(("evm-b0-", "evm-b7-")):
        owner_issue = "EVM-226"
    if owner_issue is None and kind.lower() == "node":
        owner_issue = "EVM-226"

    return RuntimeResource(
        resource_id=f"{namespace}:{kind}:{name}",
        namespace=namespace,
        kind=kind,
        name=name,
        status=status,
        node_pool=node_pool(item, cluster_context),
        readiness=readiness,
        restarts=restart_count(kind, item),
        cpu_request=string_or_none(requests.get("cpu")),
        memory_request=string_or_none(requests.get("memory")),
        gpu_request=gpu_request(requests.get("nvidia.com/gpu")),
        storage_claim=storage_claim(pod_spec, item),
        storage_root=None,
        last_transition_time=last_transition_time(item),
        owner_issue=owner_issue,
        control_actions=control_actions(kind),
        pressure=pressure_for(status),
        related_stages=related_stages(name, kind),
        observation_source="kubernetes_snapshot",
        observation_status="live",
        observed_at=observed_at,
        observation_message=message,
        reason=reason,
        desired_replicas=desired_replicas,
        ready_replicas=ready_replicas,
        gpu_capacity=gpu_capacity,
    )


def state_for(kind: str, item: dict[str, Any]) -> tuple[State, str, str | None, str | None]:
    normalized = kind.lower()
    status = item.get("status", {})
    conditions = status.get("conditions", []) or []
    if normalized == "job":
        failed = true_condition(conditions, "Failed") or true_condition(conditions, "FailureTarget")
        if failed:
            return "fail", "blocked", failed.get("reason"), failed.get("message")
        complete = true_condition(conditions, "Complete")
        if complete or int(status.get("succeeded") or 0) > 0:
            return "done", "ready", complete.get("reason") if complete else "Complete", None
        if int(status.get("active") or 0) > 0:
            return "running", "progressing", "Active", None
        return "queued", "progressing", "Pending", "Waiting for a schedulable worker."
    if normalized == "deployment":
        desired = int(item.get("spec", {}).get("replicas") or 0)
        ready = int(status.get("readyReplicas") or 0)
        if desired == 0:
            return "queued", "not_requested", "ScaledToZero", "Deployment is intentionally scaled to zero."
        if ready >= desired and int(status.get("availableReplicas") or 0) >= desired:
            return "pass", "ready", "Available", None
        failed = false_condition(conditions, "Progressing")
        if failed:
            return "fail", "blocked", failed.get("reason"), failed.get("message")
        return "running", "progressing", "Progressing", None
    if normalized == "pod":
        phase = str(status.get("phase") or "Unknown")
        scheduled_failure = false_condition(conditions, "PodScheduled")
        if phase == "Failed":
            return "fail", "blocked", status.get("reason"), status.get("message")
        if scheduled_failure:
            return "blocked", "blocked", scheduled_failure.get("reason"), scheduled_failure.get("message")
        if phase == "Succeeded":
            return "done", "ready", "Succeeded", None
        if phase == "Running":
            ready = true_condition(conditions, "Ready")
            return ("pass", "ready", "Ready", None) if ready else ("running", "progressing", "Running", None)
        return "queued", "progressing", phase, status.get("message")
    if normalized == "persistentvolumeclaim":
        phase = str(status.get("phase") or "Pending")
        if phase == "Bound":
            return "pass", "ready", "Bound", None
        if phase == "Lost":
            return "fail", "blocked", "Lost", None
        return "queued", "progressing", phase, None
    if normalized == "node":
        ready = true_condition(conditions, "Ready")
        gpu = status.get("capacity", {}).get("nvidia.com/gpu")
        if not ready:
            failed = false_condition(conditions, "Ready")
            return "fail", "blocked", failed.get("reason") if failed else "NotReady", failed.get("message") if failed else None
        if not gpu:
            return "warn", "ready", "GpuNotAdvertised", "Node is Ready but nvidia.com/gpu is not advertised."
        return "pass", "ready", "Ready", None
    return "pass", "ready", "Observed", None


def workload_pod_spec(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    normalized = kind.lower()
    if normalized in {"job", "deployment"}:
        return item.get("spec", {}).get("template", {}).get("spec", {}) or {}
    if normalized == "pod":
        return item.get("spec", {}) or {}
    return {}


def container_requests(pod_spec: dict[str, Any]) -> dict[str, str]:
    requests: dict[str, str] = {}
    for container in pod_spec.get("containers", []) or []:
        for key, value in (container.get("resources", {}).get("requests", {}) or {}).items():
            requests.setdefault(str(key), str(value))
    return requests


def replica_counts(kind: str, item: dict[str, Any]) -> tuple[int | None, int | None]:
    if kind.lower() != "deployment":
        return None, None
    return (
        int(item.get("spec", {}).get("replicas") or 0),
        int(item.get("status", {}).get("readyReplicas") or 0),
    )


def restart_count(kind: str, item: dict[str, Any]) -> int:
    if kind.lower() != "pod":
        return 0
    return sum(int(status.get("restartCount") or 0) for status in item.get("status", {}).get("containerStatuses", []) or [])


def storage_claim(pod_spec: dict[str, Any], item: dict[str, Any]) -> str | None:
    if str(item.get("kind", "")).lower() == "persistentvolumeclaim":
        return string_or_none(item.get("metadata", {}).get("name"))
    for volume in pod_spec.get("volumes", []) or []:
        claim = volume.get("persistentVolumeClaim", {}).get("claimName")
        if claim:
            return str(claim)
    return None


def node_pool(item: dict[str, Any], cluster_context: str) -> str:
    kind = str(item.get("kind", "")).lower()
    if kind == "node":
        return str(item.get("metadata", {}).get("name") or cluster_context)
    return str(item.get("spec", {}).get("nodeName") or cluster_context)


def last_transition_time(item: dict[str, Any]) -> str | None:
    conditions = item.get("status", {}).get("conditions", []) or []
    timestamps = [condition.get("lastTransitionTime") for condition in conditions if condition.get("lastTransitionTime")]
    if timestamps:
        return str(sorted(timestamps)[-1])
    return string_or_none(item.get("metadata", {}).get("creationTimestamp"))


def true_condition(conditions: list[dict[str, Any]], condition_type: str) -> dict[str, Any] | None:
    return next(
        (condition for condition in conditions if condition.get("type") == condition_type and condition.get("status") == "True"),
        None,
    )


def false_condition(conditions: list[dict[str, Any]], condition_type: str) -> dict[str, Any] | None:
    return next(
        (condition for condition in conditions if condition.get("type") == condition_type and condition.get("status") == "False"),
        None,
    )


def gpu_request(value: Any) -> str | None:
    return f"{value} x GPU" if value not in {None, "", "0", 0} else None


def string_or_none(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def control_actions(kind: str) -> list[str]:
    normalized = kind.lower()
    if normalized == "deployment":
        return ["view", "restart_dry_run", "scale_dry_run"]
    if normalized == "job":
        return ["view", "rerun_dry_run", "cancel_dry_run"]
    return ["view"]


def pressure_for(status: State) -> State:
    if status in {"fail", "blocked"}:
        return "fail"
    if status == "warn":
        return "warn"
    if status in {"running", "queued"}:
        return status
    return "pass"


def related_stages(name: str, kind: str) -> list[str]:
    if name == "evm-b0-expedited-training":
        return ["EfficientNet B0 Kubernetes Training"]
    if name == "evm-b0-production":
        return ["EfficientNet B0 Kubernetes Serving"]
    if name == "evm-b7-training":
        return ["EfficientNet B7 Kubernetes Training"]
    if name == "evm-b7-serving":
        return ["EfficientNet B7 Kubernetes Serving"]
    if kind.lower() == "node":
        return ["Kubernetes Capacity"]
    return []


def aggregate_resource_status(statuses: Iterable[State]) -> State:
    order: dict[State, int] = {
        "fail": 8,
        "blocked": 7,
        "cancelled": 6,
        "warn": 5,
        "running": 4,
        "queued": 3,
        "unknown": 2,
        "pass": 1,
        "done": 0,
    }
    values = list(statuses)
    return max(values, key=lambda value: order[value]) if values else "unknown"


def sanitize_error(exc: BaseException) -> str:
    message = " ".join(str(exc).split())
    return message[:500] or exc.__class__.__name__


def write_snapshot(
    snapshot: KubernetesResourceSnapshot,
    output_path: Path,
    *,
    history_root: Path | None = None,
    max_history: int = 500,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot.model_dump(mode="json")
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = output_path.with_suffix(f"{output_path.suffix}.{os.getpid()}.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    replace_with_retry(temporary, output_path)
    if history_root is None:
        return
    digest = snapshot_state_digest(payload)
    digest_path = history_root.parent / ".latest_digest"
    previous_digest = digest_path.read_text(encoding="ascii").strip() if digest_path.exists() else ""
    if digest == previous_digest:
        return
    try:
        history_root.mkdir(parents=True, exist_ok=True)
        stamp = snapshot.observed_at.replace(":", "").replace("-", "")
        history_path = history_root / f"{stamp}-{digest[:12]}.json"
        history_path.write_text(rendered, encoding="utf-8")
        digest_path.write_text(digest + "\n", encoding="ascii")
        histories = sorted(history_root.glob("*.json"), key=lambda path: path.stat().st_mtime)
        for stale_path in histories[:-max_history]:
            stale_path.unlink()
    except OSError as exc:
        print(f"Kubernetes observer history write failed: {sanitize_error(exc)}", file=sys.stderr)


def snapshot_state_digest(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("observed_at", None)
    canonical["resources"] = [
        {key: value for key, value in resource.items() if key != "observed_at"}
        for resource in payload.get("resources", [])
    ]
    telemetry = canonical.get("compute_telemetry")
    if isinstance(telemetry, dict):
        telemetry.pop("observed_at", None)
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()


def replace_with_retry(source: Path, target: Path, *, attempts: int = 20) -> None:
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 == attempts:
                raise
            time.sleep(min(0.05 * (attempt + 1), 0.5))


def load_kubernetes_resource_snapshot(
    path: Path | None = None,
    *,
    now: datetime | None = None,
    stale_after_seconds: float | None = None,
) -> RuntimeResourceList:
    source_path = path or Path(
        os.getenv(
            "EVM_KUBERNETES_RESOURCE_SNAPSHOT_PATH",
            "/app/artifacts/w7/kubernetes_observer/latest.json",
        )
    )
    stale_limit = stale_after_seconds
    if stale_limit is None:
        try:
            stale_limit = max(1.0, float(os.getenv("EVM_KUBERNETES_SNAPSHOT_STALE_SECONDS", "15")))
        except ValueError:
            stale_limit = 15.0
    if not source_path.exists():
        return RuntimeResourceList(
            resources=[],
            observation_status="unavailable",
            snapshot_uri=str(source_path),
            observation_message="Kubernetes resource snapshot is not available.",
        )
    try:
        snapshot = KubernetesResourceSnapshot.model_validate_json(source_path.read_text(encoding="utf-8"))
        age_seconds = max(0.0, ((now or utc_now()) - parse_timestamp(snapshot.observed_at)).total_seconds())
    except (OSError, ValueError) as exc:
        return RuntimeResourceList(
            resources=[],
            observation_status="unavailable",
            snapshot_uri=str(source_path),
            observation_message=f"Kubernetes snapshot validation failed: {sanitize_error(exc)}",
        )
    if snapshot.collection_status == "fail":
        observation_status = "unavailable"
    else:
        observation_status = "live" if age_seconds <= stale_limit else "stale"
    resources = [
        resource.model_copy(update={"observation_status": observation_status})
        for resource in snapshot.resources
    ]
    telemetry = snapshot.compute_telemetry
    if telemetry is not None and age_seconds > stale_limit:
        telemetry = telemetry.model_copy(update={"status": "stale"})
    return RuntimeResourceList(
        resources=resources,
        observation_status=observation_status,
        observed_at=snapshot.observed_at,
        snapshot_age_seconds=round(age_seconds, 3),
        cluster_context=snapshot.cluster_context,
        snapshot_uri=str(source_path),
        observation_message=snapshot.message,
        compute_telemetry=telemetry,
    )


def merge_runtime_resources(
    projected: list[RuntimeResource], observed: RuntimeResourceList
) -> RuntimeResourceList:
    resources = {
        resource.resource_id: resource.model_copy(
            update={"observation_source": "cycle_projection", "observation_status": "projected"}
        )
        for resource in projected
    }
    for resource in observed.resources:
        resources[resource.resource_id] = resource
    return observed.model_copy(update={"resources": [resources[key] for key in sorted(resources)]})


def observer_loop(
    *,
    output_path: Path,
    history_root: Path,
    namespaces: tuple[str, ...],
    cluster_context: str,
    interval_seconds: float,
    max_history: int,
) -> None:
    process_started_at = current_process_started_at().isoformat()
    process_instance_id = os.getenv("EVM_PROCESS_INSTANCE_ID") or f"observer-{os.getpid()}"
    while True:
        iteration_started = time.monotonic()
        observed = utc_now()
        compute_telemetry = collect_host_compute_telemetry(now=observed)
        snapshot = collect_kubernetes_snapshot(
            namespaces=namespaces,
            cluster_context=cluster_context,
            now=observed,
            compute_telemetry=compute_telemetry,
        )
        snapshot = snapshot.model_copy(
            update={
                "observer_id": os.getenv("EVM_OBSERVER_ID") or "windows-docker-desktop-observer",
                "pid": os.getpid(),
                "process_started_at": process_started_at,
                "process_instance_id": process_instance_id,
                "source_commit": os.getenv("EVM_GIT_COMMIT") or None,
                "source_branch": os.getenv("EVM_GIT_BRANCH") or None,
                "supervisor_lease_id": os.getenv("EVM_SUPERVISOR_LEASE_ID") or None,
                "fencing_token": int(os.getenv("EVM_SUPERVISOR_FENCING_TOKEN", "0")) or None,
            }
        )
        try:
            write_snapshot(snapshot, output_path, history_root=history_root, max_history=max_history)
        except OSError as exc:
            print(f"Kubernetes observer snapshot write failed: {sanitize_error(exc)}", file=sys.stderr)
        if interval_seconds <= 0:
            return
        elapsed = time.monotonic() - iteration_started
        time.sleep(max(0.0, interval_seconds - elapsed))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write sanitized Kubernetes resource snapshots.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--history-root", type=Path)
    parser.add_argument("--namespaces", default=",".join(DEFAULT_NAMESPACES))
    parser.add_argument("--cluster-context", default="docker-desktop")
    parser.add_argument("--interval-seconds", type=float, default=0.0)
    parser.add_argument("--max-history", type=int, default=500)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    namespaces = tuple(value.strip() for value in args.namespaces.split(",") if value.strip())
    history_root = args.history_root or args.output.parent / "history"
    observer_loop(
        output_path=args.output,
        history_root=history_root,
        namespaces=namespaces,
        cluster_context=args.cluster_context,
        interval_seconds=max(0.0, args.interval_seconds),
        max_history=max(1, args.max_history),
    )


if __name__ == "__main__":
    main()
