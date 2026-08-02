from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evm.control_panel.lifecycle_gpu_handoff import issue_gpu_handoff_approval
from evm.control_panel.lifecycle_guards import file_digest
from evm.control_panel.readiness_evaluator import runtime_path
from evm.control_panel.lifecycle_runs import LifecycleRun
from evm.operations.lifecycle_guard_d_training_live import (
    active_run_ids,
    api_request,
    docker_desktop_kubectl,
    safe_cancel,
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


SCHEMA = "evm.lifecycle_guard_scenario_c_integrated.v1"
RESUME_HANDOFF_PHASES = ("training", "isolated_ct")


def utc_now() -> datetime:
    return datetime.now(UTC)


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return payload


def validate_source_evidence_index(
    scenario_root: Path,
    index: dict[str, Any],
) -> dict[str, Any]:
    files = index.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeError("scenario_c_evidence_index_files_missing")
    observations: list[dict[str, Any]] = []
    blockers: list[str] = []
    for name, item in sorted(files.items()):
        if not isinstance(item, dict):
            blockers.append(f"scenario_c_index_entry_invalid:{name}")
            continue
        path = runtime_path(str(item.get("uri") or ""))
        expected = str(item.get("sha256") or "")
        observed = file_digest(path) if path.is_file() else None
        matched = observed == expected and len(expected) == 64
        observations.append(
            {
                "name": name,
                "uri": str(path),
                "expected_sha256": expected,
                "observed_sha256": observed,
                "matched": matched,
            }
        )
        if not matched:
            blockers.append(f"scenario_c_evidence_hash_mismatch:{name}")
    result = {
        "status": "pass" if not blockers else "blocked",
        "checked": len(observations),
        "matched": sum(item["matched"] for item in observations),
        "blockers": blockers,
        "observations": observations,
    }
    if blockers:
        raise RuntimeError(f"scenario_c_evidence_hash_closure_failed:{blockers}")
    return result


def quality_review_path(run: dict[str, Any]) -> Path:
    uri = str(run.get("quality_review_uri") or "")
    if not uri:
        raise RuntimeError("lifecycle_quality_review_uri_missing")
    return runtime_path(uri)


def registration_payload(
    *,
    run: dict[str, Any],
    scenario_root: Path,
    observed_at: str,
    actor: str = "scenario-c-drift-monitor",
) -> dict[str, Any]:
    paths = {
        "policy": scenario_root / "policy.json",
        "identity": scenario_root / "source-identities.json",
        "review_event": scenario_root / "review-event.json",
        "retraining_candidate": scenario_root / "retraining-candidate.json",
        "derived_manifest": scenario_root / "derived-shift-manifest.jsonl",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise RuntimeError(f"scenario_c_evidence_missing:{missing}")
    return {
        "actor": actor,
        "reason": "Bind real CUDA VisA drift evidence to the exact lifecycle run",
        "expected_version": run["version"],
        "policy_uri": str(paths["policy"].resolve()),
        "policy_sha256": file_digest(paths["policy"]),
        "identity_uri": str(paths["identity"].resolve()),
        "identity_sha256": file_digest(paths["identity"]),
        "review_event_uri": str(paths["review_event"].resolve()),
        "review_event_sha256": file_digest(paths["review_event"]),
        "retraining_candidate_uri": str(paths["retraining_candidate"].resolve()),
        "retraining_candidate_sha256": file_digest(paths["retraining_candidate"]),
        "derived_manifest_uri": str(paths["derived_manifest"].resolve()),
        "derived_manifest_sha256": file_digest(paths["derived_manifest"]),
        "observed_at": observed_at,
    }


def quality_action_payload(
    *,
    run: dict[str, Any],
    review: dict[str, Any],
    action: str,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "actor": actor,
        "reason": reason,
        "expected_version": run["version"],
        "expected_review_version": review["review_version"],
        "action": action,
        "candidate_id": review["candidate_id"],
        "candidate_digest": review["candidate_digest"],
        "approval_ttl_seconds": 7200,
    }


def register_with_replays(
    run: dict[str, Any],
    *,
    scenario_root: Path,
    observed_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    current = run
    for _index in range(3):
        payload = registration_payload(
            run=current,
            scenario_root=scenario_root,
            observed_at=observed_at,
        )
        current = api_request(
            "POST",
            f"/lifecycle-runs/{run['run_id']}/quality-review",
            payload,
        )
        review = read_json(quality_review_path(current))
        attempts.append(
            {
                "request": payload,
                "run_version": current["version"],
                "review_version": review["review_version"],
                "registration_attempts": review["registration_attempts"],
                "duplicate_attempts": review["duplicate_attempts"],
                "stale_attempts": review["stale_attempts"],
                "event_id": review["event_id"],
                "candidate_id": review["candidate_id"],
            }
        )
    return current, attempts


def issue_resume_handoff_approvals(
    run: dict[str, Any],
    *,
    run_root: Path,
    ttl_seconds: int,
) -> dict[str, Any]:
    lifecycle = LifecycleRun.model_validate(run)
    approvals: dict[str, Any] = {}
    for phase in RESUME_HANDOFF_PHASES:
        reference_path = issue_gpu_handoff_approval(
            lifecycle,
            phase=phase,
            approver="scenario-c-resource-approver",
            reason=(
                "Pre-authorize the bounded single-GPU handoff after an exact "
                "Scenario C approved-for-training decision"
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


def handoff_consumption(approvals: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for phase, item in approvals.items():
        approval_path = Path(str(item["reference"]["approval_path"]))
        consumed_path = approval_path.with_name(
            f"{approval_path.stem}.consumed.json"
        )
        result[phase] = {
            "approval_id": item["reference"]["approval_id"],
            "consumed": consumed_path.is_file(),
            "receipt_uri": str(consumed_path.resolve()),
            "receipt": read_json(consumed_path) if consumed_path.is_file() else None,
        }
    return result


def wait_for_quality_hold(
    run_id: str,
    *,
    timeout_seconds: float,
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_state: tuple[str, str, str] | None = None
    while time.monotonic() <= deadline:
        run = api_request("GET", f"/lifecycle-runs/{run_id}")
        current_stage = str(run.get("current_stage") or "none")
        stage = stage_for(run, current_stage) if current_stage != "none" else {}
        state = (
            str(run.get("state")),
            current_stage,
            str(stage.get("state") or "none"),
        )
        if state != last_state:
            timeline.append(
                {
                    "observed_at": utc_now().isoformat(),
                    "phase": "await_quality_hold",
                    "run_state": state[0],
                    "current_stage": state[1],
                    "stage_state": state[2],
                    "progress": run.get("progress"),
                }
            )
            last_state = state
        if state == ("blocked", "model_training", "blocked"):
            return run
        if run.get("state") in {"failed", "cancelled", "rolled_back", "completed"}:
            raise RuntimeError(f"quality_hold_not_reached:{state}")
        time.sleep(2)
    raise TimeoutError("quality_hold_timeout")


def wait_for_release_boundary(
    run_id: str,
    *,
    timeout_seconds: float,
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_state: tuple[str, str, str] | None = None
    while time.monotonic() <= deadline:
        run = api_request("GET", f"/lifecycle-runs/{run_id}")
        current_stage = str(run.get("current_stage") or "none")
        stage = stage_for(run, current_stage) if current_stage != "none" else {}
        state = (
            str(run.get("state")),
            current_stage,
            str(stage.get("state") or "none"),
        )
        if state != last_state:
            timeline.append(
                {
                    "observed_at": utc_now().isoformat(),
                    "phase": "governed_resume",
                    "run_state": state[0],
                    "current_stage": state[1],
                    "stage_state": state[2],
                    "runtime_state": stage.get("runtime_state"),
                    "progress": run.get("progress"),
                }
            )
            last_state = state
        if state == ("waiting_approval", "approval", "waiting_approval"):
            return run
        if run.get("state") in {
            "blocked",
            "failed",
            "cancelled",
            "rolled_back",
            "completed",
        }:
            raise RuntimeError(f"governed_resume_failed:{state}")
        time.sleep(2)
    raise TimeoutError("governed_resume_timeout")


def count_delta(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, int]:
    return {
        key: int(after[key]["count"]) - int(before[key]["count"])
        for key in sorted(before)
    }


def validate_hold_boundary(
    run: dict[str, Any],
    *,
    external_delta: dict[str, int],
    task_count: int,
    review: dict[str, Any],
) -> dict[str, bool]:
    training = stage_for(run, "model_training")
    return {
        "run_blocked_at_model_training": (
            run.get("state") == "blocked"
            and run.get("current_stage") == "model_training"
            and training.get("state") == "blocked"
        ),
        "training_not_admitted": (
            training.get("attempt") == 0
            and not training.get("task_id")
            and not training.get("runtime_id")
        ),
        "one_event_one_candidate_after_three_signals": (
            review.get("registration_attempts") == 3
            and review.get("duplicate_attempts") == 2
            and review.get("event_id") == run.get("quality_review_event_id")
            and review.get("candidate_id") == run.get("retraining_candidate_id")
        ),
        "manual_hold_visible": (
            review.get("state") == "manual_hold"
            and run.get("quality_review_state") == "manual_hold"
        ),
        "only_airflow_task_exists": task_count == 1,
        "zero_training_release_side_effects": (
            external_delta
            == {
                "deployment_intents": 0,
                "kubernetes_jobs": 0,
                "mlflow_runs": 0,
                "model_candidates": 0,
            }
        ),
    }


def validate_resume_boundary(
    run: dict[str, Any],
    *,
    external_delta: dict[str, int],
    task_count: int,
    review: dict[str, Any],
    handoffs: dict[str, Any],
) -> dict[str, bool]:
    completed = {
        stage["stage_id"]: stage["state"]
        for stage in run.get("stages", [])
    }
    return {
        "stopped_at_independent_release_approval": (
            run.get("state") == "waiting_approval"
            and run.get("current_stage") == "approval"
            and completed.get("approval") == "waiting_approval"
            and completed.get("deployment") == "not_started"
        ),
        "data_training_evaluation_readiness_ct_complete": all(
            completed.get(stage_id) == "completed"
            for stage_id in (
                "data_pipeline",
                "model_training",
                "model_evaluation",
                "artifact_readiness",
                "ci_ct_gate",
            )
        ),
        "training_approval_consumed_once": (
            review.get("state") == "approved_for_training"
            and review.get("approval_consumption_count") == 1
            and bool(review.get("approval_consumed_at"))
        ),
        "training_and_ct_handoffs_consumed": (
            set(handoffs) == set(RESUME_HANDOFF_PHASES)
            and all(item.get("consumed") is True for item in handoffs.values())
        ),
        "three_exact_tasks": task_count == 3,
        "expected_training_side_effect_delta": (
            external_delta.get("kubernetes_jobs") == 2
            and external_delta.get("mlflow_runs") == 1
            and external_delta.get("model_candidates") == 1
        ),
        "no_automatic_release_or_replacement": (
            external_delta.get("deployment_intents") == 0
            and not run.get("deployment_intent_id")
        ),
    }


def run(
    *,
    project_root: Path,
    scenario_root: Path,
    output_root: Path,
    profile_id: str,
    profile_version: int,
    source_commit: str,
    source_branch: str,
    hold_timeout_seconds: float,
    resume_timeout_seconds: float,
    handoff_approval_ttl_seconds: int,
    runtime_restoration_timeout_seconds: float,
    inference_image_uri: str,
) -> Path:
    head = git_text(project_root, "rev-parse", "HEAD")
    upstream = git_text(project_root, "rev-parse", "@{u}")
    dirty = bool(git_text(project_root, "status", "--porcelain", "--", "."))
    if dirty or head != upstream or source_commit != head:
        raise RuntimeError(
            f"scenario_c_integrated_source_preflight_failed:dirty={dirty}:"
            f"head={head}:upstream={upstream}:requested={source_commit}"
        )
    if active_run_ids():
        raise RuntimeError(f"active_lifecycle_runs_present:{active_run_ids()}")
    scenario_index = read_json(scenario_root / "evidence-index.json")
    scenario_result = read_json(scenario_root / "report.json")
    event = read_json(scenario_root / "review-event.json")
    source_hash_validation = validate_source_evidence_index(
        scenario_root,
        scenario_index,
    )
    decision_seconds = float(
        scenario_result.get("timing", {}).get("detection_seconds") or 0
    )
    if (
        scenario_index.get("status") != "passed"
        or scenario_index.get("source_commit") != source_commit
        or event.get("decision") != "review_required"
        or decision_seconds <= 0
        or decision_seconds > 300
    ):
        raise RuntimeError("scenario_c_source_evidence_not_admissible")

    before_runtime = runtime_snapshot(inference_image_uri=inference_image_uri)
    if (
        before_runtime["supervisor"].get("status") != "healthy"
        or before_runtime["supervisor"].get("source_commit") != source_commit
        or before_runtime["production_inference"].get("device") != "cuda"
        or before_runtime["kubernetes"].get("ready_replicas") != 1
        or before_runtime["prometheus"].get("health") != "up"
    ):
        raise RuntimeError("scenario_c_integrated_runtime_preflight_failed")
    before_effects = external_side_effect_snapshot()
    started = utc_now()
    series_id = f"scenario-c-lifecycle-{started.strftime('%Y%m%dT%H%M%SZ')}-{head[:8]}"
    run_root = output_root / series_id
    run_root.mkdir(parents=True, exist_ok=False)
    write_json(run_root / "before-runtime.json", before_runtime)
    write_json(run_root / "before-side-effects.json", before_effects)
    write_json(run_root / "source-evidence-validation.json", source_hash_validation)
    timeline: list[dict[str, Any]] = []
    main_run: dict[str, Any] | None = None
    rejection_run: dict[str, Any] | None = None

    try:
        rejection_run = api_request(
            "POST",
            "/lifecycle-runs",
            {
                "profile_id": profile_id,
                "profile_version": profile_version,
                "actor": "scenario-c-integrated-requester",
                "reason": "Create isolated rejected governance branch",
                "dry_run": True,
                "execution_mode": "automatic",
            },
        )
        rejection_payload = registration_payload(
            run=rejection_run,
            scenario_root=scenario_root,
            observed_at=str(event["created_at"]),
        )
        rejection_run = api_request(
            "POST",
            f"/lifecycle-runs/{rejection_run['run_id']}/quality-review",
            rejection_payload,
        )
        rejection_review = read_json(quality_review_path(rejection_run))
        reject_payload = quality_action_payload(
            run=rejection_run,
            review=rejection_review,
            action="reject",
            actor="scenario-c-quality-owner",
            reason="Reject the exact candidate in an isolated governance branch",
        )
        rejection_run = api_request(
            "POST",
            f"/lifecycle-runs/{rejection_run['run_id']}/quality-review/action",
            reject_payload,
        )
        rejected_review = read_json(quality_review_path(rejection_run))
        write_json(
            run_root / "rejected-branch.json",
            {
                "run": rejection_run,
                "registration": rejection_payload,
                "action": reject_payload,
                "review": rejected_review,
            },
        )
        rejection_run = api_request(
            "POST",
            f"/lifecycle-runs/{rejection_run['run_id']}/cancel",
            {
                "actor": "scenario-c-integrated-requester",
                "reason": "Close isolated rejection branch without external execution",
                "expected_version": rejection_run["version"],
            },
        )

        main_run = api_request(
            "POST",
            "/lifecycle-runs",
            {
                "profile_id": profile_id,
                "profile_version": profile_version,
                "actor": "scenario-c-integrated-requester",
                "reason": "Validate real drift hold and governed training resume",
                "dry_run": True,
                "execution_mode": "automatic",
            },
        )
        write_json(run_root / "created-run.json", main_run)
        main_run, registration_attempts = register_with_replays(
            main_run,
            scenario_root=scenario_root,
            observed_at=str(event["created_at"]),
        )
        write_json(run_root / "registration-attempts.json", registration_attempts)
        review = read_json(quality_review_path(main_run))
        hold_payload = quality_action_payload(
            run=main_run,
            review=review,
            action="manual_hold",
            actor="scenario-c-quality-owner",
            reason="Hold exact candidate for labeling and governed training review",
        )
        main_run = api_request(
            "POST",
            f"/lifecycle-runs/{main_run['run_id']}/quality-review/action",
            hold_payload,
        )
        write_json(run_root / "manual-hold-action.json", hold_payload)
        approvals = issue_resume_handoff_approvals(
            main_run,
            run_root=run_root,
            ttl_seconds=handoff_approval_ttl_seconds,
        )
        main_run = api_request(
            "POST",
            f"/lifecycle-runs/{main_run['run_id']}/queue",
            {
                "actor": "scenario-c-integrated-requester",
                "reason": "Queue exact held lifecycle for pre-training guard proof",
                "expected_version": main_run["version"],
            },
        )
        write_json(run_root / "queued-run.json", main_run)
        held = wait_for_quality_hold(
            str(main_run["run_id"]),
            timeout_seconds=hold_timeout_seconds,
            timeline=timeline,
        )
        hold_review = read_json(quality_review_path(held))
        hold_effects = external_side_effect_snapshot()
        hold_runtime = runtime_snapshot(inference_image_uri=inference_image_uri)
        hold_delta = count_delta(before_effects, hold_effects)
        hold_checks = validate_hold_boundary(
            held,
            external_delta=hold_delta,
            task_count=len(tasks_for_run(str(held["run_id"]))),
            review=hold_review,
        )
        hold_checks["production_model_unchanged_during_hold"] = (
            hold_runtime["production_ready"].get("candidate_id")
            == before_runtime["production_ready"].get("candidate_id")
            and hold_runtime["production_ready"].get("model_sha256")
            == before_runtime["production_ready"].get("model_sha256")
            and hold_runtime["production_inference"].get("device") == "cuda"
            and hold_runtime["kubernetes"].get("deployment_uid")
            == before_runtime["kubernetes"].get("deployment_uid")
            and hold_runtime["kubernetes"].get("ready_replicas") == 1
            and hold_runtime["prometheus"].get("health") == "up"
        )
        write_json(
            run_root / "hold-boundary.json",
            {
                "run": held,
                "review": hold_review,
                "effects": hold_effects,
                "delta": hold_delta,
                "runtime": hold_runtime,
                "checks": hold_checks,
            },
        )
        if not all(hold_checks.values()):
            raise RuntimeError(f"scenario_c_hold_acceptance_failed:{hold_checks}")

        approve_payload = quality_action_payload(
            run=held,
            review=hold_review,
            action="approve_for_training",
            actor="scenario-c-training-approver",
            reason="Approve only the exact versioned training plan after quality review",
        )
        approved = api_request(
            "POST",
            f"/lifecycle-runs/{held['run_id']}/quality-review/action",
            approve_payload,
        )
        write_json(run_root / "approved-for-training-action.json", approve_payload)
        resumed = api_request(
            "POST",
            f"/lifecycle-runs/{approved['run_id']}/retry",
            {
                "actor": "scenario-c-training-approver",
                "reason": "Resume the held model training stage after exact approval",
                "expected_version": approved["version"],
            },
        )
        write_json(run_root / "resumed-run.json", resumed)
        boundary = wait_for_release_boundary(
            str(resumed["run_id"]),
            timeout_seconds=resume_timeout_seconds,
            timeline=timeline,
        )
        write_json(run_root / "release-boundary-run.json", boundary)
        final_review = read_json(quality_review_path(boundary))
        after_runtime, restoration = wait_for_runtime_restoration(
            before_runtime=before_runtime,
            source_commit=source_commit,
            inference_image_uri=inference_image_uri,
            timeout_seconds=runtime_restoration_timeout_seconds,
        )
        after_effects = external_side_effect_snapshot()
        delta = count_delta(before_effects, after_effects)
        consumed_handoffs = handoff_consumption(approvals)
        resume_checks = validate_resume_boundary(
            boundary,
            external_delta=delta,
            task_count=len(tasks_for_run(str(boundary["run_id"]))),
            review=final_review,
            handoffs=consumed_handoffs,
        )
        resume_checks["production_runtime_restored"] = restoration["status"] == "pass"
        resume_checks["exact_production_model_unchanged"] = (
            after_runtime["production_ready"].get("candidate_id")
            == before_runtime["production_ready"].get("candidate_id")
            and after_runtime["production_ready"].get("model_sha256")
            == before_runtime["production_ready"].get("model_sha256")
            and after_runtime["kubernetes"].get("deployment_uid")
            == before_runtime["kubernetes"].get("deployment_uid")
        )
        rejection_checks = {
            "rejected_state_audited": rejected_review.get("state") == "rejected",
            "rejected_branch_never_queued": all(
                stage.get("task_id") is None
                for stage in rejection_run.get("stages", [])
            ),
        }
        write_json(run_root / "timeline.json", timeline)
        write_json(run_root / "after-runtime.json", after_runtime)
        write_json(run_root / "runtime-restoration.json", restoration)
        write_json(run_root / "after-side-effects.json", after_effects)
        write_json(run_root / "gpu-handoff-consumption.json", consumed_handoffs)
        write_json(
            run_root / "governed-resume-boundary.json",
            {
                "run": boundary,
                "review": final_review,
                "delta": delta,
                "checks": resume_checks,
            },
        )
        checks = {**hold_checks, **resume_checks, **rejection_checks}
        result = {
            "schema_version": SCHEMA,
            "series_id": series_id,
            "run_id": boundary["run_id"],
            "rejection_run_id": rejection_run["run_id"],
            "source_commit": source_commit,
            "source_branch": source_branch,
            "scenario_c_source_run": scenario_index.get("run_id"),
            "scenario_c_decision_seconds": decision_seconds,
            "scenario_c_source_hashes": source_hash_validation,
            "event_id": final_review["event_id"],
            "candidate_id": final_review["candidate_id"],
            "registration_attempts": final_review["registration_attempts"],
            "external_delta": delta,
            "checks": checks,
            "status": "pass" if all(checks.values()) else "blocked",
            "finished_at": utc_now().isoformat(),
            "claim_boundary": (
                "Controlled local single-node VisA/CUDA lifecycle quality hold and "
                "governed training resume through MLflow and isolated CT. The run "
                "stops before release approval; no automatic deployment, production "
                "replacement, online drift, real-user traffic, HA, or SLA claim."
            ),
        }
        result_path = run_root / "result.json"
        result["evidence_index_uri"] = str(
            (run_root / "evidence-index.json").resolve()
        )
        write_json(result_path, result)
        build_evidence_index(run_root)
        if result["status"] != "pass":
            raise RuntimeError(f"scenario_c_integrated_acceptance_failed:{checks}")
        cleanup = safe_cancel(
            boundary,
            "Scenario C evidence captured at independent release boundary; close without deployment",
        )
        if cleanup is not None:
            write_json(run_root / "controlled-cleanup.json", cleanup)
            build_evidence_index(run_root)
        return result_path
    except Exception as exc:
        write_json(
            run_root / "failure.json",
            {
                "failed_at": utc_now().isoformat(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "main_run": main_run,
                "rejection_run": rejection_run,
            },
        )
        if main_run is not None:
            current = api_request(
                "GET",
                f"/lifecycle-runs/{main_run['run_id']}",
            )
            cleanup = safe_cancel(
                current,
                "Scenario C integrated proof failed; request bounded cleanup",
            )
            if cleanup is not None:
                write_json(run_root / "automatic-safety-cancel.json", cleanup)
        write_json(run_root / "timeline.json", timeline)
        build_evidence_index(run_root)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run integrated Scenario C lifecycle hold and governed resume proof."
    )
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--scenario-root", required=True, type=Path)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/"
            "lifecycle_guard_c"
        ),
    )
    parser.add_argument("--profile-id", default="standard-b0-manual-tuning")
    parser.add_argument("--profile-version", type=int, default=9)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-branch", required=True)
    parser.add_argument("--hold-timeout-seconds", type=float, default=1200)
    parser.add_argument("--resume-timeout-seconds", type=float, default=3600)
    parser.add_argument("--handoff-approval-ttl-seconds", type=int, default=7200)
    parser.add_argument(
        "--runtime-restoration-timeout-seconds",
        type=float,
        default=90,
    )
    parser.add_argument("--inference-image-uri", default=DEFAULT_INFERENCE_IMAGE_URI)
    args = parser.parse_args()
    result = run(
        project_root=args.project_root.resolve(),
        scenario_root=args.scenario_root.resolve(),
        output_root=args.output_root.resolve(),
        profile_id=args.profile_id,
        profile_version=args.profile_version,
        source_commit=args.source_commit,
        source_branch=args.source_branch,
        hold_timeout_seconds=args.hold_timeout_seconds,
        resume_timeout_seconds=args.resume_timeout_seconds,
        handoff_approval_ttl_seconds=args.handoff_approval_ttl_seconds,
        runtime_restoration_timeout_seconds=args.runtime_restoration_timeout_seconds,
        inference_image_uri=args.inference_image_uri,
    )
    print(json.dumps({"result_uri": str(result.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
