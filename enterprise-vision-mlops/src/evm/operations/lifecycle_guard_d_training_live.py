from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

import requests

from evm.control_panel.kubernetes_task_executor import (
    expected_job_identity,
    job_container_identity,
)
from evm.control_panel.lifecycle_gpu_handoff import issue_gpu_handoff_approval
from evm.control_panel.lifecycle_guards import (
    LifecycleSideEffectLedger,
    canonical_digest,
    file_digest,
)
from evm.control_panel.lifecycle_kubernetes import short_run_id
from evm.control_panel.lifecycle_runs import LifecycleRun
from evm.control_panel.readiness_evaluator import runtime_path
from evm.operations.lifecycle_guard_e_runner import (
    DEFAULT_INFERENCE_IMAGE_URI,
    build_evidence_index,
    external_side_effect_snapshot,
    git_text,
    write_json,
)
from evm.operations.scenario_d_live import (
    SUPERVISOR_PATH,
    collect_recovery,
    exact_process_identity,
    parse_utc,
    read_json,
    runtime_snapshot,
    stop_exact_process,
    supervisor_child,
    utc_now,
)
from evm.operations.scenario_d_supervision import ScenarioDPolicy


SCHEMA = "evm.lifecycle_guard_scenario_d_training_recovery.v1"
API_ROOT = "http://127.0.0.1:8000/control-panel/v1"
DEFAULT_OUTPUT_ROOT = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/"
    "lifecycle_guard_d_training_live"
)
HANDOFF_PHASES = ("training", "isolated_ct", "staging_deployment")
RUNTIME_RESTORATION_CADENCE_SECONDS = 5.0
RUNTIME_RESTORATION_REQUIRED_SCRAPES = 2


def api_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 60,
) -> dict[str, Any]:
    response = requests.request(
        method,
        f"{API_ROOT}{path}",
        json=payload,
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:2000].replace("\n", " ")
        raise requests.HTTPError(
            f"lifecycle_api_error:{path}:status={response.status_code}:body={detail}",
            response=response,
            request=response.request,
        ) from exc
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError(f"lifecycle_api_object_required:{path}")
    return body


def kubectl_json(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        ["kubectl", "--context", "docker-desktop", *command, "-o", "json"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("kubectl_object_required")
    return payload


def docker_desktop_kubectl(
    command: list[str], **kwargs: Any
) -> subprocess.CompletedProcess[str]:
    if not command or command[0] != "kubectl":
        raise RuntimeError("docker_desktop_kubectl_command_required")
    return subprocess.run(
        ["kubectl", "--context", "docker-desktop", *command[1:]],
        **kwargs,
    )


def issue_run_handoff_approvals(
    created: dict[str, Any],
    *,
    run_root: Path,
    ttl_seconds: int,
) -> dict[str, Any]:
    run = LifecycleRun.model_validate(created)
    approvals: dict[str, Any] = {}
    for phase in HANDOFF_PHASES:
        reference_path = issue_gpu_handoff_approval(
            run,
            phase=phase,
            approver="scenario-d-resource-approver",
            reason=(
                "Pre-authorized bounded single-GPU handoff for Scenario D "
                "integrated lifecycle validation"
            ),
            ttl_seconds=ttl_seconds,
            runner=docker_desktop_kubectl,
        )
        approvals[phase] = {
            "reference_uri": str(reference_path.resolve()),
            "reference": read_json(reference_path),
        }
        write_json(run_root / "gpu-handoff-approvals.json", approvals)
    return approvals


def release_approval_request(
    run: dict[str, Any], submission: dict[str, Any]
) -> dict[str, Any]:
    candidate_id = str(submission.get("candidate_id") or "")
    model_digest = str(submission.get("model_digest") or "")
    ct_evaluation_id = str(submission.get("ct_evaluation_id") or "")
    if (
        run.get("state") != "waiting_approval"
        or run.get("current_stage") != "approval"
        or not candidate_id
        or len(model_digest) != 64
        or not ct_evaluation_id
    ):
        raise RuntimeError("release_approval_identity_incomplete")
    return {
        "actor": "scenario-d-release-approver",
        "approver": "scenario-d-release-approver",
        "reason": (
            "Approve the sealed local-staging release for the pre-authorized "
            "Scenario D integrated lifecycle validation"
        ),
        "candidate_id": candidate_id,
        "model_digest": model_digest,
        "ct_evaluation_id": ct_evaluation_id,
        "expected_version": run["version"],
    }


def handoff_consumption_snapshot(approvals: dict[str, Any]) -> dict[str, Any]:
    consumed: dict[str, Any] = {}
    for phase in HANDOFF_PHASES:
        reference = approvals.get(phase, {}).get("reference", {})
        approval_uri = str(reference.get("approval_path") or "")
        if not approval_uri:
            consumed[phase] = {
                "approval_id": reference.get("approval_id"),
                "consumed_uri": None,
                "consumed": False,
                "receipt": None,
            }
            continue
        approval_path = Path(approval_uri)
        consumed_path = approval_path.with_name(
            f"{approval_path.stem}.consumed.json"
        )
        consumed[phase] = {
            "approval_id": reference.get("approval_id"),
            "consumed_uri": str(consumed_path.resolve()),
            "consumed": consumed_path.is_file(),
            "receipt": read_json(consumed_path) if consumed_path.is_file() else None,
        }
    return consumed


def active_run_ids() -> list[str]:
    payload = api_request("GET", "/lifecycle-runs")
    return sorted(
        str(item["run_id"])
        for item in payload.get("runs", [])
        if isinstance(item, dict) and item.get("state") in {"queued", "running"}
    )


def stage_for(run: dict[str, Any], stage_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in run.get("stages", [])
        if isinstance(item, dict) and item.get("stage_id") == stage_id
    ]
    if len(matches) != 1:
        raise RuntimeError(f"lifecycle_stage_count_invalid:{stage_id}:{len(matches)}")
    return matches[0]


def tasks_for_run(run_id: str) -> list[dict[str, Any]]:
    payload = api_request("GET", "/tasks")
    return [
        item
        for item in payload.get("tasks", [])
        if isinstance(item, dict) and item.get("cycle_id") == run_id
    ]


def task_for_id(task_id: str) -> dict[str, Any]:
    payload = api_request("GET", "/tasks")
    matches = [
        item
        for item in payload.get("tasks", [])
        if isinstance(item, dict) and item.get("task_id") == task_id
    ]
    if len(matches) != 1:
        raise RuntimeError(f"lifecycle_task_count_invalid:{task_id}:{len(matches)}")
    return matches[0]


def side_effect_ledger(run: dict[str, Any]) -> tuple[LifecycleSideEffectLedger, Path]:
    root = Path(str(run["artifact_root"]))
    path = root / "side_effect_ledger.json"
    return LifecycleSideEffectLedger.model_validate(read_json(path)), path


def exact_training_entry(
    ledger: LifecycleSideEffectLedger,
) -> dict[str, Any]:
    matches = [
        entry
        for entry in ledger.entries
        if entry.stage_id == "model_training" and entry.action == "execute_kubernetes_job"
    ]
    if len(matches) != 1:
        raise RuntimeError(f"training_side_effect_count_invalid:{len(matches)}")
    return matches[0].model_dump(mode="json")


def exact_training_job(
    run_id: str,
    task: dict[str, Any],
    *,
    allow_not_found: bool = False,
) -> dict[str, Any] | None:
    runtime_id = str(task.get("runtime_id") or "")
    parts = runtime_id.split("/")
    if len(parts) != 3 or parts[1] != "job":
        raise RuntimeError(f"training_runtime_identity_invalid:{runtime_id}")
    namespace, _, name = parts
    config = task.get("config_payload")
    if not isinstance(config, dict):
        raise RuntimeError("training_task_config_missing")
    if (
        config.get("lifecycle_run_id") != run_id
        or config.get("namespace") != namespace
        or config.get("job_name") != name
    ):
        raise RuntimeError("training_task_identity_mismatch")
    manifest_dir = Path(str(config.get("manifest_dir") or "")).resolve()
    expected = expected_job_identity(
        manifest_dir,
        namespace=namespace,
        job_name=name,
    )
    try:
        payload = kubectl_json(["get", "job", name, "-n", namespace])
    except subprocess.CalledProcessError as exc:
        normalized = str(exc.stderr or "").replace(" ", "").lower()
        if allow_not_found and "notfound" in normalized:
            return None
        raise
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    labels = metadata.get("labels") if isinstance(metadata.get("labels"), dict) else {}
    expected_labels = expected["labels"]
    expected_run_label = str(expected_labels.get("evm.openai.local/lifecycle-run") or "")
    if (
        metadata.get("name") != name
        or metadata.get("namespace") != namespace
        or not metadata.get("uid")
        or not expected_run_label
        or short_run_id(run_id) != expected_run_label
        or any(
            str(labels.get(key) or "") != value
            for key, value in expected_labels.items()
        )
        or job_container_identity(payload) != expected["containers"]
    ):
        raise RuntimeError("training_job_exact_identity_mismatch")
    return payload


def training_job_state(job: dict[str, Any]) -> str:
    conditions = {
        str(item.get("type")): str(item.get("status"))
        for item in (job.get("status") or {}).get("conditions", [])
        if isinstance(item, dict)
    }
    if conditions.get("Failed") == "True":
        return "failed"
    if conditions.get("Complete") == "True":
        return "complete"
    if int((job.get("status") or {}).get("active") or 0) == 1:
        return "active"
    return "admitted"


def admission_snapshot(run_id: str) -> dict[str, Any] | None:
    run = api_request("GET", f"/lifecycle-runs/{run_id}")
    stage = stage_for(run, "model_training")
    if stage.get("state") != "running" or not stage.get("task_id"):
        return None
    task = task_for_id(str(stage["task_id"]))
    if task.get("status") != "running" or task.get("runtime_system") != "kubernetes":
        return None
    ledger, ledger_path = side_effect_ledger(run)
    entry = exact_training_entry(ledger)
    if entry["state"] != "reserved":
        return None
    job = exact_training_job(run_id, task, allow_not_found=True)
    if job is None:
        return None
    state = training_job_state(job)
    if state not in {"active", "admitted"}:
        return None
    supervisor = read_json(SUPERVISOR_PATH)
    worker = supervisor_child(supervisor, "lifecycle_worker")
    worker_api = api_request("GET", "/lifecycle-runs/worker")
    if (
        supervisor.get("status") != "healthy"
        or worker.get("status") != "live"
        or worker_api.get("current_run_id") != run_id
        or int(worker_api.get("pid") or 0) != int(worker.get("pid") or -1)
    ):
        return None
    return {
        "run": run,
        "stage": stage,
        "task": task,
        "side_effect": entry,
        "side_effect_ledger_uri": str(ledger_path.resolve()),
        "job": job,
        "job_state": state,
        "supervisor": supervisor,
        "worker": worker,
        "worker_api": worker_api,
    }


def wait_for_admission(
    run_id: str,
    *,
    timeout_seconds: float,
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_state: tuple[str, str, str] | None = None
    while time.monotonic() <= deadline:
        run = api_request("GET", f"/lifecycle-runs/{run_id}")
        stage = stage_for(run, str(run.get("current_stage") or "model_training"))
        state = (
            str(run.get("state")),
            str(run.get("current_stage")),
            str(stage.get("runtime_state") or stage.get("state")),
        )
        if state != last_state:
            timeline.append(
                {
                    "observed_at": utc_now().isoformat(),
                    "phase": "await_training_admission",
                    "run_state": state[0],
                    "current_stage": state[1],
                    "runtime_state": state[2],
                    "progress": run.get("progress"),
                }
            )
            last_state = state
        if run.get("state") in {"blocked", "failed", "cancelled", "rolled_back"}:
            raise RuntimeError(f"lifecycle_failed_before_injection:{run.get('state')}")
        admitted = admission_snapshot(run_id)
        if admitted is not None:
            return admitted
        time.sleep(1)
    raise TimeoutError("training_admission_timeout")


def wait_for_terminal(
    run_id: str,
    *,
    timeout_seconds: float,
    timeline: list[dict[str, Any]],
    run_root: Path,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_state: tuple[str, str, str] | None = None
    while time.monotonic() <= deadline:
        run = api_request("GET", f"/lifecycle-runs/{run_id}")
        current = str(run.get("current_stage") or "completed")
        stage = stage_for(run, current) if run.get("current_stage") else {}
        state = (
            str(run.get("state")),
            current,
            str(stage.get("runtime_state") or stage.get("state") or "completed"),
        )
        if state != last_state:
            timeline.append(
                {
                    "observed_at": utc_now().isoformat(),
                    "phase": "post_worker_recovery",
                    "run_state": state[0],
                    "current_stage": state[1],
                    "runtime_state": state[2],
                    "progress": run.get("progress"),
                }
            )
            last_state = state
        if run.get("state") in {
            "completed",
            "blocked",
            "failed",
            "cancelled",
            "rolled_back",
        }:
            return run
        if run.get("state") == "waiting_approval" and current == "approval":
            approval_path = run_root / "release-approval.json"
            if approval_path.exists():
                raise RuntimeError("release_approval_replay_blocked")
            submission_uri = str(run.get("release_submission_uri") or "")
            if not submission_uri:
                raise RuntimeError("release_submission_missing_at_approval")
            submission = read_json(runtime_path(submission_uri))
            request = release_approval_request(run, submission)
            approved = api_request(
                "POST",
                f"/lifecycle-runs/{run_id}/approve",
                request,
            )
            write_json(
                approval_path,
                {
                    "request": request,
                    "submission_uri": submission_uri,
                    "submission_digest": submission.get("submission_digest"),
                    "response": approved,
                },
            )
            timeline.append(
                {
                    "observed_at": utc_now().isoformat(),
                    "phase": "sealed_release_approved",
                    "run_state": approved.get("state"),
                    "current_stage": approved.get("current_stage"),
                    "runtime_state": "approved",
                    "progress": approved.get("progress"),
                }
            )
        time.sleep(2)
    raise TimeoutError("lifecycle_completion_timeout")


def run_job_identities(run_id: str) -> list[dict[str, str]]:
    identities: list[dict[str, str]] = []
    for task in tasks_for_run(run_id):
        if task.get("task_type") != "kubernetes_job":
            continue
        item = exact_training_job(run_id, task)
        if item is None:
            raise RuntimeError("lifecycle_job_disappeared_after_completion")
        metadata = item.get("metadata") if isinstance(item, dict) else {}
        labels = metadata.get("labels") if isinstance(metadata.get("labels"), dict) else {}
        identities.append(
            {
                "namespace": str(metadata.get("namespace") or ""),
                "name": str(metadata.get("name") or ""),
                "uid": str(metadata.get("uid") or ""),
                "candidate_id": str(
                    labels.get("evm.openai.local/candidate-id") or ""
                ),
            }
        )
    return sorted(identities, key=lambda item: (item["namespace"], item["name"]))


def safe_cancel(run: dict[str, Any], reason: str) -> dict[str, Any] | None:
    if run.get("state") not in {"queued", "running", "waiting_approval"}:
        return None
    try:
        return api_request(
            "POST",
            f"/lifecycle-runs/{run['run_id']}/cancel",
            {
                "actor": "scenario-d-integrated-validator",
                "reason": reason,
                "expected_version": run["version"],
            },
        )
    except requests.RequestException:
        return None


def wait_for_runtime_restoration(
    *,
    before_runtime: dict[str, Any],
    source_commit: str,
    inference_image_uri: str,
    timeout_seconds: float,
    snapshot_reader: Callable[..., dict[str, Any]] = runtime_snapshot,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = monotonic()
    observations: list[dict[str, Any]] = []
    consecutive_scrapes: list[str] = []
    latest: dict[str, Any] = {}
    while True:
        latest = snapshot_reader(inference_image_uri=inference_image_uri)
        elapsed = max(monotonic() - started, 0.0)
        supervisor = latest["supervisor"]
        kubernetes = latest["kubernetes"]
        prometheus = latest["prometheus"]
        revision_converged = (
            supervisor.get("source_commit") == source_commit
            and all(
                item.get("source_commit") == source_commit
                for item in supervisor.get("children", [])
            )
        )
        runtime_restored = (
            kubernetes.get("deployment_uid")
            == before_runtime["kubernetes"].get("deployment_uid")
            and kubernetes.get("ready_replicas") == 1
            and latest["production_inference"].get("device") == "cuda"
            and kubernetes.get("plugin_ready")
            == before_runtime["kubernetes"].get("plugin_ready")
            and revision_converged
        )
        scrape_id = str(prometheus.get("last_scrape") or "")
        scrape_healthy = (
            prometheus.get("health") == "up"
            and not prometheus.get("last_error")
            and bool(scrape_id)
        )
        if runtime_restored and scrape_healthy:
            if not consecutive_scrapes or consecutive_scrapes[-1] != scrape_id:
                consecutive_scrapes.append(scrape_id)
        else:
            consecutive_scrapes = []
        observations.append(
            {
                "elapsed_seconds": elapsed,
                "runtime_restored": runtime_restored,
                "revision_converged": revision_converged,
                "deployment_uid": kubernetes.get("deployment_uid"),
                "ready_replicas": kubernetes.get("ready_replicas"),
                "device": latest["production_inference"].get("device"),
                "plugin_ready": kubernetes.get("plugin_ready"),
                "prometheus_health": prometheus.get("health"),
                "prometheus_error": prometheus.get("last_error"),
                "prometheus_last_scrape": scrape_id,
                "distinct_consecutive_scrapes": len(consecutive_scrapes),
            }
        )
        if (
            runtime_restored
            and len(consecutive_scrapes) >= RUNTIME_RESTORATION_REQUIRED_SCRAPES
        ):
            return latest, {
                "status": "pass",
                "elapsed_seconds": elapsed,
                "required_distinct_consecutive_scrapes": (
                    RUNTIME_RESTORATION_REQUIRED_SCRAPES
                ),
                "observations": observations,
            }
        if elapsed >= timeout_seconds:
            return latest, {
                "status": "blocked",
                "elapsed_seconds": elapsed,
                "required_distinct_consecutive_scrapes": (
                    RUNTIME_RESTORATION_REQUIRED_SCRAPES
                ),
                "observations": observations,
            }
        sleep(RUNTIME_RESTORATION_CADENCE_SECONDS)


def run(
    *,
    project_root: Path,
    policy_path: Path,
    output_root: Path,
    profile_id: str,
    profile_version: int,
    source_commit: str,
    source_branch: str,
    admission_timeout_seconds: float,
    completion_timeout_seconds: float,
    handoff_approval_ttl_seconds: int,
    inference_image_uri: str,
    runtime_restoration_timeout_seconds: float = 90,
) -> Path:
    head = git_text(project_root, "rev-parse", "HEAD")
    upstream = git_text(project_root, "rev-parse", "@{u}")
    dirty = bool(git_text(project_root, "status", "--porcelain", "--", "."))
    if dirty or head != upstream or source_commit != head:
        raise RuntimeError(
            f"scenario_d_training_source_preflight_failed:dirty={dirty}:"
            f"head={head}:upstream={upstream}:requested={source_commit}"
        )
    if active_run_ids():
        raise RuntimeError(f"active_lifecycle_runs_present:{active_run_ids()}")
    policy = ScenarioDPolicy.from_toml(policy_path)
    before_runtime = runtime_snapshot(inference_image_uri=inference_image_uri)
    supervisor = before_runtime["supervisor"]
    if (
        supervisor.get("status") != "healthy"
        or supervisor.get("source_commit") != source_commit
        or before_runtime["production_inference"].get("device") != "cuda"
    ):
        raise RuntimeError("scenario_d_training_runtime_preflight_failed")
    before_effects = external_side_effect_snapshot()
    started = utc_now()
    series_id = f"scenario-d-training-{started.strftime('%Y%m%dT%H%M%SZ')}-{head[:8]}"
    run_root = output_root / series_id
    run_root.mkdir(parents=True, exist_ok=False)
    write_json(run_root / "before-runtime.json", before_runtime)
    write_json(run_root / "before-side-effects.json", before_effects)
    timeline: list[dict[str, Any]] = []
    created = api_request(
        "POST",
        "/lifecycle-runs",
        {
            "profile_id": profile_id,
            "profile_version": profile_version,
            "actor": "scenario-d-integrated-validator",
            "reason": "Validate exact worker recovery during a real admitted training Job",
            "dry_run": True,
            "execution_mode": "automatic",
        },
    )
    run_id = str(created["run_id"])
    write_json(run_root / "created-run.json", created)
    handoff_approvals = issue_run_handoff_approvals(
        created,
        run_root=run_root,
        ttl_seconds=handoff_approval_ttl_seconds,
    )
    queued = api_request(
        "POST",
        f"/lifecycle-runs/{run_id}/queue",
        {
            "actor": "scenario-d-integrated-validator",
            "reason": "Queue governed Scenario D integrated training recovery proof",
            "expected_version": created["version"],
        },
    )
    write_json(run_root / "queued-run.json", queued)
    try:
        admitted = wait_for_admission(
            run_id,
            timeout_seconds=admission_timeout_seconds,
            timeline=timeline,
        )
        write_json(run_root / "training-admission.json", admitted)
        worker_identity, worker_process = exact_process_identity("lifecycle_worker")
        worker = admitted["worker"]
        supervisor_before = admitted["supervisor"]
        job_metadata = admitted["job"]["metadata"]
        if (
            int(worker_identity["pid"]) != int(worker["pid"])
            or worker_identity["process_instance_id"] != worker["process_instance_id"]
            or worker_identity["source_commit"] != source_commit
            or worker_identity["supervisor_lease_id"] != supervisor_before["lease_id"]
            or int(worker_identity["fencing_token"]) != int(supervisor_before["fencing_token"])
        ):
            raise RuntimeError("training_worker_exact_identity_mismatch")
        approval_expires = utc_now() + timedelta(minutes=10)
        action_material = {
            "run_id": run_id,
            "task_id": admitted["task"]["task_id"],
            "side_effect_key": admitted["side_effect"]["side_effect_key"],
            "job_uid": job_metadata["uid"],
            "worker_pid": worker_identity["pid"],
            "worker_process_instance_id": worker_identity["process_instance_id"],
            "source_commit": source_commit,
            "expires_at": approval_expires.isoformat(),
        }
        approval = {
            "schema_version": "evm.lifecycle_guard_d_training_approval.v1",
            "approval_id": f"approval-{hashlib.sha256(json.dumps(action_material, sort_keys=True).encode()).hexdigest()[:20]}",
            "decision": "approved",
            "single_use": True,
            "action_digest": canonical_digest(action_material),
            **action_material,
        }
        write_json(run_root / "approval.json", approval)

        recheck = admission_snapshot(run_id)
        recheck_identity, recheck_process = exact_process_identity("lifecycle_worker")
        if (
            recheck is None
            or recheck_identity != worker_identity
            or recheck_process != worker_process
            or recheck["job"]["metadata"]["uid"] != job_metadata["uid"]
            or recheck["side_effect"]["side_effect_key"]
            != admitted["side_effect"]["side_effect_key"]
            or parse_utc(approval["expires_at"]) <= utc_now()
        ):
            raise RuntimeError("training_injection_recheck_failed")
        injection_at = utc_now()
        injection_ns = time.monotonic_ns()
        approval["decision"] = "consumed"
        approval["consumed_at"] = injection_at.isoformat()
        write_json(run_root / "approval.json", approval)
        stop_exact_process(int(worker_identity["pid"]))
        recovery = collect_recovery(
            child="lifecycle_worker",
            old_pid=int(worker_identity["pid"]),
            source_commit=source_commit,
            lease_id=str(supervisor_before["lease_id"]),
            fencing_token=int(supervisor_before["fencing_token"]),
            injection_at=injection_at,
            injection_ns=injection_ns,
            timeout_seconds=policy.max_recovery_seconds,
        )
        write_json(run_root / "worker-recovery.json", recovery)
        terminal = wait_for_terminal(
            run_id,
            timeout_seconds=completion_timeout_seconds,
            timeline=timeline,
            run_root=run_root,
        )
    except Exception as exc:
        try:
            current = api_request("GET", f"/lifecycle-runs/{run_id}")
            write_json(
                run_root / "failure.json",
                {
                    "failed_at": utc_now().isoformat(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "run": current,
                },
            )
            cancelled = safe_cancel(
                current,
                "Scenario D integrated proof failed or timed out; request automatic safe cleanup",
            )
            if cancelled is not None:
                write_json(run_root / "automatic-safety-cancel.json", cancelled)
        finally:
            write_json(run_root / "timeline.json", timeline)
        raise

    write_json(run_root / "timeline.json", timeline)
    write_json(run_root / "terminal-run.json", terminal)
    after_runtime, runtime_restoration = wait_for_runtime_restoration(
        before_runtime=before_runtime,
        source_commit=source_commit,
        inference_image_uri=inference_image_uri,
        timeout_seconds=runtime_restoration_timeout_seconds,
    )
    after_effects = external_side_effect_snapshot()
    write_json(run_root / "after-runtime.json", after_runtime)
    write_json(run_root / "runtime-restoration.json", runtime_restoration)
    write_json(run_root / "after-side-effects.json", after_effects)
    final_ledger, ledger_path = side_effect_ledger(terminal)
    handoff_consumption = handoff_consumption_snapshot(handoff_approvals)
    write_json(run_root / "gpu-handoff-consumption.json", handoff_consumption)
    training_entry = exact_training_entry(final_ledger)
    tasks = tasks_for_run(run_id)
    jobs = run_job_identities(run_id)
    reconciliation_path = Path(str(training_entry.get("evidence_uri") or ""))
    reconciliation = read_json(reconciliation_path) if reconciliation_path.is_file() else {}
    stages = terminal.get("stages", [])
    task_types = [str(item.get("task_type")) for item in tasks]
    checks = {
        "lifecycle_completed_10_of_10": (
            terminal.get("state") == "completed"
            and len(stages) == 10
            and all(item.get("state") == "completed" for item in stages)
        ),
        "worker_detection_within_10_seconds": recovery["detection_seconds"] <= 10,
        "worker_recovery_within_60_seconds": recovery["recovery_seconds"] <= 60,
        "training_side_effect_reconciled_same_identity": (
            training_entry["state"] == "completed"
            and training_entry["runtime_id"] == admitted["task"]["runtime_id"]
            and reconciliation.get("resource_uid") == admitted["job"]["metadata"]["uid"]
            and reconciliation.get("mutation_performed") is False
        ),
        "side_effect_ledger_unique_and_committed": (
            len(final_ledger.entries) == 8
            and len({entry.side_effect_key for entry in final_ledger.entries}) == 8
            and all(entry.state == "completed" for entry in final_ledger.entries)
        ),
        "one_airflow_training_and_ct_task": (
            len(tasks) == 3
            and task_types.count("airflow_dag_run") == 1
            and task_types.count("kubernetes_job") == 2
            and all(item.get("status") == "done" for item in tasks)
        ),
        "one_training_and_one_ct_job_identity": (
            len(jobs) == 2
            and len({item["uid"] for item in jobs}) == 2
            and any(item["namespace"] == "evm-training" for item in jobs)
            and any(item["namespace"] == "evm-validation" for item in jobs)
        ),
        "approval_consumed_once": approval["decision"] == "consumed",
        "gpu_handoff_approvals_consumed_once": (
            set(handoff_consumption) == set(HANDOFF_PHASES)
            and all(item["consumed"] for item in handoff_consumption.values())
            and all(
                item["receipt"].get("run_id") == run_id
                for item in handoff_consumption.values()
            )
        ),
        "runtime_revision_converged": (
            after_runtime["supervisor"].get("source_commit") == source_commit
            and all(
                item.get("source_commit") == source_commit
                for item in after_runtime["supervisor"].get("children", [])
            )
        ),
        "production_cuda_prometheus_restored": (
            runtime_restoration["status"] == "pass"
        ),
    }
    result = {
        "schema_version": SCHEMA,
        "series_id": series_id,
        "run_id": run_id,
        "source_commit": source_commit,
        "source_branch": source_branch,
        "profile_id": profile_id,
        "profile_version": profile_version,
        "injection": {
            "target": "lifecycle_worker",
            "old_pid": worker_identity["pid"],
            "old_process_instance_id": worker_identity["process_instance_id"],
            "job_uid": admitted["job"]["metadata"]["uid"],
            "task_id": admitted["task"]["task_id"],
            "side_effect_key": admitted["side_effect"]["side_effect_key"],
        },
        "timing": {
            "detection_seconds": recovery["detection_seconds"],
            "recovery_seconds": recovery["recovery_seconds"],
            "runtime_restoration_seconds": runtime_restoration["elapsed_seconds"],
        },
        "checks": checks,
        "run_job_identities": jobs,
        "task_ids": [str(item["task_id"]) for item in tasks],
        "side_effect_ledger_uri": str(ledger_path.resolve()),
        "side_effect_ledger_sha256": file_digest(ledger_path),
        "before_external_counts": {
            key: value["count"] for key, value in before_effects.items()
        },
        "after_external_counts": {
            key: value["count"] for key, value in after_effects.items()
        },
        "status": "pass" if all(checks.values()) else "blocked",
        "finished_at": utc_now().isoformat(),
        "claim_boundary": (
            "Controlled local single-node full lifecycle with one exact worker termination "
            "during a real admitted GPU training Job; no HA, real-user traffic, or "
            "distributed exactly-once claim."
        ),
    }
    result_path = run_root / "result.json"
    result["evidence_index_uri"] = str((run_root / "evidence-index.json").resolve())
    write_json(result_path, result)
    build_evidence_index(run_root)
    if result["status"] != "pass":
        raise RuntimeError(f"scenario_d_training_acceptance_failed:{checks}")
    return result_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inject exact worker recovery during a real lifecycle training Job."
    )
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--profile-id", default="standard-b0-manual-tuning")
    parser.add_argument("--profile-version", type=int, default=9)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-branch", required=True)
    parser.add_argument("--admission-timeout-seconds", type=float, default=900)
    parser.add_argument("--completion-timeout-seconds", type=float, default=3600)
    parser.add_argument("--handoff-approval-ttl-seconds", type=int, default=7200)
    parser.add_argument(
        "--runtime-restoration-timeout-seconds", type=float, default=90
    )
    parser.add_argument("--inference-image-uri", default=DEFAULT_INFERENCE_IMAGE_URI)
    args = parser.parse_args()
    result = run(
        project_root=args.project_root.resolve(),
        policy_path=args.policy.resolve(),
        output_root=args.output_root.resolve(),
        profile_id=args.profile_id,
        profile_version=args.profile_version,
        source_commit=args.source_commit,
        source_branch=args.source_branch,
        admission_timeout_seconds=args.admission_timeout_seconds,
        completion_timeout_seconds=args.completion_timeout_seconds,
        handoff_approval_ttl_seconds=args.handoff_approval_ttl_seconds,
        inference_image_uri=args.inference_image_uri,
        runtime_restoration_timeout_seconds=(
            args.runtime_restoration_timeout_seconds
        ),
    )
    print(json.dumps({"result_uri": str(result.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
