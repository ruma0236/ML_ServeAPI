from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from evm.control_panel.lifecycle_gpu_handoff import issue_gpu_handoff_approval
from evm.control_panel.lifecycle_guards import file_digest
from evm.control_panel.lifecycle_integrity import (
    LifecycleIntegrityBlocked,
    validate_lifecycle_data_integrity,
    validate_lifecycle_release_submission,
)
from evm.control_panel.lifecycle_integrity_injection import (
    DATA_ACTION,
    RELEASE_ACTION,
    injection_receipt_path,
    issue_lifecycle_integrity_injection,
)
from evm.control_panel.lifecycle_runs import LifecycleRun
from evm.control_panel.readiness_evaluator import runtime_path
from evm.operations.lifecycle_guard_c_runner import count_delta, wait_for_release_boundary
from evm.operations.lifecycle_guard_d_training_live import (
    API_ROOT,
    active_run_ids,
    api_request,
    docker_desktop_kubectl,
    stage_for,
    tasks_for_run,
    wait_for_runtime_restoration,
)
from evm.operations.lifecycle_guard_e_runner import (
    DEFAULT_INFERENCE_IMAGE_URI,
    build_evidence_index,
    external_side_effect_snapshot,
    git_text,
    write_json,
)
from evm.operations.scenario_d_live import runtime_snapshot


SCHEMA = "evm.lifecycle_guard_scenario_e_integrated.v1"
MODE = "lifecycle_stage_injection"
HANDOFF_PHASES = ("training", "isolated_ct")
DEFAULT_OUTPUT_ROOT = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/"
    "lifecycle_guard_e_integrated"
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return payload


def save_integrity_profile(base_profile_id: str, base_profile_version: int) -> dict[str, Any]:
    base = api_request(
        "GET",
        f"/pipeline-profiles/{base_profile_id}?version={base_profile_version}",
    )
    profile = dict(base["profile"])
    profile["profile_name"] = "scenario-e-lifecycle-integrity-b0"
    profile["description"] = (
        "Real VisA/CUDA lifecycle profile for run-bound L2 and L6 integrity "
        "injection validation. Production replacement is excluded."
    )
    gates = dict(profile["gates"])
    gates["approval_policy"] = "two_person"
    gates["target_environment"] = "staging"
    gates["target_namespace"] = "evm-staging"
    gates["require_controlled_replay"] = False
    profile["gates"] = gates
    saved = api_request("POST", "/pipeline-profiles", profile)
    validation = saved.get("validation") or {}
    if validation.get("executable") is not True:
        raise RuntimeError(
            f"scenario_e_integrity_profile_not_executable:{validation.get('blockers')}"
        )
    return saved


def issue_handoff_approvals(
    run: dict[str, Any],
    *,
    evidence_root: Path,
    ttl_seconds: int,
) -> dict[str, Any]:
    lifecycle = LifecycleRun.model_validate(run)
    approvals: dict[str, Any] = {}
    for phase in HANDOFF_PHASES:
        reference_path = issue_gpu_handoff_approval(
            lifecycle,
            phase=phase,
            approver="scenario-e-resource-approver",
            reason=(
                "Authorize only the isolated single-GPU handoff for the corrected "
                "Scenario E lifecycle attempt"
            ),
            ttl_seconds=ttl_seconds,
            runner=docker_desktop_kubectl,
        )
        approvals[phase] = {
            "reference_uri": str(reference_path.resolve()),
            "reference": read_json(reference_path),
        }
    write_json(evidence_root / "gpu-handoff-approvals.json", approvals)
    return approvals


def wait_for_data_integrity_block(
    run_id: str,
    *,
    timeout_seconds: float,
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    previous: tuple[str, str, str] | None = None
    while time.monotonic() <= deadline:
        run = api_request("GET", f"/lifecycle-runs/{run_id}")
        current_stage = str(run.get("current_stage") or "none")
        stage = stage_for(run, current_stage) if current_stage != "none" else {}
        state = (
            str(run.get("state") or "unknown"),
            current_stage,
            str(stage.get("state") or "none"),
        )
        if state != previous:
            timeline.append(
                {
                    "observed_at": utc_now().isoformat(),
                    "phase": "data_integrity_injection",
                    "run_state": state[0],
                    "current_stage": state[1],
                    "stage_state": state[2],
                    "runtime_state": stage.get("runtime_state"),
                    "blockers": stage.get("blockers") or [],
                    "progress": run.get("progress"),
                }
            )
            previous = state
        if state == ("blocked", "data_pipeline", "blocked"):
            return run
        if run.get("state") in {"failed", "cancelled", "rolled_back", "completed"}:
            raise RuntimeError(f"scenario_e_data_boundary_not_reached:{state}")
        time.sleep(2)
    raise TimeoutError("scenario_e_data_integrity_timeout")


def cancel_run(run: dict[str, Any], reason: str) -> dict[str, Any] | None:
    if run.get("state") in {"completed", "cancelled", "rolled_back"}:
        return None
    return api_request(
        "POST",
        f"/lifecycle-runs/{run['run_id']}/cancel",
        {
            "actor": "scenario-e-integrated-validator",
            "reason": reason,
            "expected_version": run["version"],
        },
    )


def replay_data_decision(
    artifact_root: Path,
    replay_root: Path,
    *,
    replay_count: int = 3,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for replay in range(1, replay_count + 1):
        started = time.monotonic()
        try:
            report_path = validate_lifecycle_data_integrity(artifact_root)
            report = read_json(report_path)
        except LifecycleIntegrityBlocked:
            report = read_json(artifact_root / "data" / "integrity-validation.json")
        item = {
            "replay": replay,
            "decision": report["decision"],
            "blockers": report["blockers"],
            "decision_fingerprint": report["decision_fingerprint"],
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }
        write_json(replay_root / f"replay-{replay}.json", item)
        results.append(item)
    return results


def replay_release_decision(
    submission: Path,
    *,
    run: dict[str, Any],
    identity: dict[str, str],
    replay_root: Path,
    replay_count: int = 3,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for replay in range(1, replay_count + 1):
        started = time.monotonic()
        try:
            decision = validate_lifecycle_release_submission(
                submission,
                run_id=str(run["run_id"]),
                source_commit=str(run["source_commit"]),
                expected_candidate_id=identity["candidate_id"],
                expected_model_digest=identity["model_digest"],
                expected_ct_evaluation_id=identity["ct_evaluation_id"],
            )
        except LifecycleIntegrityBlocked as exc:
            decision = {
                "decision": "blocked",
                "blockers": exc.blockers,
                "decision_fingerprint": exc.decision_fingerprint,
            }
        item = {
            **decision,
            "replay": replay,
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }
        write_json(replay_root / f"replay-{replay}.json", item)
        results.append(item)
    return results


def stable_replay(
    results: list[dict[str, Any]],
    *,
    decision: str,
    required_blockers: set[str] | None = None,
) -> bool:
    required = required_blockers or set()
    return (
        len(results) == 3
        and all(item.get("decision") == decision for item in results)
        and all(required.issubset(set(item.get("blockers") or [])) for item in results)
        and None not in {item.get("decision_fingerprint") for item in results}
        and len({item.get("decision_fingerprint") for item in results}) == 1
        and all(float(item.get("elapsed_seconds") or 999) <= 30 for item in results)
    )


def release_identity(run: dict[str, Any], submission: dict[str, Any]) -> dict[str, str]:
    identity = {
        "candidate_id": str(submission.get("candidate_id") or ""),
        "model_digest": str(submission.get("model_digest") or ""),
        "ct_evaluation_id": str(submission.get("ct_evaluation_id") or ""),
    }
    if not all(identity.values()) or len(identity["model_digest"]) != 64:
        raise RuntimeError("scenario_e_release_identity_incomplete")
    if submission.get("run_id") != run.get("run_id"):
        raise RuntimeError("scenario_e_release_run_identity_mismatch")
    return identity


def approval_denial(run: dict[str, Any], identity: dict[str, str]) -> dict[str, Any]:
    payload = {
        "actor": "scenario-e-release-approver",
        "approver": "scenario-e-release-approver",
        "reason": "Exercise the exact run-local Scenario E release identity gate",
        "candidate_id": identity["candidate_id"],
        "model_digest": identity["model_digest"],
        "ct_evaluation_id": identity["ct_evaluation_id"],
        "expected_version": run["version"],
    }
    first = requests.post(
        f"{API_ROOT}/lifecycle-runs/{run['run_id']}/approve",
        json=payload,
        timeout=60,
    )
    first_body = first.json()
    first_detail = first_body.get("detail") if isinstance(first_body, dict) else {}
    if (
        first.status_code != 422
        or not isinstance(first_detail, dict)
        or first_detail.get("error") != "lifecycle_release_integrity_blocked"
    ):
        raise RuntimeError(
            f"scenario_e_release_admission_not_blocked:{first.status_code}:{first_body}"
        )
    second = requests.post(
        f"{API_ROOT}/lifecycle-runs/{run['run_id']}/approve",
        json=payload,
        timeout=60,
    )
    second_body = second.json()
    second_detail = second_body.get("detail") if isinstance(second_body, dict) else {}
    if (
        second.status_code != 422
        or not isinstance(second_detail, dict)
        or second_detail.get("error") != "lifecycle_integrity_injection_blocked"
        or "already_consumed" not in str(second_detail.get("message") or "")
    ):
        raise RuntimeError(
            f"scenario_e_single_use_replay_not_blocked:{second.status_code}:{second_body}"
        )
    return {
        "request": payload,
        "first": {"status_code": first.status_code, "response": first_body},
        "second": {"status_code": second.status_code, "response": second_body},
    }


def canonical_dataset_hashes(profile: dict[str, Any]) -> dict[str, str]:
    data = profile.get("profile", {}).get("data", {})
    source = runtime_path(str(data.get("source_manifest_uri") or ""))
    split = runtime_path(str(data.get("split_manifest_uri") or ""))
    if not source.is_file() or not split.is_file():
        raise RuntimeError("scenario_e_canonical_dataset_evidence_missing")
    paths = [source, split]
    index = read_json(split)
    for item in index.get("shards", []):
        if not isinstance(item, dict):
            continue
        path = runtime_path(str(item.get("path") or ""))
        if not path.is_absolute():
            path = split.parent / path
        if not path.is_file():
            raise RuntimeError(f"scenario_e_canonical_shard_missing:{path}")
        paths.append(path)
    return {str(path.resolve()): file_digest(path) for path in paths}


def evidence_references(paths: dict[str, Path]) -> dict[str, Any]:
    references: dict[str, Any] = {}
    blockers: list[str] = []
    for name, path in sorted(paths.items()):
        resolved = path.resolve()
        if not resolved.is_file():
            blockers.append(f"scenario_e_external_evidence_missing:{name}")
            continue
        references[name] = {
            "uri": str(resolved),
            "sha256": file_digest(resolved),
            "size_bytes": resolved.stat().st_size,
        }
    return {
        "schema_version": "evm.lifecycle_guard_external_evidence.v1",
        "status": "pass" if not blockers else "blocked",
        "blockers": blockers,
        "references": references,
    }


def run(
    *,
    project_root: Path,
    output_root: Path,
    base_profile_id: str,
    base_profile_version: int,
    source_commit: str,
    source_branch: str,
    data_timeout_seconds: float,
    lifecycle_timeout_seconds: float,
    injection_ttl_seconds: int,
    handoff_approval_ttl_seconds: int,
    runtime_restoration_timeout_seconds: float,
    inference_image_uri: str,
) -> Path:
    head = git_text(project_root, "rev-parse", "HEAD")
    upstream = git_text(project_root, "rev-parse", "@{upstream}")
    dirty = bool(git_text(project_root, "status", "--porcelain", "--", "."))
    if dirty or head != upstream or source_commit != head:
        raise RuntimeError(
            f"scenario_e_integrated_source_preflight_failed:dirty={dirty}:"
            f"head={head}:upstream={upstream}:requested={source_commit}"
        )
    if active_run_ids():
        raise RuntimeError(f"active_lifecycle_runs_present:{active_run_ids()}")
    before_runtime = runtime_snapshot(inference_image_uri=inference_image_uri)
    supervisor = before_runtime["supervisor"]
    if (
        supervisor.get("status") != "healthy"
        or supervisor.get("source_commit") != source_commit
        or before_runtime["production_inference"].get("device") != "cuda"
        or before_runtime["kubernetes"].get("ready_replicas") != 1
        or before_runtime["kubernetes"].get("gpu_allocatable") != "1"
        or before_runtime["prometheus"].get("health") != "up"
    ):
        raise RuntimeError("scenario_e_integrated_runtime_preflight_failed")
    before_effects = external_side_effect_snapshot()
    profile = save_integrity_profile(base_profile_id, base_profile_version)
    canonical_before = canonical_dataset_hashes(profile)
    started = utc_now()
    series_id = f"scenario-e-integrated-{started.strftime('%Y%m%dT%H%M%SZ')}-{head[:8]}"
    run_root = output_root / series_id
    run_root.mkdir(parents=True, exist_ok=False)
    write_json(run_root / "before-runtime.json", before_runtime)
    write_json(run_root / "before-side-effects.json", before_effects)
    write_json(run_root / "profile.json", profile)
    write_json(run_root / "canonical-before.json", canonical_before)
    timeline: list[dict[str, Any]] = []
    data_run: dict[str, Any] | None = None
    release_run: dict[str, Any] | None = None
    try:
        data_run = api_request(
            "POST",
            "/lifecycle-runs",
            {
                "profile_id": profile["profile_id"],
                "profile_version": profile["version"],
                "actor": "scenario-e-data-requester",
                "reason": "Validate actual post-Airflow L2 integrity admission",
                "dry_run": True,
                "execution_mode": "automatic",
            },
        )
        data_contract = issue_lifecycle_integrity_injection(
            LifecycleRun.model_validate(data_run),
            action=DATA_ACTION,
            actor="scenario-e-integrated-validator",
            reason="Inject one wrong shard identity after real Airflow and before training",
            ttl_seconds=injection_ttl_seconds,
        )
        write_json(run_root / "data-created-run.json", data_run)
        write_json(run_root / "data-injection-reference.json", {"uri": str(data_contract)})
        data_run = api_request(
            "POST",
            f"/lifecycle-runs/{data_run['run_id']}/queue",
            {
                "actor": "scenario-e-data-requester",
                "reason": "Queue isolated data-integrity lifecycle branch",
                "expected_version": data_run["version"],
            },
        )
        data_run = wait_for_data_integrity_block(
            str(data_run["run_id"]),
            timeout_seconds=data_timeout_seconds,
            timeline=timeline,
        )
        write_json(run_root / "data-blocked-run.json", data_run)
        data_artifact_root = Path(str(data_run["artifact_root"]))
        data_report = data_artifact_root / "data" / "integrity-validation.json"
        data_receipt = injection_receipt_path(
            LifecycleRun.model_validate(data_run), DATA_ACTION
        )
        data_replays = replay_data_decision(
            data_artifact_root,
            run_root / "data-replays",
        )
        data_tasks = tasks_for_run(str(data_run["run_id"]))
        data_effects = external_side_effect_snapshot()
        data_delta = count_delta(before_effects, data_effects)
        data_stage = stage_for(data_run, "data_pipeline")
        training_stage = stage_for(data_run, "model_training")
        data_checks = {
            "real_airflow_completed_before_guard": (
                len(data_tasks) == 1
                and data_tasks[0].get("task_type") == "airflow_dag_run"
                and data_tasks[0].get("status") == "done"
                and bool(data_tasks[0].get("runtime_id"))
            ),
            "l2_blocked_integrity": (
                data_run.get("state") == "blocked"
                and data_run.get("current_stage") == "data_pipeline"
                and data_stage.get("state") == "blocked"
                and "integrity_shard_index_identity_mismatch"
                in set(data_stage.get("blockers") or [])
            ),
            "single_use_data_contract_consumed": data_receipt.is_file(),
            "training_not_admitted": (
                training_stage.get("state") == "not_started"
                and training_stage.get("attempt") == 0
                and not training_stage.get("task_id")
            ),
            "data_branch_external_effects_zero": all(
                value == 0 for value in data_delta.values()
            ),
            "data_decision_three_replays": stable_replay(
                data_replays,
                decision="blocked",
                required_blockers={"integrity_shard_index_identity_mismatch"},
            ),
        }
        write_json(
            run_root / "data-boundary.json",
            {
                "run": data_run,
                "tasks": data_tasks,
                "external_delta": data_delta,
                "integrity_report": read_json(data_report),
                "injection_receipt": read_json(data_receipt),
                "checks": data_checks,
            },
        )
        if not all(data_checks.values()):
            raise RuntimeError(f"scenario_e_data_acceptance_failed:{data_checks}")
        cancelled_data = cancel_run(
            data_run,
            "Scenario E L2 evidence captured; close isolated corrupt attempt",
        )
        if cancelled_data is not None:
            data_run = cancelled_data
            write_json(run_root / "data-controlled-cleanup.json", data_run)

        release_run = api_request(
            "POST",
            "/lifecycle-runs",
            {
                "profile_id": profile["profile_id"],
                "profile_version": profile["version"],
                "actor": "scenario-e-corrected-requester",
                "reason": "Run corrected data through CUDA training MLflow and isolated CT",
                "dry_run": True,
                "execution_mode": "automatic",
            },
        )
        write_json(run_root / "release-created-run.json", release_run)
        issue_handoff_approvals(
            release_run,
            evidence_root=run_root,
            ttl_seconds=handoff_approval_ttl_seconds,
        )
        release_run = api_request(
            "POST",
            f"/lifecycle-runs/{release_run['run_id']}/queue",
            {
                "actor": "scenario-e-corrected-requester",
                "reason": "Queue corrected immutable lifecycle attempt",
                "expected_version": release_run["version"],
            },
        )
        release_run = wait_for_release_boundary(
            str(release_run["run_id"]),
            timeout_seconds=lifecycle_timeout_seconds,
            timeline=timeline,
        )
        write_json(run_root / "release-boundary-run.json", release_run)
        canonical_submission_path = runtime_path(
            str(release_run.get("release_submission_uri") or "")
        )
        canonical_submission = read_json(canonical_submission_path)
        identity = release_identity(release_run, canonical_submission)
        canonical_replays = replay_release_decision(
            canonical_submission_path,
            run=release_run,
            identity=identity,
            replay_root=run_root / "canonical-release-replays",
        )
        release_contract = issue_lifecycle_integrity_injection(
            LifecycleRun.model_validate(release_run),
            action=RELEASE_ACTION,
            actor="scenario-e-integrated-validator",
            reason="Inject one wrong model identity at actual release approval admission",
            ttl_seconds=injection_ttl_seconds,
        )
        write_json(
            run_root / "release-injection-reference.json",
            {"uri": str(release_contract)},
        )
        denial = approval_denial(release_run, identity)
        write_json(run_root / "approval-denial.json", denial)
        release_run = api_request(
            "GET", f"/lifecycle-runs/{release_run['run_id']}"
        )
        release_receipt = injection_receipt_path(
            LifecycleRun.model_validate(release_run), RELEASE_ACTION
        )
        receipt_payload = read_json(release_receipt)
        derived_submission = Path(str(receipt_payload["derived_submission_uri"]))
        blocked_replays = replay_release_decision(
            derived_submission,
            run=release_run,
            identity=identity,
            replay_root=run_root / "blocked-release-replays",
        )
        release_tasks = tasks_for_run(str(release_run["run_id"]))
        completed = {
            item["stage_id"]: item["state"] for item in release_run.get("stages", [])
        }
        release_checks = {
            "corrected_data_to_l6_reached": (
                release_run.get("state") == "waiting_approval"
                and release_run.get("current_stage") == "approval"
                and all(
                    completed.get(stage) == "completed"
                    for stage in (
                        "data_pipeline",
                        "model_training",
                        "model_evaluation",
                        "artifact_readiness",
                        "ci_ct_gate",
                    )
                )
            ),
            "real_airflow_training_mlflow_ct_evidence": (
                len(release_tasks) == 3
                and all(item.get("status") == "done" for item in release_tasks)
                and bool(canonical_submission.get("mlflow_run_id"))
                and runtime_path(
                    str(canonical_submission.get("model_artifact_uri") or "")
                ).is_file()
                and runtime_path(str(release_run.get("ct_evaluation_uri") or "")).is_file()
            ),
            "canonical_release_three_replays": stable_replay(
                canonical_replays, decision="pass"
            ),
            "single_use_release_contract_consumed": release_receipt.is_file(),
            "actual_approval_failed_closed": denial["first"]["status_code"] == 422,
            "duplicate_approval_failed_closed": denial["second"]["status_code"] == 422,
            "release_identity_three_replays": stable_replay(
                blocked_replays,
                decision="blocked",
                required_blockers={
                    "release_model_digest_mismatch",
                    "release_model_artifact_digest_mismatch",
                },
            ),
            "deployment_not_admitted": (
                completed.get("deployment") == "not_started"
                and release_run.get("deployment_intent_id") is None
            ),
        }
        write_json(
            run_root / "release-boundary.json",
            {
                "run": release_run,
                "tasks": release_tasks,
                "canonical_submission": canonical_submission,
                "injection_receipt": receipt_payload,
                "checks": release_checks,
            },
        )
        if not all(release_checks.values()):
            raise RuntimeError(f"scenario_e_release_acceptance_failed:{release_checks}")
        cancelled_release = cancel_run(
            release_run,
            "Scenario E L6 evidence captured; close without deployment",
        )
        if cancelled_release is not None:
            release_run = cancelled_release
            write_json(run_root / "release-controlled-cleanup.json", release_run)

        after_runtime, restoration = wait_for_runtime_restoration(
            before_runtime=before_runtime,
            source_commit=source_commit,
            inference_image_uri=inference_image_uri,
            timeout_seconds=runtime_restoration_timeout_seconds,
        )
        after_effects = external_side_effect_snapshot()
        final_delta = count_delta(before_effects, after_effects)
        canonical_after = canonical_dataset_hashes(profile)
        write_json(run_root / "after-runtime.json", after_runtime)
        write_json(run_root / "runtime-restoration.json", restoration)
        write_json(run_root / "after-side-effects.json", after_effects)
        write_json(run_root / "canonical-after.json", canonical_after)
        write_json(run_root / "timeline.json", timeline)
        references = evidence_references(
            {
                "data_lifecycle_run": Path(str(data_run["artifact_root"]))
                / "lifecycle_run.json",
                "data_integrity_report": data_report,
                "data_injection_contract": data_contract,
                "data_injection_receipt": data_receipt,
                "release_lifecycle_run": Path(str(release_run["artifact_root"]))
                / "lifecycle_run.json",
                "release_submission": canonical_submission_path,
                "release_injection_contract": release_contract,
                "release_injection_receipt": release_receipt,
                "derived_release_submission": derived_submission,
                "readiness": runtime_path(str(release_run.get("readiness_uri") or "")),
                "model_matrix": runtime_path(
                    str(release_run.get("model_matrix_uri") or "")
                ),
                "ct_evaluation": runtime_path(
                    str(release_run.get("ct_evaluation_uri") or "")
                ),
                "model_artifact": runtime_path(
                    str(canonical_submission.get("model_artifact_uri") or "")
                ),
            }
        )
        write_json(run_root / "external-evidence.json", references)
        final_checks = {
            **{f"data_{key}": value for key, value in data_checks.items()},
            **{f"release_{key}": value for key, value in release_checks.items()},
            "canonical_dataset_unchanged": canonical_before == canonical_after,
            "external_evidence_hashes_complete": references["status"] == "pass",
            "deployment_intent_zero": final_delta.get("deployment_intents") == 0,
            "expected_corrected_training_effects": (
                final_delta.get("kubernetes_jobs") == 2
                and final_delta.get("mlflow_runs") == 1
                and final_delta.get("model_candidates") == 1
            ),
            "production_runtime_restored": restoration.get("status") == "pass",
            "exact_production_identity_unchanged": (
                after_runtime["kubernetes"].get("deployment_uid")
                == before_runtime["kubernetes"].get("deployment_uid")
                and after_runtime["production_ready"].get("candidate_id")
                == before_runtime["production_ready"].get("candidate_id")
                and after_runtime["production_ready"].get("model_sha256")
                == before_runtime["production_ready"].get("model_sha256")
                and after_runtime["production_inference"].get("device") == "cuda"
            ),
        }
        result = {
            "schema_version": SCHEMA,
            "series_id": series_id,
            "mode": MODE,
            "lifecycle_run_id": release_run["run_id"],
            "data_blocked_run_id": data_run["run_id"],
            "release_blocked_run_id": release_run["run_id"],
            "source_revision": source_commit,
            "source_branch": source_branch,
            "profile_id": profile["profile_id"],
            "profile_version": profile["version"],
            "lifecycle_reachability": {
                "L2": "real_airflow_success_then_run_local_integrity_block",
                "L4": "real_cuda_training_mlflow_model_seal_and_readiness",
                "L6": "actual_release_approval_fail_closed_before_intent",
            },
            "external_delta": final_delta,
            "checks": final_checks,
            "status": "pass" if all(final_checks.values()) else "blocked",
            "finished_at": utc_now().isoformat(),
            "claim_boundary": (
                "Controlled local single-node VisA/CUDA lifecycle integrity "
                "injection. No real-user traffic, HA, production replacement, "
                "multi-node transaction, business A/B, or SLA claim."
            ),
        }
        result_path = run_root / "result.json"
        result["evidence_index_uri"] = str(
            (run_root / "evidence-index.json").resolve()
        )
        write_json(result_path, result)
        build_evidence_index(run_root)
        if result["status"] != "pass":
            raise RuntimeError(f"scenario_e_integrated_acceptance_failed:{final_checks}")
        return result_path
    except Exception as exc:
        write_json(
            run_root / "failure.json",
            {
                "failed_at": utc_now().isoformat(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "data_run": data_run,
                "release_run": release_run,
            },
        )
        for current in (data_run, release_run):
            if current is None:
                continue
            try:
                latest = api_request("GET", f"/lifecycle-runs/{current['run_id']}")
                cleanup = cancel_run(
                    latest,
                    "Scenario E integrated proof failed; bounded run-local cleanup",
                )
                if cleanup is not None:
                    write_json(
                        run_root / f"{current['run_id']}-automatic-cleanup.json",
                        cleanup,
                    )
            except (OSError, RuntimeError, requests.RequestException):
                pass
        write_json(run_root / "timeline.json", timeline)
        build_evidence_index(run_root)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run actual LifecycleRun Scenario E L2/L4/L6 integrity proof."
    )
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--base-profile-id", default="standard-b0-manual-tuning")
    parser.add_argument("--base-profile-version", type=int, default=9)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-branch", required=True)
    parser.add_argument("--data-timeout-seconds", type=float, default=1800)
    parser.add_argument("--lifecycle-timeout-seconds", type=float, default=5400)
    parser.add_argument("--injection-ttl-seconds", type=int, default=7200)
    parser.add_argument("--handoff-approval-ttl-seconds", type=int, default=7200)
    parser.add_argument("--runtime-restoration-timeout-seconds", type=float, default=120)
    parser.add_argument("--inference-image-uri", default=DEFAULT_INFERENCE_IMAGE_URI)
    args = parser.parse_args()
    result = run(
        project_root=args.project_root.resolve(),
        output_root=args.output_root.resolve(),
        base_profile_id=args.base_profile_id,
        base_profile_version=args.base_profile_version,
        source_commit=args.source_commit,
        source_branch=args.source_branch,
        data_timeout_seconds=args.data_timeout_seconds,
        lifecycle_timeout_seconds=args.lifecycle_timeout_seconds,
        injection_ttl_seconds=args.injection_ttl_seconds,
        handoff_approval_ttl_seconds=args.handoff_approval_ttl_seconds,
        runtime_restoration_timeout_seconds=args.runtime_restoration_timeout_seconds,
        inference_image_uri=args.inference_image_uri,
    )
    print(json.dumps({"result_uri": str(result.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
