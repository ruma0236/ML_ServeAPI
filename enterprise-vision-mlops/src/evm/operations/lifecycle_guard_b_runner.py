from __future__ import annotations

import argparse
import json
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import requests

from evm.control_panel.lifecycle_gpu_handoff import issue_gpu_handoff_approval
from evm.control_panel.lifecycle_guards import file_digest
from evm.control_panel.lifecycle_runs import LifecycleRun
from evm.control_panel.readiness_evaluator import runtime_path
from evm.operations.lifecycle_guard_c_runner import count_delta, wait_for_release_boundary
from evm.operations.lifecycle_guard_d_training_live import (
    API_ROOT,
    active_run_ids,
    api_request,
    docker_desktop_kubectl,
    safe_cancel,
    wait_for_runtime_restoration,
)
from evm.operations.lifecycle_guard_e_runner import (
    DEFAULT_INFERENCE_IMAGE_URI,
    build_evidence_index,
    external_side_effect_snapshot,
    git_text,
    write_json,
)
from evm.operations.scenario_b_replay_runtime import (
    ReplayExecutionContext,
    execute_real_replay,
)
from evm.operations.scenario_d_live import runtime_snapshot


SCHEMA = "evm.lifecycle_guard_scenario_b_integrated.v1"
HANDOFF_PHASES = ("training", "isolated_ct")


def utc_now() -> datetime:
    return datetime.now(UTC)


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return payload


def save_guarded_profile(
    *,
    base_profile_id: str,
    base_profile_version: int,
) -> dict[str, Any]:
    base = api_request(
        "GET",
        f"/pipeline-profiles/{base_profile_id}?version={base_profile_version}",
    )
    profile = dict(base["profile"])
    profile["profile_name"] = "scenario-b-controlled-replay-b0"
    profile["description"] = (
        "Real VisA/CUDA lifecycle profile with an immutable Scenario B controlled "
        "replay release guard."
    )
    gates = dict(profile["gates"])
    gates["require_controlled_replay"] = True
    gates["approval_policy"] = "two_person"
    gates["target_environment"] = "staging"
    gates["target_namespace"] = "evm-staging"
    profile["gates"] = gates
    saved = api_request("POST", "/pipeline-profiles", profile)
    validation = saved.get("validation") or {}
    if validation.get("executable") is not True:
        raise RuntimeError(
            f"scenario_b_guarded_profile_not_executable:{validation.get('blockers')}"
        )
    if saved.get("profile", {}).get("gates", {}).get("require_controlled_replay") is not True:
        raise RuntimeError("scenario_b_controlled_replay_policy_not_persisted")
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
            approver="scenario-b-resource-approver",
            reason=(
                "Pre-authorize only the bounded single-GPU training and isolated CT "
                "handoffs for integrated Scenario B validation"
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


def lifecycle_binding(run: dict[str, Any], submission: dict[str, Any]) -> dict[str, Any]:
    ct_path = runtime_path(str(run.get("ct_evaluation_uri") or ""))
    if not ct_path.is_file():
        raise RuntimeError("scenario_b_lifecycle_ct_evaluation_missing")
    binding = {
        "schema_version": "evm.lifecycle_release_binding.v1",
        "lifecycle_run_id": run.get("run_id"),
        "lifecycle_series_id": run.get("lifecycle_series_id"),
        "attempt_id": run.get("attempt_id"),
        "correlation_id": run.get("correlation_id"),
        "profile_digest": run.get("profile_digest"),
        "effective_config_digest": run.get("effective_config_digest"),
        "source_commit": run.get("source_commit"),
        "candidate_id": submission.get("candidate_id"),
        "model_digest": submission.get("model_digest"),
        "ct_evaluation_id": submission.get("ct_evaluation_id"),
        "ct_evaluation_sha256": file_digest(ct_path),
        "release_submission_digest": submission.get("submission_digest"),
    }
    if any(value in {None, ""} for value in binding.values()):
        raise RuntimeError("scenario_b_lifecycle_binding_incomplete")
    return binding


def ensure_replay_runtime_ready(
    *,
    before_runtime: dict[str, Any],
    source_commit: str,
    inference_image_uri: str,
    timeout_seconds: float,
    waiter: Any = wait_for_runtime_restoration,
) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime, restoration = waiter(
        before_runtime=before_runtime,
        source_commit=source_commit,
        inference_image_uri=inference_image_uri,
        timeout_seconds=timeout_seconds,
    )
    if restoration.get("status") != "pass":
        raise RuntimeError("scenario_b_pre_replay_runtime_not_restored")
    return runtime, restoration


def replay_inputs(run: dict[str, Any]) -> tuple[dict[str, Any], Path, Path]:
    submission_path = runtime_path(str(run.get("release_submission_uri") or ""))
    submission = read_json(submission_path)
    model_path = runtime_path(str(submission.get("model_artifact_uri") or ""))
    candidate_summary_path = model_path.parent / "candidate_summary.json"
    if not model_path.is_file() or not candidate_summary_path.is_file():
        raise RuntimeError("scenario_b_lifecycle_candidate_artifacts_missing")
    if file_digest(model_path) != submission.get("model_digest"):
        raise RuntimeError("scenario_b_lifecycle_candidate_digest_mismatch")
    return submission, candidate_summary_path, model_path


def replay_manifest(config_path: Path) -> Path:
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)
    path = Path(str(payload["replay"]["manifest_path"]))
    expected = str(payload["replay"]["manifest_sha256"])
    if not path.is_file() or file_digest(path) != expected:
        raise RuntimeError("scenario_b_replay_manifest_identity_mismatch")
    return path


def replay_context(
    runtime: dict[str, Any],
    *,
    source_commit: str,
    source_branch: str,
) -> ReplayExecutionContext:
    supervisor = runtime["supervisor"]
    children = {
        str(item.get("name")): item
        for item in supervisor.get("children", [])
        if isinstance(item, dict)
    }
    kubernetes = runtime["kubernetes"]
    return ReplayExecutionContext(
        source_commit=source_commit,
        source_branch=source_branch,
        source_dirty=False,
        api_revision=source_commit,
        worker_revision=str(children["lifecycle_worker"]["source_commit"]),
        observer_revision=str(children["kubernetes_observer"]["source_commit"]),
        cluster_context="docker-desktop",
        node="docker-desktop",
        target_namespace="evm-production",
        target_name="evm-b0-production",
        target_uid=str(kubernetes["deployment_uid"]),
        actor="scenario-b-lifecycle-guard",
    )


def approval_denial(
    run: dict[str, Any],
    submission: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "actor": "scenario-b-release-approver",
        "approver": "scenario-b-release-approver",
        "reason": "Attempt exact release to prove the registered B guard fails closed",
        "candidate_id": submission["candidate_id"],
        "model_digest": submission["model_digest"],
        "ct_evaluation_id": submission["ct_evaluation_id"],
        "expected_version": run["version"],
    }
    response = requests.post(
        f"{API_ROOT}/lifecycle-runs/{run['run_id']}/approve",
        json=payload,
        timeout=60,
    )
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:2000]}
    if response.status_code != 422:
        raise RuntimeError(
            f"scenario_b_release_guard_did_not_fail_closed:"
            f"status={response.status_code}:body={body}"
        )
    detail = body.get("detail") if isinstance(body, dict) else {}
    if not isinstance(detail, dict) or detail.get("error") != "release_guard_release_blocked":
        raise RuntimeError(f"scenario_b_unexpected_approval_denial:{body}")
    return {"request": payload, "status_code": response.status_code, "response": body}


def run_branch(
    *,
    branch: Literal["quality", "runtime"],
    profile: dict[str, Any],
    config_path: Path,
    output_root: Path,
    series_root: Path,
    before_runtime: dict[str, Any],
    source_commit: str,
    source_branch: str,
    lifecycle_timeout_seconds: float,
    handoff_approval_ttl_seconds: int,
    inference_image_uri: str,
) -> dict[str, Any]:
    expected_state = "blocked_admission" if branch == "quality" else "rolled_back"
    expected_guard_state = "rejected_release" if branch == "quality" else "rolled_back"
    expected_blocker = (
        "quality_f1_below_minimum"
        if branch == "quality"
        else "runtime_error_rate_exceeded"
    )
    inject_error_count = 0 if branch == "quality" else 2
    branch_root = series_root / branch
    branch_root.mkdir(parents=True, exist_ok=False)
    before_effects = external_side_effect_snapshot()
    write_json(branch_root / "before-side-effects.json", before_effects)
    timeline: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    try:
        current = api_request(
            "POST",
            "/lifecycle-runs",
            {
                "profile_id": profile["profile_id"],
                "profile_version": profile["version"],
                "actor": "scenario-b-lifecycle-requester",
                "reason": f"Validate integrated Scenario B {branch} release guard",
                "dry_run": True,
                "execution_mode": "automatic",
            },
        )
        if current.get("release_guard_required") is not True:
            raise RuntimeError("scenario_b_lifecycle_release_guard_not_required")
        write_json(branch_root / "created-run.json", current)
        issue_handoff_approvals(
            current,
            evidence_root=branch_root,
            ttl_seconds=handoff_approval_ttl_seconds,
        )
        current = api_request(
            "POST",
            f"/lifecycle-runs/{current['run_id']}/queue",
            {
                "actor": "scenario-b-lifecycle-requester",
                "reason": f"Queue integrated Scenario B {branch} lifecycle",
                "expected_version": current["version"],
            },
        )
        current = wait_for_release_boundary(
            str(current["run_id"]),
            timeout_seconds=lifecycle_timeout_seconds,
            timeline=timeline,
        )
        write_json(branch_root / "release-boundary-run.json", current)
        pre_replay_runtime, pre_replay_restoration = ensure_replay_runtime_ready(
            before_runtime=before_runtime,
            source_commit=source_commit,
            inference_image_uri=inference_image_uri,
            timeout_seconds=90,
        )
        write_json(branch_root / "pre-replay-runtime.json", pre_replay_runtime)
        write_json(
            branch_root / "pre-replay-runtime-restoration.json",
            pre_replay_restoration,
        )
        submission, candidate_summary_path, model_path = replay_inputs(current)
        binding = lifecycle_binding(current, submission)
        replay_id = (
            f"scenario-b-lifecycle-{branch}-{utc_now().strftime('%Y%m%dT%H%M%SZ')}-"
            f"{str(submission['model_digest'])[:8]}"
        )
        result, index_path = execute_real_replay(
            run_id=replay_id,
            config_path=config_path,
            candidate_summary_path=candidate_summary_path,
            model_path=model_path,
            manifest_path=replay_manifest(config_path),
            evidence_root=output_root,
            stable_readiness_url="http://127.0.0.1:30800/ready",
            stable_predict_url="http://127.0.0.1:30800/predict",
            prometheus_targets_url="http://127.0.0.1:9090/api/v1/targets",
            prometheus_job="evm-b0-production",
            prometheus_instance="host.docker.internal:30800",
            warmup_requests=10,
            inject_error_count=inject_error_count,
            expected_state=expected_state,
            expected_blocker=expected_blocker,
            execution_context=replay_context(
                pre_replay_runtime,
                source_commit=source_commit,
                source_branch=source_branch,
            ),
            lifecycle_binding=binding,
        )
        write_json(branch_root / "replay-reference.json", {"evidence_index": str(index_path)})
        current = api_request(
            "POST",
            f"/lifecycle-runs/{current['run_id']}/release-guard",
            {
                "actor": "scenario-b-release-controller",
                "reason": f"Bind exact integrated Scenario B {branch} replay decision",
                "expected_version": current["version"],
                "evidence_index_uri": str(index_path.resolve()),
                "evidence_index_sha256": file_digest(index_path),
            },
        )
        write_json(branch_root / "guarded-run.json", current)
        denial = approval_denial(current, submission)
        write_json(branch_root / "approval-denial.json", denial)
        current = api_request("GET", f"/lifecycle-runs/{current['run_id']}")
        after_effects = external_side_effect_snapshot()
        delta = count_delta(before_effects, after_effects)
        after_runtime, restoration = wait_for_runtime_restoration(
            before_runtime=before_runtime,
            source_commit=source_commit,
            inference_image_uri=inference_image_uri,
            timeout_seconds=90,
        )
        metric_window = result.metric_window
        checks = {
            "full_lifecycle_reached_release_boundary": (
                current.get("state") == "waiting_approval"
                and current.get("current_stage") == "approval"
            ),
            "release_guard_policy_immutable": current.get("release_guard_required") is True,
            "release_guard_decision_exact": current.get("release_guard_state")
            == expected_guard_state,
            "replay_decision_exact": result.decision.state == expected_state,
            "blocker_exact": result.decision.blocker_codes == [expected_blocker],
            "approval_failed_closed": denial["status_code"] == 422,
            "deployment_intent_zero": current.get("deployment_intent_id") is None
            and delta["deployment_intents"] == 0,
            "production_not_mutated": result.production_mutated is False,
            "stable_identity_restored": result.rollback.exact_identity_restored
            and result.rollback.restored_model_digest == result.stable.model_digest,
            "detection_within_30_seconds": float(result.decision.stop_seconds or 0) <= 30,
            "recovery_within_300_seconds": result.rollback.duration_seconds <= 300,
            "production_runtime_restored": restoration["status"] == "pass",
            "exact_production_uid_retained": (
                after_runtime["kubernetes"].get("deployment_uid")
                == before_runtime["kubernetes"].get("deployment_uid")
            ),
            "cuda_candidate_evidence": (
                read_json(output_root / replay_id / "runtime.json")
                .get("cuda", {})
                .get("device")
                == "cuda"
            ),
            "quality_branch_zero_assignment": (
                branch != "quality" or len(result.assignment_ledger) == 0
            ),
            "runtime_branch_exact_10_percent": (
                branch != "runtime"
                or (
                    metric_window is not None
                    and metric_window.total_requests == 1000
                    and metric_window.challenger_requests == 100
                    and metric_window.challenger_error_count == 2
                    and metric_window.challenger_fraction == 0.1
                    and result.decision.challenger_allocation_after == 0
                )
            ),
        }
        branch_result = {
            "schema_version": SCHEMA,
            "branch": branch,
            "lifecycle_run_id": current["run_id"],
            "replay_run_id": replay_id,
            "source_commit": source_commit,
            "candidate_id": submission["candidate_id"],
            "model_digest": submission["model_digest"],
            "ct_evaluation_id": submission["ct_evaluation_id"],
            "replay_evidence_index": str(index_path.resolve()),
            "release_guard_id": current.get("release_guard_id"),
            "release_guard_state": current.get("release_guard_state"),
            "decision": result.decision.model_dump(mode="json"),
            "rollback": result.rollback.model_dump(mode="json"),
            "metric_window": metric_window.model_dump(mode="json") if metric_window else None,
            "external_delta": delta,
            "checks": checks,
            "status": "pass" if all(checks.values()) else "blocked",
        }
        write_json(branch_root / "result.json", branch_result)
        write_json(branch_root / "timeline.json", timeline)
        write_json(branch_root / "after-side-effects.json", after_effects)
        write_json(branch_root / "runtime-restoration.json", restoration)
        if branch_result["status"] != "pass":
            raise RuntimeError(f"scenario_b_{branch}_acceptance_failed:{checks}")
        cleanup = safe_cancel(
            current,
            f"Scenario B {branch} guard evidence captured; close without deployment",
        )
        if cleanup is not None:
            current = cleanup
            write_json(branch_root / "controlled-cleanup.json", cleanup)
        build_evidence_index(branch_root)
        return branch_result
    except Exception as exc:
        write_json(
            branch_root / "failure.json",
            {
                "failed_at": utc_now().isoformat(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "current_run": current,
            },
        )
        if current is not None:
            latest = api_request("GET", f"/lifecycle-runs/{current['run_id']}")
            cleanup = safe_cancel(
                latest,
                f"Scenario B {branch} integrated proof failed; bounded cleanup",
            )
            if cleanup is not None:
                write_json(branch_root / "automatic-safety-cancel.json", cleanup)
        write_json(branch_root / "timeline.json", timeline)
        build_evidence_index(branch_root)
        raise


def run(
    *,
    project_root: Path,
    output_root: Path,
    base_profile_id: str,
    base_profile_version: int,
    quality_config: Path,
    runtime_config: Path,
    source_commit: str,
    source_branch: str,
    lifecycle_timeout_seconds: float,
    handoff_approval_ttl_seconds: int,
    inference_image_uri: str,
) -> Path:
    head = git_text(project_root, "rev-parse", "HEAD")
    upstream = git_text(project_root, "rev-parse", "@{upstream}")
    dirty = bool(git_text(project_root, "status", "--porcelain", "--", "."))
    if dirty or head != upstream or source_commit != head:
        raise RuntimeError(
            f"scenario_b_integrated_source_preflight_failed:dirty={dirty}:"
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
        raise RuntimeError("scenario_b_integrated_runtime_preflight_failed")
    for path in (quality_config, runtime_config):
        replay_manifest(path)
    profile = save_guarded_profile(
        base_profile_id=base_profile_id,
        base_profile_version=base_profile_version,
    )
    started = utc_now()
    series_id = f"scenario-b-lifecycle-{started.strftime('%Y%m%dT%H%M%SZ')}-{head[:8]}"
    series_root = output_root / "_series" / series_id
    series_root.mkdir(parents=True, exist_ok=False)
    write_json(series_root / "before-runtime.json", before_runtime)
    write_json(series_root / "guarded-profile.json", profile)
    results: list[dict[str, Any]] = []
    for branch, config in (("quality", quality_config), ("runtime", runtime_config)):
        results.append(
            run_branch(
                branch=branch,
                profile=profile,
                config_path=config,
                output_root=output_root,
                series_root=series_root,
                before_runtime=before_runtime,
                source_commit=source_commit,
                source_branch=source_branch,
                lifecycle_timeout_seconds=lifecycle_timeout_seconds,
                handoff_approval_ttl_seconds=handoff_approval_ttl_seconds,
                inference_image_uri=inference_image_uri,
            )
        )
        time.sleep(5)
    after_runtime, restoration = wait_for_runtime_restoration(
        before_runtime=before_runtime,
        source_commit=source_commit,
        inference_image_uri=inference_image_uri,
        timeout_seconds=90,
    )
    checks = {
        "quality_branch_passed": results[0]["status"] == "pass",
        "runtime_branch_passed": results[1]["status"] == "pass",
        "both_release_intents_zero": all(
            item["external_delta"]["deployment_intents"] == 0 for item in results
        ),
        "runtime_restored": restoration["status"] == "pass",
        "production_uid_unchanged": (
            after_runtime["kubernetes"].get("deployment_uid")
            == before_runtime["kubernetes"].get("deployment_uid")
        ),
        "production_model_unchanged": (
            after_runtime["production_ready"].get("model_sha256")
            == before_runtime["production_ready"].get("model_sha256")
        ),
    }
    result = {
        "schema_version": SCHEMA,
        "series_id": series_id,
        "source_commit": source_commit,
        "source_branch": source_branch,
        "profile_id": profile["profile_id"],
        "profile_version": profile["version"],
        "branches": results,
        "checks": checks,
        "status": "pass" if all(checks.values()) else "blocked",
        "finished_at": utc_now().isoformat(),
        "claim_boundary": (
            "Controlled local single-node VisA/CUDA lifecycle validation. Two fresh "
            "runs reached independent release approval after real Airflow, Kubernetes "
            "training, MLflow, readiness, and isolated CT. One measured quality breach "
            "was rejected and one deterministic replay error breach rolled back to zero "
            "challenger allocation. No real-user traffic, production mutation, business "
            "A/B, HA, or enterprise SLA claim."
        ),
    }
    result_path = series_root / "result.json"
    write_json(series_root / "after-runtime.json", after_runtime)
    write_json(series_root / "runtime-restoration.json", restoration)
    write_json(result_path, result)
    build_evidence_index(series_root)
    if result["status"] != "pass":
        raise RuntimeError(f"scenario_b_integrated_acceptance_failed:{checks}")
    return result_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run integrated Scenario B lifecycle release guard proof."
    )
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/"
            "lifecycle_guard_b"
        ),
    )
    parser.add_argument("--base-profile-id", default="standard-b0-manual-tuning")
    parser.add_argument("--base-profile-version", type=int, default=9)
    parser.add_argument("--quality-config", required=True, type=Path)
    parser.add_argument("--runtime-config", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-branch", required=True)
    parser.add_argument("--lifecycle-timeout-seconds", type=float, default=3600)
    parser.add_argument("--handoff-approval-ttl-seconds", type=int, default=7200)
    parser.add_argument("--inference-image-uri", default=DEFAULT_INFERENCE_IMAGE_URI)
    args = parser.parse_args()
    result = run(
        project_root=args.project_root.resolve(),
        output_root=args.output_root.resolve(),
        base_profile_id=args.base_profile_id,
        base_profile_version=args.base_profile_version,
        quality_config=args.quality_config.resolve(),
        runtime_config=args.runtime_config.resolve(),
        source_commit=args.source_commit,
        source_branch=args.source_branch,
        lifecycle_timeout_seconds=args.lifecycle_timeout_seconds,
        handoff_approval_ttl_seconds=args.handoff_approval_ttl_seconds,
        inference_image_uri=args.inference_image_uri,
    )
    print(json.dumps({"result_uri": str(result.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
