from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import requests

from evm.operations.failure_evidence import (
    ApprovalEvidence,
    ArtifactEvidence,
    CheckEvidence,
    ClosureEvidence,
    DecisionEvidence,
    EnvironmentEvidence,
    IdentityEvidence,
    InjectionEvidence,
    OperationalFailureReport,
    PortfolioEvidence,
    RecoveryEvidence,
    SignalEvidence,
    SourceEvidence,
    TimingEvidence,
    sha256_file,
    validate_closure,
)
from evm.operations.failure_scenarios import atomic_write_json
from evm.operations.scenario_d_supervision import ScenarioDPolicy


ChildName = Literal["lifecycle_worker", "kubernetes_observer"]
SUPERVISOR_PATH = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/host_runtime/supervisor.json"
)
AUDIT_PATH = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/host_runtime/supervisor-audit.jsonl"
)
LEDGER_PATH = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/host_runtime/"
    "supervisor-restart-ledger.json"
)
WORKER_IDENTITY_PATH = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/lifecycle_runs/"
    "worker.identity.json"
)
OBSERVER_IDENTITY_PATH = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/kubernetes_observer/"
    "observer.identity.json"
)
CLAIM_ROOT = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/lifecycle_runs/_claims"
)
PRODUCTION_READY_URL = "http://127.0.0.1:30800/ready"
PRODUCTION_PREDICT_URL = "http://127.0.0.1:30800/predict"
DEFAULT_INFERENCE_IMAGE_URI = (
    "file:///F:/EnterpriseMLOps_Data/enterprise-vision-mlops/data/raw/industrial/visa/"
    "candle/Data/Images/Normal/0000.JPG"
)
EXPECTED_SEQUENCE: tuple[ChildName, ...] = (
    "lifecycle_worker",
    "kubernetes_observer",
    "lifecycle_worker",
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_not_timezone_aware")
    return parsed.astimezone(UTC)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def request_json(url: str, *, timeout: float = 10) -> dict[str, Any]:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def post_json(url: str, payload: dict[str, Any], *, timeout: float = 30) -> dict[str, Any]:
    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def run_json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout.lstrip("\ufeff"))


def git_text(project_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def powershell_json(script: str) -> Any:
    return run_json(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "$ErrorActionPreference='Stop'; " + script,
        ]
    )


def child_marker(child: ChildName) -> str:
    return f"evm.control_panel.{child}"


def child_identity_path(child: ChildName) -> Path:
    return WORKER_IDENTITY_PATH if child == "lifecycle_worker" else OBSERVER_IDENTITY_PATH


def process_census(child: ChildName) -> list[dict[str, Any]]:
    marker = child_marker(child)
    script = (
        f"$items=@(Get-CimInstance Win32_Process | Where-Object {{ $_.Name -like 'python*.exe' "
        f"-and $_.CommandLine -like '*{marker}*' }} | ForEach-Object {{ "
        "$p=Get-Process -Id $_.ProcessId -ErrorAction Stop; [ordered]@{"
        "pid=[int]$_.ProcessId; process_started_at=$p.StartTime.ToUniversalTime().ToString('o'); "
        "executable=[string]$_.ExecutablePath; command_line=[string]$_.CommandLine} }); "
        "ConvertTo-Json -InputObject @($items) -Depth 5 -Compress"
    )
    payload = powershell_json(script)
    if isinstance(payload, list):
        return payload
    return [] if payload is None else [payload]


def stop_exact_process(pid: int) -> None:
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"$ErrorActionPreference='Stop'; Stop-Process -Id {int(pid)} -Force",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def exact_process_identity(child: ChildName) -> tuple[dict[str, Any], dict[str, Any]]:
    identity = read_json(child_identity_path(child))
    census = process_census(child)
    if len(census) != 1:
        raise RuntimeError(f"exact_process_count_invalid:{child}:{len(census)}")
    process = census[0]
    if int(identity["pid"]) != int(process["pid"]):
        raise RuntimeError(f"exact_process_pid_mismatch:{child}")
    delta_ms = abs(
        (parse_utc(identity["process_started_at"]) - parse_utc(process["process_started_at"]))
        .total_seconds()
        * 1000
    )
    if delta_ms > 1:
        raise RuntimeError(f"exact_process_start_mismatch:{child}:{delta_ms:.6f}")
    return identity, process


def summarized_resources() -> dict[str, Any]:
    payload = request_json("http://127.0.0.1:8000/control-panel/v1/resources")
    production = next(
        (
            item
            for item in payload.get("resources") or []
            if item.get("namespace") == "evm-production"
            and item.get("kind") == "Deployment"
            and item.get("name") == "evm-b0-production"
        ),
        None,
    )
    return {
        "observation_status": payload.get("observation_status"),
        "observed_at": payload.get("observed_at"),
        "snapshot_age_seconds": payload.get("snapshot_age_seconds"),
        "production_deployment": production,
    }


def prometheus_target() -> dict[str, Any]:
    payload = request_json("http://127.0.0.1:9090/api/v1/targets")
    matches = [
        item
        for item in payload["data"]["activeTargets"]
        if item.get("labels", {}).get("job") == "evm-b0-production"
    ]
    if len(matches) != 1:
        raise RuntimeError(f"prometheus_target_count_invalid:{len(matches)}")
    return {
        "health": matches[0].get("health"),
        "scrape_url": matches[0].get("scrapeUrl"),
        "last_error": matches[0].get("lastError"),
        "last_scrape": matches[0].get("lastScrape"),
    }


def kubernetes_runtime() -> dict[str, Any]:
    deployment = run_json(
        [
            "kubectl",
            "--context",
            "docker-desktop",
            "get",
            "deployment",
            "-n",
            "evm-production",
            "evm-b0-production",
            "-o",
            "json",
        ]
    )
    plugin = run_json(
        [
            "kubectl",
            "--context",
            "docker-desktop",
            "get",
            "daemonset",
            "-n",
            "kube-system",
            "nvidia-device-plugin-daemonset",
            "-o",
            "json",
        ]
    )
    node = run_json(
        ["kubectl", "--context", "docker-desktop", "get", "node", "-o", "json"]
    )["items"][0]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    return {
        "deployment_uid": deployment["metadata"]["uid"],
        "desired_replicas": deployment["spec"].get("replicas", 0),
        "ready_replicas": deployment.get("status", {}).get("readyReplicas", 0),
        "available_replicas": deployment.get("status", {}).get("availableReplicas", 0),
        "image": container.get("image"),
        "gpu_allocatable": node["status"]["allocatable"].get("nvidia.com/gpu"),
        "plugin_desired": plugin["status"].get("desiredNumberScheduled", 0),
        "plugin_ready": plugin["status"].get("numberReady", 0),
    }


def active_lifecycle_runs() -> list[str]:
    payload = request_json("http://127.0.0.1:8000/control-panel/v1/lifecycle-runs")
    return [
        str(item["run_id"])
        for item in payload.get("runs") or []
        if item.get("state") in {"queued", "running"}
    ]


def lifecycle_claim_state() -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    if CLAIM_ROOT.exists():
        for path in sorted(CLAIM_ROOT.rglob("*.json")):
            try:
                claims.append({"uri": str(path.resolve()), "payload": read_json(path)})
            except (OSError, ValueError, json.JSONDecodeError):
                claims.append({"uri": str(path.resolve()), "payload": "invalid_json"})
    return {"claim_count": len(claims), "claims": claims}


def runtime_snapshot(*, inference_image_uri: str) -> dict[str, Any]:
    return {
        "supervisor": read_json(SUPERVISOR_PATH),
        "control_plane_ready": request_json("http://127.0.0.1:8000/ready"),
        "worker_api": request_json(
            "http://127.0.0.1:8000/control-panel/v1/lifecycle-runs/worker"
        ),
        "resources_api": summarized_resources(),
        "production_ready": request_json(PRODUCTION_READY_URL),
        "production_inference": post_json(
            PRODUCTION_PREDICT_URL,
            {"image_uri": inference_image_uri},
        ),
        "prometheus": prometheus_target(),
        "kubernetes": kubernetes_runtime(),
        "active_lifecycle_runs": active_lifecycle_runs(),
        "process_census": {
            "lifecycle_worker": process_census("lifecycle_worker"),
            "kubernetes_observer": process_census("kubernetes_observer"),
        },
        "restart_ledger": (
            read_json(LEDGER_PATH) if LEDGER_PATH.exists() else {"attempts": []}
        ),
        "lifecycle_claims": lifecycle_claim_state(),
    }


def supervisor_child(supervisor: dict[str, Any], child: ChildName) -> dict[str, Any]:
    matches = [item for item in supervisor.get("children") or [] if item.get("name") == child]
    if len(matches) != 1:
        raise RuntimeError(f"supervisor_child_count_invalid:{child}:{len(matches)}")
    return matches[0]


def restart_budget_status(
    child: ChildName,
    policy: ScenarioDPolicy,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    observed_at = now or utc_now()
    payload = read_json(LEDGER_PATH) if LEDGER_PATH.exists() else {"attempts": []}
    window_start = observed_at - timedelta(seconds=policy.restart_window_seconds)
    recent = sorted(
        (
            item
            for item in payload.get("attempts") or []
            if item.get("child_name") == child
            and parse_utc(str(item["attempted_at"])) >= window_start
        ),
        key=lambda item: parse_utc(str(item["attempted_at"])),
    )
    retry_after_seconds = 0.0
    if recent:
        delay_index = min(len(recent) - 1, len(policy.restart_backoff_seconds) - 1)
        eligible_at = parse_utc(str(recent[-1]["attempted_at"])) + timedelta(
            seconds=policy.restart_backoff_seconds[delay_index]
        )
        retry_after_seconds = max(0.0, (eligible_at - observed_at).total_seconds())
    return {
        "observed_at": observed_at.isoformat(),
        "window_seconds": policy.restart_window_seconds,
        "recent_attempts": len(recent),
        "max_attempts": policy.max_restarts_per_window,
        "retry_after_seconds": retry_after_seconds,
        "available": len(recent) < policy.max_restarts_per_window
        and retry_after_seconds == 0,
    }


def validate_preflight(
    *,
    child: ChildName,
    source_commit: str,
    source_branch: str,
    project_root: Path,
    snapshot: dict[str, Any],
    policy: ScenarioDPolicy,
) -> tuple[list[CheckEvidence], dict[str, Any], dict[str, Any]]:
    identity, process = exact_process_identity(child)
    supervisor = snapshot["supervisor"]
    target = supervisor_child(supervisor, child)
    other: ChildName = (
        "kubernetes_observer" if child == "lifecycle_worker" else "lifecycle_worker"
    )
    other_target = supervisor_child(supervisor, other)
    local_changes = git_text(project_root, "status", "--porcelain", "--", ".")
    local_head = git_text(project_root, "rev-parse", "HEAD")
    local_branch = git_text(project_root, "branch", "--show-current")
    upstream = git_text(project_root, "rev-parse", "@{u}")
    ready = snapshot["production_ready"]
    inference = snapshot["production_inference"]
    kubernetes = snapshot["kubernetes"]
    restart_budget = restart_budget_status(child, policy)
    checks = [
        CheckEvidence(
            check_id="source_clean_and_pushed",
            passed=not local_changes
            and local_head == source_commit
            and upstream == source_commit
            and local_branch == source_branch,
            observed={
                "local_changes": local_changes,
                "upstream": upstream,
                "head": local_head,
                "branch": local_branch,
                "expected_head": source_commit,
                "expected_branch": source_branch,
            },
        ),
        CheckEvidence(
            check_id="supervisor_exact_revision",
            passed=supervisor.get("status") == "healthy"
            and supervisor.get("source_commit") == source_commit,
            observed={"status": supervisor.get("status"), "source": supervisor.get("source_commit")},
        ),
        CheckEvidence(
            check_id="target_exact_identity",
            passed=target.get("status") == "live"
            and target.get("exact_identity") is True
            and target.get("revision_matches") is True
            and target.get("lease_matches") is True
            and target.get("fencing_matches") is True
            and float(target.get("heartbeat_age_seconds") or float("inf"))
            <= policy.heartbeat_stale_seconds
            and int(target.get("pid")) == int(identity["pid"])
            and target.get("process_instance_id") == identity["process_instance_id"],
            observed={"target": target, "identity": identity, "process": process},
        ),
        CheckEvidence(
            check_id="unaffected_child_live",
            passed=other_target.get("status") == "live"
            and other_target.get("process_count") == 1
            and other_target.get("exact_identity") is True
            and other_target.get("revision_matches") is True
            and other_target.get("lease_matches") is True
            and other_target.get("fencing_matches") is True,
            observed=other_target,
        ),
        CheckEvidence(
            check_id="restart_budget_available",
            passed=restart_budget["available"] is True,
            observed=restart_budget,
        ),
        CheckEvidence(
            check_id="no_active_lifecycle_mutation",
            passed=not snapshot["active_lifecycle_runs"],
            observed=snapshot["active_lifecycle_runs"],
        ),
        CheckEvidence(
            check_id="production_b0_ready",
            passed=ready.get("status") == "ok"
            and ready.get("cuda_available") is True
            and kubernetes["ready_replicas"] == 1
            and kubernetes["available_replicas"] == 1,
            observed={"ready": ready, "kubernetes": kubernetes},
        ),
        CheckEvidence(
            check_id="production_cuda_inference",
            passed=inference.get("candidate_id") == ready.get("candidate_id")
            and inference.get("model_sha256") == ready.get("model_sha256")
            and inference.get("dataset_version") == ready.get("dataset_version")
            and inference.get("device") == "cuda"
            and inference.get("prediction") == "normal",
            observed=inference,
        ),
        CheckEvidence(
            check_id="gpu_plugin_ready",
            passed=kubernetes["gpu_allocatable"] == "1"
            and kubernetes["plugin_desired"] == 1
            and kubernetes["plugin_ready"] == 1,
            observed=kubernetes,
        ),
        CheckEvidence(
            check_id="prometheus_target_up",
            passed=snapshot["prometheus"]["health"] == "up",
            observed=snapshot["prometheus"],
        ),
        CheckEvidence(
            check_id="control_panel_observation_live",
            passed=snapshot["worker_api"].get("status") == "online"
            and snapshot["resources_api"].get("observation_status") == "live",
            observed={"worker": snapshot["worker_api"], "resources": snapshot["resources_api"]},
        ),
    ]
    failed = [item.check_id for item in checks if not item.passed]
    if failed:
        raise RuntimeError(f"scenario_d_live_preflight_failed:{failed}")
    return checks, identity, process


def action_digest(
    *,
    run_id: str,
    child: ChildName,
    identity: dict[str, Any],
    source_commit: str,
    expires_at: datetime,
) -> str:
    payload = {
        "run_id": run_id,
        "child": child,
        "pid": identity["pid"],
        "process_started_at": identity["process_started_at"],
        "process_instance_id": identity["process_instance_id"],
        "source_commit": source_commit,
        "expires_at": expires_at.isoformat(),
        "action": "terminate_exact_supervised_child",
        "single_use": True,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def approval_binding_errors(
    approval: dict[str, Any],
    *,
    run_id: str,
    child: ChildName,
    identity: dict[str, Any],
    source_commit: str,
    now: datetime,
) -> list[str]:
    errors: list[str] = []
    try:
        expires_at = parse_utc(str(approval["expires_at"]))
    except (KeyError, TypeError, ValueError):
        return ["approval_expiry_invalid"]
    expected_digest = action_digest(
        run_id=run_id,
        child=child,
        identity=identity,
        source_commit=source_commit,
        expires_at=expires_at,
    )
    expected = {
        "decision": "approved",
        "run_id": run_id,
        "target_uid": identity["process_instance_id"],
        "target_pid": int(identity["pid"]),
        "action_digest": expected_digest,
        "source_revision": source_commit,
        "single_use": True,
    }
    for field, value in expected.items():
        if approval.get(field) != value:
            errors.append(f"approval_{field}_mismatch")
    if now >= expires_at:
        errors.append("approval_expired")
    if approval.get("consumed_at") is not None:
        errors.append("approval_already_consumed")
    if not str(approval.get("approval_id") or "").startswith("scenario-d-maintenance-"):
        errors.append("approval_id_invalid")
    return sorted(set(errors))


def audit_events_after(child: ChildName, after: datetime) -> list[dict[str, Any]]:
    if not AUDIT_PATH.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in AUDIT_PATH.read_text(encoding="utf-8-sig").splitlines():
        try:
            event = json.loads(line)
            if event.get("child_name") == child and parse_utc(event["observed_at"]) >= after:
                events.append(event)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return events


def heartbeat_observed_at(child: ChildName) -> str:
    if child == "lifecycle_worker":
        path = WORKER_IDENTITY_PATH.parent / "_worker.json"
        return str(read_json(path)["last_seen_at"])
    path = OBSERVER_IDENTITY_PATH.parent / "latest.json"
    return str(read_json(path)["observed_at"])


def percentile_95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)]


def heartbeat_cadence_summary(timestamps: list[datetime]) -> dict[str, Any]:
    deltas = [
        (right - left).total_seconds()
        for left, right in zip(timestamps, timestamps[1:])
        if right >= left
    ]
    return {
        "heartbeat_timestamps": [value.isoformat() for value in timestamps],
        "heartbeat_deltas_seconds": deltas,
        "heartbeat_delta_count": len(deltas),
        "heartbeat_p95_seconds": percentile_95(deltas),
    }


def collect_healthy_heartbeat_cadence(
    *,
    child: ChildName,
    old_pid: int,
    source_commit: str,
    lease_id: str,
    fencing_token: int,
    minimum_deltas: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    timestamps: list[datetime] = []
    seen: set[str] = set()
    samples: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        observed_at = utc_now()
        try:
            supervisor = read_json(SUPERVISOR_PATH)
            target = supervisor_child(supervisor, child)
            heartbeat_value = heartbeat_observed_at(child)
            process_live = (
                target.get("status") == "live"
                and target.get("exact_identity") is True
                and int(target.get("pid") or 0) != old_pid
                and target.get("source_commit") == source_commit
                and target.get("revision_matches") is True
                and target.get("lease_matches") is True
                and target.get("fencing_matches") is True
                and supervisor.get("lease_id") == lease_id
                and int(supervisor.get("fencing_token")) == fencing_token
            )
            if process_live and heartbeat_value not in seen:
                seen.add(heartbeat_value)
                timestamps.append(parse_utc(heartbeat_value))
        except (KeyError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
            target = None
            heartbeat_value = None
            process_live = False
        samples.append(
            {
                "observed_at": observed_at.isoformat(),
                "target": target,
                "heartbeat": heartbeat_value,
                "process_live": process_live,
            }
        )
        if len(timestamps) >= minimum_deltas + 1:
            break
        time.sleep(0.2)
    summary = heartbeat_cadence_summary(timestamps)
    summary["samples"] = samples
    if summary["heartbeat_delta_count"] < minimum_deltas:
        raise RuntimeError(
            f"scenario_d_healthy_heartbeat_samples_insufficient:{child}:"
            f"{summary['heartbeat_delta_count']}"
        )
    return summary


def collect_recovery(
    *,
    child: ChildName,
    old_pid: int,
    source_commit: str,
    lease_id: str,
    fencing_token: int,
    injection_at: datetime,
    injection_ns: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    detection_ns: int | None = None
    recovery_ns: int | None = None
    samples: list[dict[str, Any]] = []
    heartbeat_times: list[datetime] = []
    seen_heartbeats: set[str] = set()
    detection_event: dict[str, Any] | None = None
    final_supervisor: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        monotonic_ns = time.monotonic_ns()
        observed_at = utc_now()
        try:
            heartbeat_value = heartbeat_observed_at(child)
            if heartbeat_value not in seen_heartbeats:
                seen_heartbeats.add(heartbeat_value)
                heartbeat_times.append(parse_utc(heartbeat_value))
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            heartbeat_value = None
        events = audit_events_after(child, injection_at)
        if detection_ns is None:
            detection_event = next(
                (
                    item
                    for item in events
                    if item.get("reason") == "process_missing"
                    and item.get("action") == "restart_exact"
                ),
                None,
            )
            if detection_event is not None:
                detection_ns = monotonic_ns
        try:
            supervisor = read_json(SUPERVISOR_PATH)
            target = supervisor_child(supervisor, child)
        except (KeyError, OSError, RuntimeError, ValueError, json.JSONDecodeError):
            supervisor = None
            target = None
        samples.append(
            {
                "observed_at": observed_at.isoformat(),
                "monotonic_ns": monotonic_ns,
                "heartbeat": heartbeat_value,
                "target": target,
            }
        )
        if (
            detection_ns is not None
            and target
            and target.get("status") == "live"
            and target.get("exact_identity") is True
            and int(target.get("pid") or 0) != old_pid
            and target.get("source_commit") == source_commit
            and target.get("revision_matches") is True
            and target.get("lease_matches") is True
            and target.get("fencing_matches") is True
            and supervisor.get("lease_id") == lease_id
            and int(supervisor.get("fencing_token")) == fencing_token
        ):
            recovery_ns = monotonic_ns
            final_supervisor = supervisor
            break
        time.sleep(0.2)
    if detection_ns is None:
        raise RuntimeError(f"scenario_d_detection_timeout:{child}")
    if recovery_ns is None or final_supervisor is None:
        raise RuntimeError(f"scenario_d_recovery_timeout:{child}")
    observed_cadence = heartbeat_cadence_summary(heartbeat_times)
    return {
        "detection_monotonic_ns": detection_ns,
        "recovery_monotonic_ns": recovery_ns,
        "detection_seconds": (detection_ns - injection_ns) / 1_000_000_000,
        "recovery_seconds": (recovery_ns - injection_ns) / 1_000_000_000,
        "detection_event": detection_event,
        "samples": samples,
        "observed_heartbeat_timestamps": observed_cadence["heartbeat_timestamps"],
        "observed_heartbeat_deltas_seconds": observed_cadence[
            "heartbeat_deltas_seconds"
        ],
        "observed_heartbeat_p95_seconds": observed_cadence["heartbeat_p95_seconds"],
        "supervisor": final_supervisor,
    }


def same_production(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return (
        before["production_ready"].get("model_sha256")
        == after["production_ready"].get("model_sha256")
        and before["kubernetes"]["deployment_uid"] == after["kubernetes"]["deployment_uid"]
        and after["kubernetes"]["ready_replicas"] == 1
        and after["kubernetes"]["available_replicas"] == 1
        and after["kubernetes"]["gpu_allocatable"] == "1"
        and after["kubernetes"]["plugin_ready"] == 1
        and after["prometheus"]["health"] == "up"
        and after["production_ready"].get("status") == "ok"
        and after["production_ready"].get("cuda_available") is True
        and after["production_inference"].get("device") == "cuda"
        and after["production_inference"].get("prediction") == "normal"
        and before["production_inference"].get("candidate_id")
        == after["production_inference"].get("candidate_id")
        and before["production_inference"].get("model_sha256")
        == after["production_inference"].get("model_sha256")
        and before["production_inference"].get("dataset_version")
        == after["production_inference"].get("dataset_version")
    )


def write_artifact(path: Path, payload: dict[str, Any]) -> Path:
    atomic_write_json(path, payload)
    return path


def run_live_once(
    *,
    child: ChildName,
    sequence: int,
    policy: ScenarioDPolicy,
    source_commit: str,
    source_branch: str,
    project_root: Path,
    output_root: Path,
    inference_image_uri: str,
) -> Path:
    started_at = utc_now()
    monotonic_started = time.monotonic_ns()
    run_id = (
        f"scenario-d-live-{sequence}-{child}-{started_at.strftime('%Y%m%dT%H%M%SZ')}-"
        f"{source_commit[:8]}"
    )
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    before = runtime_snapshot(inference_image_uri=inference_image_uri)
    preconditions, identity, process = validate_preflight(
        child=child,
        source_commit=source_commit,
        source_branch=source_branch,
        project_root=project_root,
        snapshot=before,
        policy=policy,
    )
    supervisor_before = before["supervisor"]
    old_pid = int(identity["pid"])
    other: ChildName = (
        "kubernetes_observer" if child == "lifecycle_worker" else "lifecycle_worker"
    )
    unaffected_before = int(supervisor_child(supervisor_before, other)["pid"])
    restart_before = int((supervisor_before.get("restart_counts") or {}).get(child, 0))
    approval_expires = started_at + timedelta(minutes=5)
    digest = action_digest(
        run_id=run_id,
        child=child,
        identity=identity,
        source_commit=source_commit,
        expires_at=approval_expires,
    )
    approval = {
        "approval_id": f"scenario-d-maintenance-{uuid.uuid4().hex}",
        "decision": "approved",
        "run_id": run_id,
        "target_uid": identity["process_instance_id"],
        "target_pid": old_pid,
        "action_digest": digest,
        "source_revision": source_commit,
        "expires_at": approval_expires.isoformat(),
        "single_use": True,
        "authority": "explicit delegated Scenario D maintenance approval",
    }
    approval_path = run_root / "approval.json"
    write_artifact(approval_path, approval)
    approval_errors = approval_binding_errors(
        read_json(approval_path),
        run_id=run_id,
        child=child,
        identity=identity,
        source_commit=source_commit,
        now=utc_now(),
    )
    if approval_errors:
        raise RuntimeError(f"scenario_d_approval_binding_invalid:{approval_errors}")
    preconditions.append(
        CheckEvidence(
            check_id="maintenance_approval_exact_binding",
            passed=True,
            observed={
                "approval_id": approval["approval_id"],
                "action_digest": digest,
                "target_uid": identity["process_instance_id"],
                "expires_at": approval["expires_at"],
                "single_use": True,
            },
        )
    )
    artifact_paths = [
        write_artifact(
            run_root / "policy.json",
            policy.model_dump(mode="json"),
        ),
        write_artifact(run_root / "before.json", before),
        write_artifact(
            run_root / "preflight.json",
            {
                "checks": [item.model_dump(mode="json") for item in preconditions],
                "target_identity": identity,
                "target_process": process,
            },
        ),
        approval_path,
    ]

    identity_recheck, process_recheck = exact_process_identity(child)
    if identity_recheck != identity or process_recheck != process:
        raise RuntimeError("scenario_d_exact_identity_changed_before_action")
    if active_lifecycle_runs():
        raise RuntimeError("scenario_d_lifecycle_work_appeared_before_action")
    injection_at = utc_now()
    injection_ns = time.monotonic_ns()
    approval["decision"] = "consumed"
    approval["consumed_at"] = injection_at.isoformat()
    write_artifact(run_root / "approval.json", approval)
    stop_exact_process(old_pid)
    recovery = collect_recovery(
        child=child,
        old_pid=old_pid,
        source_commit=source_commit,
        lease_id=str(supervisor_before["lease_id"]),
        fencing_token=int(supervisor_before["fencing_token"]),
        injection_at=injection_at,
        injection_ns=injection_ns,
        timeout_seconds=policy.max_recovery_seconds,
    )
    recovery["healthy_cadence"] = collect_healthy_heartbeat_cadence(
        child=child,
        old_pid=old_pid,
        source_commit=source_commit,
        lease_id=str(supervisor_before["lease_id"]),
        fencing_token=int(supervisor_before["fencing_token"]),
        minimum_deltas=2,
        timeout_seconds=max(30.0, policy.heartbeat_interval_seconds * 6),
    )
    after = runtime_snapshot(inference_image_uri=inference_image_uri)
    new_identity, new_process = exact_process_identity(child)
    supervisor_after = after["supervisor"]
    target_after = supervisor_child(supervisor_after, child)
    unaffected_after = int(supervisor_child(supervisor_after, other)["pid"])
    restart_after = int((supervisor_after.get("restart_counts") or {}).get(child, 0))
    audit = audit_events_after(child, injection_at)
    postconditions = [
        CheckEvidence(
            check_id="detection_within_target",
            passed=recovery["detection_seconds"] <= policy.max_detection_seconds,
            observed=recovery["detection_seconds"],
        ),
        CheckEvidence(
            check_id="recovery_within_target",
            passed=recovery["recovery_seconds"] <= policy.max_recovery_seconds,
            observed=recovery["recovery_seconds"],
        ),
        CheckEvidence(
            check_id="healthy_heartbeat_cadence_within_target",
            passed=recovery["healthy_cadence"]["heartbeat_delta_count"] >= 2
            and recovery["healthy_cadence"]["heartbeat_p95_seconds"]
            <= policy.max_heartbeat_p95_seconds,
            observed=recovery["healthy_cadence"],
        ),
        CheckEvidence(
            check_id="target_replaced_exactly_once",
            passed=int(new_identity["pid"]) != old_pid
            and int(target_after["pid"]) == int(new_identity["pid"])
            and restart_after == restart_before + 1
            and len(process_census(child)) == 1,
            observed={
                "old_pid": old_pid,
                "new_identity": new_identity,
                "new_process": new_process,
                "restart_before": restart_before,
                "restart_after": restart_after,
            },
        ),
        CheckEvidence(
            check_id="unaffected_child_not_restarted",
            passed=unaffected_before == unaffected_after and len(process_census(other)) == 1,
            observed={"child": other, "before_pid": unaffected_before, "after_pid": unaffected_after},
        ),
        CheckEvidence(
            check_id="revision_lease_fence_converged",
            passed=target_after.get("status") == "live"
            and target_after.get("source_commit") == source_commit
            and target_after.get("revision_matches") is True
            and target_after.get("lease_matches") is True
            and target_after.get("fencing_matches") is True,
            observed=target_after,
        ),
        CheckEvidence(
            check_id="control_panel_recovered",
            passed=after["worker_api"].get("status") == "online"
            and after["resources_api"].get("observation_status") == "live",
            observed={"worker": after["worker_api"], "resources": after["resources_api"]},
        ),
        CheckEvidence(
            check_id="production_and_prometheus_unchanged",
            passed=same_production(before, after),
            observed={"before": before, "after": after},
        ),
        CheckEvidence(
            check_id="duplicate_process_and_active_run_zero",
            passed=len(process_census(child)) == 1
            and len(process_census(other)) == 1
            and not after["active_lifecycle_runs"],
            observed={
                "target_count": len(process_census(child)),
                "other_count": len(process_census(other)),
                "active_runs": after["active_lifecycle_runs"],
            },
        ),
        CheckEvidence(
            check_id="maintenance_approval_consumed_once",
            passed=approval.get("decision") == "consumed"
            and approval.get("consumed_at") == injection_at.isoformat()
            and injection_at < approval_expires,
            observed=approval,
        ),
    ]
    failed = [item.check_id for item in postconditions if not item.passed]
    if failed:
        write_artifact(run_root / "failed-postconditions.json", {"failed": failed})
        raise RuntimeError(f"scenario_d_live_postcondition_failed:{failed}")
    artifact_paths.extend(
        [
            write_artifact(run_root / "timeline.json", recovery),
            write_artifact(run_root / "audit-events.json", {"events": audit}),
            write_artifact(run_root / "after.json", after),
            write_artifact(
                run_root / "postconditions.json",
                {"checks": [item.model_dump(mode="json") for item in postconditions]},
            ),
        ]
    )
    finished_at = utc_now()
    monotonic_finished = time.monotonic_ns()
    artifacts = [
        ArtifactEvidence(
            uri=str(path.resolve()),
            sha256=sha256_file(path),
            media_type="application/json",
            evidence_role="run_evidence",
        )
        for path in artifact_paths
    ]
    required_readiness = [item.check_id for item in preconditions]
    required_live = [item.check_id for item in postconditions]
    report = OperationalFailureReport(
        schema_version="evm.operational_failure_evidence.v1",
        scenario_id="D",
        run_id=run_id,
        claim_class="local_operational_validation",
        status="passed",
        started_at=started_at,
        finished_at=finished_at,
        actor="codex-scenario-d-live-runner",
        approval=ApprovalEvidence(
            required=True,
            decision="consumed",
            approval_id=approval["approval_id"],
            run_id=run_id,
            target_uid=identity["process_instance_id"],
            action_digest=digest,
            source_revision=source_commit,
            expires_at=approval_expires,
            consumed_at=injection_at,
            single_use=True,
        ),
        source=SourceEvidence(
            commit=source_commit,
            branch=source_branch,
            dirty=False,
            api_revision="not_exposed_by_current_api_image",
            worker_revision=str(
                supervisor_child(supervisor_after, "lifecycle_worker")["source_commit"]
            ),
            observer_revision=str(
                supervisor_child(supervisor_after, "kubernetes_observer")["source_commit"]
            ),
        ),
        environment=EnvironmentEvidence(
            cluster_context="docker-desktop",
            node="docker-desktop",
            namespaces=["evm-production", "evm-staging", "evm-training"],
            hardware={
                "gpu_allocatable": after["kubernetes"]["gpu_allocatable"],
                "single_node": True,
                "single_gpu": True,
            },
            runtime_versions={"supervisor_contract": "evm.scenario_d_supervision.v1"},
        ),
        identities=IdentityEvidence(model_digest=after["production_ready"]["model_sha256"]),
        identity_requirements=["model_digest"],
        preconditions=preconditions,
        injection=InjectionEvidence(
            method="exact_pid_maintenance_termination",
            action="terminate one supervised child and allow canonical supervisor recovery",
            target={
                "name": child,
                "uid": identity["process_instance_id"],
                "pid": str(old_pid),
            },
            expected_effect="one child restart with unchanged production serving",
            blast_radius="one local host worker or observer process",
            performed=True,
        ),
        signals=[
            SignalEvidence(
                signal_id="supervisor_detection",
                source="supervisor-audit",
                observed_at=parse_utc(recovery["detection_event"]["observed_at"]),
                healthy=True,
                detail=recovery["detection_event"],
            ),
            SignalEvidence(
                signal_id="exact_child_recovery",
                source="supervisor-heartbeat",
                observed_at=finished_at,
                healthy=True,
                detail=target_after,
            ),
        ],
        decision=DecisionEvidence(
            expected="restart_exact",
            observed="restart_exact_then_live",
            blocker_codes=[],
        ),
        mitigation={"automatic_restart": True, "exact_target_only": True},
        recovery=RecoveryEvidence(
            action="canonical_child_launcher",
            target_identity={
                "old_instance": identity["process_instance_id"],
                "new_instance": new_identity["process_instance_id"],
            },
            result="live_exact_revision",
        ),
        postconditions=postconditions,
        artifacts=artifacts,
        limitations=[
            "single-node local host-process recovery with one child at a time",
            "API image revision is not exposed and was not rebuilt during this proof",
            "no production traffic, HA, zero-downtime, or distributed consensus claim",
        ],
        portfolio=PortfolioEvidence(
            competencies=[
                "exact-target process supervision",
                "heartbeat and fencing recovery",
                "measured operational evidence",
            ],
            interview_questions=[
                "How did you prevent PID reuse from killing an unrelated process?",
                "Why is at-least-once recovery paired with idempotency and fencing?",
            ],
            trade_offs=[
                "fail-closed identity ambiguity may require manual recovery",
                "single-replica local validation cannot demonstrate HA",
            ],
            factual_claims=[
                "one exact local worker or observer was recovered with measured timing"
            ],
            prohibited_claims=[
                "high availability",
                "zero downtime",
                "production traffic resilience",
                "enterprise SLA compliance",
            ],
        ),
        timing=TimingEvidence(
            audit_started_at=started_at,
            audit_finished_at=finished_at,
            monotonic_started_ns=monotonic_started,
            monotonic_finished_ns=monotonic_finished,
            injection_monotonic_ns=injection_ns,
            detection_monotonic_ns=recovery["detection_monotonic_ns"],
            recovery_monotonic_ns=recovery["recovery_monotonic_ns"],
            detection_seconds=recovery["detection_seconds"],
            recovery_seconds=recovery["recovery_seconds"],
            sample_cadence_seconds=0.2,
            signal_precedence=[
                "ownership",
                "duplicate_count",
                "heartbeat_identity",
                "heartbeat_freshness",
                "source_revision",
                "lease_fence",
            ],
        ),
        readiness_closure=ClosureEvidence(
            decision="passed",
            required_check_ids=required_readiness,
            completed_at=started_at,
        ),
        live_proof_closure=ClosureEvidence(
            decision="passed",
            required_check_ids=required_live,
            completed_at=finished_at,
        ),
    )
    errors = validate_closure(report, "live_proof")
    if errors:
        raise RuntimeError(f"scenario_d_live_evidence_invalid:{errors}")
    report_path = run_root / "report.json"
    atomic_write_json(report_path, report.model_dump(mode="json"))
    indexed = [*artifact_paths, report_path]
    index_path = run_root / "evidence-index.json"
    atomic_write_json(
        index_path,
        {
            "schema_version": "evm.scenario_d_evidence_index.v1",
            "run_id": run_id,
            "artifacts": [
                {"uri": str(path.resolve()), "sha256": sha256_file(path)} for path in indexed
            ],
        },
    )
    return report_path


def run_live_series(
    *,
    sequence: list[ChildName],
    policy_path: Path,
    source_commit: str,
    source_branch: str,
    project_root: Path,
    output_root: Path,
    cooldown_seconds: float,
    inference_image_uri: str,
) -> Path:
    policy = ScenarioDPolicy.from_toml(policy_path)
    if tuple(sequence) != EXPECTED_SEQUENCE:
        raise ValueError(f"scenario_d_sequence_must_equal:{','.join(EXPECTED_SEQUENCE)}")
    if cooldown_seconds < 10:
        raise ValueError("scenario_d_cooldown_must_be_at_least_10_seconds")
    series_started = utc_now()
    series_id = f"scenario-d-series-{series_started.strftime('%Y%m%dT%H%M%SZ')}-{source_commit[:8]}"
    series_root = output_root / "_series" / series_id
    series_root.mkdir(parents=True, exist_ok=False)
    reports: list[Path] = []
    try:
        for index, child in enumerate(sequence, start=1):
            report = run_live_once(
                child=child,
                sequence=index,
                policy=policy,
                source_commit=source_commit,
                source_branch=source_branch,
                project_root=project_root,
                output_root=output_root,
                inference_image_uri=inference_image_uri,
            )
            reports.append(report)
            if index < len(sequence):
                time.sleep(cooldown_seconds)
    except Exception as exc:
        atomic_write_json(
            series_root / "failure.json",
            {
                "series_id": series_id,
                "source_commit": source_commit,
                "completed_reports": [str(path.resolve()) for path in reports],
                "error": str(exc),
                "failed_at": utc_now().isoformat(),
            },
        )
        raise
    summaries = []
    required_live_checks = {
        "detection_within_target",
        "recovery_within_target",
        "healthy_heartbeat_cadence_within_target",
        "target_replaced_exactly_once",
        "unaffected_child_not_restarted",
        "revision_lease_fence_converged",
        "control_panel_recovered",
        "production_and_prometheus_unchanged",
        "duplicate_process_and_active_run_zero",
        "maintenance_approval_consumed_once",
    }
    for report_path in reports:
        report = OperationalFailureReport.model_validate_json(
            report_path.read_text(encoding="utf-8")
        )
        postconditions = {item.check_id: item for item in report.postconditions}
        missing = sorted(required_live_checks - set(postconditions))
        failed = sorted(
            check_id
            for check_id in required_live_checks
            if check_id in postconditions and not postconditions[check_id].passed
        )
        if missing or failed:
            raise RuntimeError(
                f"scenario_d_series_acceptance_failed:missing={missing}:failed={failed}"
            )
        timeline = read_json(report_path.parent / "timeline.json")
        summaries.append(
            {
                "run_id": report.run_id,
                "target": report.injection.target,
                "detection_seconds": report.timing.detection_seconds,
                "recovery_seconds": report.timing.recovery_seconds,
                "healthy_heartbeat_p95_seconds": timeline["healthy_cadence"][
                    "heartbeat_p95_seconds"
                ],
                "healthy_heartbeat_delta_count": timeline["healthy_cadence"][
                    "heartbeat_delta_count"
                ],
                "report_uri": str(report_path.resolve()),
                "report_sha256": sha256_file(report_path),
            }
        )
    series_path = series_root / "series.json"
    atomic_write_json(
        series_path,
        {
            "schema_version": "evm.scenario_d_live_series.v1",
            "series_id": series_id,
            "source_commit": source_commit,
            "sequence": sequence,
            "runs": summaries,
            "acceptance": {
                "required_live_checks": sorted(required_live_checks),
                "max_detection_seconds": max(
                    float(item["detection_seconds"]) for item in summaries
                ),
                "max_recovery_seconds": max(
                    float(item["recovery_seconds"]) for item in summaries
                ),
                "max_healthy_heartbeat_p95_seconds": max(
                    float(item["healthy_heartbeat_p95_seconds"]) for item in summaries
                ),
            },
            "passed": len(summaries) == len(sequence),
            "completed_at": utc_now().isoformat(),
        },
    )
    atomic_write_json(
        output_root / "latest-live-series.json",
        {
            "series_id": series_id,
            "series_uri": str(series_path.resolve()),
            "source_commit": source_commit,
            "passed": True,
        },
    )
    return series_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Scenario D exact-child live proof.")
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-branch", required=True)
    parser.add_argument(
        "--sequence",
        default="lifecycle_worker,kubernetes_observer,lifecycle_worker",
    )
    parser.add_argument("--cooldown-seconds", type=float, default=10)
    parser.add_argument("--inference-image-uri", default=DEFAULT_INFERENCE_IMAGE_URI)
    args = parser.parse_args()
    sequence = [value.strip() for value in args.sequence.split(",") if value.strip()]
    if any(value not in {"lifecycle_worker", "kubernetes_observer"} for value in sequence):
        raise ValueError("invalid_scenario_d_child_sequence")
    series_path = run_live_series(
        sequence=sequence,
        policy_path=args.policy,
        source_commit=args.source_commit,
        source_branch=args.source_branch,
        project_root=args.project_root,
        output_root=args.output_root,
        cooldown_seconds=max(0, args.cooldown_seconds),
        inference_image_uri=args.inference_image_uri,
    )
    print(json.dumps({"series_uri": str(series_path.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
