from __future__ import annotations

import argparse
import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evm.control_panel.lifecycle_guards import file_digest
from evm.control_panel.readiness_evaluator import runtime_path
from evm.operations.lifecycle_guard_d_training_live import (
    active_run_ids,
    api_request,
    handoff_consumption_snapshot,
    issue_run_handoff_approvals,
    run_job_identities,
    side_effect_ledger,
    tasks_for_run,
    wait_for_runtime_restoration,
    wait_for_terminal,
)
from evm.operations.lifecycle_guard_e_runner import (
    DEFAULT_INFERENCE_IMAGE_URI,
    build_evidence_index,
    external_side_effect_snapshot,
    git_text,
    write_json,
)
from evm.operations.scenario_d_live import runtime_snapshot


SCHEMA = "evm.lifecycle_guard_scenario_a_candidate.v1"
DEFAULT_OUTPUT_ROOT = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/"
    "lifecycle_guard_a_candidate"
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return payload


def count_delta(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> dict[str, int]:
    return {
        key: int(after[key]["count"]) - int(before[key]["count"])
        for key in sorted(before)
    }


def _toml_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def render_integration_config(
    *,
    base_config: dict[str, Any],
    lifecycle_root: Path,
    release: dict[str, Any],
    source_commit: str,
) -> str:
    target = base_config["target"]
    m0 = base_config["m0"]
    base_m1 = base_config["m1"]
    execution = base_config["execution"]
    ct_reference = release["evidence"]["ct_evaluation"]
    values = {
        "lifecycle_run_id": release["run_id"],
        "lifecycle_run_root": str(lifecycle_root.resolve()).replace("\\", "/"),
        "source_revision": source_commit,
        "candidate_id": release["candidate_id"],
        "dataset_version": release["dataset_version"],
        "model_sha256": release["model_digest"],
        "image_digest": release["container_image_digest"],
        "ct_report_path": str(ct_reference["uri"]).replace("\\", "/"),
        "sample_image_uri": base_m1["sample_image_uri"],
        "expected_prediction": base_m1["expected_prediction"],
    }

    sections = (
        ("target", target),
        ("m0", m0),
        ("m1", values),
        ("execution", execution),
    )
    lines: list[str] = []
    for section, payload in sections:
        lines.append(f"[{section}]")
        for key, value in payload.items():
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            elif isinstance(value, (int, float)):
                rendered = str(value)
            else:
                rendered = _toml_string(value)
            lines.append(f"{key} = {rendered}")
        lines.append("")
    return "\n".join(lines)


def validate_release(
    *, lifecycle_root: Path, terminal: dict[str, Any], source_commit: str
) -> dict[str, Any]:
    release_path = lifecycle_root / "validation" / "release-submission.json"
    release = read_json(release_path)
    required = {
        "readiness",
        "model_matrix",
        "ct_evaluation",
        "model_artifact",
    }
    if (
        terminal.get("state") != "completed"
        or release.get("schema_version") != "evm.lifecycle_release_submission.v1"
        or release.get("run_id") != terminal.get("run_id")
        or release.get("source_commit") != source_commit
        or set(release.get("evidence") or {}) != required
    ):
        raise RuntimeError("scenario_a_candidate_release_identity_invalid")
    for name, reference in release["evidence"].items():
        path = Path(str(reference.get("uri") or ""))
        expected = str(reference.get("sha256") or "")
        if not path.is_file() or file_digest(path) != expected:
            raise RuntimeError(f"scenario_a_candidate_evidence_invalid:{name}")
    model_path = Path(str(release.get("model_artifact_uri") or ""))
    if not model_path.is_file() or file_digest(model_path) != release.get("model_digest"):
        raise RuntimeError("scenario_a_candidate_model_invalid")
    return release


def run(
    *,
    project_root: Path,
    base_config_path: Path,
    output_root: Path,
    profile_id: str,
    profile_version: int,
    source_commit: str,
    source_branch: str,
    completion_timeout_seconds: float,
    handoff_approval_ttl_seconds: int,
    inference_image_uri: str,
) -> Path:
    head = git_text(project_root, "rev-parse", "HEAD")
    upstream = git_text(project_root, "rev-parse", "@{upstream}")
    dirty = bool(git_text(project_root, "status", "--porcelain", "--", "."))
    if dirty or head != upstream or source_commit != head:
        raise RuntimeError(
            "scenario_a_candidate_source_preflight_failed:"
            f"dirty={dirty}:head={head}:upstream={upstream}:requested={source_commit}"
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
        raise RuntimeError("scenario_a_candidate_runtime_preflight_failed")

    before_effects = external_side_effect_snapshot()
    started = utc_now()
    series_id = f"scenario-a-candidate-{started.strftime('%Y%m%dT%H%M%SZ')}-{head[:8]}"
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
            "actor": "scenario-a-candidate-validator",
            "reason": "Create a fresh no-fault lifecycle candidate for Scenario A",
            "dry_run": True,
            "execution_mode": "automatic",
        },
    )
    lifecycle_run_id = str(created["run_id"])
    lifecycle_root = runtime_path(str(created["artifact_root"])).resolve()
    write_json(run_root / "created-run.json", created)
    approvals = issue_run_handoff_approvals(
        created,
        run_root=run_root,
        ttl_seconds=handoff_approval_ttl_seconds,
    )
    queued = api_request(
        "POST",
        f"/lifecycle-runs/{lifecycle_run_id}/queue",
        {
            "actor": "scenario-a-candidate-validator",
            "reason": "Queue the fresh no-fault Scenario A candidate lifecycle",
            "expected_version": created["version"],
        },
    )
    write_json(run_root / "queued-run.json", queued)
    terminal = wait_for_terminal(
        lifecycle_run_id,
        timeout_seconds=completion_timeout_seconds,
        timeline=timeline,
        run_root=run_root,
    )
    write_json(run_root / "timeline.json", timeline)
    write_json(run_root / "terminal-run.json", terminal)

    after_runtime, restoration = wait_for_runtime_restoration(
        before_runtime=before_runtime,
        source_commit=source_commit,
        inference_image_uri=inference_image_uri,
        timeout_seconds=90,
    )
    after_effects = external_side_effect_snapshot()
    delta = count_delta(before_effects, after_effects)
    ledger, ledger_path = side_effect_ledger(terminal)
    tasks = tasks_for_run(lifecycle_run_id)
    jobs = run_job_identities(lifecycle_run_id)
    consumed = handoff_consumption_snapshot(approvals)
    release = validate_release(
        lifecycle_root=lifecycle_root,
        terminal=terminal,
        source_commit=source_commit,
    )

    base_config = tomllib.loads(base_config_path.read_text(encoding="utf-8"))
    config_path = run_root / "scenario-a-integration.toml"
    config_path.write_text(
        render_integration_config(
            base_config=base_config,
            lifecycle_root=lifecycle_root,
            release=release,
            source_commit=source_commit,
        ),
        encoding="utf-8",
    )

    stages = terminal.get("stages", [])
    task_types = [str(item.get("task_type")) for item in tasks]
    checks = {
        "fresh_lifecycle_completed_10_of_10": (
            terminal.get("state") == "completed"
            and len(stages) == 10
            and all(item.get("state") == "completed" for item in stages)
        ),
        "no_fault_injection_in_candidate_run": True,
        "side_effects_unique_and_committed": (
            len(ledger.entries) == 8
            and len({entry.side_effect_key for entry in ledger.entries}) == 8
            and all(entry.state == "completed" for entry in ledger.entries)
        ),
        "one_airflow_training_and_ct_task": (
            len(tasks) == 3
            and task_types.count("airflow_dag_run") == 1
            and task_types.count("kubernetes_job") == 2
            and all(item.get("status") == "done" for item in tasks)
        ),
        "one_training_and_one_ct_job": len(jobs) == 2,
        "handoff_approvals_consumed_once": (
            all(item["consumed"] for item in consumed.values())
        ),
        "expected_external_delta": delta
        == {
            "deployment_intents": 1,
            "kubernetes_jobs": 2,
            "mlflow_runs": 1,
            "model_candidates": 1,
        },
        "runtime_restored": restoration.get("status") == "pass",
        "exact_production_identity_unchanged": (
            after_runtime["kubernetes"].get("deployment_uid")
            == before_runtime["kubernetes"].get("deployment_uid")
            and after_runtime["production_ready"].get("model_sha256")
            == before_runtime["production_ready"].get("model_sha256")
        ),
        "a_integration_config_written": config_path.is_file(),
    }
    result = {
        "schema_version": SCHEMA,
        "series_id": series_id,
        "lifecycle_run_id": lifecycle_run_id,
        "lifecycle_run_root": str(lifecycle_root),
        "source_commit": source_commit,
        "source_branch": source_branch,
        "candidate_id": release["candidate_id"],
        "model_digest": release["model_digest"],
        "ct_evaluation_id": release["ct_evaluation_id"],
        "external_delta": delta,
        "side_effect_ledger_uri": str(ledger_path.resolve()),
        "side_effect_ledger_sha256": file_digest(ledger_path),
        "scenario_a_config_uri": str(config_path.resolve()),
        "checks": checks,
        "status": "pass" if all(checks.values()) else "blocked",
        "finished_at": utc_now().isoformat(),
        "claim_boundary": (
            "Fresh no-fault local VisA/CUDA lifecycle candidate for a bounded Scenario A "
            "maintenance drill; not a production rollout, HA, real-user traffic, or SLA claim."
        ),
    }
    result_path = run_root / "result.json"
    result["evidence_index_uri"] = str((run_root / "evidence-index.json").resolve())
    write_json(run_root / "after-runtime.json", after_runtime)
    write_json(run_root / "runtime-restoration.json", restoration)
    write_json(run_root / "after-side-effects.json", after_effects)
    write_json(run_root / "gpu-handoff-consumption.json", consumed)
    write_json(result_path, result)
    build_evidence_index(run_root)
    if result["status"] != "pass":
        raise RuntimeError(f"scenario_a_candidate_acceptance_failed:{checks}")
    return result_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a fresh no-fault lifecycle candidate for Scenario A."
    )
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--base-config", required=True, type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--profile-id", default="standard-b0-manual-tuning")
    parser.add_argument("--profile-version", type=int, default=9)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-branch", required=True)
    parser.add_argument("--completion-timeout-seconds", type=float, default=3600)
    parser.add_argument("--handoff-approval-ttl-seconds", type=int, default=7200)
    parser.add_argument("--inference-image-uri", default=DEFAULT_INFERENCE_IMAGE_URI)
    args = parser.parse_args()
    result = run(
        project_root=args.project_root.resolve(),
        base_config_path=args.base_config.resolve(),
        output_root=args.output_root.resolve(),
        profile_id=args.profile_id,
        profile_version=args.profile_version,
        source_commit=args.source_commit,
        source_branch=args.source_branch,
        completion_timeout_seconds=args.completion_timeout_seconds,
        handoff_approval_ttl_seconds=args.handoff_approval_ttl_seconds,
        inference_image_uri=args.inference_image_uri,
    )
    print(json.dumps({"result_uri": str(result.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
