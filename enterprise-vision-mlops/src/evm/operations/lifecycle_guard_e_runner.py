from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from evm.control_panel.lifecycle_guards import canonical_digest, file_digest
from evm.control_panel.lifecycle_integrity import (
    LifecycleIntegrityBlocked,
    build_lifecycle_release_submission,
    validate_lifecycle_data_integrity,
    validate_lifecycle_release_submission,
)
from evm.control_panel.readiness_evaluator import runtime_path
from evm.operations.scenario_e_runner import (
    DEFAULT_INFERENCE_IMAGE_URI,
    command_json,
    production_snapshot,
)


SCHEMA = "evm.lifecycle_guard_scenario_e_replay.v1"
DEFAULT_OUTPUT_ROOT = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/operations/"
    "lifecycle_guard_validation"
)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected_json_object:{path}")
    return payload


def git_text(project_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def materialize_data_branch(golden_root: Path, branch_root: Path) -> Path:
    source = golden_root / "data" / "quality" / "quality_manifest.jsonl"
    source_target = branch_root / "data" / "quality" / source.name
    source_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, source_target)
    shard_target = branch_root / "data" / "shards"
    shutil.copytree(golden_root / "data" / "shards", shard_target)
    index_path = shard_target / "shard_index.json"
    index = read_json(index_path)
    index["input_manifest"] = str(source_target.resolve())
    index["output_dir"] = str(shard_target.resolve())
    for shard in index.get("shards", []):
        if isinstance(shard, dict):
            shard["path"] = str((shard_target / Path(str(shard["path"])).name).resolve())
    write_json(index_path, index)
    return index_path


def inject_split_leakage(index_path: Path) -> None:
    index = read_json(index_path)
    train = next(item for item in index["shards"] if item["split"] == "train")
    validation = next(
        item for item in index["shards"] if item["split"] == "validation"
    )
    train_path = Path(train["path"])
    validation_path = Path(validation["path"])
    train_record = json.loads(train_path.read_text(encoding="utf-8").splitlines()[0])
    validation_lines = validation_path.read_text(encoding="utf-8").splitlines()
    target_index = min(10, len(validation_lines) - 2)
    if target_index <= 0:
        raise RuntimeError("split_leakage_fixture_requires_non_boundary_record")
    duplicate = dict(train_record)
    duplicate["split"] = "validation"
    validation_lines[target_index] = json.dumps(duplicate, ensure_ascii=False)
    validation_path.write_text("\n".join(validation_lines) + "\n", encoding="utf-8")


def replay_data(branch_root: Path, replay_count: int = 3) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for replay in range(1, replay_count + 1):
        started = time.monotonic()
        try:
            report_path = validate_lifecycle_data_integrity(branch_root)
            report = read_json(report_path)
        except LifecycleIntegrityBlocked:
            report = read_json(branch_root / "data" / "integrity-validation.json")
        result = {
            "replay": replay,
            "decision": report["decision"],
            "blockers": report["blockers"],
            "decision_fingerprint": report["decision_fingerprint"],
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "validator_report": report,
        }
        write_json(branch_root / "replays" / f"replay-{replay}.json", result)
        results.append(result)
    return results


def replay_release(
    submission_path: Path,
    *,
    run_id: str,
    source_commit: str,
    replay_root: Path,
    replay_count: int = 3,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for replay in range(1, replay_count + 1):
        started = time.monotonic()
        try:
            decision = validate_lifecycle_release_submission(
                submission_path,
                run_id=run_id,
                source_commit=source_commit,
            )
        except LifecycleIntegrityBlocked as exc:
            decision = {
                "schema_version": "evm.lifecycle_release_integrity_decision.v1",
                "run_id": run_id,
                "decision": "blocked",
                "blockers": exc.blockers,
                "decision_fingerprint": exc.decision_fingerprint,
            }
        result = {
            **decision,
            "replay": replay,
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }
        write_json(replay_root / f"replay-{replay}.json", result)
        results.append(result)
    return results


def mlflow_run_ids() -> list[str]:
    base = "http://127.0.0.1:5000/api/2.0/mlflow"
    experiments = requests.get(
        f"{base}/experiments/search",
        params={"max_results": 1000},
        timeout=30,
    )
    experiments.raise_for_status()
    experiment_ids = [
        str(item["experiment_id"])
        for item in experiments.json().get("experiments", [])
    ]
    runs = requests.post(
        f"{base}/runs/search",
        json={"experiment_ids": experiment_ids, "max_results": 5000},
        timeout=60,
    )
    runs.raise_for_status()
    if runs.json().get("next_page_token"):
        raise RuntimeError("mlflow_run_snapshot_incomplete")
    return sorted(str(item["info"]["run_id"]) for item in runs.json().get("runs", []))


def api_collection(path: str, key: str, identity: str) -> list[str]:
    response = requests.get(f"http://127.0.0.1:8000{path}", timeout=30)
    response.raise_for_status()
    return sorted(str(item.get(identity) or "") for item in response.json().get(key, []))


def external_side_effect_snapshot() -> dict[str, Any]:
    jobs = command_json(["kubectl", "get", "jobs", "-A", "-o", "json"])
    job_ids = sorted(
        f"{item['metadata']['namespace']}/{item['metadata']['name']}/{item['metadata']['uid']}"
        for item in jobs.get("items", [])
    )
    collections = {
        "kubernetes_jobs": job_ids,
        "mlflow_runs": mlflow_run_ids(),
        "model_candidates": api_collection(
            "/control-panel/v1/model-candidates",
            "candidates",
            "candidate_key",
        ),
        "deployment_intents": api_collection(
            "/control-panel/v1/deployment-intents",
            "intents",
            "intent_id",
        ),
    }
    return {
        role: {
            "count": len(values),
            "identity_digest": canonical_digest(values),
            "identities": values,
        }
        for role, values in collections.items()
    }


def canonical_hashes(golden_root: Path, lifecycle: dict[str, Any]) -> dict[str, str]:
    paths = [
        golden_root / "data" / "quality" / "quality_manifest.jsonl",
        golden_root / "data" / "shards" / "shard_index.json",
        golden_root / "readiness.json",
        golden_root / "model" / "latest_model_matrix.json",
        runtime_path(str(lifecycle["ct_evaluation_uri"])),
    ]
    paths.extend(sorted((golden_root / "data" / "shards").glob("*.jsonl")))
    readiness = read_json(golden_root / "readiness.json")
    model_check = next(
        item for item in readiness["checks"] if item["check_id"] == "model_artifact"
    )
    paths.append(runtime_path(str(model_check["evidence_uri"])))
    return {str(path.resolve()): file_digest(path) for path in paths}


def runtime_projection(snapshot: dict[str, Any]) -> dict[str, Any]:
    supervisor = snapshot.get("runtime_supervisor", {})
    children = {
        item.get("name"): {
            "pid": item.get("pid"),
            "process_instance_id": item.get("process_instance_id"),
            "source_commit": item.get("source_commit"),
            "supervisor_lease_id": item.get("supervisor_lease_id"),
            "fencing_token": item.get("fencing_token"),
            "status": item.get("status"),
        }
        for item in supervisor.get("children", [])
        if item.get("name") in {"lifecycle_worker", "kubernetes_observer"}
    }
    return {
        "deployment_uid": snapshot["deployment"]["uid"],
        "deployment_image": snapshot["deployment"]["image"],
        "ready_replicas": snapshot["deployment"]["ready_replicas"],
        "model_digest": snapshot["ready"].get("model_sha256"),
        "candidate_id": snapshot["ready"].get("candidate_id"),
        "inference_device": snapshot["inference"].get("device"),
        "gpu_allocatable": snapshot["gpu_allocatable"],
        "device_plugin": snapshot["device_plugin"],
        "prometheus_targets": snapshot["prometheus_targets"],
        "supervisor_source_commit": supervisor.get("source_commit"),
        "supervisor_status": supervisor.get("status"),
        "children": children,
    }


def invariant_diff(
    before_runtime: dict[str, Any],
    after_runtime: dict[str, Any],
    before_effects: dict[str, Any],
    after_effects: dict[str, Any],
    before_hashes: dict[str, str],
    after_hashes: dict[str, str],
) -> dict[str, Any]:
    before_projection = runtime_projection(before_runtime)
    after_projection = runtime_projection(after_runtime)
    runtime_equal = before_projection == after_projection
    runtime_healthy = (
        after_projection["ready_replicas"] == 1
        and after_projection["inference_device"] == "cuda"
        and all(
            after_projection["prometheus_targets"].get(job) == "up"
            for job in ("evm-api", "evm-b0-production")
        )
        and all(item["ready"] for item in after_projection["device_plugin"])
        and all(item["status"] == "online" for item in after_projection["children"].values())
    )
    side_effect_checks = {
        role: before_effects[role] == after_effects[role] for role in before_effects
    }
    return {
        "schema_version": "evm.lifecycle_guard_scenario_e_invariants.v1",
        "runtime_identity_unchanged": runtime_equal,
        "runtime_healthy": runtime_healthy,
        "side_effects_unchanged": side_effect_checks,
        "canonical_hashes_unchanged": before_hashes == after_hashes,
        "before_runtime": before_projection,
        "after_runtime": after_projection,
        "before_side_effects": before_effects,
        "after_side_effects": after_effects,
        "passed": runtime_equal
        and runtime_healthy
        and all(side_effect_checks.values())
        and before_hashes == after_hashes,
    }


def stable_replay(
    results: list[dict[str, Any]],
    decision: str,
    required_blockers: set[str] | None = None,
) -> bool:
    required_blockers = required_blockers or set()
    return (
        len(results) == 3
        and all(item["decision"] == decision for item in results)
        and all(required_blockers.issubset(item.get("blockers", [])) for item in results)
        and len({item["decision_fingerprint"] for item in results}) == 1
        and all(item["elapsed_seconds"] <= 30 for item in results)
    )


def build_evidence_index(run_root: Path) -> Path:
    artifacts = [
        {
            "uri": str(path.resolve()),
            "sha256": file_digest(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(run_root.rglob("*"))
        if path.is_file() and path.name != "evidence-index.json"
    ]
    index = {
        "schema_version": "evm.lifecycle_guard_evidence_index.v1",
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    path = run_root / "evidence-index.json"
    write_json(path, index)
    return path


def run(project_root: Path, golden_root: Path, output_root: Path) -> Path:
    started_at = datetime.now(UTC)
    head = git_text(project_root, "rev-parse", "HEAD")
    upstream = git_text(project_root, "rev-parse", "@{u}")
    dirty = bool(git_text(project_root, "status", "--porcelain", "--", "."))
    if dirty or head != upstream:
        raise RuntimeError(
            f"lifecycle_scenario_e_source_preflight_failed:dirty={dirty}:"
            f"head={head}:upstream={upstream}"
        )
    lifecycle = read_json(golden_root / "lifecycle_run.json")
    if lifecycle.get("state") != "completed":
        raise RuntimeError("golden_lifecycle_not_completed")
    run_id = f"scenario-e-lifecycle-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{head[:8]}"
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    write_json(
        run_root / "execution-contract.json",
        {
            "schema_version": SCHEMA,
            "run_id": run_id,
            "source_revision": head,
            "golden_run_id": lifecycle["run_id"],
            "golden_source_revision": lifecycle["source_commit"],
            "mode": "non_disruptive_controlled_branch_replay",
            "canonical_mutation_allowed": False,
            "production_mutation_allowed": False,
            "replays_per_decision": 3,
            "decision_slo_seconds": 30,
            "started_at": started_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        },
    )

    before_runtime = production_snapshot(DEFAULT_INFERENCE_IMAGE_URI)
    before_effects = external_side_effect_snapshot()
    before_hashes = canonical_hashes(golden_root, lifecycle)
    write_json(run_root / "pre-runtime.json", before_runtime)
    write_json(run_root / "pre-side-effects.json", before_effects)
    write_json(run_root / "pre-canonical-hashes.json", before_hashes)

    branches = run_root / "data-branches"
    canonical_root = branches / "canonical"
    materialize_data_branch(golden_root, canonical_root)
    canonical_results = replay_data(canonical_root)

    wrong_identity_root = branches / "wrong-semantic-identity"
    wrong_index = materialize_data_branch(golden_root, wrong_identity_root)
    wrong_payload = read_json(wrong_index)
    wrong_payload["identity_sha256"] = "0" * 64
    write_json(wrong_index, wrong_payload)
    wrong_identity_results = replay_data(wrong_identity_root)

    leakage_root = branches / "split-leakage"
    leakage_index = materialize_data_branch(golden_root, leakage_root)
    inject_split_leakage(leakage_index)
    leakage_results = replay_data(leakage_root)

    corrected_root = branches / "corrected-immutable-attempt"
    materialize_data_branch(golden_root, corrected_root)
    corrected_results = replay_data(corrected_root)

    release_root = run_root / "release-branches"
    release_submission = build_lifecycle_release_submission(
        artifact_root=release_root / "canonical",
        run_id=str(lifecycle["run_id"]),
        source_commit=str(lifecycle["source_commit"]),
        readiness_uri=str(lifecycle["readiness_uri"]),
        model_matrix_uri=str(lifecycle["model_matrix_uri"]),
        ct_evaluation_uri=str(lifecycle["ct_evaluation_uri"]),
    )
    canonical_release_results = replay_release(
        release_submission,
        run_id=str(lifecycle["run_id"]),
        source_commit=str(lifecycle["source_commit"]),
        replay_root=release_root / "canonical" / "replays",
    )
    wrong_release_root = release_root / "wrong-model-identity"
    wrong_release_root.mkdir(parents=True, exist_ok=True)
    wrong_submission = read_json(release_submission)
    wrong_submission["model_digest"] = "f" * 64
    material = dict(wrong_submission)
    material.pop("submission_digest", None)
    wrong_submission["submission_digest"] = canonical_digest(material)
    wrong_submission_path = wrong_release_root / "release-submission.json"
    write_json(wrong_submission_path, wrong_submission)
    wrong_release_results = replay_release(
        wrong_submission_path,
        run_id=str(lifecycle["run_id"]),
        source_commit=str(lifecycle["source_commit"]),
        replay_root=wrong_release_root / "replays",
    )

    after_runtime = production_snapshot(DEFAULT_INFERENCE_IMAGE_URI)
    after_effects = external_side_effect_snapshot()
    after_hashes = canonical_hashes(golden_root, lifecycle)
    write_json(run_root / "post-runtime.json", after_runtime)
    write_json(run_root / "post-side-effects.json", after_effects)
    write_json(run_root / "post-canonical-hashes.json", after_hashes)
    invariants = invariant_diff(
        before_runtime,
        after_runtime,
        before_effects,
        after_effects,
        before_hashes,
        after_hashes,
    )
    write_json(run_root / "invariant-diff.json", invariants)

    checks = {
        "canonical_data_three_replays": stable_replay(canonical_results, "pass"),
        "wrong_identity_three_replays": stable_replay(
            wrong_identity_results,
            "blocked",
            {"integrity_shard_index_identity_mismatch"},
        ),
        "split_leakage_three_replays": stable_replay(
            leakage_results,
            "blocked",
            {"integrity_duplicate_record_identity", "integrity_split_leakage_detected"},
        ),
        "corrected_attempt_three_replays": stable_replay(corrected_results, "pass"),
        "canonical_release_three_replays": stable_replay(
            canonical_release_results,
            "pass",
        ),
        "wrong_release_three_replays": stable_replay(
            wrong_release_results,
            "blocked",
            {"release_model_artifact_digest_mismatch"},
        ),
        "runtime_and_side_effect_invariants": bool(invariants["passed"]),
    }
    result = {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "source_revision": head,
        "golden_run_id": lifecycle["run_id"],
        "started_at": started_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "finished_at": utc_now(),
        "mode": "non_disruptive_controlled_branch_replay",
        "checks": checks,
        "status": "pass" if all(checks.values()) else "blocked",
        "claim_boundary": (
            "Single-node local controlled replay through production validator code; "
            "no real-user traffic, HA, distributed transaction, or production mutation proof."
        ),
    }
    result_path = run_root / "result.json"
    index_path = run_root / "evidence-index.json"
    result["evidence_index_uri"] = str(index_path.resolve())
    write_json(result_path, result)
    build_evidence_index(run_root)
    if result["status"] != "pass":
        raise RuntimeError(f"lifecycle_scenario_e_acceptance_failed:{checks}")
    return result_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run non-disruptive Scenario E guards across lifecycle boundaries."
    )
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--golden-run-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    result = run(
        args.project_root.resolve(),
        args.golden_run_root.resolve(),
        args.output_root.resolve(),
    )
    print(json.dumps({"result_uri": str(result.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
